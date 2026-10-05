"""Tests for DeepSeek multi-turn alignment (MockTransport) — screening/trading 双类型输出."""
import copy
import json

import httpx
import pytest

from backend.llm_align import (
    DeepSeekAligner,
    LLMParseError,
    _SCREENING_REFERENCE,
    _TRADING_REFERENCE,
    _build_system_prompt,
    extract_json,
)
from backend.schema import ScreeningStrategy, TradingStrategy


def make_aligner(handler, **kw):
    return DeepSeekAligner(
        "https://api.deepseek.com", "sk-test", transport=httpx.MockTransport(handler), **kw
    )


def reply(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


USER_MSG = [{"role": "user", "content": "阴跌之后等急跌，均线拧到一块是止跌信号"}]

SCREENING_CFG = {
    "name": "超跌止跌反转",
    "description": "均线粘合后的超跌急跌，止跌反转入场",
    "universe": {"type": "index", "code": "000300.SH"},
    "indicators": [
        {"id": "ma5", "kind": "MA", "of": "close", "n": 5},
        {"id": "ma10", "kind": "MA", "of": "close", "n": 10},
        {"id": "ma20", "kind": "MA", "of": "close", "n": 20},
        {"id": "conv", "kind": "MA_CONVERGE", "mas": ["ma5", "ma10", "ma20"]},
        {"id": "pct3", "kind": "PCT_CHANGE", "of": "close", "n": 3},
        {"id": "body", "kind": "BODY_RATIO"},
    ],
    "entry": {
        "logic": "all",
        "conditions": [
            {"left": "conv", "op": "<=", "right": 2, "note": "均线拧到一块"},
            {"left": "pct3", "op": "<=", "right": -8, "within": 10, "note": "阴跌之后等急跌"},
            {"left": "body", "op": ">", "right": 0.5, "note": "止跌阳线"},
        ],
    },
}

TRADING_CFG = {
    "name": "分批补仓阶梯止盈",
    "description": "下跌补仓、上涨分批止盈",
    "indicators": [
        {"id": "dif", "kind": "MACD_DIF", "of": "close"},
        {"id": "dea", "kind": "MACD_DEA", "of": "close"},
    ],
    "rules": [
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": "<=", "right": -5}]},
         "action": "buy", "size_pct": 33.33, "max_times": 1, "note": "下跌5%补仓1/3"},
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": ">=", "right": 10}]},
         "action": "sell", "size_pct": 33.33, "max_times": 1, "note": "涨10%卖1/3"},
        {"when": {"logic": "all", "conditions": [{"left": "pnl_pct", "op": ">=", "right": 20}]},
         "action": "sell", "size_pct": 100, "note": "涨20%清仓"},
    ],
    "risk": {"stop_loss_pct": 8.0, "trailing_stop_pct": None, "max_hold_days": None, "take_profit_pct": None},
    "backtest_defaults": {"start": "2025-01-01", "end": "2025-12-31", "initial_cash": 1000000,
                          "position_pct": 20, "max_positions": 5, "fee_bps": 2.5, "stamp_tax_bps": 5.0},
}


# ---------------- extract_json ----------------

def test_extract_json_plain_fenced_and_embedded():
    assert extract_json('{"a":1}') == {"a": 1}
    assert extract_json('```json\n{"a":1}\n```') == {"a": 1}
    assert extract_json('前置说明 {"a":1} 后缀') == {"a": 1}
    with pytest.raises(LLMParseError):
        extract_json("完全没有大括号的内容")


def test_extract_json_non_object_and_malformed_raise_llm_parse_error():
    for s in ["[1,2,3]", '"just a string"', '{"a":1} {"b":2}']:
        with pytest.raises(LLMParseError):
            extract_json(s)


# ---------------- screening ----------------

