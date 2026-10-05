"""FastAPI routes: settings, strategy alignment/CRUD, screening/backtest jobs, kline, search, analyses."""
from __future__ import annotations

from typing import Any, Optional, Union

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError, conlist

from .backtest import backtest_pool
from .data_service import DataService
from .fuyao import FuyaoClient, FuyaoError
from .jobs import JobRunner
from .kline import PERIODS, build_kline_response
from .llm_align import LLMNotConfigured, LLMParseError, DeepSeekAligner
from .schema import ScreeningStrategy, TradingStrategy, Universe, _valid_date
from .screener import screen_range
from .storage import Storage

LLM_NOT_CONFIGURED_CODE = "llm_not_configured"
STRATEGY_TYPES = ("screening", "trading")


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
    messages: conlist(dict, min_items=1)  # type: ignore[valid-type]
    strategy_type: str = "screening"  # "screening"（选股策略）| "trading"（交易策略），UI 按 Tab 区分


class StrategyCreate(BaseModel):
    config: Union[ScreeningStrategy, TradingStrategy]
    type: Optional[str] = None  # "screening" | "trading"；缺省按 config 结构推断（有 rules → trading）


class ScreenRequest(BaseModel):
    strategy_id: Optional[int] = None  # 指向 screening 策略
    config: Optional[ScreeningStrategy] = None
    start: str
    end: str
    universe: Optional[dict] = None


class BacktestRequest(BaseModel):
    trading_strategy_id: Optional[int] = None  # 指向 trading 策略
    config: Optional[TradingStrategy] = None
    pool: conlist(dict, min_items=1)  # type: ignore[valid-type]  # [{thscode, name, signal_date}]
    start: Optional[str] = None
    end: Optional[str] = None
    initial_cash: Optional[float] = None
    position_pct: Optional[float] = None
    fee_bps: Optional[float] = None
    stamp_tax_bps: Optional[float] = None


class BacktestAnalysisCreate(BaseModel):
    trading_strategy_id: int
    params: dict
    result: dict


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


def _infer_strategy_type(config) -> str:
    return "trading" if isinstance(config, TradingStrategy) else "screening"


def _strategy_type(body: StrategyCreate) -> str:
    """策略类型：显式 type 优先；缺省按 config 结构推断；type 与结构矛盾 → 400。"""
    inferred = _infer_strategy_type(body.config)
    if body.type is not None:
        if body.type not in STRATEGY_TYPES:
            raise ApiError(400, "bad_request", f"type 必须是 screening/trading，收到: {body.type}")
        if body.type != inferred:
            raise ApiError(400, "bad_request", f"type={body.type} 与 config 结构不符（应为 {inferred}）")
    return body.type or inferred


def _resolve_screening_strategy(
    core: Core, strategy_id: int | None, config: ScreeningStrategy | None
) -> ScreeningStrategy:
    if config is not None:
        return config
    if strategy_id is not None:
        try:
            detail = core.storage.get_strategy(strategy_id)
            return ScreeningStrategy.parse_obj(detail["current"])
        except KeyError as e:
            raise ApiError(404, "not_found", f"策略 {strategy_id} 不存在") from e
        except ValidationError as e:
            raise ApiError(400, "bad_request", f"策略 {strategy_id} 不是选股策略") from e
    raise ApiError(400, "bad_request", "strategy_id 与 config 必须提供其一")


def _resolve_trading_strategy(
    core: Core, strategy_id: int | None, config: TradingStrategy | None
) -> TradingStrategy:
    if config is not None:
        return config
    if strategy_id is not None:
        try:
            detail = core.storage.get_strategy(strategy_id)
            return TradingStrategy.parse_obj(detail["current"])
        except KeyError as e:
            raise ApiError(404, "not_found", f"策略 {strategy_id} 不存在") from e
        except ValidationError as e:
            raise ApiError(400, "bad_request", f"策略 {strategy_id} 不是交易策略") from e
    raise ApiError(400, "bad_request", "trading_strategy_id 与 config 必须提供其一")


