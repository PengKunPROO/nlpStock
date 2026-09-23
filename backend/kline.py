"""K-line period resampling (1d/5d/1w/1M/1y) and volume analysis (量比/放量状态/摘要)."""
from __future__ import annotations

from datetime import datetime

from .fuyao import CST

DAY_MS = 86_400_000
PERIODS = ("1d", "5d", "1w", "1M", "1y")
_VOL_STATES = ("surge", "incremental", "flat", "shrink")


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=CST)


def date_str(ms: int) -> str:
    return _dt(ms).strftime("%Y-%m-%d")


def _group_key(ms: int, period: str):
    d = _dt(ms)
    if period == "1w":
        iso = d.isocalendar()
        return (iso[0], iso[1])
    if period == "1M":
        return (d.year, d.month)
    if period == "1y":
        return (d.year,)
    raise ValueError(f"period {period} has no calendar key")


def resample(daily_bars: list[dict], period: str) -> list[dict]:
    if period not in PERIODS:
        raise ValueError(f"unsupported period: {period}")
    if period == "1d":
        out = []
        for b in daily_bars:
            bar = dict(b)
            bar["date"] = date_str(b["date_ms"])
            out.append(bar)
        return out
    if period == "5d":
        groups: list[list[dict]] = []
        for i, b in enumerate(daily_bars):
            if i % 5 == 0:
                groups.append([])
            groups[-1].append(b)
    else:
        groups = []
        last_key = None
        for b in daily_bars:
            key = _group_key(b["date_ms"], period)
            if key != last_key:
                groups.append([])
                last_key = key
            groups[-1].append(b)
    out = []
    for g in groups:
        out.append({
            "date_ms": g[-1]["date_ms"],
            "date": date_str(g[-1]["date_ms"]),
            "open": g[0]["open"],
            "high": max(b["high"] for b in g),
            "low": min(b["low"] for b in g),
            "close": g[-1]["close"],
            "volume": sum(b["volume"] for b in g),
            "turnover": sum(b.get("turnover") or 0 for b in g),
        })
    return out


def classify_vol(vratio: float | None) -> str | None:
    if vratio is None:
        return None
    if vratio >= 2.0:
        return "surge"
    if vratio >= 1.5:
        return "incremental"
    if vratio >= 0.7:
        return "flat"
    return "shrink"


def _sma_series(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def add_volume_analysis(bars: list[dict]) -> None:
    """In-place: vratio + vol_state per bar (量比 = vol / 前5根同周期均量，不含当日)."""
    vols = [b["volume"] for b in bars]
    for i, b in enumerate(bars):
        if i < 5:
            b["vratio"] = None
            b["vol_state"] = None
            continue
        base = sum(vols[i - 5 : i]) / 5
        vr = vols[i] / base if base else None
        b["vratio"] = round(vr, 3) if vr is not None else None
        b["vol_state"] = classify_vol(vr)


def volume_summary(bars: list[dict]) -> dict:
    if not bars:
        return {"latest_vratio": None, "latest_vol_state": None, "trend": "量能平稳", "note": "暂无数据"}
    window = [b for b in bars[-5:] if b.get("vratio") is not None]
    latest = bars[-1]
    if not window:
        return {
            "latest_vratio": None,
            "latest_vol_state": None,
            "trend": "量能平稳",
            "note": "历史数据不足，暂无法判断量能趋势",
        }
    hot = [b for b in window if b["vratio"] >= 1.5]
    all_shrink = all(b["vratio"] < 0.7 for b in window)
    spread = max(b["vratio"] for b in window) - min(b["vratio"] for b in window)
    closes = [b["close"] for b in bars[-5:]]
    price_up_pct = (closes[-1] / closes[0] - 1) * 100 if closes[0] else 0.0
    if len(hot) >= 2 and price_up_pct > 0:
        trend = "增量放量"
        note = f"近{len(hot)}个周期量比≥1.5，区间价格{'上涨' if price_up_pct > 0 else '下跌'}{abs(price_up_pct):.1f}%"
    elif all_shrink:
        trend = "持续缩量"
        note = "近5个周期量比均低于0.7，市场观望情绪明显"
    elif spread >= 1.0:
        trend = "量能波动"
        note = f"近5个周期量比在{min(b['vratio'] for b in window):.1f}~{max(b['vratio'] for b in window):.1f}间大幅波动"
    else:
        trend = "量能平稳"
        note = "近5个周期量能保持平稳"
    return {
        "latest_vratio": latest.get("vratio"),
        "latest_vol_state": latest.get("vol_state"),
        "trend": trend,
        "note": note,
    }


def build_kline_response(
    thscode: str,
    name: str | None,
    asset_type: str,
    period: str,
    daily_bars: list[dict],
) -> dict:
    bars = resample(daily_bars, period)
    add_volume_analysis(bars)
    closes = [b["close"] for b in bars]
    for n in (5, 10, 20, 60):
        ma = _sma_series(closes, n)
        for i, b in enumerate(bars):
            b[f"ma{n}"] = round(ma[i], 3) if ma[i] is not None else None
    last = bars[-1] if bars else None
    change_pct = None
    if len(bars) >= 2:
        prev = bars[-2]["close"]
        if prev:
            change_pct = round((bars[-1]["close"] / prev - 1) * 100, 3)
    return {
        "thscode": thscode,
        "name": name or thscode,
        "asset_type": asset_type,
        "period": period,
        "last_close": last["close"] if last else None,
        "change_pct": change_pct,
        "bars": bars,
        "volume_summary": volume_summary(bars),
    }
