"""多 Agent 会议编排（PRD 4.3）：发言调度、上下文可见性、轮次/token 预算、纪要入库。

调度策略 F-MTG-02：round_robin 轮流 / host_pick 主持人指定（LLM 输出 JSON 决定下一位，
用户可通过 API 覆盖）/ ai_pick 由上一位发言者指定。
"""
import asyncio
import json
from typing import Any

from . import llm_gateway
from .db import execute, fts_sync, query, query_one

SUMMARY_PROMPT = (
    "你是会议纪要助手。根据以下会议记录，输出严格的 JSON（不要代码块）：\n"
    '{"summary": "不超过200字摘要", "decisions": ["决议1", "..."], '
    '"todos": [{"task": "事项", "owner": "负责人"}]}\n\n会议记录：\n{transcript}'
)

HOST_PICK_PROMPT = (
    "你是主持人。请从参会人中选择下一位发言者，只输出 JSON："
    '{"next": "员工姓名"}\n参会人：{names}'
)


def _default_llm_config() -> dict | None:
    return query_one("SELECT * FROM llm_configs WHERE is_default=1 LIMIT 1") \
        or query_one("SELECT * FROM llm_configs ORDER BY id LIMIT 1")


def _llm_of(emp: dict) -> dict:
    """员工级绑定 → 全局默认 → mock 兜底（保证离线可跑通）。"""
    if emp.get("llm_config_id"):
        cfg = query_one("SELECT * FROM llm_configs WHERE id=?", (emp["llm_config_id"],))
        if cfg:
            cfg["kind"] = "llm"
            return cfg
    cfg = _default_llm_config()
    if cfg:
        cfg["kind"] = "llm"
        return cfg
    return {"kind": "llm", "provider": "mock", "model_id": "mock-large", "credential_id": None,
            "temperature": 0.7, "max_tokens": 2048, "extra_params_json": "{}"}


def _system_prompt(emp: dict, topic: str) -> str:
    role = (emp.get("role_prompt") or "").replace("{{meeting_topic}}", topic)
    persona = emp.get("personality_prompt") or ""
    return (
        f"你是一场多 Agent 协作会议中的「{emp['name']}」（岗位角色）。\n"
        f"议题：{topic}\n岗位职责：{role}\n性格：{persona}\n"
        "要求：发言简明、有观点、不重复他人已说过的内容；单次发言不超过 200 字。"
    )


