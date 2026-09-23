"""FastAPI routes: settings, strategy alignment/CRUD, screening/backtest jobs, kline, search."""
from __future__ import annotations

from typing import Any, Optional, Union

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .backtest import backtest
from .data_service import DataService
from .fuyao import FuyaoClient, FuyaoError
from .jobs import JobRunner
from .kline import PERIODS, build_kline_response
from .llm_align import LLMNotConfigured, LLMParseError, DeepSeekAligner
from .screener import screen
from .schema import StrategyConfig
from .storage import Storage

LLM_NOT_CONFIGURED_CODE = "llm_not_configured"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, raw: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.raw = status, code, message, raw


class Core:
    def __init__(self, storage: Storage, fuyao_api_key: str):
        self.storage = storage
        self.jobs = JobRunner(storage)
        self.set_fuyao_key(fuyao_api_key)

    def set_fuyao_key(self, key: str) -> None:
        self.fuyao = FuyaoClient(key)
        self.data = DataService(self.fuyao, self.storage)

    def settings(self) -> dict:
        return self.storage.get_settings()


class SettingsUpdate(BaseModel):
    fuyao_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    default_universe: Optional[dict] = None


class ParseRequest(BaseModel):
    messages: list[dict] = Field(min_length=1)


class StrategyCreate(BaseModel):
    config: StrategyConfig


class ScreenRequest(BaseModel):
    strategy_id: Optional[int] = None
    config: Optional[StrategyConfig] = None
    universe: Optional[dict] = None
    as_of: Optional[str] = None


class BacktestRequest(BaseModel):
    strategy_id: Optional[int] = None
    config: Optional[StrategyConfig] = None
    start: Optional[str] = None
    end: Optional[str] = None
    initial_cash: Optional[float] = None
    position_pct: Optional[float] = None
    max_positions: Optional[int] = None
    fee_bps: Optional[float] = None
    stamp_tax_bps: Optional[float] = None
    universe: Optional[dict] = None


def _mask(key: str | None) -> str | None:
    if not key:
        return None
    return key[:5] + "****" + key[-4:] if len(key) > 12 else "****"


def _settings_view(core: Core) -> dict:
    s = core.settings()
    fuyao_key = s.get("fuyao_api_key") or ""
    llm_key = s.get("llm_api_key") or ""
    return {
        "fuyao_api_key": _mask(fuyao_key),
        "fuyao_api_key_set": bool(fuyao_key),
        "llm_base_url": s.get("llm_base_url") or "https://api.deepseek.com",
        "llm_api_key_set": bool(llm_key),
        "llm_model": s.get("llm_model") or "deepseek-chat",
        "llm_available": bool(llm_key),
        "default_universe": s.get("default_universe") or {"type": "index", "code": "000300.SH"},
    }


def _resolve_config(core: Core, strategy_id: int | None, config: StrategyConfig | None) -> StrategyConfig:
    if config is not None:
        return config
    if strategy_id is not None:
        detail = core.storage.get_strategy(strategy_id)
        return StrategyConfig.model_validate(detail["current"])
    raise ApiError(400, "bad_request", "strategy_id 与 config 必须提供其一")


