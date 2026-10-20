"""LLM 网关：统一 OpenAI 兼容 + Anthropic/Gemini 轻量适配 + mock 离线通道。

决策（相对 PRD §7）：M1 先用 httpx 薄适配层实现 3 种 api_format，
不引入 LiteLLM（依赖树过重，且与 G0 打包体积目标冲突）；
Provider 接口保持一致，后续可在不改编排层的前提下替换为 LiteLLM。
"""
import asyncio
import json
from typing import AsyncIterator

import httpx

from .providers import vendor_of
from .security import load_secret

MOCK_SPEAKERS = [
    "从产品角度看，这个方案的核心价值在于把多角色协作做成开箱即用，我建议先明确 MVP 边界。",
    "技术上可行，需要注意上下文窗口差异与失败重试，我倾向于先做编排引擎的最小闭环。",
    "市场侧关注差异化表达，建议围绕'数据不出域'作为主卖点，竞品对比要补上。",
    "我同意上面的意见，并补充：知识库沉淀是长期留存的关键，第一版就要打通会议纪要入库。",
    "综合大家意见，我建议本次先锁定三项决议，其余待办放到下一轮讨论。",
]


def _resolve(cfg: dict) -> dict:
    """组装运行时参数：base_url / api_key / api_format。"""
    cred = None
    if cfg.get("credential_id"):
        from .db import query_one

        cred = query_one("SELECT * FROM provider_credentials WHERE id=?", (cfg["credential_id"],))
    api_key = load_secret(cred["api_key_ref"]) if cred else None
    vendor = vendor_of(cfg.get("kind", "llm"), cfg.get("provider", ""))
    fmt = vendor["api_format"] if vendor else "openai"
    base_url = (cred and cred.get("base_url")) or (vendor and vendor.get("base_url")) or ""
    return {"api_format": fmt, "base_url": base_url.rstrip("/"), "api_key": api_key,
            "model": cfg.get("model_id") or "", "vendor_id": vendor["id"] if vendor else None}


def estimate_tokens(text: str) -> int:
    """无 tiktoken 依赖的粗估：中文 ~1.5 字符/token，英文 ~4 字符/token。"""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return max(1, int(cjk / 1.5 + other / 4))


