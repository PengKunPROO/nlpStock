# 策略模型 v2 设计契约：条件→动作规则 + 持仓状态

版本: 2.0（草案，待 review）· 本文档是 v2 策略模型的单一事实源。确认后再实施。

## 0. 动机

v1 只有 `entry`(买不买) / `exit`(卖不卖) / `risk` 三个固定槽位，**仓位是全局固定 `position_pct`**，无法表达：

- “MACD 死叉，买入 1/3 仓”
- “下跌 5%，再补仓 1/3”
- “涨 10% 减半仓”

补仓只是这类“策略行为”的一种。v2 的目标是**泛化**：把“买卖动作 + 仓位比例 + 触发条件”变成**数据（规则列表）**，由 AI 从自然语言解析、引擎统一执行——**不为“补仓”写任何特殊代码**。

## 1. 规则模型（rules）

策略配置新增 `rules`（与 `entry`/`exit` 二选一，见 §5 兼容层）：

```jsonc
"rules": [
  {
    "when": { "logic": "all", "conditions": [ Condition... ] },  // 触发条件（复用 v1 条件模型，见 docs/api-contract.md §1）
    "action": "buy" | "sell",
    "size_pct": 33.33,     // buy=占“当前总权益”%；sell=占“当前持仓股数”%；null=用全局 position_pct(buy)/100(sell)
    "max_times": 1,        // 该规则在单只股票上最多触发次数；null=不限
    "note": "死叉买入1/3仓"  // 原文依据，审查页展示
  }
]
```

**语义要点**：
- `buy` 的 `size_pct`：按**当前总权益**百分比买入（与 v1 `position_pct` 口径一致）。`null` 时用回测参数 `position_pct`。
- `sell` 的 `size_pct`：按**当前持仓股数**百分比卖出。`100` = 清仓，`50` = 卖一半。`null` 视为 `100`。
- `max_times`：防止“跌 X% 补仓”这类规则在条件持续满足时无限触发。计数按**单只股票**独立。
- 买入数量向下取整到 100 股整数倍；卖出后不足 100 股则清仓。

## 2. 持仓状态字段（内置，无需声明）

“下跌 X%”依赖**持仓状态**（成本价、持仓天数），不是纯 K 线。v2 引入 4 个**内置字段**，可直接在 `when` 条件里引用（与 `open/close` 同级，**不需要在 indicators 里声明**）：

| 字段 | 含义 | 未持仓时 |
|---|---|---|
| `cost` | 当前加权成本价 | null |
| `pnl_pct` | 相对成本价浮盈亏 % = (close/cost − 1)×100 | null |
| `hold_days` | 持仓以来的交易日数 | null |
| `dd_from_peak` | 距持仓期最高收盘价回撤 % = (close/peak − 1)×100 | null |

- 条件校验时，可引用的名字 = `open/high/low/close/volume` ∪ `{cost, pnl_pct, hold_days, dd_from_peak}` ∪ 已声明的指标 id。
- **未持仓时这些字段为 null → 引用它们的条件求值为 False**（与现有 None 语义一致）。

## 3. 引擎执行语义（`backtest.py`）

### 3.1 信号→成交时点（不变）
- 信号在 T 日收盘判定，动作在 **T+1 开盘价**执行（无未来函数）。
- A 股 T+1：买入当日不可卖（保留现有约束）。

### 3.2 每日执行顺序
1. **强制风控离场**（`risk`，优先）：止损盘中触发按止损价、止盈/最长持仓按规则（保持 v1 语义）。
2. **sell 规则**：按 `rules` 列表顺序，对**当前持仓股票**评估；命中的按 `size_pct` 部分/全部卖出（同一股票多条 sell 规则可叠加，卖完为止）。
3. **buy 规则**：按 `rules` 列表顺序，对**未持仓股票**评估开仓规则；对**已持仓股票**评估加仓规则。
4. 现金约束：买入受可用现金限制；不足则按可买数量成交或跳过。

### 3.3 规则分类（引擎自动判断，无需用户标注）
- **开仓规则**：`when` **不引用**持仓状态字段（cost/pnl_pct/hold_days/dd_from_peak）→ 对全池未持仓股票评估（等价 v1 entry，可预计算加速）。
- **持仓期规则**：`when` **引用**了持仓状态字段 → 仅对已持仓股票评估（如“跌 5% 补仓”）。

### 3.4 加权成本与分批
- 多次 buy → 持仓股数累加，成本价 = 加权平均买入价。
- 部分 sell → 股数减少，成本价不变；剩余持仓继续受 sell 规则/风控管理。

