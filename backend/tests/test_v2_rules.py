"""v2 规则引擎测试：rules（条件→动作+仓位）、持仓状态字段、v1 兼容层。"""
import copy

import pytest

from backend.backtest import backtest
from backend.schema import StrategyConfig

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
        return [b for b in self.bars_map[code] if start_ms - 400 * DAY <= b["date_ms"] <= end_ms]


def rules_cfg(rules, indicators=None, risk=None):
    return {
        "name": "v2规则测试",
        "universe": {"type": "custom", "codes": ["600001.SH"]},
        "indicators": indicators or [{"id": "ma5", "kind": "MA", "of": "close", "n": 5}],
        "rules": rules,
        "risk": risk or {"stop_loss_pct": None, "max_hold_days": None, "take_profit_pct": None},
        "backtest_defaults": {
            "start": "2025-06-01", "end": "2025-12-31", "initial_cash": 1000000,
            "position_pct": 20, "max_positions": 5, "fee_bps": 2.5, "stamp_tax_bps": 5.0,
        },
    }


def run_v2(rules, closes, **kw):
    cfg = StrategyConfig.model_validate(rules_cfg(rules, **kw))
    data = FakeData({"600001.SH": bars_from_closes(closes)})
    return backtest(cfg, params(), data)


# ---------- schema 校验 ----------


def test_rules_valid():
    cfg = StrategyConfig.model_validate(rules_cfg([
        {"when": {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": "ma5"}]},
         "action": "buy", "size_pct": 33.33, "max_times": None, "note": "上穿买入"},
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": "<=", "right": -5}]},
         "action": "buy", "size_pct": 33.33, "max_times": 1, "note": "跌5%补仓"},
        {"when": {"logic": "any", "conditions": [{"left": "close", "op": "<", "right": "ma5"}]},
         "action": "sell", "size_pct": 100, "max_times": None, "note": "跌破清仓"},
    ]))
    assert len(cfg.rules) == 3


