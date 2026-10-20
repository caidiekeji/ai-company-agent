"""本地知识库（PRD 4.6）：事件层 / 知识条目层 / FTS5 检索（语义检索 M2 接入）。"""
import json

from fastapi import APIRouter, HTTPException

from ..db import execute, fts_sync, query, query_one

router = APIRouter(prefix="/api/kb", tags=["kb"])


@router.get("/events")
def list_events():
    rows = query("SELECT * FROM events ORDER BY id DESC LIMIT 200")
    return rows


@router.get("/items")
def list_items():
    rows = query("SELECT * FROM knowledge_items ORDER BY id DESC")
    for r in rows:
        r["event"] = query_one("SELECT type, ref_id FROM events WHERE id=?", (r.get("source_event_id"),)) \
            if r.get("source_event_id") else None
    return rows


@router.post("/items")
def create_item(body: dict):
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(400, "标题与正文必填")
    ev_id = None
    if body.get("event_id") is None and body.get("auto_event"):
        ev_id = execute("INSERT INTO events(type, ref_id) VALUES ('manual', NULL)")
    else:
        ev_id = body.get("event_id")
    iid = execute("INSERT INTO knowledge_items(title, content, source_event_id, tags) VALUES (?,?,?,?)",
                  (title, content, ev_id, body.get("tags") or ""))
    fts_sync(iid, title, content)
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
    return {"ok": True}


@router.delete("/items/{iid}")
def delete_item(iid: int):
    execute("DELETE FROM knowledge_fts WHERE rowid=?", (iid,))
    execute("DELETE FROM knowledge_items WHERE id=?", (iid,))
    return {"ok": True}


@router.get("/search")
def search(q: str = "", mode: str = "auto", limit: int = 20):
    """mode: fts | like | auto（auto=fts 失败/无结果回退 LIKE，F-KB-06）。"""
    q = q.strip()
    if not q:
        return {"mode": "-", "results": []}
    results: list[dict] = []
    used = "fts"
    if mode in ("auto", "fts"):
        try:
            # trigram tokenizer：模式串加引号做短语匹配
            rows = query(
                "SELECT i.* FROM knowledge_fts f JOIN knowledge_items i ON i.id = f.rowid "
                "WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT ?", (f'"{q}"', limit))
            results = rows
        except Exception:
            results = []
        if not results and mode == "auto":
            used = "like"
    if not results or mode == "like":
        used = "like"
        results = query(
            "SELECT * FROM knowledge_items WHERE title LIKE ? OR content LIKE ? "
            "ORDER BY id DESC LIMIT ?", (f"%{q}%", f"%{q}%", limit))
    for r in results:
        r.pop("embedding_status", None)
    return {"mode": used, "results": results}
