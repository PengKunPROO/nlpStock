"""内置种子策略：幂等创建 2 个选股策略（screening）+ 2 个交易策略（trading）。

用法：
    from backend.seed import seed_strategies
    result = seed_strategies(storage)   # -> {"created": [...], "skipped": [...]}

或直接执行（默认写 data/app.db）：
    python -m backend.seed
"""
from __future__ import annotations

from pathlib import Path

from .schema import ScreeningStrategy, TradingStrategy
from .storage import Storage

_BT_DEFAULTS = {
    "start": "2025-01-01",
    "end": "2025-12-31",
    "initial_cash": 1_000_000.0,
    "position_pct": 20.0,
    "max_positions": 5,
    "fee_bps": 2.5,
    "stamp_tax_bps": 5.0,
}

# ---- screening seeds ----
_MACD_CROSS = {
    "name": "MACD金叉选股",
    "description": "MACD 金叉：当日 DIF > DEA 且前一日 DIF <= DEA（默认参数 12/26/9）",
    "parse_engine": "manual",
    "universe": {"type": "all"},
    "indicators": [
        {"id": "dif", "kind": "MACD_DIF"},
        {"id": "dea", "kind": "MACD_DEA"},
    ],
    "entry": {
        "logic": "all",
        "conditions": [
            {"left": "dif", "op": ">", "right": "dea"},
            {"left": "dif", "op": "<=", "right": "dea", "lag": 1, "right_lag": 1},
        ],
    },
}

_VOLUME_MA20 = {
    "name": "放量站上20日线",
    "description": "收盘价站上 20 日均线，且量比（当期成交量 / 前 5 日均量）>= 1.5",
    "parse_engine": "manual",
    "universe": {"type": "all"},
    "indicators": [
        {"id": "ma20", "kind": "MA", "of": "close", "n": 20},
        {"id": "vr", "kind": "VRATIO", "n": 5},
    ],
    "entry": {
        "logic": "all",
        "conditions": [
            {"left": "close", "op": ">", "right": "ma20"},
            {"left": "vr", "op": ">=", "right": 1.5},
        ],
    },
}

# ---- trading seeds ----
_STOP_LOSS_8 = {
    "name": "止损8%+涨10%减半+跌破10日线清仓",
    "description": "止损 8%；浮盈 10% 减半（限 1 次）；跌破 10 日线清仓；浮亏 5% 补仓 33%（限 1 次）",
    "parse_engine": "manual",
    "indicators": [{"id": "ma10", "kind": "MA", "of": "close", "n": 10}],
    "rules": [
        {
            "when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": ">=", "right": 10}]},
            "action": "sell", "size_pct": 50, "max_times": 1, "note": "涨10%减半",
        },
        {
            "when": {"logic": "all", "conditions": [{"left": "close", "op": "<", "right": "ma10"}]},
            "action": "sell", "size_pct": 100, "note": "跌破10日线清仓",
        },
        {
            "when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": "<=", "right": -5}]},
            "action": "buy", "size_pct": 33, "max_times": 1, "note": "跌5%补仓",
        },
    ],
    "risk": {"stop_loss_pct": 8.0},
    "backtest_defaults": dict(_BT_DEFAULTS),
}

_TRAILING_STOP_8 = {
    "name": "移动止损8%+持仓20日",
    "description": "移动止损：自持仓期最高价回撤 8% 离场；持仓满 20 个交易日清仓",
    "parse_engine": "manual",
    "indicators": [{"id": "ma5", "kind": "MA", "of": "close", "n": 5}],
    "rules": [
        {
            "when": {"logic": "all", "conditions": [{"left": "hold_days", "op": ">=", "right": 20}]},
            "action": "sell", "size_pct": 100, "note": "持仓20日清仓",
        },
    ],
    "risk": {"trailing_stop_pct": 8.0},
    "backtest_defaults": dict(_BT_DEFAULTS),
}

SEEDS: list[tuple[dict, str]] = [
    (_MACD_CROSS, "screening"),
    (_VOLUME_MA20, "screening"),
    (_STOP_LOSS_8, "trading"),
    (_TRAILING_STOP_8, "trading"),
]


def seed_strategies(storage: Storage) -> dict[str, list[str]]:
    """幂等创建内置策略：按 (name, type) 查重，已存在则跳过。返回 created/skipped 名称列表。"""
    existing = {(it["name"], it["type"]) for it in storage.list_strategies()}
    created: list[str] = []
    skipped: list[str] = []
    for config, stype in SEEDS:
        if (config["name"], stype) in existing:
            skipped.append(config["name"])
            continue
        # 保存前按 schema 校验，防止种子配置漂移写坏数据
        if stype == "screening":
            ScreeningStrategy(**config)
        else:
            TradingStrategy(**config)
        storage.create_strategy(config, type=stype)
        created.append(config["name"])
    return {"created": created, "skipped": skipped}


if __name__ == "__main__":
    import sys

    db = sys.argv[1] if len(sys.argv) > 1 else "data/app.db"
    print(seed_strategies(Storage(Path(db))))
