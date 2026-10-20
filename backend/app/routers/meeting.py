"""会议路由（PRD 4.3）：创建/查询 + SSE 流式运行 + 插话/打断/指定下一位。"""
import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..db import execute, query, query_one
from ..llm_gateway import estimate_tokens
from ..orchestrator import get_runtime, start_meeting, stop_meeting

router = APIRouter(prefix="/api/meetings", tags=["meeting"])


@router.get("")
def list_meetings():
    rows = query("SELECT * FROM meetings ORDER BY id DESC")
    for r in rows:
        r["decisions"] = json.loads(r.pop("decisions_json") or "[]")
        r["todos"] = json.loads(r.pop("todos_json") or "[]")
        r["cost_estimate"] = estimate_cost(r)
    return rows


def estimate_cost(m: dict) -> dict:
    """F-MTG-12 单场成本预估：人数 × 平均单轮 400 token × 轮次上限（粗估）。"""
    n = query("SELECT COUNT(*) c FROM employees WHERE enabled=1")[0]["c"]
    est_tokens = max(1, n) * 400 * (m.get("max_rounds") or 6)
    return {"est_tokens": est_tokens,
            "budget": m.get("token_budget"),
            "used": m.get("total_tokens_used") or 0}


@router.post("")
def create_meeting(body: dict):
    topic = (body.get("topic") or "").strip()
    if not topic:
        raise HTTPException(400, "议题必填")
    participant_ids = body.get("participant_ids") or []
    if len(participant_ids) < 2:
        raise HTTPException(400, "参会员工至少 2 人")
    mid = execute(
        "INSERT INTO meetings(topic, host_employee_id, scheduler_mode, max_rounds, token_budget) "
        "VALUES (?,?,?,?,?)",
        (topic, body.get("host_employee_id"),
         body.get("scheduler_mode", "round_robin"),
         body.get("max_rounds", 6), body.get("token_budget", 60000)))
    # 参会名单以预置发言形式落库，供编排层识别
    for pid in participant_ids:
        execute("INSERT INTO meeting_messages(meeting_id, employee_id, role, content, tokens) "
                "VALUES (?,?,?,?,0)", (mid, pid, "member", "[已入会]"))
    return {"id": mid}


@router.get("/{mid}")
def get_meeting(mid: int):
    m = query_one("SELECT * FROM meetings WHERE id=?", (mid,))
    if not m:
        raise HTTPException(404, "会议不存在")
    m["decisions"] = json.loads(m.pop("decisions_json") or "[]")
    m["todos"] = json.loads(m.pop("todos_json") or "[]")
    msgs = query("SELECT * FROM meeting_messages WHERE meeting_id=? AND role != 'member' "
                 "OR (meeting_id=? AND content NOT LIKE '[已入会]%') ORDER BY id", (mid, mid))
    names = {e["id"]: e["name"] for e in query("SELECT id, name FROM employees")}
    for msg in msgs:
        msg["employee_name"] = names.get(msg.get("employee_id"), "用户")
        msg.pop("meeting_id", None)
    m["messages"] = msgs
    m["participants"] = [
        {"id": r["employee_id"], "name": names.get(r["employee_id"], "?")}
        for r in query("SELECT DISTINCT employee_id FROM meeting_messages "
                       "WHERE meeting_id=? AND content='[已入会]'", (mid,)) if r["employee_id"]]
    m["cost_estimate"] = estimate_cost(m)
    return m


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
                ev = await asyncio.wait_for(rt.queue.get(), timeout=600)
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


@router.post("/{mid}/speak")
async def user_speak(mid: int, body: dict):
    """用户插话（F-MTG-06）：写入 user 消息；可带 next_employee_id 指定下一位。"""
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "内容必填")
    execute("INSERT INTO meeting_messages(meeting_id, employee_id, role, content, tokens) "
            "VALUES (?,?, 'user', ?, ?)",
            (mid, None, content, estimate_tokens(content)))
    rt = get_runtime(mid)
    if body.get("next_employee_id"):
        rt.pending_next = body["next_employee_id"]
    return {"ok": True}


@router.delete("/{mid}")
def delete_meeting(mid: int):
    stop_meeting(mid)
    execute("DELETE FROM meetings WHERE id=?", (mid,))
    return {"ok": True}
