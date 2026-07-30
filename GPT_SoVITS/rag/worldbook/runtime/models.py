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


class LinkedStoryEvent(BaseModel):
    """表示 Thought 显式关联且对当前进度可见的剧情事件。"""

    model_config = ConfigDict(extra="forbid")

    title: str
    summary: str
    participant_names: list[str]


class ThoughtMemory(BaseModel):
    """表示记忆工具返回的角色观点及其显式事件。"""

    model_config = ConfigDict(extra="forbid")

    character_name: str
    thought_text: str
    epistemic_status: str
    events: list[LinkedStoryEvent] = Field(default_factory=list)


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
