"""语义检索与嵌入模型管理（PRD 4.6 F-KB-05/06/09/10）。

架构：
- 嵌入 Provider 抽象：本地 ONNX（bge-small-zh / bge-m3）+ 哈希兜底（离线可跑通）。
- 向量按「模型 + 维度」分表：vec_<model_slug>_<dim>，避免混合维度写入同一张表。
- 优先用 sqlite-vec 虚拟表；未安装时回退到普通表 + Python 余弦计算（结果等价）。
- 嵌入模型切换 → 后台重嵌入任务，期间旧模型条目降级 FTS5 召回并在 UI 标注「重建中」。
"""
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from .db import DATA_DIR, MODELS_DIR, execute, execute_many, query, query_one

EMBED_MODELS = {
    "bge-small-zh-v1.5": {"dim": 512, "size_mb": 95, "slug": "bge_small_zh_512",
                          "source": "BAAI/bge-small-zh-v1.5", "note": "默认，轻量中文嵌入"},
    "bge-m3": {"dim": 1024, "size_mb": 2200, "slug": "bge_m3_1024",
               "source": "BAAI/bge-m3", "note": "多语言长文本，体积较大"},
}

VEC_TABLES: dict[str, str] = {}      # model -> 虚拟表名
VEC_FALLBACK: dict[str, str] = {}   # model -> 普通表名
_lock = threading.RLock()
_local = threading.local()          # 每线程的 vec 扩展加载状态（扩展按连接生效）
_reembed_thread: threading.Thread | None = None


def _slug(model: str) -> str:
    return EMBED_MODELS.get(model, {}).get("slug", re.sub(r"[^a-z0-9]+", "_", model.lower()))


def _dim(model: str) -> int:
    return int(EMBED_MODELS.get(model, {}).get("dim", 512))


def table_name(model: str) -> str:
    return f"vec_{_slug(model)}_{_dim(model)}"


def _conn():
    from .db import get_conn

    return get_conn()


def _ensure_vec_on(conn) -> bool:
    """sqlite-vec 扩展按「连接」加载：每个新连接都要单独 load 一次。

    FastAPI 的同步路由跑在线程池里，连接是 thread-local 的，
    因此不能只在主线程加载一次，否则工作线程写 vec0 表会报 no such module。
    """
    if getattr(_local, "vec_loaded", False):
        return True
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _local.vec_loaded = True
        return True
    except Exception:
        return False


def ensure_vec_tables() -> None:
    """建向量表（虚拟表或兜底表）。"""
    conn = _conn()
    use_vec = _ensure_vec_on(conn)
    with _lock:
        VEC_TABLES.clear()
        VEC_FALLBACK.clear()
        for m in EMBED_MODELS:
            t = table_name(m)
            if use_vec:
                try:
                    conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {t} USING vec0("
                                 f"item_id INTEGER PRIMARY KEY, embedding float[{_dim(m)}])")
                    VEC_TABLES[m] = t
                    continue
                except Exception:
                    pass
            fb = f"{t}_fallback"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {fb}("
                         f"item_id INTEGER PRIMARY KEY, embedding BLOB, dim INTEGER)")
            VEC_FALLBACK[m] = fb
        conn.commit()


def current_model() -> str:
    from . import settings

    return settings.get_setting("embedding_model", "bge-small-zh-v1.5")


def model_installed(model: str) -> bool:
    """本地 ONNX 模型是否已下载。"""
    d = MODELS_DIR / _slug(model)
    return d.exists() and any(d.glob("*.onnx"))


# ---------------- 嵌入计算 ----------------
_onnx_sessions: dict[str, object] = {}


def _hash_embed(text: str, dim: int) -> list[float]:
    """离线兜底嵌入：字符 n-gram 哈希 + L2 归一化。保证无模型也能演示语义近似。"""
    vec = [0.0] * dim
    t = (text or "").strip()
    if not t:
        return vec
    toks = [t[i:i + 2] for i in range(max(1, len(t) - 1))] + list(t)
    for tok in toks:
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec] if norm else vec