def test_clarify_round_screening_prompt():
    captured = {}

    def handler(request):
        body = json.loads(request.content)
        captured["system"] = body["messages"][0]["content"]
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][1]["content"].startswith("阴跌")
        return httpx.Response(200, json=reply(json.dumps({
            "type": "clarify",
            "understanding": "你希望捕捉超跌后的反转",
            "questions": ["关键均线是哪条？", "止损多少？"],
        }, ensure_ascii=False)))

    out = make_aligner(handler).align(USER_MSG, "screening")
    assert out["type"] == "clarify"
    assert out["questions"] == ["关键均线是哪条？", "止损多少？"]
    assert out["round"] == 1
    assert out["understanding"].startswith("你希望")
    assert "ScreeningStrategy" in captured["system"]
    assert "选股" in captured["system"]
    assert "TradingStrategy" not in captured["system"]


def test_screening_config_validates_and_fills_source():
    def handler(request):
        return httpx.Response(200, json=reply(json.dumps({
            "type": "config", "config": copy.deepcopy(SCREENING_CFG), "summary": "完成", "warnings": ["止损默认8%"],
        }, ensure_ascii=False)))

    out = make_aligner(handler).align(USER_MSG, "screening")
    assert out["type"] == "config"
    cfg = out["config"]
    assert cfg["source_text"] == USER_MSG[0]["content"]
    assert cfg["parse_engine"] == "llm"
    assert cfg["universe"]["code"] == "000300.SH"
    assert cfg["entry"] and "rules" not in cfg and "risk" not in cfg
    assert out["warnings"] == ["止损默认8%"]
    ScreeningStrategy.parse_obj(cfg)


def test_screening_entry_with_hold_field_rejected():
    bad = copy.deepcopy(SCREENING_CFG)
    bad["entry"]["conditions"].append({"left": "pnl_pct", "op": "<=", "right": -5, "note": "误用持仓字段"})

    def handler(request):
        return httpx.Response(200, json=reply(json.dumps({"type": "config", "config": bad}, ensure_ascii=False)))

    with pytest.raises(LLMParseError) as ei:
        make_aligner(handler, max_attempts=1).align(USER_MSG, "screening")
    assert ei.value.raw


# ---------------- trading ----------------

def test_trading_config_validates_and_fills_source():
    def handler(request):
        return httpx.Response(200, json=reply(json.dumps({
            "type": "config", "config": copy.deepcopy(TRADING_CFG), "summary": "完成", "warnings": [],
        }, ensure_ascii=False)))

    out = make_aligner(handler).align(USER_MSG, "trading")
    assert out["type"] == "config"
    cfg = out["config"]
    assert cfg["source_text"] == USER_MSG[0]["content"]
    assert cfg["parse_engine"] == "llm"
    assert len(cfg["rules"]) == 3
    assert "entry" not in cfg and "universe" not in cfg
    assert cfg["risk"]["stop_loss_pct"] == 8.0
    TradingStrategy.parse_obj(cfg)


def test_trading_buy_rule_without_hold_field_rejected():
    bad = copy.deepcopy(TRADING_CFG)
    bad["rules"].append({
        "when": {"logic": "all", "conditions": [{"left": "close", "op": ">", "right": "dif"}]},
        "action": "buy", "size_pct": 33.33, "note": "未引用持仓字段的买入",
    })

    def handler(request):
        return httpx.Response(200, json=reply(json.dumps({"type": "config", "config": bad}, ensure_ascii=False)))

    with pytest.raises(LLMParseError) as ei:
        make_aligner(handler, max_attempts=1).align(USER_MSG, "trading")
    assert ei.value.raw


# ---------------- 提示词 / 类型分发 ----------------

def test_system_prompt_differs_by_strategy_type():
    s = _build_system_prompt("screening")
    t = _build_system_prompt("trading")
    for kw in ("ScreeningStrategy", "universe", "选股", "不引用持仓状态字段"):
        assert kw in s, f"screening 提示词缺少 {kw}"
    for kw in ("TradingStrategy", "risk", "backtest_defaults", "补仓", "必须引用持仓状态字段"):
        assert kw in t, f"trading 提示词缺少 {kw}"
    assert "TradingStrategy" not in s
    assert "ScreeningStrategy" not in t
    # 指标目录共用
    for kw in ("MACD_HIST", "持仓状态字段"):
        assert kw in s and kw in t


