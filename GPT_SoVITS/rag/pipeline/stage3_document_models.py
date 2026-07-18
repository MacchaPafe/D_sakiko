"""定义 Story Event 与 Lore Entry 的 Stage 3 审核产物。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .review_models import IdentitySuggestion, ReviewFields, SourceFingerprint
from .schemas import LoreEntryPayload, NormalizationIssue, Stage3ImportMetadata, StoryEventPayload


class StoryEventReviewRecord(ReviewFields):
    """表示一条具有稳定候选身份的 Story Event 审核记录。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^story_candidate:[0-9a-f-]{36}$")
    legacy_source_id: str | None = None
    source_scene_id: str
    source_local_id: str
    evidence_u_ids: list[str] = Field(default_factory=list)
    evidence_s_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    generated_document: StoryEventPayload
    reviewed_document: StoryEventPayload | None = None
    previous_reviewed_document: StoryEventPayload | None = None
    identity_suggestions: list[IdentitySuggestion] = Field(default_factory=list)

    def effective_document(self) -> StoryEventPayload:
        """返回发布和展示应使用的最终 Story Event 文档。"""

        return self.reviewed_document or self.generated_document


class LoreEntryReviewRecord(ReviewFields):
    """表示一条具有稳定候选身份的 Lore Entry 审核记录。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^lore_candidate:[0-9a-f-]{36}$")
    legacy_source_id: str | None = None
    source_scene_id: str
    source_local_id: str
    evidence_u_ids: list[str] = Field(default_factory=list)
    evidence_s_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    generated_document: LoreEntryPayload
    reviewed_document: LoreEntryPayload | None = None
    previous_reviewed_document: LoreEntryPayload | None = None
    identity_suggestions: list[IdentitySuggestion] = Field(default_factory=list)

    def effective_document(self) -> LoreEntryPayload:
        """返回发布和展示应使用的最终 Lore Entry 文档。"""

        return self.reviewed_document or self.generated_document


class Stage3DocumentReviewArtifact(BaseModel):
    """表示单集 Story Event 与 Lore Entry 的完整审核产物。"""

    model_config = ConfigDict(extra="forbid")

    format_version: int = 0
    artifact_type: Literal["stage3_document_review"] = "stage3_document_review"
    metadata: Stage3ImportMetadata
    direct_sources: list[SourceFingerprint] = Field(default_factory=list)
    story_events: list[StoryEventReviewRecord] = Field(default_factory=list)
    lore_entries: list[LoreEntryReviewRecord] = Field(default_factory=list)
    issues: list[NormalizationIssue] = Field(default_factory=list)