def _onnx_embed(text: str, model: str) -> list[float] | None:
    """本地 ONNX 推理（bge 系列）。缺少 onnxruntime 或模型文件时返回 None。"""
    if not model_installed(model):
        return None
    try:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except Exception:
        return None
    d = MODELS_DIR / _slug(model)
    sess = _onnx_sessions.get(model)
    if sess is None:
        onnx_files = sorted(d.glob("*.onnx"))
        if not onnx_files:
            return None
        try:
            sess = ort.InferenceSession(str(onnx_files[0]), providers=["CPUExecutionProvider"])
        except Exception:
            return None
        _onnx_sessions[model] = sess
    tok_file = d / "tokenizer.json"
    if not tok_file.exists():
        return None
    try:
        tok = Tokenizer.from_file(str(tok_file))
        enc = tok.encode(text or " ")
        ids = [enc.ids]
        mask = [enc.attention_mask]
        inputs = {}
        for i in sess.get_inputs():
            if "mask" in i.name:
                inputs[i.name] = np.array(mask, dtype=np.int64)
            elif "token_type" in i.name:
                inputs[i.name] = np.zeros_like(np.array(ids, dtype=np.int64))
            else:
                inputs[i.name] = np.array(ids, dtype=np.int64)
        out = sess.run(None, inputs)[0]
        vec = np.asarray(out[0]).mean(axis=0).astype(float)
        norm = float((vec * vec).sum() ** 0.5)
        return (vec / norm).tolist() if norm else vec.tolist()
    except Exception:
        return None


def embed(text: str, model: str | None = None) -> list[float]:
    m = model or current_model()
    dim = _dim(m)
    v = _onnx_embed(text, m)
    if v and len(v) == dim:
        return v
    return _hash_embed(text, dim)


def embed_batch(texts: list[str], model: str | None = None) -> list[list[float]]:
    return [embed(t, model) for t in texts]


# ---------------- 写入 / 删除向量 ----------------
def _pack(vec: list[float]) -> bytes:
    import struct

    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    import struct

    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob[:n * 4]))


def store_vector(item_id: int, vec: list[float], model: str) -> None:
    ensure_vec_tables()
    t = table_name(model)
    with _lock:
        conn = _conn()
        use_vec = _ensure_vec_on(conn) and model in VEC_TABLES
        if use_vec:
            try:
                conn.execute(f"DELETE FROM {t} WHERE item_id=?", (item_id,))
                conn.execute(f"INSERT INTO {t}(item_id, embedding) VALUES (?,?)", (item_id, _pack(vec)))
                conn.commit()
                return
            except Exception:
                conn.rollback()
        fb = f"{t}_fallback"
        conn.execute(f"DELETE FROM {fb} WHERE item_id=?", (item_id,))
        conn.execute(f"INSERT OR REPLACE INTO {fb}(item_id, embedding, dim) VALUES (?,?,?)",
                     (item_id, _pack(vec), len(vec)))
        conn.commit()


def delete_vector(item_id: int, model: str | None = None) -> None:
    ensure_vec_tables()
    models = [model] if model else list(EMBED_MODELS)
    with _lock:
        conn = _conn()
        for m in models:
            t = table_name(m)
            for name in (t, f"{t}_fallback"):
                try:
                    conn.execute(f"DELETE FROM {name} WHERE item_id=?", (item_id,))
                except Exception:
                    pass
        conn.commit()


def drop_all_vectors() -> int:
    ensure_vec_tables()
    freed = 0
    with _lock:
        conn = _conn()
        for m in EMBED_MODELS:
            t = table_name(m)
            for name in (t, f"{t}_fallback"):
                try:
                    cur = conn.execute(f"SELECT COUNT(*) FROM {name}")
                    freed += int(cur.fetchone()[0])
                    conn.execute(f"DROP TABLE IF EXISTS {name}")
                except Exception:
                    pass
        conn.commit()
    ensure_vec_tables()
    return freed


# ---------------- 检索 ----------------
def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def search_vectors(qvec: list[float], model: str, limit: int = 20) -> list[tuple[int, float]]:
    ensure_vec_tables()
    t = table_name(model)
    rows: list[tuple[int, float]] = []
    with _lock:
        conn = _conn()
        if _ensure_vec_on(conn) and model in VEC_TABLES:
            try:
                cur = conn.execute(
                    f"SELECT item_id, distance FROM {t} WHERE embedding MATCH ? AND k = ?",
                    (_pack(qvec), limit))
                for r in cur.fetchall():
                    dist = float(r[1])
                    rows.append((int(r[0]), 1.0 - dist / 2.0))
                return rows
            except Exception:
                rows = []
        fb = f"{t}_fallback"
        try:
            cur = conn.execute(f"SELECT item_id, embedding FROM {fb}")
            for r in cur.fetchall():
                rows.append((int(r[0]), _cosine(qvec, _unpack(r[1]))))
        except Exception:
            rows = []
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows[:limit]


