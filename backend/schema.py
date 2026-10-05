"""Quantified strategy pydantic schema — ScreeningStrategy (选股) 与 TradingStrategy (交易) (docs/api-contract.md §1-2)."""
from __future__ import annotations

import re
from typing import Literal, Union

from pydantic import BaseModel, Field, conlist, root_validator, validator

BASE_FIELDS = {"open", "high", "low", "close", "volume"}
HOLD_FIELDS = {"cost", "pnl_pct", "hold_days", "dd_from_peak"}  # 持仓状态字段（内置，无需声明）
_OPS = (">", ">=", "<", "<=", "==")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,19}$")
_THSCODE_RE = re.compile(r"^[0-9A-Z]{4,6}\.(SH|SZ|BJ|TI|OF)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_date(v: str) -> str:
    if not _DATE_RE.match(v):
        raise ValueError("date must be YYYY-MM-DD")
    return v


class IndicatorSpec(BaseModel):
    id: str
    kind: Literal[
        "MA", "PCT_CHANGE", "VRATIO", "BODY_RATIO", "UPPER_SHADOW_RATIO", "MA_CONVERGE", "BOX_TOP",
        "EMA", "MACD_DIF", "MACD_DEA", "MACD_HIST", "KDJ_K", "KDJ_D", "KDJ_J", "RSI",
        "BOLL_UP", "BOLL_MID", "BOLL_LOW",
    ]
    of: Union[str, None] = None
    n: Union[int, None] = None
    mas: Union[list[str], None] = None
    fast: Union[int, None] = None
    slow: Union[int, None] = None
    signal: Union[int, None] = None
    m1: Union[int, None] = None
    m2: Union[int, None] = None
    k: Union[float, None] = None

    @validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError("indicator id must be lowercase snake_case (max 20 chars)")
        return v

    @classmethod
    def _check_int_range_values(cls, values, names):
        for name in names:
            v = values.get(name)
            if v is not None and not (2 <= v <= 250):
                raise ValueError(f"{name} must be in [2, 250]")

    @classmethod
    def _check_of_optional_values(cls, values):
        of = values.get("of")
        if of is not None and of not in BASE_FIELDS and not _ID_RE.match(of):
            raise ValueError(f"of must be a base field or a declared indicator id")

    @root_validator(skip_on_failure=True)
    def _check_params(cls, values):  # noqa: N805
        kind = values.get("kind")
        if kind in ("MA", "PCT_CHANGE"):
            of = values.get("of")
            if of not in BASE_FIELDS and not _ID_RE.match(of or ""):
                raise ValueError(f"{kind} requires of in base fields or a declared indicator id")
            n = values.get("n")
            if n is None or not (2 <= n <= 250):
                raise ValueError(f"{kind} requires n in [2, 250]")
        elif kind == "VRATIO":
            n = values.get("n")
            if n is None or not (2 <= n <= 250):
                raise ValueError("VRATIO requires n in [2, 250]")
        elif kind == "BOX_TOP":
            n = values.get("n")
            if n is None or not (2 <= n <= 250):
                raise ValueError("BOX_TOP requires n in [2, 250]")
        elif kind == "MA_CONVERGE":
            mas = values.get("mas")
            if not mas or len(mas) < 2:
                raise ValueError("MA_CONVERGE requires mas with >= 2 entries")
        elif kind == "EMA":
            of = values.get("of")
            if of not in BASE_FIELDS and not _ID_RE.match(of or ""):
                raise ValueError(f"EMA requires of in base fields or a declared indicator id")
            n = values.get("n")
            if n is None or not (2 <= n <= 250):
                raise ValueError("EMA requires n in [2, 250]")
        elif kind in ("MACD_DIF", "MACD_DEA", "MACD_HIST"):
            cls._check_of_optional_values(values)
            cls._check_int_range_values(values, ("fast", "slow", "signal"))
        elif kind in ("KDJ_K", "KDJ_D", "KDJ_J"):
            cls._check_int_range_values(values, ("n", "m1", "m2"))
        elif kind == "RSI":
            cls._check_of_optional_values(values)
            cls._check_int_range_values(values, ("n",))
        elif kind in ("BOLL_UP", "BOLL_MID", "BOLL_LOW"):
            cls._check_of_optional_values(values)
            cls._check_int_range_values(values, ("n",))
            k = values.get("k")
            if k is not None and k <= 0:
                raise ValueError("k must be > 0")
        return values

    @classmethod
    def _check_int_range_values(cls, values, names):
        for name in names:
            v = values.get(name)
            if v is not None and not (2 <= v <= 250):
                raise ValueError(f"{name} must be in [2, 250]")


class LeafCondition(BaseModel):
    left: str
    op: Literal[">", ">=", "<", "<=", "=="]
    right: Union[float, str]
    right_factor: Union[float, None] = None
    lag: int = 0
    right_lag: int = 0
    within: Union[int, None] = None
    note: Union[str, None] = None

    @root_validator(skip_on_failure=True)
    def _check(cls, values):  # noqa: N805
        if values.get("lag", 0) < 0 or values.get("right_lag", 0) < 0:
            raise ValueError("lag/right_lag must be >= 0")
        if values.get("within") is not None and values["within"] < 1:
            raise ValueError("within must be >= 1")
        if values.get("right_factor") is not None and values["right_factor"] <= 0:
            raise ValueError("right_factor must be > 0")
        return values


class ConditionGroup(BaseModel):
    logic: Literal["all", "any"]
    conditions: list[Union[LeafCondition, "ConditionGroup"]]
    note: Union[str, None] = None


class Rule(BaseModel):
    """v2 规则：触发条件 → 动作（买/卖）+ 仓位比例 + 次数上限。补仓只是 buy 规则的一种。"""

    when: ConditionGroup
    action: Literal["buy", "sell"]
    size_pct: Union[float, None] = None  # buy=当前权益% / sell=当前持仓%；null=用全局 position_pct(buy) / 100(sell)
    max_times: Union[int, None] = None  # 单只股票最多触发次数；null=不限
    note: str = ""

    @root_validator(skip_on_failure=True)
    def _check(cls, values):  # noqa: N805
        when = values.get("when")
        if when is None or not when.conditions:
            raise ValueError("rule.when.conditions must not be empty")
        size_pct = values.get("size_pct")
        if size_pct is not None and not (0 < size_pct <= 100):
            raise ValueError("size_pct must be in (0, 100]")
        max_times = values.get("max_times")
        if max_times is not None and max_times < 1:
            raise ValueError("max_times must be >= 1")
        return values


class Universe(BaseModel):
    type: Literal["index", "sector", "custom", "all"]
    code: Union[str, None] = None
    codes: Union[list[str], None] = None

    @root_validator(skip_on_failure=True)
    def _check(cls, values):  # noqa: N805
        if values.get("type") in ("index", "sector"):
            if not values.get("code") or not _THSCODE_RE.match(values["code"]):
                raise ValueError(f"universe type {values.get('type')} requires a valid thscode code")
        elif values.get("type") == "custom":
            if not values.get("codes"):
                raise ValueError("universe type custom requires non-empty codes")
            for c in values["codes"]:
                if not _THSCODE_RE.match(c):
                    raise ValueError(f"invalid thscode: {c}")
        return values


class RiskConfig(BaseModel):
    stop_loss_pct: Union[float, None] = Field(default=None, ge=0.5, le=50)
    trailing_stop_pct: Union[float, None] = Field(default=None, ge=0.5, le=50)  # 移动止损：自持仓期最高价（盘中）回撤%
    max_hold_days: Union[int, None] = Field(default=None, ge=1, le=500)
    take_profit_pct: Union[float, None] = Field(default=None, ge=0.5, le=200)


class BacktestDefaults(BaseModel):
    start: str
    end: str
    initial_cash: float = Field(gt=0)
    position_pct: float = Field(ge=1, le=100)
    max_positions: int = Field(ge=1, le=50)
    fee_bps: float = Field(ge=0, le=100)
    stamp_tax_bps: float = Field(ge=0, le=100)

    @validator("start", "end")
    @classmethod
    def _check_date(cls, v: str) -> str:
        return _valid_date(v)


def _assert_no_cycle(graph: dict[str, list[str]]) -> None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}

    def dfs(n: str) -> None:
        color[n] = GRAY
        for m in graph[n]:
            if color[m] == GRAY:
                raise ValueError(f"indicator reference cycle detected at: {n} -> {m}")
            if color[m] == WHITE:
                dfs(m)
        color[n] = BLACK

    for n in graph:
        if color[n] == WHITE:
            dfs(n)