class MeetingRuntime:
    def __init__(self, meeting_id: int):
        self.meeting_id = meeting_id
        self.queue: asyncio.Queue = asyncio.Queue()
        self.stop = False
        self.pending_next: int | None = None
        self.task: asyncio.Task | None = None

    async def emit(self, ev_type: str, **data: Any) -> None:
        await self.queue.put({"type": ev_type, **data})

    async def run(self) -> None:
        m = query_one("SELECT * FROM meetings WHERE id=?", (self.meeting_id,))
        if not m:
            await self.emit("error", message="会议不存在")
            await self.emit("end")
            return
        participants = query(
            "SELECT * FROM employees WHERE enabled=1 AND id IN "
            "(SELECT DISTINCT employee_id FROM meeting_messages WHERE meeting_id=?) "
            "ORDER BY id", (self.meeting_id,))
        if not participants:
            participants = query("SELECT * FROM employees WHERE enabled=1 ORDER BY id")
        if len(participants) < 2:
            await self.emit("error", message="参会员工不足 2 人，请先在组织架构中创建员工")
            await self.emit("end")
            return

        execute("UPDATE meetings SET status='running' WHERE id=?", (self.meeting_id,))
        await self.emit("start", participants=[{"id": p["id"], "name": p["name"]} for p in participants])

        topic = m["topic"]
        budget = m["token_budget"] or 60000
        max_rounds = m["max_rounds"] or 6
        mode = m["scheduler_mode"]
        host_id = m["host_employee_id"]
        used = m["total_tokens_used"] or 0
        warned = False
        idx = 0
        rounds_done = 0
        last_speaker_idx = -1

        while rounds_done < max_rounds and not self.stop:
            if used >= budget:
                await self.emit("budget_exhausted", used=used)
                break
            emp = participants[idx % len(participants)]

            # 可见性 F-MTG-03：主持人看全量，成员看最近 N 轮 + 被 @ 内容
            history = query(
                "SELECT role, content, employee_id FROM meeting_messages "
                "WHERE meeting_id=? ORDER BY id DESC LIMIT 40", (self.meeting_id,))
            history.reverse()
            msgs: list[dict] = [{"role": "system", "content": _system_prompt(emp, topic)}]
            if emp["id"] == host_id:
                visible = history
            else:
                visible = [h for h in history if h["employee_id"] == emp["id"]][-6:]
                visible += [h for h in history if h["role"] == "user"]
                visible = visible[:12]
            for h in visible:
                r = "assistant" if h["employee_id"] == emp["id"] else ("user" if h["role"] == "user" else "system")
                who = query_one("SELECT name FROM employees WHERE id=?", (h["employee_id"],)) if h["employee_id"] else None
                prefix = f"[{who['name']}] " if who else ""
                msgs.append({"role": r, "content": prefix + h["content"]})

            await self.emit("speaker_start", employee_id=emp["id"], name=emp["name"])
            cfg = _llm_of(emp)
            parts: list[str] = []
            err = None
            try:
                async for tok in llm_gateway.stream_chat(cfg, msgs, turn_index=idx):
                    if self.stop:
                        break
                    parts.append(tok)
                    await self.emit("token", employee_id=emp["id"], text=tok)
            except Exception as e:  # F-MTG-13：失败重试一次后跳过该员工
                try:
                    parts = []
                    async for tok in llm_gateway.stream_chat(cfg, msgs, turn_index=idx):
                        if self.stop:
                            break
                        parts.append(tok)
                        await self.emit("token", employee_id=emp["id"], text=tok)
                except Exception as e2:
                    err = str(e2)
            content = "".join(parts)
            tokens = llm_gateway.estimate_tokens(content)
            used += tokens
            execute(
                "INSERT INTO meeting_messages(meeting_id, employee_id, role, content, model_used, tokens, interrupted) "
                "VALUES (?,?,?,?,?,?,?)",
                (self.meeting_id, emp["id"], "host" if emp["id"] == host_id else "member",
                 content or (f"[发言失败] {err}" if err else "[已打断]"), cfg.get("model_id"), tokens,
                 1 if self.stop else 0))
            execute("UPDATE meetings SET total_tokens_used=? WHERE id=?", (used, self.meeting_id))

            if err:
                await self.emit("speaker_error", employee_id=emp["id"], name=emp["name"], message=err)
            await self.emit("speaker_end", employee_id=emp["id"], tokens=tokens, used=used)
            if not warned and used >= budget * 0.8:
                warned = True
                await self.emit("budget_warn", used=used, budget=budget)
            if self.stop:
                break

            last_speaker_idx = idx
            rounds_done += 1
            idx = await self._next_index(mode, participants, idx, host_id, topic, emp)
            await asyncio.sleep(0.05)

        # 纪要生成 F-MTG-07/08
        status = "stopped" if self.stop else "ended"
        execute("UPDATE meetings SET status=?, ended_at=datetime('now') WHERE id=?", (status, self.meeting_id))
        await self.emit("summary_start")
        summary_ev = await self._make_summary(m, participants)
        await self.emit("summary", **summary_ev)
        await self.emit("end", status=status, used=used)

    async def _next_index(self, mode: str, participants: list[dict], idx: int,
                          host_id: int, topic: str, emp: dict) -> int:
        # 用户覆盖（插话指定下一位）
        if self.pending_next is not None:
            for i, p in enumerate(participants):
                if p["id"] == self.pending_next:
                    self.pending_next = None
                    return i
            self.pending_next = None
        if mode == "round_robin" or len(participants) < 2:
            return idx + 1
        # host_pick / ai_pick：由 LLM 决定下一位，解析失败回退轮流
        names = "、".join(p["name"] for p in participants)
        try:
            raw = await llm_gateway.chat_once(
                _llm_of(emp),
                [{"role": "system", "content": HOST_PICK_PROMPT.format(names=names)},
                 {"role": "user", "content": f"议题：{topic}，刚才由 {emp['name']} 发言。"}],
                turn_index=idx)
            nxt = json.loads(raw.strip().strip("`").removeprefix("json").strip()).get("next")
            for i, p in enumerate(participants):
                if p["name"] == nxt:
                    return i
        except Exception:
            pass
        return idx + 1

    async def _make_summary(self, m: dict, participants: list[dict]) -> dict:
        rows = query("SELECT employee_id, content FROM meeting_messages WHERE meeting_id=? ORDER BY id",
                     (self.meeting_id,))
        if not rows:
            return {"summary": "会议无有效发言", "decisions": [], "todos": []}
        transcript = "\n".join(
            f"{(query_one('SELECT name FROM employees WHERE id=?', (r['employee_id'],)) or {}).get('name', '?')}：{r['content']}"
            for r in rows)[:12000]
        host = query_one("SELECT * FROM employees WHERE id=?", (m["host_employee_id"],)) or participants[0]
        cfg = _llm_of(host)
        raw = ""
        try:
            raw = await llm_gateway.chat_once(
                cfg, [{"role": "system", "content": SUMMARY_PROMPT},
                      {"role": "user", "content": transcript}])
        except Exception:
            raw = ""
        summary, decisions, todos = "", [], []
        try:
            txt = raw.strip().strip("`").removeprefix("json").strip()
            data = json.loads(txt)
            summary = data.get("summary", "")
            decisions = data.get("decisions", [])
            todos = data.get("todos", [])
        except Exception:
            summary = (raw or transcript)[:300]
        if not summary:
            summary = transcript[:300]
        execute("UPDATE meetings SET summary=?, decisions_json=?, todos_json=? WHERE id=?",
                (summary, json.dumps(decisions, ensure_ascii=False),
                 json.dumps(todos, ensure_ascii=False), self.meeting_id))

        # 纪要入库（events + knowledge_items + FTS）
        ev_id = execute("INSERT INTO events(type, ref_id) VALUES ('meeting', ?)", (self.meeting_id,))
        title = f"会议纪要：{m['topic']}"
        body = summary + "\n\n决议：\n" + "\n".join(f"- {d}" for d in decisions) + \
            "\n\n待办：\n" + "\n".join(f"- {t.get('task','')}（{t.get('owner','')}）" for t in todos if isinstance(t, dict))
        item_id = execute(
            "INSERT INTO knowledge_items(title, content, source_event_id, tags) VALUES (?,?,?,?)",
            (title, body, ev_id, "会议纪要"))
        fts_sync(item_id, title, body)
        return {"summary": summary, "decisions": decisions, "todos": todos,
                "knowledge_item_id": item_id}


_runtimes: dict[int, MeetingRuntime] = {}


def get_runtime(meeting_id: int) -> MeetingRuntime:
    rt = _runtimes.get(meeting_id)
    if rt is None or rt.task is not None and rt.task.done():
        rt = MeetingRuntime(meeting_id)
        _runtimes[meeting_id] = rt
    return rt


def start_meeting(meeting_id: int) -> MeetingRuntime:
    rt = get_runtime(meeting_id)
    if rt.task and not rt.task.done():
        return rt
    rt.stop = False
    rt.queue = asyncio.Queue()
    rt.task = asyncio.create_task(rt.run())
    return rt


def stop_meeting(meeting_id: int) -> bool:
    rt = _runtimes.get(meeting_id)
    if rt and rt.task and not rt.task.done():
        rt.stop = True
        return True
    return False
