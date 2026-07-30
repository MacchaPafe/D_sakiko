"""集中定义世界书写入与只读查询共享的索引契约。"""

from __future__ import annotations

from dataclasses import dataclass

from .models import EntryType


INDEX_SCHEMA_VERSION = 3
"""当前世界书派生索引结构版本。"""

PROJECTION_VERSION = 1
"""当前世界书索引投影版本。"""

EMBEDDING_MODEL_ID = "multilingual-e5-small"
"""当前世界书索引使用的 embedding 模型标识。"""

@dataclass(frozen=True, slots=True)
class PayloadIndexSpec:
    """描述一个 Qdrant payload 索引字段。"""

    field_name: str
    schema_name: str


COLLECTION_NAMES: dict[EntryType, str] = {
    "story_event": "story_events",
    "character_relation": "character_relations",
    "lore_entry": "lore_entries",
    "character_thought": "character_thoughts",
}
"""保存四类世界书条目对应的稳定 collection 名。"""


PAYLOAD_INDEXES: dict[EntryType, tuple[PayloadIndexSpec, ...]] = {
    "story_event": (
        PayloadIndexSpec("package_id", "KEYWORD"),
        PayloadIndexSpec("timeline_id", "KEYWORD"),
        PayloadIndexSpec("canon_branch", "KEYWORD"),
        PayloadIndexSpec("series_id", "KEYWORD"),
        PayloadIndexSpec("visible_from", "INTEGER"),
        PayloadIndexSpec("visible_to", "INTEGER"),
        PayloadIndexSpec("participants", "KEYWORD"),
    ),
    "character_relation": (
        PayloadIndexSpec("package_id", "KEYWORD"),
        PayloadIndexSpec("timeline_id", "KEYWORD"),
        PayloadIndexSpec("canon_branch", "KEYWORD"),
        PayloadIndexSpec("visible_from", "INTEGER"),
        PayloadIndexSpec("visible_to", "INTEGER"),
        PayloadIndexSpec("subject_character_id", "KEYWORD"),
        PayloadIndexSpec("object_character_id", "KEYWORD"),
        PayloadIndexSpec("relation_type_key", "KEYWORD"),
    ),
    "lore_entry": (
        PayloadIndexSpec("package_id", "KEYWORD"),
        PayloadIndexSpec("timeline_id", "KEYWORD"),
        PayloadIndexSpec("canon_branch", "KEYWORD"),
        PayloadIndexSpec("scope_type", "KEYWORD"),
        PayloadIndexSpec("series_ids", "KEYWORD"),
        PayloadIndexSpec("applicable_story_years", "INTEGER"),
        PayloadIndexSpec("visible_from", "INTEGER"),
        PayloadIndexSpec("visible_to", "INTEGER"),
    ),
    "character_thought": (
        PayloadIndexSpec("package_id", "KEYWORD"),
        PayloadIndexSpec("timeline_id", "KEYWORD"),
        PayloadIndexSpec("canon_branch", "KEYWORD"),
        PayloadIndexSpec("visible_from", "INTEGER"),
        PayloadIndexSpec("visible_to", "INTEGER"),
        PayloadIndexSpec("character_id", "KEYWORD"),
        PayloadIndexSpec("thought_thread_key", "KEYWORD"),
    ),
}
"""保存各 collection 的运行时过滤索引。"""


def e5_passage_text(text: str) -> str:
    """把索引正文规范化为 multilingual-E5 passage 输入。"""

    normalized = text.strip()
    if not normalized:
        raise ValueError("E5 passage 文本不能为空")
    return f"passage: {normalized}"


def e5_query_text(text: str) -> str:
    """把检索问题规范化为 multilingual-E5 query 输入。"""

    normalized = text.strip()
    if not normalized:
        raise ValueError("E5 query 文本不能为空")
    return f"query: {normalized}"


def payload_schema_type(schema_name: str) -> str:
    """校验并返回共享契约支持的 payload schema 名称。"""

    supported = {"KEYWORD", "INTEGER"}
    if schema_name not in supported:
        raise ValueError(f"不支持的 payload schema: {schema_name}")
    return schema_name