def test_system_prompt_includes_indicator_kinds():
    kinds = ("EMA", "MACD_DIF", "MACD_DEA", "MACD_HIST", "KDJ_K", "KDJ_D", "KDJ_J",
             "RSI", "BOLL_UP", "BOLL_MID", "BOLL_LOW")
    for prompt in (_build_system_prompt("screening"), _build_system_prompt("trading")):
        for kw in kinds:
            assert kw in prompt, f"指标目录缺少 {kw}"


def test_inline_reference_examples_pass_schema():
    ScreeningStrategy.parse_obj(dict(_SCREENING_REFERENCE))
    TradingStrategy.parse_obj(dict(_TRADING_REFERENCE))


def test_invalid_strategy_type_raises():
    def handler(request):
        return httpx.Response(200, json=reply("{}"))

    a = make_aligner(handler)
    with pytest.raises(LLMParseError, match="strategy_type"):
        a.align(USER_MSG, "swing")


# ---------------- 校验回炉 / 重试 ----------------

def test_invalid_config_retries_then_succeeds():
    calls = {"n": 0}
    bad = copy.deepcopy(SCREENING_CFG)
    bad["entry"]["conditions"][0]["left"] = "unknown_indicator"

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=reply(json.dumps({"type": "config", "config": bad}, ensure_ascii=False)))
        good = copy.deepcopy(SCREENING_CFG)
        return httpx.Response(200, json=reply(json.dumps({"type": "config", "config": good}, ensure_ascii=False)))

    out = make_aligner(handler).align(USER_MSG, "screening")
    assert calls["n"] == 2 and out["type"] == "config"


def test_invalid_config_twice_raises_with_raw():
    bad = copy.deepcopy(SCREENING_CFG)
    bad["entry"]["conditions"][0]["left"] = "unknown_indicator"

    def handler(request):
        return httpx.Response(200, json=reply(json.dumps({"type": "config", "config": bad}, ensure_ascii=False)))

    with pytest.raises(LLMParseError) as ei:
        make_aligner(handler).align(USER_MSG, "screening")
    assert ei.value.raw


def test_non_json_then_valid():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=reply("我觉得应该先问问你"))
        return httpx.Response(200, json=reply(json.dumps({
            "type": "clarify", "understanding": "ok", "questions": ["止损?"],
        }, ensure_ascii=False)))

    out = make_aligner(handler).align(USER_MSG, "screening")
    assert out["type"] == "clarify" and calls["n"] == 2


def test_forced_round_config():
    msgs = USER_MSG + [
        {"role": "assistant", "content": "先问：均线？"},
        {"role": "user", "content": "20日线"},
        {"role": "assistant", "content": "再问：止损？"},
        {"role": "user", "content": "8%"},
        {"role": "assistant", "content": "还差一个问题"},
        {"role": "user", "content": "没了，出配置"},
    ]
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        body = json.loads(request.content)
        system_tail = [m for m in body["messages"] if m["role"] == "system"]
        assert len(system_tail) == 2  # force message appended
        if calls["n"] == 1:
            return httpx.Response(200, json=reply(json.dumps({
                "type": "clarify", "understanding": "还想问", "questions": ["再问一个"],
            }, ensure_ascii=False)))
        return httpx.Response(200, json=reply(json.dumps({
            "type": "config", "config": copy.deepcopy(SCREENING_CFG), "summary": "ok", "warnings": [],
        }, ensure_ascii=False)))

    out = make_aligner(handler).align(msgs, "screening")
    assert out["type"] == "config" and calls["n"] == 2


