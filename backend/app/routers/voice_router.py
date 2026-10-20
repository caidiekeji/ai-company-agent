"""语音路由（PRD 4.4）：ASR 转写、TTS 合成、音色列表、员工语音配置。"""
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import voice
from ..db import DATA_DIR, query, query_one
from ..providers import default_voice_for, extra_params_capability, voices_for

router = APIRouter(prefix="/api/voice", tags=["voice"])

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/voices")
def voices(provider: str = "", model_id: str = ""):
    """音色列表：厂商内置（附录 A 核实后维护）。"""
    return {"voices": voices_for(provider, model_id),
            "gender_map": default_voice_for(provider, "女") and {
                "男": default_voice_for(provider, "男"),
                "女": default_voice_for(provider, "女"),
                "其他": default_voice_for(provider, "其他"),
            }}


@router.get("/params/{kind}")
def params(kind: str, provider: str = ""):
    return {"params": extra_params_capability(kind, provider)}


@router.post("/asr/transcribe")
async def transcribe(config_id: int, file: UploadFile = File(...)):
    """语音输入转文字（F-VOI-01）。"""
    cfg = query_one("SELECT * FROM asr_configs WHERE id=?", (config_id,))
    if not cfg:
        raise HTTPException(404, "ASR 配置不存在")
    cfg["kind"] = "asr"
    name = f"{uuid.uuid4().hex}{Path(file.filename or 'audio.wav').suffix or '.wav'}"
    path = UPLOAD_DIR / name
    with path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    result = await voice.transcribe(str(path), cfg)
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "识别失败"))
    return {"text": result.get("text", ""), "engine": result.get("engine", "")}


@router.post("/tts/synthesize")
async def synthesize(body: dict | None = None):
    """TTS 合成（可指定员工，按其音色播放）。"""
    cfg = query_one("SELECT * FROM tts_configs WHERE id=?", (body.get("config_id"),))
    if not cfg:
        raise HTTPException(404, "TTS 配置不存在")
    cfg["kind"] = "tts"
    emp = None
    if body.get("employee_id"):
        emp = query_one("SELECT * FROM employees WHERE id=?", (body["employee_id"],))
    vid = voice.resolve_voice(cfg, emp)
    result = await voice.synthesize(body.get("text", ""), cfg, vid, emp)
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "合成失败"))
    return result


@router.get("/audio/{name}")
def audio(name: str):
    p = (voice.VOICE_DIR / name).resolve()
    if not str(p).startswith(str(voice.VOICE_DIR.resolve())) or not p.exists():
        raise HTTPException(404, "音频不存在")
    return FileResponse(p)


@router.get("/cache")
def cache():
    return voice.list_voice_cache()


@router.get("/employee/{eid}")
def employee_voice(eid: int):
    """员工语音配置解析结果（继承/覆盖状态展示，F-ORG-07）。"""
    emp = query_one("SELECT * FROM employees WHERE id=?", (eid,))
    if not emp:
        raise HTTPException(404, "员工不存在")
    asr = query_one("SELECT * FROM asr_configs WHERE id=?", (emp["asr_config_id"],)) if emp["asr_config_id"] else None
    tts = query_one("SELECT * FROM tts_configs WHERE id=?", (emp["tts_config_id"],)) if emp["tts_config_id"] else None
    if not asr:
        asr = query_one("SELECT * FROM asr_configs WHERE is_default=1 LIMIT 1")
    if not tts:
        tts = query_one("SELECT * FROM tts_configs WHERE is_default=1 LIMIT 1")
    asr_src = "员工指定" if emp["asr_config_id"] else "继承全局"
    tts_src = "员工指定" if emp["tts_config_id"] else "继承全局"
    resolved_voice = None
    if tts:
        resolved_voice = emp["tts_voice_id"] or default_voice_for(tts["provider"], emp["gender"]) \
            or tts["voice_id"]
    return {
        "asr": asr, "asr_source": asr_src,
        "tts": tts, "tts_source": tts_src,
        "voice_id": resolved_voice,
        "voice_is_manual": bool(emp["tts_voice_id"]),
        "available_voices": voices_for(tts["provider"]) if tts else [],
    }
