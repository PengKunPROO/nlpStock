"""API layer tests: TestClient with fake data service and fake aligner.

覆盖：screening/trading 策略拆分（type 区分）、screen_range 区间选股、backtest_pool 固定池回测、
backtest_analyses 保存/列出、?type= 过滤，以及 settings/parse-strategy/kline/search/universe/health 不回归。
"""
import copy
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend.api import Core
from backend.app import create_app
from backend.fuyao import CST
from backend.storage import Storage

DAY = 86_400_000
BASE_MS = 1_760_000_000_000


def dstr(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=CST).strftime("%Y-%m-%d")


def make_bars():
    """130 根K线：前100根平10元，其后每天+0.2；i=105/106 放量 → screening entry 在 i=105 成立。"""
    bars = []
    for i in range(130):
        price = 10.0 + (0.2 * (i - 100) if i > 100 else 0.0)
        vol = 500.0 if i in (105, 106) else 100.0
        bars.append({
            "date_ms": BASE_MS - 130 * DAY + i * DAY,
            "open": price * 0.999, "high": price * 1.005, "low": price * 0.994,
            "close": price, "volume": vol, "turnover": price * vol,
        })
    return bars


SCREENING_CFG = {
    "name": "站上20日线且放量",
    "description": "测试选股策略",
    "source_text": "",
    "parse_engine": "llm",
    "universe": {"type": "custom", "codes": ["600001.SH"]},
    "indicators": [
        {"id": "ma20", "kind": "MA", "of": "close", "n": 20},
        {"id": "vr", "kind": "VRATIO", "n": 5},
    ],
    "entry": {"logic": "all", "conditions": [
        {"left": "close", "op": ">", "right": "ma20", "note": "站上20日线"},
        {"left": "vr", "op": ">=", "right": 1.2, "note": "量比≥1.2"},
    ]},
}

TRADING_CFG = {
    "name": "跌破5日线清仓",
    "description": "测试交易策略",
    "source_text": "",
    "parse_engine": "llm",
    "indicators": [{"id": "ma5", "kind": "MA", "of": "close", "n": 5}],
    "rules": [
        {"when": {"logic": "any", "conditions": [{"left": "close", "op": "<", "right": "ma5"}]},
         "action": "sell", "size_pct": 100, "note": "跌破5日线清仓"},
    ],
    "risk": {"stop_loss_pct": None, "trailing_stop_pct": None, "max_hold_days": None, "take_profit_pct": None},
    "backtest_defaults": {
        "start": "2025-01-01", "end": "2026-12-31", "initial_cash": 500000,
        "position_pct": 50, "max_positions": 5, "fee_bps": 2.5, "stamp_tax_bps": 5.0,
    },
}


class ApiFakeData:
    def __init__(self):
        self.bars = make_bars()

    def resolve_universe(self, u):
        if u["type"] == "custom":
            return list(u["codes"]), {c: f"股{c[:6]}" for c in u["codes"]}, "自选"
        return ["600001.SH"], {"600001.SH": "平安银行"}, "测试池"

    def kind_for(self, code):
        return "stock"

    def get_bars(self, code, kind="stock", end_ms=None, count=250):
        bars = self.bars
        if end_ms is not None:
            bars = [b for b in bars if b["date_ms"] <= end_ms]
        return bars[-count:]

    def get_bars_range(self, code, kind, start_ms, end_ms, warmup_bars=250):
        return [b for b in self.bars if b["date_ms"] <= end_ms]

    def search(self, q, limit=20):
        return [
            {"thscode": "600519.SH", "name": "贵州茅台", "asset_type": "a-share", "exchange": "SH", "kline_available": True},
            {"thscode": "881101.TI", "name": "种植业", "asset_type": "ths-index", "exchange": None, "kline_available": True},
        ][:limit]

    def universe_options(self):
        return {
            "indices": [{"code": "000300.SH", "name": "沪深300", "count": None}],
            "sectors": [{"code": "881101.TI", "name": "种植业", "count": None}],
        }


class FakeAligner:
    instances = []

    def __init__(self, base_url, api_key, model="deepseek-chat", **kw):
        self.base_url, self.api_key, self.model = base_url, api_key, model
        self.responses = FakeAligner.instances
        FakeAligner.instances.append(self)

    def align(self, messages, strategy_type="screening"):
        cfg = copy.deepcopy(SCREENING_CFG)
        cfg["source_text"] = messages[0]["content"]
        cfg["parse_engine"] = "llm"
        return {
            "type": "config",
            "config": cfg,
            "summary": "已量化",
            "warnings": ["止损默认8%"],
        }

    def close(self):
        pass


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FUYAO_API_KEY", "sk-fuyao-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-llm-test")
    monkeypatch.setattr("backend.api.DeepSeekAligner", FakeAligner)
    FakeAligner.instances = []
    app = create_app(db_path=tmp_path / "t.db", static_dir=tmp_path / "no_frontend")
    return TestClient(app)


