"""系统设置中心（PRD 4.7）：域名白名单、隐私说明、导出备份、缓存管理、更新通道。

设置以 key-value 存 SQLite（app_settings），域名单独建表便于引用检查。
"""
import json
import shutil
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from .db import DATA_DIR, DB_PATH, LOG_DIR, MODELS_DIR, query, query_one, execute

SETTINGS_KEYS = {
    "rpa_allow_non_whitelist": "false",   # F-SYS-06 非白名单域名是否允许（需确认）
    "rpa_high_risk_confirm": "true",       # F-RPA-07 高风险动作二次确认
    "rpa_headless": "true",
    "embedding_model": "bge-small-zh-v1.5",
    "voice_auto_play": "true",
    "auto_update_enabled": "true",
    "update_channel": "stable",
    "update_manifest_url": "",
    "telemetry": "false",
    "llm_price_per_1k": "0.002",          # 成本预估用（元 / 1K token）
    "context_compress_default": "true",
    "visible_rounds_default": "6",
}

PRIVACY_TEXT = {
    "本地存储": [
        "组织架构、员工画像、会议记录、知识条目、模型配置元数据全部存放在本机 SQLite 数据库中。",
        "API Key 存于系统密钥链（keyring）；不可用时回退到本机加密文件，主密钥不落 SQLite。",
        "RPA 浏览器 profile（Cookie/localStorage）隔离存放于应用数据目录，不随知识库导出。",
    ],
    "会发送到云厂商的数据": [
        "LLM：会议议题、员工 system prompt、可见的上下文与发言内容会发送到所选 LLM 厂商。",
        "ASR：录音音频会发送到所选语音识别厂商；使用本地引擎时不外发。",
        "TTS：待朗读的发言文本会发送到所选语音合成厂商；使用本地引擎时不外发。",
        "RPA：仅在你主动发起的 RPA 任务中访问目标站点，网页内容会作为外部内容注入上下文。",
    ],
    "安全边界": [
        "RPA 只能访问域名白名单内的站点；提交/支付类动作需二次确认。",
        "网页与知识库检索到的外部内容均带边界标记，其中的指令不会覆盖系统指令。",
        "导出备份不包含任何明文 API Key。",
    ],
}

DEFAULT_SETTINGS = dict(SETTINGS_KEYS)


def get_settings() -> dict:
    rows = query("SELECT key, value FROM app_settings")
    vals = dict(DEFAULT_SETTINGS)
    for r in rows:
        vals[r["key"]] = r["value"]
    return vals


def get_setting(key: str, default: str | None = None) -> str:
    row = query_one("SELECT value FROM app_settings WHERE key=?", (key,))
    if row and row["value"] is not None:
        return row["value"]
    if default is not None:
        return default
    return DEFAULT_SETTINGS.get(key, "")


def get_bool(key: str, default: bool = False) -> bool:
    return str(get_setting(key)).strip().lower() in ("1", "true", "yes", "on")


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(float(get_setting(key)))
    except (TypeError, ValueError):
        return default


def set_setting(key: str, value) -> None:
    val = "true" if value is True else "false" if value is False else str(value)
    execute("INSERT INTO app_settings(key, value, updated_at) VALUES (?,?,datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
            (key, val))


def update_settings(patch: dict) -> dict:
    for k, v in (patch or {}).items():
        if k in DEFAULT_SETTINGS or k.startswith("custom."):
            set_setting(k, v)
    return get_settings()


# ---------------- 域名白名单（F-SYS-06 / F-RPA-06） ----------------
def list_whitelist() -> list[dict]:
    return query("SELECT * FROM domain_whitelist ORDER BY id")


def add_whitelist(domain: str, note: str = "") -> dict:
    d = (domain or "").strip().lower()
    d = d.replace("https://", "").replace("http://", "").rstrip("/")
    if "/" in d:
        d = d.split("/", 1)[0]
    if not d:
        raise ValueError("域名不能为空")
    if query_one("SELECT id FROM domain_whitelist WHERE domain=?", (d,)):
        return query_one("SELECT * FROM domain_whitelist WHERE domain=?", (d,))
    execute("INSERT INTO domain_whitelist(domain, note) VALUES (?,?)", (d, note or ""))
    return query_one("SELECT * FROM domain_whitelist WHERE domain=?", (d,))


