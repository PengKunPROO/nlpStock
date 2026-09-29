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


# ---------- verbose 评估（审计追踪：返回每条条件的实际数值与真假） ----------


def _leaf_expr(cond: LeafCondition) -> str:
    left = cond.left + (f"(lag={cond.lag})" if cond.lag else "")
    right = str(cond.right)
    if isinstance(cond.right, str) and cond.right_lag:
        right += f"(right_lag={cond.right_lag})"
    return f"{left} {cond.op} {right}"


def eval_leaf_verbose(cond: LeafCondition, series: dict[str, list[float | None]], i: int) -> dict:
    """评估单个叶子条件，返回 {expr, left, right, passed, note}；within 时显示命中日的值。"""
    idx = i
    if cond.within is not None:
        start = max(0, i - cond.within + 1)
        for k in range(start, i + 1):
            if eval_leaf(cond, series, k):
                idx = k
                break
    passed = eval_leaf(cond, series, idx) if idx != i or cond.within is None else eval_leaf(cond, series, i)
    # 重新取 idx 的值用于展示
    left = _resolve(cond.left, cond.lag, series, idx)
    if isinstance(cond.right, str):
        right = _resolve(cond.right, cond.right_lag, series, idx)
    else:
        right = float(cond.right)
    note = cond.note or ""
    out = {
        "expr": _leaf_expr(cond),
        "left": left,
        "right": right,
        "passed": passed,
        "note": note,
    }
    if cond.within is not None:
        out["within"] = cond.within
    if cond.right_factor is not None and right is not None:
        out["right"] = right * cond.right_factor
        out["expr"] += f" ×{cond.right_factor}"
    return out


def eval_group_verbose(group: ConditionGroup, series: dict[str, list[float | None]], i: int) -> dict:
    """评估条件组，返回 {logic, conditions: [明细], passed}，嵌套组递归。"""
    details = []
    for c in group.conditions:
        if isinstance(c, ConditionGroup):
            details.append(eval_group_verbose(c, series, i))
        else:
            details.append(eval_leaf_verbose(c, series, i))
    results = (d["passed"] for d in details)
    passed = all(results) if group.logic == "all" else any(results)
    return {"logic": group.logic, "conditions": details, "passed": passed, "note": group.note or ""}
