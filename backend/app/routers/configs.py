"""模型配置中心：厂商 / 凭证 / LLM·ASR·TTS 配置 统一范式（PRD 4.2）。

厂商清单来自 data/vendors.json + data/catalog.json（F-MOD-05 可扩展）。
"""
import json

from fastapi import APIRouter, HTTPException

from .. import llm_gateway, voice
from ..db import execute, query, query_one
from ..providers import (BUILTIN_MODELS, extra_params_capability, reload_catalog,
                          vendor_list, vendor_of, voices_for)
from ..security import delete_secret, load_secret, mask_secret, store_secret

router = APIRouter(prefix="/api", tags=["config"])

KINDS = {"llm", "asr", "tts"}
TABLES = {"llm": "llm_configs", "asr": "asr_configs", "tts": "tts_configs"}


# ---------- 厂商清单 ----------
@router.get("/vendors/{kind}")
def vendors(kind: str):
    if kind not in KINDS:
        raise HTTPException(400, "kind 必须是 llm/asr/tts")
    out = []
    for v in vendor_list(kind):
        item = dict(v)
        item["params"] = extra_params_capability(kind, v["id"])
        item["models"] = BUILTIN_MODELS().get(v["id"], [])
        if kind == "tts":
            item["voices"] = voices_for(v["id"])
        out.append(item)
    return out


@router.post("/vendors/reload")
def reload_vendors():
    reload_catalog()
    return {"ok": True, "message": "厂商清单已重载"}


# ---------- 凭证（F-MOD-01/02/03/06） ----------
@router.get("/credentials")
def list_credentials():
    rows = query("SELECT id, provider, alias, base_url, api_key_ref, created_at, updated_at "
                 "FROM provider_credentials ORDER BY id")
    for r in rows:
        r["api_key_masked"] = mask_secret(r.pop("api_key_ref"))
        r["refs"] = credential_refs(r["id"])
    return rows


def credential_refs(cid: int) -> list[dict]:
    out = []
    cols = {"llm_configs": "id, model_id", "asr_configs": "id, model_id", "tts_configs": "id, model_id, voice_id"}
    for kind, table in TABLES.items():
        for r in query(f"SELECT {cols[table]} FROM {table} WHERE credential_id=?", (cid,)):
            out.append({"kind": kind, "id": r["id"], "label": r.get("model_id") or r.get("voice_id") or ""})
    return out


@router.post("/credentials")
def create_credential(body: dict | None = None):
    provider = body.get("provider", "").strip()
    alias = (body.get("alias") or "默认").strip()
    base_url = body.get("base_url") or None
    api_key = (body.get("api_key") or "").strip()
    if not provider:
        raise HTTPException(400, "provider 必填")
    if not api_key:
        raise HTTPException(400, "api_key 必填")
    if query_one("SELECT id FROM provider_credentials WHERE provider=? AND alias=?", (provider, alias)):
        raise HTTPException(409, f"厂商「{provider}」下已存在别名「{alias}」")
    ref = store_secret(provider, alias, api_key)
    cid = execute(
        "INSERT INTO provider_credentials(provider, alias, base_url, api_key_ref) VALUES (?,?,?,?)",
        (provider, alias, base_url, ref))
    return query_one("SELECT id, provider, alias, base_url FROM provider_credentials WHERE id=?", (cid,))


@router.put("/credentials/{cid}")
def update_credential(cid: int, body: dict):
    row = query_one("SELECT * FROM provider_credentials WHERE id=?", (cid,))
    if not row:
        raise HTTPException(404, "凭证不存在")
    base_url = body.get("base_url", row["base_url"])
    alias = (body.get("alias") or row["alias"]).strip()
    api_key = (body.get("api_key") or "").strip()
    ref = row["api_key_ref"]
    if api_key:
        delete_secret(ref)
        ref = store_secret(row["provider"], alias, api_key)
    execute("UPDATE provider_credentials SET alias=?, base_url=?, api_key_ref=?, "
            "updated_at=datetime('now') WHERE id=?", (alias, base_url, ref, cid))
    return {"ok": True}


