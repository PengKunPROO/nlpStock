"""Stock screening engine: range scan — first day a strategy's entry fires inside [start, end]."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .conditions import eval_condition, signal_at
from .data_service import DataService
from .fuyao import CST
from .indicators import compute_indicators
from .schema import HOLD_FIELDS, ConditionGroup, LeafCondition, ScreeningStrategy

MIN_BARS = 30


def _refs_hold(group: ConditionGroup) -> bool:
    """条件组是否引用持仓状态字段（cost/pnl_pct/hold_days/dd_from_peak）。"""
    for c in group.conditions:
        if isinstance(c, ConditionGroup):
            if _refs_hold(c):
                return True
        else:
            if c.left in HOLD_FIELDS:
                return True
            if isinstance(c.right, str) and c.right in HOLD_FIELDS:
                return True
    return False


def parse_as_of(as_of: str | None) -> int | None:
    if not as_of:
        return None
    d = datetime.strptime(as_of, "%Y-%m-%d")
    return int(datetime(d.year, d.month, d.day, tzinfo=CST).timestamp() * 1000)


def _date_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=CST).strftime("%Y-%m-%d")


def _collect_matched(group: ConditionGroup, series: dict, i: int, out: list[str]) -> bool:
    ok = eval_condition(group, series, i)
    if not ok:
        return False
    for c in group.conditions:
        if isinstance(c, ConditionGroup):
            if eval_condition(c, series, i):
                out.append(c.note or "组合条件满足")
        else:
            if eval_condition(c, series, i):
                out.append(c.note or f"{c.left} {c.op} {c.right}")
    return True


def _referenced_ids(strategy: ScreeningStrategy) -> list[str]:
    ids: set[str] = set()

    def walk(conds):
        for c in conds:
            if isinstance(c, ConditionGroup):
                walk(c.conditions)
            else:
                if c.left in cfg_ids:
                    ids.add(c.left)
                if isinstance(c.right, str) and c.right in cfg_ids:
                    ids.add(c.right)

    cfg_ids = {ind.id for ind in strategy.indicators}
    walk(strategy.entry.conditions)
    return sorted(ids)


def _find_as_of_index(bars: list[dict], end_ms: int) -> int | None:
    """找到 ≤ end_ms 的最后一根K线索引（二分）。"""
    lo, hi = 0, len(bars) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if bars[mid]["date_ms"] <= end_ms:
            lo = mid + 1
        else:
            hi = mid - 1
    return hi if hi >= 0 else None


def _forward_return(bars: list[dict], sig_idx: int, n_days: int) -> float | None:
    """信号日 → n_days 个交易日后的涨跌%（数据不足返回 None）。"""
    tgt = sig_idx + n_days
    if tgt >= len(bars):
        return None
    base = bars[sig_idx]["close"]
    if not base:
        return None
    return round((bars[tgt]["close"] / base - 1) * 100, 2)


def _benchmark_forward(benchmark_closes: list[tuple[int, float]], sig_ms: int, n_days: int) -> float | None:
    """基准从信号日 → n_days 个交易日后的涨跌%。"""
    if not benchmark_closes:
        return None
    idx = None
    for j, (ms, _) in enumerate(benchmark_closes):
        if ms == sig_ms:
            idx = j
            break
        if ms > sig_ms:
            idx = j - 1 if j > 0 else None
            break
    if idx is None or idx + n_days >= len(benchmark_closes):
        return None
    base = benchmark_closes[idx][1]
    if not base:
        return None
    return round((benchmark_closes[idx + n_days][1] / base - 1) * 100, 2)


def screen_range(
    strategy: ScreeningStrategy,
    data: DataService,
    start: str,
    end: str,
    count: int = 250,
    max_workers: int = 6,
    progress_cb=None,
) -> dict:
    """区间扫描选股：在 [start, end] 内首次满足 entry 即入选（去重，记录 signal_date）。"""
    started = time.monotonic()
    start_ms = parse_as_of(start)
    end_ms = parse_as_of(end) + 86_400_000 - 1  # inclusive end of day
    codes, names, label = data.resolve_universe(strategy.universe.dict())
    specs = strategy.indicators
    entry = strategy.entry
    ref_ids = _referenced_ids(strategy)

    matched: list[dict] = []
    state = {"done": 0, "failed": 0}

    def work(code: str):
        kind = data.kind_for(code)
        bars = data.get_bars_range(code, kind, start_ms, end_ms, warmup_bars=count)
        if len(bars) < MIN_BARS:
            return None
        series = compute_indicators(bars, specs)
        hi = _find_as_of_index(bars, end_ms)
        if hi is None:
            return None
        lo = 0
        while lo < len(bars) and bars[lo]["date_ms"] < start_ms:
            lo += 1
        if lo > hi:
            return None
        for i in range(lo, hi + 1):
            if not signal_at(entry, series, i):
                continue
            notes: list[str] = []
            _collect_matched(entry, series, i, notes)
            snapshot = {"close": round(bars[i]["close"], 4)}
            for rid in ref_ids:
                v = series[rid][i]
                snapshot[rid] = round(v, 4) if v is not None else None
            change_pct = None
            if i > 0 and bars[i - 1]["close"]:
                change_pct = round((bars[i]["close"] / bars[i - 1]["close"] - 1) * 100, 3)
            sig_ms = bars[i]["date_ms"]
            return {
                "thscode": code,
                "name": names.get(code) or code,
                "signal_date": _date_str(sig_ms),
                "last_close": bars[i]["close"],
                "change_pct": change_pct,
                "signals": notes,
                "snapshot": snapshot,
                "last_date_ms": sig_ms,
            }
        return None

    total = len(codes)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(work, code): code for code in codes}
        for fut in as_completed(futures):
            code = futures[fut]
            res = None
            try:
                res = fut.result()
            except Exception:
                state["failed"] += 1
            if res is not None:
                matched.append(res)
            state["done"] += 1
            if progress_cb:
                progress_cb(state["done"], total, code)

    matched.sort(key=lambda m: (m["signal_date"], m["thscode"]))
    return {
        "as_of": end,
        "evaluated": total - state["failed"],
        "failed": state["failed"],
        "matched_count": len(matched),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "universe": {"type": strategy.universe.type, "code": strategy.universe.code, "name": label},
        "matched": matched,
    }
