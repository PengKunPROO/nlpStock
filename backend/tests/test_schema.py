"""Tests for StrategyConfig schema validation (contract §1-2)."""
import pytest
from pydantic import ValidationError

from backend.schema import REFERENCE_STRATEGY, IndicatorSpec, StrategyConfig


def test_reference_strategy_is_valid():
    cfg = StrategyConfig.model_validate(REFERENCE_STRATEGY)
    assert cfg.name == "阴跌急跌·止跌反转·回踩进场"
    assert len(cfg.indicators) == 12
    assert len(cfg.entry.conditions) == 8
    assert cfg.entry.conditions[0].within == 60
    group = cfg.exit.conditions[2]
    assert isinstance(group.conditions[0].right_lag, int) and group.conditions[0].right_lag == 1


def test_reference_strategy_roundtrip_dict():
    cfg = StrategyConfig.model_validate(REFERENCE_STRATEGY)
    again = StrategyConfig.model_validate(cfg.model_dump())
    assert again == cfg


def _mutate(**overrides):
    import copy

    data = copy.deepcopy(REFERENCE_STRATEGY)
    for path, value in overrides.items():
        keys = path.split(".")
        obj = data
        for k in keys[:-1]:
            obj = obj[int(k) if k.isdigit() else k]
        last = keys[-1]
        if value is ...:
            del obj[int(last) if last.isdigit() else last]
        else:
            obj[int(last) if last.isdigit() else last] = value
    return data


@pytest.mark.parametrize(
    "override",
    [
        {"indicators.0.id": "Ma5"},
        {"indicators.0.id": "ma5!", },
        {"indicators.0.n": 1},
        {"indicators.0.n": 251},
        {"indicators.0.n": None},
        {"indicators.4.n": None},
        {"indicators.0.of": "vwap"},
        {"indicators.7.mas": ["ma5"]},
        {"indicators.7.mas": ["ma5", "ma999"]},
        {"entry.conditions.0.left": "unknown_ind"},
        {"entry.conditions.0.right": "unknown_ind"},
        {"entry.conditions.0.op": "!="},
        {"entry.conditions.0.lag": -1},
        {"entry.conditions.0.right_factor": 0},
        {"entry.conditions.0.within": 0},
        {"entry.conditions": []},
        {"universe.type": "custom"},
        {"universe.type": "custom", "universe.code": None, "universe.codes": ["600519"]},
        {"universe.type": "index", "universe.code": "600519"},
        {"universe.type": "galaxy"},
        {"risk.stop_loss_pct": 0.1},
        {"risk.stop_loss_pct": 80},
        {"backtest_defaults.initial_cash": 0},
        {"backtest_defaults.position_pct": 200},
        {"backtest_defaults.start": "2025/01/01"},
        {"indicators": []},
        {"name": ""},
        {"name": "x" * 41},
    ],
)
def test_invalid_variants_rejected(override):
    with pytest.raises(ValidationError):
        StrategyConfig.model_validate(_mutate(**override))


def test_duplicate_indicator_ids_rejected():
    data = _mutate(**{"indicators.1.id": "ma5"})
    with pytest.raises(ValidationError, match="unique"):
        StrategyConfig.model_validate(data)


