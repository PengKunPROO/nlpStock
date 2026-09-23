"""Condition evaluator: leaf/group evaluation with within-lookback, lag and factor semantics."""
from __future__ import annotations

from .schema import ConditionGroup, LeafCondition

_CMP = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


def _resolve(ref: str, lag: int, series: dict[str, list[float | None]], i: int) -> float | None:
    j = i - lag
    if j < 0:
        return None
    arr = series.get(ref)
    if arr is None:
        raise KeyError(f"unknown series: {ref}")
    v = arr[j]
    return None if v is None else float(v)


def eval_leaf(cond: LeafCondition, series: dict[str, list[float | None]], i: int) -> bool:
    left = _resolve(cond.left, cond.lag, series, i)
    if isinstance(cond.right, str):
        right = _resolve(cond.right, cond.right_lag, series, i)
    else:
        right = float(cond.right)
    if right is not None and cond.right_factor is not None:
        right = right * cond.right_factor
    if left is None or right is None:
        return False
    return _CMP[cond.op](left, right)


def eval_condition(cond: LeafCondition | ConditionGroup, series: dict[str, list[float | None]], i: int) -> bool:
    if isinstance(cond, ConditionGroup):
        results = (eval_condition(c, series, i) for c in cond.conditions)
        return all(results) if cond.logic == "all" else any(results)
    if cond.within is not None:
        start = max(0, i - cond.within + 1)
        return any(eval_leaf(cond, series, k) for k in range(start, i + 1))
    return eval_leaf(cond, series, i)


def signal_at(group: ConditionGroup, series: dict[str, list[float | None]], i: int) -> bool:
    return eval_condition(group, series, i)