def semantic_search(text: str, limit: int = 20, model: str | None = None) -> dict:
    """语义召回；无模型/无向量时返回空并交由上层回退 FTS5。"""
    m = model or current_model()
    qvec = embed(text, m)
    hits = search_vectors(qvec, m, limit)
    if not hits:
        return {"mode": "vector", "results": [], "model": m}
    ids = [i for i, _ in hits]
    score = {i: s for i, s in hits}
    rows = query(f"SELECT * FROM knowledge_items WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
    for r in rows:
        r["score"] = round(score.get(r["id"], 0.0), 4)
    rows.sort(key=lambda r: r.get("score", 0), reverse=True)
    return {"mode": "vector", "results": rows, "model": m}


# ---------------- 条目索引状态 ----------------
def index_item(item_id: int, title: str, content: str, model: str | None = None) -> str:
    """写入向量并更新条目状态。返回状态。"""
    m = model or current_model()
    try:
        vec = embed(f"{title}\n{content}", m)
        store_vector(item_id, vec, m)
        execute("UPDATE knowledge_items SET embedding_model=?, embedding_status='done' WHERE id=?",
                (m, item_id))
        return "done"
    except Exception:
        execute("UPDATE knowledge_items SET embedding_status='failed' WHERE id=?")
        return "failed"


def reindex_pending(model: str | None = None) -> int:
    m = model or current_model()
    rows = query("SELECT id, title, content FROM knowledge_items "
                 "WHERE embedding_model IS NULL OR embedding_model != ? OR embedding_status != 'done'", (m,))
    for r in rows:
        index_item(r["id"], r["title"], r["content"], m)
    return len(rows)


# ---------------- 切换模型与重嵌入迁移（F-KB-10） ----------------
def switch_model(target: str) -> dict:
    if target not in EMBED_MODELS:
        raise ValueError(f"未知嵌入模型：{target}")
    from . import settings

    total = query("SELECT COUNT(*) c FROM knowledge_items")[0]["c"]
    job_id = execute(
        "INSERT INTO embedding_jobs(target_model, target_dim, total, status) VALUES (?,?,?,'running')",
        (target, _dim(target), total))
    old = current_model()
    settings.set_setting("embedding_model", target)
    # 旧模型条目标记为「重建中」，检索时降级 FTS5
    execute("UPDATE knowledge_items SET embedding_status='rebuilding' "
            "WHERE embedding_model IS NOT NULL AND embedding_model != ?", (target,))
    _start_reembed(job_id, target)
    return {"job_id": job_id, "target_model": target, "from_model": old, "total": total,
            "installed": model_installed(target)}


def _start_reembed(job_id: int, target: str) -> None:
    global _reembed_thread

    def worker() -> None:
        ensure_vec_tables()
        done = failed = 0
        rows = query("SELECT id, title, content FROM knowledge_items ORDER BY id")
        for r in rows:
            try:
                index_item(r["id"], r["title"], r["content"], target)
                done += 1
            except Exception:
                failed += 1
            execute("UPDATE embedding_jobs SET done=?, failed=? WHERE id=?", (done, failed, job_id))
        execute("UPDATE embedding_jobs SET status=?, finished_at=datetime('now') WHERE id=?",
                ("done" if not failed else "done", job_id))

    t = threading.Thread(target=worker, daemon=True, name="reembed")
    _reembed_thread = t
    t.start()


def job_status(job_id: int | None = None) -> dict:
    if job_id:
        row = query_one("SELECT * FROM embedding_jobs WHERE id=?", (job_id,))
    else:
        row = query_one("SELECT * FROM embedding_jobs ORDER BY id DESC LIMIT 1")
    if not row:
        return {"status": "idle", "done": 0, "total": 0}
    total = row["total"] or 0
    done = row["done"] or 0
    row["progress"] = round(done / total, 3) if total else 1.0
    return row


def pending_rebuild_count() -> int:
    return query("SELECT COUNT(*) c FROM knowledge_items WHERE embedding_status='rebuilding'")[0]["c"]


def model_catalog() -> list[dict]:
    out = []
    for name, meta in EMBED_MODELS.items():
        out.append({
            "id": name,
            "dim": meta["dim"],
            "size_mb": meta["size_mb"],
            "slug": meta["slug"],
            "note": meta.get("note", ""),
            "installed": model_installed(name),
            "current": name == current_model(),
        })
    return out
