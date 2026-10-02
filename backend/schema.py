"""StrategyConfig pydantic schema — the quantified strategy contract (docs/api-contract.md §1-2)."""
from __future__ import annotations

import re
from typing import Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

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

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError("indicator id must be lowercase snake_case (max 20 chars)")
        return v

    def _check_int_range(self, names: tuple[str, ...]) -> None:
        for name in names:
            v = getattr(self, name)
            if v is not None and not (2 <= v <= 250):
                raise ValueError(f"{name} must be in [2, 250]")

    def _check_of_optional(self) -> None:
        if self.of is not None and self.of not in BASE_FIELDS and not _ID_RE.match(self.of):
            raise ValueError(f"of must be a base field or a declared indicator id")

    @model_validator(mode="after")
    def _check_params(self) -> "IndicatorSpec":
        if self.kind in ("MA", "PCT_CHANGE"):
            if self.of not in BASE_FIELDS and not _ID_RE.match(self.of or ""):
                raise ValueError(f"{self.kind} requires of in base fields or a declared indicator id")
            if self.n is None or not (2 <= self.n <= 250):
                raise ValueError(f"{self.kind} requires n in [2, 250]")
        elif self.kind == "VRATIO":
            if self.n is None or not (2 <= self.n <= 250):
                raise ValueError("VRATIO requires n in [2, 250]")
        elif self.kind == "BOX_TOP":
            if self.n is None or not (2 <= self.n <= 250):
                raise ValueError("BOX_TOP requires n in [2, 250]")
        elif self.kind == "MA_CONVERGE":
            if not self.mas or len(self.mas) < 2:
                raise ValueError("MA_CONVERGE requires mas with >= 2 entries")
        elif self.kind == "EMA":
            if self.of not in BASE_FIELDS and not _ID_RE.match(self.of or ""):
                raise ValueError(f"EMA requires of in base fields or a declared indicator id")
            if self.n is None or not (2 <= self.n <= 250):
                raise ValueError("EMA requires n in [2, 250]")
        elif self.kind in ("MACD_DIF", "MACD_DEA", "MACD_HIST"):
            self._check_of_optional()
            self._check_int_range(("fast", "slow", "signal"))
        elif self.kind in ("KDJ_K", "KDJ_D", "KDJ_J"):
            self._check_int_range(("n", "m1", "m2"))
        elif self.kind == "RSI":
            self._check_of_optional()
            self._check_int_range(("n",))
        elif self.kind in ("BOLL_UP", "BOLL_MID", "BOLL_LOW"):
            self._check_of_optional()
            self._check_int_range(("n",))
            if self.k is not None and self.k <= 0:
                raise ValueError("k must be > 0")
        return self


class LeafCondition(BaseModel):
    left: str
    op: Literal[">", ">=", "<", "<=", "=="]
    right: Union[float, str]
    right_factor: Union[float, None] = None
    lag: int = 0
    right_lag: int = 0
    within: Union[int, None] = None
    note: Union[str, None] = None

    @model_validator(mode="after")
    def _check(self) -> "LeafCondition":
        if self.lag < 0 or self.right_lag < 0:
            raise ValueError("lag/right_lag must be >= 0")
        if self.within is not None and self.within < 1:
            raise ValueError("within must be >= 1")
        if self.right_factor is not None and self.right_factor <= 0:
            raise ValueError("right_factor must be > 0")
        return self


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

    @model_validator(mode="after")
    def _check(self) -> "Rule":
        if not self.when.conditions:
            raise ValueError("rule.when.conditions must not be empty")
        if self.size_pct is not None and not (0 < self.size_pct <= 100):
            raise ValueError("size_pct must be in (0, 100]")
        if self.max_times is not None and self.max_times < 1:
            raise ValueError("max_times must be >= 1")
        return self


class Universe(BaseModel):
    type: Literal["index", "sector", "custom", "all"]
    code: Union[str, None] = None
    codes: Union[list[str], None] = None

    @model_validator(mode="after")
    def _check(self) -> "Universe":
        if self.type in ("index", "sector"):
            if not self.code or not _THSCODE_RE.match(self.code):
                raise ValueError(f"universe type {self.type} requires a valid thscode code")
        elif self.type == "custom":
            if not self.codes:
                raise ValueError("universe type custom requires non-empty codes")
            for c in self.codes:
                if not _THSCODE_RE.match(c):
                    raise ValueError(f"invalid thscode: {c}")
        return self


class RiskConfig(BaseModel):
    stop_loss_pct: Union[float, None] = Field(default=None, ge=0.5, le=50)
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

    @field_validator("start", "end")
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


