"""定义 Stage 3 人工审核、来源摘要与迁移报告的公共模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ReviewStatus = Literal["unreviewed", "needs_followup", "completed"]
Disposition = Literal["publish", "reject", "exclude"]
SuggestedDisposition = Literal["publish", "reject", "exclude"]
RejectReasonCode = Literal["unsupported", "extraction_error", "duplicate", "other"]
ExcludeReasonCode = Literal[
    "transient_state",
    "too_trivial",
    "wrong_package",
    "not_long_term_knowledge",
    "other",
]
DispositionReasonCode = RejectReasonCode | ExcludeReasonCode
RiskLevel = Literal["low", "medium", "high"]


class SourceFingerprint(BaseModel):
    """描述审核产物的一份直接来源文件摘要。"""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1)
    episode: int | None = Field(default=None, ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewFields(BaseModel):
    """保存所有审核候选共享的人工状态与发布处置。"""

    model_config = ConfigDict(extra="forbid")

    review_status: ReviewStatus = "unreviewed"
    disposition: Disposition | None = None
    suggested_disposition: SuggestedDisposition | None = None
    disposition_reason_code: DispositionReasonCode | None = None
    disposition_reason_note: str | None = None
    review_notes: str | None = None
    review_basis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_review_reference: str | None = None
    risk_level: RiskLevel = "low"
    risk_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_review_decision(self) -> "ReviewFields":
        """确保审核状态、处置和结构化原因保持一致。"""

        if self.review_status != "completed" and self.disposition is not None:
            raise ValueError("审核未完成时 disposition 必须为空")
        if self.review_status == "completed" and self.disposition is None:
            raise ValueError("审核完成时必须填写 disposition")
        if self.disposition == "publish":
            if self.disposition_reason_code is not None:
                raise ValueError("publish 不得填写 disposition_reason_code")
        elif self.disposition == "reject":
            valid_reject_codes = {"unsupported", "extraction_error", "duplicate", "other"}
            if self.disposition_reason_code not in valid_reject_codes:
                raise ValueError("reject 必须使用合法的拒绝原因")
        elif self.disposition == "exclude":
            valid_exclude_codes = {
                "transient_state",
                "too_trivial",
                "wrong_package",
                "not_long_term_knowledge",
                "other",
            }
            if self.disposition_reason_code not in valid_exclude_codes:
                raise ValueError("exclude 必须使用合法的排除原因")
        elif self.disposition_reason_code is not None:
            raise ValueError("没有最终处置时不得填写 disposition_reason_code")
        if self.disposition_reason_code == "other" and not (self.disposition_reason_note or "").strip():
            raise ValueError("原因代码为 other 时必须填写说明")
        return self


class IdentitySuggestion(BaseModel):
    """表示待人工确认的旧身份继承建议。"""

    model_config = ConfigDict(extra="forbid")

    previous_id: str
    candidate_id: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class MigrationCounts(BaseModel):
    """汇总一次审核产物重生成的迁移数量。"""

    model_config = ConfigDict(extra="forbid")

    old_candidates: int = 0
    new_candidates: int = 0
    migrated_reviews: int = 0
    reset_reviews: int = 0
    added_candidates: int = 0
    disappeared_candidates: int = 0
    pending_identities: int = 0


class ReviewMigrationReport(BaseModel):
    """保存一次重生成的完整、可覆盖迁移诊断。"""

    model_config = ConfigDict(extra="forbid")

    artifact_type: str
    output_path: str
    succeeded: bool
    blocked_reason: str | None = None
    counts: MigrationCounts = Field(default_factory=MigrationCounts)
    migrated_ids: list[str] = Field(default_factory=list)
    reset_ids: list[str] = Field(default_factory=list)
    added_ids: list[str] = Field(default_factory=list)
    disappeared_ids: list[str] = Field(default_factory=list)
    blocked_removed_ids: list[str] = Field(default_factory=list)
    pending_identity_ids: list[str] = Field(default_factory=list)


def reset_review_fields(fields: ReviewFields) -> None:
    """在内容或结构被修改后撤销既有人工审批。"""

    fields.review_status = "unreviewed"
    fields.disposition = None
    fields.disposition_reason_code = None
    fields.disposition_reason_note = None


def complete_review(
    fields: ReviewFields,
    disposition: Disposition,
    reason_code: DispositionReasonCode | None = None,
    reason_note: str | None = None,
) -> None:
    """用统一规则完成人工审核并立即校验处置组合。"""

    candidate = fields.model_copy(
        update={
            "review_status": "completed",
            "disposition": disposition,
            "disposition_reason_code": reason_code,
            "disposition_reason_note": reason_note,
        }
    )
    validated = fields.__class__.model_validate(candidate.model_dump(mode="python"))
    fields.review_status = validated.review_status
    fields.disposition = validated.disposition
    fields.disposition_reason_code = validated.disposition_reason_code
    fields.disposition_reason_note = validated.disposition_reason_note


def copy_completed_review(target: ReviewFields, source: ReviewFields) -> None:
    """在审核基础完全一致时复制上一版人工审核结果。"""

    target.review_status = source.review_status
    target.disposition = source.disposition
    target.suggested_disposition = source.suggested_disposition
    target.disposition_reason_code = source.disposition_reason_code
    target.disposition_reason_note = source.disposition_reason_note
    target.review_notes = source.review_notes
