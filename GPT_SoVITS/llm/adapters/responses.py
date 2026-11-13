"""无服务端历史依赖的 Responses 请求与完整响应解析。"""
from __future__ import annotations

import json
from typing import cast

import httpx

from ..errors import LLMError, response_error
from ..response_routes import response_route
from ..replay import compatible, make_replay
from ..types import LLMConfig, LLMRequest, LLMResult


_OPENAI_RESPONSES_BASE_URL = "https://api.openai.com/v1"
# https://developers.openai.com/api/docs/guides/images-vision#image-input-requirements
_OPENAI_IMAGE_REQUEST_MAX_BYTES = 512_000_000


def mapping(value: object) -> dict[str, object]:
    """把 SDK 数据对象转换成普通字典，不保存 SDK 实例。"""
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    return dict(dump(mode="json")) if callable(dump) else {}


def content_parts(value: object) -> object:
    """把兼容聊天格式中的图片转换为内联 Responses 图片。"""
    if not isinstance(value, list):
        return value
    result: list[dict[str, object]] = []
    for part in value:
        if not isinstance(part, dict):
            raise LLMError("bad_request", "上下文包含无效内容块。")
        kind = part.get("type")
        if kind in {"text", "input_text"}:
            result.append({"type": "input_text", "text": part.get("text", "")})
        elif kind in {"image_url", "input_image"}:
            image = part.get("image_url", "")
            url = str(image.get("url", "")) if isinstance(image, dict) else str(image)
            if not url.startswith("data:image/") or ";base64," not in url:
                raise LLMError("unsupported_input", "Responses 图片必须使用本地图片的 base64 data URL。")
            result.append({"type": "input_image", "image_url": url})
        else:
            raise LLMError("unsupported_input", "Responses 暂不支持该附件内容类型。")
    return result


def replay_items(content: object) -> list[dict[str, object]]:
    """从共同协议材料恢复 Responses items，保持原有顺序。"""
    items: list[dict[str, object]] = []
    for raw in content if isinstance(content, list) else []:
        if not isinstance(raw, dict):
            continue
        providers = raw.get("providerOptions", {})
        options = providers.get("openai", {}) if isinstance(providers, dict) else {}
        options = options if isinstance(options, dict) else {}
        kind = raw.get("type")
        if kind == "reasoning":
            item: dict[str, object] = {"type": "reasoning", "summary": []}
            for source, target in (("itemId", "id"), ("reasoningEncryptedContent", "encrypted_content")):
                if options.get(source) is not None:
                    item[target] = options[source]
            if "encrypted_content" in item:
                items.append(item)
        elif kind == "tool-call":
            call = {"type": "function_call", "call_id": raw.get("toolCallId"),
                          "name": raw.get("toolName"),
                          "arguments": json.dumps(raw.get("input", {}), ensure_ascii=False)}
            if options.get("namespace"):
                call["namespace"] = options["namespace"]
            items.append(call)
        elif kind == "text":
            item = {"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": raw.get("text", ""), "annotations": []}]}
            if options.get("phase") is not None:
                item["phase"] = options["phase"]
            items.append(item)
    return items


def build_input(request: LLMRequest, config: LLMConfig) -> list[dict[str, object]]:
    """完整转换本地上下文，不使用 response ID 关联服务端历史。"""
    items: list[dict[str, object]] = []
    prefix: list[dict[str, object]] = []
    for message in request.messages:
        role = message.get("role")
        replay = message.get("_replay")
        if role == "assistant" and compatible(replay, config, prefix):
            items.extend(replay_items(cast(dict[str, object], replay).get("content")))
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": message.get("tool_call_id"),
                          "output": str(message.get("content", ""))})
        elif role == "assistant":
            if message.get("content"):
                items.append({"role": "assistant", "content": str(message["content"])})
            for call in message.get("tool_calls", []) or []:
                function = call.get("function", {})
                items.append({"type": "function_call", "call_id": call.get("id"),
                              "name": function.get("name"), "arguments": function.get("arguments", "{}")})
        else:
            items.append({"role": "developer" if role == "system" else role,
                          "content": content_parts(message.get("content", ""))})
        prefix.append(message)
    namespace = response_route(config.route).namespace
    if namespace:
        for item in items:
            if item.get("type") == "function_call":
                item.setdefault("namespace", namespace)
    return items