def _validate_dates(start: str, end: str) -> None:
    for k, v in (("start", start), ("end", end)):
        try:
            _valid_date(v)
        except ValueError as e:
            raise ApiError(400, "bad_request", f"{k} 日期格式错误: {v}") from e


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
            return aligner.align(body.messages, body.strategy_type)
        except LLMParseError as e:
            raise ApiError(400, "llm_parse_failed", str(e), raw=e.raw) from e
        finally:
            aligner.close()

    # ---- strategies ----
    @app.get("/api/strategies")
    def list_strategies(type: Optional[str] = None):
        return {"items": core.storage.list_strategies(type)}

    @app.post("/api/strategies")
    def create_strategy(body: StrategyCreate):
        stype = _strategy_type(body)
        created = core.storage.create_strategy(body.config.dict(), type=stype)
        created["type"] = stype
        return created

    @app.get("/api/strategies/{sid}")
    def get_strategy(sid: int):
        try:
            return core.storage.get_strategy(sid)
        except KeyError as e:
            raise ApiError(404, "not_found", f"策略 {sid} 不存在") from e

    @app.put("/api/strategies/{sid}")
    def update_strategy(sid: int, body: StrategyCreate):
        stype = _strategy_type(body)
        try:
            updated = core.storage.update_strategy(sid, body.config.dict(), type=stype)
        except KeyError as e:
            raise ApiError(404, "not_found", f"策略 {sid} 不存在") from e
        updated["type"] = stype
        return updated

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
        cfg = _resolve_screening_strategy(core, body.strategy_id, body.config)
        if body.universe:
            cfg = cfg.copy(update={"universe": Universe.parse_obj(body.universe)})
        _validate_dates(body.start, body.end)

        def run_screen(progress_cb):
            return screen_range(cfg, core.data, body.start, body.end, progress_cb=progress_cb)

        return {"job_id": core.jobs.submit("screen", run_screen)}

    @app.post("/api/backtest")
    def start_backtest(body: BacktestRequest):
        cfg = _resolve_trading_strategy(core, body.trading_strategy_id, body.config)
        d = cfg.backtest_defaults
        p = {
            "start": body.start or d.start,
            "end": body.end or d.end,
            "initial_cash": body.initial_cash or d.initial_cash,
            "position_pct": body.position_pct or d.position_pct,
            "fee_bps": body.fee_bps if body.fee_bps is not None else d.fee_bps,
            "stamp_tax_bps": body.stamp_tax_bps if body.stamp_tax_bps is not None else d.stamp_tax_bps,
        }
        _validate_dates(p["start"], p["end"])

        def run_bt(progress_cb):
            result = backtest_pool(cfg, body.pool, p, core.data, progress_cb=progress_cb)
            result["params"]["strategy_id"] = body.trading_strategy_id
            result["params"]["strategy_name"] = cfg.name
            result["params"]["strategy_version"] = None
            if body.trading_strategy_id is not None:
                detail = core.storage.get_strategy(body.trading_strategy_id)
                result["params"]["strategy_version"] = detail["version"]
            return result

        return {"job_id": core.jobs.submit("backtest", run_bt)}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = core.storage.get_job(job_id)
        if job is None:
            raise ApiError(404, "not_found", f"任务 {job_id} 不存在")
        return job

    # ---- backtest analyses ----
    @app.post("/api/backtest_analyses")
    def save_backtest_analysis(body: BacktestAnalysisCreate):
        aid = core.storage.save_analysis(body.trading_strategy_id, body.params, body.result)
        return {"id": aid}

    @app.get("/api/backtest_analyses")
    def list_backtest_analyses(trading_strategy_id: Optional[int] = None):
        return {"items": core.storage.list_analyses(trading_strategy_id)}

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