@router.delete("/credentials/{cid}")
def delete_credential(cid: int):
    row = query_one("SELECT * FROM provider_credentials WHERE id=?", (cid,))
    if not row:
        raise HTTPException(404, "凭证不存在")
    affected = []
    for kind, table in TABLES.items():
        affected += [{"kind": kind, "id": r["id"], "model_id": r.get("model_id")}
                     for r in query(f"SELECT id, model_id FROM {table} WHERE credential_id=?", (cid,))]
    if affected:
        raise HTTPException(409, detail={"message": "凭证被以下配置引用，禁止删除", "affected": affected})
    delete_secret(row["api_key_ref"])
    execute("DELETE FROM provider_credentials WHERE id=?", (cid,))
    return {"ok": True}


@router.get("/credentials/{cid}/reveal")
def reveal_credential(cid: int):
    row = query_one("SELECT api_key_ref FROM provider_credentials WHERE id=?", (cid,))
    if not row:
        raise HTTPException(404, "凭证不存在")
    val = load_secret(row["api_key_ref"])
    if not val:
        raise HTTPException(404, "密钥不可读（可能来自其他机器）")
    return {"api_key": val}


# ---------- 统一配置 CRUD（llm/asr/tts 同构） ----------
def _cfg_row(kind: str, cid: int) -> dict:
    row = query_one(f"SELECT * FROM {TABLES[kind]} WHERE id=?", (cid,))
    if not row:
        raise HTTPException(404, "配置不存在")
    row["kind"] = kind
    return row


@router.get("/configs/{kind}")
def list_configs(kind: str):
    if kind not in KINDS:
        raise HTTPException(400, "kind 必须是 llm/asr/tts")
    rows = query(f"SELECT * FROM {TABLES[kind]} ORDER BY is_default DESC, id")
    creds = {c["id"]: c for c in query("SELECT id, provider, alias FROM provider_credentials")}
    for r in rows:
        c = creds.get(r.get("credential_id"))
        r["credential_label"] = f"{c['provider']} / {c['alias']}" if c else (
            "本地引擎" if r.get("engine_type") == "local" else None)
        r["voices"] = voices_for(r.get("provider", "")) if kind == "tts" else []
    return rows


@router.post("/configs/{kind}")
def create_config(kind: str, body: dict):
    if kind not in KINDS:
        raise HTTPException(400, "kind 必须是 llm/asr/tts")
    provider = body.get("provider", "").strip()
    if not provider:
        raise HTTPException(400, "provider 必填")
    vendor = vendor_of(kind, provider) or {}
    engine = "local" if vendor.get("local") else "cloud"
    if engine == "cloud" and not body.get("credential_id"):
        raise HTTPException(400, "云厂商需要先配置凭证（同一厂商的 LLM/ASR/TTS 共用一条凭证）")
    model_id = (body.get("model_id") or "").strip()
    if kind == "llm" and not model_id:
        raise HTTPException(400, "model_id 必填")
    if body.get("set_default"):
        execute(f"UPDATE {TABLES[kind]} SET is_default=0")
    if kind == "llm":
        cid = execute(
            "INSERT INTO llm_configs(provider, credential_id, model_id, temperature, max_tokens, "
            "context_window, extra_params_json, is_default) VALUES (?,?,?,?,?,?,?,?)",
            (provider, body.get("credential_id"), model_id, body.get("temperature", 0.7),
             body.get("max_tokens", 2048), body.get("context_window", 32768),
             body.get("extra_params_json") or "{}", 1 if body.get("set_default") else 0))
    elif kind == "asr":
        cid = execute(
            "INSERT INTO asr_configs(provider, credential_id, engine_type, model_id, model_path, "
            "extra_params_json, is_default) VALUES (?,?,?,?,?,?,?)",
            (provider, body.get("credential_id"), engine, model_id, body.get("model_path"),
             body.get("extra_params_json") or "{}", 1 if body.get("set_default") else 0))
    else:
        cid = execute(
            "INSERT INTO tts_configs(provider, credential_id, engine_type, model_id, voice_id, model_path, "
            "extra_params_json, is_default) VALUES (?,?,?,?,?,?,?,?)",
            (provider, body.get("credential_id"), engine, model_id, body.get("voice_id"),
             body.get("model_path"), body.get("extra_params_json") or "{}", 1 if body.get("set_default") else 0))
    return _cfg_row(kind, cid)