async def _stream_openai(base_url: str, api_key: str | None, model: str,
                         messages: list[dict], temperature: float, max_tokens: int,
                         extra: dict | None = None) -> AsyncIterator[str]:
    payload = {"model": model, "messages": messages, "stream": True,
               "temperature": temperature, "max_tokens": max_tokens, **(extra or {})}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        async with client.stream("POST", f"{base_url}/chat/completions", json=payload, headers=headers) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode(errors="replace")[:500]
                raise RuntimeError(f"LLM 接口错误 {resp.status_code}: {body}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {})
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta.get("content"):
                    yield delta["content"]


async def _stream_anthropic(base_url: str, api_key: str | None, model: str,
                            messages: list[dict], max_tokens: int) -> AsyncIterator[str]:
    sys_text = "\n".join(m["content"] for m in messages if m["role"] == "system")
    chat = [m for m in messages if m["role"] != "system"]
    payload = {"model": model, "max_tokens": max_tokens, "messages": chat, "stream": True}
    if sys_text:
        payload["system"] = sys_text
    headers = {"x-api-key": api_key or "", "anthropic-version": "2023-06-01",
               "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        async with client.stream("POST", f"{base_url}/v1/messages", json=payload, headers=headers) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode(errors="replace")[:500]
                raise RuntimeError(f"Anthropic 接口错误 {resp.status_code}: {body}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "content_block_delta":
                    text = ev.get("delta", {}).get("text")
                    if text:
                        yield text


async def _stream_gemini(base_url: str, api_key: str | None, model: str,
                         messages: list[dict], max_tokens: int) -> AsyncIterator[str]:
    sys_text = "\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
                for m in messages if m["role"] != "system"]
    url = f"{base_url}/models/{model}:streamGenerateContent?alt=sse&key={api_key or ''}"
    payload = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
    if sys_text:
        payload["systemInstruction"] = {"parts": [{"text": sys_text}]}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        async with client.stream("POST", url, json=payload) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode(errors="replace")[:500]
                raise RuntimeError(f"Gemini 接口错误 {resp.status_code}: {body}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    cand = json.loads(line[5:].strip())["candidates"][0]
                    parts = cand.get("content", {}).get("parts", [])
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                for p in parts:
                    if p.get("text"):
                        yield p["text"]


async def _stream_mock(messages: list[dict], turn_index: int = 0) -> AsyncIterator[str]:
    """离线演示/测试通道：无需 API Key 即可跑通会议链路。"""
    text = MOCK_SPEAKERS[turn_index % len(MOCK_SPEAKERS)]
    for ch in text:
        await asyncio.sleep(0.012)
        yield ch


async def stream_chat(kind_cfg: dict, messages: list[dict],
                      turn_index: int = 0) -> AsyncIterator[str]:
    """按配置流式对话。kind_cfg 需含 kind/provider/model_id/credential_id。"""
    rt = _resolve(kind_cfg)
    temperature = kind_cfg.get("temperature") or 0.7
    max_tokens = kind_cfg.get("max_tokens") or 2048
    extra = None
    raw = kind_cfg.get("extra_params_json")
    if raw and isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            extra = json.loads(raw)
        except json.JSONDecodeError:
            extra = None
    if rt["api_format"] == "mock":
        async for t in _stream_mock(messages, turn_index):
            yield t
        return
    if rt["api_format"] == "anthropic":
        async for t in _stream_anthropic(rt["base_url"], rt["api_key"], rt["model"], messages, max_tokens):
            yield t
    elif rt["api_format"] == "gemini":
        async for t in _stream_gemini(rt["base_url"], rt["api_key"], rt["model"], messages, max_tokens):
            yield t
    else:
        async for t in _stream_openai(rt["base_url"], rt["api_key"], rt["model"], messages,
                                      temperature, max_tokens, extra):
            yield t


async def chat_once(kind_cfg: dict, messages: list[dict], turn_index: int = 0) -> str:
    parts = []
    async for t in stream_chat(kind_cfg, messages, turn_index):
        parts.append(t)
    return "".join(parts)


async def fetch_models(vendor_id: str, base_url: str, api_key: str | None,
                       kind: str) -> dict:
    """获取模型列表：标准接口优先，无接口走内置清单兜底（PRD F-LLM-03/F-ASR-03）。"""
    from .providers import BUILTIN_MODELS, vendor_of

    vendor = vendor_of(kind, vendor_id) or {}
    fmt = vendor.get("api_format", "openai")
    if fmt == "mock":
        return {"source": "builtin", "models": ["mock-large", "mock-mini"]}
    if fmt in ("local", "cloud_asr", "cloud_tts"):
        builtin = BUILTIN_MODELS.get(vendor_id, [])
        if builtin:
            return {"source": "builtin", "models": builtin}
        return {"source": "manual", "models": [], "note": "该厂商无列表接口，请手动填写 model_id"}
    if not base_url:
        return {"source": "manual", "models": BUILTIN_MODELS.get(vendor_id, []),
                "note": "请填写 Base URL 或手动输入 model_id"}
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}")
            data = resp.json()
            ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
            if not ids:
                raise RuntimeError("空列表")
            return {"source": "api", "models": sorted(ids)}
    except Exception as e:
        builtin = BUILTIN_MODELS.get(vendor_id, [])
        return {"source": "fallback", "models": builtin, "note": f"接口拉取失败（{e}），已回退内置清单"}


async def test_connection(kind_cfg: dict) -> dict:
    """连接测试：LLM 发一条极小请求；ASR/TTS 测鉴权/文件可用性。"""
    kind = kind_cfg.get("kind", "llm")
    rt = _resolve(kind_cfg)
    if rt["api_format"] == "mock":
        return {"ok": True, "message": "Mock 通道（离线演示）"}
    if kind == "llm":
        try:
            text = await chat_once(kind_cfg, [{"role": "user", "content": "回复 OK"}])
            return {"ok": True, "message": f"连通，模型回复：{text[:40]}"}
        except Exception as e:
            return {"ok": False, "message": str(e)}
    # ASR/TTS：无凭证的本地引擎测模型包；云厂商做一次鉴权探测
    if kind_cfg.get("engine_type") == "local":
        import os

        p = kind_cfg.get("model_path")
        if p and os.path.exists(p):
            return {"ok": True, "message": "本地模型包可用"}
        return {"ok": True, "message": "本地引擎占位通过（模型包下载在 M2 完整接入）"}
    if rt["api_format"] in ("openai_tts", "openai_asr") and rt["base_url"]:
        headers = {"Authorization": f"Bearer {rt['api_key'] or ''}"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{rt['base_url']}/models", headers=headers)
                ok = resp.status_code < 400
                return {"ok": ok, "message": f"鉴权{'通过' if ok else '失败'}（HTTP {resp.status_code}）"}
        except Exception as e:
            return {"ok": False, "message": str(e)}
    return {"ok": True, "message": "已保存，接入该厂商接口后可完整测试"}
