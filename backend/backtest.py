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

from .conditions import signal_at
from .data_service import DataService
from .fuyao import CST
from .indicators import compute_indicators
from .screener import parse_as_of

EXIT_PRIORITY = ("stop_loss", "take_profit", "signal", "max_hold")


def _date_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=CST).strftime("%Y-%m-%d")


class _Stock:
    __slots__ = ("name", "dates", "date_idx", "open", "high", "low", "close", "entry_sig", "exit_sig", "last_close")

    def __init__(self, name, bars, series, entry_group, exit_group):
        self.name = name
        self.dates = [b["date_ms"] for b in bars]
        self.date_idx = {ms: i for i, ms in enumerate(self.dates)}
        self.open = [b["open"] for b in bars]
        self.high = [b["high"] for b in bars]
        self.low = [b["low"] for b in bars]
        self.close = [b["close"] for b in bars]
        self.last_close = self.close[-1] if self.close else None
        self.entry_sig = [signal_at(entry_group, series, i) for i in range(len(bars))]
        self.exit_sig = [signal_at(exit_group, series, i) for i in range(len(bars))]


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
    max_hold = cfg.risk.max_hold_days

    universe = params.get("universe") or cfg.universe.model_dump()
    codes, names, label = data.resolve_universe(universe)

    stocks: dict[str, _Stock] = {}
    done = 0
    lock_pct = {"n": 0}

    def load(code: str) -> tuple[str, _Stock | None]:
        kind = data.kind_for(code)
        bars = data.get_bars_range(code, kind, start_ms, end_ms)
        if len(bars) < 40:
            return code, None
        series = compute_indicators(bars, cfg.indicators)
        return code, _Stock(names.get(code) or code, bars, series, cfg.entry, cfg.exit)

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
    peak = initial_cash
    max_dd = 0.0

    def market_value() -> float:
        total = 0.0
        for code, p in positions.items():
            s = stocks[code]
            total += p["shares"] * (s.last_close or p["entry_price"])
        return total

    def sell(code: str, gidx: int, price: float, reason: str, date_ms: int) -> None:
        nonlocal cash
        p = positions.pop(code)
        gross = p["shares"] * price
        fee = gross * fee_rate
        tax = gross * tax_rate
        net = gross - fee - tax
        cash += net
        pnl = net - p["cost"]
        trades.append({
            "code": code,
            "name": p["name"],
            "entry_date": p["entry_date"],
            "entry_price": round(p["entry_price"], 4),
            "exit_date": _date_str(date_ms),
            "exit_price": round(price, 4),
            "shares": p["shares"],
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / p["cost"] * 100, 4) if p["cost"] else 0.0,
            "holding_days": gidx - p["entry_gidx"],
            "exit_reason": reason,
        })

    for gidx, d in enumerate(sim_dates):
        # ---- exit phase (open) ----
        for code in list(positions.keys()):
            p = positions[code]
            s = stocks[code]
            if d not in s.date_idx:
                continue
            i = s.date_idx[d]
            op = s.open[i]
            stop = p["stop_price"]
            tp = p["tp_price"]
            reason = None
            price = None
            if gidx > p["entry_gidx"]:
                if stop is not None and op <= stop:
                    reason, price = "stop_loss", op
                elif tp is not None and op >= tp:
                    reason, price = "take_profit", op
                elif i > 0 and s.exit_sig[i - 1]:
                    reason, price = "signal", op
                elif max_hold is not None and (gidx - p["entry_gidx"]) >= max_hold:
                    reason, price = "max_hold", op
                elif stop is not None and s.low[i] <= stop:
                    reason, price = "stop_loss", stop
                elif tp is not None and s.high[i] >= tp:
                    reason, price = "take_profit", tp
            if reason:
                sell(code, gidx, price, reason, d)
            else:
                s.last_close = s.close[i]

        # ---- entry phase (open), signal from previous close ----
        if len(positions) < max_positions:
            equity_now = cash + market_value()
            budget = equity_now * position_pct / 100.0
            for code, s in stocks.items():
                if len(positions) >= max_positions:
                    break
                if code in positions or d not in s.date_idx:
                    continue
                i = s.date_idx[d]
                if i == 0 or not s.entry_sig[i - 1]:
                    continue
                op = s.open[i]
                if not op or op <= 0:
                    continue
                shares = int(budget / op / 100) * 100
                if shares < 100:
                    continue
                cost = shares * op
                fee = cost * fee_rate
                while shares >= 100 and cost + fee > cash:
                    shares -= 100
                    cost = shares * op
                    fee = cost * fee_rate
                if shares < 100:
                    continue
                cash -= cost + fee
                positions[code] = {
                    "name": s.name,
                    "shares": shares,
                    "entry_price": op,
                    "cost": cost + fee,
                    "entry_gidx": gidx,
                    "entry_date": _date_str(d),
                    "stop_price": op * (1 - stop_pct / 100) if stop_pct is not None else None,
                    "tp_price": op * (1 + tp_pct / 100) if tp_pct is not None else None,
                }

        # ---- mark to market ----
        for code, p in list(positions.items()):
            s = stocks[code]
            if d in s.date_idx:
                s.last_close = s.close[s.date_idx[d]]
        value = cash + market_value()
        peak = max(peak, value)
        dd = (value / peak - 1) * 100 if peak else 0.0
        max_dd = min(max_dd, dd)
        equity_curve.append({"date": _date_str(d), "value": round(value, 2), "drawdown_pct": round(dd, 3)})

    # ---- force close at end ----
    for code in list(positions.keys()):
        p = positions[code]
        s = stocks[code]
        price = s.last_close or p["entry_price"]
        sell(code, len(sim_dates) - 1, price, "end_of_data", s.dates[-1])

    return _metrics(cfg, params, label, initial_cash, trades, equity_curve, max_dd, len(stocks))


def _metrics(cfg, params, label, initial_cash, trades, equity_curve, max_dd, stock_count) -> dict:
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
    }
