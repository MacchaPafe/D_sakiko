"""RAG 运行时使用的角色标识解析器。"""

from __future__ import annotations

import re

from .models import CharacterId


def _normalize_character_name(value: str) -> str:
    """将角色名称整理为适合精确查表的形式。"""

    return re.sub(r"\s+", "", value).casefold()


def _build_character_lookup() -> dict[str, CharacterId]:
    """根据枚举值、成员名和常用中文名构造角色查找表。"""

    lookup: dict[str, CharacterId] = {}
    for character_id in CharacterId:
        candidates = (
            character_id.value,
            character_id.name,
            character_id.common_name,
        )
        for candidate in candidates:
            lookup[_normalize_character_name(candidate)] = character_id
    return lookup


_CHARACTER_LOOKUP = _build_character_lookup()


def resolve_character_id(character_name: str) -> CharacterId | None:
    """将已知角色名称解析为知识库角色标识，未知名称返回空值。"""

    normalized_name = _normalize_character_name(character_name)
    if not normalized_name:
        return None
    return _CHARACTER_LOOKUP.get(normalized_name)
