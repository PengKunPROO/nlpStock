"""FastAPI application entry: API routes + static frontend hosting.

Run: python -m backend.app [--host 0.0.0.0] [--port 8000] [--db data/app.db]
"""
from __future__ import annotations

import argparse
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import Core, register_routes
from .logging_setup import get_logger, set_request_id, setup_logging
from .storage import Storage

VERSION = "1.0.0"
log = get_logger("app")


def create_app(db_path: str | Path = "data/app.db", static_dir: str | Path | None = None, core=None) -> FastAPI:
    setup_logging()
    app = FastAPI(title="NLP策略选股器", version=VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next):
        set_request_id(uuid.uuid4().hex[:8])
        return await call_next(request)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        log.error(
            "未捕获异常 %s %s",
            request.method,
            request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(status_code=500, content={"error": {"code": "internal", "message": "服务器内部错误，详见日志"}})

    storage = Storage(db_path)
    s = storage.get_settings()
    defaults = {
        "fuyao_api_key": os.environ.get("FUYAO_API_KEY", ""),
        "llm_base_url": "https://api.deepseek.com",
        "llm_api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "llm_model": "deepseek-chat",
        "default_universe": {"type": "index", "code": "000300.SH"},
    }
    missing = {k: v for k, v in defaults.items() if not s.get(k) and v}
    if missing:
        storage.update_settings(missing)
    if core is None:
        core = Core(storage, storage.get_settings().get("fuyao_api_key") or "")
    register_routes(app, core)

    static_dir = Path(static_dir) if static_dir else Path(__file__).resolve().parent.parent / "frontend"
    if static_dir.exists() and (static_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="NLP策略选股器服务")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="data/app.db")
    args = parser.parse_args()
    global app
    app = create_app(db_path=args.db)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