@pytest.fixture()
def client_fake_data(tmp_path, monkeypatch):
    """App whose Core.data is the fake data service (jobs actually run, no network)."""
    monkeypatch.setenv("FUYAO_API_KEY", "sk-fuyao-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-llm-test")
    monkeypatch.setattr("backend.api.DeepSeekAligner", FakeAligner)
    FakeAligner.instances = []

    class PatchedCore(Core):
        def __init__(self):
            super().__init__(Storage(tmp_path / "t2.db"), "sk-test")
            self.data = ApiFakeData()

    app = create_app(db_path=tmp_path / "t2.db", static_dir=tmp_path / "no_frontend", core=PatchedCore())
    return TestClient(app)


def wait_job(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("job timeout")


def create_strategy(client, cfg, type=None):
    body = {"config": cfg}
    if type is not None:
        body["type"] = type
    r = client.post("/api/strategies", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ---- health / settings / parse-strategy（不回归）----

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["db_ok"] is True and body["fuyao_ok"] is True
    assert body["version"]


def test_settings_masked_and_update(client_fake_data):
    c = client_fake_data
    body = c.get("/api/settings").json()
    assert body["fuyao_api_key_set"] is True
    assert body["llm_available"] is True
    r = c.put("/api/settings", json={"llm_api_key": ""})
    assert r.status_code == 200
    assert c.get("/api/settings").json()["llm_api_key_set"] is False
    c.put("/api/settings", json={"llm_api_key": "sk-test"})


def test_parse_strategy_not_configured(client_fake_data):
    client_fake_data.put("/api/settings", json={"llm_api_key": ""})
    r = client_fake_data.post("/api/parse-strategy", json={"messages": [{"role": "user", "content": "test"}]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "llm_not_configured"
    client_fake_data.put("/api/settings", json={"llm_api_key": "sk-test"})


def test_parse_strategy_config_flow(client_fake_data):
    msgs = [{"role": "user", "content": "站上20日线且放量"}]
    r = client_fake_data.post("/api/parse-strategy", json={"messages": msgs})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "config"
    assert body["config"]["name"] == SCREENING_CFG["name"]
    assert body["config"]["source_text"] == "站上20日线且放量"
    assert body["warnings"] == ["止损默认8%"]


# ---- strategies CRUD + type 区分 ----

def test_create_strategies_with_type(client_fake_data):
    c = client_fake_data
    # 无 type → 结构推断 screening
    created = create_strategy(c, SCREENING_CFG)
    assert created["type"] == "screening"
    # 显式 type=trading
    t1 = create_strategy(c, TRADING_CFG, type="trading")
    assert t1["type"] == "trading"
    # 无 type → 结构推断 trading（有 rules 无 entry）
    t2 = create_strategy(c, TRADING_CFG)
    assert t2["type"] == "trading"
    # type 与 config 结构不符 → 400
    r = c.post("/api/strategies", json={"config": SCREENING_CFG, "type": "trading"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"
    # 列表带 type 字段
    items = c.get("/api/strategies").json()["items"]
    types = {it["id"]: it["type"] for it in items}
    assert types[created["id"]] == "screening"
    assert types[t1["id"]] == "trading"
    assert types[t2["id"]] == "trading"


def test_list_strategies_type_filter(client_fake_data):
    c = client_fake_data
    s = create_strategy(c, SCREENING_CFG)
    t = create_strategy(c, TRADING_CFG, type="trading")

    trading_ids = {it["id"] for it in c.get("/api/strategies?type=trading").json()["items"]}
    assert t["id"] in trading_ids and s["id"] not in trading_ids

    screening_ids = {it["id"] for it in c.get("/api/strategies?type=screening").json()["items"]}
    assert s["id"] in screening_ids and t["id"] not in screening_ids


def test_strategy_crud_versioning(client_fake_data):
    c = client_fake_data
    created = create_strategy(c, SCREENING_CFG)
    sid, v = created["id"], created["version"]
    assert v == 1

    cfg = copy.deepcopy(SCREENING_CFG)
    cfg["name"] = "改名"
    r = c.put(f"/api/strategies/{sid}", json={"config": cfg})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert r.json()["type"] == "screening"

    detail = c.get(f"/api/strategies/{sid}").json()
    assert detail["version"] == 2 and [x["version"] for x in detail["versions"]] == [1, 2]

    v1 = c.get(f"/api/strategies/{sid}/versions/1").json()
    assert v1["config"]["name"] == SCREENING_CFG["name"]

    restored = c.post(f"/api/strategies/{sid}/restore/1").json()
    assert restored["version"] == 3

    assert c.delete(f"/api/strategies/{sid}").json()["ok"] is True
    assert c.get(f"/api/strategies/{sid}").status_code == 404


# ---- screen（选股：screen_range）----

def test_screen_job_flow(client_fake_data):
    c = client_fake_data
    bars = make_bars()
    sid = create_strategy(c, SCREENING_CFG)["id"]
    start, end = dstr(bars[100]["date_ms"]), dstr(bars[129]["date_ms"])

    r = c.post("/api/screen", json={"strategy_id": sid, "start": start, "end": end})
    assert r.status_code == 200
    job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done", job.get("error")
    res = job["result"]
    assert res["evaluated"] == 1
    assert res["matched_count"] == 1
    assert res["as_of"] == end
    assert res["matched"][0]["thscode"] == "600001.SH"
    assert res["matched"][0]["signal_date"] == dstr(bars[105]["date_ms"])


def test_screen_universe_override(client_fake_data):
    c = client_fake_data
    bars = make_bars()
    sid = create_strategy(c, SCREENING_CFG)["id"]
    start, end = dstr(bars[100]["date_ms"]), dstr(bars[129]["date_ms"])
    r = c.post("/api/screen", json={
        "strategy_id": sid, "start": start, "end": end,
        "universe": {"type": "custom", "codes": ["600001.SH"]},
    })
    job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done", job.get("error")
    assert job["result"]["matched_count"] == 1


def test_screen_requires_strategy(client_fake_data):
    c = client_fake_data
    bars = make_bars()
    r = c.post("/api/screen", json={"start": dstr(bars[0]["date_ms"]), "end": dstr(bars[129]["date_ms"])})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_screen_rejects_trading_strategy(client_fake_data):
    c = client_fake_data
    bars = make_bars()
    tid = create_strategy(c, TRADING_CFG, type="trading")["id"]
    r = c.post("/api/screen", json={
        "strategy_id": tid, "start": dstr(bars[0]["date_ms"]), "end": dstr(bars[129]["date_ms"]),
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


# ---- backtest（交易：backtest_pool）----

def test_backtest_job_flow(client_fake_data):
    c = client_fake_data
    bars = make_bars()
    sid = create_strategy(c, TRADING_CFG, type="trading")["id"]
    pool = [{"thscode": "600001.SH", "name": "平安银行", "signal_date": dstr(bars[105]["date_ms"])}]

    r = c.post("/api/backtest", json={
        "trading_strategy_id": sid, "pool": pool,
        "start": dstr(bars[0]["date_ms"]), "end": dstr(bars[129]["date_ms"]),
        "initial_cash": 500000, "position_pct": 50,
    })
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done", job.get("error")
    res = job["result"]
    assert res["params"]["initial_cash"] == 500000
    assert res["params"]["strategy_id"] == sid
    assert res["params"]["strategy_name"] == TRADING_CFG["name"]
    assert res["params"]["strategy_version"] == 1
    assert res["metrics"]["final_equity"] > 0
    assert len(res["equity_curve"]) > 0
    assert isinstance(res["trades"], list) and len(res["trades"]) > 0


def test_backtest_bad_date_rejected(client_fake_data):
    c = client_fake_data
    pool = [{"thscode": "600001.SH", "name": "平安银行", "signal_date": "2025-06-01"}]
    r = c.post("/api/backtest", json={"config": TRADING_CFG, "pool": pool, "start": "2025/01/01"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_backtest_empty_pool_rejected(client_fake_data):
    c = client_fake_data
    r = c.post("/api/backtest", json={"config": TRADING_CFG, "pool": []})
    assert r.status_code == 422  # pydantic min_items


# ---- backtest analyses ----

def test_backtest_analyses_save_and_list(client_fake_data):
    c = client_fake_data
    sid = create_strategy(c, TRADING_CFG, type="trading")["id"]
    params = {"start": "2025-01-01", "end": "2025-12-31", "initial_cash": 500000}
    result = {"metrics": {"final_equity": 600000}}

    r = c.post("/api/backtest_analyses", json={"trading_strategy_id": sid, "params": params, "result": result})
    assert r.status_code == 200
    aid = r.json()["id"]
    assert aid == 1

    items = c.get("/api/backtest_analyses").json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == aid
    assert items[0]["trading_strategy_id"] == sid
    assert items[0]["params"] == params
    assert items[0]["result"] == result

    filtered = c.get(f"/api/backtest_analyses?trading_strategy_id={sid}").json()["items"]
    assert len(filtered) == 1
    assert c.get("/api/backtest_analyses?trading_strategy_id=999").json()["items"] == []


# ---- market data / jobs（不回归）----

def test_kline_endpoint(client_fake_data):
    c = client_fake_data
    r = c.get("/api/kline?thscode=600519.SH&period=1d&count=60")
    assert r.status_code == 200
    body = r.json()
    assert body["thscode"] == "600519.SH"
    assert body["asset_type"] == "a-share"
    assert len(body["bars"]) == 60
    last = body["bars"][-1]
    for k in ("open", "high", "low", "close", "volume", "vratio", "vol_state", "ma5", "ma20"):
        assert k in last

    r3 = c.get("/api/kline?thscode=600519.SH&period=1h")
    assert r3.status_code == 400 and r3.json()["error"]["code"] == "bad_request"


def test_search_and_universe_options(client_fake_data):
    c = client_fake_data
    hits = c.get("/api/search?q=茅台").json()["items"]
    assert hits[0]["thscode"] == "600519.SH"
    opts = c.get("/api/universe/options").json()
    assert opts["indices"][0]["code"] == "000300.SH"
    assert opts["sectors"][0]["code"] == "881101.TI"


def test_job_not_found(client_fake_data):
    r = client_fake_data.get("/api/jobs/j_missing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
