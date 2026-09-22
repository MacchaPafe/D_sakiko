"""定义世界书聊天运行时的上下文、候选和模型安全结果。"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rag.models import CanonBranch, CharacterId, SeriesId
from rag.worldbook.models import EntryType


ResultItemT = TypeVar("ResultItemT")


class WorldbookRootOption(BaseModel):
    """描述世界书选择菜单中的一个季度根包。"""

    model_config = ConfigDict(extra="forbid")

    package_id: str
    display_name: str
    package_version: str
    enabled: bool
    unavailable_reasons: list[str] = Field(default_factory=list)
    available_characters: list[CharacterId] = Field(default_factory=list)


class WorldbookResolvedContext(BaseModel):
    """保存一次聊天检索所需的已解析不可变世界书上下文。"""

    model_config = ConfigDict(extra="forbid")

    root_package_id: str
    root_package_version: str
    package_ids: list[str]
    package_versions: dict[str, str]
    package_depths: dict[str, int]
    character_id: CharacterId
    series_id: SeriesId
    timeline_id: str
    canon_branch: CanonBranch
    current_time: int
    story_year: int | None = None
    episode: int = Field(ge=1, le=13)


class WorldbookTurnSnapshot(WorldbookResolvedContext):
    """表示冻结在真实用户消息上的世界书上下文快照。"""


class RetrievalCandidate(BaseModel):
    """保存仅供排序与诊断使用的内部检索候选。"""

    model_config = ConfigDict(extra="forbid")

    entry_id: UUID
    package_id: str
    entry_type: EntryType
    payload: dict[str, object]
    score: float
    boost: float = 0.0
    final_score: float


class RetrievalTrace(BaseModel):
    """保存精确选中条目 ID 与完整候选，仅供内部诊断和验收。"""

    model_config = ConfigDict(extra="forbid")

    selected_entry_ids: list[UUID] = Field(default_factory=list)
    candidates: list[RetrievalCandidate] = Field(default_factory=list)


class RetrievalFailure(BaseModel):
    """描述可静默降级的只读检索失败。"""

    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "temporarily_unavailable",
        "index_unavailable",
        "invalid_request",
        "retrieval_failed",
    ]
    message: str


class RetrievalBatch(BaseModel):
    """保存一次内部检索的候选或可降级错误。"""

    model_config = ConfigDict(extra="forbid")

    candidates: list[RetrievalCandidate] = Field(default_factory=list)
    failure: RetrievalFailure | None = None


class PayloadRecord(BaseModel):
    """保存按过滤条件或 UUID 读取的一条无向量记录。"""

    model_config = ConfigDict(extra="forbid")

    entry_id: UUID
    package_id: str
    entry_type: EntryType
    payload: dict[str, object]


class PayloadBatch(BaseModel):
    """保存无向量 payload 查询结果或可降级错误。"""

    model_config = ConfigDict(extra="forbid")

    records: list[PayloadRecord] = Field(default_factory=list)
    failure: RetrievalFailure | None = None


class DirectThought(BaseModel):
    """表示可以直接提供给模型的角色观点。"""

    model_config = ConfigDict(extra="forbid")

    character_name: str
    thought_text: str
    epistemic_status: str


class KnownStoryEvent(BaseModel):
    """表示当前角色获准知道完整正文的剧情事件。"""

    model_config = ConfigDict(extra="forbid")

    title: str
    summary: str
    participant_names: list[str]


class DirectWorldbookContext(BaseModel):
    """表示直接注入模型的事实事件与角色观点。"""

    model_config = ConfigDict(extra="forbid")

    events: list[KnownStoryEvent] = Field(default_factory=list)
    thoughts: list[DirectThought] = Field(default_factory=list)


class LoreKnowledge(BaseModel):
    """表示世界书 Lore 工具可返回的一条知识。"""

    model_config = ConfigDict(extra="forbid")

    title: str
    content: str


class RelationKnowledge(BaseModel):
    """表示角色关系工具可返回的一条主观关系状态。"""

    model_config = ConfigDict(extra="forbid")

    target_character_name: str
    state_summary: str
    speech_hint: str | None = None
    object_character_nickname: str | None = None


class RelationHistoryPage(BaseModel):
    """表示固定页长的倒序角色关系历史。"""

    model_config = ConfigDict(extra="forbid")

    items: list[RelationKnowledge]
    page: int = Field(ge=1)
    has_more: bool
    next_page: int | None = None


class ThoughtMemory(BaseModel):
    """表示记忆工具返回的一条角色观点。"""

    model_config = ConfigDict(extra="forbid")

    character_name: str
    thought_text: str
    epistemic_status: str


class CharacterMemoryKnowledge(BaseModel):
    """表示记忆工具返回的顶层事实事件与角色观点。"""

    model_config = ConfigDict(extra="forbid")

    events: list[KnownStoryEvent] = Field(default_factory=list)
    thoughts: list[ThoughtMemory] = Field(default_factory=list)


class SourceRetrievalFailure(BaseModel):
    """描述 Thought 或 Event 单一来源的可降级失败。"""

    model_config = ConfigDict(extra="forbid")

    source: Literal["thought", "event"]
    failure: RetrievalFailure


class WorldbookKnowledgeResult(BaseModel):
    """保存双来源知识结果、独立追踪与合并诊断。"""

    model_config = ConfigDict(extra="forbid")

    knowledge: DirectWorldbookContext | CharacterMemoryKnowledge
    thought_trace: RetrievalTrace = Field(default_factory=RetrievalTrace)
    event_trace: RetrievalTrace = Field(default_factory=RetrievalTrace)
    linked_event_ids: list[UUID] = Field(default_factory=list)
    unauthorized_linked_event_ids: list[UUID] = Field(default_factory=list)
    deduplicated_event_ids: list[UUID] = Field(default_factory=list)
    source_failures: list[SourceRetrievalFailure] = Field(default_factory=list)
    source_durations_sec: dict[Literal["thought", "event"], float] = Field(
        default_factory=dict
    )

    @property
    def failure(self) -> RetrievalFailure | None:
        """兼容读取第一个来源失败，同时保留完整失败列表。"""

        return self.source_failures[0].failure if self.source_failures else None

    @property
    def trace(self) -> RetrievalTrace:
        """兼容返回合并后的选中 ID 与候选列表。"""

        return RetrievalTrace(
            selected_entry_ids=[
                *self.event_trace.selected_entry_ids,
                *self.thought_trace.selected_entry_ids,
            ],
            candidates=[
                *self.event_trace.candidates,
                *self.thought_trace.candidates,
            ],
        )


class WorldbookQueryResult(BaseModel, Generic[ResultItemT]):
    """把模型安全结果与仅供内部使用的检索追踪分离保存。"""

    model_config = ConfigDict(extra="forbid")

    items: list[ResultItemT] = Field(default_factory=list)
    failure: RetrievalFailure | None = None
    trace: RetrievalTrace = Field(default_factory=RetrievalTrace)


class RelationHistoryQueryResult(BaseModel):
    """保存关系历史分页结果、错误和内部检索追踪。"""

    model_config = ConfigDict(extra="forbid")

    page: RelationHistoryPage
    failure: RetrievalFailure | None = None
    trace: RetrievalTrace = Field(default_factory=RetrievalTrace)


class RelationTargetsQueryResult(BaseModel):
    """保存本轮可安全公开的关系目标角色与检索失败。"""

    model_config = ConfigDict(extra="forbid")

    items: list[CharacterId] = Field(default_factory=list)
    failure: RetrievalFailure | None = None
