"""FastAPI application entry: API routes + static frontend hosting.

Run: python -m backend.app [--host 0.0.0.0] [--port 8000] [--db data/app.db]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import Core, register_routes
from .storage import Storage

VERSION = "1.0.0"


def create_app(db_path: str | Path = "data/app.db", static_dir: str | Path | None = None, core=None) -> FastAPI:
    app = FastAPI(title="NLP策略选股器", version=VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
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
