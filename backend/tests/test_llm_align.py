"""Tests for DeepSeek multi-turn alignment (MockTransport)."""
import copy
import json

import httpx
import pytest

from backend.llm_align import DeepSeekAligner, LLMNotConfigured, LLMParseError, extract_json
from backend.schema import REFERENCE_STRATEGY


def make_aligner(handler, **kw):
    return DeepSeekAligner(
        "https://api.deepseek.com", "sk-test", transport=httpx.MockTransport(handler), **kw
    )


def reply(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


USER_MSG = [{"role": "user", "content": "阴跌之后等急跌，均线拧到一块是止跌信号"}]


def test_extract_json_plain_fenced_and_embedded():
    assert extract_json('{"a":1}') == {"a": 1}
    assert extract_json('```json\n{"a":1}\n```') == {"a": 1}
    assert extract_json('前置说明 {"a":1} 后缀') == {"a": 1}
    with pytest.raises(LLMParseError):
        extract_json("完全没有大括号的内容")


def test_clarify_round():
    def handler(request):
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["content"].startswith("阴跌")
        return httpx.Response(200, json=reply(json.dumps({
            "type": "clarify",
            "understanding": "你希望捕捉超跌后的反转",
            "questions": ["关键均线是哪条？", "止损多少？"],
        }, ensure_ascii=False)))

    out = make_aligner(handler).align(USER_MSG)
    assert out["type"] == "clarify"
    assert out["questions"] == ["关键均线是哪条？", "止损多少？"]
    assert out["round"] == 1
    assert out["understanding"].startswith("你希望")


def test_config_round_validates_and_fills_source():
    cfg = copy.deepcopy(REFERENCE_STRATEGY)

    def handler(request):
        return httpx.Response(200, json=reply(json.dumps({
            "type": "config", "config": cfg, "summary": "完成", "warnings": ["止损默认8%"],
        }, ensure_ascii=False)))

    out = make_aligner(handler).align(USER_MSG)
    assert out["type"] == "config"
    assert out["config"]["source_text"] == USER_MSG[0]["content"]
    assert out["config"]["parse_engine"] == "llm"
    assert out["config"]["universe"]["code"] == "000300.SH"
    assert out["warnings"] == ["止损默认8%"]
    from backend.schema import StrategyConfig
    StrategyConfig.model_validate(out["config"])


def test_invalid_config_retries_then_succeeds():
    calls = {"n": 0}
    bad = copy.deepcopy(REFERENCE_STRATEGY)
    bad["indicators"][0]["id"] = "typo_ref_ok"
    # break a condition reference to force ValidationError
    bad["entry"]["conditions"][4]["right"] = "unknown_indicator"

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=reply(json.dumps({"type": "config", "config": bad}, ensure_ascii=False)))
        good = copy.deepcopy(REFERENCE_STRATEGY)
        return httpx.Response(200, json=reply(json.dumps({"type": "config", "config": good}, ensure_ascii=False)))

    out = make_aligner(handler).align(USER_MSG)
    assert calls["n"] == 2 and out["type"] == "config"


def test_invalid_config_twice_raises_with_raw():
    bad = copy.deepcopy(REFERENCE_STRATEGY)
    bad["entry"]["conditions"][4]["right"] = "unknown_indicator"

    def handler(request):
        return httpx.Response(200, json=reply(json.dumps({"type": "config", "config": bad}, ensure_ascii=False)))

    with pytest.raises(LLMParseError) as ei:
        make_aligner(handler).align(USER_MSG)
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

    out = make_aligner(handler).align(USER_MSG)
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
            "type": "config", "config": copy.deepcopy(REFERENCE_STRATEGY), "summary": "ok", "warnings": [],
        }, ensure_ascii=False)))

    out = make_aligner(handler).align(msgs)
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
        make_aligner(handler, max_attempts=1).align(msgs)


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
        make_aligner(handler).align(USER_MSG)


def test_network_error_wrapped():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LLMParseError, match="网络错误"):
        make_aligner(handler, retry_delay=0.001).align(USER_MSG)


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
    out = a.align(USER_MSG)
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
    out = a.align(USER_MSG)
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
    out = a.align(USER_MSG)
    assert calls["n"] == 2
    assert out["type"] == "clarify"


def test_chat_exhausts_network_retries():
    def handler(request):
        raise httpx.ConnectError("boom")

    a = make_aligner(handler, retry_delay=0.001, network_retries=1)
    with pytest.raises(LLMParseError, match="网络错误"):
        a.align(USER_MSG)