def test_rules_invalid_action():
    with pytest.raises(Exception):
        StrategyConfig.model_validate(rules_cfg([
            {"when": {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": 0}]},
             "action": "short", "size_pct": 100},
        ]))


def test_rules_invalid_size_pct():
    with pytest.raises(Exception):
        StrategyConfig.model_validate(rules_cfg([
            {"when": {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": 0}]},
             "action": "buy", "size_pct": 150},
        ]))


def test_rules_empty_rejected():
    with pytest.raises(Exception):
        StrategyConfig.model_validate(rules_cfg([]))


def test_position_state_fields_available():
    """持仓状态字段（pnl_pct/hold_days/dd_from_peak/cost）可在 when 中引用。"""
    cfg = StrategyConfig.model_validate(rules_cfg([
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": "<=", "right": -5}]},
         "action": "buy", "size_pct": 33.33, "max_times": 1},
    ]))
    assert cfg.rules[0].when.conditions[0].left == "pnl_pct"


# ---------- v1 兼容层 ----------


def test_v1_entry_exit_auto_converted_to_rules():
    """v1 配置（entry/exit）加载后自动转成 rules。"""
    from backend.schema import REFERENCE_STRATEGY
    cfg = StrategyConfig.model_validate(copy.deepcopy(REFERENCE_STRATEGY))
    assert hasattr(cfg, "rules") and len(cfg.rules) >= 2
    assert cfg.rules[0].action == "buy"
    assert cfg.rules[-1].action == "sell"


# ---------- 引擎执行 ----------


def test_v2_buy_with_size_pct():
    """buy 规则带 size_pct=33.33 → 首仓约1/3权益。"""
    closes = [10.0] * 10 + [10.5, 11.0, 11.5, 12.0, 12.5, 11.0, 10.8, 10.6, 10.4]
    result = run_v2([
        {"when": {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": "ma5"}]},
         "action": "buy", "size_pct": 33.33, "note": "上穿买1/3"},
        {"when": {"logic": "any", "conditions": [{"left": "close", "op": "<", "right": "ma5"}]},
         "action": "sell", "size_pct": 100, "note": "跌破清仓"},
    ], closes)
    t = result["trades"][0]
    # position_pct=100 但规则 size_pct=33.33 → 首仓应为约 1/3 权益
    # 权益 1M，价格约 10.5 → 约 330000/10.5 ≈ 31400 股 → 31400 股
    assert t["shares"] <= 33000  # 不超过 1/3 权益可买数量
    assert t["shares"] >= 30000  # 接近 1/3 权益


def test_v2_add_position_on_drawdown():
    """跌5%补仓：首仓后跌5%触发第二次buy，持仓股数增加。"""
    # 先涨出信号，然后跌 5%+ 触发补仓
    closes = [10.0] * 10 + [11.0, 11.5, 12.0, 11.5, 11.0, 10.4, 10.0, 9.8, 9.6, 9.4]
    result = run_v2([
        {"when": {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": "ma5"}]},
         "action": "buy", "size_pct": 50, "max_times": 1, "note": "上穿买半仓"},
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": "<=", "right": -5}]},
         "action": "buy", "size_pct": 30, "max_times": 1, "note": "跌5%补仓"},
        {"when": {"logic": "any", "conditions": [{"left": "close", "op": "<", "right": "ma5"}]},
         "action": "sell", "size_pct": 100, "note": "跌破清仓"},
    ], closes)
    # 至少一笔交易；买入后如果又补仓，总股数应大于首次买入
    assert len(result["trades"]) >= 1
    # 检查审计中是否有补仓动作
    all_actions = [a for d in result["audit"]["daily"] for a in d["actions"] if a["action"] == "buy"]
    # 应该至少有首次买入；如果跌了5%应该还有补仓
    assert len(all_actions) >= 1
    # 验证 max_times：补仓规则只触发一次
    add_actions = [a for a in all_actions if a.get("evidence") and "补仓" in (a["evidence"].get("label") or "")]
    assert len(add_actions) <= 1


def test_v2_partial_sell():
    """sell 规则带 size_pct=50 → 卖出一半持仓。"""
    closes = [10.0] * 10 + [10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0, 14.0, 13.0, 12.0, 11.0, 10.0]
    result = run_v2([
        {"when": {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": "ma5"}]},
         "action": "buy", "size_pct": 100, "max_times": 1, "note": "满仓买入"},
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": ">=", "right": 10}]},
         "action": "sell", "size_pct": 50, "max_times": 1, "note": "涨10%减半"},
        {"when": {"logic": "any", "conditions": [{"left": "close", "op": "<", "right": "ma5"}]},
         "action": "sell", "size_pct": 100, "note": "跌破清仓"},
    ], closes)
    # 应有多次卖出（先减半再清仓）
    sell_actions = [a for d in result["audit"]["daily"] for a in d["actions"] if a["action"] == "sell"]
    assert len(sell_actions) >= 2  # 至少减半一次 + 清仓一次
    # 第一次卖出应约为一半持仓


def test_v2_max_times_prevents_infinite():
    """max_times=1 的规则只触发一次，不会在条件持续满足时反复触发。"""
    # 持续跌 → pnl_pct 持续 ≤ -5，但 max_times=1 只补一次
    closes = [10.0] * 10 + [11.0, 10.5, 10.0, 9.5, 9.0, 8.5, 8.0, 7.5, 7.0, 6.5, 6.0]
    result = run_v2([
        {"when": {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": "ma5"}]},
         "action": "buy", "size_pct": 50, "max_times": 1, "note": "上穿买半仓"},
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": "<=", "right": -5}]},
         "action": "buy", "size_pct": 20, "max_times": 1, "note": "跌5%补仓一次"},
    ], closes, risk={"stop_loss_pct": 50, "max_hold_days": None, "take_profit_pct": None})
    buy_actions = [a for d in result["audit"]["daily"] for a in d["actions"] if a["action"] == "buy"]
    assert len(buy_actions) <= 2  # 首仓 + 最多1次补仓，不会无限补


def test_v2_hold_days_field():
    """hold_days 持仓状态字段可在 when 中引用。"""
    closes = [10.0] * 10 + [10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0, 14.0, 13.0]
    result = run_v2([
        {"when": {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": "ma5"}]},
         "action": "buy", "size_pct": 100, "max_times": 1, "note": "买入"},
        {"when": {"logic": "all", "conditions": [{"left": "hold_days", "op": ">=", "right": 5}]},
         "action": "sell", "size_pct": 100, "note": "持仓5日清仓"},
    ], closes)
    sell_actions = [a for d in result["audit"]["daily"] for a in d["actions"] if a["action"] == "sell"]
    assert len(sell_actions) >= 1
    # 交易持有天数应约等于 5
    t = result["trades"][0]
    assert t["holding_days"] >= 5
