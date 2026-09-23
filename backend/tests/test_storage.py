"""Tests for SQLite storage: settings, versioned strategies, kline cache, tickers, trading days, jobs."""
import pytest

from backend.storage import Storage
from backend.schema import REFERENCE_STRATEGY


@pytest.fixture()
def store(tmp_path):
    return Storage(tmp_path / "test.db")


def test_settings_roundtrip_and_merge(store):
    store.update_settings({"fuyao_api_key": "sk-abc", "default_universe": {"type": "index", "code": "000300.SH"}})
    s = store.get_settings()
    assert s["fuyao_api_key"] == "sk-abc"
    assert s["default_universe"] == {"type": "index", "code": "000300.SH"}
    store.update_settings({"fuyao_api_key": "sk-xyz"})
    s2 = store.get_settings()
    assert s2["fuyao_api_key"] == "sk-xyz"
    assert s2["default_universe"] == {"type": "index", "code": "000300.SH"}


def test_strategy_crud_versioning_restore(store):
    import copy

    cfg1 = copy.deepcopy(REFERENCE_STRATEGY)
    created = store.create_strategy(cfg1)
    assert created["id"] == 1 and created["version"] == 1

    cfg2 = copy.deepcopy(REFERENCE_STRATEGY)
    cfg2["name"] = "改名后的策略"
    cfg2["risk"]["stop_loss_pct"] = 5.0
    updated = store.update_strategy(1, cfg2)
    assert updated["version"] == 2

    detail = store.get_strategy(1)
    assert detail["version"] == 2
    assert detail["current"]["name"] == "改名后的策略"
    assert [v["version"] for v in detail["versions"]] == [1, 2]

    v1 = store.get_version(1, 1)
    assert v1["name"] == REFERENCE_STRATEGY["name"]

    restored = store.restore_version(1, 1)
    assert restored["version"] == 3
    assert store.get_strategy(1)["current"]["name"] == REFERENCE_STRATEGY["name"]

    items = store.list_strategies()
    assert len(items) == 1
    assert items[0]["version"] == 3
    assert items[0]["entry_count"] == 8
    assert items[0]["exit_count"] == 5

    assert store.delete_strategy(1) is True
    assert store.list_strategies() == []
    with pytest.raises(KeyError):
        store.get_strategy(1)


def test_strategy_missing_raises(store):
    with pytest.raises(KeyError):
        store.get_version(99, 1)
    with pytest.raises(KeyError):
        store.update_strategy(99, {"name": "x"})


def test_klines_upsert_conflict_and_query(store):
    bars1 = [
        {"date_ms": 1000, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100, "turnover": 1050},
        {"date_ms": 2000, "open": 10.5, "high": 12, "low": 10, "close": 11.5, "volume": 200, "turnover": 2300},
    ]
    store.upsert_klines("600519.SH", bars1)
    bars2 = [{"date_ms": 2000, "open": 10.5, "high": 12, "low": 10, "close": 11.8, "volume": 250, "turnover": 2400}]
    store.upsert_klines("600519.SH", bars2)

    got = store.get_klines("600519.SH")
    assert len(got) == 2
    assert got[1]["close"] == 11.8  # conflict updated
    assert got[1]["volume"] == 250
    assert store.last_kline_date("600519.SH") == 2000

    ranged = store.get_klines("600519.SH", start_ms=1500, end_ms=2500)
    assert [b["date_ms"] for b in ranged] == [2000]
    assert store.get_klines("000001.SZ") == []
    assert store.cached_codes() == {"600519.SH"}


def test_tickers_search_and_all(store):
    store.upsert_tickers([
        {"thscode": "600519.SH", "name": "贵州茅台", "asset_type": "a-share", "exchange": "SH"},
        {"thscode": "000300.SH", "name": "沪深300", "asset_type": "a-share-index", "exchange": "SH"},
        {"thscode": "881101.TI", "name": "种植业", "asset_type": "ths-index", "exchange": None},
    ])
    assert store.get_ticker("600519.SH")["name"] == "贵州茅台"
    by_name = store.search_tickers("茅台")
    assert [t["thscode"] for t in by_name] == ["600519.SH"]
    by_code = store.search_tickers("8811")
    assert [t["thscode"] for t in by_code] == ["881101.TI"]
    all_a = store.all_tickers("a-share")
    assert len(all_a) == 1
    assert len(store.all_tickers()) == 3


def test_trading_days(store):
    store.upsert_trading_days([
        {"date_ms": 1000, "date": "20260101"},
        {"date_ms": 2000, "date": "20260102"},
        {"date_ms": 3000, "date": "20260105"},
    ])
    days = store.trading_days_list()
    assert len(days) == 3
    assert store.latest_trading_day() == 3000
    assert store.latest_trading_day(at_ms=2500) == 2000
    assert store.latest_trading_day(at_ms=500) is None


def test_jobs_lifecycle(store):
    store.create_job("j_1", "screen")
    assert store.get_job("j_1")["status"] == "running"
    store.update_job("j_1", progress={"done": 5, "total": 10, "current": "600519.SH"})
    job = store.get_job("j_1")
    assert job["progress"] == {"done": 5, "total": 10, "current": "600519.SH"}
    assert job["result"] is None
    store.update_job("j_1", status="done", result={"matched_count": 3})
    job = store.get_job("j_1")
    assert job["status"] == "done"
    assert job["result"]["matched_count"] == 3
    assert store.get_job("missing") is None


def test_job_error_path(store):
    store.create_job("j_2", "backtest")
    store.update_job("j_2", status="error", error="上游数据源不可用")
    job = store.get_job("j_2")
    assert job["status"] == "error" and job["error"] == "上游数据源不可用"