def parse_response(response: object, request: LLMRequest, config: LLMConfig) -> LLMResult:
    """提取完整有序内容和白名单协议字段，拒绝未完成的响应。"""
    data = mapping(response)
    if data.get("status") != "completed":
        raise LLMError("incomplete", "模型响应未完成：" + str(data.get("error") or data.get("incomplete_details") or data.get("status")))
    result = LLMResult(model=str(data.get("model") or config.model), usage=mapping(data.get("usage")))
    for raw in data.get("output", []) or []:
        item = mapping(raw)
        kind = item.get("type")
        options: dict[str, object] = {}
        if item.get("id"):
            options["itemId"] = item["id"]
        if item.get("namespace"):
            options["namespace"] = item["namespace"]
        if kind == "reasoning":
            summary = item.get("summary", []) or []
            text = "\n".join(str(mapping(part).get("text", "")) for part in summary)
            if item.get("encrypted_content") is not None:
                options["reasoningEncryptedContent"] = item["encrypted_content"]
            result.reasoning += text
            result.content.append({"type": "reasoning", "text": text,
                                   "providerOptions": {"openai": options}})
        elif kind == "function_call":
            arguments = str(item.get("arguments") or "{}")
            try:
                parsed = json.loads(arguments)
            except ValueError as exc:
                raise LLMError("invalid_tool_arguments", "模型工具参数不是完整 JSON。", original=exc) from exc
            result.tool_calls.append({"id": item.get("call_id"), "type": "function",
                                      "function": {"name": item.get("name"), "arguments": arguments}})
            result.content.append({"type": "tool-call", "toolCallId": item.get("call_id"),
                                   "toolName": item.get("name"), "input": parsed,
                                   "providerOptions": {"openai": options}})
        elif kind == "message":
            if item.get("phase") is not None:
                options["phase"] = item["phase"]
            for part in item.get("content", []) or []:
                block = mapping(part)
                if block.get("type") == "refusal":
                    raise LLMError("refusal", str(block.get("refusal") or "模型拒绝了该请求。"))
                if block.get("type") != "output_text":
                    raise LLMError("unsupported_output", "模型返回了尚未支持的消息内容。")
                text = str(block.get("text", ""))
                result.text += text
                result.content.append({"type": "text", "text": text,
                                       "providerOptions": {"openai": options}})
        else:
            raise LLMError("unsupported_output", "模型返回了尚未支持的服务端工具或内容。")
    result.finish_reason = "tool_calls" if result.tool_calls else "stop"
    result.protocol = make_replay(config, request.messages, result.content)
    return result


def prepare_body(request: LLMRequest, config: LLMConfig) -> tuple[dict[str, object], list[str]]:
    """先完成共同协议编码，再执行一次路由契约；普通 API 不受订阅规则影响。"""
    options = dict(request.options)
    extra = options.pop("extra_body", {})
    if not isinstance(extra, dict):
        raise LLMError("unsupported_parameter", "extra_body 必须是对象。")
    options.update(extra)
    options.pop("_response_headers", None)
    for forbidden in ("previous_response_id", "conversation", "background"):
        if forbidden in options:
            raise LLMError("unsupported_parameter", "不允许使用服务端历史或后台请求：" + forbidden)
    for key in ("stream", "store", "drop_params", "max_retries", "timeout", "api_key", "base_url"):
        options.pop(key, None)
    if "max_tokens" in options:
        maximum = options.pop("max_tokens")
        if isinstance(maximum, int) and maximum > 0:
            options["max_output_tokens"] = maximum
    effort = options.pop("reasoning_effort", None)
    thinking = options.pop("thinking", None)
    if effort is not None:
        options["reasoning"] = {"effort": effort}
    elif isinstance(thinking, dict) and thinking.get("type") == "disabled":
        options["reasoning"] = {"effort": "none"}
    elif isinstance(thinking, dict) and thinking.get("type") == "enabled":
        options["reasoning"] = {"effort": "medium"}
    response_format = options.pop("response_format", None)
    if isinstance(response_format, dict):
        fmt = dict(response_format)
        if fmt.get("type") == "json_schema":
            schema = fmt.get("json_schema", {})
            fmt = {"type": "json_schema", **(schema if isinstance(schema, dict) else {})}
        options["text"] = {"format": fmt}
    include = options.pop("include", [])
    options["include"] = list(dict.fromkeys([*(include if isinstance(include, list) else []),
                                            "reasoning.encrypted_content"]))
    if request.tools:
        options["tools"] = [{"type": "function", **cast(dict[str, object], tool.get("function", tool))}
                            for tool in request.tools]
        options["tool_choice"] = request.tool_choice
    options.update(model=config.model.removeprefix("openai/"), input=build_input(request, config),
                   store=False, stream=True)
    return response_route(config.route).prepare(options)


