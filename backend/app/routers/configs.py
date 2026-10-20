"""模型配置中心：厂商 / 凭证 / LLM·ASR·TTS 配置 统一范式（PRD 4.2）。"""
from fastapi import APIRouter, HTTPException

from .. import llm_gateway
from ..db import execute, query, query_one
from ..providers import vendor_list, vendor_of
from ..security import delete_secret, load_secret, mask_secret, store_secret

router = APIRouter(prefix="/api", tags=["config"])

KINDS = {"llm", "asr", "tts"}
TABLES = {"llm": "llm_configs", "asr": "asr_configs", "tts": "tts_configs"}


# ---------- 厂商清单 ----------
@router.get("/vendors/{kind}")
def vendors(kind: str):
    if kind not in KINDS:
        raise HTTPException(400, "kind 必须是 llm/asr/tts")
    return vendor_list(kind)


# ---------- 凭证（F-MOD-01/02/03/06） ----------
@router.get("/credentials")
def list_credentials():
    rows = query("SELECT id, provider, alias, base_url, api_key_ref, created_at, updated_at "
                 "FROM provider_credentials ORDER BY id")
    for r in rows:
        r["api_key_masked"] = mask_secret(r.pop("api_key_ref"))
    return rows


@router.post("/credentials")
def create_credential(body: dict):
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
    for t in TABLES.values():
        affected += [{"kind": t.replace("_configs", ""), "id": r["id"]}
                     for r in query(f"SELECT id FROM {t} WHERE credential_id=?", (cid,))]
    if affected:
        # F-MOD-03：被引用时禁止直接删除
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
        r["credential_label"] = f"{c['provider']} / {c['alias']}" if c else ("本地引擎" if r.get("engine_type") == "local" else None)
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
    if engine == "cloud" and kind != "llm" and not body.get("credential_id"):
        raise HTTPException(400, "云厂商需要先配置凭证")
    if engine == "cloud" and kind == "llm" and provider not in ("openai_compat",) and not body.get("credential_id"):
        raise HTTPException(400, "云厂商需要先配置凭证")
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
def get_voices(body: dict):
    from ..providers import BUILTIN_VOICES

    provider = body.get("provider", "")
    voices = BUILTIN_VOICES.get(provider)
    if voices:
        return {"source": "builtin", "voices": voices}
    return {"source": "manual", "voices": [], "note": "该厂商音色需手动填写 voice_id"}


@router.post("/configs/{kind}/{cid}/test")
async def test_config(kind: str, cid: int):
    if kind not in KINDS:
        raise HTTPException(400, "kind 必须是 llm/asr/tts")
    row = _cfg_row(kind, cid)
    return await llm_gateway.test_connection(row)
