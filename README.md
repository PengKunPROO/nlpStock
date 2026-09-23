# 策略选股器 · NLP 量化选股与回测

自然语言描述交易策略 → DeepSeek 多轮对齐量化成可审查的指标配置 → 全市场/指数/板块选股 → 历史回测（胜率、最大回撤、净值曲线）→ K线量能分析。前后端可在手机上闭环运行。

## 功能

| 模块 | 说明 |
|---|---|
| 策略 | 自然语言输入，AI 先复述量化理解并追问模糊点（最多4轮），产出可审查配置（指标/入场/离场/风控），版本化保存 |
| 选股 | 股票池（指数成分 / 行业板块 / 自选 / 全市场）+ 异步任务进度；命中结果含原文依据与指标快照 |
| 回测 | T+1开盘成交、盘中止损/止盈、佣金+印花税；胜率/盈亏比/最大回撤/夏普/净值曲线/交易明细 |
| 图表 | 个股/指数/板块 K线（5日/日/周/月/年），MA5/10/20/60，量比与增量放量/缩量标注，触屏点K线看明细 |
| 设置 | 扶摇数据 Key、DeepSeek（或任意 OpenAI 兼容）LLM、默认股票池 |

## 架构

```
backend/   FastAPI（纯 Python，Termux 可跑）
  ├─ fuyao.py        扶摇(同花顺)数据客户端：限速/重试/信封
  ├─ data_service.py 缓存感知取数、股票池解析、标的检索
  ├─ llm_align.py    DeepSeek 多轮对齐（JSON mode + 校验回炉）
  ├─ indicators.py   指标引擎（MA/量比/涨跌幅/实体/上影/粘合/箱体）
  ├─ conditions.py   条件评估（within 回看 / lag / 系数 / 分组）
  ├─ screener.py     并行选股
  ├─ backtest.py     回测引擎（无未来函数）
  ├─ kline.py        周期重采样 + 量能分析
  └─ api.py / app.py REST 路由 + 静态托管 + 异步任务
frontend/  移动优先 SPA（vanilla ES，无构建步骤，Apple 风格）
docs/api-contract.md  前后端契约（单一事实源）
```

## 运行

### PC / 服务器
```bash
pip install -r requirements.txt
python -m backend.app --host 0.0.0.0 --port 8000
# 浏览器打开 http://localhost:8000
```

### 安卓（Termux，前后端手机闭环）
```bash
# 方式一：一键脚本
bash deploy/termux_bootstrap.sh

# 方式二：手动
pkg update && pkg install python python-pip git
pip install fastapi uvicorn httpx
python -m backend.app --host 0.0.0.0 --port 8000
```
手机浏览器打开 `http://localhost:8000`，菜单 →「添加到主屏幕」，即可全屏当 App 用。

### 打包原生 APK（调研结论，见 deploy/README.md）
本机无 Android SDK 无法在此构建验证，工程脚手架已就绪，见 `deploy/apk/`。

## 数据源与密钥

- 行情数据：扶摇（同花顺）`https://fuyao.aicubes.cn`，请求头 `X-api-key`。接口见 `llmWiki/llms-full.txt`。
- AI 解析：DeepSeek `https://api.deepseek.com`（OpenAI 兼容，设置页可换成智谱/Kimi/自建）。
- 两个 Key 均已在设置页预置（`backend/app.py` 默认值）；本机数据库 `data/app.db` 持久化，可在设置页更换。

## 关键语义（与前端文案一致）

- 量比 vratio = 当期成交量 / 前5期均量（不含当期，A股口径）。
- 选股信号 = entry 条件全部满足（logic=all）于最近收盘；`within N` 表示最近 N 日内任一天成立。
- 回测无未来函数：T 收盘信号 → T+1 开盘成交；止损跳空按开盘价、盘中触发按止损价；期末持仓按收盘估值不计入胜率。

## 测试

```bash
python -m pytest backend/tests -q        # 127 用例：schema/指标/条件/存储/客户端/数据服务/LLM对齐/K线/选股/回测/API
```

## 目录

- `frontend/dev/mock.js` — 独立 UI 预览（`http://localhost:8000/?mock=1`）
- `frontend/dev/qa/` — Playwright 视觉 QA 截图
- `llmWiki/llms-full.txt` — 扶摇 API 文档（GBK 编码）
