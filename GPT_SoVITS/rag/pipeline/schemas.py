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


class CharacterRelationCandidate(BaseModel):
    """表示第二阶段抽取的一条角色关系候选。"""

    scene_id: str
    relation_local_id: str
    subject_character_name: str
    object_character_name: str
    relation_label: str
    state_summary: str
    speech_hint: str
    object_character_nickname: str
    tags: list[str] = Field(default_factory=list)
    retrieval_text: str
    evidence_u_ids: list[str] = Field(default_factory=list)
    confidence: float

    @model_validator(mode="after")
    def validate_distinct_characters(self) -> "CharacterRelationCandidate":
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
    character_relations: list[CharacterRelationCandidate] = Field(default_factory=list)
    lore_entries: list[LoreEntryCandidate] = Field(default_factory=list)


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
    event_facts: list[NormalizedEventFact] = Field(default_factory=list)
    linked_updates: list[LinkedCharacterThoughtUpdate] = Field(default_factory=list)
    character_thoughts: list[CharacterThoughtReviewRecord] = Field(default_factory=list)
    issues: list[NormalizationIssue] = Field(default_factory=list)
