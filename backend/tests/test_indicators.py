"""Tests for indicator engine and condition evaluator with hand-computed fixtures."""
import math

from backend.conditions import eval_leaf, signal_at
from backend.indicators import compute_indicators
from backend.schema import ConditionGroup, IndicatorSpec, LeafCondition


def make_bars(rows):
    """rows: (open, high, low, close, volume)"""
    keys = ("open", "high", "low", "close", "volume")
    return [dict(zip(keys, r), date_ms=1_700_000_000_000 + i * 86_400_000) for i, r in enumerate(rows)]


BARS = make_bars([
    (10, 11, 9.5, 10.5, 100),
    (10.5, 10.8, 10, 10.2, 90),
    (10.2, 12.5, 10.1, 12.0, 300),
    (12.0, 12.4, 11.5, 11.8, 150),
    (11.8, 13.0, 11.6, 12.8, 250),
    (12.8, 13.2, 12.2, 12.5, 120),
    (12.5, 12.6, 11.8, 12.0, 110),
    (12.0, 12.3, 11.9, 12.2, 130),
    (12.2, 12.5, 12.0, 12.4, 140),
    (12.4, 12.9, 12.3, 12.8, 280),
])


def approx(a, b, eps=1e-9):
    return a is not None and b is not None and math.isclose(a, b, rel_tol=eps, abs_tol=1e-9)


def test_ma5_hand_computed():
    specs = [IndicatorSpec(id="ma5", kind="MA", of="close", n=5)]
    s = compute_indicators(BARS, specs)
    assert s["ma5"][3] is None
    assert approx(s["ma5"][4], (10.5 + 10.2 + 12.0 + 11.8 + 12.8) / 5)
    assert approx(s["ma5"][9], (12.8 + 12.5 + 12.0 + 12.2 + 12.4) / 5)


def test_pct_change():
    specs = [IndicatorSpec(id="chg5", kind="PCT_CHANGE", of="close", n=5)]
    s = compute_indicators(BARS, specs)
    assert s["chg5"][4] is None
    assert approx(s["chg5"][5], (12.5 / 10.5 - 1) * 100)


def test_vratio():
    specs = [IndicatorSpec(id="vr", kind="VRATIO", n=5)]
    s = compute_indicators(BARS, specs)
    vols = [100, 90, 300, 150, 250, 120, 110, 130, 140, 280]
    assert s["vr"][4] is None  # needs 5 prior bars (exclusive of current)
    base5 = sum(vols[0:5]) / 5
    assert s["vr"][5] == 120 / base5
    base9 = sum(vols[4:9]) / 5
    assert s["vr"][9] == 280 / base9
    assert s["vr"][0] is None


def test_body_and_shadow():
    specs = [IndicatorSpec(id="body", kind="BODY_RATIO"), IndicatorSpec(id="ush", kind="UPPER_SHADOW_RATIO")]
    s = compute_indicators(BARS, specs)
    # bar 3: open 12.0, close 11.8, high 12.4, low 11.5 → body=(11.8-12)/(12.4-11.5)
    assert approx(s["body"][3], (11.8 - 12.0) / (12.4 - 11.5))
    assert approx(s["ush"][3], (12.4 - max(12.0, 11.8)) / (12.4 - 11.5))
    assert s["body"][0] == (10.5 - 10) / (11 - 9.5)


def test_box_top_excludes_current():
    specs = [IndicatorSpec(id="box5", kind="BOX_TOP", n=5)]
    s = compute_indicators(BARS, specs)
    assert s["box5"][4] is None  # needs 5 prior bars (i>=n)
    assert approx(s["box5"][5], max(b["high"] for b in BARS[0:5]))  # bars 0-4, excludes bar 5
    assert approx(s["box5"][9], max(b["high"] for b in BARS[4:9]))


def test_ma_converge():
    specs = [
        IndicatorSpec(id="ma2a", kind="MA", of="close", n=2),
        IndicatorSpec(id="ma2b", kind="MA", of="close", n=3),
        IndicatorSpec(id="conv", kind="MA_CONVERGE", mas=["ma2a", "ma2b"]),
    ]
    s = compute_indicators(BARS, specs)
    assert s["conv"][1] is None  # ma2b needs 3 bars
    ma2 = s["ma2a"][2]
    ma3 = s["ma2b"][2]
    close = BARS[2]["close"]
    assert approx(s["conv"][2], (max(ma2, ma3) - min(ma2, ma3)) / close * 100)


