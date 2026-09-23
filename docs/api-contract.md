# API 契约与策略配置 Schema（单一事实源）

版本: 1.0 · 本文档是后端与前端并行开发的唯一契约。字段名、类型、枚举值必须严格一致。

## 0. 通用约定

- 后端: FastAPI，监听 `0.0.0.0:8000`（可配），同时托管 `frontend/` 静态文件（`GET /` → index.html）。
- 数据源: 扶摇（同花顺）API，`https://fuyao.aicubes.cn`，请求头 `X-api-key`。后端代理所有外部请求（前端永不直连外网）。
- 证券代码: 完整 thscode，如 `600519.SH`、`000001.SZ`、`000300.SH`（标准指数）、`881101.TI`（同花顺行业指数）。
- 时间: 毫秒 Unix 时间戳（`date_ms`）+ 展示用 `date`（`yyyy-MM-dd`）。
- 业务错误: HTTP 非 200 时返回 `{"error": {"code": "not_found|bad_request|upstream_error|conflict|internal", "message": "中文说明"}}`。HTTP 200 即业务成功。
- 所有响应对象的 JSON 键与本文件完全一致（snake_case）。

## 1. 策略配置 Schema（StrategyConfig）

自然语言策略经 AI（或内置规则解析器）量化后的产物。全程可审查、可编辑、可版本化。

```jsonc
{
  "name": "策略名（必填，≤40字）",
  "description": "策略一句话摘要",
  "source_text": "原始自然语言描述（保留存档）",
  "parse_engine": "rules | llm",            // 产生该配置的引擎
  "universe": {                              // 选股范围
    "type": "index | sector | custom | all", // 指数成分 / 行业板块成分 / 自选 / 全市场(慢)
    "code": "000300.SH",                     // type=index|sector 时必填
    "codes": ["600519.SH"]                   // type=custom 时必填
  },
  "indicators": [                            // 声明式指标，按 id 引用
    { "id": "ma5",  "kind": "MA",         "of": "close", "n": 5 },
    { "id": "vma5", "kind": "MA",         "of": "volume", "n": 5 },
    { "id": "chg5", "kind": "PCT_CHANGE", "of": "close", "n": 5 },      // 近n日涨跌幅%(close/close[-n]-1)*100
    { "id": "vr",   "kind": "VRATIO",     "n": 5 },                     // 量比 = volume / MA(volume,n)
    { "id": "body", "kind": "BODY_RATIO" },        // 阳线实体强度 (close-open)/(high-low)，∈[-1,1]
    { "id": "ush",  "kind": "UPPER_SHADOW_RATIO" },// 上影线比例 (high-max(open,close))/(high-low)，∈[0,1]
    { "id": "conv", "kind": "MA_CONVERGE", "mas": ["ma5","ma10","ma20"] }, // 均线粘合度% =(max-min)/close*100
    { "id": "box20","kind": "BOX_TOP",    "n": 20 }  // 前箱体上沿 = max(high[-n-1..-1])，不含当日
  ],
  "entry": { "logic": "all | any", "conditions": [Condition, ...] },  // 选股=入场信号
  "exit":  { "logic": "all | any", "conditions": [Condition, ...] },
  "risk": {
    "stop_loss_pct": 8.0,       // 止损%，null=禁用
    "max_hold_days": 30,        // 最长持仓自然交易日，null=禁用
    "take_profit_pct": null     // 止盈%，null=禁用
  },
  "backtest_defaults": {
    "start": "2025-01-01", "end": "2026-09-18",
    "initial_cash": 1000000, "position_pct": 20, "max_positions": 5,
    "fee_bps": 2.5, "stamp_tax_bps": 5.0
  }
}
```

### Condition（结构化条件，无字符串 DSL）

```jsonc
// 叶子条件: left op right [*right_factor]
{
  "left": "close",            // 字段: open/high/low/close/volume 或 indicators[].id
  "op": "> | >= | < | <= | ==",
  "right": "ma20",            // 同 left，或数值
  "right_factor": 0.98,       // 可选，right*right_factor 再比较
  "lag": 0,                   // 可选，左值取 lag 日前的值（默认0=当日）
  "right_lag": 0,             // 可选，右值取 right_lag 日前的值
  "within": 15                // 可选，回看窗口: 最近 within 个交易日内任一天成立即算成立（含当日）；缺省=必须当日成立
}
// 分组条件（最多一层嵌套，用于"实体越来越大"这类递进表述）
{ "logic": "all | any", "conditions": [Condition, ...] }
```

