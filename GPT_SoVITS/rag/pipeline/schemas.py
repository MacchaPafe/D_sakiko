"""RAG 标注流水线的数据结构。"""

from __future__ import annotations

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
