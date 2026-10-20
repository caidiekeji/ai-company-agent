"""本地模型包管理（PRD F-MOD-04 / F-ASR-08 / F-TTS-08 / F-RPA-12）。

统一管理 ASR / TTS / 嵌入模型 / RPA 浏览器内核的下载状态、体积、删除入口。
- 真实下载走 HTTP（支持断点续传）。
- RPA 浏览器内核由 Playwright 自行管理，本模块只暴露状态与引导安装。
- 模型包登记在 model_packages 表，磁盘落地在 data/models/<slug>/。
"""
import os
import shutil
import threading
import time
from pathlib import Path

from .db import MODELS_DIR, execute, query, query_one

# 内置可下载清单（source_url 为公开可直下的地址；未核实项留空走「手动放置」）
CATALOG: list[dict] = [
    {"id": "bge-small-zh-v1.5", "category": "embedding", "display_name": "BGE Small 中文嵌入模型",
     "version": "1.5", "size_bytes": 99_614_720, "source_url": "",
     "license": "MIT", "note": "默认嵌入模型，512 维；可从 HuggingFace BAAI/bge-small-zh-v1.5 获取 ONNX + tokenizer.json"},
    {"id": "bge-m3", "category": "embedding", "display_name": "BGE M3 多语言嵌入模型",
     "version": "3", "size_bytes": 2_305_955_712, "source_url": "",
     "license": "MIT", "note": "1024 维，多语言长文本；体积大，按需下载"},
    {"id": "sherpa-zh-en-faster-whisper-small", "category": "asr", "display_name": "sherpa-onnx 中文小模型",
     "version": "1", "size_bytes": 75_497_472, "source_url": "",
     "license": "Apache-2.0", "note": "本地 ASR 模型包，需配套 sherpa-onnx 运行时"},
    {"id": "whisper-small", "category": "asr", "display_name": "whisper.cpp small",
     "version": "1", "size_bytes": 244_608_512, "source_url": "",
     "license": "MIT", "note": "本地 ASR 模型包，需 whisper.cpp 可执行文件"},
    {"id": "moss-tts-nano", "category": "tts", "display_name": "MOSS-TTS Nano（本地）",
     "version": "nano", "size_bytes": 419_430_400, "source_url": "",
     "license": "待核实", "note": "附录 A 核实清单项；发布状态与商用授权需确认"},
    {"id": "cosyvoice-mini", "category": "tts", "display_name": "CosyVoice Mini（本地）",
     "version": "mini", "size_bytes": 1_073_741_824, "source_url": "",
     "license": "待核实", "note": "附录 A 核实清单项；需自行放置推理包"},
]

# 浏览器内核（Playwright 管理，状态通过 import 查询）
BROWSER_PACKAGES = [
    {"id": "playwright-chromium", "category": "browser", "display_name": "Chromium 内核（RPA 用）",
     "version": "bundled", "size_bytes": 165_000_000, "source_url": "",
     "license": "Apache-2.0", "note": "由 Playwright 下载管理，首次启用 RPA 时引导安装"},
    {"id": "playwright-firefox", "category": "browser", "display_name": "Firefox 内核（备用）",
     "version": "bundled", "size_bytes": 92_000_000, "source_url": "",
     "license": "Mozilla", "note": "由 Playwright 下载管理"},
]

_downloads: dict[str, dict] = {}
_lock = threading.RLock()


def _slug(pid: str) -> str:
    return pid.replace(".", "_").replace("/", "_").replace("-", "_")


def install_path(pid: str) -> Path:
    return MODELS_DIR / _slug(pid)


def _sync_catalog() -> None:
    for item in CATALOG + BROWSER_PACKAGES:
        row = query_one("SELECT id FROM model_packages WHERE id=?", (item["id"],))
        p = install_path(item["id"])
        installed = 1 if p.exists() and any(p.iterdir()) else 0
        size = 0
        if installed:
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        if row:
            execute("UPDATE model_packages SET display_name=?, category=?, version=?, size_bytes=?, "
                    "installed=?, install_path=?, source_url=?, license=?, note=?, updated_at=datetime('now') "
                    "WHERE id=?",
                    (item["display_name"], item["category"], item["version"], size or item["size_bytes"],
                     installed, str(p), item.get("source_url", ""), item.get("license", ""),
                     item.get("note", ""), item["id"]))
        else:
            execute("INSERT INTO model_packages(id, category, display_name, version, size_bytes, installed, "
                    "install_path, source_url, license, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (item["id"], item["category"], item["display_name"], item["version"],
                     size or item["size_bytes"], installed, str(p), item.get("source_url", ""),
                     item.get("license", ""), item.get("note", "")))


