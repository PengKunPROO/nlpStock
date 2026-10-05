"""Tests for the range-scan screening engine with a fake DataService."""
from datetime import datetime

import pytest

from backend.fuyao import CST
from backend.schema import ScreeningStrategy
from backend.screener import parse_as_of, screen_range

BASE_MS = parse_as_of("2025-01-01")  # CST 午夜，保证 date_ms ↔ yyyy-MM-dd 精确往返


def dstr(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=CST).strftime("%Y-%m-%d")


def simple_strategy():
    """Simple crossover strategy: close > ma20 and vr >= 1.2 (any day in range)."""
    return ScreeningStrategy.parse_obj({
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
    })


def make_bars(n, base=10.0, drift=0.05, vol=100.0, spike=None):
    """spike: {index -> volume}，在指定交易日放量（触发 vr>=1.2）。"""
    bars = []
    for i in range(n):
        close = base + i * drift
        v = spike.get(i, vol) if spike else vol
        bars.append({
            "date_ms": BASE_MS + i * 86_400_000,
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

    def get_bars_range(self, code, kind, start_ms, end_ms, warmup_bars=250):
        p = self.patterns[code]
        if isinstance(p, Exception):
            raise p
        return p  # fake：返回全部 K 线（含 warmup，无需按日期裁剪）


def test_screen_range_matches_in_range():
    # 上升趋势 + 第40天放量 → entry 在区间内第40天成立 → 入选且 signal_date 正确
    bars = make_bars(60, drift=0.2, spike={40: 500.0})
    data = FakeData({"600001.SH": bars})
    start, end = dstr(bars[20]["date_ms"]), dstr(bars[59]["date_ms"])
    result = screen_range(simple_strategy(), data, start, end)
    assert result["matched_count"] == 1
    m = result["matched"][0]
    assert m["thscode"] == "600001.SH"
    assert m["signal_date"] == dstr(bars[40]["date_ms"])
    assert m["signals"] == ["站上20日线", "量比≥1.2"]
    assert m["snapshot"]["close"] > 0 and m["snapshot"]["ma20"] > 0 and m["snapshot"]["vr"] >= 1.2
    assert result["as_of"] == end
    assert result["evaluated"] == 1 and result["failed"] == 0
    assert result["universe"]["name"] == "测试池"


def test_screen_range_outside_range_not_matched():
    # 放量日（第40天）落在 [start,end] 之外 → 区间内 entry 永不成立 → 不入选
    bars = make_bars(60, drift=0.2, spike={40: 500.0})
    data = FakeData({"600001.SH": bars})
    start, end = dstr(bars[45]["date_ms"]), dstr(bars[59]["date_ms"])
    result = screen_range(simple_strategy(), data, start, end)
    assert result["matched_count"] == 0
    assert result["evaluated"] == 1 and result["failed"] == 0


def test_screen_range_first_signal_dedup():
    # 多天满足（第30、50天均放量）→ 只入选一次，signal_date 为最早命中日
    bars = make_bars(60, drift=0.2, spike={30: 500.0, 50: 500.0})
    data = FakeData({"600001.SH": bars})
    start, end = dstr(bars[20]["date_ms"]), dstr(bars[59]["date_ms"])
    result = screen_range(simple_strategy(), data, start, end)
    assert result["matched_count"] == 1
    assert result["matched"][0]["signal_date"] == dstr(bars[30]["date_ms"])


def test_screen_range_progress_cb():
    data = FakeData({
        "600001.SH": make_bars(60, drift=0.2, spike={40: 500.0}),
        "600002.SH": make_bars(60, drift=0.0),
        "600003.SH": make_bars(60, base=20, drift=-0.2, spike={40: 500.0}),
    })
    start, end = dstr(BASE_MS + 20 * 86_400_000), dstr(BASE_MS + 59 * 86_400_000)
    result = screen_range(simple_strategy(), data, start, end,
                          progress_cb=lambda d, t, c: data.progress_log.append((d, t, c)))
    assert len(data.progress_log) == 3
    assert result["matched_count"] == 1  # 仅 600001 上升且放量


def test_screen_range_failure_resilience():
    data = FakeData({
        "600001.SH": make_bars(60, drift=0.2, spike={40: 500.0}),
        "600002.SH": make_bars(10),  # too few bars → skipped (not failed)
        "600003.SH": RuntimeError("上游炸了"),
    })
    start, end = dstr(BASE_MS + 20 * 86_400_000), dstr(BASE_MS + 59 * 86_400_000)
    result = screen_range(simple_strategy(), data, start, end)
    assert result["matched_count"] == 1
    assert result["failed"] == 1
    assert result["evaluated"] == 2  # total 3 - failed 1


def test_parse_as_of():
    assert parse_as_of(None) is None
    assert parse_as_of("") is None
    ms = parse_as_of("2026-06-30")
    assert ms % 86_400_000 == 16 * 3600 * 1000  # CST midnight == UTC 16:00 previous day
    with pytest.raises(ValueError):
        parse_as_of("2026/06/30")
