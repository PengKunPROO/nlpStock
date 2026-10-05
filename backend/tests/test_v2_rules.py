"""交易策略独立回测测试：TradingStrategy（持仓管理规则）+ backtest_pool（固定池独立回测）。"""
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
        self.names = {c: f"股{c[:6]}" for c in bars_map}

    def kind_for(self, code):
        return "stock"

    def get_bars_range(self, code, kind, start_ms, end_ms, warmup_bars=250):
        return [b for b in self.bars_map[code] if start_ms - 400 * DAY <= b["date_ms"] <= end_ms]


def trading_cfg(rules, indicators=None, risk=None):
    return {
        "name": "交易策略测试",
        "indicators": indicators or [{"id": "ma5", "kind": "MA", "of": "close", "n": 5}],
        "rules": rules,
        "risk": risk or {"stop_loss_pct": None, "trailing_stop_pct": None, "max_hold_days": None, "take_profit_pct": None},
        "backtest_defaults": {
            "start": "2025-06-01", "end": "2025-12-31", "initial_cash": 1000000,
            "position_pct": 20, "max_positions": 5, "fee_bps": 2.5, "stamp_tax_bps": 5.0,
        },
    }


def run_pool(rules, closes, opens=None, lows=None, highs=None, signal_pos=0, risk=None, indicators=None, pool_codes=None):
    """signal_pos=0 表示信号日在第一个 closes bar（bars[warmup]），入场在 bars[warmup+1] 开盘。"""
    strategy = TradingStrategy.parse_obj(trading_cfg(rules, indicators=indicators, risk=risk))
    bars = bars_from_closes(closes, opens=opens, lows=lows, highs=highs)
    sig_idx = 30 + signal_pos
    signal_date = _date_str(bars[sig_idx]["date_ms"])
    codes = pool_codes or ["600001.SH"]
    bars_map = {}
    pool = []
    for code in codes:
        bars_map[code] = bars
        pool.append({"thscode": code, "name": f"股{code[:6]}", "signal_date": signal_date})
    data = FakeData(bars_map)
    result = backtest_pool(strategy, pool, params(), data)
    if len(pool) == 1:
        return result["per_stock"][0]  # 单票：返回单票结果（含 trades/audit/equity_curve）
    return result


# ---------- schema 校验 ----------


def test_trading_strategy_valid():
    cfg = TradingStrategy.parse_obj(trading_cfg([
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": "<=", "right": -5}]},
         "action": "buy", "size_pct": 33.33, "max_times": 1, "note": "跌5%补仓"},
        {"when": {"logic": "any", "conditions": [{"left": "close", "op": "<", "right": "ma5"}]},
         "action": "sell", "size_pct": 100, "note": "跌破清仓"},
    ]))
    assert len(cfg.rules) == 2


def test_trading_buy_rule_must_ref_hold():
    """buy 规则必须引用持仓字段（补仓），否则拒绝。"""
    with pytest.raises(Exception):
        TradingStrategy.parse_obj(trading_cfg([
            {"when": {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": "ma5"}]},
             "action": "buy", "size_pct": 100, "note": "上穿买入（非法：未引用持仓字段）"},
        ]))


def test_trading_empty_rules_rejected():
    with pytest.raises(Exception):
        TradingStrategy.parse_obj(trading_cfg([]))


# ---------- 引擎执行 ----------


def test_partial_sell():
    """涨10%减半：入场后涨，pnl>=10% 触发卖一半。"""
    closes = [10.0] * 10 + [10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 12.0, 11.5, 11.0, 10.5]
    result = run_pool([
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": ">=", "right": 10}]},
         "action": "sell", "size_pct": 50, "max_times": 1, "note": "涨10%减半"},
    ], closes)
    # 入场价 10.5（bars[31] open），后续涨到 pnl>=10% 触发卖一半
    sells = [a for d in result["audit"]["daily"] for a in d["actions"] if a["action"] == "sell"]
    assert len(sells) >= 1
    # 卖一半后持仓股数减半（初始约 95200 股，卖后剩余约 47600）
    t = result["trades"][0]
    assert t["shares"] < 70000  # 减半后远小于初始满仓


def test_add_on_buy_weighted_cost():
    """跌5%补仓：入场后跌5%，补仓规则触发，加权成本。"""
    closes = [10.0] * 10 + [10.0, 10.5, 10.0, 9.5, 9.0, 8.5, 8.0, 7.5, 7.0]
    result = run_pool([
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": "<=", "right": -5}]},
         "action": "buy", "size_pct": 30, "max_times": 1, "note": "跌5%补仓"},
    ], closes)
    buys = [a for d in result["audit"]["daily"] for a in d["actions"] if a["action"] == "buy"]
    assert len(buys) >= 2  # 首次入场 + 至少一次补仓


def test_max_times_prevents_infinite():
    """max_times=1 的补仓规则只触发一次，不无限补仓。"""
    closes = [10.0] * 10 + [10.0, 10.5, 10.0, 9.5, 9.0, 8.5, 8.0, 7.5, 7.0, 6.5, 6.0]
    result = run_pool([
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": "<=", "right": -5}]},
         "action": "buy", "size_pct": 20, "max_times": 1, "note": "跌5%补仓一次"},
    ], closes)
    buys = [a for d in result["audit"]["daily"] for a in d["actions"] if a["action"] == "buy"]
    assert len(buys) == 2  # 首仓 + 最多1次补仓


