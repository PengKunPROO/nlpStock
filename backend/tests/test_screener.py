"""Tests for the screening engine with a fake DataService."""
import copy

import pytest

from backend.screener import parse_as_of, screen
from backend.schema import REFERENCE_STRATEGY, StrategyConfig


def simple_cfg():
    """Simple crossover strategy: close > ma20 and vr >= 1.2 today."""
    return StrategyConfig.model_validate({
        "name": "站上20日线且放量",
        "description": "",
        "source_text": "",
        "parse_engine": "llm",
        "universe": {"type": "custom", "codes": ["600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH"]},
        "indicators": [
            {"id": "ma20", "kind": "MA", "of": "close", "n": 20},
            {"id": "vr", "kind": "VRATIO", "n": 5},
        ],
        "entry": {"logic": "all", "conditions": [
            {"left": "close", "op": ">", "right": "ma20", "note": "站上20日线"},
            {"left": "vr", "op": ">=", "right": 1.2, "note": "量比≥1.2"},
        ]},
        "exit": {"logic": "any", "conditions": [
            {"left": "close", "op": "<", "right": "ma20", "note": "跌破20日线"},
        ]},
        "risk": {"stop_loss_pct": 8.0, "max_hold_days": 30, "take_profit_pct": None},
        "backtest_defaults": {
            "start": "2025-01-01", "end": "2025-12-31", "initial_cash": 1000000,
            "position_pct": 20, "max_positions": 5, "fee_bps": 2.5, "stamp_tax_bps": 5.0,
        },
    })


def make_bars(n, base=10.0, drift=0.05, vol=100.0, last_vol=None):
    bars = []
    for i in range(n):
        close = base + i * drift
        v = last_vol if (last_vol is not None and i == n - 1) else vol
        bars.append({
            "date_ms": 1_760_000_000_000 + i * 86_400_000,
            "open": close - 0.1, "high": close + 0.2, "low": close - 0.3, "close": close,
            "volume": float(v), "turnover": close * v,
        })
    return bars


class FakeData:
    def __init__(self, patterns):
        self.patterns = patterns  # code -> bars list or Exception
        self.progress_log = []

    def resolve_universe(self, u):
        codes = list(self.patterns.keys())
        return codes, {c: f"股{c[0]}" for c in codes}, "测试池"

    def kind_for(self, code):
        return "stock"

    def get_bars(self, code, kind="stock", end_ms=None, count=250):
        p = self.patterns[code]
        if isinstance(p, Exception):
            raise p
        bars = p
        if end_ms is not None:
            bars = [b for b in bars if b["date_ms"] <= end_ms]
        return bars[-count:]


def test_screen_matches_and_signals():
    # AAA: rising trend + last-day volume surge → match; BBB: flat (below ma20) → no; CCC: surge but below ma20 → no
    data = FakeData({
        "600001.SH": make_bars(60, drift=0.2, last_vol=500),
        "600002.SH": make_bars(60, drift=0.0),
        "600003.SH": make_bars(60, base=20, drift=-0.2, last_vol=500),
    })
    result = screen(simple_cfg(), data, progress_cb=lambda d, t, c: data.progress_log.append((d, t, c)))
    assert result["matched_count"] == 1
    m = result["matched"][0]
    assert m["thscode"] == "600001.SH"
    assert m["signals"] == ["站上20日线", "量比≥1.2"]
    assert m["snapshot"]["close"] > 0 and m["snapshot"]["ma20"] > 0 and m["snapshot"]["vr"] >= 1.2
    assert result["evaluated"] == 3 and result["failed"] == 0
    assert result["universe"]["name"] == "测试池"
    assert result["as_of"] is not None
    assert len(data.progress_log) == 3


def test_screen_failure_resilience():
    data = FakeData({
        "600001.SH": make_bars(60, drift=0.2, last_vol=500),
        "600002.SH": make_bars(10),  # too few bars → skipped (not failed)
        "600003.SH": RuntimeError("上游炸了"),
    })
    result = screen(simple_cfg(), data)
    assert result["matched_count"] == 1
    assert result["failed"] == 1
    assert result["evaluated"] == 2  # total 3 - failed 1


def test_screen_as_of_slices_history():
    rising = make_bars(60, drift=0.2, last_vol=500)
    data = FakeData({"600001.SH": rising})
    bars = rising
    as_of_ms = bars[29]["date_ms"]
    from backend.screener import parse_as_of
    from datetime import datetime
    from backend.fuyao import CST
    d = datetime.fromtimestamp(as_of_ms / 1000, tz=CST)
    as_of_str = d.strftime("%Y-%m-%d")
    result = screen(simple_cfg(), data, as_of=as_of_str)
    # early cutoff: only 30 bars, still >= MIN_BARS; trend exists; last bar volume is normal → no match
    assert result["matched_count"] == 0
    assert result["as_of"] == as_of_str


def test_screen_reference_strategy_runs():
    cfg = StrategyConfig.model_validate(copy.deepcopy(REFERENCE_STRATEGY))
    cfg = cfg.model_copy(update={"universe": type(cfg.universe).model_validate({"type": "custom", "codes": ["600001.SH"]})})
    # Build synthetic bars that could satisfy: decline, convergence, volume surge, recovery
    bars = []
    price = 100.0
    for i in range(150):
        if i < 80:
            price *= 0.998  # slow decline
        elif i < 100:
            price *= 1.0  # consolidation (convergence)
        elif i == 100:
            price *= 1.02
        else:
            price *= 1.004
        vol = 100.0
        if 95 <= i <= 105:
            vol = 250.0
        if i >= 140:
            vol = 90.0
        bars.append({
            "date_ms": 1_760_000_000_000 + i * 86_400_000,
            "open": price * 0.999, "high": price * 1.005, "low": price * 0.994, "close": price,
            "volume": vol, "turnover": price * vol,
        })
    data = FakeData({"600001.SH": bars})
    result = screen(cfg, data)
    # exact match depends on strict thresholds; the invariant is it runs and reports consistently
    assert result["matched_count"] in (0, 1)
    assert result["evaluated"] == 1


def test_parse_as_of():
    assert parse_as_of(None) is None
    assert parse_as_of("") is None
    ms = parse_as_of("2026-06-30")
    assert ms % 86_400_000 == 16 * 3600 * 1000  # CST midnight == UTC 16:00 previous day
    with pytest.raises(ValueError):
        parse_as_of("2026/06/30")
