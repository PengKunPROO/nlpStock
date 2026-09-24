"""StrategyConfig pydantic schema — the quantified strategy contract (docs/api-contract.md §1-2)."""
from __future__ import annotations

import re
from typing import Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

BASE_FIELDS = {"open", "high", "low", "close", "volume"}
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
        if self.of is not None and self.of not in BASE_FIELDS:
            raise ValueError(f"of must be one of {sorted(BASE_FIELDS)}")

    @model_validator(mode="after")
    def _check_params(self) -> "IndicatorSpec":
        if self.kind in ("MA", "PCT_CHANGE"):
            if self.of not in BASE_FIELDS:
                raise ValueError(f"{self.kind} requires of in {sorted(BASE_FIELDS)}")
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
            if self.of not in BASE_FIELDS:
                raise ValueError(f"EMA requires of in {sorted(BASE_FIELDS)}")
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


class StrategyConfig(BaseModel):
    name: str
    description: str = ""
    source_text: str = ""
    parse_engine: str = "llm"
    universe: Universe
    indicators: list[IndicatorSpec] = Field(min_length=1, max_length=30)
    entry: ConditionGroup
    exit: ConditionGroup
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
    def _check_refs(self) -> "StrategyConfig":
        ids = [ind.id for ind in self.indicators]
        if len(ids) != len(set(ids)):
            raise ValueError("indicator ids must be unique")
        known = set(ids) | BASE_FIELDS
        for ind in self.indicators:
            if ind.kind == "MA_CONVERGE":
                for ref in ind.mas or []:
                    if ref not in known:
                        raise ValueError(f"MA_CONVERGE references unknown indicator: {ref}")

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

        if not self.entry.conditions:
            raise ValueError("entry.conditions must not be empty")
        walk(self.entry.conditions, 1)
        walk(self.exit.conditions, 1)
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