- 每个叶子/分组可带 `"note": "原文依据"`，审查界面展示"哪句话→哪条指标"。
- 校验规则: indicators 中 id 唯一；条件引用的 id 必须已声明；MA/PCT_CHANGE 的 n ∈ [2,250]；`of` ∈ {open,high,low,close,volume}。

## 2. 参考策略（量化基准样例：DeepSeek 对齐应产出的目标形态，测试与提示词共用）

```json
{
  "name": "阴跌急跌·止跌反转·回踩进场",
  "description": "阴跌急跌洗出空间，均线粘合止跌，底部放量站上20日线确认反转，缩量回踩不破箱体上沿进场；上影线/缩量力竭或破位离场。",
  "source_text": "1，阴跌之后等急跌，2，急跌之后等止跌，(均线拧到一块是止跌信号) 3，止跌之后等反转(底部放量，阳线实体越来越大，价格站上关键均线才是反转信号)，4，反转之后等进场(拉一波再缩量回踩不破前期箱体上沿，确认支撑有效，说明主力锁仓，这时候才可以进)，5，力竭出现，因为量能跟不上，出现上影线，越来越短的阳线，都是力竭信号，6，力竭后的离场，不舍得卖啊，这时落袋才是利润，7，离场之后等待回落，千万别追，8，回调支撑如果被击穿就不要进了。",
  "parse_engine": "rules",
  "universe": { "type": "index", "code": "000300.SH" },
  "indicators": [
    { "id": "ma5",   "kind": "MA",         "of": "close", "n": 5 },
    { "id": "ma10",  "kind": "MA",         "of": "close", "n": 10 },
    { "id": "ma20",  "kind": "MA",         "of": "close", "n": 20 },
    { "id": "vma5",  "kind": "MA",         "of": "volume", "n": 5 },
    { "id": "vr",    "kind": "VRATIO",     "n": 5 },
    { "id": "body",  "kind": "BODY_RATIO" },
    { "id": "ush",   "kind": "UPPER_SHADOW_RATIO" },
    { "id": "conv",  "kind": "MA_CONVERGE", "mas": ["ma5", "ma10", "ma20"] },
    { "id": "box20", "kind": "BOX_TOP",    "n": 20 },
    { "id": "chg5",  "kind": "PCT_CHANGE", "of": "close", "n": 5 },
    { "id": "chg10", "kind": "PCT_CHANGE", "of": "close", "n": 10 },
    { "id": "chg20", "kind": "PCT_CHANGE", "of": "close", "n": 20 }
  ],
  "entry": {
    "logic": "all",
    "conditions": [
      { "left": "chg20", "op": "<=", "right": -8, "within": 60, "note": "①阴跌：近期出现过20日累计跌幅≥8%" },
      { "left": "chg5",  "op": "<=", "right": -4, "within": 40, "note": "②急跌：近期出现过5日急跌≥4%" },
      { "left": "conv",  "op": "<=", "right": 2.5, "within": 15, "note": "③止跌：均线拧到一块（粘合度≤2.5%）" },
      { "left": "vr",    "op": ">=", "right": 1.8, "within": 10, "note": "④反转：底部放量（量比≥1.8）" },
      { "left": "close", "op": ">",  "right": "ma20", "note": "⑤站上关键均线20日线" },
      { "left": "chg10", "op": ">=", "right": 5, "within": 15, "note": "⑥拉一波：出现过10日涨幅≥5%" },
      { "left": "close", "op": ">=", "right": "box20", "right_factor": 0.98, "note": "⑦回踩不破前期箱体上沿（容差2%）" },
      { "left": "vr",    "op": "<=", "right": 1.1, "note": "⑧回踩缩量（当下量比≤1.1，主力锁仓）" }
    ]
  },
  "exit": {
    "logic": "any",
    "conditions": [
      { "left": "ush",   "op": ">",  "right": 0.4, "note": "力竭：长上影线" },
      { "left": "vr",    "op": "<",  "right": 0.5, "within": 3, "note": "力竭：量能跟不上（近3日出现过极端缩量）" },
      { "logic": "all", "conditions": [
          { "left": "body", "op": "<", "right": "body", "right_lag": 1, "note": "力竭：阳线实体越来越短" },
          { "left": "body", "op": ">", "right": 0 }
        ], "note": "阳线但实体连续收窄" },
      { "left": "close", "op": "<",  "right": "ma10", "note": "离场：跌破10日线" },
      { "left": "close", "op": "<",  "right": "box20", "right_factor": 0.95, "note": "离场：击穿箱体上沿5%（支撑失效）" }
    ]
  },
  "risk": { "stop_loss_pct": 8.0, "max_hold_days": 30, "take_profit_pct": null },
  "backtest_defaults": {
    "start": "2025-01-01", "end": "2026-09-18",
    "initial_cash": 1000000, "position_pct": 20, "max_positions": 5,
    "fee_bps": 2.5, "stamp_tax_bps": 5.0
  }
}
```

