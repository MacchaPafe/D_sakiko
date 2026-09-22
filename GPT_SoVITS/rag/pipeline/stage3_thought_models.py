"""定义跨集 Character Thought 聚合响应与审核产物。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from rag.models import CanonBranch, CharacterId

from .review_models import IdentitySuggestion, ReviewFields, SourceFingerprint
from .schemas import (
    EpistemicStatus,
    NormalizationIssue,
    ProvisionalThoughtUpdateType,
    ThoughtEvidenceStrength,
    ThoughtSubjectKind,
)


ThoughtTransitionKind = Literal["acquired", "reaffirmed", "revised", "retracted"]
UnassignedThoughtKind = Literal["excluded", "unresolved"]


class ThoughtAggregationMetadata(BaseModel):
    """描述一次跨集 Thought 聚合的完整范围。"""

    model_config = ConfigDict(extra="forbid")

    anime_title: str
    series_id: str
    timeline_id: str
    story_year: int | None = None
    canon_branch: CanonBranch
    episodes: list[int] = Field(default_factory=list)


class ThoughtUpdateEvidence(BaseModel):
    """表示供跨集聚合使用的一条稳定 Character Thought Update。"""

    model_config = ConfigDict(extra="forbid")

    update_id: str
    source_scene_id: str
    source_local_id: str
    character_id: CharacterId
    thought_text: str
    subject_kind: ThoughtSubjectKind
    subject_text: str
    epistemic_status: EpistemicStatus
    provisional_update_type: ProvisionalThoughtUpdateType
    evidence_strength: ThoughtEvidenceStrength
    evidence_u_ids: list[str] = Field(default_factory=list)
    inference_note: str | None = None
    ambiguity_notes: list[str] = Field(default_factory=list)
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    event_candidate_id: str | None = None
    event_fact_id: str | None = None
    evidence_time: int


class ThoughtStateProposal(BaseModel):
    """表示 LLM 对 Thread 中一次状态变化的提议。"""

    model_config = ConfigDict(extra="forbid")

    transition: ThoughtTransitionKind
    supporting_update_ids: list[str] = Field(default_factory=list)
    thought_text: str = ""
    epistemic_status: EpistemicStatus
    story_event_candidate_ids: list[str] = Field(default_factory=list)
    event_fact_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    retrieval_text: str = ""


class ThoughtThreadProposal(BaseModel):
    """表示 LLM 为一个角色提出的完整 Thought Thread。"""

    model_config = ConfigDict(extra="forbid")

    canonical_subject: str = Field(min_length=1)
    thought_aspect: str = Field(min_length=1)
    states: list[ThoughtStateProposal] = Field(default_factory=list)


class ThoughtUnassignedProposal(BaseModel):
    """表示 LLM 未归入 Thread 的 Update 及其建议原因。"""

    model_config = ConfigDict(extra="forbid")

    update_id: str
    kind: UnassignedThoughtKind
    reason: str


class CharacterThoughtAggregationResponse(BaseModel):
    """表示一个角色完整跨集 Thought 聚合的 LLM 输出。"""

    model_config = ConfigDict(extra="forbid")

    character_id: CharacterId
    threads: list[ThoughtThreadProposal] = Field(default_factory=list)
    unassigned_updates: list[ThoughtUnassignedProposal] = Field(default_factory=list)


class ThoughtStateDraft(BaseModel):
    """表示 Thought Thread 中一个具有开发侧身份的有效状态。"""

    model_config = ConfigDict(extra="forbid")

    thought_state_id: str = Field(pattern=r"^thought_state:[0-9a-f-]{36}$")
    transition: Literal["acquired", "revised"]
    supporting_update_ids: list[str] = Field(default_factory=list)
    thought_text: str = Field(min_length=1)
    epistemic_status: EpistemicStatus
    visible_from: int
    visible_to: int
    story_event_candidate_ids: list[str] = Field(default_factory=list)
    event_fact_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    retrieval_text: str = Field(min_length=1)


class ThoughtTransitionDraft(BaseModel):
    """保存只用于审核和时间投影的 Thread Transition。"""

    model_config = ConfigDict(extra="forbid")

    transition: ThoughtTransitionKind
    supporting_update_ids: list[str] = Field(default_factory=list)
    evidence_time: int


class ThoughtThreadContentDraft(BaseModel):
    """表示 Thought Thread 可被人工完整替换的语义内容。"""

    model_config = ConfigDict(extra="forbid")

    canonical_subject: str = Field(min_length=1)
    thought_aspect: str = Field(min_length=1)
    states: list[ThoughtStateDraft] = Field(default_factory=list)


class ThoughtThreadReviewRecord(ReviewFields):
    """表示一个角色围绕同一主题和方面的完整 Thought Thread。"""

    model_config = ConfigDict(extra="forbid")

    thought_thread_id: str = Field(pattern=r"^thought_thread:[0-9a-f-]{36}$")
    character_id: CharacterId
    series_id: str
    timeline_id: str
    canon_branch: CanonBranch
    covered_update_ids: list[str] = Field(default_factory=list)
    transitions: list[ThoughtTransitionDraft] = Field(default_factory=list)
    generated_content: ThoughtThreadContentDraft
    reviewed_content: ThoughtThreadContentDraft | None = None
    previous_reviewed_content: ThoughtThreadContentDraft | None = None
    identity_suggestions: list[IdentitySuggestion] = Field(default_factory=list)

    @property
    def canonical_subject(self) -> str:
        """返回机器基准中的规范化主题。"""

        return self.generated_content.canonical_subject

    @property
    def thought_aspect(self) -> str:
        """返回机器基准中的认知方面。"""

        return self.generated_content.thought_aspect

    @property
    def generated_sequence(self) -> list[ThoughtStateDraft]:
        """返回机器基准状态序列。"""

        return self.generated_content.states

    @property
    def reviewed_sequence(self) -> list[ThoughtStateDraft] | None:
        """返回人工状态序列，供只读兼容调用。"""

        return None if self.reviewed_content is None else self.reviewed_content.states

    @property
    def previous_reviewed_sequence(self) -> list[ThoughtStateDraft] | None:
        """返回上一版人工状态序列，供只读对比。"""

        return (
            None
            if self.previous_reviewed_content is None
            else self.previous_reviewed_content.states
        )

    def effective_content(self) -> ThoughtThreadContentDraft:
        """返回发布和展示应使用的最终观点内容。"""

        return self.reviewed_content or self.generated_content

    def effective_sequence(self) -> list[ThoughtStateDraft]:
        """返回发布和展示应使用的最终 Thought 状态序列。"""

        return self.effective_content().states


class UnassignedThoughtUpdateDecision(ReviewFields):
    """表示尚未进入 Thread 的 Update 审核决定。"""

    model_config = ConfigDict(extra="forbid")

    update_id: str
    kind: UnassignedThoughtKind
    generated_reason: str


class Stage3ThoughtReviewArtifact(BaseModel):
    """表示完整集数范围内唯一的 Character Thought Review。"""

    model_config = ConfigDict(extra="forbid")

    format_version: int = 0
    artifact_type: Literal["stage3_thought_review"] = "stage3_thought_review"
    metadata: ThoughtAggregationMetadata
    direct_sources: list[SourceFingerprint] = Field(default_factory=list)
    aggregation_model: str
    updates: list[ThoughtUpdateEvidence] = Field(default_factory=list)
    event_facts: list[dict[str, object]] = Field(default_factory=list)
    threads: list[ThoughtThreadReviewRecord] = Field(default_factory=list)
    unassigned_updates: list[UnassignedThoughtUpdateDecision] = Field(default_factory=list)
    issues: list[NormalizationIssue] = Field(default_factory=list)
