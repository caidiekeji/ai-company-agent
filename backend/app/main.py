"""FastAPI 应用入口（AI Company Agent sidecar）。"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import init_db
from .routers import configs, kb, meeting, org


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI Company Agent", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 仅本地开发：Vite dev server / Tauri webview
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(configs.router)
app.include_router(org.router)
app.include_router(meeting.router)
app.include_router(kb.router)


@app.get("/api/health")
def health():
    return {"ok": True, "service": "ai-company-agent"}


def _find_dist() -> Path | None:
    """前端产物探测：开发态 = 仓库 frontend/dist；打包态 = exe 同级 frontend/dist。"""
    import sys

    if getattr(sys, "frozen", False):
        candidates = [Path(sys.executable).parent / "frontend" / "dist"]
    else:
        candidates = [Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"]
    return next((p for p in candidates if p.exists()), None)


_DIST = _find_dist()
if _DIST is not None:
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")


def _pick_port(start: int = 8756, attempts: int = 10) -> int:
    import socket

    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"{start}~{start + attempts - 1} 端口均被占用")


def main() -> None:
    """双击启动入口：崩溃不闪退（留窗显示错误）、自动打开浏览器、端口占用自动顺延。"""
    import sys
    import threading
    import webbrowser

    try:
        port = _pick_port()
        url = f"http://127.0.0.1:{port}/"
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
        print(f"智企 Agent 已启动：{url}\n关闭本窗口即退出应用。")
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except Exception:
        import traceback

        traceback.print_exc()
        print("\n启动失败，请把上面的错误信息截图反馈。")
        try:
            input("按回车退出...")
        except EOFError:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
