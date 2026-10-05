"""Stock screening engine: evaluate strategy entry conditions across a universe."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .conditions import eval_condition
from .data_service import DataService
from .fuyao import CST
from .indicators import compute_indicators
from .schema import ConditionGroup, LeafCondition, StrategyConfig

MIN_BARS = 30


def parse_as_of(as_of: str | None) -> int | None:
    if not as_of:
        return None
    d = datetime.strptime(as_of, "%Y-%m-%d")
    return int(datetime(d.year, d.month, d.day, tzinfo=CST).timestamp() * 1000)


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


def _referenced_ids(cfg: StrategyConfig) -> list[str]:
    ids = set()

    def walk(conds):
        for c in conds:
            if isinstance(c, ConditionGroup):
                walk(c.conditions)
            else:
                if c.left in cfg_ids:
                    ids.add(c.left)
                if isinstance(c.right, str) and c.right in cfg_ids:
                    ids.add(c.right)

    cfg_ids = {ind.id for ind in cfg.indicators}
    walk(cfg.entry.conditions)
    walk(cfg.exit.conditions)
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


def screen(
    cfg: StrategyConfig,
    data: DataService,
    as_of: str | None = None,
    count: int = 250,
    max_workers: int = 6,
    progress_cb=None,
) -> dict:
    started = time.monotonic()
    end_ms = parse_as_of(as_of)
    codes, names, label = data.resolve_universe(cfg.universe.dict())
    specs = cfg.indicators
    ref_ids = _referenced_ids(cfg)

    # 大盘基准（沪深300）：信号追踪对照
    benchmark_closes: list[tuple[int, float]] = []
    try:
        bench_bars = data.get_bars("000300.SH", kind="index", count=count + 60)
        benchmark_closes = [(b["date_ms"], b["close"]) for b in bench_bars]
    except Exception:
        pass

    def _benchmark_forward(sig_ms: int, n_days: int) -> float | None:
        """基准从信号日 → n_days 个交易日后的涨跌%。"""
        if not benchmark_closes:
            return None
        # 找信号日在基准中的位置
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

    matched: list[dict] = []
    state = {"done": 0, "failed": 0, "last_date": 0}

    def work(code: str):
        kind = data.kind_for(code)
        # as_of 为历史日期时取到最新（含信号日之后数据用于信号追踪）
        bars = data.get_bars(code, kind=kind, count=count + 60)
        if len(bars) < MIN_BARS:
            return None
        if as_of:
            i = _find_as_of_index(bars, end_ms)
            if i is None or i < 30:
                return None
        else:
            i = len(bars) - 1
        series = compute_indicators(bars[: i + 1], specs)
        notes: list[str] = []
        if not _collect_matched(cfg.entry, series, i, notes):
            return None
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
            "last_close": bars[i]["close"],
            "change_pct": change_pct,
            "signals": notes,
            "snapshot": snapshot,
            "last_date_ms": sig_ms,
            "chg_5d": _forward_return(bars, i, 5),
            "chg_20d": _forward_return(bars, i, 20),
            "bench_5d": _benchmark_forward(sig_ms, 5),
            "bench_20d": _benchmark_forward(sig_ms, 20),
        }

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
                state["last_date"] = max(state["last_date"], res["last_date_ms"])
            state["done"] += 1
            if progress_cb:
                progress_cb(state["done"], total, code)

    matched.sort(key=lambda m: (m["change_pct"] is None, -(m["change_pct"] or 0)))
    as_of_out = as_of or (
        datetime.fromtimestamp(state["last_date"] / 1000, tz=CST).strftime("%Y-%m-%d") if state["last_date"] else None
    )
    return {
        "as_of": as_of_out,
        "evaluated": total - state["failed"],
        "failed": state["failed"],
        "matched_count": len(matched),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "universe": {"type": cfg.universe.type, "code": cfg.universe.code, "name": label},
        "matched": matched,
    }
