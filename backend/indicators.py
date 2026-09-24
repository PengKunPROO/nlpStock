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


def _ema(values: list[float], n: int) -> list[float | None]:
    """指数均线：种子 = SMA(n)，之后 α=2/(n+1) 递推；前 n-1 个为 None。"""
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    ema = sum(values[:n]) / n
    out[n - 1] = ema
    alpha = 2.0 / (n + 1)
    for i in range(n, len(values)):
        ema = alpha * values[i] + (1 - alpha) * ema
        out[i] = ema
    return out


def _macd(values: list[float], fast: int, slow: int, signal: int):
    """A 股口径 MACD：DIF=EMA(fast)-EMA(slow)，DEA=EMA(DIF,signal)，柱=2×(DIF-DEA)。"""
    ema_fast = _ema(values, fast)
    ema_slow = _ema(values, slow)
    dif: list[float | None] = [
        f - s if f is not None and s is not None else None for f, s in zip(ema_fast, ema_slow)
    ]
    valid = [d for d in dif if d is not None]
    dea_valid = _ema(valid, signal)
    dea: list[float | None] = [None] * (len(dif) - len(valid)) + dea_valid
    hist: list[float | None] = [
        2.0 * (d - e) if d is not None and e is not None else None for d, e in zip(dif, dea)
    ]
    return dif, dea, hist


def _kdj(bars: list[dict], n: int, m1: int, m2: int):
    """通达信口径 KDJ：RSV=(C-LLV)/(HHV-LLV)×100，K/D 指数平滑初值 50，J=3K-2D。"""
    count = len(bars)
    k_out: list[float | None] = [None] * count
    d_out: list[float | None] = [None] * count
    j_out: list[float | None] = [None] * count
    prev_k, prev_d = 50.0, 50.0
    for i in range(n - 1, count):
        window = bars[i - n + 1 : i + 1]
        hh = max(b["high"] for b in window)
        ll = min(b["low"] for b in window)
        rsv = 50.0 if hh == ll else (bars[i]["close"] - ll) / (hh - ll) * 100.0
        k = (rsv + (m1 - 1) * prev_k) / m1
        d = (k + (m2 - 1) * prev_d) / m2
        k_out[i], d_out[i], j_out[i] = k, d, 3 * k - 2 * d
        prev_k, prev_d = k, d
    return k_out, d_out, j_out


def _rsi(values: list[float], n: int) -> list[float | None]:
    """Wilder 平滑 RSI：up/down 各 α=1/n，种子 = 前 n 个涨跌幅平均。"""
    count = len(values)
    out: list[float | None] = [None] * count
    if count <= n:
        return out
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, count)]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, count)]
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n

    def _to_rsi(g: float, l: float) -> float:
        return 50.0 if g + l == 0 else g / (g + l) * 100.0

    out[n] = _to_rsi(avg_gain, avg_loss)
    for i in range(n + 1, count):
        avg_gain = (gains[i - 1] + (n - 1) * avg_gain) / n
        avg_loss = (losses[i - 1] + (n - 1) * avg_loss) / n
        out[i] = _to_rsi(avg_gain, avg_loss)
    return out


def _boll(values: list[float], n: int, k: float):
    """布林带：MID=MA(n)，UP/LOW=MID±k×STD（样本标准差 N-1）。"""
    count = len(values)
    up: list[float | None] = [None] * count
    mid: list[float | None] = [None] * count
    low: list[float | None] = [None] * count
    for i in range(n - 1, count):
        window = values[i - n + 1 : i + 1]
        m = sum(window) / n
        std = (sum((x - m) ** 2 for x in window) / (n - 1)) ** 0.5
        mid[i], up[i], low[i] = m, m + k * std, m - k * std
    return up, mid, low


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
        elif spec.kind == "EMA":
            series[spec.id] = _ema(series[spec.of], spec.n)
        elif spec.kind in ("MACD_DIF", "MACD_DEA", "MACD_HIST"):
            dif, dea, hist = _macd(series[spec.of or "close"], spec.fast or 12, spec.slow or 26, spec.signal or 9)
            series[spec.id] = {"MACD_DIF": dif, "MACD_DEA": dea, "MACD_HIST": hist}[spec.kind]
        elif spec.kind in ("KDJ_K", "KDJ_D", "KDJ_J"):
            k_out, d_out, j_out = _kdj(bars, spec.n or 9, spec.m1 or 3, spec.m2 or 3)
            series[spec.id] = {"KDJ_K": k_out, "KDJ_D": d_out, "KDJ_J": j_out}[spec.kind]
        elif spec.kind == "RSI":
            series[spec.id] = _rsi(series[spec.of or "close"], spec.n or 6)
        elif spec.kind in ("BOLL_UP", "BOLL_MID", "BOLL_LOW"):
            up, mid, low = _boll(series[spec.of or "close"], spec.n or 20, spec.k or 2.0)
            series[spec.id] = {"BOLL_UP": up, "BOLL_MID": mid, "BOLL_LOW": low}[spec.kind]
    for spec in specs:
        if spec.id not in series:
            raise ValueError(f"indicator not computed: {spec.id}")
    return series
