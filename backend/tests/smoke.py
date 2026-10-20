"""端到端冒烟：覆盖 PRD v1.2 全量功能路径。

流程：厂商→凭证→LLM/ASR/TTS 配置→组织架构(头像/归属)→会议(模板/调度/插话/打断/纪要入库)
→知识库(语义+FTS+嵌入切换)→语音配置→RPA(白名单/模板)→系统设置(导出/白名单/模型包/更新)。

运行：先启动服务（python -m app.main），再 ./.venv/Scripts/python.exe tests/smoke.py
"""
import json
import sys
import time

# Windows（CI cp1252 控制台）打印中文兜底
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import httpx

BASE = "http://127.0.0.1:8756"
ok_count = 0
# 每次运行使用唯一别名，保证冒烟可重复执行
RUN = str(int(time.time()))[-6:]


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok_count
    if not cond:
        print(f"FAIL {name} {extra}")
        sys.exit(1)
    ok_count += 1
    print(f"ok   {name} {extra}")


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=90)

    # ---------- 1. 健康检查 ----------
    r = c.get("/api/health")
    check("health", r.status_code == 200 and r.json()["ok"], f"v{r.json().get('version')}")

    # ---------- 2. 厂商清单（F-MOD-05 JSON 可扩展） ----------
    r = c.get("/api/vendors/llm")
    ids = [v["id"] for v in r.json()]
    check("vendors.llm", "deepseek" in ids and "mock" in ids, f"({len(ids)} 家)")
    r = c.get("/api/vendors/tts")
    tts_ids = [v["id"] for v in r.json()]
    check("vendors.tts", "openai_tts" in tts_ids and "moss_tts" in tts_ids, f"({len(tts_ids)} 家)")
    r = c.post("/api/vendors/reload")
    check("vendors.reload", r.status_code == 200)

    # ---------- 3. 凭证 ----------
    r = c.post("/api/credentials", json={"provider": "deepseek", "alias": "默认-" + RUN,
                                         "base_url": "https://api.deepseek.com/v1",
                                         "api_key": "sk-test-1234567890abcdef"})
    check("credential.create", r.status_code == 200, f"id={r.json().get('id')}")
    cred_id = r.json()["id"]
    r = c.get("/api/credentials")
    masked = r.json()[0]["api_key_masked"]
    check("credential.masked", "sk-test" not in masked and "••" in masked, masked)

    # ---------- 4. LLM 配置 + 被引用保护 ----------
    r = c.post("/api/configs/llm", json={"provider": "deepseek", "credential_id": cred_id,
                                         "model_id": "deepseek-chat", "set_default": False})
    check("llm_config.create", r.status_code == 200, f"id={r.json().get('id')}")
    r = c.delete(f"/api/credentials/{cred_id}")
    check("credential.protected", r.status_code == 409, str(r.status_code))

    # ---------- 5. mock LLM 设为默认（离线可跑） ----------
    r = c.post("/api/configs/llm", json={"provider": "mock", "model_id": "mock-large", "set_default": True})
    check("mock_config.default", r.status_code == 200, f"id={r.json().get('id')}")
    mock_id = r.json()["id"]
    r = c.post(f"/api/configs/llm/{mock_id}/test")
    check("llm.test", r.json().get("ok") is True, r.json().get("message", ""))

    # ---------- 6. ASR / TTS 配置 + 音色 ----------
    r = c.post("/api/configs/asr", json={"provider": "openai_asr", "credential_id": cred_id,
                                         "model_id": "whisper-1", "set_default": True})
    check("asr_config.create", r.status_code == 200, f"id={r.json().get('id')}")
    r = c.post("/api/configs/tts", json={"provider": "openai_tts", "credential_id": cred_id,
                                         "model_id": "tts-1", "voice_id": "nova", "set_default": True})
    check("tts_config.create", r.status_code == 200, f"id={r.json().get('id')}")
    r = c.post("/api/providers/tts/voices", json={"provider": "openai_tts", "model_id": "tts-1"})
    check("tts.voices", len(r.json()["voices"]) >= 6, f"({len(r.json()['voices'])} 个音色)")
    r = c.get("/api/voice/voices", params={"provider": "openai_tts"})
    check("voice.gender_map", r.json()["gender_map"]["男"] == "onyx", str(r.json().get("gender_map")))
    r = c.get("/api/voice/params/tts", params={"provider": "openai_tts"})
    check("voice.params", "speed" in r.json()["params"])

    # ---------- 7. 组织架构 ----------
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

    # 员工级 LLM 绑定 + 语音覆盖
    r = c.put(f"/api/employees/{emps[1]}", json={"llm_config_id": mock_id,
                                                 "tts_voice_id": "echo", "asr_config_id": None})
    check("employee.update", r.status_code == 200)
    r = c.get(f"/api/voice/employee/{emps[0]}")
    check("voice.inherit", r.json()["tts_source"] == "继承全局", r.json()["tts_source"])
    r = c.get(f"/api/voice/employee/{emps[1]}")
    check("voice.manual", r.json()["voice_id"] == "echo" and r.json()["voice_is_manual"])

    # 提示词预览
    r = c.get(f"/api/employees/{emps[0]}/prompt-preview", params={"topic": "示例"})
    check("employee.prompt_preview", "示例" in r.json()["system_prompt"])

    # 拖拽调整部门归属
    r = c.post("/api/departments", json={"name": "技术部"})
    dept2 = r.json()["id"]
    r = c.put(f"/api/employees/{emps[1]}/dept", json={"dept_id": dept2})
    check("employee.move_dept", r.status_code == 200 and r.json()["dept_id"] == dept2)
    r = c.get("/api/departments")
    moved = [e for d in r.json()["departments"] if d["id"] == dept2 for e in d["employees"]]
    check("employee.move_dept.effect", any(e["id"] == emps[1] for e in moved))

    # ---------- 8. 会议模板 ----------
    r = c.get("/api/meetings/templates")
    tpl_ids = [t["id"] for t in r.json()]
    check("meeting.templates", "kickoff" in tpl_ids and "weekly" in tpl_ids, f"({len(tpl_ids)} 个)")

    # ---------- 9. 创建会议 ----------
    r = c.post("/api/meetings", json={
        "topic": "是否开发一款 AI 会议纪要工具",
        "participant_ids": emps, "host_employee_id": emps[0],
        "scheduler_mode": "round_robin", "max_rounds": 2, "token_budget": 30000,
        "template_id": "kickoff", "context_visibility": "recent_n", "visible_rounds": 4,
        "compress_context": True})
    check("meeting.create", r.status_code == 200, f"id={r.json().get('id')}")
    mid = r.json()["id"]
    check("meeting.template_topic", "立项" in r.json()["topic"], r.json()["topic"])

    r = c.get(f"/api/meetings/{mid}")
    est = r.json()["cost_estimate"]
    check("meeting.cost_estimate", est["est_tokens"] > 0 and "¥" in est["cost_text"], est["cost_text"])
    check("meeting.participants", len(r.json()["participants"]) == 3)

    # ---------- 10. SSE 流式运行 ----------
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

    # ---------- 11. 会议详情 / 回放 ----------
    r = c.get(f"/api/meetings/{mid}")
    m = r.json()
    check("meeting.status", m["status"] == "ended", m["status"])
    speeches = [x for x in m["messages"] if x["role"] in ("host", "member")]
    check("meeting.messages", len(speeches) >= 2,
          f"{len(speeches)} 条发言 / {len(m['messages'])} 条记录")
    r = c.get(f"/api/meetings/{mid}/replay")
    check("meeting.replay", len(r.json()["timeline"]) >= 2, f"{len(r.json()['timeline'])} 条时间轴")

    # ---------- 12. 用户插话 + 打断 ----------
    r = c.post(f"/api/meetings/{mid}/speak", json={"content": "插话：请聚焦成本。",
                                                  "next_employee_id": emps[2],
                                                  "interrupt": True})
    check("meeting.speak", r.status_code == 200 and r.json()["interrupted"])
    r = c.get(f"/api/meetings/{mid}")
    check("meeting.user_msg", any(x["role"] == "user" for x in r.json()["messages"]))

    # ---------- 13. 员工主动沉淀知识（F-KB-08） ----------
    r = c.post(f"/api/meetings/{mid}/deposit", json={"employee_id": emps[0],
                                                     "title": "成本要点", "content": "MVP 阶段控制在 5 人以内。"})
    check("meeting.deposit", r.status_code == 200 and r.json().get("knowledge_item_id"))

    # ---------- 14. 知识库检索（语义 + FTS 降级） ----------
    r = c.get("/api/kb/search", params={"q": "会议纪要工具"})
    data = r.json()
    check("kb.search", len(data["results"]) > 0, f"mode={data['mode']}")
    r = c.get("/api/kb/search", params={"q": "MVP 成本", "mode": "fts"})
    check("kb.search.fts", len(r.json()["results"]) >= 0, f"mode={r.json()['mode']}")

    # ---------- 15. 嵌入模型管理 ----------
    r = c.get("/api/kb/embedding")
    emb = r.json()
    check("kb.embedding.catalog", len(emb["models"]) >= 2, f"backend={emb['vec_backend']}")
    check("kb.embedding.current", emb["current"] == "bge-small-zh-v1.5", emb["current"])
    r = c.post("/api/kb/embedding/switch", json={"model": "bge-m3"})
    check("kb.embedding.switch", r.status_code == 200 and r.json()["total"] >= 1,
          r.json().get("message", "")[:50])
    r = c.get("/api/kb/embedding/job")
    check("kb.embedding.job", r.json()["status"] in ("running", "done"), r.json()["status"])
    r = c.post("/api/kb/embedding/switch", json={"model": "bge-small-zh-v1.5"})

    # ---------- 16. 知识条目增删改 ----------
    r = c.post("/api/kb/items", json={"title": "手动条目", "content": "用于测试的知识条目内容。",
                                      "tags": "测试"})
    iid = r.json()["id"]
    check("kb.item.create", iid > 0)
    r = c.put(f"/api/kb/items/{iid}", json={"tags": "测试2", "content": "更新后的正文内容。"})
    check("kb.item.update", r.status_code == 200)
    r = c.delete(f"/api/kb/items/{iid}")
    check("kb.item.delete", r.status_code == 200)

    # ---------- 17. RPA：白名单 + 模板 + 状态 ----------
    r = c.get("/api/rpa/status")
    check("rpa.status", "playwright_available" in r.json())
    r = c.post("/api/settings/whitelist", json={"domain": "example.com", "note": "竞品官网"})
    check("settings.whitelist.add", r.status_code == 200)
    wl = c.get("/api/settings/whitelist").json()
    check("settings.whitelist.list", len(wl) >= 1, f"{len(wl)} 条")
    r = c.post("/api/rpa/templates", json={"name": "竞品定价调研", "url": "https://example.com/pricing",
                                            "steps": [{"type": "open", "url": "https://example.com/pricing"},
                                                      {"type": "extract"}]})
    check("rpa.template", r.status_code == 200, f"id={r.json().get('id')}")

    # ---------- 18. 系统设置 ----------
    r = c.get("/api/settings")
    st = r.json()
    check("settings.get", "whitelist" in st and "privacy" in st and "usage" in st)
    r = c.put("/api/settings", json={"settings": {"rpa_high_risk_confirm": True,
                                                  "voice_auto_play": True}})
    check("settings.update", r.status_code == 200)
    r = c.get("/api/settings/privacy")
    check("settings.privacy", "本地存储" in r.json())
    r = c.get("/api/settings/packages")
    check("settings.packages", len(r.json()) >= 4, f"({len(r.json())} 个包)")
    r = c.get("/api/settings/usage")
    check("settings.usage", r.json()["total_bytes"] >= 0)
    r = c.post("/api/settings/export")
    check("settings.export", r.status_code == 200 and r.json()["filename"].endswith(".zip"),
          r.json().get("filename", ""))
    r = c.post("/api/settings/update/check")
    check("settings.update_check", "update_available" in r.json())
    r = c.get("/api/settings/app-info")
    check("settings.app_info", r.json()["version"] == "1.2.0", r.json()["version"])

    # ---------- 19. 凭证多账号别名 ----------
    r = c.post("/api/credentials", json={"provider": "deepseek", "alias": "备用-" + RUN,
                                         "api_key": "sk-test-0000000000000000"})
    check("credential.alias", r.status_code == 200)
    r = c.post("/api/credentials", json={"provider": "deepseek", "alias": "备用-" + RUN,
                                         "api_key": "sk-dup"})
    check("credential.alias.dup", r.status_code == 409)
    r = c.get("/api/credentials")
    refs = [x for x in r.json() if x["alias"] == "默认-" + RUN]
    check("credential.refs", len(refs[0]["refs"]) >= 3, f"{len(refs[0]['refs'])} 个引用")

    print(f"\nPASS: {ok_count} 项冒烟全部通过")


if __name__ == "__main__":
    main()