def list_packages() -> list[dict]:
    _sync_catalog()
    rows = query("SELECT * FROM model_packages ORDER BY category, id")
    for r in rows:
        with _lock:
            st = _downloads.get(r["id"])
        r["download_state"] = st["state"] if st else ("installed" if r["installed"] else "not_installed")
        r["progress"] = st["progress"] if st else (1.0 if r["installed"] else 0.0)
        r["error"] = st.get("error") if st else None
        r["size_bytes"] = r["size_bytes"] or 0
    return rows


def package_status(pid: str) -> dict:
    for r in list_packages():
        if r["id"] == pid:
            return r
    return {"id": pid, "download_state": "unknown"}


# ---------------- 下载 ----------------
def start_download(pid: str) -> dict:
    """启动下载（后台线程）。支持断点续传；未配置直链的包返回需手动放置。"""
    item = next((x for x in CATALOG + BROWSER_PACKAGES if x["id"] == pid), None)
    if not item:
        raise ValueError(f"未知模型包：{pid}")
    if item["category"] == "browser":
        return install_browser(pid)
    url = item.get("source_url") or ""
    if not url:
        return {"ok": False, "message": f"「{item['display_name']}」未配置直链地址，"
                                        f"请按说明下载后放入 {install_path(pid)} 目录"}
    with _lock:
        if pid in _downloads and _downloads[pid]["state"] == "downloading":
            return {"ok": True, "message": "已在下载中", ** _downloads[pid]}

    def worker() -> None:
        import httpx

        dest = install_path(pid)
        dest.mkdir(parents=True, exist_ok=True)
        tmp = dest / (item["id"] + ".part")
        state = {"state": "downloading", "progress": 0.0, "error": None}
        with _lock:
            _downloads[pid] = state
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0) or 0)
                done = tmp.stat().st_size if tmp.exists() else 0
                mode = "ab" if done else "wb"
                with tmp.open(mode) as f:
                    for chunk in resp.iter_bytes(1024 * 256):
                        f.write(chunk)
                        done += len(chunk)
                        state["progress"] = round(done / total, 3) if total else 0.0
            state["state"] = "done"
            state["progress"] = 1.0
        except Exception as e:
            state["state"] = "failed"
            state["error"] = str(e)
        finally:
            _sync_catalog()

    threading.Thread(target=worker, daemon=True, name=f"dl-{pid}").start()
    return {"ok": True, "message": "下载已开始", "id": pid}


def install_browser(pid: str = "playwright-chromium") -> dict:
    """引导安装 Playwright 浏览器内核（首次启用 RPA 时调用）。"""
    browser = "chromium" if "chromium" in pid else "firefox"
    try:
        import subprocess
        import sys

        def worker() -> None:
            state = {"state": "downloading", "progress": 0.0, "error": None}
            with _lock:
                _downloads[pid] = state
            try:
                proc = subprocess.run([sys.executable, "-m", "playwright", "install", browser],
                                      capture_output=True, text=True, timeout=3600)
                state["state"] = "done" if proc.returncode == 0 else "failed"
                if proc.returncode != 0:
                    state["error"] = (proc.stderr or proc.stdout)[-400:]
                state["progress"] = 1.0
            except Exception as e:
                state["state"] = "failed"
                state["error"] = str(e)

        threading.Thread(target=worker, daemon=True, name=f"dl-{pid}").start()
        return {"ok": True, "message": f"开始安装 {browser} 内核（首次约 150–400 MB）"}
    except Exception as e:
        return {"ok": False, "message": f"无法启动安装：{e}"}


def browser_installed(pid: str = "playwright-chromium") -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            b = p.chromium if "chromium" in pid else p.firefox
            path = b.executable_path
            return bool(path and Path(path).exists())
    except Exception:
        return False


def delete_package(pid: str) -> dict:
    p = install_path(pid)
    freed = 0
    if p.exists():
        freed = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        shutil.rmtree(p, ignore_errors=True)
    execute("UPDATE model_packages SET installed=0, size_bytes=0, updated_at=datetime('now') WHERE id=?", (pid,))
    return {"ok": True, "freed_bytes": freed}


def register_local_package(pid: str, category: str, display_name: str,
                           path: str, license: str = "", note: str = "") -> dict:
    """手动放置的模型包登记（无直链时的兜底路径）。"""
    p = Path(path)
    if not p.exists():
        raise ValueError("路径不存在")
    size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
    execute("INSERT INTO model_packages(id, category, display_name, installed, install_path, size_bytes, "
            "license, note, updated_at) VALUES (?,?,?,1,?,?,?,?,datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET installed=1, install_path=excluded.install_path, "
            "size_bytes=excluded.size_bytes, updated_at=datetime('now')",
            (pid, category, display_name, str(p), size, license, note))
    return {"ok": True, "id": pid, "path": str(p), "size": size}
