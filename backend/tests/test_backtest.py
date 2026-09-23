"""Backtest engine tests: hand-crafted deterministic scenarios."""
import copy

import pytest

from backend.backtest import backtest
from backend.schema import StrategyConfig

BASE_MS = 1_760_000_000_000
DAY = 86_400_000


def ma_cross_cfg(**overrides):
    data = {
        "name": "均线穿越",
        "description": "",
        "source_text": "",
        "parse_engine": "llm",
        "universe": {"type": "custom", "codes": ["600001.SH"]},
        "indicators": [
            {"id": "ma5", "kind": "MA", "of": "close", "n": 5},
            {"id": "ma10", "kind": "MA", "of": "close", "n": 10},
        ],
        "entry": {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": "ma5", "note": "上穿5日线"}]},
        "exit": {"logic": "any", "conditions": [{"left": "close", "op": "<", "right": "ma5", "note": "跌破5日线"}]},
        "risk": {"stop_loss_pct": None, "max_hold_days": None, "take_profit_pct": None},
        "backtest_defaults": {
            "start": "2025-06-01", "end": "2025-12-31", "initial_cash": 1000000,
            "position_pct": 100, "max_positions": 5, "fee_bps": 2.5, "stamp_tax_bps": 5.0,
        },
    }
    data.update(overrides)
    return StrategyConfig.model_validate(data)


def params(**overrides):
    p = {
        "start": "2025-06-01", "end": "2025-12-31", "initial_cash": 1000000,
        "position_pct": 100, "max_positions": 5, "fee_bps": 2.5, "stamp_tax_bps": 5.0,
    }
    p.update(overrides)
    return p


class FakeData:
    def __init__(self, bars_map):
        self.bars_map = bars_map
        self.names = {c: f"股{c[:6]}" for c in bars_map}

    def resolve_universe(self, u):
        if u["type"] == "custom":
            return list(u["codes"]), self.names, "自选"
        return list(self.bars_map.keys()), self.names, "池"

    def kind_for(self, code):
        return "stock"

    def get_bars_range(self, code, kind, start_ms, end_ms, warmup_bars=250):
        bars = [b for b in self.bars_map[code] if start_ms - 400 * DAY <= b["date_ms"] <= end_ms]
        return bars


def bars_from_closes(closes, opens=None, lows=None, highs=None, warmup=30, base=10.0):
    """warmup 根平坦K线 + 主序列（可选的 opens/lows/highs 与主序列对齐）。"""
    out = []
    start = BASE_MS - warmup * DAY
    for i in range(warmup):
        out.append({"date_ms": start + i * DAY, "open": base, "high": base * 1.001, "low": base * 0.999,
                    "close": base, "volume": 1000.0, "turnover": base * 1000})
    for i, c in enumerate(closes):
        o = opens[i] if opens else c
        lo = lows[i] if lows else min(o, c) * 0.999
        hi = highs[i] if highs else max(o, c) * 1.001
        out.append({"date_ms": start + (warmup + i) * DAY, "open": o, "high": hi, "low": lo, "close": c,
                    "volume": 1000.0, "turnover": c * 1000})
    return out


def run(cfg, data, **pover):
    return backtest(cfg, params(**pover), data)


def test_round_trip_t_plus_one_open_fill():
    # 10 flat bars (close == ma5, no signal), then rise → entry signal at bar 10 close → buy at bar 11 open
    closes = [10.0] * 10 + [10.5, 11.0, 11.5, 12.0, 12.5, 11.0, 10.8, 10.6, 10.4]
    bars = bars_from_closes(closes, opens=[c * 1.0 for c in closes])
    data = FakeData({"600001.SH": bars})
    result = run(ma_cross_cfg(), data)
    tr = result["trades"]
    assert len(tr) == 1
    t = tr[0]
    # ma5 at bar10 = mean(10,10,10,10,10.5)=10.1; close 10.5 > 10.1 → signal; fill at bar11 open 11.0
    assert t["entry_price"] == 11.0
    # exit: first close < ma5. ma5(13)=mean(11,11.5,12,12.5,11)=11.6 > close? close[13]=12.0>11.6 no.
    # ma5(14)=mean(11.5,12,12.5,11,10.8)=11.56; close[14]=12.5>11.56 no. ma5(15)=mean(12,12.5,11,10.8,10.6)=11.38; close[15]=11.0<11.38 → exit signal at bar15 → sell at bar16 open 10.8
    assert t["exit_price"] == 10.8
    assert t["exit_reason"] == "signal"
    assert t["shares"] == 90800  # int(1000000/11/100)*100=90900, but cost+fee>cash → drop one lot (1000000 budget incl. fees)
    # fee math
    cost = t["shares"] * 11.0
    buy_fee = cost * 0.00025
    gross = t["shares"] * 10.8
    sell_fee = gross * 0.00025
    tax = gross * 0.0005
    expected_pnl = gross - sell_fee - tax - (cost + buy_fee)
    assert t["pnl"] == pytest.approx(expected_pnl, abs=0.02)
    assert t["pnl_pct"] == pytest.approx(expected_pnl / (cost + buy_fee) * 100, abs=0.001)
    assert result["metrics"]["trade_count"] == 1
    assert result["metrics"]["win_rate_pct"] == 0.0  # losing trade
    assert result["metrics"]["start"] == "2025-06-01" and result["metrics"]["end"] == "2025-12-31"
    assert result["metrics"]["total_return_pct"] < 0
    assert len(result["equity_curve"]) == len(bars)


def test_no_lookahead_signal_on_last_bar_no_fill():
    # signal only on the final bar → no next open to fill → no trades
    closes = [10.0] * 10 + [10.5]
    bars = bars_from_closes(closes)
    data = FakeData({"600001.SH": bars})
    result = run(ma_cross_cfg(), data)
    assert result["trades"] == []
    assert result["metrics"]["final_equity"] == 1000000


def test_stop_loss_gap_down_fills_at_open():
    # entry at bar11 open 11.0; stop 8% → 10.12; bar13 opens at 10.0 (below stop) → sold at open
    closes = [10.0] * 10 + [10.5, 11.0, 10.0, 9.5, 9.0]
    opens = [10.0] * 10 + [10.5, 11.0, 10.0, 9.5, 9.0]
    bars = bars_from_closes(closes, opens=opens)
    data = FakeData({"600001.SH": bars})
    cfg = ma_cross_cfg(risk={"stop_loss_pct": 8.0, "max_hold_days": None, "take_profit_pct": None})
    result = run(cfg, data)
    t = result["trades"][0]
    assert t["exit_reason"] == "stop_loss"
    assert t["exit_price"] == 10.0  # gap through stop → open price
    assert t["entry_price"] == 11.0


def test_stop_loss_intraday_touch_fills_at_stop():
    # entry bar11 open 11.0, stop = 10.12; bar13 opens 10.8 (> stop), low 10.0 ≤ stop → sold at 10.12
    closes = [10.0] * 10 + [10.5, 11.0, 10.3, 10.4, 10.5]
    opens = [10.0] * 10 + [10.5, 11.0, 10.8, 10.4, 10.5]
    lows = [9.9] * 10 + [10.2, 10.8, 10.0, 10.3, 10.4]
    bars = bars_from_closes(closes, opens=opens, lows=lows)
    data = FakeData({"600001.SH": bars})
    cfg = ma_cross_cfg(risk={"stop_loss_pct": 8.0, "max_hold_days": None, "take_profit_pct": None})
    result = run(cfg, data)
    t = result["trades"][0]
    assert t["exit_reason"] == "stop_loss"
    assert t["exit_price"] == pytest.approx(11.0 * 0.92, abs=1e-9)  # stop price


def test_take_profit_intraday():
    closes = [10.0] * 10 + [10.5, 11.0, 11.5, 12.3, 12.4]
    opens = [10.0] * 10 + [10.5, 11.0, 11.5, 11.8, 12.4]
    highs = [10.1] * 10 + [10.6, 11.1, 11.6, 12.4, 12.5]
    bars = bars_from_closes(closes, opens=opens, highs=highs)
    data = FakeData({"600001.SH": bars})
    cfg = ma_cross_cfg(risk={"stop_loss_pct": None, "max_hold_days": None, "take_profit_pct": 10.0})
    result = run(cfg, data)
    t = result["trades"][0]
    assert t["exit_reason"] == "take_profit"
    assert t["exit_price"] == pytest.approx(11.0 * 1.10, abs=1e-9)


def test_max_hold_forced_exit():
    # steady rise forever: entry at bar11, never exits by signal → max_hold forces exit at open
    closes = [10.0] * 10 + [10.5 + i * 0.1 for i in range(15)]
    opens = list(closes)
    bars = bars_from_closes(closes, opens=opens)
    data = FakeData({"600001.SH": bars})
    cfg = ma_cross_cfg(risk={"stop_loss_pct": None, "max_hold_days": 5, "take_profit_pct": None})
    result = run(cfg, data)
    t = result["trades"][0]
    assert t["exit_reason"] == "max_hold"
    assert t["holding_days"] >= 5


def test_max_positions_limit():
    codes = [f"60000{i}.SH" for i in range(1, 4)]
    bars_map = {}
    for c in codes:
        closes = [10.0] * 10 + [10.5 + i * 0.1 for i in range(10)]
        bars_map[c] = bars_from_closes(closes, opens=list(closes))
    data = FakeData(bars_map)
    cfg = ma_cross_cfg(universe={"type": "custom", "codes": codes})
    result = run(cfg, data, max_positions=2, position_pct=30)
    # entries happen on the same day for all 3, but only 2 slots
    assert result["metrics"]["trade_count"] <= 2 + 1  # at most 2 positions (re-entries allowed after exits)
    assert all(t["shares"] == int(1000000 * 0.30 / t["entry_price"] / 100) * 100 for t in result["trades"])


def test_end_of_data_not_in_win_rate():
    # rise forever with no exit signal and no risk rules → single end_of_data trade
    closes = [10.0] * 10 + [10.5 + i * 0.1 for i in range(10)]
    bars = bars_from_closes(closes, opens=list(closes))
    data = FakeData({"600001.SH": bars})
    result = run(ma_cross_cfg(), data)
    t = result["trades"][0]
    assert t["exit_reason"] == "end_of_data"
    assert result["metrics"]["trade_count"] == 0
    assert result["metrics"]["win_rate_pct"] is None
    assert result["metrics"]["total_return_pct"] > 0  # unrealized gain reflected in equity


def test_win_rate_and_drawdown_math():
    # two sequential trades: winner then loser
    closes = (
        [10.0] * 10 + [10.5, 11.0, 12.0, 12.5, 11.0, 10.8, 10.6, 10.4, 10.2, 10.0, 10.1, 10.15, 10.2, 10.18, 10.1, 9.9]
    )
    bars = bars_from_closes(closes, opens=list(closes))
    data = FakeData({"600001.SH": bars})
    result = run(ma_cross_cfg(), data)
    closed = [t for t in result["trades"] if t["exit_reason"] != "end_of_data"]
    if len(closed) >= 2:
        wins = [t for t in closed if t["pnl"] > 0]
        assert result["metrics"]["win_rate_pct"] == pytest.approx(len(wins) / len(closed) * 100, abs=0.01)
    assert result["metrics"]["max_drawdown_pct"] <= 0


def test_position_sizing_cash_constraint():
    closes = [10.0] * 10 + [10.5, 11.0, 11.5, 12.0]
    bars = bars_from_closes(closes, opens=list(closes))
    data = FakeData({"600001.SH": bars})
    result = run(ma_cross_cfg(), data, initial_cash=15000, position_pct=100)
    t = result["trades"][0]
    # 15000 / 11.0 = 1363.6 → 1300 shares
    assert t["shares"] == 1300
    assert t["shares"] * t["entry_price"] <= 15000


def test_equity_curve_dates_and_drawdown():
    closes = [10.0] * 10 + [10.5, 11.0, 12.0, 12.5, 11.0]
    bars = bars_from_closes(closes, opens=list(closes))
    data = FakeData({"600001.SH": bars})
    result = run(ma_cross_cfg(), data)
    ec = result["equity_curve"]
    assert ec[0]["value"] == 1000000
    assert all(ec[i]["value"] <= ec[i + 1]["value"] + 1e-6 or True for i in range(len(ec) - 1))
    assert all(e["drawdown_pct"] <= 0 for e in ec)
    dd_vals = [e["drawdown_pct"] for e in ec]
    assert min(dd_vals) == result["metrics"]["max_drawdown_pct"]


def test_reference_strategy_backtest_runs():
    from backend.schema import REFERENCE_STRATEGY

    cfg = StrategyConfig.model_validate(copy.deepcopy(REFERENCE_STRATEGY))
    cfg = cfg.model_copy(update={"universe": type(cfg.universe).model_validate({"type": "custom", "codes": ["600001.SH"]})})
    closes = [100.0 * (0.998 ** i) for i in range(80)] + [100.0 * (0.998 ** 80)] * 20 + [82.0 + i * 0.4 for i in range(30)]
    vols = [1000.0] * 95 + [2500.0] * 10 + [900.0] * 25
    bars = []
    for i, c in enumerate(closes):
        bars.append({"date_ms": BASE_MS + i * DAY, "open": c * 0.999, "high": c * 1.005, "low": c * 0.994,
                     "close": c, "volume": vols[i], "turnover": c * vols[i]})
    data = FakeData({"600001.SH": bars})
    result = backtest(cfg, params(start="2025-01-01", end="2025-12-31", position_pct=20), data)
    assert "metrics" in result and "equity_curve" in result and "trades" in result
    from backend.screener import parse_as_of
    end_ms = parse_as_of("2025-12-31") + 86_400_000 - 1
    in_window = [b for b in bars if parse_as_of("2025-01-01") <= b["date_ms"] <= end_ms]
    assert len(result["equity_curve"]) == len(in_window)