def test_forced_round_still_clarify_raises():
    msgs = USER_MSG + [
        {"role": "assistant", "content": "问1"},
        {"role": "user", "content": "答1"},
        {"role": "assistant", "content": "问2"},
        {"role": "user", "content": "答2"},
        {"role": "assistant", "content": "问3"},
        {"role": "user", "content": "答3"},
    ]

    def handler(request):
        return httpx.Response(200, json=reply(json.dumps({
            "type": "clarify", "understanding": "u", "questions": ["q"],
        }, ensure_ascii=False)))

    with pytest.raises(LLMParseError, match="对齐失败"):
        make_aligner(handler, max_attempts=1).align(msgs, "screening")


# ---------------- 消息校验 / 传输 ----------------

def test_message_validation():
    def handler(request):
        return httpx.Response(200, json=reply("{}"))

    a = make_aligner(handler)
    with pytest.raises(LLMParseError):
        a.align([])
    with pytest.raises(LLMParseError):
        a.align([{"role": "assistant", "content": "hi"}])
    with pytest.raises(LLMParseError):
        a.align([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}])


def test_http_error_wrapped():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "Authentication Fails"}})

    with pytest.raises(LLMParseError, match="401"):
        make_aligner(handler).align(USER_MSG, "screening")


def test_network_error_wrapped():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LLMParseError, match="网络错误"):
        make_aligner(handler, retry_delay=0.001).align(USER_MSG, "screening")


def test_base_url_normalization():
    a = DeepSeekAligner("https://api.deepseek.com/v1", "sk-x")
    assert a.base_url == "https://api.deepseek.com/v1"
    b = DeepSeekAligner("https://api.deepseek.com/chat/completions", "sk-x")
    assert b.base_url == "https://api.deepseek.com"
    a.close(); b.close()


def test_ssl_context_forces_tls12():
    import ssl

    from backend.llm_align import make_ssl_context

    ctx = make_ssl_context()
    assert ctx.maximum_version == ssl.TLSVersion.TLSv1_2


def test_chat_retries_transient_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("EOF occurred in violation of protocol")
        return httpx.Response(200, json=reply(json.dumps(
            {"type": "clarify", "understanding": "ok", "questions": ["q"]}, ensure_ascii=False)))

    a = make_aligner(handler, retry_delay=0.001)
    out = a.align(USER_MSG, "screening")
    assert calls["n"] == 2
    assert out["type"] == "clarify"


def test_chat_retries_429_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(200, json=reply(json.dumps(
            {"type": "clarify", "understanding": "ok", "questions": ["q"]}, ensure_ascii=False)))

    a = make_aligner(handler, retry_delay=0.001)
    out = a.align(USER_MSG, "screening")
    assert calls["n"] == 2
    assert out["type"] == "clarify"


def test_chat_retries_5xx_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(502, json={})
        return httpx.Response(200, json=reply(json.dumps(
            {"type": "clarify", "understanding": "ok", "questions": ["q"]}, ensure_ascii=False)))

    a = make_aligner(handler, retry_delay=0.001)
    out = a.align(USER_MSG, "screening")
    assert calls["n"] == 2
    assert out["type"] == "clarify"


def test_chat_exhausts_network_retries():
    def handler(request):
        raise httpx.ConnectError("boom")

    a = make_aligner(handler, retry_delay=0.001, network_retries=1)
    with pytest.raises(LLMParseError, match="网络错误"):
        a.align(USER_MSG, "screening")


def test_deepseek_client_trust_env_false(monkeypatch):
    real = httpx.Client
    captured = {}

    def fake(*args, **kwargs):
        captured.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr("backend.llm_align.httpx.Client", fake)
    a = DeepSeekAligner("https://api.deepseek.com", "sk-test")
    a.close()
    assert captured.get("trust_env") is False


def test_chat_malformed_body_raises_llm_parse_error():
    def handler(request):
        return httpx.Response(200, content=b"not json at all")

    a = make_aligner(handler, retry_delay=0.001)
    with pytest.raises(LLMParseError, match="响应体非 JSON"):
        a.align(USER_MSG, "screening")


def test_chat_unexpected_shape_raises_llm_parse_error():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": None}]})

    a = make_aligner(handler, retry_delay=0.001)
    with pytest.raises(LLMParseError, match="结构异常"):
        a.align(USER_MSG, "screening")
