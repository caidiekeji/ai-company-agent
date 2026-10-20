"""内置厂商注册表（PRD F-MOD-05：厂商清单以 JSON 维护，新增厂商不改核心代码）。

数据文件：
- data/vendors.json   厂商清单（id / 名称 / api_format / 默认 base_url / 本地标记）
- data/catalog.json   模型清单、音色清单、性别→音色映射、厂商额外参数能力
"""
import json
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "data"


def _load(name: str) -> dict:
    path = DATA_PATH / name
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _vendors_raw() -> dict:
    return _load("vendors.json")


@lru_cache(maxsize=1)
def _catalog_raw() -> dict:
    return _load("catalog.json")


def VENDORS() -> dict[str, list[dict]]:
    return _vendors_raw()


def BUILTIN_MODELS() -> dict[str, list[str]]:
    return _catalog_raw().get("models", {})


def BUILTIN_VOICES() -> dict[str, list[dict]]:
    return _catalog_raw().get("voices", {})


def GENDER_VOICE_MAP() -> dict[str, dict[str, str]]:
    return _catalog_raw().get("gender_voice_map", {})


def ASR_PARAMS() -> dict[str, list[str]]:
    return _catalog_raw().get("asr_params", {})


def TTS_PARAMS() -> dict[str, list[str]]:
    return _catalog_raw().get("tts_params", {})


def vendor_list(kind: str) -> list[dict]:
    return VENDORS().get(kind, [])


def vendor_of(kind: str, vendor_id: str) -> dict | None:
    return next((v for v in VENDORS().get(kind, []) if v["id"] == vendor_id), None)


def vendor_price_per_1k(kind: str, vendor_id: str) -> float:
    v = vendor_of(kind, vendor_id) or {}
    try:
        return float(v.get("price_per_1k", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def voices_for(provider: str, model_id: str = "") -> list[dict]:
    """音色列表：优先厂商内置清单；部分厂商音色与模型解耦（如 Azure）。"""
    return BUILTIN_VOICES().get(provider, [])


def default_voice_for(provider: str, gender: str) -> str | None:
    """性别 → 默认音色初始映射（F-VOI-04：仅在用户未手动指定时生效）。"""
    m = GENDER_VOICE_MAP().get(provider, {})
    return m.get(gender) or m.get("其他")


def extra_params_capability(kind: str, provider: str) -> list[str]:
    return (ASR_PARAMS() if kind == "asr" else TTS_PARAMS()).get(provider, [])


def reload_catalog() -> None:
    """外部修改 JSON 后热加载（设置页「重载厂商清单」）。"""
    _vendors_raw.cache_clear()
    _catalog_raw.cache_clear()
