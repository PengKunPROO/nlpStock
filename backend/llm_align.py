"""DeepSeek multi-turn strategy alignment: NL text → clarify questions → validated StrategyConfig."""
from __future__ import annotations

import json
import random
import ssl
import time
from typing import Any

import httpx
from pydantic import ValidationError

from .logging_setup import get_logger
from .schema import REFERENCE_STRATEGY, StrategyConfig

log = get_logger("llm")

MAX_ROUNDS = 4


def make_ssl_context() -> ssl.SSLContext:
    """TLS 1.3 与部分 CDN 边缘节点存在间歇性握手中断（EOF），强制降级到 TLS 1.2 规避。"""
    ctx = ssl.create_default_context()
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx

_INDICATOR_CATALOG = """可用指标 kind 目录（indicators 数组元素）：
- {"id":"ma5","kind":"MA","of":"close","n":5}      移动均线，of∈open/high/low/close/volume，n∈[2,250]
- {"id":"chg5","kind":"PCT_CHANGE","of":"close","n":5}  近n日涨跌幅%，(close/close[-n]-1)*100
- {"id":"vr","kind":"VRATIO","n":5}                 量比 = 当日成交量/近n日均量
- {"id":"body","kind":"BODY_RATIO"}                 阳线实体强度 (close-open)/(high-low)，∈[-1,1]
- {"id":"ush","kind":"UPPER_SHADOW_RATIO"}          上影线比例 (high-max(open,close))/(high-low)，∈[0,1]
- {"id":"conv","kind":"MA_CONVERGE","mas":["ma5","ma10","ma20"]}  均线粘合度% =(max-min)/close*100（mas引用已声明的MA指标id）
- {"id":"box20","kind":"BOX_TOP","n":20}            前箱体上沿 = 前n根K线最高价（不含当日）
指标 id 规则：小写snake_case，≤20字符，全局唯一。"""

_CONDITION_MODEL = """条件模型（entry/exit 的 conditions 数组元素）：
叶子条件：{"left":"close","op":">","right":"ma20","right_factor":0.98,"lag":0,"right_lag":0,"within":15,"note":"原文依据"}
- left/right：基础字段(open/high/low/close/volume)或已声明指标id；right 也可以是数字常量
- op：> >= < <= ==
- right_factor：可选，right×该系数再比较（如"不破箱体上沿2%容差"→0.98）
- lag/right_lag：左/右值取N个交易日前的值（"实体越来越短"→right=同指标,right_lag=1）
- within：可选回看窗口，最近within个交易日内任一天成立即成立（表达"阴跌之后等急跌"这类分阶段时序）
- note：必须写，标注对应的用户原话或量化理由，供用户审查
分组条件（最多一层嵌套）：{"logic":"all"|"any","conditions":[叶子或分组],"note":"..."}
entry.logic/exit.logic：all=全部满足，any=任一满足（离场通常用any）。"""

_PROTOCOL = """输出协议（严格遵守）：
每次只输出一个 JSON 对象，两种形态二选一：

1. 需要继续对齐：
{"type":"clarify","understanding":"当前我对策略的量化理解（要点式中文摘要）","questions":["问题1","问题2"]}

2. 对齐完成，产出配置：
{"type":"config","config":{完整的策略配置JSON},"summary":"最终量化口径说明","warnings":["未确认的默认值说明",...]}

对齐规则：
- 每轮最多追问 3 个问题，聚焦真正影响量化的模糊点：均线参数、幅度阈值、时间窗口、止损止盈、股票池范围
- 用户明确说过的数值必须原样采用，禁止修改
- 用户没说的参数用行业常见默认值，并把每个默认值写入 warnings 供审查
- 信息足够时立即输出 config，不要为了流程而追问；通常 1-2 轮收敛
- 分阶段叙事（先A后B再C）必须用 within 回看语义表达阶段先后
- config 必须可通过 StrategyConfig 校验：指标id全部声明、条件引用可解析、universe/risk/backtest_defaults 完整"""

_REFERENCE = "完整参考示例（用户描述“阴跌急跌止跌反转回踩进场”时的标准量化输出）：\n" + json.dumps(
    REFERENCE_STRATEGY, ensure_ascii=False, indent=1
)

SYSTEM_PROMPT = f"""你是A股量化策略架构师。任务：把用户的自然语言交易策略转化为可回测、可审查的量化配置（StrategyConfig JSON），必要时先向用户提问澄清。

{_INDICATOR_CATALOG}

{_CONDITION_MODEL}

config 其余字段：
- universe：{{"type":"index"|"sector"|"custom"|"all","code":"000300.SH","codes":[...]}}（index/sector需code，custom需codes，all=全市场）
- risk：{{"stop_loss_pct":8.0,"max_hold_days":30,"take_profit_pct":null}}（null=禁用）
- backtest_defaults：{{"start":"2025-01-01","end":"<今天>","initial_cash":1000000,"position_pct":20,"max_positions":5,"fee_bps":2.5,"stamp_tax_bps":5.0}}

{_PROTOCOL}

{_REFERENCE}"""

FORCE_CONFIG_MSG = "对齐轮数已达上限，本轮必须输出 type=config 的完整配置，不要再追问。"


