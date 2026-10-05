"""Tests for backend.seed: built-in strategies creation + idempotency."""
import pytest

from backend.seed import seed_strategies
from backend.storage import Storage

SCREENING_NAMES = {"MACD金叉选股", "放量站上20日线"}
TRADING_NAMES = {"止损8%+涨10%减半+跌破10日线清仓", "移动止损8%+持仓20日"}


@pytest.fixture()
def store(tmp_path):
    return Storage(tmp_path / "test.db")


def test_seed_creates_two_screening_and_two_trading(store):
    result = seed_strategies(store)
    assert result["created"] == [
        "MACD金叉选股", "放量站上20日线",
        "止损8%+涨10%减半+跌破10日线清仓", "移动止损8%+持仓20日",
    ]
    assert result["skipped"] == []

    screening = store.list_strategies(type="screening")
    trading = store.list_strategies(type="trading")
    assert len(screening) == 2
    assert len(trading) == 2
    assert {it["name"] for it in screening} == SCREENING_NAMES
    assert {it["name"] for it in trading} == TRADING_NAMES
    assert {it["type"] for it in screening} == {"screening"}
    assert {it["type"] for it in trading} == {"trading"}
    assert store.list_strategies(type="nope") == []


def test_seed_is_idempotent(store):
    first = seed_strategies(store)
    assert len(first["created"]) == 4 and first["skipped"] == []
    second = seed_strategies(store)
    assert second["created"] == []
    assert len(second["skipped"]) == 4
    assert len(store.list_strategies()) == 4


def test_seed_rerun_repairs_only_missing(store):
    seed_strategies(store)
    deleted = store.list_strategies(type="screening")[0]["id"]
    assert store.delete_strategy(deleted) is True

    result = seed_strategies(store)
    assert len(result["created"]) == 1
    assert len(result["skipped"]) == 3
    assert len(store.list_strategies(type="screening")) == 2
    assert len(store.list_strategies(type="trading")) == 2
    assert len(store.list_strategies()) == 4
