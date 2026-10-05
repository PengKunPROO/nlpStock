"""Backtest engine: T+1-open execution, intraday risk exits, fees, metrics and equity curve.

Execution semantics (docs/api-contract.md §3.5):
- Signals are evaluated at close of day T; fills happen at open of T+1 (no lookahead).
- A-share T+1 rule: risk/signal exits only on dates strictly after the entry date.
- Stop-loss/take-profit fill at min(open, stop) / max(open, tp) when gapped through, else at the trigger price.
- Fees: commission both sides (fee_bps), stamp tax on sells (stamp_tax_bps).
- end_of_data closes are valuations, excluded from win-rate statistics.
"""
from __future__ import annotations

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
    __slots__ = ("name", "dates", "date_idx", "open", "high", "low", "close", "last_close", "series", "open_sig")

    def __init__(self, name, bars, series, open_rules):
        self.name = name
        self.dates = [b["date_ms"] for b in bars]
        self.date_idx = {ms: i for i, ms in enumerate(self.dates)}
        self.open = [b["open"] for b in bars]
        self.high = [b["high"] for b in bars]
        self.low = [b["low"] for b in bars]
        self.close = [b["close"] for b in bars]
        self.last_close = self.close[-1] if self.close else None
        self.series = series
        # 开仓规则信号预计算（open_rules = [(全局规则索引, Rule)]）
        self.open_sig = {ridx: [signal_at(r.when, series, i) for i in range(len(bars))] for ridx, r in open_rules}


def _hold_ctx(s: _Stock, sig_idx: int, p: dict) -> dict:
    """构造持仓期规则评估上下文：单元素 series，索引 0 = 信号日（含持仓状态字段）。"""
    ctx = {k: [arr[sig_idx]] for k, arr in s.series.items()}
    cost = p["entry_price"]
    ctx["cost"] = [cost]
    ctx["pnl_pct"] = [(s.close[sig_idx] / cost - 1) * 100 if cost else 0.0]
    ctx["hold_days"] = [p["hold_days"]]
    ctx["dd_from_peak"] = [(s.close[sig_idx] / p["peak_close"] - 1) * 100 if p["peak_close"] else 0.0]
    return ctx


