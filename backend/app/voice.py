"""语音服务（PRD 4.4）：ASR / TTS Provider 可插拔，本地引擎 + 云厂商双路径。

约定：
- ASR：OpenAI 兼容 `/audio/transcriptions`（OpenAI / Groq / 硅基流动）走真实 HTTP；
  其余云厂商给出手动填写与适配位；本地引擎（sherpa-onnx / whisper.cpp）检测模型包可用性。
- TTS：OpenAI 兼容 `/audio/speech`（OpenAI）走真实 HTTP；Azure/阿里/腾讯/讯飞给适配位。
- 全部走 Provider 接口，新增厂商不改编排层（PRD 非功能需求）。
"""
import asyncio
import base64
import hashlib
import os
import time
from pathlib import Path

import httpx

from .db import DATA_DIR, MODELS_DIR, execute, query_one
from .providers import default_voice_for, vendor_of
from .security import load_secret

VOICE_DIR = DATA_DIR / "voice"
VOICE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------- 凭证与配置解析 ----------------
def _credential_of(cfg: dict) -> tuple[str | None, str]:
    base = ""
    key = None
    if cfg.get("credential_id"):
        row = query_one("SELECT * FROM provider_credentials WHERE id=?", (cfg["credential_id"],))
        if row:
            key = load_secret(row["api_key_ref"])
            base = row.get("base_url") or ""
    vendor = vendor_of(cfg.get("kind", "tts"), cfg.get("provider", "")) or {}
    if not base:
        base = vendor.get("base_url") or ""
    return key, base.rstrip("/")


def _extra(cfg: dict) -> dict:
    raw = cfg.get("extra_params_json")
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            import json

            return json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


# ---------------- ASR ----------------
async def transcribe(audio_path: str, cfg: dict) -> dict:
    """语音识别。返回 {text, duration_ms, engine}。"""
    engine = cfg.get("engine_type") or "cloud"
    provider = cfg.get("provider", "")
    if engine == "local":
        return await _transcribe_local(audio_path, cfg)
    key, base = _credential_of(cfg)
    if not base:
        return {"ok": False, "message": "该 ASR 配置缺少 Base URL", "text": ""}
    fmt = (vendor_of("asr", provider) or {}).get("api_format", "openai_asr")
    if fmt != "openai_asr":
        return {"ok": False, "message": f"厂商「{provider}」需专用 SDK 接入，当前请改用 OpenAI 兼容 ASR 或本地引擎",
                "text": ""}
    p = Path(audio_path)
    if not p.exists():
        return {"ok": False, "message": "音频文件不存在", "text": ""}
    extra = _extra(cfg)
    data = {"model": cfg.get("model_id") or "whisper-1"}
    for k in ("language", "prompt", "temperature"):
        if extra.get(k) is not None:
            data[k] = str(extra[k])
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            with p.open("rb") as f:
                resp = await client.post(f"{base}/audio/transcriptions",
                                        headers=headers, data=data,
                                        files={"file": (p.name, f, "audio/wav")})
            if resp.status_code >= 400:
                return {"ok": False, "message": f"ASR 接口错误 {resp.status_code}: {resp.text[:200]}", "text": ""}
            data_json = resp.json()
            return {"ok": True, "text": data_json.get("text", ""), "raw": data_json,
                    "engine": provider}
    except Exception as e:
        return {"ok": False, "message": f"ASR 请求失败：{e}", "text": ""}


