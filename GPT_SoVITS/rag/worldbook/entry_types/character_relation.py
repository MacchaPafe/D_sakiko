"""Character Relation 正式发布 Schema 与类型模块。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rag.models import CanonBranch, CharacterId, SeriesId

from ..models import EntryType, WorldbookEntry
from .common import display_fields


class CharacterRelationContentV0(BaseModel):
    """表示发布包中的一段有向角色关系状态。"""

    model_config = ConfigDict(extra="forbid")

    subject_character_id: CharacterId
    object_character_id: CharacterId
    series_id: SeriesId
    timeline_id: str = Field(min_length=1)
    canon_branch: CanonBranch
    relation_type_key: UUID
    state_summary: str = Field(min_length=1)
    speech_hint: str = ""
    object_character_nickname: str = ""
    visible_from: int
    visible_to: int
    tags: list[str] = Field(default_factory=list)
    retrieval_text: str = Field(min_length=1)

    @field_validator("timeline_id", "state_summary", "speech_hint", "object_character_nickname", "retrieval_text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        """去除发布文本字段两端的空白。"""

        return value.strip()

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        """清理标签并保持第一次出现的顺序。"""

        normalized = [item.strip() for item in value if item.strip()]
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_relation(self) -> "CharacterRelationContentV0":
        """校验有向角色对与可见时间窗口。"""

        if self.subject_character_id == self.object_character_id:
            raise ValueError("subject_character_id 与 object_character_id 不能相同")
        if self.visible_from > self.visible_to:
            raise ValueError("visible_from 不能晚于 visible_to")
        return self


@dataclass(frozen=True, slots=True)
class CharacterRelationTypeModule:
    """集中解释 Character Relation 发布 Schema v0。"""

    entry_type: EntryType = "character_relation"
    projection_version: int = 0
    semantic_fields: frozenset[str] = frozenset(
        {"state_summary", "speech_hint", "object_character_nickname"}
    )

    def _document(self, entry: WorldbookEntry) -> CharacterRelationContentV0:
        """解析正式角色关系内容。"""

        return CharacterRelationContentV0.model_validate(entry.content)

    def validate(self, entry: WorldbookEntry) -> None:
        """验证当前内容。"""

        self._document(entry)

    def display_fields(self, entry: WorldbookEntry) -> list[tuple[str, str]]:
        """生成只读字段。"""

        return display_fields(self.payload(entry))

    def embedding_text(self, entry: WorldbookEntry) -> str:
        """生成角色关系 embedding 文本。"""

        return self._document(entry).retrieval_text

    def basic_retrieval_text(self, entry: WorldbookEntry) -> str:
        """按正式关系规则拼接摘要与说话方式。"""

        document = self._document(entry)
        suffix = f" 说话方式：{document.speech_hint}" if document.speech_hint else ""
        return f"{document.state_summary}{suffix}"

    def payload(self, entry: WorldbookEntry) -> dict[str, object]:
        """生成角色关系索引投影。"""

        return self._document(entry).model_dump(mode="json")