def test_nested_depth_three_rejected():
    data = _mutate(**{
        "exit.conditions.2.conditions": [
            {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": 1}]}
        ]
    })
    with pytest.raises(ValidationError, match="nesting"):
        StrategyConfig.model_validate(data)


def test_nested_group_two_levels_allowed():
    data = _mutate(**{
        "exit.conditions.2.conditions": [
            {"left": "body", "op": "<", "right": "body", "right_lag": 1},
            {"left": "close", "op": ">", "right": 0},
        ]
    })
    cfg = StrategyConfig.model_validate(data)
    assert len(cfg.exit.conditions) == 5


def test_exit_may_be_empty():
    data = _mutate(**{"exit.conditions": []})
    cfg = StrategyConfig.model_validate(data)
    assert cfg.exit.conditions == []


def test_thscode_formats():
    ok = _mutate(**{"universe.type": "custom", "universe.codes": ["600519.SH", "000001.SZ", "430001.BJ", "886042.TI"]})
    StrategyConfig.model_validate(ok)
    bad = _mutate(**{"universe.type": "custom", "universe.codes": ["600519"]})
    with pytest.raises(ValidationError):
        StrategyConfig.model_validate(bad)


def test_numeric_right_and_string_right():
    data = _mutate(**{"entry.conditions.4.right": 1234.5})
    cfg = StrategyConfig.model_validate(data)
    assert cfg.entry.conditions[4].right == 1234.5
    data2 = _mutate(**{"entry.conditions.0.right": "chg5"})
    cfg2 = StrategyConfig.model_validate(data2)
    assert cfg2.entry.conditions[0].right == "chg5"


# ---------- 新技术指标 kind 校验（EMA/MACD/KDJ/RSI/BOLL） ----------


def test_new_indicator_kinds_valid():
    ok = [
        {"id": "ema12", "kind": "EMA", "of": "close", "n": 12},
        {"id": "dif", "kind": "MACD_DIF", "of": "close", "fast": 12, "slow": 26, "signal": 9},
        {"id": "dea", "kind": "MACD_DEA"},  # 全默认
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
        IndicatorSpec.model_validate(spec)


@pytest.mark.parametrize(
    "spec",
    [
        {"id": "e1", "kind": "EMA", "n": 5},  # 缺 of
        {"id": "e2", "kind": "EMA", "of": "close"},  # 缺 n
        {"id": "e3", "kind": "EMA", "of": "vwap", "n": 5},  # of 非法
        {"id": "e4", "kind": "MACD_DIF", "of": "vwap"},  # of 非法
        {"id": "e5", "kind": "MACD_DIF", "fast": 1},  # fast 越界
        {"id": "e6", "kind": "MACD_DEA", "signal": 251},
        {"id": "e7", "kind": "KDJ_K", "m1": 1},
        {"id": "e8", "kind": "KDJ_J", "m2": 0},
        {"id": "e9", "kind": "RSI", "n": 1},
        {"id": "e10", "kind": "RSI", "of": "vwap"},
        {"id": "e11", "kind": "BOLL_UP", "k": 0},  # k<=0
        {"id": "e12", "kind": "BOLL_MID", "n": 251},
        {"id": "e13", "kind": "BOLL_LOW", "of": "vwap"},
    ],
)
def test_new_indicator_kinds_rejected(spec):
    with pytest.raises(ValidationError):
        IndicatorSpec.model_validate(spec)


def test_macd_cross_strategy_config_validates():
    cfg = StrategyConfig.model_validate({
        "name": "MACD金叉",
        "universe": {"type": "custom", "codes": ["600519.SH"]},
        "indicators": [
            {"id": "dif", "kind": "MACD_DIF", "of": "close", "fast": 12, "slow": 26, "signal": 9},
            {"id": "dea", "kind": "MACD_DEA", "of": "close", "fast": 12, "slow": 26, "signal": 9},
            {"id": "kdjk", "kind": "KDJ_K", "n": 9, "m1": 3, "m2": 3},
            {"id": "kdjd", "kind": "KDJ_D", "n": 9, "m1": 3, "m2": 3},
        ],
        "entry": {"logic": "all", "conditions": [
            {"logic": "all", "conditions": [
                {"left": "dif", "op": ">", "right": "dea"},
                {"left": "dif", "op": "<=", "right": "dea", "lag": 1, "right_lag": 1},
            ], "note": "DIF上穿DEA金叉"},
            {"left": "kdjk", "op": ">", "right": "kdjd", "note": "K在D上方"},
        ]},
        "exit": {"logic": "any", "conditions": [
            {"left": "dif", "op": "<", "right": "dea"},
        ]},
        "risk": {"stop_loss_pct": 8.0, "max_hold_days": 30, "take_profit_pct": None},
        "backtest_defaults": {
            "start": "2025-01-01", "end": "2026-09-18", "initial_cash": 1000000,
            "position_pct": 20, "max_positions": 5, "fee_bps": 2.5, "stamp_tax_bps": 5.0,
        },
    })
    assert len(cfg.indicators) == 4
