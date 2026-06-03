from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from log import get_logger


logger = get_logger(__name__)
_OVERRIDES_FILE = Path(__file__).with_name("model_context_overrides.json")


def count_message_tokens(model: str, messages: list[dict[str, object]]) -> int:
    """使用 LiteLLM 计算一组消息在指定模型下消耗的 token 数。"""
    from litellm import token_counter

    token_count = token_counter(model=model.strip(), messages=messages)
    return max(0, int(token_count))


@lru_cache(maxsize=256)
def get_model_input_token_limit(model: str) -> int | None:
    """获取模型输入上下文 token 上限，优先使用 LiteLLM，缺失时查本地补充表。"""
    normalized_model = model.strip()
    if not normalized_model:
        return None

    litellm_limit = _get_litellm_model_input_token_limit(normalized_model)
    if litellm_limit is not None:
        return litellm_limit
    return _get_local_override_input_token_limit(normalized_model)


def _get_litellm_model_input_token_limit(model: str) -> int | None:
    """从 LiteLLM 模型信息表中读取输入 token 上限。"""
    try:
        from litellm import get_model_info

        info = get_model_info(model=model)
    except Exception as exc:
        logger.warning("查询 LiteLLM 模型上下文上限失败：%s", exc)
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


def _as_positive_int(value: object) -> int | None:
    """将未知数值转换为正整数。"""
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