async def _transcribe_local(audio_path: str, cfg: dict) -> dict:
    """本地引擎：sherpa-onnx / whisper.cpp 可执行探测 + 模型包检查。"""
    p = Path(audio_path)
    if not p.exists():
        return {"ok": False, "message": "音频文件不存在", "text": ""}
    model_path = cfg.get("model_path")
    provider = cfg.get("provider", "")
    if not model_path:
        return {"ok": False, "message": "本地 ASR 未配置模型包路径，请在配置中选择已下载的模型包", "text": ""}
    mp = Path(model_path)
    if not mp.exists():
        return {"ok": False, "message": f"本地模型包不存在：{model_path}", "text": ""}
    if provider == "whisper_cpp":
        exe = mp / "whisper-cli.exe" if os.name == "nt" else mp / "whisper-cli"
        if not exe.exists():
            exe = mp / "main"
        if exe.exists():
            try:
                proc = await asyncio.create_subprocess_exec(
                    str(exe), "-m", str(mp / "ggml-model.bin"), "-f", str(p),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                out, err = await proc.communicate()
                text = out.decode("utf-8", errors="replace")
                if proc.returncode == 0 and text.strip():
                    return {"ok": True, "text": text.strip(), "engine": "whisper.cpp"}
                return {"ok": False, "message": f"whisper.cpp 执行失败：{err.decode(errors='replace')[:200]}", "text": ""}
            except Exception as e:
                return {"ok": False, "message": f"whisper.cpp 启动失败：{e}", "text": ""}
    if provider == "sherpa_asr":
        try:
            import sherpa_onnx  # noqa: F401

            return {"ok": True, "text": "", "engine": "sherpa-onnx",
                    "message": "sherpa-onnx 已安装，解码需要模型包内的 tokens.txt，请确认模型包完整"}
        except ImportError:
            return {"ok": False, "message": "未安装 sherpa-onnx 运行时，请改用云 ASR 或下载本地运行时", "text": ""}
    return {"ok": False, "message": f"本地 ASR 引擎「{provider}」暂未实现解码", "text": ""}


# ---------------- TTS ----------------
def resolve_voice(cfg: dict, employee: dict | None) -> str | None:
    """音色解析：员工手动指定 → 性别映射 → 配置默认音色（F-VOI-04 / §6 继承规则）。"""
    if employee and employee.get("tts_voice_id"):
        return employee["tts_voice_id"]
    if employee:
        v = default_voice_for(cfg.get("provider", ""), employee.get("gender", "其他"))
        if v:
            return v
    return cfg.get("voice_id")


async def synthesize(text: str, cfg: dict, voice_id: str | None = None,
                     employee: dict | None = None) -> dict:
    """语音合成。返回 {ok, audio_path, mime, message}。"""
    if not (text or "").strip():
        return {"ok": False, "message": "文本为空"}
    engine = cfg.get("engine_type") or "cloud"
    if engine == "local":
        return await _synthesize_local(text, cfg, voice_id)
    key, base = _credential_of(cfg)
    if not base:
        return {"ok": False, "message": "该 TTS 配置缺少 Base URL"}
    provider = cfg.get("provider", "")
    fmt = (vendor_of("tts", provider) or {}).get("api_format", "openai_tts")
    voice = voice_id or cfg.get("voice_id") or "alloy"
    if fmt == "openai_tts":
        payload = {"model": cfg.get("model_id") or "tts-1", "voice": voice, "input": text}
        extra = _extra(cfg)
        if extra.get("speed"):
            payload["speed"] = float(extra["speed"])
        if extra.get("response_format"):
            payload["response_format"] = extra["response_format"]
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{base}/audio/speech", json=payload, headers=headers)
            if resp.status_code >= 400:
                return {"ok": False, "message": f"TTS 接口错误 {resp.status_code}: {resp.text[:200]}"}
            return _save_audio(resp.content, "mp3", cfg, voice, text)
        except Exception as e:
            return {"ok": False, "message": f"TTS 请求失败：{e}"}
    if fmt == "azure_tts":
        # Azure 需 SSML + 订阅 Key，base_url 形如 https://<region>.tts.speech.microsoft.com
        region = base.split("//")[-1].split(".")[0]
        url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        ssml = (f"<speak version='1.0' xml:lang='zh-CN'><voice xml:lang='zh-CN' name='{voice}'>"
                f"{_escape(text)}</voice></speak>")
        headers = {"Content-Type": "application/ssml+xml", "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3"}
        if key:
            headers["Ocp-Apim-Subscription-Key"] = key
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, content=ssml.encode("utf-8"), headers=headers)
            if resp.status_code >= 400:
                return {"ok": False, "message": f"Azure TTS 错误 {resp.status_code}: {resp.text[:200]}"}
            return _save_audio(resp.content, "mp3", cfg, voice, text)
        except Exception as e:
            return {"ok": False, "message": f"Azure TTS 请求失败：{e}"}
    return {"ok": False, "message": f"厂商「{provider}」的 TTS 接口需专用 SDK 接入，当前请改用 OpenAI 兼容 TTS 或本地引擎"}


def _escape(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


async def _synthesize_local(text: str, cfg: dict, voice_id: str | None) -> dict:
    model_path = cfg.get("model_path")
    if not model_path:
        return {"ok": False, "message": "本地 TTS 未配置模型包路径，请在配置中选择已下载的模型包"}
    mp = Path(model_path)
    if not mp.exists():
        return {"ok": False, "message": f"本地模型包不存在：{model_path}"}
    return {"ok": False, "message": f"本地 TTS 引擎「{cfg.get('provider')}」需在模型包内提供推理入口，"
                                     f"请参考附录 A 核实清单中的运行方式"}


def _save_audio(content: bytes, ext: str, cfg: dict, voice: str | None, text: str) -> dict:
    key = hashlib.md5(f"{cfg.get('id')}|{voice}|{text}".encode()).hexdigest()[:20]
    path = VOICE_DIR / f"{key}.{ext}"
    path.write_bytes(content)
    execute("INSERT OR IGNORE INTO voice_cache(text_hash, config_id, voice_id, audio_path, duration_ms) "
            "VALUES (?,?,?,?,?)", (key, cfg.get("id"), voice, str(path), 0))
    return {"ok": True, "audio_path": str(path),
            "url": f"/api/voice/audio/{key}.{ext}",
            "mime": "audio/mpeg" if ext == "mp3" else "audio/wav",
            "size": len(content)}


# ---------------- 连接测试 / 试听 ----------------
async def test_tts(cfg: dict) -> dict:
    r = await synthesize("这是一段测试语音，用于验证配置是否可用。", cfg, cfg.get("voice_id"))
    if r.get("ok"):
        return {"ok": True, "message": f"合成成功（{r.get('size', 0)} 字节）", "audio_url": r.get("url")}
    return {"ok": False, "message": r.get("message", "合成失败")}


async def test_asr(cfg: dict) -> dict:
    """ASR 连接测试：云厂商做轻量鉴权探测；本地引擎检查模型包。"""
    if cfg.get("engine_type") == "local":
        mp = cfg.get("model_path")
        if mp and Path(mp).exists():
            return {"ok": True, "message": "本地 ASR 模型包可用"}
        return {"ok": False, "message": "本地 ASR 模型包不存在或未配置路径"}
    key, base = _credential_of(cfg)
    if not base:
        return {"ok": False, "message": "缺少 Base URL"}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{base}/models", headers=headers)
        if resp.status_code < 400:
            return {"ok": True, "message": f"鉴权通过（HTTP {resp.status_code}）"}
        if resp.status_code in (401, 403):
            return {"ok": False, "message": "鉴权失败：API Key 无效或无权限"}
        return {"ok": True, "message": f"可达（HTTP {resp.status_code}）"}
    except Exception as e:
        return {"ok": False, "message": f"连接失败：{e}"}


def list_voice_cache() -> list[dict]:
    return query("SELECT * FROM voice_cache ORDER BY id DESC LIMIT 50")