def _has_image_input(body: dict[str, object]) -> bool:
    """识别最终 Responses 请求中的内联图片。"""
    items = body.get("input")
    if not isinstance(items, list):
        return False
    for item in items:
        content = item.get("content") if isinstance(item, dict) else None
        if isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") == "input_image" for part in content
        ):
            return True
    return False


async def _validate_openai_image_request_size(request: httpx.Request) -> None:
    """在发送前检查 SDK 已序列化的图片请求体大小。"""
    if len(request.content) > _OPENAI_IMAGE_REQUEST_MAX_BYTES:
        raise LLMError("request_too_large", "OpenAI 图片请求体过大，请减少附件或压缩图片。")


async def execute(request: LLMRequest, config: LLMConfig, *,
                  prepared: tuple[dict[str, object], list[str]] | None = None) -> LLMResult:
    """内部消费 SSE 至完成；相同编解码和终止规则用于两种计费路由。"""
    from openai import AsyncOpenAI, DefaultAsyncHttpxClient
    body, diagnostics = prepared if prepared is not None else prepare_body(request, config)
    headers = request.options.get("_response_headers", {})
    base_url = config.base_url or _OPENAI_RESPONSES_BASE_URL
    client_options: dict[str, httpx.AsyncClient] = {}
    if base_url.rstrip("/") == _OPENAI_RESPONSES_BASE_URL and _has_image_input(body):
        client_options["http_client"] = DefaultAsyncHttpxClient(
            event_hooks={"request": [_validate_openai_image_request_size]},
        )
    async with AsyncOpenAI(api_key=config.api_key, base_url=base_url,
                           timeout=config.timeout, max_retries=0, **client_options) as client:
        try:
            stream = await client.responses.create(**body, **({"extra_headers": headers} if headers else {}))
        except Exception as exc:
            cause = exc.__cause__
            if isinstance(cause, LLMError) and cause.code == "request_too_large":
                raise cause from None
            response = getattr(exc, "response", None)
            if response is not None:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {"detail": response.text[:4000]}
                raise response_error(response.status_code, payload, response.headers.get("x-request-id", "")) from exc
            raise
        try:
            async for event in stream:
                kind = getattr(event, "type", "")
                if kind == "response.completed":
                    result = parse_response(event.response, request, config)
                    result.diagnostics.extend(diagnostics)
                    if config.route == "chatgpt-subscription":
                        from ..chatgpt.prompt_cache_debug import log_prompt_cache
                        log_prompt_cache(body, request, config, result)
                    return result
                if kind in {"response.failed", "response.incomplete", "error"}:
                    payload = mapping(getattr(event, "response", None)) or mapping(event)
                    if payload.get("error") or kind == "error":
                        # 流已开启，其 HTTP 状态是 200；错误语义来自事件，不伪造 400/429。
                        raw_response = getattr(stream, "response", None)
                        request_id = raw_response.headers.get("x-request-id", "") if raw_response is not None else ""
                        raise response_error(200, payload, request_id)
                    raise LLMError("incomplete", "模型响应未完成：" + kind)
        finally:
            await stream.close()
    raise LLMError("incomplete", "连接在收到 response.completed 之前结束。")
