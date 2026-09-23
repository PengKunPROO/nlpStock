"""Pure-python indicator engine: aligned series computed from daily bars per IndicatorSpec."""
from __future__ import annotations

from .schema import IndicatorSpec

BASES = ("open", "high", "low", "close", "volume")


def _ma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def _pct_change(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(n, len(values)):
        base = values[i - n]
        if base:
            out[i] = (values[i] / base - 1.0) * 100.0
    return out


def _body_ratio(bars: list[dict]) -> list[float]:
    out = []
    for b in bars:
        rng = b["high"] - b["low"]
        out.append((b["close"] - b["open"]) / rng if rng > 0 else 0.0)
    return out


def _upper_shadow_ratio(bars: list[dict]) -> list[float]:
    out = []
    for b in bars:
        rng = b["high"] - b["low"]
        up = b["high"] - max(b["open"], b["close"])
        out.append(up / rng if rng > 0 else 0.0)
    return out


def _box_top(highs: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(highs)
    for i in range(n, len(highs)):
        out[i] = max(highs[i - n : i])
    return out


def compute_indicators(bars: list[dict], specs: list[IndicatorSpec]) -> dict[str, list[float | None]]:
    """Compute all declared indicators; returns series map including base field arrays."""
    series: dict[str, list[float | None]] = {f: [b[f] for b in bars] for f in BASES}
    mas_first = [s for s in specs if s.kind == "MA"]
    rest = [s for s in specs if s.kind != "MA"]
    for spec in mas_first + rest:
        if spec.kind == "MA":
            series[spec.id] = _ma(series[spec.of], spec.n)
        elif spec.kind == "PCT_CHANGE":
            series[spec.id] = _pct_change(series[spec.of], spec.n)
        elif spec.kind == "VRATIO":
            out: list[float | None] = [None] * len(bars)
            vols = series["volume"]
            for i in range(spec.n, len(vols)):
                base = sum(vols[i - spec.n : i]) / spec.n
                out[i] = vols[i] / base if base else None
            series[spec.id] = out
        elif spec.kind == "BODY_RATIO":
            series[spec.id] = _body_ratio(bars)
        elif spec.kind == "UPPER_SHADOW_RATIO":
            series[spec.id] = _upper_shadow_ratio(bars)
        elif spec.kind == "BOX_TOP":
            series[spec.id] = _box_top(series["high"], spec.n)
        elif spec.kind == "MA_CONVERGE":
            closes = series["close"]
            cols = [series[ref] for ref in spec.mas]
            conv: list[float | None] = []
            for i in range(len(bars)):
                vals = [c[i] for c in cols]
                if any(v is None for v in vals) or not closes[i]:
                    conv.append(None)
                else:
                    conv.append((max(vals) - min(vals)) / closes[i] * 100.0)
            series[spec.id] = conv
    for spec in specs:
        if spec.id not in series:
            raise ValueError(f"indicator not computed: {spec.id}")
    return series
