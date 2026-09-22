"""定义跨集 Lore 去重决策产物。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .review_models import SourceFingerprint
from .schemas import LoreEntryPayload


LoreDedupAction = Literal["auto_merge_identical", "merge", "keep_separate", "drop"]
LoreDecisionStatus = Literal["pending", "completed", "automatic"]


class LoreDedupDecisionRecord(BaseModel):
    """表示一组 Lore 候选的最终去重决定。"""

    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(min_length=1)
    decision_basis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_ids: list[str] = Field(min_length=2)
    status: LoreDecisionStatus = "pending"
    action: LoreDedupAction | None = None
    primary_candidate_id: str | None = None
    reviewed_document: LoreEntryPayload | None = None
    review_notes: str | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> "LoreDedupDecisionRecord":
        """校验状态、动作、主候选与人工文档组合。"""

        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("candidate_ids 不得重复")
        if self.status == "pending" and self.action is not None:
            raise ValueError("待处理决策不得提前填写 action")
        if self.status != "pending" and self.action is None:
            raise ValueError("已完成或自动决策必须填写 action")
        if self.action in {"auto_merge_identical", "merge"}:
            if self.primary_candidate_id not in self.candidate_ids:
                raise ValueError("合并决策的 primary_candidate_id 必须属于候选组")
        elif self.primary_candidate_id is not None:
            raise ValueError("keep_separate/drop 不得填写 primary_candidate_id")
        if self.action != "merge" and self.reviewed_document is not None:
            raise ValueError("只有人工 merge 可以提供 reviewed_document")
        if self.status == "automatic" and self.action != "auto_merge_identical":
            raise ValueError("automatic 只允许 auto_merge_identical")
        return self


class Stage3LoreDecisionsArtifact(BaseModel):
    """保存完整集数范围内唯一的 Lore 去重决策。"""

    model_config = ConfigDict(extra="forbid")

    format_version: int = 0
    artifact_type: Literal["stage3_lore_decisions"] = "stage3_lore_decisions"
    episodes: list[int] = Field(default_factory=list)
    direct_sources: list[SourceFingerprint] = Field(default_factory=list)
    decisions: list[LoreDedupDecisionRecord] = Field(default_factory=list)
