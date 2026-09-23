"""Tests for FuyaoClient (MockTransport) and DataService (FakeClient) cache behavior."""
import json
from datetime import datetime, timedelta

import httpx
import pytest

from backend.data_service import DAY_MS, DataService
from backend.fuyao import FuyaoClient, FuyaoError, CST, last_completed_trading_day
from backend.storage import Storage


def envelope(data):
    return {"code": 0, "message": "success", "request_id": "t", "data": data}


def make_client(handler, **kw):
    transport = httpx.MockTransport(handler)
    return FuyaoClient("sk-test", min_interval=0, retry_delay=0.001, transport=transport, **kw)


def test_historical_params_and_normalization():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("X-api-key")
        return httpx.Response(200, json=envelope({
            "timestamp": 1,
            "item": [{"date_ms": 100, "open_price": 1.0, "high_price": 2.0, "low_price": 0.5,
                      "close_price": 1.5, "volume": 10, "turnover": 15}],
        }))

    c = make_client(handler)
    bars = c.stock_historical("600519.SH", 1000, 2000)
    assert "thscode=600519.SH" in captured["url"] and "adjust=forward" in captured["url"]
    assert captured["key"] == "sk-test"
    assert bars == [{"date_ms": 100, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10, "turnover": 15}]


def test_business_error_raises():
    def handler(request):
        return httpx.Response(200, json={"code": 3001, "message": "标的不存在", "request_id": "t"})

    c = make_client(handler)
    with pytest.raises(FuyaoError, match="3001"):
        c.snapshot(["600519.SH"])


def test_http_429_retries_then_success():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"code": 4001, "message": "rate limited"})
        return httpx.Response(200, json=envelope({"item": [{"thscode": "600519.SH"}]}))

    c = make_client(handler)
    items = c.snapshot(["600519.SH"])
    assert calls["n"] == 2 and items[0]["thscode"] == "600519.SH"


def test_code_4001_retries_then_gives_up():
    def handler(request):
        return httpx.Response(200, json={"code": 4001, "message": "频率超限"})

    c = make_client(handler, max_retries=2)
    with pytest.raises(FuyaoError, match="4001"):
        c.snapshot(["600519.SH"])


def test_transport_error_retries():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError("boom")

    c = make_client(handler, max_retries=1)
    with pytest.raises(httpx.ConnectError):
        c.snapshot(["600519.SH"])
    assert calls["n"] == 2  # initial + 1 retry


