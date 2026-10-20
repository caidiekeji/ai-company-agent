"""会议路由（PRD 4.3）：创建/查询 + SSE 流式运行 + 插话/打断/指定下一位 + 回放。"""
import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import settings
from ..db import execute, query, query_one
from ..llm_gateway import estimate_tokens
from ..orchestrator import (TEMPLATES, deposit_knowledge, estimate_cost, get_runtime,
                            interrupt_meeting, start_meeting, stop_meeting)

router = APIRouter(prefix="/api/meetings", tags=["meeting"])


@router.get("/templates")
def templates():
    return TEMPLATES


@router.get("")
def list_meetings():
    rows = query("SELECT * FROM meetings ORDER BY id DESC")
    for r in rows:
        r["decisions"] = json.loads(r.pop("decisions_json") or "[]")
        r["todos"] = json.loads(r.pop("todos_json") or "[]")
        r["cost_estimate"] = meeting_cost(r)
    return rows


def meeting_cost(m: dict) -> dict:
    """F-MTG-12 单场成本预估（元）。"""
    n = query("SELECT COUNT(*) c FROM meeting_participants WHERE meeting_id=?", (m["id"],))[0]["c"]
    if not n:
        n = query("SELECT COUNT(*) c FROM employees WHERE enabled=1")[0]["c"]
    cfg = query_one("SELECT * FROM llm_configs WHERE is_default=1 LIMIT 1")
    from ..providers import vendor_price_per_1k

    price = vendor_price_per_1k("llm", (cfg or {}).get("provider", "")) or \
        float(settings.get_setting("llm_price_per_1k", "0.002"))
    est = estimate_cost(n, m.get("max_rounds") or 6, 400, price)
    used = m.get("total_tokens_used") or 0
    est["used"] = used
    est["budget"] = m.get("token_budget")
    est["used_cost"] = round(used * price / 1000, 4)
    est["budget_pct"] = round(used / (m.get("token_budget") or 60000), 3)
    return est


@router.post("")
def create_meeting(body: dict | None = None):
    topic = (body.get("topic") or "").strip()
    participant_ids = body.get("participant_ids") or []
    template_id = body.get("template_id")
    max_rounds = body.get("max_rounds")
    if template_id:
        tpl = next((t for t in TEMPLATES if t["id"] == template_id), None)
        if tpl:
            topic = tpl["topic"].format(topic=topic or "未命名议题")
            # 模板自带轮次建议；用户显式指定时以用户为准
            if not max_rounds:
                max_rounds = tpl["rounds"]
    if not topic:
        raise HTTPException(400, "议题必填")
    if len(participant_ids) < 2:
        raise HTTPException(400, "参会员工至少 2 人")
    host_id = body.get("host_employee_id")
    if host_id and host_id not in participant_ids:
        participant_ids = list(participant_ids) + [host_id]
    mid = execute(
        "INSERT INTO meetings(topic, host_employee_id, scheduler_mode, max_rounds, token_budget, "
        "context_visibility, visible_rounds, compress_context, template_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (topic, host_id, body.get("scheduler_mode", "round_robin"),
         max_rounds or 6, body.get("token_budget", 60000),
         body.get("context_visibility", "host_full"), body.get("visible_rounds", 6),
         1 if body.get("compress_context", settings.get_bool("context_compress_default", True)) else 0,
         template_id))
    for i, pid in enumerate(participant_ids):
        execute("INSERT OR IGNORE INTO meeting_participants(meeting_id, employee_id, seat) "
                "VALUES (?,?,?)", (mid, pid, i))
    m = query_one("SELECT * FROM meetings WHERE id=?", (mid,))
    m["decisions"] = []
    m["todos"] = []
    m["cost_estimate"] = meeting_cost(m)
    return m


