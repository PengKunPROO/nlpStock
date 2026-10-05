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


def create_app(
    db_path: str | Path = "data/app.db",
    static_dir: str | Path | None = None,
    log_dir: str | Path | None = None,
    core=None,
) -> FastAPI:
    setup_logging(log_dir=str(log_dir) if log_dir else "data/logs")
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
    # 首次启动自动部署内置种子策略（幂等：已存在则跳过），确保有 universe=all 的选股策略可用
    try:
        from .seed import seed_strategies

        seed_strategies(storage)
    except Exception as e:  # seed 失败不阻塞启动
        log.warning("seed 策略部署失败: %s", e)
    if core is None:
        core = Core(storage, storage.get_settings().get("fuyao_api_key") or "")
    register_routes(app, core)

    static_dir = Path(static_dir) if static_dir else Path(__file__).resolve().parent.parent / "frontend"
    if static_dir.exists() and (static_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


def build_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    db_path: str | Path = "data/app.db",
    static_dir: str | Path | None = None,
    log_dir: str | Path | None = None,
) -> "uvicorn.Server":
    """构建可编程启动的服务实例（供 Android/Chaquopy 等嵌入环境在后台线程运行）。

    用法::

        server = build_server(port=8000, db_path="<app私有目录>/app.db",
                              static_dir="<assets里的frontend>", log_dir="<app私有目录>/logs")
        threading.Thread(target=server.run, daemon=True).start()
        # 轮询 server.started 为 True 后再加载前端
        # 退出时设置 server.should_exit = True
    """
    import uvicorn

    app = create_app(db_path=db_path, static_dir=static_dir, log_dir=log_dir)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    # 非主线程无法安装信号处理器（"signal only works in main thread"），嵌入环境必须禁用
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    return server


# 注：不提供模块级 `app = create_app()`——嵌入环境（Android/Chaquopy）import 本模块时
# 不应在 cwd 产生 data/ 副作用；命令行入口走 main()，嵌入环境走 build_server()。


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