def test_hold_days_sell():
    """持仓5日清仓：hold_days>=5 触发卖出。"""
    closes = [10.0] * 10 + [10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0, 14.0]
    result = run_pool([
        {"when": {"logic": "all", "conditions": [{"left": "hold_days", "op": ">=", "right": 5}]},
         "action": "sell", "size_pct": 100, "note": "持仓5日清仓"},
    ], closes)
    t = result["trades"][0]
    assert t["holding_days"] >= 5
    assert t["exit_reason"] == "signal"


# ---------- 移动止损（盘中 trailing stop） ----------

TRAIL_RISK = {"stop_loss_pct": None, "trailing_stop_pct": 8, "max_hold_days": None, "take_profit_pct": None}
_PRE = [10.0] * 10


def run_trail(closes, opens, lows=None, highs=None):
    # 占位规则（永不触发），只测 risk 的 trailing stop
    placeholder = [{"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": ">=", "right": 999}]},
                    "action": "sell", "size_pct": 100, "note": "占位"}]
    return run_pool(placeholder, _PRE + closes, opens=_PRE + opens,
                    lows=_PRE + lows if lows else None, highs=_PRE + highs if highs else None,
                    risk=TRAIL_RISK)


def test_trailing_stop_intraday():
    """盘中触发：峰值13.5 回撤8% → 止损价12.42，当日 low 跌破按止损价成交。"""
    result = run_trail(
        closes=[10.5, 11.0, 12.0, 13.0, 11.5],
        opens=[10.5, 10.5, 11.5, 12.5, 13.0],
        lows=[10.4, 10.4, 11.4, 12.4, 11.0],
        highs=[10.6, 11.2, 12.0, 13.5, 13.2],
    )
    t = result["trades"][0]
    assert t["exit_reason"] == "trailing_stop"
    assert t["exit_price"] == pytest.approx(13.5 * 0.92, abs=0.001)  # 12.42
    assert t["exit_evidence"]["trigger"].startswith("盘中最低")


def test_trailing_stop_gap_open():
    """跳空低开：开盘价 ≤ 移动止损价 → 按开盘价成交。"""
    result = run_trail(
        closes=[10.5, 11.0, 12.0, 13.0, 12.2],
        opens=[10.5, 10.5, 11.5, 12.5, 12.0],
        lows=[10.4, 10.4, 11.4, 12.4, 11.8],
        highs=[10.6, 11.2, 12.0, 13.5, 12.5],
    )
    t = result["trades"][0]
    assert t["exit_reason"] == "trailing_stop"
    assert t["exit_price"] == 12.0
    assert t["exit_evidence"]["trigger"].startswith("开盘价")


def test_trailing_stop_no_future_peek():
    """无未来函数：当日盘中新高不参与当日止损判定（用截至昨日的峰值）。"""
    result = run_trail(
        closes=[10.5, 11.0, 19.0, 16.5],
        opens=[10.5, 10.5, 12.0, 17.0],
        lows=[10.4, 10.4, 11.5, 16.0],
        highs=[10.6, 11.2, 20.0, 17.5],
    )
    t = result["trades"][0]
    assert t["exit_reason"] == "trailing_stop"
    assert t["exit_price"] == 17.0  # 次日开盘成交，而非 i2 的 18.4


# ---------- 分批止盈阶梯 ----------


def test_scale_out_ladder():
    """分批止盈：涨10%卖1/3 + 涨20%清仓。"""
    closes = _PRE + [10.5, 11.0, 11.6, 12.8, 12.8]
    opens = _PRE + [10.5, 10.5, 11.5, 11.6, 12.8]
    result = run_pool([
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": ">=", "right": 10}]},
         "action": "sell", "size_pct": 33.33, "max_times": 1, "note": "涨10%卖1/3"},
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": ">=", "right": 20}]},
         "action": "sell", "size_pct": 100, "note": "涨20%清仓"},
    ], _PRE + [10.5, 11.0, 11.6, 12.8, 12.8], opens=opens)
    trades = result["trades"]
    assert len(trades) >= 2  # 卖1/3 + 清仓


# ---------- 独立回测（核心新语义） ----------


def test_independent_accounts():
    """池里 2 票独立账户，互不影响。"""
    closes = [10.0] * 10 + [10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 12.0, 11.5]
    result = run_pool([
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": ">=", "right": 5}]},
         "action": "sell", "size_pct": 100, "note": "涨5%清仓"},
    ], closes, pool_codes=["600001.SH", "600002.SH"])
    assert result["metrics"]["stock_count"] == 2
    assert len(result["per_stock"]) == 2
    # 每票独立：各自有自己的 trades
    for r in result["per_stock"]:
        assert len(r["trades"]) >= 1


def test_no_rotation_after_sell():
    """卖出后不换票：卖出即结束，不再次买入。"""
    closes = [10.0] * 10 + [10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 12.0, 11.5, 11.0, 10.5, 10.0]
    result = run_pool([
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": ">=", "right": 8}]},
         "action": "sell", "size_pct": 100, "note": "涨8%清仓"},
    ], closes)
    # 只买入一次（入场），卖出后不再买入（不轮动）
    buys = [a for d in result["audit"]["daily"] for a in d["actions"] if a["action"] == "buy"]
    assert len(buys) == 1  # 只有初始入场，卖出后不换票
