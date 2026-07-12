"""世界书条目 Schema adapter 与当前类型模块注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import EffectiveWorldbookEntry, EntryType, IndexProjection, WorldbookEntry
from .entry_types.character_relation import CharacterRelationTypeModule
from .entry_types.lore_entry import LoreEntryTypeModule
from .entry_types.story_event import StoryEventTypeModule


class EntrySchemaAdapter(Protocol):
    """把历史条目 Schema 迁移成当前统一条目。"""

    entry_type: EntryType
    schema_version: int

    def migrate(self, entry: WorldbookEntry) -> WorldbookEntry:
        """验证并迁移一条历史条目。"""


class EntryTypeModule(Protocol):
    """集中提供当前条目的展示、embedding 与索引投影。"""

    entry_type: EntryType
    projection_version: int

    def validate(self, entry: WorldbookEntry) -> None:
        """验证当前条目内容。"""

    def display_fields(self, entry: WorldbookEntry) -> list[tuple[str, str]]:
        """生成通用只读详情字段。"""

    def embedding_text(self, entry: WorldbookEntry) -> str:
        """生成用于 embedding 的文本。"""

    def payload(self, entry: WorldbookEntry) -> dict[str, object]:
        """生成扁平 Qdrant payload。"""


@dataclass(frozen=True, slots=True)
class _SchemaV0Adapter:
    """验证当前实验 Schema v0，不把它承诺为公开 v1。"""

    entry_type: EntryType
    schema_version: int = 0

    def migrate(self, entry: WorldbookEntry) -> WorldbookEntry:
        """确保注册键与条目自描述一致。"""

        if entry.entry_type != self.entry_type or entry.schema_version != self.schema_version:
            raise ValueError("adapter 与条目类型或版本不匹配")
        return entry


class AdapterRegistry:
    """维护历史 Schema adapter 与当前 Type Module 的唯一注册。"""

    def __init__(self) -> None:
        """创建空注册表。"""

        self._adapters: dict[tuple[EntryType, int], EntrySchemaAdapter] = {}
        self._modules: dict[EntryType, EntryTypeModule] = {}

    def register_adapter(self, adapter: EntrySchemaAdapter) -> None:
        """注册 adapter，并拒绝重复键。"""

        key = (adapter.entry_type, adapter.schema_version)
        if key in self._adapters:
            raise ValueError(f"adapter 已注册: {key}")
        self._adapters[key] = adapter

    def register_module(self, module: EntryTypeModule) -> None:
        """注册当前类型模块，并拒绝重复类型。"""

        if module.entry_type in self._modules:
            raise ValueError(f"Type Module 已注册: {module.entry_type}")
        self._modules[module.entry_type] = module

    def normalize(self, entry: WorldbookEntry) -> WorldbookEntry:
        """通过匹配 adapter 迁移并使用当前模块校验条目。"""

        adapter = self._adapters.get((entry.entry_type, entry.schema_version))
        if adapter is None:
            raise ValueError(f"不支持的条目 Schema: {(entry.entry_type, entry.schema_version)}")
        normalized = adapter.migrate(entry)
        self.module(entry.entry_type).validate(normalized)
        return normalized

    def module(self, entry_type: EntryType) -> EntryTypeModule:
        """返回指定类型的当前模块。"""

        try:
            return self._modules[entry_type]
        except KeyError as exc:
            raise ValueError(f"未注册 Type Module: {entry_type}") from exc

    def projection(self, effective: EffectiveWorldbookEntry) -> IndexProjection:
        """为有效条目生成带统一 envelope 的索引投影。"""

        entry = self.normalize(effective.entry)
        module = self.module(entry.entry_type)
        payload = module.payload(entry)
        payload.update(
            {
                "entry_id": str(entry.entry_id),
                "package_id": effective.package_id,
                "entry_type": entry.entry_type,
                "entry_schema_version": entry.schema_version,
                "entry_revision": effective.revision,
            }
        )
        return IndexProjection(
            entry_id=entry.entry_id,
            package_id=effective.package_id,
            entry_type=entry.entry_type,
            entry_revision=effective.revision,
            embedding_text=module.embedding_text(entry),
            payload=payload,
        )


def create_default_registry() -> AdapterRegistry:
    """创建包含三类 Schema v0 的默认注册表。"""

    registry = AdapterRegistry()
    modules: tuple[EntryTypeModule, ...] = (
        StoryEventTypeModule(),
        CharacterRelationTypeModule(),
        LoreEntryTypeModule(),
    )
    for module in modules:
        registry.register_adapter(_SchemaV0Adapter(module.entry_type))
        registry.register_module(module)
    return registry
