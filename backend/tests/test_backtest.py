"""独立回测引擎测试：止损/止盈/最长持仓/期末平仓/仓位/净值/审计，入场由 signal_date 驱动。"""
import pytest

from backend.backtest import _date_str, backtest_pool
from backend.schema import TradingStrategy

BASE_MS = 1_760_000_000_000
DAY = 86_400_000


def bars_from_closes(closes, opens=None, lows=None, highs=None, warmup=30, base=10.0):
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


def params(**overrides):
    p = {
        "start": "2025-06-01", "end": "2025-12-31", "initial_cash": 1000000,
        "position_pct": 100, "fee_bps": 2.5, "stamp_tax_bps": 5.0,
    }
    p.update(overrides)
    return p


class FakeData:
    def __init__(self, bars_map):
        self.bars_map = bars_map

    def kind_for(self, code):
        return "stock"

    def get_bars_range(self, code, kind, start_ms, end_ms, warmup_bars=250):
        return [b for b in self.bars_map[code] if start_ms - 400 * DAY <= b["date_ms"] <= end_ms]


def trading_cfg(rules, indicators=None, risk=None):
    return {
        "name": "交易策略",
        "indicators": indicators or [{"id": "ma5", "kind": "MA", "of": "close", "n": 5}],
        "rules": rules,
        "risk": risk or {"stop_loss_pct": None, "trailing_stop_pct": None, "max_hold_days": None, "take_profit_pct": None},
        "backtest_defaults": {
            "start": "2025-06-01", "end": "2025-12-31", "initial_cash": 1000000,
            "position_pct": 100, "max_positions": 5, "fee_bps": 2.5, "stamp_tax_bps": 5.0,
        },
    }


SELL_BELOW_MA5 = [{"when": {"logic": "any", "conditions": [{"left": "close", "op": "<", "right": "ma5"}]},
                    "action": "sell", "size_pct": 100, "note": "跌破5日线清仓"}]


def run(rules, closes, opens=None, lows=None, highs=None, risk=None, indicators=None, signal_pos=10, **pover):
    """单票独立回测。signal_pos=10 → 信号日 bars[40]（closes[10]，前 10 根平坦 bar 之后），入场 bars[41] 开盘。"""
    strategy = TradingStrategy.parse_obj(trading_cfg(rules, indicators=indicators, risk=risk))
    bars = bars_from_closes(closes, opens=opens, lows=lows, highs=highs)
    sig_idx = 30 + signal_pos
    signal_date = _date_str(bars[sig_idx]["date_ms"])
    pool = [{"thscode": "600001.SH", "name": "股600001", "signal_date": signal_date}]
    data = FakeData({"600001.SH": bars})
    result = backtest_pool(strategy, pool, params(**pover), data)
    return result["per_stock"][0]


def test_t_plus_one_open_fill():
    """T+1 开盘成交：信号日 bars[40]，入场价 = bars[41] 开盘价。"""
    closes = [10.0] * 10 + [10.5, 11.0, 11.5, 12.0, 12.5, 11.0, 10.8, 10.6, 10.4]
    result = run(SELL_BELOW_MA5, closes, opens=list(closes))
    t = result["trades"][0]
    # 入场价 = bars[41] 开盘 = 11.0
    assert t["entry_price"] == 11.0
    # entry_idx = 41（bars 索引）
    assert t["entry_idx"] == 41


def test_stop_loss_gap_down_fills_at_open():
    """止损跳空：开盘价跌破止损价 → 按开盘价成交。"""
    closes = [10.0] * 10 + [10.5, 11.0, 10.0, 9.5, 9.0]
    opens = [10.0] * 10 + [10.5, 11.0, 10.0, 9.5, 9.0]
    result = run(SELL_BELOW_MA5, closes, opens=opens,
                 risk={"stop_loss_pct": 8.0, "trailing_stop_pct": None, "max_hold_days": None, "take_profit_pct": None})
    t = result["trades"][0]
    assert t["exit_reason"] == "stop_loss"
    assert t["entry_price"] == 11.0


def test_stop_loss_intraday_fills_at_stop():
    """止损盘中：low 跌破止损价 → 按止损价成交。"""
    closes = [10.0] * 10 + [10.5, 11.0, 10.3, 10.4, 10.5]
    opens = [10.0] * 10 + [10.5, 11.0, 10.8, 10.4, 10.5]
    lows = [9.9] * 10 + [10.2, 10.8, 10.0, 10.3, 10.4]
    result = run(SELL_BELOW_MA5, closes, opens=opens, lows=lows,
                 risk={"stop_loss_pct": 8.0, "trailing_stop_pct": None, "max_hold_days": None, "take_profit_pct": None})
    t = result["trades"][0]
    assert t["exit_reason"] == "stop_loss"
    assert t["exit_price"] == pytest.approx(11.0 * 0.92, abs=1e-9)