def _check_name_len(v: str) -> str:
    v = v.strip()
    if not (1 <= len(v) <= 40):
        raise ValueError("name must be 1-40 chars")
    return v


def _check_indicator_refs(indicators, groups) -> None:
    """指标引用校验：indicator id 唯一 + of/mas 引用可解析 + 无循环 + 条件引用可解析。

    groups: 需要校验条件引用的 ConditionGroup 列表（Screening 的 entry / Trading 的 rules[].when）。
    """
    ids = [ind.id for ind in indicators]
    if len(ids) != len(set(ids)):
        raise ValueError("indicator ids must be unique")
    known = set(ids) | BASE_FIELDS | HOLD_FIELDS

    graph: dict[str, list[str]] = {ind.id: [] for ind in indicators}
    for ind in indicators:
        refs: list[str] = []
        if ind.of and ind.of not in BASE_FIELDS:
            if ind.of not in known:
                raise ValueError(f"indicator {ind.id} references unknown field/indicator: {ind.of}")
            refs.append(ind.of)
        for ref in ind.mas or []:
            if ref not in known:
                raise ValueError(f"MA_CONVERGE references unknown indicator: {ref}")
            if ref not in BASE_FIELDS:
                refs.append(ref)
        graph[ind.id] = refs

    _assert_no_cycle(graph)

    def walk(conds: list, depth: int) -> None:
        for c in conds:
            if isinstance(c, ConditionGroup):
                if depth >= 2:
                    raise ValueError("condition nesting deeper than 2 levels is not allowed")
                walk(c.conditions, depth + 1)
            else:
                if c.left not in known:
                    raise ValueError(f"condition references unknown indicator/field: {c.left}")
                if isinstance(c.right, str) and c.right not in known:
                    raise ValueError(f"condition references unknown indicator/field: {c.right}")

    for g in groups:
        if g is not None:
            walk(g.conditions, 1)


