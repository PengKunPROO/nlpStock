"""Tests for TradingStrategy schema validation."""
import copy

import pytest
from pydantic import ValidationError

from backend.schema import TradingStrategy


def _apply(data, **overrides):
    out = copy.deepcopy(data)
    for path, value in overrides.items():
        keys = path.split(".")
        obj = out
        for k in keys[:-1]:
            obj = obj[int(k) if k.isdigit() else k]
        last = keys[-1]
        obj[int(last) if last.isdigit() else last] = value
    return out


def _cfg(**overrides):
    base = {
        "name": "交易策略",
        "indicators": [
            {"id": "ma5", "kind": "MA", "of": "close", "n": 5},
        ],
        "rules": [
            {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": "<=", "right": -5}]}, "action": "buy", "note": "补仓"},
            {"when": {"logic": "any", "conditions": [{"left": "close", "op": "<", "right": "ma5"}]}, "action": "sell", "size_pct": 100},
        ],
        "risk": {"stop_loss_pct": 8.0, "max_hold_days": 30},
        "backtest_defaults": {
            "start": "2025-01-01", "end": "2025-12-31", "initial_cash": 1000000,
            "position_pct": 20, "max_positions": 5, "fee_bps": 2.5, "stamp_tax_bps": 5.0,
        },
    }
    return _apply(base, **overrides)


# ---------- 合法样例 ----------


def test_valid_trading_strategy():
    cfg = TradingStrategy.parse_obj(_cfg())
    assert cfg.name == "交易策略"
    assert len(cfg.rules) == 2
    assert cfg.rules[0].action == "buy"
    assert cfg.rules[1].action == "sell"


def test_valid_trading_roundtrip_dict():
    cfg = TradingStrategy.parse_obj(_cfg())
    again = TradingStrategy.parse_obj(cfg.dict())
    assert again == cfg


def test_default_risk_config():
    cfg = TradingStrategy.parse_obj(_cfg(risk={"stop_loss_pct": 8.0}))
    assert cfg.risk.trailing_stop_pct is None
    assert cfg.risk.max_hold_days is None


# ---------- buy 规则必须引用持仓字段 ----------


@pytest.mark.parametrize("field", ["cost", "pnl_pct", "hold_days", "dd_from_peak"])
def test_buy_rule_references_hold_field_valid(field):
    cfg = TradingStrategy.parse_obj(_cfg(**{"rules.0.when.conditions.0.left": field}))
    assert cfg.rules[0].action == "buy"


def test_buy_rule_without_hold_field_rejected():
    with pytest.raises(ValidationError, match="hold"):
        TradingStrategy.parse_obj(_cfg(**{"rules.0.when.conditions.0.left": "close"}))


def test_buy_rule_nested_hold_field_valid():
    data = _cfg(**{"rules.0.when": {"logic": "all", "conditions": [
        {"logic": "any", "conditions": [{"left": "pnl_pct", "op": "<=", "right": -5}]},
        {"left": "close", "op": ">", "right": "ma5"},
    ]}})
    cfg = TradingStrategy.parse_obj(data)
    assert cfg.rules[0].action == "buy"


# ---------- rules 结构校验 ----------


def test_empty_rules_rejected():
    with pytest.raises(ValidationError):
        TradingStrategy.parse_obj(_cfg(rules=[]))


def test_missing_rules_rejected():
    data = _cfg()
    del data["rules"]
    with pytest.raises(ValidationError):
        TradingStrategy.parse_obj(data)


def test_invalid_action_rejected():
    with pytest.raises(ValidationError):
        TradingStrategy.parse_obj(_cfg(**{"rules.0.action": "hold"}))


# ---------- 指标/条件引用校验 ----------


def test_indicator_of_cycle_rejected():
    with pytest.raises(ValidationError, match="cycle"):
        TradingStrategy.parse_obj(_cfg(indicators=[
            {"id": "a", "kind": "MA", "of": "b", "n": 5},
            {"id": "b", "kind": "MA", "of": "a", "n": 5},
        ]))


def test_condition_unknown_ref_rejected():
    with pytest.raises(ValidationError, match="unknown"):
        TradingStrategy.parse_obj(_cfg(**{"rules.1.when.conditions.0.left": "unknown_ind"}))


def test_duplicate_indicator_ids_rejected():
    with pytest.raises(ValidationError, match="unique"):
        TradingStrategy.parse_obj(_cfg(indicators=[
            {"id": "ma5", "kind": "MA", "of": "close", "n": 5},
            {"id": "ma5", "kind": "MA", "of": "close", "n": 10},
        ]))


# ---------- 风控 / 回测默认 ----------


def test_invalid_risk_rejected():
    with pytest.raises(ValidationError):
        TradingStrategy.parse_obj(_cfg(**{"risk.stop_loss_pct": 0.1}))
    with pytest.raises(ValidationError):
        TradingStrategy.parse_obj(_cfg(**{"risk.stop_loss_pct": 80}))


def test_missing_backtest_defaults_rejected():
    data = _cfg()
    del data["backtest_defaults"]
    with pytest.raises(ValidationError):
        TradingStrategy.parse_obj(data)


def test_invalid_backtest_start_date_rejected():
    with pytest.raises(ValidationError):
        TradingStrategy.parse_obj(_cfg(**{"backtest_defaults.start": "2025/01/01"}))