class StrategyConfig(BaseModel):
    name: str
    description: str = ""
    source_text: str = ""
    parse_engine: str = "llm"
    universe: Universe
    indicators: list[IndicatorSpec] = Field(min_length=1, max_length=30)
    entry: Union[ConditionGroup, None] = None
    exit: Union[ConditionGroup, None] = None
    rules: Union[list[Rule], None] = None
    risk: RiskConfig = RiskConfig()
    backtest_defaults: BacktestDefaults

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= 40):
            raise ValueError("name must be 1-40 chars")
        return v

    @model_validator(mode="after")
    def _normalize_rules(self) -> "StrategyConfig":
        """v1 兼容：entry/exit 自动转为 rules（先执行，供 _check_refs 使用）。"""
        if not self.rules:
            if self.entry is None:
                raise ValueError("必须提供 rules 或 entry")
            rules = [Rule(when=self.entry, action="buy", size_pct=None, note=self.entry.note or "入场信号")]
            if self.exit is not None and self.exit.conditions:
                rules.append(Rule(when=self.exit, action="sell", size_pct=100, note=self.exit.note or "离场信号"))
            object.__setattr__(self, "rules", rules)
        return self

    @model_validator(mode="after")
    def _check_refs(self) -> "StrategyConfig":
        ids = [ind.id for ind in self.indicators]
        if len(ids) != len(set(ids)):
            raise ValueError("indicator ids must be unique")
        known = set(ids) | BASE_FIELDS | HOLD_FIELDS

        graph: dict[str, list[str]] = {ind.id: [] for ind in self.indicators}
        for ind in self.indicators:
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

        for rule in self.rules or []:
            walk(rule.when.conditions, 1)
        return self


REFERENCE_STRATEGY: dict = {
    "name": "阴跌急跌·止跌反转·回踩进场",
    "description": "阴跌急跌洗出空间，均线粘合止跌，底部放量站上20日线确认反转，缩量回踩不破箱体上沿进场；上影线/缩量力竭或破位离场。",
    "source_text": "1，阴跌之后等急跌，2，急跌之后等止跌，(均线拧到一块是止跌信号) 3，止跌之后等反转(底部放量，阳线实体越来越大，价格站上关键均线才是反转信号)，4，反转之后等进场(拉一波再缩量回踩不破前期箱体上沿，确认支撑有效，说明主力锁仓，这时候才可以进)，5，力竭出现，因为量能跟不上，出现上影线，越来越短的阳线，都是力竭信号，6，力竭后的离场，不舍得卖啊，这时落袋才是利润，7，离场之后等待回落，千万别追，8，回调支撑如果被击穿就不要进了。",
    "parse_engine": "llm",
    "universe": {"type": "index", "code": "000300.SH"},
    "indicators": [
        {"id": "ma5", "kind": "MA", "of": "close", "n": 5},
        {"id": "ma10", "kind": "MA", "of": "close", "n": 10},
        {"id": "ma20", "kind": "MA", "of": "close", "n": 20},
        {"id": "vma5", "kind": "MA", "of": "volume", "n": 5},
        {"id": "vr", "kind": "VRATIO", "n": 5},
        {"id": "body", "kind": "BODY_RATIO"},
        {"id": "ush", "kind": "UPPER_SHADOW_RATIO"},
        {"id": "conv", "kind": "MA_CONVERGE", "mas": ["ma5", "ma10", "ma20"]},
        {"id": "box20", "kind": "BOX_TOP", "n": 20},
        {"id": "chg5", "kind": "PCT_CHANGE", "of": "close", "n": 5},
        {"id": "chg10", "kind": "PCT_CHANGE", "of": "close", "n": 10},
        {"id": "chg20", "kind": "PCT_CHANGE", "of": "close", "n": 20},
    ],
    "entry": {
        "logic": "all",
        "conditions": [
            {"left": "chg20", "op": "<=", "right": -8, "within": 60, "note": "①阴跌：近期出现过20日累计跌幅≥8%"},
            {"left": "chg5", "op": "<=", "right": -4, "within": 40, "note": "②急跌：近期出现过5日急跌≥4%"},
            {"left": "conv", "op": "<=", "right": 2.5, "within": 15, "note": "③止跌：均线拧到一块（粘合度≤2.5%）"},
            {"left": "vr", "op": ">=", "right": 1.8, "within": 10, "note": "④反转：底部放量（量比≥1.8）"},
            {"left": "close", "op": ">", "right": "ma20", "note": "⑤站上关键均线20日线"},
            {"left": "chg10", "op": ">=", "right": 5, "within": 15, "note": "⑥拉一波：出现过10日涨幅≥5%"},
            {"left": "close", "op": ">=", "right": "box20", "right_factor": 0.98, "note": "⑦回踩不破前期箱体上沿（容差2%）"},
            {"left": "vr", "op": "<=", "right": 1.1, "note": "⑧回踩缩量（当下量比≤1.1，主力锁仓）"},
        ],
    },
    "exit": {
        "logic": "any",
        "conditions": [
            {"left": "ush", "op": ">", "right": 0.4, "note": "力竭：长上影线"},
            {"left": "vr", "op": "<", "right": 0.5, "within": 3, "note": "力竭：量能跟不上（近3日出现过极端缩量）"},
            {
                "logic": "all",
                "conditions": [
                    {"left": "body", "op": "<", "right": "body", "right_lag": 1, "note": "力竭：阳线实体越来越短"},
                    {"left": "body", "op": ">", "right": 0},
                ],
                "note": "阳线但实体连续收窄",
            },
            {"left": "close", "op": "<", "right": "ma10", "note": "离场：跌破10日线"},
            {"left": "close", "op": "<", "right": "box20", "right_factor": 0.95, "note": "离场：击穿箱体上沿5%（支撑失效）"},
        ],
    },
    "risk": {"stop_loss_pct": 8.0, "max_hold_days": 30, "take_profit_pct": None},
    "backtest_defaults": {
        "start": "2025-01-01",
        "end": "2026-09-18",
        "initial_cash": 1000000,
        "position_pct": 20,
        "max_positions": 5,
        "fee_bps": 2.5,
        "stamp_tax_bps": 5.0,
    },
}