注意: `body < body(lag=1)` 的语义——left 与 right 均为 `body`，right 带 `lag:1`。

## 3. HTTP 端点

### 3.1 设置

```
GET /api/settings →
{ "fuyao_api_key": "sk-...(已配置时脱敏显示 sk-...****)", "fuyao_api_key_set": true,
  "llm_base_url": "https://api.deepseek.com", "llm_api_key_set": true, "llm_model": "deepseek-chat",
  "llm_available": true, "default_universe": {"type":"index","code":"000300.SH"} }

PUT /api/settings  body: { "fuyao_api_key"?: "...", "llm_base_url"?: "...", "llm_api_key"?: "...",
                            "llm_model"?: "...", "default_universe"?: {...} }
→ 同 GET 返回（键省略=不修改；空串=清除 llm/fuyao key）
```

### 3.2 策略解析（DeepSeek 多轮对齐：自然语言 → 配置草稿）

无状态协议：前端持有完整对话历史，每轮把全部消息回传，后端不存会话。未配置 LLM key 时返回 400 `{"error":{"code":"llm_not_configured","message":"请先在设置页配置 DeepSeek API Key"}}`。

```
POST /api/parse-strategy  body: {
  "messages": [ {"role":"user","content":"阴跌之后等急跌..."},           // 第1条必为 user
                {"role":"assistant","content":"<上轮AI回复原文>"},       // 原样回传
                {"role":"user","content":"五日线和十日线..."} ]          // 用户的回答
}
→ 两种响应（type 判别）:

{ "type": "clarify",                     // AI 需要继续对齐（系统强制最多 4 轮，第 4 轮必出 config）
  "understanding": "当前我对策略的量化理解（中文摘要，可含要点列表）",
  "questions": ["关键均线指哪条？5/10/20/60日线", "止损设多少百分比？"],   // 1~3 个聚焦问题
  "round": 2 }

{ "type": "config",                      // 对齐完成，产出配置草稿
  "config": { ...StrategyConfig... },    // name/description/source_text 已填好; parse_engine="llm"
  "summary": "最终量化口径说明（中文）",
  "warnings": ["策略未提及止损，已按默认 8% 填充，请在审查页确认", ...] }
```

- LLM 输出非法 JSON 或配置未过 schema 校验时，后端自动携错误回炉重试 1 次；仍失败 → 400 `{"error":{"code":"llm_parse_failed","message":"...","raw":"模型原始输出片段"}}`。
- `understanding`/`questions`/`summary` 均为纯文本（可含 \n），前端按对话气泡渲染；`config` 仅在 type=config 时存在。
- 对齐过程的系统提示词内嵌指标目录 + 条件模型 + 参考示例（§2），要求 AI 先复述理解再追问，不臆造用户没说的阈值（未提及的给默认值并写入 warnings）。

### 3.3 策略 CRUD（版本化）

```
GET  /api/strategies →
{ "items": [ { "id": 1, "name": "...", "description": "...", "version": 3,
               "updated_at": "2026-09-22T10:00:00", "parse_engine": "rules",
               "entry_count": 8, "exit_count": 5 } ] }

POST /api/strategies  body: { "config": {...StrategyConfig...} }   // 保存草稿为正式策略
→ { "id": 1, "version": 1, "created_at": "...", ...config }

GET  /api/strategies/{id} → { "id": 1, "version": 3, "current": {...config}, 
                              "versions": [ {"version":1,"created_at":"..."}, ... ] }

GET  /api/strategies/{id}/versions/{v} → { "id": 1, "version": 2, "config": {...该版本完整config} }

PUT  /api/strategies/{id}  body: { "config": {...StrategyConfig...} }   // 产生新版本（可审查的历史轨迹）
→ { "id": 1, "version": 4, ...config }

POST /api/strategies/{id}/restore/{v} → 恢复历史版本为新版本 → 同 PUT 返回

DELETE /api/strategies/{id} → { "ok": true }
```

