"""端到端冒烟：厂商→凭证→LLM配置→员工→会议流式→纪要入库→知识检索。

运行：先启动服务（python -m app.main），再 ./.venv/Scripts/python.exe tests/smoke.py
"""
import json
import sys

# Windows（CI cp1252 控制台）打印中文兜底
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


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=60)

    # 1. 健康检查
    r = c.get("/api/health")
    check("health", r.status_code == 200 and r.json()["ok"])

    # 2. 厂商清单
    r = c.get("/api/vendors/llm")
    ids = [v["id"] for v in r.json()]
    check("vendors", "deepseek" in ids and "mock" in ids, f"({len(ids)} 家)")

    # 3. 凭证（keyring 优先，失败回退加密文件）
    r = c.post("/api/credentials", json={"provider": "deepseek", "alias": "默认",
                                         "base_url": "https://api.deepseek.com/v1",
                                         "api_key": "sk-test-1234567890abcdef"})
    check("credential.create", r.status_code == 200, f"id={r.json().get('id')}")
    cred_id = r.json()["id"]
    r = c.get("/api/credentials")
    masked = r.json()[0]["api_key_masked"]
    check("credential.masked", "sk-test" not in masked and "••" in masked, masked)

    # 4. 凭证被引用时禁止删除
    r = c.post("/api/configs/llm", json={"provider": "deepseek", "credential_id": cred_id,
                                         "model_id": "deepseek-chat", "set_default": False})
    check("llm_config.create", r.status_code == 200, f"id={r.json().get('id')}")
    r = c.delete(f"/api/credentials/{cred_id}")
    check("credential.protected", r.status_code == 409, str(r.status_code))

    # 5. mock LLM 配置设为默认（离线可跑）
    r = c.post("/api/configs/llm", json={"provider": "mock", "model_id": "mock-large", "set_default": True})
    check("mock_config.default", r.status_code == 200, f"id={r.json().get('id')}")

    # 6. 连接测试
    mock_id = r.json()["id"]
    r = c.post(f"/api/configs/llm/{mock_id}/test")
    check("llm.test", r.json().get("ok") is True, r.json().get("message", ""))

    # 7. 组织架构
    r = c.post("/api/departments", json={"name": "产品部"})
    dept_id = r.json()["id"]
    emps = []
    for name, gender in [("产品经理", "女"), ("工程师", "男"), ("市场", "女")]:
        r = c.post("/api/employees", json={
            "name": name, "gender": gender, "dept_id": dept_id,
            "role_prompt": "围绕议题给出专业意见，结论先行。",
            "personality_prompt": "务实、简洁。"})
        check(f"employee.{name}", r.status_code == 200)
        emps.append(r.json()["id"])

    # 8. 创建会议（3 人、2 轮）
    r = c.post("/api/meetings", json={
        "topic": "是否开发一款 AI 会议纪要工具",
        "participant_ids": emps, "host_employee_id": emps[0],
        "scheduler_mode": "round_robin", "max_rounds": 2, "token_budget": 30000})
    check("meeting.create", r.status_code == 200, f"id={r.json().get('id')}")
    mid = r.json()["id"]

    # 成本预估
    r = c.get(f"/api/meetings/{mid}")
    check("meeting.cost_estimate", r.json()["cost_estimate"]["est_tokens"] > 0)

    # 9. SSE 流式运行
    events: list[str] = []
    tokens_seen = 0
    summary_ev = None
    with c.stream("POST", f"/api/meetings/{mid}/run") as resp:
        check("sse.status", resp.status_code == 200, resp.headers.get("content-type", ""))
        cur_ev = None
        for line in resp.iter_lines():
            if line.startswith("event:"):
                cur_ev = line[6:].strip()
                events.append(cur_ev)
            elif line.startswith("data:") and cur_ev == "token":
                tokens_seen += 1
            elif line.startswith("data:") and cur_ev == "summary":
                summary_ev = json.loads(line[5:].strip())
            if cur_ev == "end":
                break
    check("sse.streamed", "speaker_start" in events and "token" in events and "end" in events,
          f"{tokens_seen} 个 token 片段")
    check("sse.summary", summary_ev is not None and summary_ev.get("summary"),
          (summary_ev or {}).get("summary", "")[:40])
    check("sse.kb_written", (summary_ev or {}).get("knowledge_item_id"))

    # 10. 会议详情与纪要
    r = c.get(f"/api/meetings/{mid}")
    m = r.json()
    check("meeting.status", m["status"] == "ended", m["status"])
    speeches = [x for x in m["messages"] if x["role"] in ("host", "member")]
    check("meeting.messages", len(speeches) >= 2,
          f"{len(speeches)} 条发言 / {len(m['messages'])} 条记录")

    # 11. 知识库检索（FTS5 trigram / LIKE 回退）
    r = c.get("/api/kb/search", params={"q": "会议纪要工具"})
    data = r.json()
    check("kb.search", len(data["results"]) > 0, f"mode={data['mode']}")

    # 12. 凭证多账号别名
    r = c.post("/api/credentials", json={"provider": "deepseek", "alias": "备用",
                                         "api_key": "sk-test-0000000000000000"})
    check("credential.alias", r.status_code == 200)
    r = c.post("/api/credentials", json={"provider": "deepseek", "alias": "备用",
                                         "api_key": "sk-dup"})
    check("credential.alias.dup", r.status_code == 409)

    print(f"\nPASS: {ok_count} 项冒烟全部通过")


if __name__ == "__main__":
    main()
