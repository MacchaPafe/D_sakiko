"""为 Stage 3 审核 CLI 提供薄文件适配器。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .review_migration import safely_write_json_model
from .review_models import Disposition, DispositionReasonCode
from .schemas import LoreEntryPayload, StoryEventPayload
from .stage3_document_models import Stage3DocumentReviewArtifact
from .stage3_lore_models import LoreDedupAction, Stage3LoreDecisionsArtifact
from .stage3_relation_models import RelationTypeContentDraft, Stage3RelationReviewArtifact
from .stage3_review_operations import (
    ClearLoreDecisionCommand,
    CompleteItemReviewCommand,
    CompleteLoreDecisionCommand,
    MarkItemFollowupCommand,
    ReplaceLoreDocumentCommand,
    ReplaceRelationContentCommand,
    ReplaceStoryDocumentCommand,
    ReplaceThoughtContentCommand,
    ResolveIdentityCommand,
    RestoreGeneratedContentCommand,
    ReviewArtifact,
    ReviewCommand,
    UpdateItemNotesCommand,
    apply_review_command,
)
from .stage3_thought_models import Stage3ThoughtReviewArtifact, ThoughtThreadContentDraft


def complete_artifact_item_review(
    artifact_path: str | Path,
    item_id: str,
    disposition: Disposition,
    reason_code: DispositionReasonCode | None = None,
    reason_note: str | None = None,
    review_notes: str | None = None,
) -> None:
    """完成一个普通审核项并安全替换文件。"""

    _apply_and_save(
        artifact_path,
        CompleteItemReviewCommand(
            item_id=item_id,
            disposition=disposition,
            reason_code=reason_code,
            reason_note=reason_note,
            review_notes=review_notes,
        ),
    )


def mark_artifact_item_followup(
    artifact_path: str | Path,
    item_id: str,
    review_notes: str | None = None,
) -> None:
    """把一个普通审核项标记为需要跟进。"""

    _apply_and_save(
        artifact_path,
        MarkItemFollowupCommand(item_id=item_id, review_notes=review_notes),
    )


def update_artifact_item_notes(
    artifact_path: str | Path,
    item_id: str,
    review_notes: str | None,
) -> None:
    """只修改审核备注，不撤销已有审批。"""

    _apply_and_save(
        artifact_path,
        UpdateItemNotesCommand(item_id=item_id, review_notes=review_notes),
    )


def replace_artifact_item_content(
    artifact_path: str | Path,
    item_id: str,
    replacement_path: str | Path,
) -> None:
    """写入完整人工快照并立即撤销既有审批。"""

    artifact = _load_review_artifact(artifact_path)
    replacement = json.loads(Path(replacement_path).read_text(encoding="utf-8"))
    if isinstance(artifact, Stage3DocumentReviewArtifact):
        if any(item.candidate_id == item_id for item in artifact.story_events):
            command = ReplaceStoryDocumentCommand(
                item_id=item_id,
                content=StoryEventPayload.model_validate(replacement),
            )
        elif any(item.candidate_id == item_id for item in artifact.lore_entries):
            command = ReplaceLoreDocumentCommand(
                item_id=item_id,
                content=LoreEntryPayload.model_validate(replacement),
            )
        else:
            raise KeyError(f"未找到可编辑审核项: {item_id}")
    elif isinstance(artifact, Stage3RelationReviewArtifact):
        command = ReplaceRelationContentCommand(
            item_id=item_id,
            content=RelationTypeContentDraft.model_validate(replacement),
        )
    elif isinstance(artifact, Stage3ThoughtReviewArtifact):
        command = ReplaceThoughtContentCommand(
            item_id=item_id,
            content=ThoughtThreadContentDraft.model_validate(replacement),
        )
    else:
        raise ValueError("Lore decisions 不支持 edit-stage3-item")
    _save_artifact(artifact_path, apply_review_command(artifact, command))


def restore_artifact_item_content(
    artifact_path: str | Path,
    item_id: str,
) -> None:
    """删除人工快照并恢复机器版本。"""

    _apply_and_save(
        artifact_path,
        RestoreGeneratedContentCommand(item_id=item_id),
    )


def resolve_artifact_identity(
    artifact_path: str | Path,
    item_id: str,
    choice: Literal["inherit", "new"],
    previous_id: str | None = None,
) -> str:
    """确认身份建议并返回确认后的开发侧稳定 ID。"""

    _apply_and_save(
        artifact_path,
        ResolveIdentityCommand(
            item_id=item_id,
            choice=choice,
            previous_id=previous_id,
        ),
    )
    if choice == "inherit":
        if previous_id is None:
            raise ValueError("inherit 必须指定 previous_id")
        return previous_id
    return item_id


def complete_lore_dedup_decision(
    artifact_path: str | Path,
    group_id: str,
    action: LoreDedupAction,
    primary_candidate_id: str | None = None,
    replacement_path: str | Path | None = None,
    review_notes: str | None = None,
) -> None:
    """完成一个人工 Lore 去重决定。"""

    reviewed_document = (
        None
        if replacement_path is None
        else LoreEntryPayload.model_validate_json(
            Path(replacement_path).read_text(encoding="utf-8")
        )
    )
    _apply_and_save(
        artifact_path,
        CompleteLoreDecisionCommand(
            group_id=group_id,
            action=action,
            primary_candidate_id=primary_candidate_id,
            reviewed_document=reviewed_document,
            review_notes=review_notes,
        ),
    )


def clear_lore_dedup_decision(
    artifact_path: str | Path,
    group_id: str,
) -> None:
    """清除一个人工 Lore 去重决定。"""

    _apply_and_save(artifact_path, ClearLoreDecisionCommand(group_id=group_id))


def _apply_and_save(
    artifact_path: str | Path,
    command: ReviewCommand,
) -> None:
    """加载审核文件、应用受支持命令并安全保存。"""

    artifact = _load_review_artifact(artifact_path)
    _save_artifact(
        artifact_path,
        apply_review_command(artifact, command),
    )


def _load_review_artifact(path: str | Path) -> ReviewArtifact:
    """按 artifact_type 读取四种审核文件。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("审核 artifact 顶层必须是对象")
    artifact_type = payload.get("artifact_type")
    if artifact_type == "stage3_document_review":
        return Stage3DocumentReviewArtifact.model_validate(payload)
    if artifact_type == "stage3_relation_review":
        return Stage3RelationReviewArtifact.model_validate(payload)
    if artifact_type == "stage3_thought_review":
        return Stage3ThoughtReviewArtifact.model_validate(payload)
    if artifact_type == "stage3_lore_decisions":
        return Stage3LoreDecisionsArtifact.model_validate(payload)
    raise ValueError(f"不支持的审核 artifact_type: {artifact_type}")


def _save_artifact(path: str | Path, artifact: BaseModel) -> None:
    """完整校验后安全替换审核文件。"""

    safely_write_json_model(artifact, path)
