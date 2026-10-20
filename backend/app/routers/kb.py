"""本地知识库（PRD 4.6）：事件层 / 知识条目层 / 语义检索 + FTS5 关键词降级。"""
import json

from fastapi import APIRouter, HTTPException

from .. import embedding
from ..db import execute, fts_delete, fts_sync, query, query_one

router = APIRouter(prefix="/api/kb", tags=["kb"])


@router.get("/events")
def list_events():
    return query("SELECT * FROM events ORDER BY id DESC LIMIT 200")


@router.get("/items")
def list_items():
    rows = query("SELECT * FROM knowledge_items ORDER BY id DESC")
    for r in rows:
        r["event"] = query_one("SELECT type, ref_id FROM events WHERE id=?", (r.get("source_event_id"),)) \
            if r.get("source_event_id") else None
    return rows


@router.post("/items")
def create_item(body: dict | None = None):
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(400, "标题与正文必填")
    ev_id = body.get("event_id")
    if ev_id is None:
        ev_id = execute("INSERT INTO events(type, ref_id) VALUES ('manual', NULL)")
    iid = execute("INSERT INTO knowledge_items(title, content, source_event_id, tags) VALUES (?,?,?,?)",
                  (title, content, ev_id, body.get("tags") or ""))
    fts_sync(iid, title, content)
    embedding.index_item(iid, title, content)
    return query_one("SELECT * FROM knowledge_items WHERE id=?", (iid,))


@router.put("/items/{iid}")
def update_item(iid: int, body: dict):
    row = query_one("SELECT * FROM knowledge_items WHERE id=?", (iid,))
    if not row:
        raise HTTPException(404, "条目不存在")
    title = body.get("title", row["title"])
    content = body.get("content", row["content"])
    tags = body.get("tags", row["tags"])
    execute("UPDATE knowledge_items SET title=?, content=?, tags=?, updated_at=datetime('now') "
            "WHERE id=?", (title, content, tags, iid))
    fts_sync(iid, title, content)
    embedding.index_item(iid, title, content)
    return {"ok": True}


@router.delete("/items/{iid}")
def delete_item(iid: int):
    fts_delete(iid)
    embedding.delete_vector(iid)
    execute("DELETE FROM knowledge_items WHERE id=?", (iid,))
    return {"ok": True}


@router.get("/search")
def search(q: str = "", mode: str = "auto", limit: int = 20):
    """mode: vector | fts | like | auto。

    auto：优先语义召回（向量可用时），否则 FTS5 trigram 关键词检索（嵌入模型未下载/重建中时降级）。
    """
    q = q.strip()
    if not q:
        return {"mode": "-", "results": [], "rebuilding": embedding.pending_rebuild_count()}
    rebuilding = embedding.pending_rebuild_count()
    results: list[dict] = []
    used = "fts"

    if mode in ("auto", "vector"):
        vec = embedding.semantic_search(q, limit)
        results = vec.get("results") or []
        used = "vector" if results else "fts"
        if mode == "vector" and not results:
            return {"mode": "vector", "results": [], "rebuilding": rebuilding,
                    "note": "向量索引为空（嵌入模型未下载或尚未建索引），已返回空结果"}

    if not results and mode in ("auto", "fts"):
        try:
            results = query(
                "SELECT i.* FROM knowledge_fts f JOIN knowledge_items i ON i.id = f.rowid "
                "WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT ?", (f'"{q}"', limit))
            used = "fts"
        except Exception:
            results = []
    if not results and mode in ("auto", "like"):
        results = query(
            "SELECT * FROM knowledge_items WHERE title LIKE ? OR content LIKE ? "
            "ORDER BY id DESC LIMIT ?", (f"%{q}%", f"%{q}%", limit))
        used = "like"

    for r in results:
        r["rebuilding"] = r.get("embedding_status") == "rebuilding"
    return {"mode": used, "results": results, "rebuilding": rebuilding,
            "embedding_model": embedding.current_model()}


# ---------------- 嵌入模型管理（F-KB-09/10） ----------------
@router.get("/embedding")
def embedding_info():
    return {
        "current": embedding.current_model(),
        "models": embedding.model_catalog(),
        "job": embedding.job_status(),
        "rebuilding": embedding.pending_rebuild_count(),
        "vec_backend": "sqlite-vec" if embedding.VEC_TABLES else "fallback",
    }


@router.post("/embedding/switch")
def switch(body: dict | None = None):
    """切换嵌入模型 → 后台全量重嵌入（F-KB-10）。"""
    target = body.get("model")
    try:
        result = embedding.switch_model(target)
    except ValueError as e:
        raise HTTPException(400, str(e))
    total = result.get("total", 0)
    return {"ok": True, **result,
            "message": f"已切换到 {target}，需要重建索引（约 {total} 条）。"
                       f"重建期间相关条目降级为关键词检索并在列表中标注「重建中」。"}


@router.post("/embedding/reindex")
def reindex(body: dict | None = None):
    model = (body or {}).get("model") or embedding.current_model()
    n = embedding.reindex_pending(model)
    return {"ok": True, "reindexed": n, "model": model}


@router.get("/embedding/job")
def job():
    return embedding.job_status()
