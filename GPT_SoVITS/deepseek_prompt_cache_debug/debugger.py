from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from . import encoding_dsv4


CACHE_DEBUG_PHASE_KEY = "_d_sakiko_cache_debug_phase"
DEEPSEEK_PROMPT_CACHE_DEBUG_ENV = "D_SAKIKO_DEEPSEEK_PROMPT_CACHE_DEBUG"
DEEPSEEK_PROMPT_CACHE_DEBUG_CHARS_ENV = "D_SAKIKO_DEEPSEEK_PROMPT_CACHE_DEBUG_CHARS"
DEEPSEEK_PROMPT_CACHE_RICH_ENV = "D_SAKIKO_DEEPSEEK_PROMPT_CACHE_RICH"

_DEEPSEEK_TOKENIZER = None
_DEEPSEEK_TOKENIZER_LOAD_ATTEMPTED = False


def is_debug_enabled() -> bool:
    value = os.getenv(DEEPSEEK_PROMPT_CACHE_DEBUG_ENV, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _prompt_cache_debug_char_limit() -> int:
    raw_value = os.getenv(DEEPSEEK_PROMPT_CACHE_DEBUG_CHARS_ENV, "")
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 2000
    return max(200, value)


def _is_rich_enabled() -> bool:
    value = os.getenv(DEEPSEEK_PROMPT_CACHE_RICH_ENV, "1")
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _load_deepseek_tokenizer(logger):
    global _DEEPSEEK_TOKENIZER, _DEEPSEEK_TOKENIZER_LOAD_ATTEMPTED
    if _DEEPSEEK_TOKENIZER_LOAD_ATTEMPTED:
        return _DEEPSEEK_TOKENIZER

    _DEEPSEEK_TOKENIZER_LOAD_ATTEMPTED = True
    tokenizer_dir = Path(__file__).resolve().parent / "tokenizer"
    if not tokenizer_dir.exists():
        logger.warning("DeepSeek tokenizer 目录不存在，无法生成 prompt cache 边界预览：%s", tokenizer_dir)
        return None

    try:
        from transformers import AutoTokenizer

        _DEEPSEEK_TOKENIZER = AutoTokenizer.from_pretrained(str(tokenizer_dir), trust_remote_code=True)
    except Exception as exc:
        logger.warning("加载 DeepSeek tokenizer 失败，无法生成 prompt cache 边界预览：%s", exc)
        _DEEPSEEK_TOKENIZER = None
    return _DEEPSEEK_TOKENIZER


def _object_to_mapping(value: object) -> Optional[dict[str, object]]:
    if isinstance(value, dict):
        return dict(value)

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dict(dumped)
        except Exception:
            return None

    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        try:
            dumped = dict_method()
            if isinstance(dumped, dict):
                return dict(dumped)
        except Exception:
            return None

    return None


def extract_prompt_cache_usage(response: object) -> Optional[dict[str, object]]:
    response_mapping = _object_to_mapping(response)
    usage_obj: object = None
    if response_mapping is not None:
        usage_obj = response_mapping.get("usage")
    if usage_obj is None:
        usage_obj = getattr(response, "usage", None)

    usage_mapping = _object_to_mapping(usage_obj)
    if usage_mapping is None:
        return None

    if "prompt_cache_hit_tokens" not in usage_mapping or "prompt_cache_miss_tokens" not in usage_mapping:
        return None

    result: dict[str, object] = {
        "prompt_cache_hit_tokens": usage_mapping.get("prompt_cache_hit_tokens"),
        "prompt_cache_miss_tokens": usage_mapping.get("prompt_cache_miss_tokens"),
    }
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if key in usage_mapping:
            result[key] = usage_mapping[key]
    return result


def _safe_json_for_debug(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(value)


def _clip_debug_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = max(1, limit // 2)
    return text[:half] + "\n...<debug text truncated>...\n" + text[-half:]


def _prepare_v4_debug_messages(request_kwargs: dict[str, object]) -> Optional[list[dict[str, object]]]:
    raw_messages = request_kwargs.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        return None

    messages = [dict(one) if isinstance(one, dict) else {"role": "user", "content": str(one)} for one in raw_messages]
    tools = request_kwargs.get("tools")
    response_format = request_kwargs.get("response_format")
    if not tools and not response_format:
        return messages

    target_index = 0
    for index, message in enumerate(messages):
        if message.get("role") == "system":
            target_index = index
            break

    if tools:
        messages[target_index]["tools"] = tools
    if response_format:
        messages[target_index]["response_format"] = response_format
    return messages


def _thinking_mode_from_request(request_kwargs: dict[str, object]) -> str:
    thinking = request_kwargs.get("thinking")
    if isinstance(thinking, dict):
        thinking_type = str(thinking.get("type") or "").lower()
        if thinking_type == "enabled":
            return "thinking"
        if thinking_type == "disabled":
            return "chat"

    extra_body = request_kwargs.get("extra_body")
    if isinstance(extra_body, dict):
        extra_thinking = extra_body.get("thinking")
        if isinstance(extra_thinking, dict):
            thinking_type = str(extra_thinking.get("type") or "").lower()
            if thinking_type == "enabled":
                return "thinking"
            if thinking_type == "disabled":
                return "chat"
    return "chat"


def _reasoning_effort_from_request(request_kwargs: dict[str, object]) -> Optional[str]:
    effort = request_kwargs.get("reasoning_effort")
    extra_body = request_kwargs.get("extra_body")
    if effort is None and isinstance(extra_body, dict):
        effort = extra_body.get("reasoning_effort")
    if effort == "xhigh":
        effort = "max"
    if effort in {"max", "high"}:
        return str(effort)
    return None


def _render_deepseek_messages_prompt(request_kwargs: dict[str, object], logger) -> Optional[tuple[str, list[int]]]:
    tokenizer = _load_deepseek_tokenizer(logger)
    if tokenizer is None:
        return None

    try:
        debug_messages = _prepare_v4_debug_messages(request_kwargs)
        if debug_messages is None:
            return None

        rendered = encoding_dsv4.encode_messages(
            debug_messages,
            thinking_mode=_thinking_mode_from_request(request_kwargs),
            reasoning_effort=_reasoning_effort_from_request(request_kwargs),
        )
        token_ids = tokenizer.encode(rendered, add_special_tokens=False)
    except Exception as exc:
        logger.warning("DeepSeek messages prompt 渲染失败：%s", exc)
        return None
    return str(rendered), list(token_ids)


def _split_prompt_cache_boundary(
    request_kwargs: dict[str, object],
    hit_tokens: int,
    logger,
) -> dict[str, object]:
    rendered_result = _render_deepseek_messages_prompt(request_kwargs, logger)
    if rendered_result is None:
        return {}

    tokenizer = _load_deepseek_tokenizer(logger)
    if tokenizer is None:
        return {}

    rendered, token_ids = rendered_result
    boundary = min(max(0, hit_tokens), len(token_ids))
    try:
        hit_text = tokenizer.decode(token_ids[:boundary])
        miss_text = tokenizer.decode(token_ids[boundary:])
    except Exception as exc:
        logger.warning("DeepSeek prompt cache 边界解码失败：%s", exc)
        return {
            "rendered_prompt": rendered,
            "messages_prompt_tokens_estimate": len(token_ids),
            "cache_hit_boundary_tokens": boundary,
        }

    return {
        "rendered_prompt": rendered,
        "messages_prompt_tokens_estimate": len(token_ids),
        "cache_hit_boundary_tokens": boundary,
        "cache_hit_text": hit_text,
        "cache_miss_text": miss_text,
    }


def _build_prompt_cache_boundary_preview(boundary_parts: dict[str, object]) -> dict[str, object]:
    if not boundary_parts:
        return {}

    char_limit = _prompt_cache_debug_char_limit()
    hit_text = str(boundary_parts.get("cache_hit_text") or "")
    miss_text = str(boundary_parts.get("cache_miss_text") or "")
    rendered = str(boundary_parts.get("rendered_prompt") or "")
    preview: dict[str, object] = {
        "messages_prompt_tokens_estimate": boundary_parts.get("messages_prompt_tokens_estimate"),
        "cache_hit_boundary_tokens": boundary_parts.get("cache_hit_boundary_tokens"),
    }
    if hit_text or miss_text:
        preview.update({
            "cache_hit_text_tail": _clip_debug_text(hit_text[-char_limit:], char_limit),
            "cache_miss_text_head": _clip_debug_text(miss_text[:char_limit], char_limit),
        })
    if rendered:
        preview["rendered_prompt_preview"] = _clip_debug_text(rendered, char_limit)
    return preview


def _emit_rich_prompt(phase: str, model: str, boundary_parts: dict[str, object], logger) -> None:
    if not _is_rich_enabled() or not boundary_parts:
        return

    hit_text = str(boundary_parts.get("cache_hit_text") or "")
    miss_text = str(boundary_parts.get("cache_miss_text") or "")
    rendered = str(boundary_parts.get("rendered_prompt") or "")
    if not rendered:
        return

    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text
    except Exception as exc:
        logger.warning("rich 不可用，无法彩色打印 DeepSeek prompt cache 边界：%s", exc)
        logger.info(
            "deepseek_prompt_cache_rendered_prompt phase=%s model=%s boundary=%s\n%s\n<<< CACHE MISS START >>>\n%s",
            phase,
            model,
            boundary_parts.get("cache_hit_boundary_tokens"),
            hit_text,
            miss_text,
        )
        return

    text = Text()
    if hit_text:
        text.append(hit_text, style="green")
    text.append("\n\n<<< CACHE MISS START >>>\n\n", style="bold white on red")
    if miss_text:
        text.append(miss_text, style="yellow")

    title = (
        f"DeepSeek prompt cache phase={phase} model={model} "
        f"hit_boundary={boundary_parts.get('cache_hit_boundary_tokens')} "
        f"message_tokens_est={boundary_parts.get('messages_prompt_tokens_estimate')}"
    )
    Console(stderr=True).print(Panel(text, title=title, border_style="cyan"))


def log_prompt_cache_usage(response: object, request_kwargs: dict[str, object], logger) -> None:
    if not is_debug_enabled():
        return

    usage = extract_prompt_cache_usage(response)
    if usage is None:
        return

    hit_tokens = int(usage.get("prompt_cache_hit_tokens") or 0)
    miss_tokens = int(usage.get("prompt_cache_miss_tokens") or 0)
    total_cache_tokens = hit_tokens + miss_tokens
    hit_rate = "0.00%"
    if total_cache_tokens > 0:
        hit_rate = f"{hit_tokens / total_cache_tokens * 100:.2f}%"

    model = str(request_kwargs.get("model") or getattr(response, "model", "") or "")
    phase = str(request_kwargs.get(CACHE_DEBUG_PHASE_KEY) or "unknown")
    logger.info(
        "deepseek_prompt_cache phase=%s model=%s hit=%s miss=%s hit_rate=%s prompt=%s completion=%s total=%s",
        phase,
        model,
        hit_tokens,
        miss_tokens,
        hit_rate,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        usage.get("total_tokens"),
    )
    debug_payload: dict[str, object] = {
        "phase": phase,
        "model": model,
        "stream": request_kwargs.get("stream"),
        "timeout": request_kwargs.get("timeout"),
        "max_tokens": request_kwargs.get("max_tokens"),
        "tool_choice": request_kwargs.get("tool_choice"),
        "has_tools": bool(request_kwargs.get("tools")),
        "response_format": request_kwargs.get("response_format"),
        "usage": usage,
        "messages": request_kwargs.get("messages"),
    }
    if request_kwargs.get("tools"):
        debug_payload["tools"] = request_kwargs.get("tools")
    boundary_parts = _split_prompt_cache_boundary(request_kwargs, hit_tokens, logger)
    debug_payload.update(_build_prompt_cache_boundary_preview(boundary_parts))
    _emit_rich_prompt(phase, model, boundary_parts, logger)
