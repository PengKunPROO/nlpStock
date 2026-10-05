"""Tests for ScreeningStrategy schema validation."""
import copy

import pytest
from pydantic import ValidationError

from backend.schema import IndicatorSpec, ScreeningStrategy


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
        "name": "选股策略",
        "universe": {"type": "index", "code": "000300.SH"},
        "indicators": [
            {"id": "ma5", "kind": "MA", "of": "close", "n": 5},
            {"id": "chg5", "kind": "PCT_CHANGE", "of": "close", "n": 5},
        ],
        "entry": {
            "logic": "all",
            "conditions": [
                {"left": "close", "op": ">", "right": "ma5"},
                {"left": "chg5", "op": ">=", "right": 3, "within": 10},
            ],
        },
    }
    return _apply(base, **overrides)


# ---------- 合法样例 ----------


def test_valid_screening_strategy():
    cfg = ScreeningStrategy.parse_obj(_cfg())
    assert cfg.name == "选股策略"
    assert len(cfg.indicators) == 2
    assert len(cfg.entry.conditions) == 2


def test_valid_screening_roundtrip_dict():
    cfg = ScreeningStrategy.parse_obj(_cfg())
    again = ScreeningStrategy.parse_obj(cfg.dict())
    assert again == cfg


# ---------- entry 不可引用持仓字段 ----------


@pytest.mark.parametrize("field", ["cost", "pnl_pct", "hold_days", "dd_from_peak"])
def test_entry_left_references_hold_field_rejected(field):
    with pytest.raises(ValidationError, match="hold"):
        ScreeningStrategy.parse_obj(_cfg(**{"entry.conditions.0.left": field}))


@pytest.mark.parametrize("field", ["cost", "pnl_pct", "hold_days", "dd_from_peak"])
def test_entry_right_references_hold_field_rejected(field):
    with pytest.raises(ValidationError, match="hold"):
        ScreeningStrategy.parse_obj(_cfg(**{"entry.conditions.0.right": field}))


def test_entry_nested_hold_field_rejected():
    data = _cfg(**{"entry": {"logic": "all", "conditions": [
        {"logic": "any", "conditions": [{"left": "pnl_pct", "op": "<=", "right": -5}]},
    ]}})
    with pytest.raises(ValidationError, match="hold"):
        ScreeningStrategy.parse_obj(data)


# ---------- name / 结构校验 ----------


@pytest.mark.parametrize("name", ["", "   ", "x" * 41])
def test_invalid_name_rejected(name):
    with pytest.raises(ValidationError):
        ScreeningStrategy.parse_obj(_cfg(name=name))


def test_empty_indicators_rejected():
    with pytest.raises(ValidationError):
        ScreeningStrategy.parse_obj(_cfg(indicators=[]))


def test_empty_entry_rejected():
    with pytest.raises(ValidationError):
        ScreeningStrategy.parse_obj(_cfg(**{"entry.conditions": []}))


def test_missing_entry_rejected():
    data = _cfg()
    del data["entry"]
    with pytest.raises(ValidationError):
        ScreeningStrategy.parse_obj(data)


def test_missing_universe_rejected():
    data = _cfg()
    del data["universe"]
    with pytest.raises(ValidationError):
        ScreeningStrategy.parse_obj(data)


# ---------- 指标引用校验（id 唯一 / 可解析 / 无循环） ----------


def test_duplicate_indicator_ids_rejected():
    with pytest.raises(ValidationError, match="unique"):
        ScreeningStrategy.parse_obj(_cfg(**{"indicators.1.id": "ma5"}))


def test_indicator_of_unknown_rejected():
    with pytest.raises(ValidationError, match="unknown"):
        ScreeningStrategy.parse_obj(_cfg(indicators=[{"id": "a", "kind": "MA", "of": "notexist", "n": 5}]))


def test_indicator_of_self_rejected():
    with pytest.raises(ValidationError, match="cycle"):
        ScreeningStrategy.parse_obj(_cfg(indicators=[{"id": "a", "kind": "MA", "of": "a", "n": 5}]))


def test_indicator_of_cycle_rejected():
    with pytest.raises(ValidationError, match="cycle"):
        ScreeningStrategy.parse_obj(_cfg(indicators=[
            {"id": "a", "kind": "MA", "of": "b", "n": 5},
            {"id": "b", "kind": "MA", "of": "a", "n": 5},
        ]))


