"""统一请求层的离线协议、完整性和取消验证。"""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from unittest.mock import patch

import httpx
import pytest

from llm.adapters.responses import build_input, parse_response, execute
from llm.client import generate
from llm.errors import LLMError
from llm.types import CancellationToken, LLMConfig, LLMRequest, LLMResult


def config() -> LLMConfig:
    """创建只用于内存 HTTP 传输的连接，不使用用户配置。"""
    return LLMConfig("custom", "test-model", "test-key", "https://example.invalid/v1", "responses")


def test_responses_inline_image_and_completed_stream() -> None:
    """图片内联且不调用 Files，SSE 必须接收到完成事件才返回。"""
    from openai import AsyncOpenAI
    captured: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        """内存模拟 Responses SSE，验证实际发出的 HTTP JSON。"""
        assert request.url.path == "/v1/responses"
        captured.append(json.loads(request.content))
        response = {"id": "r1", "object": "response", "created_at": 1, "status": "completed",
                    "model": "test-model", "output": [{"id": "m1", "type": "message", "role": "assistant",
                    "status": "completed", "content": [{"type": "output_text", "text": "你好", "annotations": []}]}]}
        event = {"type": "response.completed", "sequence_number": 1, "response": response}
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              text="event: response.completed\ndata: " + json.dumps(event) + "\n\n")

    def client(**kwargs: object) -> AsyncOpenAI:
        """只允许 MockTransport，不产生真实网络请求。"""
        return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))

    request = LLMRequest([{"role": "user", "content": [{"type": "text", "text": "看看图片"},
                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}]}],
                         options={"response_format": {"type": "json_object"}})
    with patch("openai.AsyncOpenAI", side_effect=client):
        result = generate(request, config())
    assert result.text == "你好"
    assert captured[0]["store"] is False and captured[0]["stream"] is True
    assert "reasoning.encrypted_content" in captured[0]["include"]
    assert captured[0]["text"] == {"format": {"type": "json_object"}}
    assert captured[0]["input"][0]["content"][1] == {
        "type": "input_image", "image_url": "data:image/png;base64,YQ=="}
    assert "api_key" not in json.dumps(result.protocol)
    assert "test-key" not in json.dumps(result.protocol)


def test_openai_image_size_limit_checks_serialized_request_before_sending() -> None:
    """通过真实 SDK 和内存传输验证图片请求体超限时不会发送。"""
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        """记录任何实际进入传输的请求。"""
        calls.append(request)
        return httpx.Response(413)

    def http_client(**kwargs: object) -> httpx.AsyncClient:
        """保留真实的请求事件钩子，并替换底层 HTTP 传输。"""
        return httpx.AsyncClient(**kwargs, transport=httpx.MockTransport(handle))

    request = LLMRequest([{"role": "user", "content": [
        {"type": "image_url", "image_url": "data:image/png;base64," + "A" * 2048},
    ]}])
    connection = replace(config(), base_url="https://api.openai.com/v1", route="chatgpt-subscription")
    with (
        patch("openai.DefaultAsyncHttpxClient", side_effect=http_client),
        patch("llm.adapters.responses._OPENAI_IMAGE_REQUEST_MAX_BYTES", 1024),
        pytest.raises(LLMError, match="减少附件或压缩图片") as caught,
    ):
        asyncio.run(execute(request, connection))
    assert caught.value.code == "request_too_large"
    assert calls == []


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [(400, {"error": {"code": "context_length_exceeded"}}, "context_too_long"),
     (413, {"detail": "body too large"}, "request_too_large")],
)
def test_responses_distinguishes_context_and_transport_limits(
    status: int, body: dict[str, object], expected: str,
) -> None:
    """让服务端明确返回的上下文与请求体错误得到不同提示。"""
    from llm.errors import response_error

    error = response_error(status, body, "req-test")
    assert error.code == expected
    assert error.status_code == status
    assert error.request_id == "req-test"


def test_reasoning_and_tool_replay_requires_matching_origin_and_prefix() -> None:
    """加密推理和调用顺序可回传，来源切换或历史修改后不复用密文。"""
    request = LLMRequest([{"role": "user", "content": "查询"}])
    response = {"status": "completed", "model": "test-model", "output": [
        {"type": "reasoning", "id": "reason-1", "summary": [], "encrypted_content": "opaque-ciphertext"},
        {"type": "function_call", "id": "function-1", "call_id": "call-1", "name": "lookup", "arguments": "{}"}]}
    result = parse_response(response, request, config())
    history = request.messages + [{"role": "assistant", "content": "", "tool_calls": result.tool_calls,
                                   "_replay": result.protocol},
                                  {"role": "tool", "tool_call_id": "call-1", "content": "找到"}]
    replay = build_input(LLMRequest(history), config())
    assert [item.get("type") for item in replay[1:]] == ["reasoning", "function_call", "function_call_output"]
    assert replay[1]["encrypted_content"] == "opaque-ciphertext"
    changed = build_input(LLMRequest(history), replace(config(), api_key="another-key"))
    assert all(item.get("type") != "reasoning" for item in changed)
    history[0] = {"role": "user", "content": "修改过"}
    changed = build_input(LLMRequest(history), config())
    assert all(item.get("type") != "reasoning" for item in changed)


