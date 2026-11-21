"""保留现有供应商路由的完整聊天请求适配。"""
from __future__ import annotations

import json
from collections.abc import AsyncIterable, Awaitable, Callable
from inspect import isawaitable
from ..deepseek_chat import WEB_TOOLS, supports_chat_tools
from ..replay import compatible, make_replay
from ..types import LLMConfig, LLMRequest, LLMResult
from .responses import mapping
from ..errors import LLMError, normalize_error


def build_messages(request: LLMRequest, config: LLMConfig) -> list[dict[str, object]]:
    """移除内部元数据，只在来源与历史相容时回传推理内容。"""
    messages: list[dict[str, object]] = []
    prefix: list[dict[str, object]] = []
    for original in request.messages:
        message = {key: value for key, value in original.items() if not key.startswith("_")}
        if original.get("role") == "assistant":
            replay = original.get("_replay")
            if compatible(replay, config, prefix) and isinstance(replay, dict):
                content = replay.get("content", [])
                reasoning = "".join(str(part.get("text", "")) for part in content
                                    if isinstance(part, dict) and part.get("type") == "reasoning")
                if reasoning and "deepseek" in config.model.lower():
                    message["reasoning_content"] = reasoning
                provider_options = replay.get("providerOptions", {})
                if isinstance(provider_options, dict):
                    native = provider_options.get("deepseek", {})
                    if supports_chat_tools(config) and isinstance(native, dict) and native.get("server_tool_calls"):
                        message["server_tool_calls"] = native["server_tool_calls"]
                    details = provider_options.get("openaiCompatible", {})
                    if isinstance(details, dict) and details.get("reasoningDetails"):
                        message["reasoning_details"] = details["reasoningDetails"]
        # 读链接只识别最后一条用户消息；把应用附加的本轮要求并入这条消息，
        # 否则真正带 URL 的提问会被控制消息遮住。指纹仍按项目原始历史计算。
        control = message.get("content")
        if (supports_chat_tools(config) and message.get("role") == "user" and isinstance(control, str)
                and control.startswith(("<runtime_controls>", "<worldbook_context>"))
                and messages and messages[-1].get("role") == "user"):
            preceding = messages[-1].get("content")
            if isinstance(preceding, list):
                messages[-1]["content"] = [*preceding, {"type": "text", "text": control}]
            else:
                messages[-1]["content"] = f"{preceding}\n\n{control}"
        else:
            messages.append(message)
        prefix.append(original)
    return messages


def parse_response(response: object, request: LLMRequest, config: LLMConfig) -> LLMResult:
    """一次返回完整的标准响应，不向业务层暴露 LiteLLM 对象。"""
    data = mapping(response)
    choices = data.get("choices", []) or []
    if not choices:
        raise LLMError("incomplete", "模型没有返回完整 choices。")
    choice = mapping(choices[0])
    finish = str(choice.get("finish_reason") or "")
    if finish == "length":
        raise LLMError("incomplete", "模型输出达到长度限制，内容已被截断。")
    if finish not in {"stop", "tool_calls", "function_call"}:
        raise LLMError("incomplete", "模型响应未完成：" + finish)
    message = mapping(choice.get("message"))
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    reasoning = str(message.get("reasoning_content") or "")
    calls = [mapping(call) for call in message.get("tool_calls", []) or []]
    blocks: list[dict[str, object]] = []
    if reasoning:
        blocks.append({"type": "reasoning", "text": reasoning, "providerOptions": {}})
    for call in calls:
        function = mapping(call.get("function"))
        try:
            arguments = json.loads(str(function.get("arguments") or "{}"))
        except ValueError as exc:
            raise LLMError("invalid_tool_arguments", "模型工具参数不是完整 JSON。", original=exc) from exc
        blocks.append({"type": "tool-call", "toolCallId": call.get("id"),
                       "toolName": function.get("name"), "input": arguments, "providerOptions": {}})
    if text:
        blocks.append({"type": "text", "text": text, "providerOptions": {}})
    provider_options: dict[str, object] = {}
    if isinstance(message.get("reasoning_details"), list):
        details = [{key: value for key, value in mapping(part).items()
                    if key in {"type", "format", "index", "id", "text", "data", "signature", "summary"}}
                   for part in message["reasoning_details"]]
        provider_options = {"openaiCompatible": {"reasoningDetails": details}}
    if supports_chat_tools(config):
        native = {key: message[key] for key in ("server_tool_calls", "annotations") if message.get(key)}
        if native:
            provider_options["deepseek"] = native
    return LLMResult(text=text, reasoning=reasoning, tool_calls=calls, content=blocks, title=str(message.get("title") or ""),
                     usage=mapping(data.get("usage")), model=str(data.get("model") or config.model),
                     finish_reason=finish, protocol=make_replay(config, request.messages, blocks, provider_options))


