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


# 托管前端构建产物（Tauri 形态同理：前端打包进应用，后端仅提供 API）
_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _DIST.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8756, log_level="warning")


if __name__ == "__main__":
    main()
