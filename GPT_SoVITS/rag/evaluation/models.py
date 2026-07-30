"""定义版本化世界书检索验收案例与报告。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.models import CharacterId


EvaluationQueryType = Literal[
    "direct_thought",
    "lore",
    "relation",
    "memory",
]


class WorldbookEvaluationCase(BaseModel):
    """描述一条可由 fake 或真实 embedding 共用的检索验收案例。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    query_type: EvaluationQueryType
    root_package_id: str
    episode: int = Field(ge=1, le=13)
    character_id: CharacterId
    query: str = Field(min_length=1)
    current_user_text: str = ""
    target_character_id: CharacterId | None = None
    relation_episode: int | None = Field(default=None, ge=1, le=13)
    expected_entry_ids: list[UUID] = Field(default_factory=list)
    forbidden_entry_ids: list[UUID] = Field(default_factory=list)
    expect_empty: bool = False

    @model_validator(mode="after")
    def validate_expectations(self) -> "WorldbookEvaluationCase":
        """确保查询类型参数和至少一种验收断言完整。"""

        if self.query_type == "relation" and self.target_character_id is None:
            raise ValueError("relation 案例必须声明 target_character_id")
        if self.query_type != "relation" and self.relation_episode is not None:
            raise ValueError("只有 relation 案例可以声明 relation_episode")
        if not (
            self.expected_entry_ids
            or self.forbidden_entry_ids
            or self.expect_empty
        ):
            raise ValueError("案例必须声明正例、禁用条目或空结果断言")
        return self


class WorldbookEvaluationCaseFile(BaseModel):
    """保存一个可以随已审核分集增量扩展的案例文件。"""

    model_config = ConfigDict(extra="forbid")

    format_version: Literal[1] = 1
    dataset_id: str = Field(min_length=1)
    description: str = ""
    cases: list[WorldbookEvaluationCase] = Field(min_length=1)


class WorldbookEvaluationCaseResult(BaseModel):
    """记录单个案例的实际条目顺序和失败原因。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    passed: bool
    retrieved_entry_ids: list[UUID] = Field(default_factory=list)
    missing_expected_entry_ids: list[UUID] = Field(default_factory=list)
    leaked_forbidden_entry_ids: list[UUID] = Field(default_factory=list)
    failure: str | None = None


class WorldbookEvaluationReport(BaseModel):
    """汇总一次 fake 或真实 embedding 验收运行。"""

    model_config = ConfigDict(extra="forbid")

    format_version: Literal[1] = 1
    dataset_id: str
    backend_label: str
    total: int
    passed: int
    pass_rate: float
    results: list[WorldbookEvaluationCaseResult]