### 3.4 选股（异步任务）

```
POST /api/screen  body: {
  "strategy_id": 1,                      // 与 config 二选一
  "config": { ...StrategyConfig... },    // 未保存的草稿也可直接跑
  "universe": null,                      // 可选，临时覆盖 config.universe（选股页切换股票池用）
  "as_of": null                          // null=最新；"2026-09-18"=按历史某日收盘选股（回看验证）
}
→ { "job_id": "j_abc123" }

GET /api/jobs/{job_id} →
{ "id": "j_abc123", "type": "screen | backtest", "status": "running | done | error",
  "progress": { "done": 132, "total": 300, "current": "600519.SH" },
  "error": null | "中文错误",
  "result": null | ScreenResult }

ScreenResult = {
  "as_of": "2026-09-18", "evaluated": 300, "matched_count": 7, "duration_ms": 4231,
  "universe": { "type": "index", "code": "000300.SH", "name": "沪深300" },
  "matched": [
    { "thscode": "600519.SH", "name": "贵州茅台", "last_close": 1253.8, "change_pct": 0.098,
      "signals": [ "①阴跌…", "②急跌…" ],           // 命中条件的 note 列表
      "snapshot": { "close": 1253.8, "vr": 1.92, "ma20": 1240.1 }  // 关键指标快照(字段值)
    } ] }
```

### 3.5 回测（异步任务）

```
POST /api/backtest  body: {
  "strategy_id": 1, "config": null,
  "start": "2025-01-01", "end": "2026-09-18",
  "initial_cash": 1000000, "position_pct": 20, "max_positions": 5,
  "fee_bps": 2.5, "stamp_tax_bps": 5.0,
  "universe": null            // 缺省用 config.universe
}
→ { "job_id": "j_def456" }

GET /api/jobs/{job_id} → result = BacktestResult:
{
  "params": { ...本次回测参数... },
  "metrics": {
    "total_return_pct": 23.4, "annual_return_pct": 11.7, "max_drawdown_pct": -12.6,
    "sharpe": 0.85, "win_rate_pct": 58.3, "profit_factor": 1.72,
    "trade_count": 24, "win_count": 14, "loss_count": 10,
    "avg_win_pct": 9.8, "avg_loss_pct": -4.2, "avg_hold_days": 11.3,
    "final_equity": 1234000, "start": "2025-01-01", "end": "2026-09-18"
  },
  "equity_curve": [ { "date": "2025-01-02", "value": 1000000, "drawdown_pct": 0.0 }, ... ],  // 逐交易日
  "trades": [
    { "code": "600519.SH", "name": "贵州茅台",
      "entry_date": "2025-03-04", "entry_price": 1480.2, "exit_date": "2025-03-28",
      "exit_price": 1560.5, "shares": 1300, "pnl": 103690.0, "pnl_pct": 5.42,
      "holding_days": 18, "exit_reason": "signal | stop_loss | max_hold | take_profit | end_of_data" }
  ]
}
```

回测执行语义（写进 UI 帮助文案）: T 日收盘出现信号 → T+1 日开盘价成交；买入按 `position_pct`% 当前权益开仓，同时持仓数 ≤ `max_positions`；止损盘中触发按止损价成交（以当日 low 校验）；卖出收印花税、双边收佣金（bps）。`exit_reason=end_of_data` 表示回测期末仍持仓按最后收盘价估值平仓（不计入胜率）。

### 3.6 K线与量能分析