@pytest.mark.parametrize("status", ["incomplete", "failed", "in_progress"])
def test_incomplete_response_is_not_a_success(status: str) -> None:
    """部分文本不代表请求成功，不允许作为完整历史提交。"""
    with pytest.raises(LLMError, match="未完成"):
        parse_response({"status": status, "output": []}, LLMRequest([]), config())


def test_cancel_interrupts_internal_wait_and_closes_task() -> None:
    """取消能结束等待并清理内部任务，对外没有增量接口。"""
    token = CancellationToken()
    closed = threading.Event()

    async def transport(request: LLMRequest, connection: LLMConfig) -> LLMResult:
        """模拟长请求，取消时必须执行清理。"""
        try:
            await asyncio.sleep(10)
        finally:
            closed.set()
        return LLMResult(text="不应返回")

    timer = threading.Timer(0.05, token.cancel)
    timer.start()
    try:
        with pytest.raises(LLMError) as caught:
            generate(LLMRequest([]), config(), token, transport=transport)
        assert caught.value.code == "cancelled"
        assert closed.is_set()
    finally:
        timer.cancel()


def test_no_automatic_retry_on_transport_failure() -> None:
    """统一层不额外重放失败请求。"""
    attempts: list[int] = []

    async def fail(request: LLMRequest, connection: LLMConfig) -> LLMResult:
        """记录调用后模拟断网。"""
        attempts.append(1)
        raise httpx.ConnectError("offline")

    with pytest.raises(LLMError):
        generate(LLMRequest([]), config(), transport=fail)
    assert len(attempts) == 1


def test_responses_reject_server_history_and_file_references() -> None:
    """不允许服务端历史或 Files 图片绕过完整本地上下文。"""
    with pytest.raises(LLMError):
        asyncio.run(execute(LLMRequest([], options={"previous_response_id": "r1"}), config()))
    with pytest.raises(LLMError):
        build_input(LLMRequest([{"role": "user", "content": [{"type": "input_image", "file_id": "file-1"}]}]), config())


def test_custom_protocols_resolve_independent_configuration() -> None:
    """两个自定义提供商共存，切换协议不借用另一方模型或凭据。"""
    from types import SimpleNamespace
    from llm.configuration import resolve_config
    values = {"use_default_deepseek_api": False, "enable_custom_llm_api_provider": True,
              "custom_llm_api_protocol": "responses", "custom_responses_url": "https://response.invalid/v1",
              "custom_responses_model": "response-model", "custom_responses_key": "response-key",
              "custom_llm_api_url": "https://chat.invalid/v1", "custom_llm_api_model": "chat-model",
              "custom_llm_api_key": "chat-key"}
    settings = SimpleNamespace(**{key: SimpleNamespace(value=value) for key, value in values.items()})
    response = resolve_config(settings)
    settings.custom_llm_api_protocol.value = "chat-completions"
    chat = resolve_config(settings)
    assert response.protocol == "responses" and response.model == "response-model"
    assert response.api_key == "response-key" and response.base_url == "https://response.invalid/v1"
    assert chat.protocol == "chat-completions" and chat.model == "openai/chat-model"
    assert chat.api_key == "chat-key" and chat.base_url == "https://chat.invalid/v1"


def test_unknown_capability_does_not_silently_drop_parameters() -> None:
    """第三方模型别名没有可靠能力依据时保留参数，由服务端明确报告。"""
    from llm.capabilities import prepare_request
    request = LLMRequest([], options={"temperature": 0.7, "top_p": 0.9, "response_format": {"type": "json_object"}})
    prepared, notices = prepare_request(request, config())
    assert prepared.options == request.options and notices == []
    with patch("litellm.get_model_info", return_value={}), patch("litellm.get_supported_openai_params", return_value=["response_format"]):
        prepared, notices = prepare_request(request, replace(config(), base_url="https://api.openai.com/v1"))
    assert prepared.options == {"response_format": {"type": "json_object"}}
    assert len(notices) == 2


@pytest.mark.parametrize("exhausted", [False, True])
def test_modelscope_policy_preserved_at_shared_entry(exhausted: bool) -> None:
    """魔搭临时限流可以有限重试，日配额耗尽不能重复请求。"""
    from openai import RateLimitError
    from llm_provider.modelscope import ModelScopeQuotaExceededError
    attempts: list[int] = []
    headers = {"Retry-After": "0.001"}
    if exhausted:
        headers["ModelScope-RateLimit-Requests-Remaining"] = "0"
    response = httpx.Response(429, headers=headers, request=httpx.Request("POST", "https://example.invalid"))

    async def transport(request: LLMRequest, connection: LLMConfig) -> LLMResult:
        """首次限流，允许重试时下一次返回完整结果。"""
        attempts.append(1)
        if len(attempts) == 1:
            raise RateLimitError("limited", response=response, body={})
        return LLMResult(text="完成")

    connection = replace(config(), provider="modelscope", protocol="chat-completions")
    if exhausted:
        with pytest.raises(ModelScopeQuotaExceededError):
            generate(LLMRequest([]), connection, transport=transport)
        assert len(attempts) == 1
    else:
        assert generate(LLMRequest([]), connection, transport=transport).text == "完成"
        assert len(attempts) == 2
