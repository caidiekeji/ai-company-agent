"""RPA 路由（PRD 4.5）：会话管理、无障碍树快照、动作执行、内容提取、知识入库。"""
import uuid

from fastapi import APIRouter, HTTPException

from .. import rpa
from ..db import query, query_one

router = APIRouter(prefix="/api/rpa", tags=["rpa"])


@router.get("/status")
def status():
    return {
        "playwright_available": rpa.PLAYWRIGHT_AVAILABLE,
        "sessions": query("SELECT * FROM rpa_sessions ORDER BY updated_at DESC LIMIT 20"),
        "browser_installed": _browser_ready(),
    }


def _browser_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            path = p.chromium.executable_path
            return bool(path)
    except Exception:
        return False


@router.post("/open")
async def open_page(body: dict | None = None):
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "URL 必填")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    sid = body.get("session_id") or uuid.uuid4().hex
    result = await rpa.open_page(sid, url, body.get("employee_id"),
                                 bool(body.get("allow_non_whitelist")))
    if not result.get("ok"):
        if result.get("blocked"):
            raise HTTPException(403, result.get("message") or result.get("reason"))
        raise HTTPException(400, result.get("message", "打开失败"))
    return result


@router.get("/{sid}/snapshot")
async def snapshot(sid: str):
    s = rpa._sessions.get(sid)
    if not s or not s.page:
        raise HTTPException(404, "会话不存在或已关闭")
    return await s.snapshot()


@router.post("/{sid}/action")
async def action(sid: str, body: dict):
    s = rpa._sessions.get(sid)
    if not s or not s.page:
        raise HTTPException(404, "会话不存在或已关闭")
    try:
        return await s.perform(body)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/{sid}/confirm")
async def confirm(sid: str):
    s = rpa._sessions.get(sid)
    if not s:
        raise HTTPException(404, "会话不存在")
    result = await s.confirm_pending()
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "没有待确认动作"))
    return result


@router.post("/{sid}/extract")
async def extract(sid: str, body: dict):
    s = rpa._sessions.get(sid)
    if not s or not s.page:
        raise HTTPException(404, "会话不存在或已关闭")
    return {"content": await s.extract((body or {}).get("format", "markdown"))}


@router.post("/{sid}/close")
async def close(sid: str):
    await rpa.close_session(sid)
    return {"ok": True}


@router.get("/runs")
def runs():
    return rpa.list_runs()


@router.post("/runs/{rid}/to-knowledge")
def to_knowledge(rid: int, body: dict):
    try:
        return rpa.run_to_knowledge(rid, (body or {}).get("tags", "RPA"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/templates")
def templates():
    return rpa.list_templates()


@router.post("/templates")
def save_template(body: dict | None = None):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "模板名称必填")
    return rpa.save_template(name, body.get("url", ""), body.get("steps") or [],
                              body.get("description", ""))


@router.post("/run")
async def run_task(body: dict | None = None):
    """一键任务：打开 → 提取 → 落库 → 入知识库。"""
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "URL 必填")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    sid = uuid.uuid4().hex
    opened = await rpa.open_page(sid, url, body.get("employee_id"),
                                 bool(body.get("allow_non_whitelist")))
    if not opened.get("ok"):
        if opened.get("blocked"):
            raise HTTPException(403, opened.get("message") or opened.get("reason"))
        raise HTTPException(400, opened.get("message", "打开失败"))
    run_id = rpa.create_run(body.get("employee_id"), url, body.get("title", ""))
    try:
        content = await rpa._sessions[sid].extract("markdown")
        rpa.finish_run(run_id, "done", [{"type": "open", "url": url}, {"type": "extract"}],
                       content, body.get("title", ""))
        item = rpa.run_to_knowledge(run_id, body.get("tags", "RPA"))
        return {"ok": True, "run_id": run_id, "content": content, "knowledge_item_id": item["knowledge_item_id"]}
    except Exception as e:
        rpa.finish_run(run_id, "failed", [], "", "", str(e))
        raise HTTPException(400, f"任务失败：{e}")
    finally:
        await rpa.close_session(sid)
