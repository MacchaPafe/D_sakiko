from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Mapping, cast

from log import get_logger


logger = get_logger(__name__)
_OVERRIDES_FILE = Path(__file__).with_name("model_context_overrides.json")


_FILE_BLOCK_REMOVED = object()


def count_message_tokens(
    model: str,
    messages: list[dict[str, object]],
    *,
    file_token_cost: int | None = None,
) -> int:
    """使用 LiteLLM 计算消息 token 数，并按策略估算 file 内容块。"""
    from litellm import token_counter

    if file_token_cost is None:
        token_count = token_counter(model=model.strip(), messages=messages)
        return max(0, int(token_count))
    if isinstance(file_token_cost, bool) or not isinstance(file_token_cost, int):
        raise ValueError("file_token_cost 必须是正整数或 None。")
    if file_token_cost <= 0:
        raise ValueError("file_token_cost 必须是正整数或 None。")

    sanitized, file_block_count = _sanitize_file_blocks(messages)
    sanitized_messages = cast(list[dict[str, object]], sanitized)
    token_count = token_counter(
        model=model.strip(),
        messages=sanitized_messages,
    )
    return max(0, int(token_count)) + file_block_count * file_token_cost


def _sanitize_file_blocks(value: object) -> tuple[object, int]:
    """复制消息结构并移除 file 内容块，返回移除数量。"""
    if isinstance(value, list):
        sanitized_list: list[object] = []
        file_block_count = 0
        for item in value:
            sanitized_item, nested_count = _sanitize_file_blocks(item)
            file_block_count += nested_count
            if sanitized_item is not _FILE_BLOCK_REMOVED:
                sanitized_list.append(sanitized_item)
        return sanitized_list, file_block_count

    if isinstance(value, dict):
        if value.get("type") == "file":
            return _FILE_BLOCK_REMOVED, 1
        sanitized_dict: dict[object, object] = {}
        file_block_count = 0
        for key, item in value.items():
            sanitized_item, nested_count = _sanitize_file_blocks(item)
            file_block_count += nested_count
            if sanitized_item is not _FILE_BLOCK_REMOVED:
                sanitized_dict[key] = sanitized_item
        return sanitized_dict, file_block_count

    return value, 0


@lru_cache(maxsize=256)
def get_model_input_token_limit(model: str) -> int | None:
    """获取模型输入上下文 token 上限，优先使用 LiteLLM，缺失时查本地补充表。"""
    normalized_model = model.strip()
    if not normalized_model:
        return None

    local_override = _get_local_override_input_token_limit(normalized_model)
    litellm_limit = _get_litellm_model_input_token_limit(
        normalized_model,
        suppress_unmapped_debug=local_override is not None,
    )
    if litellm_limit is not None:
        return litellm_limit
    return local_override


def _get_litellm_model_input_token_limit(
    model: str,
    *,
    suppress_unmapped_debug: bool = False,
) -> int | None:
    """从 LiteLLM 模型信息表中读取输入 token 上限。"""
    try:
        from litellm import get_model_info

        info = get_model_info(model=model)
    except Exception as exc:
        if not suppress_unmapped_debug or not _is_litellm_unmapped_model_error(exc):
            logger.debug("查询 LiteLLM 模型上下文上限失败：%s", exc)
        return None

    if not isinstance(info, Mapping):
        return None
    raw_limit = info.get("max_input_tokens") or info.get("max_tokens")
    return _as_positive_int(raw_limit)


def _get_local_override_input_token_limit(model: str) -> int | None:
    """从本地模型上下文补充表中读取输入 token 上限。"""
    normalized_model = _normalize_model_name(model)
    for entry in _load_override_model_entries():
        if _override_entry_matches_model(entry, normalized_model):
            return _as_positive_int(entry.get("max_input_tokens"))
    return None


@lru_cache(maxsize=1)
def _load_override_model_entries() -> tuple[Mapping[str, object], ...]:
    """加载本地模型上下文补充表。"""
    try:
        with open(_OVERRIDES_FILE, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except FileNotFoundError:
        return ()
    except Exception as exc:
        logger.warning("读取模型上下文补充表失败：%s", exc)
        return ()

    if not isinstance(payload, Mapping):
        return ()
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return ()

    entries: list[Mapping[str, object]] = []
    for raw_entry in raw_models:
        if isinstance(raw_entry, Mapping):
            entries.append(raw_entry)
    return tuple(entries)


def _override_entry_matches_model(entry: Mapping[str, object], normalized_model: str) -> bool:
    """判断本地补充表中的一条记录是否匹配模型名。"""
    raw_id = entry.get("id")
    if isinstance(raw_id, str) and _normalize_model_name(raw_id) == normalized_model:
        return True

    raw_aliases = entry.get("aliases")
    if not isinstance(raw_aliases, list):
        return False
    for alias in raw_aliases:
        if isinstance(alias, str) and _normalize_model_name(alias) == normalized_model:
            return True
    return False


def _normalize_model_name(model: str) -> str:
    """规范化模型名，以便匹配不同 provider 前缀和大小写写法。"""
    return model.strip().lower()


def _is_litellm_unmapped_model_error(exc: Exception) -> bool:
    """判断异常是否表示 LiteLLM 尚未收录该模型。"""
    return "this model isn't mapped yet." in str(exc).lower()


def _as_positive_int(value: object) -> int | None:
    """将未知数值转换为正整数。"""
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