### 3.5 边界规则
| 场景 | 行为 |
|---|---|
| buy 累计 `size_pct` 使仓位超 100% | 受现金约束，买不满则按可买量成交 |
| sell 后不足 100 股 | 全部卖出 |
| 同日多条 buy 命中 | 按 rules 顺序执行，直到现金不足或无可买 |
| 同日多条 sell 命中 | 按 rules 顺序执行，直到清仓 |
| `max_times` 用尽 | 该规则在该股票上不再触发 |
| 未持仓时引用持仓字段 | 条件为 False |
| 信号在回测最后一根 K 线 | 无次日 → 不成交 |

## 4. AI 对齐（`llm_align.py` 目录更新）

`_INDICATOR_CATALOG` 增加：持仓状态字段说明 + `rules` 结构 + 自然语言→rules 示例。

示例（用户原话 → 配置）：
> “MACD 死叉买入三分之一仓，下跌 5% 再补仓三分之一，涨 10% 减半，金叉清仓，止损 8%”

```json
{
  "indicators": [ {"id":"dif","kind":"MACD_DIF"}, {"id":"dea","kind":"MACD_DEA"} ],
  "rules": [
    {"when":{"logic":"all","conditions":[
        {"logic":"all","conditions":[
          {"left":"dif","op":"<","right":"dea"},
          {"left":"dif","op":">=","right":"dea","lag":1,"right_lag":1}]}]},
     "action":"buy","size_pct":33.33,"max_times":null,"note":"MACD死叉买入1/3仓"},
    {"when":{"logic":"all","conditions":[{"left":"pnl_pct","op":"<=","right":-5}]},
     "action":"buy","size_pct":33.33,"max_times":1,"note":"下跌5%补仓1/3"},
    {"when":{"logic":"all","conditions":[{"left":"pnl_pct","op":">=","right":10}]},
     "action":"sell","size_pct":50,"max_times":null,"note":"涨10%减半"},
    {"when":{"logic":"all","conditions":[
        {"logic":"all","conditions":[
          {"left":"dif","op":">","right":"dea"},
          {"left":"dif","op":"<=","right":"dea","lag":1,"right_lag":1}]}]},
     "action":"sell","size_pct":100,"max_times":null,"note":"MACD金叉清仓"}
  ],
  "risk": {"stop_loss_pct": 8.0, "max_hold_days": null, "take_profit_pct": null}
}
```

## 5. 兼容层（v1 → v2，自动转换）

- v1 配置（有 `entry`/`exit`、无 `rules`）加载时自动转：
  - `entry` → `{"when": entry, "action": "buy", "size_pct": null, "note": entry 首条 note}`（`size_pct=null` 用全局 `position_pct`）
  - `exit` → `{"when": exit, "action": "sell", "size_pct": 100}`
- v2 配置直接用 `rules`；`entry`/`exit` 变为**只读兼容字段**（保存时归一化为 rules）。
- `risk` 两版都保留（独立风控语义）。
- **已保存的 3 个 v1 策略无需改动**即可继续回测。

## 6. 前端审查页（阶段 2）

规则列表编辑器：每行 = `when` 条件（复用现有条件编辑）+ `action` 下拉(买/卖) + `size_pct` 输入 + `max_times` 输入 + `note`。JSON 模式同步。

## 7. 分阶段实施（超 500 行，必须拆分）

| 阶段 | 内容 | 预估 |
|---|---|---|
| **1（本次）** | schema v2（rules + 持仓字段 + 兼容层）+ 引擎规则驱动（分批/加权成本/部分卖出/max_times）+ 持仓状态实时评估 + AI 目录 + 测试 | ~500 行 |
| 2 | 前端审查页规则编辑 | ~100 行 |
| 3（可选） | 更复杂动作（分批止盈增强、移动止损等） | 待定 |

## 8. 测试计划（阶段 1）

- schema：rules 合法/非法（action、size_pct 范围 0<≤100、max_times≥1）、持仓字段可引用、v1 自动转 rules。
- 引擎（手算 fixture）：
  - “死叉买 1/3 仓” → 首仓股数 = 权益×33.33%/价格（取整 100）；
  - “跌 5% 补仓” → 持仓期触发一次、`max_times=1` 不再触发、成本价加权正确；
  - “涨 10% 减半” → 卖出一半股数；
  - “金叉清仓” → 全卖；
  - 未持仓时引用 `pnl_pct` 的开仓规则 → 不触发；
  - 同日多规则顺序、现金约束、不足 100 股清仓。
- 回归：现有 183 测试全绿（兼容层保证 v1 行为不变）。
- 表面：真实 DeepSeek 解析“死叉买1/3、跌5%补仓”产出 rules + 真实回测跑通。

## 9. 已知取舍

- 持仓状态字段为**内置**（不通过 indicators 声明）——简洁，但不可自定义（如“距成本涨 X%”已由 `pnl_pct` 覆盖）。
- `risk`（止损/止盈/最长持仓）不并入 rules：它是风控强制离场，执行时点（盘中）与 rules（次日开盘）不同。
- 规则仅支持 `buy`/`sell` 两动作；做空、条件单等不支持（A 股 long-only）。