def test_condition_compare_and_factor():
    series = {"close": [10, 11, 12], "ma20": [11, 11, 11]}
    c = LeafCondition(left="close", op=">", right="ma20")
    assert eval_leaf(c, series, 0) is False
    assert eval_leaf(c, series, 2) is True
    cf = LeafCondition(left="close", op=">=", right="ma20", right_factor=0.95)
    assert eval_leaf(cf, series, 0) is False  # 10 >= 11*0.95=10.45 → False
    assert eval_leaf(cf, series, 1) is True  # 11 >= 10.45


def test_condition_lag_and_right_lag():
    series = {"close": [10, 20, 30, 25]}
    up = LeafCondition(left="close", op=">", right="close", right_lag=1)
    assert eval_leaf(up, series, 0) is False  # no prior bar
    assert eval_leaf(up, series, 1) is True  # 20 > 10
    assert eval_leaf(up, series, 3) is False  # 25 < 30
    lag_left = LeafCondition(left="close", op=">", right=5, lag=2)
    assert eval_leaf(lag_left, series, 1) is False  # index -1 → None → False
    assert eval_leaf(lag_left, series, 2) is True  # close[0]=10 > 5


def test_within_lookback():
    series = {"close": [5, 5, 5, 5, 20, 5], "thr": [10, 10, 10, 10, 10, 10]}
    c = LeafCondition(left="close", op=">", right="thr", within=3)
    assert signal_at(ConditionGroup(logic="all", conditions=[c]), series, 5) is True  # spike at i=4 within 3
    c2 = LeafCondition(left="close", op=">", right="thr", within=1)
    assert signal_at(ConditionGroup(logic="all", conditions=[c2]), series, 5) is False
    c3 = LeafCondition(left="close", op=">", right="thr", within=2)
    assert signal_at(ConditionGroup(logic="all", conditions=[c3]), series, 5) is True


def test_within_bounded_at_array_start():
    series = {"close": [20, 5, 5], "thr": [10, 10, 10]}
    c = LeafCondition(left="close", op=">", right="thr", within=5)
    assert signal_at(ConditionGroup(logic="all", conditions=[c]), series, 2) is True


def test_group_logic_all_any():
    series = {"close": [10], "ma5": [9], "ma10": [11]}
    a = LeafCondition(left="close", op=">", right="ma5")
    b = LeafCondition(left="close", op=">", right="ma10")
    assert signal_at(ConditionGroup(logic="all", conditions=[a, b]), series, 0) is False
    assert signal_at(ConditionGroup(logic="any", conditions=[a, b]), series, 0) is True


def test_nested_group():
    series = {"close": [10], "body": [0.7, 0.5]}
    inner = ConditionGroup(
        logic="all",
        conditions=[
            LeafCondition(left="body", op="<", right="body", right_lag=1),
            LeafCondition(left="body", op=">", right=0),
        ],
    )
    assert signal_at(ConditionGroup(logic="any", conditions=[inner]), series, 1) is True
    assert signal_at(ConditionGroup(logic="any", conditions=[inner]), series, 0) is False


def test_none_values_are_false():
    series = {"close": [10, None, 12]}
    c = LeafCondition(left="close", op=">", right=0)
    assert eval_leaf(c, series, 1) is False


def test_full_reference_strategy_pipeline_on_synthetic_bars():
    """Reference strategy entry/exit evaluate without error on synthetic data."""
    import copy

    from backend.schema import REFERENCE_STRATEGY, StrategyConfig

    cfg = StrategyConfig.model_validate(copy.deepcopy(REFERENCE_STRATEGY))
    specs = [IndicatorSpec.model_validate(i) for i in cfg.model_dump()["indicators"]]
    bars = make_bars([(10 + i * 0.1, 11 + i * 0.1, 9.9 + i * 0.1, 10.5 + i * 0.1, 100 + i * 37) for i in range(120)])
    bars += make_bars([(20 - i * 0.2, 20.5 - i * 0.2, 18 - i * 0.2, 19 - i * 0.2, 90 + i * 5) for i in range(30)])
    series = compute_indicators(bars, specs)
    entry = ConditionGroup.model_validate(cfg.model_dump()["entry"])
    exit_ = ConditionGroup.model_validate(cfg.model_dump()["exit"])
    for i in range(0, len(bars), 7):
        signal_at(entry, series, i)
        signal_at(exit_, series, i)