@router.get("/{mid}")
def get_meeting(mid: int):
    m = query_one("SELECT * FROM meetings WHERE id=?", (mid,))
    if not m:
        raise HTTPException(404, "会议不存在")
    m["decisions"] = json.loads(m.pop("decisions_json") or "[]")
    m["todos"] = json.loads(m.pop("todos_json") or "[]")
    msgs = query("SELECT * FROM meeting_messages WHERE meeting_id=? "
                 "AND content NOT LIKE '[已入会]%' ORDER BY id", (mid,))
    names = {e["id"]: e["name"] for e in query("SELECT id, name FROM employees")}
    for msg in msgs:
        msg["employee_name"] = names.get(msg.get("employee_id"), "用户")
    m["messages"] = msgs
    m["participants"] = [
        {"id": r["employee_id"], "name": names.get(r["employee_id"], "?")}
        for r in query("SELECT employee_id FROM meeting_participants WHERE meeting_id=? ORDER BY seat",
                       (mid,))]
    m["cost_estimate"] = meeting_cost(m)
    return m


@router.get("/{mid}/replay")
def replay(mid: int):
    """F-MTG-09 会议记录回放（时间轴）。"""
    rows = query("SELECT id, employee_id, role, content, model_used, tokens, interrupted, seq, created_at "
                 "FROM meeting_messages WHERE meeting_id=? AND content NOT LIKE '[已入会]%' "
                 "ORDER BY id", (mid,))
    names = {e["id"]: e["name"] for e in query("SELECT id, name FROM employees")}
    for r in rows:
        r["employee_name"] = names.get(r.get("employee_id"), "用户")
    return {"meeting_id": mid, "timeline": rows}


@router.post("/{mid}/run")
async def run_meeting(mid: int):
    m = query_one("SELECT id, status FROM meetings WHERE id=?", (mid,))
    if not m:
        raise HTTPException(404, "会议不存在")
    if m["status"] == "running":
        rt = get_runtime(mid)
    else:
        rt = start_meeting(mid)

    async def gen():
        yield f"event: connected\ndata: {json.dumps({'meeting_id': mid})}\n\n"
        while True:
            try:
                ev = await asyncio.wait_for(rt.queue.get(), timeout=900)
            except asyncio.TimeoutError:
                yield "event: error\ndata: {\"message\":\"超时\"}\n\n"
                break
            yield f"event: {ev['type']}\ndata: {json.dumps({k: v for k, v in ev.items() if k != 'type'}, ensure_ascii=False)}\n\n"
            if ev["type"] == "end":
                break

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{mid}/stop")
def stop(mid: int):
    if not stop_meeting(mid):
        raise HTTPException(400, "会议未在运行")
    return {"ok": True}


@router.post("/{mid}/interrupt")
def interrupt(mid: int):
    """F-MTG-06：打断当前流式生成（保留已生成部分）。"""
    if not interrupt_meeting(mid):
        raise HTTPException(400, "会议未在运行")
    return {"ok": True}


@router.post("/{mid}/speak")
async def user_speak(mid: int, body: dict):
    """用户插话：写入 user 消息；可带 next_employee_id 指定下一位；可带 interrupt 打断当前生成。"""
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "内容必填")
    execute("INSERT INTO meeting_messages(meeting_id, employee_id, role, content, tokens, seq) "
            "VALUES (?,?, 'user', ?, ?, "
            "(SELECT COALESCE(MAX(seq),0)+1 FROM meeting_messages WHERE meeting_id=?))",
            (mid, None, content, estimate_tokens(content), mid))
    rt = get_runtime(mid)
    if body.get("next_employee_id"):
        rt.pending_next = body["next_employee_id"]
    if body.get("interrupt"):
        rt.interrupt = True
    return {"ok": True, "interrupted": bool(body.get("interrupt"))}


@router.post("/{mid}/deposit")
def deposit(mid: int, body: dict):
    """F-KB-08 员工主动沉淀知识。"""
    try:
        return deposit_knowledge(mid, body.get("employee_id"), body.get("title", ""),
                                 body.get("content", ""), body.get("tags", "会议沉淀"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{mid}")
def delete_meeting(mid: int):
    stop_meeting(mid)
    execute("DELETE FROM meetings WHERE id=?", (mid,))
    return {"ok": True}
