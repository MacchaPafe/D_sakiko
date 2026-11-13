"""记录订阅请求的服务端缓存用量和本地可见输入差异。"""
from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from log import get_logger

from ..replay import fingerprint
from ..types import LLMConfig, LLMRequest, LLMResult


logger = get_logger(__name__)
_DEBUG_ENV = "D_SAKIKO_CHATGPT_PROMPT_CACHE_DEBUG"
_SETTINGS = (
    "instructions", "tools", "tool_choice", "allowed_tools", "parallel_tool_calls",
    "reasoning", "text", "service_tier", "prompt_cache_key", "prompt_cache_options",
)
_ROLES = ("developer", "system", "user", "assistant", "tool")
_ITEM_TYPES = ("reasoning", "message", "function_call", "function_call_output")
_MAX_SCOPES = 64
_lock = Lock()


@dataclass(frozen=True)
class _RequestFingerprint:
    """只保存输入项和请求设置的摘要。"""

    items: tuple[str, ...]
    settings: dict[str, str]


_previous: OrderedDict[tuple[str, str, str, str], _RequestFingerprint] = OrderedDict()


def _token_count(value: object) -> int | None:
    """接受非负整数用量，将缺失或无效值视为未知。"""
    return value if type(value) is int and value >= 0 else None


def _describe_input_change(previous: tuple[str, ...], current: tuple[str, ...], items: list[object]) -> str:
    """报告可见输入的首个变化位置，正文和原始标识不进入日志。"""
    for index, (old, new) in enumerate(zip(previous, current)):
        if old == new:
            continue
        item = items[index]
        role = item.get("role") if isinstance(item, dict) else None
        kind = item.get("type") if isinstance(item, dict) else None
        label = f" role={role}" if role in _ROLES else f" type={kind}" if kind in _ITEM_TYPES else ""
        return f"shared_items={index} change=modified first_changed=input[{index}]{label}"

    shared = min(len(previous), len(current))
    if len(current) > shared:
        return f"shared_items={shared} change=append_only appended_items={len(current) - shared}"
    if len(previous) > shared:
        return f"shared_items={shared} change=truncated first_changed=input[{shared}]"
    return f"shared_items={shared} change=unchanged"


def _describe_request_change(
    body: dict[str, object], scope: tuple[str, str, str, str],
) -> tuple[str, str]:
    """在有稳定对话标识时，用最终请求更新并比较同组基准。"""
    if not scope[1]:
        return "change=no_conversation", "n/a"
    items = body.get("input")
    if not isinstance(items, list):
        return "change=unavailable", "n/a"
    try:
        current = _RequestFingerprint(
            tuple(fingerprint(item) for item in items),
            {key: fingerprint(body[key]) for key in _SETTINGS if key in body},
        )
    except (TypeError, ValueError, RecursionError):
        return "change=unavailable", "n/a"

    with _lock:
        previous = _previous.pop(scope, None)
        _previous[scope] = current
        if len(_previous) > _MAX_SCOPES:
            _previous.popitem(last=False)

    if previous is None:
        return f"change=baseline input_items={len(current.items)}", "n/a"
    changed_settings = ",".join(
        key for key in _SETTINGS if previous.settings.get(key) != current.settings.get(key)
    )
    return _describe_input_change(previous.items, current.items, items), changed_settings or "none"


def log_prompt_cache(
    body: dict[str, object], request: LLMRequest, config: LLMConfig, result: LLMResult,
) -> None:
    """在显式开启诊断时记录用量和本地差异，缺失用量保留为未知。"""
    if os.getenv(_DEBUG_ENV) != "1":
        return
    details = result.usage.get("input_tokens_details")
    if not isinstance(details, dict):
        details = {}
    input_tokens = _token_count(result.usage.get("input_tokens"))
    cached = _token_count(details.get("cached_tokens"))
    written = _token_count(details.get("cache_write_tokens"))
    hit_rate = "n/a"
    if input_tokens and cached is not None and cached <= input_tokens:
        hit_rate = f"{cached / input_tokens:.2%}"

    model = result.model or config.model
    phase = request.cache_debug_phase
    logger.info(
        "chatgpt_prompt_cache model=%s phase=%s input=%s cached=%s hit_rate=%s cache_write=%s",
        model, phase, input_tokens if input_tokens is not None else "n/a",
        cached if cached is not None else "n/a", hit_rate, written if written is not None else "n/a",
    )
    diff, settings = _describe_request_change(body, (config.account_id, request.conversation_id, model, phase))
    logger.info("chatgpt_prompt_diff model=%s phase=%s %s settings_changed=%s", model, phase, diff, settings)