def test_indicator_of_indicator_valid():
    cfg = ScreeningStrategy.parse_obj(_cfg(
        indicators=[
            {"id": "dif", "kind": "MACD_DIF", "of": "close", "fast": 12, "slow": 26, "signal": 9},
            {"id": "difma5", "kind": "MA", "of": "dif", "n": 5},
        ],
        **{"entry": {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": "difma5"}]}},
    ))
    assert cfg.indicators[1].of == "dif"


# ---------- 条件引用校验 ----------


def test_condition_unknown_left_rejected():
    with pytest.raises(ValidationError, match="unknown"):
        ScreeningStrategy.parse_obj(_cfg(**{"entry.conditions.0.left": "unknown_ind"}))


def test_condition_unknown_right_rejected():
    with pytest.raises(ValidationError, match="unknown"):
        ScreeningStrategy.parse_obj(_cfg(**{"entry.conditions.0.right": "unknown_ind"}))


def test_nested_depth_three_rejected():
    data = _cfg(**{"entry": {"logic": "all", "conditions": [
        {"logic": "all", "conditions": [
            {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": 0}]},
        ]},
    ]}})
    with pytest.raises(ValidationError, match="nesting"):
        ScreeningStrategy.parse_obj(data)


def test_nested_group_two_levels_allowed():
    data = _cfg(**{"entry": {"logic": "all", "conditions": [
        {"logic": "all", "conditions": [
            {"left": "close", "op": ">", "right": "ma5"},
            {"left": "chg5", "op": ">=", "right": 3},
        ]},
    ]}})
    cfg = ScreeningStrategy.parse_obj(data)
    assert isinstance(cfg.entry.conditions[0].conditions[0].right, str)


# ---------- universe 校验 ----------


def test_universe_thscode_validation():
    ok = _cfg(**{"universe.type": "custom", "universe.codes": ["600519.SH", "000001.SZ", "430001.BJ", "886042.TI"]})
    ScreeningStrategy.parse_obj(ok)
    bad = _cfg(**{"universe.type": "custom", "universe.codes": ["600519"]})
    with pytest.raises(ValidationError):
        ScreeningStrategy.parse_obj(bad)


# ---------- IndicatorSpec 新指标 kind 校验 ----------


def test_new_indicator_kinds_valid():
    ok = [
        {"id": "ema12", "kind": "EMA", "of": "close", "n": 12},
        {"id": "dif", "kind": "MACD_DIF", "of": "close", "fast": 12, "slow": 26, "signal": 9},
        {"id": "dea", "kind": "MACD_DEA"},
        {"id": "hist", "kind": "MACD_HIST", "fast": 6, "slow": 13, "signal": 5},
        {"id": "kdjk", "kind": "KDJ_K", "n": 9, "m1": 3, "m2": 3},
        {"id": "kdjd", "kind": "KDJ_D"},
        {"id": "kdjj", "kind": "KDJ_J", "n": 5},
        {"id": "rsi6", "kind": "RSI", "of": "close", "n": 6},
        {"id": "rsi14", "kind": "RSI", "n": 14},
        {"id": "boll_up", "kind": "BOLL_UP", "of": "close", "n": 20, "k": 2.0},
        {"id": "boll_mid", "kind": "BOLL_MID"},
        {"id": "boll_low", "kind": "BOLL_LOW", "n": 26, "k": 2.5},
    ]
    for spec in ok:
        IndicatorSpec.parse_obj(spec)


@pytest.mark.parametrize(
    "spec",
    [
        {"id": "e1", "kind": "EMA", "n": 5},
        {"id": "e2", "kind": "EMA", "of": "close"},
        {"id": "e3", "kind": "EMA", "of": "VWAP", "n": 5},
        {"id": "e4", "kind": "MACD_DIF", "of": "VWAP"},
        {"id": "e5", "kind": "MACD_DIF", "fast": 1},
        {"id": "e6", "kind": "MACD_DEA", "signal": 251},
        {"id": "e7", "kind": "KDJ_K", "m1": 1},
        {"id": "e8", "kind": "KDJ_J", "m2": 0},
        {"id": "e9", "kind": "RSI", "n": 1},
        {"id": "e10", "kind": "RSI", "of": "VWAP"},
        {"id": "e11", "kind": "BOLL_UP", "k": 0},
        {"id": "e12", "kind": "BOLL_MID", "n": 251},
        {"id": "e13", "kind": "BOLL_LOW", "of": "VWAP"},
    ],
)
def test_new_indicator_kinds_rejected(spec):
    with pytest.raises(ValidationError):
        IndicatorSpec.parse_obj(spec)