def test_tickers_all_pagination():
    pages = [
        [{"thscode": f"60000{i}.SH", "name": f"股{i}", "asset_type": "a-share", "exchange": "SH"} for i in range(2)],
        [{"thscode": "600009.SH", "name": "股9", "asset_type": "a-share", "exchange": "SH"}],
    ]
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        params = httpx.QueryParams(request.url.query)
        offset = int(params.get("offset", 0) or 0)
        return httpx.Response(200, json=envelope({"item": pages[offset // 2]}))

    c = make_client(handler)
    items = c.tickers_all("a-share", page=2)
    assert len(items) == 3 and calls["n"] == 2


def test_last_completed_trading_day_cutoffs():
    def day(ms):
        return {"date_ms": ms, "date": "x"}

    base = int(datetime(2026, 9, 23, tzinfo=CST).timestamp() * 1000)
    days = [day(base - 2 * DAY_MS), day(base - DAY_MS), day(base)]
    morning = datetime(2026, 9, 23, 10, 0, tzinfo=CST)
    assert last_completed_trading_day(morning, days) == base - DAY_MS  # before 15:30 → previous day
    evening = datetime(2026, 9, 23, 16, 0, tzinfo=CST)
    assert last_completed_trading_day(evening, days) == base
    assert last_completed_trading_day(morning, []) is None


def test_fuyao_client_trust_env_false(monkeypatch):
    real = httpx.Client
    captured = {}

    def fake(*args, **kwargs):
        captured.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr("backend.fuyao.httpx.Client", fake)
    c = FuyaoClient("sk-test")
    c.close()
    assert captured.get("trust_env") is False


# ---------- DataService with FakeClient ----------


class FakeClient:
    def __init__(self):
        self.fetches: list[tuple[str, int, int]] = []
        self.constituents = [
            {"thscode": "600519.SH", "ticker": "600519", "name": "贵州茅台"},
            {"thscode": "000858.SZ", "ticker": "000858", "name": "五粮液"},
        ]
        now = datetime.now(CST)
        self.today_ms = int(datetime(now.year, now.month, now.day, tzinfo=CST).timestamp() * 1000)
        self.trading = [
            {"date_ms": self.today_ms - DAY_MS, "date": "yday"},
            {"date_ms": self.today_ms, "date": "today"},
        ]

    def trading_days(self):
        return self.trading

    def stock_historical(self, thscode, start_ms, end_ms):
        self.fetches.append((thscode, start_ms, end_ms))
        days = [self.today_ms - k * DAY_MS for k in range(0, 1000) if start_ms <= self.today_ms - k * DAY_MS <= end_ms]
        return [
            {"date_ms": d, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0, "turnover": 1050.0}
            for d in sorted(days)
        ]

    def index_historical(self, thscode, start_ms, end_ms):
        return self.stock_historical(thscode, start_ms, end_ms)

    def index_constituents(self, code):
        return self.constituents

    def tickers_all(self, asset_type):
        if asset_type == "a-share":
            return [
                {"thscode": "600519.SH", "name": "贵州茅台", "asset_type": "a-share", "exchange": "SH"},
                {"thscode": "000858.SZ", "name": "五粮液", "asset_type": "a-share", "exchange": "SZ"},
            ]
        return [{"thscode": "000300.SH", "name": "沪深300", "asset_type": "a-share-index", "exchange": "SH"}]

    def ths_index_list(self, tag):
        return [{"thscode": "881101.TI", "name": "种植业"}]


@pytest.fixture()
def svc(tmp_path):
    client = FakeClient()
    storage = Storage(tmp_path / "t.db")
    return DataService(client, storage), client, storage


def _completed_day(client):
    from backend.fuyao import last_completed_trading_day, now_cst

    return last_completed_trading_day(now_cst(), client.trading)


def test_get_bars_empty_cache_full_fetch(svc):
    ds, client, storage = svc
    bars = ds.get_bars("600519.SH", "stock", count=10)
    assert len(bars) >= 10
    assert len(client.fetches) == 1
    code, start, end = client.fetches[0]
    assert code == "600519.SH" and end == _completed_day(client)
    assert start <= client.today_ms - 30 * DAY_MS
    cached = storage.get_klines("600519.SH")
    assert len(cached) >= 10


def test_get_bars_fresh_cache_no_fetch(svc):
    ds, client, storage = svc
    ds.get_bars("600519.SH", "stock", count=10)
    n = len(client.fetches)
    bars2 = ds.get_bars("600519.SH", "stock", count=10)
    assert len(client.fetches) == n  # no additional fetch
    assert bars2[-1]["date_ms"] == _completed_day(client)


def test_get_bars_stale_tail_incremental(svc):
    ds, client, storage = svc
    old_bars = [
        {"date_ms": client.today_ms - k * DAY_MS, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100, "turnover": 1000}
        for k in range(11, 31)
    ]
    storage.upsert_klines("600519.SH", old_bars)
    ds.get_bars("600519.SH", "stock", count=10)
    assert len(client.fetches) == 1
    _, start, end = client.fetches[0]
    assert end == _completed_day(client)
    assert start <= client.today_ms - 11 * DAY_MS - 5 * DAY_MS + 1000  # incremental from ~have_last-5d


def test_get_bars_as_of_uses_cache(svc):
    ds, client, storage = svc
    as_of = client.today_ms - 5 * DAY_MS
    bars = [
        {"date_ms": client.today_ms - k * DAY_MS, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100, "turnover": 1000}
        for k in range(6, 26)
    ]
    storage.upsert_klines("600519.SH", bars)
    got = ds.get_bars("600519.SH", "stock", end_ms=as_of, count=15)
    assert len(client.fetches) == 0
    assert got[-1]["date_ms"] == client.today_ms - 6 * DAY_MS  # last cached bar ≤ as_of (within tolerance)


def test_get_bars_range_head_backfill(svc):
    ds, client, storage = svc
    start_ms = client.today_ms - 60 * DAY_MS
    end_ms = client.today_ms - 10 * DAY_MS
    bars = ds.get_bars_range("600519.SH", "stock", start_ms, end_ms)
    assert len(client.fetches) >= 1
    assert bars[0]["date_ms"] <= start_ms  # includes warmup history before start
    assert bars[-1]["date_ms"] >= end_ms - 10 * DAY_MS


def test_resolve_universe(svc):
    ds, client, storage = svc
    codes, names, label = ds.resolve_universe({"type": "index", "code": "000300.SH"})
    assert codes == ["600519.SH", "000858.SZ"] and names["600519.SH"] == "贵州茅台"

    codes2, _, label2 = ds.resolve_universe({"type": "custom", "codes": ["600519.SH"]})
    assert codes2 == ["600519.SH"] and label2 == "自选"

    ds.sync_tickers()
    codes3, names3, label3 = ds.resolve_universe({"type": "all"})
    assert set(codes3) == {"600519.SH", "000858.SZ"} and label3 == "全市场"


def test_search_and_kind(svc):
    ds, client, storage = svc
    ds.sync_tickers()
    hits = ds.search("种植")
    assert hits[0]["thscode"] == "881101.TI" and hits[0]["asset_type"] == "ths-index"
    assert hits[0]["kline_available"] is True
    assert ds.kind_for("881101.TI") == "index"
    assert ds.kind_for("600519.SH") == "stock"
    assert ds.kind_for("000300.SH") == "index"


def test_universe_options(svc):
    ds, client, storage = svc
    opts = ds.universe_options()
    names = [i["name"] for i in opts["indices"]]
    assert "沪深300" in names
    assert any(s["code"] == "881101.TI" for s in opts["sectors"])
