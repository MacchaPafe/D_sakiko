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


class RelationTypeContentDraft(BaseModel):
    """表示 Relation Type 可被人工完整替换的语义内容。"""

    model_config = ConfigDict(extra="forbid")

    semantic_label: str = Field(min_length=1)
    states: list[RelationStateDraft] = Field(default_factory=list)


class RelationTypeReviewRecord(ReviewFields):
    """表示一个有向角色对下完整的 Relation Type 状态序列。"""

    model_config = ConfigDict(extra="forbid")

    relation_type_id: str = Field(pattern=r"^relation_type:[0-9a-f-]{36}$")
    subject_character_id: CharacterId
    object_character_id: CharacterId
    series_id: str
    timeline_id: str
    canon_branch: CanonBranch
    covered_observation_ids: list[str] = Field(default_factory=list)
    generated_content: RelationTypeContentDraft
    reviewed_content: RelationTypeContentDraft | None = None
    previous_reviewed_content: RelationTypeContentDraft | None = None
    identity_suggestions: list[IdentitySuggestion] = Field(default_factory=list)

    @property
    def semantic_label(self) -> str:
        """返回机器基准中的关系语义标签。"""

        return self.generated_content.semantic_label

    @property
    def generated_sequence(self) -> list[RelationStateDraft]:
        """返回机器基准状态序列。"""

        return self.generated_content.states

    @property
    def reviewed_sequence(self) -> list[RelationStateDraft] | None:
        """返回人工状态序列，供只读兼容调用。"""

        return None if self.reviewed_content is None else self.reviewed_content.states

    @property
    def previous_reviewed_sequence(self) -> list[RelationStateDraft] | None:
        """返回上一版人工状态序列，供只读对比。"""

        return (
            None
            if self.previous_reviewed_content is None
            else self.previous_reviewed_content.states
        )

    def effective_content(self) -> RelationTypeContentDraft:
        """返回发布和展示应使用的最终关系内容。"""

        return self.reviewed_content or self.generated_content

    def effective_sequence(self) -> list[RelationStateDraft]:
        """返回发布和展示应使用的最终关系状态序列。"""

        return self.effective_content().states


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