def _walk_leaves(group: ConditionGroup):
    """递归展开条件组，产出所有叶子条件 LeafCondition。"""
    for c in group.conditions:
        if isinstance(c, ConditionGroup):
            yield from _walk_leaves(c)
        else:
            yield c


def _references_hold_field(cond) -> bool:
    return cond.left in HOLD_FIELDS or (isinstance(cond.right, str) and cond.right in HOLD_FIELDS)


class ScreeningStrategy(BaseModel):
    """选股策略：纯筛选，entry 入场条件不可引用持仓状态字段（未持仓时无意义）。"""

    name: str
    description: str = ""
    source_text: str = ""
    parse_engine: str = "llm"
    universe: Universe
    indicators: conlist(IndicatorSpec, min_items=1, max_items=30)  # type: ignore[valid-type]
    entry: ConditionGroup

    @validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return _check_name_len(v)

    @root_validator(skip_on_failure=True)
    def _check(cls, values):  # noqa: N805
        indicators = values.get("indicators") or []
        entry = values.get("entry")
        if entry is None or not entry.conditions:
            raise ValueError("entry.conditions must not be empty")
        _check_indicator_refs(indicators, [entry])
        for cond in _walk_leaves(entry):
            if _references_hold_field(cond):
                raise ValueError("screening entry must not reference hold fields (cost/pnl_pct/hold_days/dd_from_peak)")
        return values


class TradingStrategy(BaseModel):
    """交易策略：持仓管理规则，入场由选股策略负责；buy 规则仅用于补仓（必须引用持仓字段）。"""

    name: str
    description: str = ""
    source_text: str = ""
    parse_engine: str = "llm"
    indicators: conlist(IndicatorSpec, min_items=1, max_items=30)  # type: ignore[valid-type]
    rules: conlist(Rule, min_items=1)  # type: ignore[valid-type]
    risk: RiskConfig = RiskConfig()
    backtest_defaults: BacktestDefaults

    @validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return _check_name_len(v)

    @root_validator(skip_on_failure=True)
    def _check(cls, values):  # noqa: N805
        indicators = values.get("indicators") or []
        rules = values.get("rules") or []
        _check_indicator_refs(indicators, [r.when for r in rules])
        for rule in rules:
            if rule.action == "buy" and not any(_references_hold_field(c) for c in _walk_leaves(rule.when)):
                raise ValueError("buy rule must reference a hold field (cost/pnl_pct/hold_days/dd_from_peak)")
        return values