def backtest(
    cfg,
    params: dict,
    data: DataService,
    max_workers: int = 6,
    progress_cb=None,
) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    start_ms = parse_as_of(params["start"])
    end_ms = parse_as_of(params["end"])
    end_ms += 86_400_000 - 1  # inclusive end of day
    initial_cash = float(params["initial_cash"])
    position_pct = float(params["position_pct"])
    max_positions = int(params["max_positions"])
    fee_rate = float(params["fee_bps"]) / 10000.0
    tax_rate = float(params["stamp_tax_bps"]) / 10000.0
    stop_pct = cfg.risk.stop_loss_pct
    tp_pct = cfg.risk.take_profit_pct
    trail_pct = cfg.risk.trailing_stop_pct
    max_hold = cfg.risk.max_hold_days

    universe = params.get("universe") or cfg.universe.dict()
    codes, names, label = data.resolve_universe(universe)

    # 规则分类：开仓（buy 且不引用持仓状态）→ 预计算；持仓期（sell 或引用持仓状态）→ 实时评估
    rules = cfg.rules or []
    open_rules = [(i, r) for i, r in enumerate(rules) if r.action == "buy" and not _refs_hold(r.when)]
    hold_rules = [(i, r) for i, r in enumerate(rules) if r.action == "sell" or _refs_hold(r.when)]

    stocks: dict[str, _Stock] = {}
    done = 0
    lock_pct = {"n": 0}

    def load(code: str) -> tuple[str, _Stock | None]:
        kind = data.kind_for(code)
        bars = data.get_bars_range(code, kind, start_ms, end_ms)
        if len(bars) < 40:
            return code, None
        series = compute_indicators(bars, cfg.indicators)
        return code, _Stock(names.get(code) or code, bars, series, open_rules)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(load, c): c for c in codes}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                c, s = fut.result()
                if s is not None:
                    stocks[c] = s
            except Exception:
                pass
            lock_pct["n"] += 1
            if progress_cb:
                progress_cb(lock_pct["n"], len(codes), code)

    all_dates = sorted({ms for s in stocks.values() for ms in s.dates})
    sim_dates = [ms for ms in all_dates if start_ms <= ms <= end_ms]
    if not sim_dates:
        sim_dates = all_dates[-1:]

    cash = initial_cash
    positions: dict[str, dict] = {}
    trades: list[dict] = []
    equity_curve: list[dict] = []
    audit_daily: list[dict] = []
    peak = initial_cash
    max_dd = 0.0

    def market_value() -> float:
        total = 0.0
        for code, p in positions.items():
            s = stocks[code]
            total += p["shares"] * (s.last_close or p["entry_price"])
        return total

    def sell(code: str, gidx: int, price: float, reason: str, date_ms: int, exit_ev: dict | None = None, sell_pct: float = 100.0) -> None:
        """卖出（sell_pct=卖出持仓百分比，100=清仓）。"""
        nonlocal cash
        p = positions[code]
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
            "name": p["name"],
            "entry_date": p["entry_date"],
            "entry_price": round(p["entry_price"], 4),
            "exit_date": _date_str(date_ms),
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
            "name": p["name"],
            "action": "sell",
            "price": round(price, 4),
            "shares": shares,
            "amount": round(gross, 2),
            "fee": round(fee + tax, 2),
            "reason": reason,
            "evidence": exit_ev,
        })
        if shares >= p["shares"]:
            positions.pop(code)
        else:
            p["shares"] -= shares
            p["cost"] -= sell_cost

    def buy(code: str, gidx: int, price: float, date_ms: int, size_pct: float, entry_ev: dict, reason: str) -> bool:
        """买入/加仓（size_pct=当前权益%）。返回是否成交。"""
        nonlocal cash
        s = stocks[code]
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
        if code in positions:
            p = positions[code]
            p["shares"] += shares
            p["cost"] += cost + fee
            p["entry_price"] = p["cost"] / p["shares"]  # 加权成本价
            if stop_pct is not None:
                p["stop_price"] = p["entry_price"] * (1 - stop_pct / 100)
            if tp_pct is not None:
                p["tp_price"] = p["entry_price"] * (1 + tp_pct / 100)
        else:
            positions[code] = {
                "name": s.name,
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
            "name": s.name,
            "action": "buy",
            "price": round(price, 4),
            "shares": shares,
            "amount": round(cost, 2),
            "fee": round(fee, 2),
            "reason": reason,
            "evidence": entry_ev,
        })
        return True

    for gidx, d in enumerate(sim_dates):
        day_actions: list[dict] = []
        # ---- 1. 风控强制离场 + 持仓期规则（sell 减仓/清仓 + buy 加仓）----
        for code in list(positions.keys()):
            p = positions[code]
            s = stocks[code]
            if d not in s.date_idx:
                continue
            i = s.date_idx[d]
            op = s.open[i]
            if gidx <= p["entry_gidx"]:
                continue  # 买入当天不卖（T+1）
            stop = p["stop_price"]
            tp = p["tp_price"]
            # 移动止损价：基于截至昨日的持仓期盘中最高价（当日新高先后顺序未知，用昨日峰值判定，无未来函数）
            trail_stop = p["peak_high"] * (1 - trail_pct / 100) if trail_pct is not None else None
            reason = None
            price = None
            exit_ev: dict | None = None
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
                sell(code, gidx, price, reason, d, exit_ev=exit_ev)
                continue
            # 持仓期规则：信号在 i-1 日收盘判定，i 日开盘执行
            if i > 0:
                ctx = _hold_ctx(s, i - 1, p)
                for ridx, r in hold_rules:
                    if r.max_times is not None and p["rule_counts"].get(ridx, 0) >= r.max_times:
                        continue
                    if not signal_at(r.when, ctx, 0):
                        continue
                    ev = _make_evidence(ctx, 0, _date_str(s.dates[i - 1]), r.when, r.note or r.action)
                    if r.action == "sell":
                        sell_pct = r.size_pct if r.size_pct is not None else 100.0
                        sell(code, gidx, op, "signal", d, exit_ev=ev, sell_pct=sell_pct)
                        p["rule_counts"][ridx] = p["rule_counts"].get(ridx, 0) + 1
                        if code not in positions:
                            break  # 已清仓
                    else:  # buy 加仓
                        sz = r.size_pct if r.size_pct is not None else position_pct
                        if buy(code, gidx, op, d, sz, ev, "add"):
                            p["rule_counts"][ridx] = p["rule_counts"].get(ridx, 0) + 1

        # ---- 2. 开仓规则（对未持仓股票）----
        for code, s in stocks.items():
            if len(positions) >= max_positions:
                break
            if code in positions or d not in s.date_idx:
                continue
            i = s.date_idx[d]
            if i == 0:
                continue
            for ridx, r in open_rules:
                if len(positions) >= max_positions:
                    break
                if not s.open_sig[ridx][i - 1]:
                    continue
                op = s.open[i]
                if not op or op <= 0:
                    continue
                sz = r.size_pct if r.size_pct is not None else position_pct
                ev = _make_evidence(s.series, i - 1, _date_str(s.dates[i - 1]), r.when, r.note or "入场")
                if buy(code, gidx, op, d, sz, ev, "entry"):
                    break  # 该股已开仓，跳过其余开仓规则

        # ---- 3. mark to market ----
        for code, p in list(positions.items()):
            s = stocks[code]
            if d in s.date_idx:
                s.last_close = s.close[s.date_idx[d]]
                if gidx > p["entry_gidx"]:
                    p["hold_days"] += 1
                p["peak_close"] = max(p["peak_close"], s.last_close)
                p["peak_high"] = max(p["peak_high"], s.high[s.date_idx[d]])
        value = cash + market_value()
        peak = max(peak, value)
        dd = (value / peak - 1) * 100 if peak else 0.0
        max_dd = min(max_dd, dd)
        equity_curve.append({"date": _date_str(d), "value": round(value, 2), "drawdown_pct": round(dd, 3)})
        pos_snapshot = []
        for code, p in positions.items():
            s = stocks[code]
            close_now = s.last_close or p["entry_price"]
            pos_snapshot.append({
                "code": code,
                "name": p["name"],
                "shares": p["shares"],
                "cost": round(p["entry_price"], 4),
                "close": round(close_now, 4),
                "pnl_pct": round((close_now / p["entry_price"] - 1) * 100, 3) if p["entry_price"] else 0.0,
            })
        audit_daily.append({
            "date": _date_str(d),
            "actions": day_actions,
            "cash": round(cash, 2),
            "equity": round(value, 2),
            "positions": pos_snapshot,
        })

    # ---- force close at end ----
    for code in list(positions.keys()):
        p = positions[code]
        s = stocks[code]
        price = s.last_close or p["entry_price"]
        sell(code, len(sim_dates) - 1, price, "end_of_data", s.dates[-1],
             exit_ev={"trigger": "回测期末，按最后收盘价估值平仓"})

    return _metrics(cfg, params, label, initial_cash, trades, equity_curve, max_dd, len(stocks), audit_daily)


