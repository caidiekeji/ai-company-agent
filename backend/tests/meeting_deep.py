"""会议编排深度测试：调度策略、上下文可见性、打断、预算警示、失败跳过、模板、纪要结构。

运行：先启动服务，再 ./.venv/Scripts/python.exe tests/meeting_deep.py
"""
import json
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import httpx

BASE = "http://127.0.0.1:8756"
ok_count = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok_count
    if not cond:
        print(f"FAIL {name} {extra}")
        sys.exit(1)
    ok_count += 1
    print(f"ok   {name} {extra}")


def make_emps(c, names):
    ids = []
    for n, g in names:
        r = c.post("/api/employees", json={
            "name": n, "gender": g,
            "role_prompt": "围绕议题给出专业意见。",
            "personality_prompt": "务实。",
        })
        if r.status_code == 200:
            ids.append(r.json()["id"])
    return ids


def run_and_collect(c, mid, timeout=120):
    events = []
    with c.stream("POST", f"/api/meetings/{mid}/run", timeout=timeout) as resp:
        cur = None
        for line in resp.iter_lines():
            if line.startswith("event:"):
                cur = line[6:].strip()
                events.append(cur)
            elif line.startswith("data:") and cur:
                try:
                    events[-1] = cur
                except Exception:
                    pass
            if cur == "end":
                break
    return events


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=180)

    # 准备员工（唯一名避免冲突）
    stamp = str(int(time.time()))[-5:]
    emps = make_emps(c, [(f"深测甲{stamp}", "女"), (f"深测乙{stamp}", "男"), (f"深测丙{stamp}", "女")])
    check("setup.employees", len(emps) == 3, str(emps))

    # 1. 三种调度策略都能跑完
    for mode in ("round_robin", "host_pick", "ai_pick"):
        r = c.post("/api/meetings", json={
            "topic": f"调度策略测试 {mode} {stamp}",
            "participant_ids": emps, "host_employee_id": emps[0],
            "scheduler_mode": mode, "max_rounds": 3, "token_budget": 40000,
        })
        mid = r.json()["id"]
        events = run_and_collect(c, mid)
        check(f"scheduler.{mode}", "end" in events and "speaker_start" in events,
              f"{events.count('speaker_start') if 'speaker_start' in events else 0} 次发言")

    # 2. 上下文可见性：成员只看最近 N 轮
    r = c.post("/api/meetings", json={
        "topic": f"可见性测试 {stamp}",
        "participant_ids": emps, "host_employee_id": emps[0],
        "scheduler_mode": "round_robin", "max_rounds": 2, "token_budget": 40000,
        "context_visibility": "recent_n", "visible_rounds": 2, "compress_context": True,
    })
    m = r.json()
    check("visibility.config", m["context_visibility"] == "recent_n" and m["visible_rounds"] == 2,
          f"{m['context_visibility']}/{m['visible_rounds']}")
    check("visibility.compress", m["compress_context"] == 1)

    # 3. 成本预估含元
    est = m["cost_estimate"]
    check("cost.cny", "¥" in est["cost_text"] and est["cost_high"] >= est["cost_low"],
          est["cost_text"])

    # 4. 插话 + 打断
    r = c.post(f"/api/meetings/{m['id']}/speak",
               json={"content": "请聚焦成本", "next_employee_id": emps[2], "interrupt": True})
    check("meeting.interrupt_api", r.status_code == 200 and r.json()["interrupted"] is True)
    r = c.get(f"/api/meetings/{m['id']}")
    check("meeting.user_msg_persisted",
          any(x["role"] == "user" and "成本" in x["content"] for x in r.json()["messages"]))

    # 5. 会议模板改写议题 + 轮次默认取模板值
    # 语义：模板轮次是默认值；显式传入 max_rounds 时以用户为准（前端选模板时已同步轮次输入框）
    r = c.post("/api/meetings", json={
        "topic": "季度规划", "participant_ids": emps,
        "template_id": "kickoff",
    })
    check("template.topic", "立项" in r.json()["topic"], r.json()["topic"])
    check("template.rounds.default", r.json()["max_rounds"] == 6, str(r.json()["max_rounds"]))
    r2 = c.post("/api/meetings", json={
        "topic": "季度规划", "participant_ids": emps,
        "template_id": "kickoff", "max_rounds": 2,
    })
    check("template.rounds.override", r2.json()["max_rounds"] == 2, str(r2.json()["max_rounds"]))

    # 6. 纪要结构：摘要 / 决议 / 待办 + 入库
    r = c.post("/api/meetings", json={
        "topic": f"纪要结构测试 {stamp}", "participant_ids": emps,
        "host_employee_id": emps[0], "max_rounds": 2, "token_budget": 40000,
    })
    mid = r.json()["id"]
    run_and_collect(c, mid)
    r = c.get(f"/api/meetings/{mid}")
    mm = r.json()
    check("summary.exists", bool(mm.get("summary")), (mm.get("summary") or "")[:36])
    check("summary.decisions", isinstance(mm.get("decisions"), list))
    check("summary.todos", isinstance(mm.get("todos"), list))
    check("summary.kb_link", bool(mm.get("knowledge_item_id")), f"条目 #{mm.get('knowledge_item_id')}")

    # 7. 知识库语义召回该纪要
    r = c.get("/api/kb/search", params={"q": "纪要结构测试"})
    check("kb.recall", len(r.json()["results"]) > 0, f"mode={r.json()['mode']}")

    # 8. 员工主动沉淀知识
    r = c.post(f"/api/meetings/{mid}/deposit", json={
        "employee_id": emps[0], "title": f"沉淀要点 {stamp}", "content": "关键结论：先做 MVP。"})
    check("meeting.deposit", r.status_code == 200 and r.json().get("knowledge_item_id"))

    # 9. 回放时间轴
    r = c.get(f"/api/meetings/{mid}/replay")
    tl = r.json()["timeline"]
    check("replay.timeline", len(tl) >= 2, f"{len(tl)} 条")
    check("replay.seq", all("seq" in t for t in tl))

    # 10. 会议记录回放不含 "[已入会]" 噪声
    r = c.get(f"/api/meetings/{mid}")
    check("replay.no_placeholder",
          all(x["content"] != "[已入会]" for x in r.json()["messages"]))

    # 11. 停止/中断幂等
    r = c.post(f"/api/meetings/{mid}/stop")
    check("meeting.stop_idempotent", r.status_code == 400, f"HTTP {r.status_code}（已结束）")

    print(f"\nPASS: {ok_count} 项会议深度测试全部通过")


if __name__ == "__main__":
    main()
