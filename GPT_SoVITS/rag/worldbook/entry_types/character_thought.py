"""Character Thought 正式发布 Schema 与类型模块。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rag.models import CanonBranch, CharacterId, SeriesId

from ..models import EntryType, WorldbookEntry
from .common import display_fields


EpistemicStatus = Literal["knows", "believes", "suspects", "uncertain", "rejects"]


class CharacterThoughtContentV0(BaseModel):
    """表示发布包中的一个角色观点有效状态。"""

    model_config = ConfigDict(extra="forbid")

    character_id: CharacterId
    series_id: SeriesId
    timeline_id: str = Field(min_length=1)
    canon_branch: CanonBranch
    thought_thread_key: UUID
    canonical_subject: str = Field(min_length=1)
    thought_aspect: str = Field(min_length=1)
    thought_text: str = Field(min_length=1)
    epistemic_status: EpistemicStatus
    visible_from: int
    visible_to: int
    story_event_entry_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    retrieval_text: str = Field(min_length=1)

    @field_validator("timeline_id", "canonical_subject", "thought_aspect", "thought_text", "retrieval_text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        """去除发布文本字段两端的空白。"""

        return value.strip()

    @field_validator("story_event_entry_ids")
    @classmethod
    def normalize_event_ids(cls, value: list[UUID]) -> list[UUID]:
        """保持事件引用第一次出现的顺序。"""

        return list(dict.fromkeys(value))

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        """清理标签并保持第一次出现的顺序。"""

        normalized = [item.strip() for item in value if item.strip()]
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_time_window(self) -> "CharacterThoughtContentV0":
        """校验观点可见时间窗口。"""

        if self.visible_from > self.visible_to:
            raise ValueError("visible_from 不能晚于 visible_to")
        return self


@dataclass(frozen=True, slots=True)
class CharacterThoughtTypeModule:
    """集中解释 Character Thought 发布 Schema v0。"""

    entry_type: EntryType = "character_thought"
    projection_version: int = 0

    def _document(self, entry: WorldbookEntry) -> CharacterThoughtContentV0:
        """解析正式角色观点内容。"""

        return CharacterThoughtContentV0.model_validate(entry.content)

    def validate(self, entry: WorldbookEntry) -> None:
        """验证当前内容。"""

        self._document(entry)

    def display_fields(self, entry: WorldbookEntry) -> list[tuple[str, str]]:
        """生成只读字段。"""

        return display_fields(self.payload(entry))

    def embedding_text(self, entry: WorldbookEntry) -> str:
        """生成角色观点 embedding 文本。"""

        return self._document(entry).retrieval_text

    def payload(self, entry: WorldbookEntry) -> dict[str, object]:
        """生成角色观点索引投影。"""

        return self._document(entry).model_dump(mode="json")