def register_routes(app: FastAPI, core: Core) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        body: dict[str, Any] = {"error": {"code": exc.code, "message": exc.message}}
        if exc.raw:
            body["error"]["raw"] = exc.raw
        return JSONResponse(status_code=exc.status, content=body)

    @app.get("/api/health")
    def health():
        try:
            core.storage.get_settings()
            db_ok = True
        except Exception:
            db_ok = False
        return {
            "ok": db_ok,
            "version": app.version,
            "fuyao_ok": bool(core.settings().get("fuyao_api_key")),
            "db_ok": db_ok,
            "time": core.settings().get("tickers_synced_at"),
        }

    @app.get("/api/settings")
    def get_settings():
        return _settings_view(core)

    @app.put("/api/settings")
    def put_settings(body: SettingsUpdate):
        s = core.settings()
        updates: dict = {}
        if body.fuyao_api_key is not None:
            updates["fuyao_api_key"] = body.fuyao_api_key.strip()
        if body.llm_base_url is not None:
            updates["llm_base_url"] = body.llm_base_url.strip() or "https://api.deepseek.com"
        if body.llm_api_key is not None:
            updates["llm_api_key"] = body.llm_api_key.strip()
        if body.llm_model is not None:
            updates["llm_model"] = body.llm_model.strip() or "deepseek-chat"
        if body.default_universe is not None:
            updates["default_universe"] = body.default_universe
        core.storage.update_settings(updates)
        new_fuyao = core.settings().get("fuyao_api_key")
        if body.fuyao_api_key is not None and new_fuyao and new_fuyao != s.get("fuyao_api_key"):
            core.set_fuyao_key(new_fuyao)
        return _settings_view(core)

    @app.post("/api/parse-strategy")
    def parse_strategy(body: ParseRequest):
        s = core.settings()
        api_key = s.get("llm_api_key") or ""
        if not api_key:
            raise ApiError(400, LLM_NOT_CONFIGURED_CODE, "请先在设置页配置 DeepSeek API Key")
        aligner = DeepSeekAligner(
            base_url=s.get("llm_base_url") or "https://api.deepseek.com",
            api_key=api_key,
            model=s.get("llm_model") or "deepseek-chat",
        )
        try:
            return aligner.align(body.messages)
        except LLMParseError as e:
            raise ApiError(400, "llm_parse_failed", str(e), raw=e.raw) from e
        finally:
            aligner.close()

    # ---- strategies ----
    @app.get("/api/strategies")
    def list_strategies():
        return {"items": core.storage.list_strategies()}

    @app.post("/api/strategies")
    def create_strategy(body: StrategyCreate):
        return core.storage.create_strategy(body.config.model_dump())

    @app.get("/api/strategies/{sid}")
    def get_strategy(sid: int):
        try:
            return core.storage.get_strategy(sid)
        except KeyError as e:
            raise ApiError(404, "not_found", f"策略 {sid} 不存在") from e

    @app.put("/api/strategies/{sid}")
    def update_strategy(sid: int, body: StrategyCreate):
        try:
            return core.storage.update_strategy(sid, body.config.model_dump())
        except KeyError as e:
            raise ApiError(404, "not_found", f"策略 {sid} 不存在") from e

    @app.delete("/api/strategies/{sid}")
    def delete_strategy(sid: int):
        if not core.storage.delete_strategy(sid):
            raise ApiError(404, "not_found", f"策略 {sid} 不存在")
        return {"ok": True}

    @app.get("/api/strategies/{sid}/versions/{version}")
    def get_version(sid: int, version: int):
        try:
            cfg = core.storage.get_version(sid, version)
            return {"id": sid, "version": version, "config": cfg}
        except KeyError as e:
            raise ApiError(404, "not_found", f"策略 {sid} 版本 {version} 不存在") from e

    @app.post("/api/strategies/{sid}/restore/{version}")
    def restore_version(sid: int, version: int):
        try:
            return core.storage.restore_version(sid, version)
        except KeyError as e:
            raise ApiError(404, "not_found", f"策略 {sid} 版本 {version} 不存在") from e

    # ---- jobs ----
    @app.post("/api/screen")
    def start_screen(body: ScreenRequest):
        cfg = _resolve_config(core, body.strategy_id, body.config)
        if body.universe:
            cfg = cfg.model_copy(update={"universe": type(cfg.universe).model_validate(body.universe)})

        def run_screen(progress_cb):
            return screen(cfg, core.data, as_of=body.as_of, progress_cb=progress_cb)

        return {"job_id": core.jobs.submit("screen", run_screen)}

    @app.post("/api/backtest")
    def start_backtest(body: BacktestRequest):
        cfg = _resolve_config(core, body.strategy_id, body.config)
        d = cfg.backtest_defaults
        p = {
            "start": body.start or d.start,
            "end": body.end or d.end,
            "initial_cash": body.initial_cash or d.initial_cash,
            "position_pct": body.position_pct or d.position_pct,
            "max_positions": body.max_positions or d.max_positions,
            "fee_bps": body.fee_bps if body.fee_bps is not None else d.fee_bps,
            "stamp_tax_bps": body.stamp_tax_bps if body.stamp_tax_bps is not None else d.stamp_tax_bps,
        }
        from .schema import _valid_date

        for k in ("start", "end"):
            try:
                _valid_date(p[k])
            except ValueError as e:
                raise ApiError(400, "bad_request", f"{k} 日期格式错误: {p[k]}") from e

        def run_bt(progress_cb):
            return backtest(cfg, p, core.data, progress_cb=progress_cb)

        return {"job_id": core.jobs.submit("backtest", run_bt)}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = core.storage.get_job(job_id)
        if job is None:
            raise ApiError(404, "not_found", f"任务 {job_id} 不存在")
        return job

    # ---- market data ----
    @app.get("/api/kline")
    def kline(thscode: str, period: str = "1d", count: int = 120):
        if period not in PERIODS:
            raise ApiError(400, "bad_request", f"period 必须是 {PERIODS} 之一")
        count = max(10, min(count, 500))
        try:
            kind = core.data.kind_for(thscode)
            bars = core.data.get_bars(thscode, kind=kind, count=count)
        except FuyaoError as e:
            raise ApiError(502, "upstream_error", f"数据源错误: {e.message}") from e
        ticker = core.storage.get_ticker(thscode)
        if ticker:
            asset_type = "ths-index" if thscode.endswith(".TI") else ticker["asset_type"]
            name = ticker["name"]
        else:
            asset_type = "ths-index" if thscode.endswith(".TI") else ("a-share-index" if kind == "index" else "a-share")
            name = None
        return build_kline_response(thscode, name, asset_type, period, bars)

    @app.get("/api/search")
    def search(q: str, limit: int = 20):
        if not q.strip():
            return {"items": []}
        try:
            return {"items": core.data.search(q.strip(), min(limit, 50))}
        except FuyaoError as e:
            raise ApiError(502, "upstream_error", f"数据源错误: {e.message}") from e

    @app.get("/api/universe/options")
    def universe_options():
        try:
            return core.data.universe_options()
        except FuyaoError as e:
            raise ApiError(502, "upstream_error", f"数据源错误: {e.message}") from e