```
GET /api/kline?thscode=600519.SH&period=1d&count=120
  period ∈ { "5d", "1d", "1w", "1M", "1y" }   // 5日K/日K/周K/月K/年K（由日线重采样）
→ {
  "thscode": "600519.SH", "name": "贵州茅台", "asset_type": "a-share | a-share-index | ths-index",
  "period": "1d", "last_close": 1253.8, "change_pct": 0.098,
  "bars": [
    { "date_ms": 1790006400000, "date": "2026-09-18",
      "open": 1252.15, "high": 1265.88, "low": 1248.1, "close": 1253.8,
      "volume": 2457294, "turnover": 3088526147.79,
      "vratio": 1.92,             // 量比 = 本周期成交量 / 前5根同周期K线均量（不含当期，A股量比口径）
      "vol_state": "incremental", // 见下方枚举
      "ma5": 1248.3, "ma10": 1240.1, "ma20": 1236.5, "ma60": 1250.2   // 当前周期上的均线，前几根不足为 null
    }, ... ],                      // 升序，最多 count 根
  "volume_summary": {
    "latest_vratio": 1.92, "latest_vol_state": "incremental",
    "trend": "增量放量",           // 近5根的量能趋势: "增量放量" | "量能平稳" | "持续缩量" | "量能波动"
    "note": "近3日量比持续≥1.5，价格重心上移"   // 一句话人话总结
  }
}

vol_state 枚举（对每根K线）:
  "surge"    vratio ≥ 2.0        显著放量
  "incremental" 1.5 ≤ vratio < 2.0  增量放量
  "flat"     0.7 ≤ vratio < 1.5  量能平稳
  "shrink"   vratio < 0.7        缩量
```

### 3.7 标的搜索 + 股票池选项

```
GET /api/search?q=茅台&limit=20 →
{ "items": [ { "thscode": "600519.SH", "name": "贵州茅台",
               "asset_type": "a-share",        // a-share | a-share-index | ths-index
               "exchange": "SH", "kline_available": true } ] }

GET /api/universe/options →
{ "indices": [ { "code": "000300.SH", "name": "沪深300", "count": null }, ... ],   // count 为提示性字段，可空
  "sectors": [ { "code": "881101.TI", "name": "种植业", "count": null }, ... ] }    // 同花顺行业指数(板块)
```

### 3.8 健康

```
GET /api/health → { "ok": true, "version": "1.0.0", "fuyao_ok": true, "db_ok": true, "time": "..." }
```

## 4. 前端页面契约（5 Tab 底部导航）

1. **策略**: 策略列表卡片 → 新建（**对齐式对话**：输入自然语言策略 + "填入示例策略"按钮 → AI 复述量化理解并追问 → 用户回答，最多4轮 → 产出配置草稿；无 LLM key 时给出引导去设置页）→ 审查页（分组编辑: 名称/范围/指标/入场/离场/风控/回测参数；每条条件显示 note 与可编辑数字；JSON 源码模式切换）→ 保存。版本历史（查看/恢复）。
2. **选股**: 选策略 + 股票池(指数/板块/自选/全市场) → 开始选股 → 进度条(done/total) → 结果列表（代码 名称 最新价 涨跌幅 命中信号 chips）→ 点行进 K线页。
3. **回测**: 选策略 + 区间/资金/仓位参数 → 运行 → 报告（指标卡片: 胜率/最大回撤/总收益/年化/夏比/盈亏比；净值曲线+回撤 Canvas 图；交易明细表）。
4. **图表**: 搜索(个股/指数/板块) → K线 Canvas（周期分段控件 5日/日/周/月/年；MA5/10/20/60 叠加线；量能柱按涨跌着色；触屏/点击单根K线 → 浮层显示 OHLC/涨跌幅/成交量/成交额/量比/量能状态/均线值）→ 顶部量能摘要 chip（trend+note）。
5. **设置**: 扶摇 API Key（脱敏）、LLM 配置（base_url/key/model，注明“任意 OpenAI 兼容接口”）、默认股票池、数据缓存状态。

## 5. 关键语义（后端实现必须遵守，前端文案必须一致）

- **选股信号**: entry 条件全部（logic=all）在最近一根日K收盘成立。
- **多轮对齐**: 解析是 LLM 驱动的对话过程（DeepSeek，OpenAI 兼容协议），AI 先复述理解、追问模糊点，收敛后产出配置草稿交用户审查；无内置规则兜底，未配置 key 时解析功能不可用（其他功能不受影响）。
- **within N**: 回看最近 N 个交易日（含当日），任一天该条件成立即为成立。
- **lag / right_lag**: 左/右值分别取 N 个交易日前的值。
- **回测无未来函数**: 信号在 T 收盘判定，成交在 T+1 开盘。
- **量比(vratio)** 在 K 线接口与策略指标中口径一致：当期量 / 前5期均量（不含当期）。周K等重采样周期同理在重采样后的序列上计算。
- **复权**: 个股历史K线统一 `adjust=forward`（前复权），指数无复权。
