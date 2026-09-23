"""Tests for kline resampling and volume analysis."""
import pytest

from backend.kline import (
    add_volume_analysis,
    build_kline_response,
    classify_vol,
    resample,
    volume_summary,
)

BASE = 1_760_000_000_000  # a fixed epoch ms aligned to a past date


def mk_daily(n, start_ms=BASE, step=DAY if False else 86_400_000, base_price=10.0, vols=None):
    bars = []
    for i in range(n):
        p = base_price + i * 0.1
        bars.append({
            "date_ms": start_ms + i * step,
            "open": p,
            "high": p + 1,
            "low": p - 1,
            "close": p + 0.5,
            "volume": float(vols[i]) if vols else 100.0,
            "turnover": (p + 0.5) * (vols[i] if vols else 100),
        })
    return bars


def test_resample_1d_passthrough_with_date():
    bars = resample(mk_daily(3), "1d")
    assert len(bars) == 3
    assert bars[0]["date_ms"] == BASE
    assert bars[0]["open"] == 10.0 and bars[0]["close"] == 10.5
    assert "date" in bars[0]


def test_resample_5d_chunks():
    bars = mk_daily(12)
    out = resample(bars, "5d")
    assert len(out) == 3  # 5+5+2
    assert out[0]["open"] == bars[0]["open"]
    assert out[0]["close"] == bars[4]["close"]
    assert out[0]["high"] == max(b["high"] for b in bars[0:5])
    assert out[0]["low"] == min(b["low"] for b in bars[0:5])
    assert out[0]["volume"] == 500.0
    assert out[0]["date_ms"] == bars[4]["date_ms"]
    assert out[2]["volume"] == 200.0  # partial chunk of 2


def test_resample_week_groups():
    from backend.kline import date_str

    days = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-09", "2026-01-12"]
    from datetime import datetime, timezone
    from backend.fuyao import CST

    ms = [int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp() * 1000) + 8 * 3600 * 1000 - 3600 * 1000 * 0 for d in days]
    bars = mk_daily(len(days), start_ms=0)  # placeholder, then override dates
    for b, m in zip(bars, ms):
        b["date_ms"] = m
    out = resample(bars, "1w")
    # 2026-01-01(Thu),02(Fri) week 1; 05(Mon)..09(Fri) week 2; 12(Mon) week 3
    assert len(out) == 3
    assert date_str(out[0]["date_ms"]) == "2026-01-02"
    assert date_str(out[1]["date_ms"]) == "2026-01-09"
    assert date_str(out[2]["date_ms"]) == "2026-01-12"


def test_resample_month_and_year():
    from datetime import datetime, timezone
    from backend.fuyao import CST

    days = ["2025-12-30", "2025-12-31", "2026-01-02", "2026-01-15", "2026-02-03"]
    ms = [int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp() * 1000) + 16 * 3600 * 1000 for d in days]
    bars = mk_daily(len(days), start_ms=0)
    for b, m in zip(bars, ms):
        b["date_ms"] = m
    monthly = resample(bars, "1M")
    assert len(monthly) == 3
    yearly = resample(bars, "1y")
    assert len(yearly) == 2


def test_resample_invalid_period():
    with pytest.raises(ValueError):
        resample(mk_daily(3), "1h")


def test_classify_vol_boundaries():
    assert classify_vol(2.0) == "surge"
    assert classify_vol(1.99) == "incremental"
    assert classify_vol(1.5) == "incremental"
    assert classify_vol(0.7) == "flat"
    assert classify_vol(0.69) == "shrink"
    assert classify_vol(None) is None


def test_add_volume_analysis_vratio():
    bars = mk_daily(6, vols=[100, 100, 100, 100, 100, 300])
    add_volume_analysis(bars)
    assert bars[0]["vratio"] is None and bars[0]["vol_state"] is None
    assert bars[4]["vratio"] is None  # 量比分母=前5根均量（不含当日），i<5 无值
    assert bars[5]["vratio"] == pytest.approx(300 / 100)
    assert bars[5]["vol_state"] == "surge"


def test_volume_summary_incremental():
    bars = mk_daily(12, vols=[100] * 10 + [300, 280])
    for i, b in enumerate(bars):
        b["close"] = 10 + i
    add_volume_analysis(bars)
    s = volume_summary(bars)
    assert s["trend"] == "增量放量"
    assert s["latest_vol_state"] == "surge"
    assert "量比" in s["note"] or "量能" in s["note"]


def test_volume_summary_shrink():
    bars = mk_daily(8, vols=[100, 100, 100, 100, 50, 40, 30, 20])
    add_volume_analysis(bars)
    s = volume_summary(bars)
    assert s["trend"] == "持续缩量"


def test_volume_summary_empty():
    s = volume_summary([])
    assert s["trend"] == "量能平稳"


def test_build_kline_response_shape():
    bars = mk_daily(70, vols=[100 + (i % 7) * 40 for i in range(70)], base_price=10.0)
    resp = build_kline_response("600519.SH", "贵州茅台", "a-share", "1d", bars)
    assert resp["thscode"] == "600519.SH" and resp["name"] == "贵州茅台"
    assert resp["period"] == "1d"
    assert len(resp["bars"]) == 70
    b = resp["bars"][-1]
    for k in ("date_ms", "date", "open", "high", "low", "close", "volume", "turnover", "vratio", "vol_state", "ma5", "ma10", "ma20", "ma60"):
        assert k in b
    assert resp["bars"][0]["ma60"] is None
    assert resp["bars"][-1]["ma60"] is not None
    assert resp["change_pct"] is not None
    assert set(resp["volume_summary"]) == {"latest_vratio", "latest_vol_state", "trend", "note"}

    weekly = build_kline_response("600519.SH", None, "a-share", "1w", bars)
    assert len(weekly["bars"]) < 70
    assert weekly["name"] == "600519.SH"
