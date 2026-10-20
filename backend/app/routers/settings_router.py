"""系统设置与安全路由（PRD 4.7）。"""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import models_mgr, settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_all():
    return {
        "settings": settings.get_settings(),
        "whitelist": settings.list_whitelist(),
        "privacy": settings.PRIVACY_TEXT,
        "usage": settings.cache_usage(),
    }


@router.put("")
def update(body: dict | None = None):
    return {"ok": True, "settings": settings.update_settings(body.get("settings") or body)}


@router.get("/privacy")
def privacy():
    return settings.PRIVACY_TEXT


# ---------------- 域名白名单 ----------------
@router.get("/whitelist")
def wl_list():
    return settings.list_whitelist()


@router.post("/whitelist")
def wl_add(body: dict | None = None):
    try:
        return settings.add_whitelist(body.get("domain", ""), body.get("note", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/whitelist/{wid}")
def wl_del(wid: int):
    settings.remove_whitelist(wid)
    return {"ok": True}


# ---------------- 导出 / 备份 / 缓存 ----------------
@router.post("/export")
def export():
    path = settings.export_backup()
    return {"ok": True, "path": path, "filename": Path(path).name}


@router.get("/usage")
def usage():
    return settings.cache_usage()


@router.post("/clear-cache")
def clear(body: dict | None = None):
    target = (body or {}).get("target", "logs")
    if target not in ("logs", "models", "vectors"):
        raise HTTPException(400, "target 必须是 logs/models/vectors")
    return settings.clear_cache(target)


# ---------------- 模型包管理 ----------------
@router.get("/packages")
def packages():
    return models_mgr.list_packages()


@router.post("/packages/{pid}/download")
def download(pid: str):
    try:
        return models_mgr.start_download(pid)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/packages/{pid}")
def delete_pkg(pid: str):
    return models_mgr.delete_package(pid)


@router.post("/packages/register")
def register(body: dict | None = None):
    try:
        return models_mgr.register_local_package(
            body.get("id", ""), body.get("category", "asr"), body.get("display_name", body.get("id", "")),
            body.get("path", ""), body.get("license", ""), body.get("note", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------- 更新 ----------------
@router.post("/update/check")
async def check_update(body: dict | None = None):
    import asyncio

    return await asyncio.to_thread(settings.check_update, (body or {}).get("manifest_url"))


@router.get("/app-info")
def app_info():
    from ..main import APP_VERSION

    return {"version": APP_VERSION, "data_dir": str(settings.DATA_DIR),
            "python": os.sys.version.split()[0]}
