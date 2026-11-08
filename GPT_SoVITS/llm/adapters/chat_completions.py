"""保留现有供应商路由的完整聊天请求适配。"""
from __future__ import annotations

import json
from ..replay import compatible, make_replay
from ..types import LLMConfig, LLMRequest, LLMResult
from .responses import mapping
from ..errors import LLMError


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
                    details = provider_options.get("openaiCompatible", {})
                    if isinstance(details, dict) and details.get("reasoningDetails"):
                        message["reasoning_details"] = details["reasoningDetails"]
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
    return LLMResult(text=text, reasoning=reasoning, tool_calls=calls, content=blocks,
                     usage=mapping(data.get("usage")), model=str(data.get("model") or config.model),
                     finish_reason=finish, protocol=make_replay(config, request.messages, blocks, provider_options))


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
    if config.sdk_chat:
        from openai import AsyncOpenAI
        async with AsyncOpenAI(api_key=config.api_key, base_url=config.base_url,
                               timeout=config.timeout, max_retries=0) as client:
            response = await client.chat.completions.create(
                model=config.model.removeprefix("openai/").removeprefix("deepseek/"),
                messages=build_messages(request, config), stream=False, **options)
    else:
        import litellm
        response = await litellm.acompletion(
            model=config.model, messages=build_messages(request, config), api_key=config.api_key,
            **({"base_url": config.base_url} if config.base_url else {}),
            timeout=config.timeout, stream=False, max_retries=0, drop_params=False, **options)
    return parse_response(response, request, config)
