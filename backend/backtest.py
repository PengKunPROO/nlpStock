"""Backtest engine: independent per-stock backtest over a fixed pool (no rotation).

Execution semantics:
- Entry is driven by the screening result (pool), not by a rotation rule: buy at open of signal_date+1.
- A-share T+1: risk/signal exits only on dates strictly after the entry date.
- Stop-loss/take-profit/trailing-stop fill intraday at the trigger price (gap → open price).
- Fees: commission both sides (fee_bps), stamp tax on sells (stamp_tax_bps).
- end_of_data closes are valuations, excluded from win-rate statistics.
- No rotation: after a full sell, the stock's trade ends (no re-entry, no cross-stock replacement).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .conditions import eval_group_verbose, signal_at
from .data_service import DataService
from .fuyao import CST
from .indicators import compute_indicators
from .schema import HOLD_FIELDS, ConditionGroup
from .screener import parse_as_of

EXIT_PRIORITY = ("stop_loss", "take_profit", "trailing_stop", "signal", "max_hold")


def _date_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=CST).strftime("%Y-%m-%d")


def _refs_hold(group) -> bool:
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


def _make_evidence(series: dict, sig_idx: int, date_str: str, group, label: str) -> dict:
    """构造信号判定依据快照。"""
    verbose = eval_group_verbose(group, series, sig_idx)
    return {
        "signal_date": date_str,
        "label": label,
        "logic": verbose.get("logic", "all"),
        "conditions": verbose.get("conditions", []),
        "passed": verbose.get("passed", False),
    }


class _Stock:
    __slots__ = ("name", "dates", "date_idx", "open", "high", "low", "close", "last_close", "series")

    def __init__(self, name, bars, series):
        self.name = name
        self.dates = [b["date_ms"] for b in bars]
        self.date_idx = {ms: i for i, ms in enumerate(self.dates)}
        self.open = [b["open"] for b in bars]
        self.high = [b["high"] for b in bars]
        self.low = [b["low"] for b in bars]
        self.close = [b["close"] for b in bars]
        self.last_close = self.close[-1] if self.close else None
        self.series = series


def _hold_ctx(s: _Stock, sig_idx: int, p: dict) -> dict:
    """构造持仓期规则评估上下文：单元素 series，索引 0 = 信号日（含持仓状态字段）。"""
    ctx = {k: [arr[sig_idx]] for k, arr in s.series.items()}
    cost = p["entry_price"]
    ctx["cost"] = [cost]
    ctx["pnl_pct"] = [(s.close[sig_idx] / cost - 1) * 100 if cost else 0.0]
    ctx["hold_days"] = [p["hold_days"]]
    ctx["dd_from_peak"] = [(s.close[sig_idx] / p["peak_close"] - 1) * 100 if p["peak_close"] else 0.0]
    return ctx


def _run_one_stock(
    strategy,
    code: str,
    name: str,
    bars: list[dict],
    series: dict,
    sig_idx: int,
    initial_cash: float,
    position_pct: float,
    fee_rate: float,
    tax_rate: float,
) -> dict | None:
    """单票独立回测：signal_date+1 开盘买入，交易策略管理持仓，卖出即结束（不轮动）。

    返回单票结果（trades/equity_curve/audit/metrics），或 None（信号日无次日无法买入）。
    """
    s = _Stock(name, bars, series)
    n = len(bars)
    entry_idx = sig_idx + 1
    if entry_idx >= n:
        return None

    stop_pct = strategy.risk.stop_loss_pct
    tp_pct = strategy.risk.take_profit_pct
    trail_pct = strategy.risk.trailing_stop_pct
    max_hold = strategy.risk.max_hold_days

    rules = strategy.rules or []
    # TradingStrategy 的 buy 规则均引用持仓字段（补仓），sell 规则卖出 → 全部是持仓期规则
    hold_rules = [(i, r) for i, r in enumerate(rules) if r.action == "sell" or _refs_hold(r.when)]

    cash = initial_cash
    position: dict | None = None
    trades: list[dict] = []
    equity_curve: list[dict] = []
    audit_daily: list[dict] = []
    peak = initial_cash
    max_dd = 0.0

    def market_value() -> float:
        if position is None:
            return 0.0
        return position["shares"] * (s.last_close or position["entry_price"])

    def do_sell(gidx: int, price: float, reason: str, date_ms: int, exit_ev=None, sell_pct: float = 100.0) -> None:
        nonlocal cash, position
        p = position
        shares = p["shares"] if sell_pct >= 100 else int(p["shares"] * sell_pct / 100 / 100) * 100
        if shares <= 0:
            return
        if p["shares"] - shares < 100:  # 剩余不足一手 → 全卖
            shares = p["shares"]
        gross = shares * price
        fee = gross * fee_rate
        tax = gross * tax_rate
        net = gross - fee - tax
        cash += net
        sell_cost = p["cost"] * (shares / p["shares"])
        pnl = net - sell_cost
        trades.append({
            "code": code,
            "name": name,
            "entry_date": p["entry_date"],
            "entry_idx": entry_idx,
            "entry_price": round(p["entry_price"], 4),
            "exit_date": _date_str(date_ms),
            "exit_idx": gidx,
            "exit_price": round(price, 4),
            "shares": shares,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / sell_cost * 100, 4) if sell_cost else 0.0,
            "holding_days": gidx - p["entry_gidx"],
            "exit_reason": reason,
            "entry_evidence": p.get("entry_evidence"),
            "exit_evidence": exit_ev,
        })
        day_actions.append({
            "code": code,
            "name": name,
            "action": "sell",
            "price": round(price, 4),
            "shares": shares,
            "amount": round(gross, 2),
            "fee": round(fee + tax, 2),
            "reason": reason,
            "evidence": exit_ev,
        })
        if shares >= p["shares"]:
            position = None
        else:
            p["shares"] -= shares
            p["cost"] -= sell_cost

    def do_buy(gidx: int, price: float, date_ms: int, size_pct: float, entry_ev, reason: str) -> bool:
        """买入/加仓（size_pct=当前权益%）。返回是否成交。"""
        nonlocal cash, position
        equity_now = cash + market_value()
        shares = int(equity_now * size_pct / 100.0 / price / 100) * 100
        if shares < 100:
            return False
        cost = shares * price
        fee = cost * fee_rate
        while shares >= 100 and cost + fee > cash:
            shares -= 100
            cost = shares * price
            fee = cost * fee_rate
        if shares < 100:
            return False
        cash -= cost + fee
        if position is not None:
            p = position
            p["shares"] += shares
            p["cost"] += cost + fee
            p["entry_price"] = p["cost"] / p["shares"]  # 加权成本价
            if stop_pct is not None:
                p["stop_price"] = p["entry_price"] * (1 - stop_pct / 100)
            if tp_pct is not None:
                p["tp_price"] = p["entry_price"] * (1 + tp_pct / 100)
        else:
            position = {
                "name": name,
                "shares": shares,
                "entry_price": price,
                "cost": cost + fee,
                "entry_gidx": gidx,
                "entry_date": _date_str(date_ms),
                "stop_price": price * (1 - stop_pct / 100) if stop_pct is not None else None,
                "tp_price": price * (1 + tp_pct / 100) if tp_pct is not None else None,
                "entry_evidence": entry_ev,
                "rule_counts": {},
                "peak_close": price,
                "peak_high": price,
                "hold_days": 0,
            }
        day_actions.append({
            "code": code,
            "name": name,
            "action": "buy",
            "price": round(price, 4),
            "shares": shares,
            "amount": round(cost, 2),
            "fee": round(fee, 2),
            "reason": reason,
            "evidence": entry_ev,
        })
        return True

    # ---- 入场：signal_date+1 开盘买入（T+1，无未来函数）----
    day_actions: list[dict] = []  # 当前日动作（do_buy/do_sell 闭包引用）
    entry_ev = {"signal_date": _date_str(s.dates[sig_idx]), "label": "入场", "logic": "screener", "conditions": [], "passed": True}
    op_entry = s.open[entry_idx]
    if op_entry and op_entry > 0:
        do_buy(entry_idx, op_entry, s.dates[entry_idx], position_pct, entry_ev, "entry")

    # ---- 每日循环：风控 + 持仓期规则 + mark to market ----
    for gidx in range(entry_idx, n):
        if gidx > entry_idx:
            day_actions = []  # 后续日清空；entry_idx 保留入场动作
        if position is not None:
            p = position
            i = gidx
            op = s.open[i]
            if gidx > p["entry_gidx"]:  # T+1
                stop = p["stop_price"]
                tp = p["tp_price"]
                # 移动止损价：基于截至昨日的持仓期盘中最高价（无未来函数）
                trail_stop = p["peak_high"] * (1 - trail_pct / 100) if trail_pct is not None else None
                reason = None
                price = None
                exit_ev = None
                if stop is not None and op <= stop:
                    reason, price, exit_ev = "stop_loss", op, {"trigger": f"开盘价 {op} ≤ 止损价 {stop}"}
                elif tp is not None and op >= tp:
                    reason, price, exit_ev = "take_profit", op, {"trigger": f"开盘价 {op} ≥ 止盈价 {tp}"}
                elif trail_stop is not None and op <= trail_stop:
                    reason, price, exit_ev = "trailing_stop", op, {
                        "trigger": f"开盘价 {op} ≤ 移动止损价 {trail_stop:.2f}（峰值 {p['peak_high']} 回撤 {trail_pct}%）"}
                elif max_hold is not None and (gidx - p["entry_gidx"]) >= max_hold:
                    reason, price, exit_ev = "max_hold", op, {"trigger": f"持仓 {gidx - p['entry_gidx']} 日 ≥ 最长 {max_hold} 日"}
                elif stop is not None and s.low[i] <= stop:
                    reason, price, exit_ev = "stop_loss", stop, {"trigger": f"盘中最低 {s.low[i]} ≤ 止损价 {stop}"}
                elif tp is not None and s.high[i] >= tp:
                    reason, price, exit_ev = "take_profit", tp, {"trigger": f"盘中最高 {s.high[i]} ≥ 止盈价 {tp}"}
                elif trail_stop is not None and s.low[i] <= trail_stop:
                    reason, price, exit_ev = "trailing_stop", trail_stop, {
                        "trigger": f"盘中最低 {s.low[i]} ≤ 移动止损价 {trail_stop:.2f}（峰值 {p['peak_high']} 回撤 {trail_pct}%）"}
                if reason:
                    do_sell(gidx, price, reason, s.dates[gidx], exit_ev=exit_ev)
                elif i > 0:
                    ctx = _hold_ctx(s, i - 1, p)
                    for ridx, r in hold_rules:
                        if position is None:
                            break
                        if r.max_times is not None and p["rule_counts"].get(ridx, 0) >= r.max_times:
                            continue
                        if not signal_at(r.when, ctx, 0):
                            continue
                        ev = _make_evidence(ctx, 0, _date_str(s.dates[i - 1]), r.when, r.note or r.action)
                        if r.action == "sell":
                            sell_pct = r.size_pct if r.size_pct is not None else 100.0
                            do_sell(gidx, op, "signal", s.dates[gidx], exit_ev=ev, sell_pct=sell_pct)
                            p["rule_counts"][ridx] = p["rule_counts"].get(ridx, 0) + 1
                        else:  # buy 补仓
                            sz = r.size_pct if r.size_pct is not None else position_pct
                            if do_buy(gidx, op, s.dates[gidx], sz, ev, "add"):
                                p["rule_counts"][ridx] = p["rule_counts"].get(ridx, 0) + 1

        # ---- mark to market ----
        if position is not None:
            p = position
            s.last_close = s.close[gidx]
            if gidx > p["entry_gidx"]:
                p["hold_days"] += 1
            p["peak_close"] = max(p["peak_close"], s.last_close)
            p["peak_high"] = max(p["peak_high"], s.high[gidx])
        else:
            s.last_close = s.close[gidx]
        value = cash + market_value()
        peak = max(peak, value)
        dd = (value / peak - 1) * 100 if peak else 0.0
        max_dd = min(max_dd, dd)
        equity_curve.append({"date": _date_str(s.dates[gidx]), "value": round(value, 2), "drawdown_pct": round(dd, 3)})
        pos_snapshot = []
        if position is not None:
            p = position
            close_now = s.last_close or p["entry_price"]
            pos_snapshot.append({
                "code": code,
                "name": name,
                "shares": p["shares"],
                "cost": round(p["entry_price"], 4),
                "close": round(close_now, 4),
                "pnl_pct": round((close_now / p["entry_price"] - 1) * 100, 3) if p["entry_price"] else 0.0,
            })
        audit_daily.append({
            "date": _date_str(s.dates[gidx]),
            "actions": day_actions,
            "cash": round(cash, 2),
            "equity": round(value, 2),
            "positions": pos_snapshot,
        })

    # ---- 期末不平仓：持仓按最后收盘价 mark-to-market（已在每日循环计入 equity_curve）----
    # final 直接用 equity_curve 最后一项，不再产生 end_of_data 假卖出

    # ---- 单票 metrics ----
    closed = [t for t in trades if t["exit_reason"] != "end_of_data"]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = sum(t["pnl"] for t in losses)
    final = equity_curve[-1]["value"] if equity_curve else initial_cash
    total_ret = (final / initial_cash - 1) * 100 if initial_cash else 0.0

    return {
        "code": code,
        "name": name,
        "signal_date": _date_str(s.dates[sig_idx]),
        "metrics": {
            "total_return_pct": round(total_ret, 3),
            "max_drawdown_pct": round(max_dd, 3),
            "win_rate_pct": round(len(wins) / len(closed) * 100, 2) if closed else None,
            "profit_factor": round(gross_win / abs(gross_loss), 3) if gross_loss < 0 else None,
            "trade_count": len(closed),
            "win_count": len(wins),
            "loss_count": len(losses),
            "final_equity": round(final, 2),
        },
        "trades": trades,
        "equity_curve": equity_curve,
        "audit": {"daily": audit_daily},
    }


def _merge_equity(per_stock: list[dict], initial_cash: float) -> list[dict]:
    """合并各票净值曲线：按日期对齐，求和（未入场前按 initial_cash 计，离场后按终值计）。"""
    curves = [r["equity_curve"] for r in per_stock]
    all_dates = sorted({e["date"] for c in curves for e in c})
    out: list[dict] = []
    peak = 0.0
    for date in all_dates:
        total = 0.0
        for c in curves:
            v = initial_cash
            for e in c:
                if e["date"] <= date:
                    v = e["value"]
                else:
                    break
            total += v
        peak = max(peak, total)
        dd = (total / peak - 1) * 100 if peak else 0.0
        out.append({"date": date, "value": round(total, 2), "drawdown_pct": round(dd, 3)})
    return out


def backtest_pool(
    strategy,
    pool: list[dict],
    params: dict,
    data: DataService,
    max_workers: int = 6,
    progress_cb=None,
) -> dict:
    """独立回测：固定勾选池，每票独立账户（各自 initial_cash），不轮动。"""
    start_ms = parse_as_of(params["start"])
    end_ms = parse_as_of(params["end"])
    end_ms += 86_400_000 - 1  # inclusive end of day
    initial_cash = float(params["initial_cash"])
    position_pct = float(params["position_pct"])
    fee_rate = float(params["fee_bps"]) / 10000.0
    tax_rate = float(params["stamp_tax_bps"]) / 10000.0

    items = []
    for it in pool:
        code = it.get("thscode")
        if not code:
            continue
        items.append((code, it.get("name") or code, it.get("signal_date")))

    def load_and_run(item) -> dict | None:
        code, name, signal_date = item
        kind = data.kind_for(code)
        bars = data.get_bars_range(code, kind, start_ms, end_ms)
        if len(bars) < 40:
            return None
        # 找 signal_date 对应的 bar 索引
        sig_ms = parse_as_of(signal_date) if signal_date else None
        sig_idx = None
        if sig_ms is not None:
            for i, b in enumerate(bars):
                if b["date_ms"] == sig_ms:
                    sig_idx = i
                    break
            if sig_idx is None:
                for i, b in enumerate(bars):
                    if b["date_ms"] >= sig_ms:
                        sig_idx = i
                        break
        if sig_idx is None or sig_idx + 1 >= len(bars):
            return None
        series = compute_indicators(bars, strategy.indicators)
        return _run_one_stock(
            strategy, code, name, bars, series, sig_idx,
            initial_cash, position_pct, fee_rate, tax_rate,
        )

    per_stock: list[dict] = []
    done = 0
    lock = {"n": 0}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(load_and_run, it): it for it in items}
        for fut in as_completed(futures):
            try:
                r = fut.result()
                if r is not None:
                    per_stock.append(r)
            except Exception:
                pass
            lock["n"] += 1
            if progress_cb:
                progress_cb(lock["n"], len(items), futures[fut][0])

    all_trades: list[dict] = []
    for r in per_stock:
        all_trades.extend(r["trades"])
    equity_curve = _merge_equity(per_stock, initial_cash)

    # 组合汇总
    rets = [r["metrics"]["total_return_pct"] for r in per_stock]
    win_rates = [r["metrics"]["win_rate_pct"] for r in per_stock if r["metrics"]["win_rate_pct"] is not None]
    closed = [t for t in all_trades if t["exit_reason"] != "end_of_data"]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    merged_final = equity_curve[-1]["value"] if equity_curve else initial_cash * len(per_stock)
    total_ret = (merged_final / (initial_cash * len(per_stock)) - 1) * 100 if per_stock else 0.0
    max_dd = min((e["drawdown_pct"] for e in equity_curve), default=0.0)

    return {
        "params": {
            "start": params["start"],
            "end": params["end"],
            "initial_cash": initial_cash,
            "position_pct": params["position_pct"],
            "fee_bps": params["fee_bps"],
            "stamp_tax_bps": params["stamp_tax_bps"],
            "stock_count": len(per_stock),
        },
        "metrics": {
            "start": params["start"],
            "end": params["end"],
            "avg_total_return_pct": round(sum(rets) / len(rets), 3) if rets else 0.0,
            "avg_win_rate_pct": round(sum(win_rates) / len(win_rates), 2) if win_rates else None,
            "total_return_pct": round(total_ret, 3),
            "max_drawdown_pct": round(max_dd, 3),
            "trade_count": len(closed),
            "win_count": len(wins),
            "loss_count": len(losses),
            "stock_count": len(per_stock),
            "final_equity": round(merged_final, 2),
        },
        "per_stock": per_stock,
        "equity_curve": equity_curve,
        "trades": sorted(all_trades, key=lambda t: t["exit_date"]),
        "audit": {"daily": []},
    }