@router.put("/configs/{kind}/{cid}")
def update_config(kind: str, cid: int, body: dict):
    if kind not in KINDS:
        raise HTTPException(400, "kind 必须是 llm/asr/tts")
    row = _cfg_row(kind, cid)
    if body.get("set_default"):
        execute(f"UPDATE {TABLES[kind]} SET is_default=0")
    fields = {}
    for k in ("credential_id", "model_id", "voice_id", "model_path", "temperature", "max_tokens",
              "context_window", "extra_params_json"):
        if k in body:
            fields[k] = body[k]
    if body.get("set_default"):
        fields["is_default"] = 1
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        execute(f"UPDATE {TABLES[kind]} SET {sets} WHERE id=?", (*fields.values(), cid))
    return _cfg_row(kind, cid)


@router.delete("/configs/{kind}/{cid}")
def delete_config(kind: str, cid: int):
    if kind not in KINDS:
        raise HTTPException(400, "kind 必须是 llm/asr/tts")
    _cfg_row(kind, cid)
    execute(f"DELETE FROM {TABLES[kind]} WHERE id=?", (cid,))
    return {"ok": True}


# ---------- 获取模型 / 音色 / 连接测试 ----------
@router.post("/providers/{kind}/models")
async def get_models(kind: str, body: dict):
    if kind not in KINDS:
        raise HTTPException(400, "kind 必须是 llm/asr/tts")
    provider = body.get("provider", "")
    vendor = vendor_of(kind, provider) or {}
    base_url = body.get("base_url") or vendor.get("base_url") or ""
    api_key = None
    if body.get("credential_id"):
        row = query_one("SELECT api_key_ref FROM provider_credentials WHERE id=?", (body["credential_id"],))
        if row:
            api_key = load_secret(row["api_key_ref"])
    return await llm_gateway.fetch_models(provider, base_url, api_key, kind)


@router.post("/providers/tts/voices")
def get_voices(body: dict | None = None):
    provider = body.get("provider", "")
    voices = voices_for(provider, body.get("model_id", ""))
    if voices:
        return {"source": "builtin", "voices": voices}
    return {"source": "manual", "voices": [], "note": "该厂商音色需手动填写 voice_id"}


@router.post("/configs/{kind}/{cid}/test")
async def test_config(kind: str, cid: int):
    if kind not in KINDS:
        raise HTTPException(400, "kind 必须是 llm/asr/tts")
    row = _cfg_row(kind, cid)
    if kind == "tts":
        return await voice.test_tts(row)
    if kind == "asr":
        return await voice.test_asr(row)
    return await llm_gateway.test_connection(row)


@router.post("/providers/{kind}/test-live")
async def test_live(kind: str, body: dict):
    """未保存前的连接测试（配置表单内直接测试）。"""
    if kind not in KINDS:
        raise HTTPException(400, "kind 必须是 llm/asr/tts")
    cfg = dict(body.get("config") or {})
    cfg["kind"] = kind
    if not cfg.get("credential_id") and body.get("api_key"):
        provider = cfg.get("provider", "")
        alias = "临时测试"
        ref = store_secret(provider, alias, body["api_key"])
        cid = execute("INSERT INTO provider_credentials(provider, alias, base_url, api_key_ref) "
                      "VALUES (?,?,?,?)", (provider, alias, cfg.get("base_url"), ref))
        cfg["credential_id"] = cid
    if kind == "tts":
        return await voice.test_tts(cfg)
    if kind == "asr":
        return await voice.test_asr(cfg)
    return await llm_gateway.test_connection(cfg)