# ---------- 新技术指标（EMA / MACD / KDJ / RSI / BOLL，A 股口径） ----------


def closes_bars(closes):
    return make_bars([(c, c + 0.1, c - 0.1, c, 100.0) for c in closes])


def test_ema_hand_computed():
    specs = [IndicatorSpec(id="e", kind="EMA", of="close", n=3)]
    s = compute_indicators(closes_bars([10, 11, 12, 13, 14]), specs)
    assert s["e"][0] is None and s["e"][1] is None
    assert approx(s["e"][2], 11.0)  # 种子 = SMA(3)
    assert approx(s["e"][3], 12.0)  # α=0.5: 0.5*13 + 0.5*11
    assert approx(s["e"][4], 13.0)


def test_macd_hand_computed():
    bars = closes_bars([10, 11, 12, 14])
    common = dict(of="close", fast=2, slow=3, signal=2)
    specs = [
        IndicatorSpec(id="dif", kind="MACD_DIF", **common),
        IndicatorSpec(id="dea", kind="MACD_DEA", **common),
        IndicatorSpec(id="hist", kind="MACD_HIST", **common),
    ]
    s = compute_indicators(bars, specs)
    assert s["dif"][0] is None and s["dif"][1] is None
    assert approx(s["dif"][2], 0.5)  # EMA2=11.5 - EMA3=11
    assert approx(s["dif"][3], 2 / 3)  # 79/6 - 25/2
    assert s["dea"][2] is None
    assert approx(s["dea"][3], 7 / 12)  # (0.5 + 2/3) / 2
    assert s["hist"][2] is None
    assert approx(s["hist"][3], 1 / 6)  # 2 * (2/3 - 7/12)


def test_macd_hist_is_a_share_convention():
    bars = closes_bars([10, 11, 12, 14, 15, 13, 12, 14, 16, 18])
    common = dict(fast=3, slow=6, signal=3)
    specs = [
        IndicatorSpec(id="dif", kind="MACD_DIF", **common),
        IndicatorSpec(id="dea", kind="MACD_DEA", **common),
        IndicatorSpec(id="hist", kind="MACD_HIST", **common),
    ]
    s = compute_indicators(bars, specs)
    checked = 0
    for i in range(len(bars)):
        if s["dif"][i] is not None and s["dea"][i] is not None:
            assert approx(s["hist"][i], 2 * (s["dif"][i] - s["dea"][i]))
            checked += 1
    assert checked > 0


def test_macd_default_params():
    closes = [10 + 0.5 * i for i in range(40)]
    a = compute_indicators(closes_bars(closes), [IndicatorSpec(id="d1", kind="MACD_DIF")])
    b = compute_indicators(closes_bars(closes), [IndicatorSpec(id="d2", kind="MACD_DIF", of="close", fast=12, slow=26, signal=9)])
    assert a["d1"] == b["d2"]
    assert a["d1"][24] is None and a["d1"][25] is not None  # slow-1=25 起有值


def test_kdj_hand_computed():
    rows = [(10, 12, 9, 11, 100), (11, 13, 10, 12, 100), (12, 14, 11, 13, 100), (13, 15, 12, 14, 100)]
    specs = [
        IndicatorSpec(id="kdjk", kind="KDJ_K", n=3, m1=3, m2=3),
        IndicatorSpec(id="kdjd", kind="KDJ_D", n=3, m1=3, m2=3),
        IndicatorSpec(id="kdjj", kind="KDJ_J", n=3, m1=3, m2=3),
    ]
    s = compute_indicators(make_bars(rows), specs)
    assert s["kdjk"][0] is None and s["kdjk"][1] is None
    assert approx(s["kdjk"][2], 60.0)  # K=(80+2*50)/3
    assert approx(s["kdjd"][2], 160 / 3)
    assert approx(s["kdjj"][2], 220 / 3)
    assert approx(s["kdjk"][3], 200 / 3)  # K=(80+2*60)/3
    assert approx(s["kdjd"][3], 520 / 9)
    assert approx(s["kdjj"][3], 760 / 9)


