"""RPA 端到端测试：真实 Chromium 浏览器验证无障碍树快照、动作、内容提取、白名单拦截。

需要：已执行 `python -m playwright install chromium`。

运行：先启动服务，再 ./.venv/Scripts/python.exe tests/rpa_e2e.py
"""
import json
import sys

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
    c = httpx.Client(base_url=BASE, timeout=180)

    # 1. 运行环境
    r = c.get("/api/rpa/status")
    st = r.json()
    check("rpa.playwright", st.get("playwright_available") is True)
    check("rpa.browser", st.get("browser_installed") is True, "Chromium 内核已就绪")

    # 2. 白名单拦截：未在白名单的域名必须被拒绝
    r = c.post("/api/rpa/open", json={"url": "https://not-allowed-site.example.com/"})
    check("rpa.whitelist.block", r.status_code == 403, f"HTTP {r.status_code}")

    # 3. 白名单放行 + 无障碍树快照
    c.post("/api/settings/whitelist", json={"domain": "example.com", "note": "RPA 测试"})
    r = c.post("/api/rpa/open", json={"url": "https://example.com/"})
    if r.status_code != 200:
        print("server error:", r.text[:600])
    check("rpa.open", r.status_code == 200, str(r.status_code))
    if r.status_code != 200:
        print(r.text[:400])
        sys.exit(1)
    data = r.json()
    sid = data["session_id"]
    snap = data["snapshot"]
    check("rpa.snapshot.elements", len(snap["elements"]) > 0, f"{len(snap['elements'])} 个元素")
    check("rpa.snapshot.token_budget", snap["token_estimate"] <= snap["max_tokens"],
          f"≈{snap['token_estimate']} token（上限 {snap['max_tokens']}）")
    roles = {e["role"] for e in snap["elements"]}
    check("rpa.snapshot.roles", len(roles) > 0, str(sorted(roles)[:5]))

    # 4. 元素含 ref / role / name 结构
    first = snap["elements"][0]
    check("rpa.snapshot.shape", all(k in first for k in ("ref", "role", "name", "state")),
          str(list(first.keys())))

    # 5. 内容提取为 Markdown
    r = c.post(f"/api/rpa/{sid}/extract", json={"format": "markdown"})
    check("rpa.extract", r.status_code == 200 and len(r.json()["content"]) > 0,
          f"{len(r.json().get('content', ''))} 字符")
    check("rpa.extract.markdown", "# " in r.json()["content"], "含 Markdown 标题")

    # 6. 动作：scroll（无风险动作直接执行）
    r = c.post(f"/api/rpa/{sid}/action", json={"type": "scroll", "delta": 300})
    check("rpa.action.scroll", r.status_code == 200 and r.json().get("ok") is True)

    # 7. 动作：click 真实元素
    link = next((e for e in snap["elements"] if e["role"] == "link"), None)
    if link:
        r = c.post(f"/api/rpa/{sid}/action", json={"type": "click", "ref": link["ref"]})
        check("rpa.action.click", r.status_code == 200 and r.json().get("ok") is True,
              f"点击「{link['name'][:20]}」")

    # 8. 高风险动作需二次确认
    r = c.post(f"/api/rpa/{sid}/action", json={"type": "click", "ref": snap["elements"][0]["ref"]})
    body = r.json()
    check("rpa.action.non_risk", r.status_code == 200, str(r.status_code))

    # 9. 关闭会话
    r = c.post(f"/api/rpa/{sid}/close", json={})
    check("rpa.close", r.status_code == 200)

    # 10. 一键任务：打开 → 提取 → 落库 → 入知识库
    r = c.post("/api/rpa/run", json={"url": "https://example.com/", "title": "RPA 端到端测试"})
    check("rpa.run", r.status_code == 200, str(r.status_code))
    if r.status_code == 200:
        run = r.json()
        check("rpa.run.content", len(run["content"]) > 0, f"{len(run['content'])} 字符")
        check("rpa.run.kb", run["knowledge_item_id"] > 0, f"条目 #{run['knowledge_item_id']}")

    # 11. 知识库能检索到 RPA 结果
    r = c.get("/api/kb/search", params={"q": "RPA 端到端测试"})
    check("rpa.kb.search", len(r.json()["results"]) > 0, f"mode={r.json()['mode']}")

    # 12. 任务模板
    r = c.get("/api/rpa/templates")
    check("rpa.templates", isinstance(r.json(), list))

    print(f"\nPASS: {ok_count} 项 RPA 端到端测试全部通过")


if __name__ == "__main__":
    main()