def test_take_profit_intraday():
    """止盈盘中：high 触及止盈价 → 按止盈价成交。"""
    closes = [10.0] * 10 + [10.5, 11.0, 11.5, 12.3, 12.4]
    opens = [10.0] * 10 + [10.5, 11.0, 11.5, 11.8, 12.4]
    highs = [10.1] * 10 + [10.6, 11.1, 11.6, 12.4, 12.5]
    result = run(SELL_BELOW_MA5, closes, opens=opens, highs=highs,
                 risk={"stop_loss_pct": None, "trailing_stop_pct": None, "max_hold_days": None, "take_profit_pct": 10.0})
    t = result["trades"][0]
    assert t["exit_reason"] == "take_profit"
    assert t["exit_price"] == pytest.approx(11.0 * 1.10, abs=1e-9)


def test_max_hold_forced_exit():
    """最长持仓：持仓 N 日后强制卖出。"""
    closes = [10.0] * 10 + [10.5 + i * 0.1 for i in range(15)]
    opens = list(closes)
    result = run(SELL_BELOW_MA5, closes, opens=opens,
                 risk={"stop_loss_pct": None, "trailing_stop_pct": None, "max_hold_days": 5, "take_profit_pct": None})
    t = result["trades"][0]
    assert t["exit_reason"] == "max_hold"
    assert t["holding_days"] >= 5


def test_no_sell_holds_to_end_marked_to_market():
    """期末不平仓：持仓按最后收盘价 mark-to-market，不产生 end_of_data 假卖出。"""
    closes = [10.0] * 10 + [10.5 + i * 0.1 for i in range(10)]
    result = run(SELL_BELOW_MA5, closes, opens=list(closes))
    # 价格一路上涨，从没跌破 MA5 → 无主动卖出，期末持仓不平仓
    assert result["trades"] == []
    assert result["metrics"]["trade_count"] == 0
    assert result["metrics"]["win_rate_pct"] is None
    # 期末 mark-to-market：浮盈计入 final_equity 和 total_return_pct
    assert result["metrics"]["total_return_pct"] > 0
    assert result["metrics"]["final_equity"] > 1000000


def test_position_sizing_cash_constraint():
    """仓位现金约束：资金不足按可买数量成交。"""
    closes = [10.0] * 10 + [10.5, 11.0, 11.5, 12.0, 12.5, 11.0, 10.8, 10.6, 10.4]
    result = run(SELL_BELOW_MA5, closes, opens=list(closes), initial_cash=15000)
    t = result["trades"][0]
    # 15000 / 11.0 = 1363.6 → 1300 shares
    assert t["shares"] == 1300
    assert t["shares"] * t["entry_price"] <= 15000


def test_equity_curve_dates_and_drawdown():
    """净值曲线：首日（入场日）略小于初始资金（买入费用），回撤 ≤ 0。"""
    closes = [10.0] * 10 + [10.5, 11.0, 12.0, 12.5, 11.0]
    result = run(SELL_BELOW_MA5, closes, opens=list(closes))
    ec = result["equity_curve"]
    assert 999000 < ec[0]["value"] < 1000000  # 入场日买入后市值（含费用）
    assert all(e["drawdown_pct"] <= 0 for e in ec)


def test_audit_daily_and_evidence():
    """审计：逐日流水 + 交易依据。"""
    closes = [10.0] * 10 + [10.5, 11.0, 11.5, 12.0, 12.5, 11.0, 10.8, 10.6, 10.4]
    result = run(SELL_BELOW_MA5, closes, opens=list(closes))
    daily = result["audit"]["daily"]
    assert len(daily) > 0
    assert len(daily) == len(result["equity_curve"])
    for d, ec in zip(daily, result["equity_curve"]):
        assert d["date"] == ec["date"]
        assert d["equity"] == ec["value"]
    t = result["trades"][0]
    assert "entry_evidence" in t and t["entry_evidence"] is not None
    assert "signal_date" in t["entry_evidence"]


def test_no_lookahead_signal_on_last_bar_no_fill():
    """信号在最后一根 K 线 → 无次日 → 不成交。"""
    closes = [10.0] * 10 + [10.5]
    strategy = TradingStrategy.parse_obj(trading_cfg(SELL_BELOW_MA5))
    bars = bars_from_closes(closes, opens=list(closes))
    sig_idx = 30 + len(closes) - 1  # 最后一根 bar
    signal_date = _date_str(bars[sig_idx]["date_ms"])
    pool = [{"thscode": "600001.SH", "name": "股600001", "signal_date": signal_date}]
    data = FakeData({"600001.SH": bars})
    result = backtest_pool(strategy, pool, params(), data)
    assert result["trades"] == []
    assert result["metrics"]["stock_count"] == 0  # 无有效回测（无次日无法入场）