def test_kdj_flat_window_rsv_50():
    # HH==LL（无波动）→ RSV=50 → K=D=50, J=50
    rows = [(10, 10, 10, 10, 100)] * 3
    specs = [IndicatorSpec(id="kdjk", kind="KDJ_K", n=3)]
    s = compute_indicators(make_bars(rows), specs)
    assert approx(s["kdjk"][2], 50.0)


def test_kdj_default_params():
    rows = [(10 + i, 11 + i, 9 + i, 10 + i, 100) for i in range(12)]
    a = compute_indicators(make_bars(rows), [IndicatorSpec(id="k1", kind="KDJ_K")])
    b = compute_indicators(make_bars(rows), [IndicatorSpec(id="k2", kind="KDJ_K", n=9, m1=3, m2=3)])
    assert a["k1"] == b["k2"]
    assert a["k1"][7] is None and a["k1"][8] is not None  # n-1=8 起有值


def test_rsi_hand_computed():
    specs = [IndicatorSpec(id="r", kind="RSI", of="close", n=2)]
    s = compute_indicators(closes_bars([10, 11, 10, 12, 13]), specs)
    assert s["r"][0] is None and s["r"][1] is None
    assert approx(s["r"][2], 50.0)  # 种子 gain=loss=0.5
    assert approx(s["r"][3], 250 / 3)  # 1.25/1.5*100
    assert approx(s["r"][4], 90.0)  # 1.125/1.25*100


def test_rsi_default_n_is_6():
    closes = [10 + (i % 5) for i in range(15)]
    a = compute_indicators(closes_bars(closes), [IndicatorSpec(id="r1", kind="RSI")])
    b = compute_indicators(closes_bars(closes), [IndicatorSpec(id="r2", kind="RSI", of="close", n=6)])
    assert a["r1"] == b["r2"]


def test_boll_hand_computed():
    common = dict(of="close", n=3, k=2.0)
    specs = [
        IndicatorSpec(id="bu", kind="BOLL_UP", **common),
        IndicatorSpec(id="bm", kind="BOLL_MID", **common),
        IndicatorSpec(id="bl", kind="BOLL_LOW", **common),
    ]
    s = compute_indicators(closes_bars([10, 11, 12, 13]), specs)
    assert s["bm"][0] is None and s["bm"][1] is None
    assert approx(s["bm"][2], 11.0) and approx(s["bu"][2], 13.0) and approx(s["bl"][2], 9.0)
    assert approx(s["bm"][3], 12.0) and approx(s["bu"][3], 14.0) and approx(s["bl"][3], 10.0)


def test_boll_default_params():
    closes = [10 + 0.3 * i for i in range(25)]
    a = compute_indicators(closes_bars(closes), [IndicatorSpec(id="u1", kind="BOLL_UP")])
    b = compute_indicators(closes_bars(closes), [IndicatorSpec(id="u2", kind="BOLL_UP", of="close", n=20, k=2.0)])
    assert a["u1"] == b["u2"]


def test_macd_golden_cross_condition_evaluates():
    # 金叉 = 今日 DIF>DEA 且 昨日 DIF<=DEA（lag 语义现有引擎已支持）
    series = {
        "dif": [-1.0, -0.5, -0.2, 0.1, 0.3, 0.5],
        "dea": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    }
    cross = ConditionGroup.model_validate({"logic": "all", "conditions": [
        {"left": "dif", "op": ">", "right": "dea"},
        {"left": "dif", "op": "<=", "right": "dea", "lag": 1, "right_lag": 1},
    ]})
    assert signal_at(cross, series, 3) is True  # 0.1>0 且 昨日 -0.2<=0
    assert signal_at(cross, series, 4) is False  # 昨日已金叉，非新交叉
    assert signal_at(cross, series, 2) is False  # 今日都不满足