def _metrics(cfg, params, label, initial_cash, trades, equity_curve, max_dd, stock_count, audit_daily=None) -> dict:
    closed = [t for t in trades if t["exit_reason"] != "end_of_data"]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = sum(t["pnl"] for t in losses)
    final = equity_curve[-1]["value"] if equity_curve else initial_cash
    n_days = len(equity_curve)
    total_ret = (final / initial_cash - 1) * 100 if initial_cash else 0.0
    annual = ((final / initial_cash) ** (252 / n_days) - 1) * 100 if n_days > 0 and initial_cash > 0 and final > 0 else None
    sharpe = None
    if n_days > 1:
        rets = []
        prev = initial_cash
        for e in equity_curve:
            if prev:
                rets.append(e["value"] / prev - 1)
            prev = e["value"]
        mean_r = sum(rets) / len(rets)
        var = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
        std = var ** 0.5
        if std > 0:
            sharpe = round(mean_r / std * (252 ** 0.5), 3)
    return {
        "params": {
            "start": params["start"],
            "end": params["end"],
            "initial_cash": initial_cash,
            "position_pct": params["position_pct"],
            "max_positions": params["max_positions"],
            "fee_bps": params["fee_bps"],
            "stamp_tax_bps": params["stamp_tax_bps"],
            "universe_name": label,
            "stock_count": stock_count,
        },
        "metrics": {
            "start": params["start"],
            "end": params["end"],
            "total_return_pct": round(total_ret, 3),
            "annual_return_pct": round(annual, 3) if annual is not None else None,
            "max_drawdown_pct": round(max_dd, 3),
            "sharpe": sharpe,
            "win_rate_pct": round(len(wins) / len(closed) * 100, 2) if closed else None,
            "profit_factor": round(gross_win / abs(gross_loss), 3) if gross_loss < 0 else None,
            "trade_count": len(closed),
            "win_count": len(wins),
            "loss_count": len(losses),
            "avg_win_pct": round(sum(t["pnl_pct"] for t in wins) / len(wins), 3) if wins else None,
            "avg_loss_pct": round(sum(t["pnl_pct"] for t in losses) / len(losses), 3) if losses else None,
            "avg_hold_days": round(sum(t["holding_days"] for t in closed) / len(closed), 1) if closed else None,
            "final_equity": round(final, 2),
        },
        "equity_curve": equity_curve,
        "trades": sorted(trades, key=lambda t: t["exit_date"]),
        "audit": {"daily": audit_daily or []},
    }
