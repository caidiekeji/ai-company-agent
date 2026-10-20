"""Playwright 运行时定位与内核安装（兼容 PyInstaller frozen 模式）。

问题背景：
- Playwright Python 包的浏览器驱动实际是 `playwright/driver/node(.exe)` + `driver/package/cli.js`，
  靠 `inspect.getfile(playwright)` 定位。
- 源码模式下 `python -m playwright install chromium` 可用；
  但 PyInstaller 打包后 `sys.executable` 是业务 exe 而非 python，
  `sys.executable -m playwright` 会直接失败，导致 RPA 浏览器内核永远装不上。

本模块提供：
- `driver_paths()`：定位 node 可执行文件与 cli.js（frozen / 源码两态兼容）
- `install_browser_cmd()`：构造正确的内核安装命令
- `browsers_root()`：统一浏览器内核落盘目录（放在应用数据目录，便于删除与统计）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _base_dir() -> Path:
    """应用数据根目录（frozen 与源码一致，见 db.DATA_DIR）。"""
    from .db import DATA_DIR

    return Path(DATA_DIR)


def browsers_root() -> Path:
    """浏览器内核落盘根目录：<数据目录>/browsers。

    放在应用数据目录而非用户全局缓存，便于「模型包」页统一管理体积与删除。
    """
    p = _base_dir() / "browsers"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _within(driver_dir: Path) -> tuple[Path | None, Path | None]:
    cli = driver_dir / "package" / "cli.js"
    if not cli.exists():
        return None, None
    node = driver_dir / ("node.exe" if os.name == "nt" else "node")
    return (node if node.exists() else None), cli


def _installed_driver_dir() -> Path | None:
    try:
        import inspect

        import playwright

        return Path(inspect.getfile(playwright)).parent / "driver"
    except Exception:
        return None


def driver_paths() -> tuple[Path | None, Path | None]:
    """返回 (node 可执行文件, cli.js)；找不到时返回 (None, None)。"""
    # 1) 已安装包的真实位置（frozen 时 PyInstaller 会解压到 _MEIPASS）
    d = _installed_driver_dir()
    if d:
        node, cli = _within(d)
        if node and cli:
            return node, cli

    # 2) frozen 兜底：sys._MEIPASS / playwright/driver
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        node, cli = _within(Path(meipass) / "playwright" / "driver")
        if node and cli:
            return node, cli

    # 3) frozen 再兜底：exe 同级 _internal/playwright/driver
    if _is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        for cand in (exe_dir / "_internal" / "playwright" / "driver",
                     exe_dir / "playwright" / "driver"):
            node, cli = _within(cand)
            if node and cli:
                return node, cli
    return None, None


def install_browser_cmd(browser: str = "chromium") -> list[str] | None:
    """构造内核安装命令；无法定位驱动时返回 None（调用方应提示手动安装）。"""
    node, cli = driver_paths()
    if node and cli:
        return [str(node), str(cli), "install", browser]

    # 源码模式且解释器可用时，用模块入口
    if not _is_frozen() and shutil.which(sys.executable):
        return [sys.executable, "-m", "playwright", "install", browser]
    return None


def browser_env() -> dict[str, str]:
    """内核安装/运行所需环境变量：统一浏览器落盘目录。"""
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_root())
    node, _ = driver_paths()
    if node:
        env["PLAYWRIGHT_NODEJS_PATH"] = str(node)
    return env


def run_install(browser: str = "chromium", timeout: int = 3600) -> tuple[bool, str]:
    """执行内核安装，返回 (是否成功, 错误信息)。"""
    cmd = install_browser_cmd(browser)
    if not cmd:
        return False, "无法定位 Playwright 驱动，请手动安装 Playwright 后重试"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, env=browser_env())
        if proc.returncode == 0:
            return True, ""
        return False, (proc.stderr or proc.stdout or "")[-600:]
    except subprocess.TimeoutExpired:
        return False, "安装超时"
    except Exception as e:  # noqa: BLE001
        return False, str(e)