def remove_whitelist(domain_id: int) -> None:
    execute("DELETE FROM domain_whitelist WHERE id=?", (domain_id,))


def is_domain_allowed(url: str) -> tuple[bool, str]:
    """返回 (是否放行, 原因)。白名单为空时按「未配置」处理——需用户确认。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False, "URL 解析失败"
    if not host:
        return False, "URL 缺少主机名"
    allowed = list_whitelist()
    if not allowed:
        return False, "域名白名单为空"
    for row in allowed:
        d = row["domain"].lower()
        if host == d or host.endswith("." + d):
            return True, f"命中白名单 {d}"
    return False, f"{host} 不在白名单内"


# ---------------- 导出 / 备份（F-SYS-03） ----------------
def export_backup(dest_dir: str | None = None) -> str:
    """导出 SQLite + 配置（不含明文 Key）。返回生成的文件路径。"""
    out_dir = Path(dest_dir) if dest_dir else (DATA_DIR.parent / "exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    zip_path = out_dir / f"ai-company-agent-backup-{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        if DB_PATH.exists():
            z.write(DB_PATH, "app.db")
        for extra in ("README.txt",):
            p = DATA_DIR / extra
            if p.exists():
                z.write(p, extra)
        # 导出一份脱敏后的设置快照
        z.writestr("settings.json", json.dumps(get_settings(), ensure_ascii=False, indent=2))
        z.writestr("privacy.txt", json.dumps(PRIVACY_TEXT, ensure_ascii=False, indent=2))
    return str(zip_path)


def cache_usage() -> dict:
    """F-SYS-04：模型/缓存/日志占用统计。"""

    def dir_size(p: Path) -> int:
        if not p.exists():
            return 0
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    return {
        "database_bytes": db_size,
        "models_bytes": dir_size(MODELS_DIR),
        "logs_bytes": dir_size(LOG_DIR),
        "data_dir": str(DATA_DIR),
        "total_bytes": db_size + dir_size(MODELS_DIR) + dir_size(LOG_DIR),
    }


def clear_cache(target: str = "logs") -> dict:
    """target: logs | models | vectors"""
    freed = 0
    if target == "logs":
        for f in LOG_DIR.glob("*"):
            if f.is_file():
                freed += f.stat().st_size
                f.unlink()
    elif target == "models":
        for f in MODELS_DIR.glob("*"):
            if f.is_file():
                freed += f.stat().st_size
                f.unlink()
    elif target == "vectors":
        from . import embedding

        freed = embedding.drop_all_vectors()
    return {"ok": True, "freed_bytes": freed, "target": target}


def check_update(manifest_url: str | None = None) -> dict:
    """F-SYS-07：检查更新（拉取签名 manifest JSON）。"""
    url = manifest_url or get_setting("update_manifest_url")
    if not url:
        return {"ok": False, "message": "未配置更新源（设置 → 自动更新）", "update_available": False}
    import httpx

    try:
        resp = httpx.get(url, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"ok": False, "message": f"检查更新失败：{e}", "update_available": False}
    latest = str(data.get("version", "")).lstrip("v")
    current = data.get("current_version") or _app_version()
    available = bool(latest) and _ver_tuple(latest) > _ver_tuple(current)
    return {
        "ok": True,
        "update_available": available,
        "current_version": current,
        "latest_version": latest,
        "notes": data.get("notes", ""),
        "download_url": data.get("download_url", ""),
        "signature": data.get("signature", ""),
    }


def _app_version() -> str:
    from .main import APP_VERSION

    return APP_VERSION


def _ver_tuple(v: str) -> tuple:
    parts = []
    for p in str(v).split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def copy_dir(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True)