class LLMNotConfigured(Exception):
    pass


class LLMParseError(Exception):
    def __init__(self, message: str, raw: str | None = None):
        super().__init__(message)
        self.raw = raw


def extract_json(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    parsed = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError as e:
                raise LLMParseError("模型输出不是有效 JSON", raw=content[:500]) from e
    if not isinstance(parsed, dict):
        raise LLMParseError("模型输出不是 JSON 对象", raw=content[:500])
    return parsed


class DeepSeekAligner:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "deepseek-chat",
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 2,
        network_retries: int = 2,
        retry_delay: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/chat/completions"):
            self.base_url = self.base_url[: -len("/chat/completions")]
        self.model = model
        self.max_attempts = max_attempts
        self.network_retries = network_retries
        self.retry_delay = retry_delay
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout,
            verify=make_ssl_context(),
            trust_env=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _chat(self, messages: list[dict]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 6000,
            "response_format": {"type": "json_object"},
        }
        delay = self.retry_delay
        last_err: Exception | None = None
        for attempt in range(self.network_retries + 1):
            if attempt:
                log.warning("LLM 网络重试 %d/%d", attempt, self.network_retries)
                time.sleep(delay + random.uniform(0, 0.4))
                delay *= 3
            try:
                resp = self._client.post(f"{self.base_url}/chat/completions", json=payload)
            except httpx.HTTPError as e:
                last_err = e
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = Exception(f"HTTP {resp.status_code}")
                continue
            if resp.status_code != 200:
                raise LLMParseError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                data = resp.json()
            except ValueError as e:
                raise LLMParseError(f"LLM 响应体非 JSON: {resp.text[:200]}") from e
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as e:
                raise LLMParseError(f"LLM 响应结构异常: {json.dumps(data, ensure_ascii=False)[:300]}") from e
            return content or ""
        log.error("LLM 网络错误(重试%d次): %s", self.network_retries, last_err)
        raise LLMParseError(f"LLM 网络错误: {last_err}")

    @staticmethod
    def _validate_messages(messages: list[dict]) -> None:
        if not messages or messages[0]["role"] != "user" or messages[-1]["role"] != "user":
            raise LLMParseError("对话格式无效：首条和末条必须是 user 消息")
        for m in messages:
            if m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str) or not m["content"].strip():
                raise LLMParseError("对话格式无效：角色/内容不合法")

    def align(self, messages: list[dict]) -> dict:
        self._validate_messages(messages)
        round_no = sum(1 for m in messages if m["role"] == "assistant") + 1
        forced = round_no >= MAX_ROUNDS
        convo: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if forced:
            convo.append({"role": "system", "content": FORCE_CONFIG_MSG})
        convo.extend({"role": m["role"], "content": m["content"]} for m in messages)

        last_raw: str | None = None
        for attempt in range(self.max_attempts):
            content = self._chat(convo)
            last_raw = content
            try:
                parsed = extract_json(content)
            except LLMParseError:
                convo.append({"role": "assistant", "content": content[:2000]})
                convo.append({"role": "user", "content": "输出不是有效 JSON。请严格按协议重新输出。"})
                continue
            ptype = parsed.get("type")
            if ptype == "clarify":
                questions = [str(q) for q in (parsed.get("questions") or []) if str(q).strip()]
                if not questions or forced:
                    convo.append({"role": "assistant", "content": content[:2000]})
                    convo.append({"role": "user", "content": FORCE_CONFIG_MSG + " 请直接输出配置。"})
                    continue
                log.info("LLM 追问 round=%d", round_no)
                return {
                    "type": "clarify",
                    "understanding": str(parsed.get("understanding") or ""),
                    "questions": questions[:3],
                    "round": round_no,
                }
            if ptype == "config":
                try:
                    cfg = self._build_config(parsed.get("config"), messages)
                except ValidationError as e:
                    log.warning("配置校验失败回炉: %s", e.errors()[:3])
                    convo.append({"role": "assistant", "content": content[:2000]})
                    convo.append({
                        "role": "user",
                        "content": f"配置校验失败：{e.errors()[:5]}。请修正后重新输出完整 config。",
                    })
                    continue
                log.info("LLM 产出配置 round=%d", round_no)
                return {
                    "type": "config",
                    "config": cfg,
                    "summary": str(parsed.get("summary") or ""),
                    "warnings": [str(w) for w in (parsed.get("warnings") or [])],
                }
            convo.append({"role": "assistant", "content": content[:2000]})
            convo.append({"role": "user", "content": '输出缺少有效 type 字段。请按协议输出 {"type":"clarify"|...} 或 {"type":"config"|...}。'})
        raise LLMParseError("LLM 连续输出无效，对齐失败", raw=(last_raw or "")[:500])

    @staticmethod
    def _build_config(raw: Any, messages: list[dict]) -> dict:
        if not isinstance(raw, dict):
            raise LLMParseError("config 不是对象")
        cfg = dict(raw)
        cfg["parse_engine"] = "llm"
        cfg["source_text"] = messages[0]["content"]
        validated = StrategyConfig.model_validate(cfg)
        return validated.model_dump()
