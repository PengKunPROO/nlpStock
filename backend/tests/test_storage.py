"""Tests for SQLite storage: settings, versioned strategies, kline cache, tickers, trading days, jobs, analyses."""
import copy
import sqlite3

import pytest

from backend.storage import Storage


# 与 schema.py 解耦：直接用 dict 构造，不依赖 StrategyConfig/ScreeningStrategy/TradingStrategy
SCREENING_CFG = {
    "name": "选股策略",
    "description": "测试选股策略",
    "parse_engine": "llm",
    "universe": {"type": "index", "code": "000300.SH"},
    "indicators": [
        {"id": "ma5", "kind": "MA", "of": "close", "n": 5},
        {"id": "chg5", "kind": "PCT_CHANGE", "of": "close", "n": 5},
    ],
    "entry": {
        "logic": "all",
        "conditions": [
            {"left": "close", "op": ">", "right": "ma5"},
            {"left": "chg5", "op": ">=", "right": 3, "within": 10},
        ],
    },
}

TRADING_CFG = {
    "name": "交易策略",
    "description": "测试交易策略",
    "parse_engine": "llm",
    "indicators": [{"id": "ma5", "kind": "MA", "of": "close", "n": 5}],
    "rules": [
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": "<=", "right": -5}]}, "action": "buy", "note": "补仓"},
        {"when": {"logic": "any", "conditions": [{"left": "close", "op": "<", "right": "ma5"}]}, "action": "sell", "size_pct": 100},
        {"when": {"logic": "all", "conditions": [{"left": "dd_from_peak", "op": "<=", "right": -8}]}, "action": "sell", "size_pct": 50},
    ],
    "risk": {"stop_loss_pct": 8.0},
    "backtest_defaults": {
        "start": "2025-01-01", "end": "2025-12-31", "initial_cash": 1000000,
        "position_pct": 20, "max_positions": 5, "fee_bps": 2.5, "stamp_tax_bps": 5.0,
    },
}


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
    cfg1 = copy.deepcopy(SCREENING_CFG)
    created = store.create_strategy(cfg1)
    assert created["id"] == 1 and created["version"] == 1

    cfg2 = copy.deepcopy(SCREENING_CFG)
    cfg2["name"] = "改名后的策略"
    cfg2["entry"]["conditions"].append({"left": "close", "op": "<", "right": "ma5"})
    updated = store.update_strategy(1, cfg2)
    assert updated["version"] == 2

    detail = store.get_strategy(1)
    assert detail["version"] == 2
    assert detail["current"]["name"] == "改名后的策略"
    assert [v["version"] for v in detail["versions"]] == [1, 2]

    v1 = store.get_version(1, 1)
    assert v1["name"] == SCREENING_CFG["name"]

    restored = store.restore_version(1, 1)
    assert restored["version"] == 3
    assert store.get_strategy(1)["current"]["name"] == SCREENING_CFG["name"]

    items = store.list_strategies()
    assert len(items) == 1
    assert items[0]["version"] == 3
    assert items[0]["type"] == "screening"
    assert items[0]["entry_count"] == 2
    assert items[0]["exit_count"] == 0

    assert store.delete_strategy(1) is True
    assert store.list_strategies() == []
    with pytest.raises(KeyError):
        store.get_strategy(1)


def test_strategy_type_stored_and_filtered(store):
    s1 = store.create_strategy(copy.deepcopy(SCREENING_CFG))  # 从 config 形状推断 screening
    t1 = store.create_strategy(copy.deepcopy(TRADING_CFG))  # 从 config 形状推断 trading
    t2 = store.create_strategy(copy.deepcopy(TRADING_CFG), type="trading")  # 显式参数
    s2 = store.create_strategy(copy.deepcopy(SCREENING_CFG), type="screening")  # 显式参数

    items = store.list_strategies()
    assert {it["id"] for it in items} == {s1["id"], t1["id"], t2["id"], s2["id"]}
    by_type = {it["id"]: it["type"] for it in items}
    assert by_type[s1["id"]] == "screening"
    assert by_type[t1["id"]] == "trading"
    assert by_type[t2["id"]] == "trading"
    assert by_type[s2["id"]] == "screening"

    trading = store.list_strategies(type="trading")
    assert {it["id"] for it in trading} == {t1["id"], t2["id"]}
    for it in trading:
        assert it["type"] == "trading"
        assert it["entry_count"] == 1  # 1 条 buy 规则 = 补仓数
        assert it["exit_count"] == 2  # 2 条 sell 规则 = 卖出数

    screening = store.list_strategies(type="screening")
    assert {it["id"] for it in screening} == {s1["id"], s2["id"]}
    for it in screening:
        assert it["type"] == "screening"
        assert it["entry_count"] == 2  # entry 条件数
        assert it["exit_count"] == 0

    assert store.list_strategies(type="nope") == []


def test_update_strategy_keeps_type(store):
    store.create_strategy(copy.deepcopy(TRADING_CFG))
    updated = store.update_strategy(1, copy.deepcopy(TRADING_CFG), type="trading")
    assert updated["version"] == 2
    assert store.list_strategies()[0]["type"] == "trading"
    assert [it["id"] for it in store.list_strategies(type="trading")] == [1]


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


def test_constituents_roundtrip(store):
    assert store.get_constituents("000300.SH") is None
    assert store.get_constituents_updated_at("000300.SH") is None

    items = [
        {"thscode": "600519.SH", "name": "贵州茅台"},
        {"thscode": "000858.SZ", "name": "五粮液"},
    ]
    store.upsert_constituents("000300.SH", items)
    got = store.get_constituents("000300.SH")
    assert got == items
    assert store.get_constituents_updated_at("000300.SH") is not None

    # overwrite keeps a single row
    store.upsert_constituents("000300.SH", items[:1])
    assert store.get_constituents("000300.SH") == items[:1]
    assert store.get_constituents("881101.TI") is None


def test_backtest_analysis_roundtrip(store):
    store.create_strategy(copy.deepcopy(TRADING_CFG))  # id=1
    aid = store.save_analysis(1, {"start": "2025-01-01", "initial_cash": 1000000}, {"win_rate": 0.6, "max_drawdown": -0.08})
    assert isinstance(aid, int)

    got = store.get_analysis(aid)
    assert got["id"] == aid
    assert got["trading_strategy_id"] == 1
    assert got["params"] == {"start": "2025-01-01", "initial_cash": 1000000}
    assert got["result"]["win_rate"] == 0.6
    assert got["created_at"]

    aid2 = store.save_analysis(1, {"start": "2025-02-01"}, {"win_rate": 0.5})
    all_ids = [a["id"] for a in store.list_analyses()]
    assert all_ids == [aid, aid2]
    assert [a["id"] for a in store.list_analyses(1)] == [aid, aid2]
    assert store.list_analyses(2) == []
    assert store.get_analysis(999) is None


def test_migration_adds_type_column_idempotently(tmp_path):
    # 模拟旧库：strategies 表没有 type 列
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE strategies(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    store1 = Storage(db)  # 首次初始化：迁移加 type 列
    store2 = Storage(db)  # 连续第二次初始化：不应报错

    store1.create_strategy(copy.deepcopy(SCREENING_CFG))
    store1.create_strategy(copy.deepcopy(TRADING_CFG))
    assert {it["type"] for it in store2.list_strategies()} == {"screening", "trading"}
