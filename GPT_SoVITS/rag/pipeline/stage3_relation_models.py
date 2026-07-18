"""定义跨集 Character Relation 状态序列审核产物。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from rag.models import CanonBranch, CharacterId

from .review_models import IdentitySuggestion, ReviewFields, SourceFingerprint
from .schemas import (
    NormalizationIssue,
    RelationObservationReviewRecord,
    Stage3RelationAggregationMetadata,
)


class RelationStateDraft(BaseModel):
    """表示 Relation Type 中一个具有开发侧身份的状态。"""

    model_config = ConfigDict(extra="forbid")

    relation_state_id: str = Field(pattern=r"^relation_state:[0-9a-f-]{36}$")
    supporting_observation_ids: list[str] = Field(default_factory=list)
    state_summary: str = Field(min_length=1)
    speech_hint: str = ""
    object_character_nickname: str = ""
    visible_from: int
    visible_to: int
    tags: list[str] = Field(default_factory=list)
    retrieval_text: str = Field(min_length=1)


class RelationTypeReviewRecord(ReviewFields):
    """表示一个有向角色对下完整的 Relation Type 状态序列。"""

    model_config = ConfigDict(extra="forbid")

    relation_type_id: str = Field(pattern=r"^relation_type:[0-9a-f-]{36}$")
    subject_character_id: CharacterId
    object_character_id: CharacterId
    series_id: str
    timeline_id: str
    canon_branch: CanonBranch
    semantic_label: str = Field(min_length=1)
    covered_observation_ids: list[str] = Field(default_factory=list)
    generated_sequence: list[RelationStateDraft] = Field(default_factory=list)
    reviewed_sequence: list[RelationStateDraft] | None = None
    previous_reviewed_sequence: list[RelationStateDraft] | None = None
    identity_suggestions: list[IdentitySuggestion] = Field(default_factory=list)

    def effective_sequence(self) -> list[RelationStateDraft]:
        """返回发布和展示应使用的最终关系状态序列。"""

        return self.reviewed_sequence or self.generated_sequence


class UnmergedRelationObservationDecision(ReviewFields):
    """表示尚未进入任何 Relation Type 的 Observation 审核决定。"""

    model_config = ConfigDict(extra="forbid")

    observation_id: str
    generated_reason: str


class Stage3RelationReviewArtifact(BaseModel):
    """表示完整集数范围内唯一的 Character Relation Review。"""

    model_config = ConfigDict(extra="forbid")

    format_version: int = 0
    artifact_type: Literal["stage3_relation_review"] = "stage3_relation_review"
    metadata: Stage3RelationAggregationMetadata
    direct_sources: list[SourceFingerprint] = Field(default_factory=list)
    aggregation_model: str
    observations: list[RelationObservationReviewRecord] = Field(default_factory=list)
    relation_types: list[RelationTypeReviewRecord] = Field(default_factory=list)
    unmerged_observations: list[UnmergedRelationObservationDecision] = Field(default_factory=list)
    issues: list[NormalizationIssue] = Field(default_factory=list)
