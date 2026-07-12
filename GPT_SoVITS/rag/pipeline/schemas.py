"""RAG 标注流水线的数据结构。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.models import CanonBranch, CharacterId, ScopeType, SeasonId, SeriesId


class RawSubtitleLine(BaseModel):
    """表示从字幕文件中读取的一条原始对话或屏幕字记录。"""

    model_config = ConfigDict(frozen=True)

    source_path: str
    line_no: int
    layer: int
    start_ms: int
    end_ms: int
    style: str
    raw_text: str
    clean_text: str


class UtteranceUnit(BaseModel):
    """表示对齐后的双语台词单元。"""

    model_config = ConfigDict(frozen=True)

    u_id: str
    episode: int
    start_ms: int
    end_ms: int
    jp_text: str = ""
    zh_text: str = ""
    source_line_nos: list[int] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ScreenTextUnit(BaseModel):
    """表示屏幕字或注释文本。"""

    model_config = ConfigDict(frozen=True)

    s_id: str
    episode: int
    start_ms: int
    end_ms: int
    kind: str
    text: str
    source_line_nos: list[int] = Field(default_factory=list)


class CandidateCharacter(BaseModel):
    """表示交给第一阶段 LLM 的候选角色。"""

    model_config = ConfigDict(frozen=True)

    display_name: str
    character_id: str
    aliases: list[str] = Field(default_factory=list)
    notes: str | None = None
    score: int = 0


class SceneChunk(BaseModel):
    """表示一个可送入第一阶段 LLM 的场景块。"""

    model_config = ConfigDict(frozen=True)

    anime_title: str
    series_id: str
    season_id: int
    scene_id: str
    episode: int
    start_ms: int
    end_ms: int
    utterances: list[UtteranceUnit] = Field(default_factory=list)
    screen_texts: list[ScreenTextUnit] = Field(default_factory=list)
    candidate_characters: list[CandidateCharacter] = Field(default_factory=list)
    scene_summary_hint: str | None = None


class SpeakerAnnotation(BaseModel):
    """表示单句 speaker 标注结果。"""

    u_id: str
    speaker_name: str | None
    speaker_confidence: float
    is_inner_monologue: bool
    addressee_candidates: list[str] = Field(default_factory=list)
    mentioned_characters: list[str] = Field(default_factory=list)
    emotion_hint: str | None = None
    reason_brief: str


class SceneAnnotationPass1(BaseModel):
    """表示第一阶段场景级标注结果。"""

    scene_id: str
    episode: int
    present_characters: list[str] = Field(default_factory=list)
    utterance_annotations: list[SpeakerAnnotation] = Field(default_factory=list)
    global_notes: list[str] = Field(default_factory=list)


class Stage1Metadata(BaseModel):
    """表示第一阶段准备或标注任务的元信息。"""

    subtitle_path: str
    anime_title: str
    series_id: str
    season_id: int
    canon_branch: str
    episode: int
    scene_gap_ms: int


class Stage1PreparedArtifact(BaseModel):
    """表示第一阶段预处理产物。"""

    metadata: Stage1Metadata
    scenes: list[SceneChunk] = Field(default_factory=list)


class Stage1SceneAnnotationResult(BaseModel):
    """表示单个场景的第一阶段标注结果。"""

    scene_id: str
    prompt_path: str | None = None
    raw_response_text: str | None = None
    annotation: SceneAnnotationPass1 | None = None
    error: str | None = None


class Stage1AnnotationArtifact(BaseModel):
    """表示第一阶段批量标注结果。"""

    metadata: Stage1Metadata
    model: str
    template_path: str
    results: list[Stage1SceneAnnotationResult] = Field(default_factory=list)


class Stage2ScreenText(BaseModel):
    """表示第二阶段输入中的屏幕字或注释。"""

    s_id: str
    start_ms: int
    end_ms: int
    start_text: str
    end_text: str
    kind: str
    text: str


class Stage2Utterance(BaseModel):
    """表示第二阶段输入中的整合台词单元。"""

    u_id: str
    start_ms: int
    end_ms: int
    start_text: str
    end_text: str
    speaker_name: str | None = None
    speaker_confidence: float = 0.0
    is_inner_monologue: bool = False
    addressee_candidates: list[str] = Field(default_factory=list)
    mentioned_characters: list[str] = Field(default_factory=list)
    emotion_hint: str | None = None
    zh_text: str = ""
    jp_text: str = ""


class Stage2SceneInput(BaseModel):
    """表示一个可直接送入第二阶段 prompt 的场景。"""

    anime_title: str
    series_id: str
    season_id: int
    episode: int
    scene_id: str
    start_ms: int
    end_ms: int
    scene_start_text: str
    scene_end_text: str
    scene_summary_hint: str | None = None
    present_characters: list[str] = Field(default_factory=list)
    screen_texts: list[Stage2ScreenText] = Field(default_factory=list)
    utterances: list[Stage2Utterance] = Field(default_factory=list)
    global_notes: list[str] = Field(default_factory=list)


class Stage2SkippedScene(BaseModel):
    """表示未能转换为第二阶段输入的场景。"""

    scene_id: str
    error: str


class Stage2InputMetadata(BaseModel):
    """表示第二阶段输入产物的元信息。"""

    subtitle_path: str
    anime_title: str
    series_id: str
    season_id: int
    canon_branch: str
    episode: int
    scene_gap_ms: int
    source_stage1_model: str
    source_stage1_template_path: str
    source_stage1_output_path: str | None = None


class Stage2InputArtifact(BaseModel):
    """表示第二阶段使用的整合输入产物。"""

    metadata: Stage2InputMetadata
    scenes: list[Stage2SceneInput] = Field(default_factory=list)
    skipped_scenes: list[Stage2SkippedScene] = Field(default_factory=list)


class StoryEventCandidate(BaseModel):
    """表示第二阶段抽取的一条剧情事件候选。"""

    scene_id: str
    event_local_id: str
    title: str
    summary: str
    participants: list[str] = Field(default_factory=list)
    importance: int
    tags: list[str] = Field(default_factory=list)
    retrieval_text: str
    evidence_u_ids: list[str] = Field(default_factory=list)
    evidence_s_ids: list[str] = Field(default_factory=list)
    confidence: float


RelationEvidenceStrength = Literal["explicit", "inferred"]


class RelationObservationCandidate(BaseModel):
    """表示第二阶段抽取的一条场景级角色关系观察。"""

    scene_id: str
    observation_local_id: str
    subject_character_name: str
    object_character_name: str
    observation_text: str
    speech_hint: str = ""
    object_character_nickname: str = ""
    evidence_u_ids: list[str] = Field(default_factory=list)
    evidence_strength: RelationEvidenceStrength
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguity_notes: str = ""

    @model_validator(mode="after")
    def validate_distinct_characters(self) -> "RelationObservationCandidate":
        """确保关系观察的主体与客体不是同一角色。"""

        if self.subject_character_name == self.object_character_name:
            raise ValueError("subject_character_name 与 object_character_name 不能相同")
        return self


class LoreEntryCandidate(BaseModel):
    """表示第二阶段抽取的一条设定条目候选。"""

    scene_id: str
    lore_local_id: str
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    retrieval_text: str
    evidence_u_ids: list[str] = Field(default_factory=list)
    evidence_s_ids: list[str] = Field(default_factory=list)
    confidence: float


class SceneAnnotationPass2(BaseModel):
    """表示第二阶段场景级抽取结果。"""

    scene_id: str
    story_events: list[StoryEventCandidate] = Field(default_factory=list)
    relation_observations: list[RelationObservationCandidate] = Field(default_factory=list)
    lore_entries: list[LoreEntryCandidate] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_character_relations(cls, data: object) -> object:
        """把旧版场景关系候选迁移为新的关系观察结构。"""

        if not isinstance(data, dict) or "relation_observations" in data:
            return data
        legacy_relations = data.get("character_relations")
        if not isinstance(legacy_relations, list):
            return data

        migrated: list[dict[str, object]] = []
        for raw_relation in legacy_relations:
            if not isinstance(raw_relation, dict):
                continue
            confidence_value = raw_relation.get("confidence", 0.0)
            confidence = float(confidence_value) if isinstance(confidence_value, (int, float)) else 0.0
            migrated.append(
                {
                    "scene_id": str(raw_relation.get("scene_id", data.get("scene_id", ""))),
                    "observation_local_id": str(raw_relation.get("relation_local_id", "")),
                    "subject_character_name": str(raw_relation.get("subject_character_name", "")),
                    "object_character_name": str(raw_relation.get("object_character_name", "")),
                    "observation_text": str(raw_relation.get("state_summary", "")),
                    "speech_hint": str(raw_relation.get("speech_hint", "")),
                    "object_character_nickname": str(raw_relation.get("object_character_nickname", "")),
                    "evidence_u_ids": raw_relation.get("evidence_u_ids", []),
                    "evidence_strength": "inferred",
                    "confidence": confidence,
                    "ambiguity_notes": "由旧版 character_relations 迁移，原始证据强度未标注。",
                }
            )

        copied = dict(data)
        copied.pop("character_relations", None)
        copied["relation_observations"] = migrated
        return copied


class Stage2SceneAnnotationResult(BaseModel):
    """表示单个场景的第二阶段抽取结果。"""

    scene_id: str
    prompt_path: str | None = None
    raw_response_text: str | None = None
    annotation: SceneAnnotationPass2 | None = None
    error: str | None = None


class Stage2AnnotationArtifact(BaseModel):
    """表示第二阶段批量抽取结果。"""

    metadata: Stage2InputMetadata
    model: str
    template_path: str
    results: list[Stage2SceneAnnotationResult] = Field(default_factory=list)


class RelationStateProposal(BaseModel):
    """表示 Stage 3 LLM 为一个有向角色对提出的长期关系状态。"""

    relation_type_key: str
    summary: str
    speech_hint: str = ""
    object_character_nickname: str = ""
    supporting_observation_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguity_notes: str = ""


class UnmergedRelationObservation(BaseModel):
    """表示没有被提升为长期关系状态的场景观察及其原因。"""

    observation_id: str
    reason: str


class RelationAggregationResponse(BaseModel):
    """表示一次有向角色对关系聚合的 LLM 输出。"""

    subject_character_name: str
    object_character_name: str
    states: list[RelationStateProposal] = Field(default_factory=list)
    unmerged_observations: list[UnmergedRelationObservation] = Field(default_factory=list)


RelationReviewStatus = Literal["unreviewed", "approved", "rejected"]
RelationRiskLevel = Literal["low", "high"]


class CharacterRelationStatePayload(BaseModel):
    """表示供人工审核的精简角色关系状态。"""

    subject_character_id: CharacterId
    object_character_id: CharacterId
    season_id: SeasonId
    series_id: SeriesId
    visible_from: int
    visible_to: int
    canon_branch: CanonBranch
    relation_type_key: str
    summary: str
    speech_hint: str = ""
    object_character_nickname: str = ""


class CharacterRelationStateReviewRecord(BaseModel):
    """表示一条可追溯、可人工复核的长期角色关系状态。"""

    state_id: str
    supporting_observation_ids: list[str] = Field(default_factory=list)
    review_status: RelationReviewStatus = "unreviewed"
    risk_level: RelationRiskLevel = "low"
    risk_reasons: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguity_notes: str = ""
    document: CharacterRelationStatePayload | None = None


class RelationObservationReviewRecord(BaseModel):
    """表示带剧情顺序与规范角色 ID 的场景关系观察。"""

    observation_id: str
    scene_id: str
    time_order: int
    subject_character_id: CharacterId
    object_character_id: CharacterId
    observation_text: str
    speech_hint: str = ""
    object_character_nickname: str = ""
    evidence_u_ids: list[str] = Field(default_factory=list)
    evidence_strength: RelationEvidenceStrength
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguity_notes: str = ""


class UnmergedRelationObservationReviewRecord(BaseModel):
    """表示 Stage 3 审核产物中没有合并为长期状态的观察。"""

    observation_id: str
    reason: str
    risk_level: RelationRiskLevel = "low"
    risk_reasons: list[str] = Field(default_factory=list)


class Stage3RelationAggregationMetadata(BaseModel):
    """表示跨一个或多个剧集进行关系聚合的公共范围。"""

    anime_title: str
    series_id: str
    season_id: int
    canon_branch: str
    episodes: list[int] = Field(default_factory=list)
    subtitle_paths: list[str] = Field(default_factory=list)


ThoughtSubjectKind = Literal["event", "event_fact", "standalone_topic", "uncertain"]
EpistemicStatus = Literal["knows", "believes", "suspects", "uncertain", "rejects"]
ProvisionalThoughtUpdateType = Literal[
    "acquired",
    "reaffirmed",
    "revised",
    "retracted",
    "disclosed_existing",
    "unspecified",
]
ThoughtEvidenceStrength = Literal["explicit", "inferred"]
ThoughtEffectiveFromHint = Literal[
    "current_scene",
    "earlier_than_current_scene",
    "explicit_prior_scene",
    "unknown",
]


class EventFactCandidate(BaseModel):
    """表示为角色观点链接而按需抽取的客观原子事实。"""

    scene_id: str
    fact_local_id: str
    event_local_id: str | None = None
    fact_text: str
    tags: list[str] = Field(default_factory=list)
    evidence_u_ids: list[str] = Field(default_factory=list)
    evidence_s_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class CharacterThoughtUpdateCandidate(BaseModel):
    """表示当前场景中有证据支持的一次角色观点更新。"""

    scene_id: str
    update_local_id: str
    character_name: str
    thought_text: str
    subject_kind: ThoughtSubjectKind
    subject_text: str
    epistemic_status: EpistemicStatus
    provisional_update_type: ProvisionalThoughtUpdateType
    evidence_strength: ThoughtEvidenceStrength
    evidence_u_ids: list[str] = Field(default_factory=list)
    about_event_local_id: str | None = None
    about_fact_local_id: str | None = None
    effective_from_hint: ThoughtEffectiveFromHint = "current_scene"
    inference_note: str | None = None
    ambiguity_notes: list[str] = Field(default_factory=list)
    extraction_confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_inference_note(self) -> "CharacterThoughtUpdateCandidate":
        """确保推断型证据说明其推断依据。"""

        if self.evidence_strength == "inferred" and not (self.inference_note or "").strip():
            raise ValueError("evidence_strength 为 inferred 时必须提供 inference_note")
        return self


class SceneThoughtExtractionPass2B(BaseModel):
    """表示单场景的 Event Fact 与角色观点更新抽取结果。"""

    scene_id: str
    event_facts: list[EventFactCandidate] = Field(default_factory=list)
    character_thought_updates: list[CharacterThoughtUpdateCandidate] = Field(default_factory=list)


class Stage2BSceneAnnotationResult(BaseModel):
    """表示单场景 Stage 2B 调用及校验结果。"""

    scene_id: str
    prompt_path: str | None = None
    prompt_paths: list[str] = Field(default_factory=list)
    raw_response_text: str | None = None
    annotation: SceneThoughtExtractionPass2B | None = None
    error: str | None = None


class Stage2BAnnotationArtifact(BaseModel):
    """表示 Stage 2B 批量角色观点抽取产物。"""

    metadata: Stage2InputMetadata
    source_stage2a_model: str
    source_stage2a_output_path: str | None = None
    model: str
    template_path: str
    results: list[Stage2BSceneAnnotationResult] = Field(default_factory=list)


ThoughtLinkStatus = Literal["linked", "standalone", "unresolved"]
ResolvedThoughtUpdateType = Literal["acquired", "reaffirmed", "revised", "retracted"]
ThoughtReviewStatus = Literal["unreviewed", "approved", "edited", "rejected", "needs_followup"]
ThoughtRiskLevel = Literal["low", "medium", "high"]
ThoughtReferenceTargetKind = Literal["event", "event_fact", "standalone_topic", "unresolved"]


class ThoughtReferenceLinkDecision(BaseModel):
    """表示 Stage 3 LLM 在受限候选集合内作出的观点链接决策。"""

    source_local_id: str
    link_status: ThoughtLinkStatus
    target_kind: ThoughtReferenceTargetKind
    target_id: str | None = None
    thought_aspect: str
    link_confidence: float = Field(ge=0.0, le=1.0)
    reason_brief: str


class NormalizedEventFact(BaseModel):
    """表示 Stage 3 中可供角色观点引用的规范化 Event Fact。"""

    fact_id: str
    source_scene_id: str
    source_local_id: str
    about_event_id: str | None = None
    fact_text: str
    tags: list[str] = Field(default_factory=list)
    evidence_u_ids: list[str] = Field(default_factory=list)
    evidence_s_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class LinkedCharacterThoughtUpdate(BaseModel):
    """表示 Stage 3 已尝试完成语义对象链接的角色观点更新。"""

    source_scene_id: str
    source_local_id: str
    character_id: CharacterId
    thought_text: str
    subject_kind: ThoughtSubjectKind
    subject_text: str
    epistemic_status: EpistemicStatus
    provisional_update_type: ProvisionalThoughtUpdateType
    resolved_update_type: ResolvedThoughtUpdateType
    evidence_strength: ThoughtEvidenceStrength
    evidence_u_ids: list[str] = Field(default_factory=list)
    inference_note: str | None = None
    ambiguity_notes: list[str] = Field(default_factory=list)
    effective_from_hint: ThoughtEffectiveFromHint
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    link_status: ThoughtLinkStatus
    link_confidence: float = Field(ge=0.0, le=1.0)
    about_event_id: str | None = None
    about_fact_id: str | None = None
    standalone_topic_key: str | None = None
    thought_aspect: str
    thought_thread_key: str
    evidence_time: int


class CharacterThoughtPayload(BaseModel):
    """表示可直接实例化为 CharacterThoughtDocument 的 payload。"""

    character_id: CharacterId
    series_id: SeriesId
    season_id: SeasonId
    canon_branch: CanonBranch
    thought_thread_key: str
    subject_kind: ThoughtSubjectKind
    about_event_id: str | None = None
    about_fact_id: str | None = None
    standalone_topic_key: str | None = None
    thought_text: str
    epistemic_status: EpistemicStatus
    valid_from: int
    valid_to: int
    tags: list[str] = Field(default_factory=list)
    retrieval_text: str
    source_scene_ids: list[str] = Field(default_factory=list)
    evidence_u_ids: list[str] = Field(default_factory=list)
    evidence_strength: ThoughtEvidenceStrength
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    link_confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_subject_reference(self) -> "CharacterThoughtPayload":
        """确保最终观点的 subject_kind 与引用字段一致且可入库。"""

        if self.subject_kind == "event" and self.about_event_id is None:
            raise ValueError("event 类型必须包含 about_event_id")
        if self.subject_kind == "event_fact" and self.about_fact_id is None:
            raise ValueError("event_fact 类型必须包含 about_fact_id")
        if self.subject_kind == "standalone_topic" and self.standalone_topic_key is None:
            raise ValueError("standalone_topic 类型必须包含 standalone_topic_key")
        if self.subject_kind == "uncertain":
            raise ValueError("uncertain 类型不得生成最终 CharacterThought payload")
        if self.valid_from > self.valid_to:
            raise ValueError("valid_from 不能大于 valid_to")
        return self


class CharacterThoughtReviewRecord(BaseModel):
    """表示一条可供人工复核并在通过后导入的角色观点。"""

    point_id: str
    source_update_ids: list[str] = Field(default_factory=list)
    link_status: ThoughtLinkStatus
    review_status: ThoughtReviewStatus = "unreviewed"
    risk_level: ThoughtRiskLevel
    risk_score: int = Field(ge=0, le=100)
    risk_reasons: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    provisional_update_types: list[ProvisionalThoughtUpdateType] = Field(default_factory=list)
    resolved_update_type: ResolvedThoughtUpdateType
    document: CharacterThoughtPayload | None = None


class StoryEventPayload(BaseModel):
    """表示可直接实例化为 StoryEventDocument 的 payload。"""

    season_id: SeasonId
    series_id: SeriesId
    episode: int
    time_order: int
    visible_from: int
    visible_to: int
    canon_branch: CanonBranch
    title: str
    summary: str
    participants: list[CharacterId] = Field(default_factory=list)
    importance: int
    tags: list[str] = Field(default_factory=list)
    retrieval_text: str


class CharacterRelationPayload(BaseModel):
    """表示可直接实例化为 CharacterRelationDocument 的 payload。"""

    subject_character_id: CharacterId
    object_character_id: CharacterId
    season_id: SeasonId
    series_id: SeriesId
    visible_from: int
    visible_to: int
    canon_branch: CanonBranch
    relation_label: str
    state_summary: str
    speech_hint: str
    object_character_nickname: str
    tags: list[str] = Field(default_factory=list)
    retrieval_text: str


class LoreEntryPayload(BaseModel):
    """表示可直接实例化为 LoreEntryDocument 的 payload。"""

    scope_type: ScopeType
    series_ids: list[SeriesId] | None = None
    season_ids: list[SeasonId] | None = None
    visible_from: int | None = None
    visible_to: int | None = None
    canon_branch: CanonBranch
    title: str
    content: str
    retrieval_text: str
    tags: list[str] = Field(default_factory=list)


class StoryEventImportRecord(BaseModel):
    """表示一个待入库的剧情事件记录。"""

    point_id: str
    source_scene_id: str
    source_local_id: str
    evidence_u_ids: list[str] = Field(default_factory=list)
    evidence_s_ids: list[str] = Field(default_factory=list)
    confidence: float
    document: StoryEventPayload


class CharacterRelationImportRecord(BaseModel):
    """表示一个待入库的角色关系记录。"""

    point_id: str
    source_scene_id: str
    source_local_id: str
    evidence_u_ids: list[str] = Field(default_factory=list)
    supporting_observation_ids: list[str] = Field(default_factory=list)
    relation_type_key: str = ""
    risk_level: RelationRiskLevel = "low"
    risk_reasons: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    confidence: float
    document: CharacterRelationPayload


class LoreEntryImportRecord(BaseModel):
    """表示一个待入库的设定条目记录。"""

    point_id: str
    source_scene_id: str
    source_local_id: str
    evidence_u_ids: list[str] = Field(default_factory=list)
    evidence_s_ids: list[str] = Field(default_factory=list)
    confidence: float
    document: LoreEntryPayload


class NormalizationIssue(BaseModel):
    """表示从第二阶段原始结果规范化时发现的问题。"""

    scene_id: str
    collection_name: str
    candidate_local_id: str
    message: str


class Stage3RelationAggregationArtifact(BaseModel):
    """表示全量角色关系聚合与风险分析后的审查产物。"""

    metadata: Stage3RelationAggregationMetadata
    source_stage2_models: list[str] = Field(default_factory=list)
    aggregation_model: str
    observations: list[RelationObservationReviewRecord] = Field(default_factory=list)
    character_relation_states: list[CharacterRelationStateReviewRecord] = Field(default_factory=list)
    unmerged_observations: list[UnmergedRelationObservationReviewRecord] = Field(default_factory=list)
    issues: list[NormalizationIssue] = Field(default_factory=list)


class Stage3ImportMetadata(BaseModel):
    """表示待入库 artifact 的元信息。"""

    subtitle_path: str
    anime_title: str
    series_id: SeriesId
    season_id: SeasonId
    canon_branch: CanonBranch
    episode: int
    source_stage2_model: str
    source_stage2_template_path: str


class Stage3NormalizedImportArtifact(BaseModel):
    """表示人工审查后可直接导入数据库的 artifact。"""

    metadata: Stage3ImportMetadata
    story_events: list[StoryEventImportRecord] = Field(default_factory=list)
    character_relations: list[CharacterRelationImportRecord] = Field(default_factory=list)
    lore_entries: list[LoreEntryImportRecord] = Field(default_factory=list)
    issues: list[NormalizationIssue] = Field(default_factory=list)


class Stage3ThoughtImportArtifact(BaseModel):
    """表示 Character Thought 跨场景聚合与风险分析后的审查产物。"""

    metadata: Stage3ImportMetadata
    source_stage2b_model: str
    linker_model: str | None = None
    event_facts: list[NormalizedEventFact] = Field(default_factory=list)
    linked_updates: list[LinkedCharacterThoughtUpdate] = Field(default_factory=list)
    character_thoughts: list[CharacterThoughtReviewRecord] = Field(default_factory=list)
    issues: list[NormalizationIssue] = Field(default_factory=list)