async def _collect_stream(
    stream: AsyncIterable[object], request: LLMRequest, messages: list[dict[str, object]],
) -> object:
    """逐步转发台词，结束时按原格式重组工具、推理和用量。"""
    import litellm

    chunks: list[object] = []
    finish_reason = ""
    extensions: dict[str, object] = {}
    final_reasoning: list[str] | None = None
    observer = request.text_observer
    try:
        async for chunk in stream:
            # SDK 增量没有 LiteLLM 的字典接口，需要统一成重组器可直接使用的结构。
            chunks.append(chunk if isinstance(chunk, litellm.ModelResponseStream)
                          else litellm.ModelResponseStream(**mapping(chunk)))
            raw = mapping(chunk)
            if raw.get("error"):
                raise LLMError("incomplete", "聊天流返回错误：" + str(raw["error"]))
            choices = raw.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = mapping(choices[0])
            fields = mapping(choice.get("delta"))
            if final_reasoning is not None and isinstance(fields.get("reasoning_content"), str):
                final_reasoning.append(fields["reasoning_content"])
            if mapping(choice.get("server_tool_event")).get("type") == "step_reasoning_done":
                # 此前的推理已归入对应的服务端步骤，只回传最后一步的推理。
                final_reasoning = []
            for key in ("title", "annotations", "server_tool_calls"):
                if fields.get(key):
                    extensions[key] = fields[key]
            delta = fields.get("content")
            if observer is not None and isinstance(delta, str) and delta:
                observer.on_delta(delta)
            if choice.get("finish_reason"):
                finish_reason = str(choice["finish_reason"])
    finally:
        close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
        if callable(close):
            result = close()
            if isawaitable(result):
                await result
    if not finish_reason:
        raise LLMError("incomplete", "聊天流在模型确认结束前中断。")
    result = mapping(litellm.stream_chunk_builder(chunks, messages=messages))
    if final_reasoning is not None and result["choices"][0]["message"].get("reasoning_content"):
        extensions["reasoning_content"] = "".join(final_reasoning)
    if extensions:
        result["choices"][0]["message"].update(extensions)
    return result


def _stream_option_rejected(error: Exception, parameter: str) -> bool:
    """明确拒绝流式参数时才尝试更保守的请求。"""
    normalized = normalize_error(error)
    message = str(normalized).lower()
    return (
        normalized.status_code in {400, 422, 501}
        and parameter in message
        and any(marker in message for marker in ("unsupported", "not supported", "not support", "unrecognized"))
    )


async def _complete_chat(
    call: Callable[..., Awaitable[object]], request: LLMRequest, messages: list[dict[str, object]],
    options: dict[str, object],
) -> object:
    """仅在有人消费增量时开启流式，保留不兼容接口的完整响应路径。"""
    fallback = {key: value for key, value in options.items() if key != "stream_options"}
    if request.text_observer is None:
        return await call(stream=False, **fallback)
    stream_options = options.get("stream_options") or {"include_usage": True}
    try:
        try:
            response = await call(stream=True, stream_options=stream_options, **fallback)
        except Exception as error:
            if not _stream_option_rejected(error, "stream_options"):
                raise
            response = await call(stream=True, **fallback)
    except Exception as error:
        if not _stream_option_rejected(error, "stream"):
            raise
        return await call(stream=False, **fallback)
    if isinstance(response, AsyncIterable):
        return await _collect_stream(response, request, messages)
    return response


async def execute(request: LLMRequest, config: LLMConfig) -> LLMResult:
    """执行可取消的完整请求，供应商特殊参数留在本模块内部。"""
    options = dict(request.options)
    for key in ("stream", "api_key", "base_url", "timeout", "max_retries", "drop_params"):
        options.pop(key, None)
    if options.get("max_tokens") == -1:
        options.pop("max_tokens")
    if "deepseek" in config.model.lower():
        body = mapping(options.get("extra_body"))
        thinking = options.pop("thinking", None)
        if isinstance(thinking, dict):
            body["thinking"] = thinking
        effort = options.pop("reasoning_effort", None)
        if mapping(body.get("thinking")).get("type") != "disabled" and effort is not None:
            body["reasoning_effort"] = "max" if effort == "xhigh" else effort
        if mapping(body.get("thinking")).get("type") == "disabled":
            body.pop("reasoning_effort", None)
        if body:
            options["extra_body"] = body
    if request.tools:
        options.update(tools=request.tools, tool_choice=request.tool_choice)
    messages = build_messages(request, config)
    native_chat = supports_chat_tools(config) and (any(tool.get("type") in WEB_TOOLS for tool in request.tools)
                  or bool(mapping(options.get("extra_body")).get("title"))
                  or any(message.get("server_tool_calls") for message in messages))
    if config.sdk_chat or native_chat:
        from openai import AsyncOpenAI
        async with AsyncOpenAI(api_key=config.api_key, base_url=config.base_url or "https://api.deepseek.com",
                               timeout=config.timeout, max_retries=0) as client:
            sdk_options = dict(options, messages=messages, model=config.model.removeprefix("openai/").removeprefix("deepseek/"))
            response = await _complete_chat(client.chat.completions.create, request, messages, sdk_options)
    else:
        import litellm
        litellm_options = dict(options, model=config.model, messages=messages, api_key=config.api_key,
                              timeout=config.timeout, max_retries=0, drop_params=False)
        if config.base_url:
            litellm_options["base_url"] = config.base_url
        response = await _complete_chat(litellm.acompletion, request, messages, litellm_options)
    return parse_response(response, request, config)
