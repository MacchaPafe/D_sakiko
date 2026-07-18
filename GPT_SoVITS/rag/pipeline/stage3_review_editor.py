"""四类 Stage 3 审核产物的统一命令行编辑操作。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, TypeAdapter, ValidationError

from .review_migration import safely_write_json_model
from .review_models import (
    Disposition,
    DispositionReasonCode,
    ReviewFields,
    complete_review,
    reset_review_fields,
)
from .schemas import LoreEntryPayload, StoryEventPayload
from .stage3_document_models import (
    LoreEntryReviewRecord,
    Stage3DocumentReviewArtifact,
    StoryEventReviewRecord,
)
from .stage3_lore_models import LoreDedupAction, Stage3LoreDecisionsArtifact
from .stage3_relation_models import RelationStateDraft, RelationTypeReviewRecord, Stage3RelationReviewArtifact
from .stage3_thought_models import (
    Stage3ThoughtReviewArtifact,
    ThoughtStateDraft,
    ThoughtThreadReviewRecord,
)


ReviewArtifact = (
    Stage3DocumentReviewArtifact
    | Stage3RelationReviewArtifact
    | Stage3ThoughtReviewArtifact
)
IdentityReviewItem = (
    StoryEventReviewRecord
    | LoreEntryReviewRecord
    | RelationTypeReviewRecord
    | ThoughtThreadReviewRecord
)


def complete_artifact_item_review(
    artifact_path: str | Path,
    item_id: str,
    disposition: Disposition,
    reason_code: DispositionReasonCode | None = None,
    reason_note: str | None = None,
    review_notes: str | None = None,
) -> None:
    """定位四类审核项，写入人工终态并安全替换原文件。"""

    artifact = _load_review_artifact(artifact_path)
    item = _find_review_item(artifact, item_id)
    if review_notes is not None:
        item.review_notes = review_notes
    complete_review(item, disposition, reason_code, reason_note)
    _save_validated_artifact(artifact_path, artifact)


def update_artifact_item_notes(
    artifact_path: str | Path,
    item_id: str,
    review_notes: str | None,
) -> None:
    """只修改审核备注，不撤销已有审核状态。"""

    artifact = _load_review_artifact(artifact_path)
    item = _find_review_item(artifact, item_id)
    item.review_notes = review_notes
    _save_validated_artifact(artifact_path, artifact)


def replace_artifact_item_content(
    artifact_path: str | Path,
    item_id: str,
    replacement_path: str | Path,
) -> None:
    """用完整人工快照替换发布内容，并立即撤销既有审批。"""

    artifact = _load_review_artifact(artifact_path)
    raw_replacement = json.loads(Path(replacement_path).read_text(encoding="utf-8"))
    if isinstance(artifact, Stage3DocumentReviewArtifact):
        for record in artifact.story_events:
            if record.candidate_id == item_id:
                record.reviewed_document = StoryEventPayload.model_validate(raw_replacement)
                reset_review_fields(record)
                _save_validated_artifact(artifact_path, artifact)
                return
        for record in artifact.lore_entries:
            if record.candidate_id == item_id:
                record.reviewed_document = LoreEntryPayload.model_validate(raw_replacement)
                reset_review_fields(record)
                _save_validated_artifact(artifact_path, artifact)
                return
    elif isinstance(artifact, Stage3RelationReviewArtifact):
        for record in artifact.relation_types:
            if record.relation_type_id == item_id:
                record.reviewed_sequence = TypeAdapter(list[RelationStateDraft]).validate_python(raw_replacement)
                reset_review_fields(record)
                _save_validated_artifact(artifact_path, artifact)
                return
    else:
        for record in artifact.threads:
            if record.thought_thread_id == item_id:
                record.reviewed_sequence = TypeAdapter(list[ThoughtStateDraft]).validate_python(raw_replacement)
                reset_review_fields(record)
                _save_validated_artifact(artifact_path, artifact)
                return
    raise KeyError(f"未找到可编辑审核项: {item_id}")


def resolve_artifact_identity(
    artifact_path: str | Path,
    item_id: str,
    choice: Literal["inherit", "new"],
    previous_id: str | None = None,
) -> str:
    """确认身份建议，选择继承旧身份或保留当前新身份。"""

    artifact = _load_review_artifact(artifact_path)
    item = _find_identity_item(artifact, item_id)
    suggestions = item.identity_suggestions
    if choice == "inherit":
        if previous_id is None or previous_id not in {value.previous_id for value in suggestions}:
            raise ValueError("inherit 必须指定该候选现有建议中的 previous_id")
        new_id = previous_id
        if isinstance(item, (StoryEventReviewRecord, LoreEntryReviewRecord)):
            item.candidate_id = new_id
        elif isinstance(item, RelationTypeReviewRecord):
            item.relation_type_id = new_id
        else:
            item.thought_thread_id = new_id
    else:
        new_id = item_id
    item.identity_suggestions = []
    reset_review_fields(item)
    _save_validated_artifact(artifact_path, artifact)
    return new_id


def complete_lore_dedup_decision(
    artifact_path: str | Path,
    group_id: str,
    action: LoreDedupAction,
    primary_candidate_id: str | None = None,
    replacement_path: str | Path | None = None,
    review_notes: str | None = None,
) -> None:
    """完成人工 Lore 去重决定并校验合并文档。"""

    try:
        artifact = Stage3LoreDecisionsArtifact.model_validate_json(
            Path(artifact_path).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ValueError("输入不是 Stage 3 Lore decisions") from exc
    decision = next((item for item in artifact.decisions if item.group_id == group_id), None)
    if decision is None:
        raise KeyError(f"未找到 Lore 去重组: {group_id}")
    reviewed_document = None
    if replacement_path is not None:
        reviewed_document = LoreEntryPayload.model_validate_json(
            Path(replacement_path).read_text(encoding="utf-8")
        )
    updated = decision.model_copy(
        update={
            "status": "completed",
            "action": action,
            "primary_candidate_id": primary_candidate_id,
            "reviewed_document": reviewed_document,
            "review_notes": review_notes,
        }
    )
    validated = decision.__class__.model_validate(updated.model_dump(mode="python"))
    artifact.decisions[artifact.decisions.index(decision)] = validated
    safely_write_json_model(artifact, artifact_path)


def _load_review_artifact(path: str | Path) -> ReviewArtifact:
    """按 artifact_type 读取三种承载四类候选的审核产物。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("审核 artifact 顶层必须是对象")
    artifact_type = payload.get("artifact_type")
    if artifact_type == "stage3_document_review":
        return Stage3DocumentReviewArtifact.model_validate(payload)
    elif artifact_type == "stage3_relation_review":
        return Stage3RelationReviewArtifact.model_validate(payload)
    elif artifact_type == "stage3_thought_review":
        return Stage3ThoughtReviewArtifact.model_validate(payload)
    else:
        raise ValueError(f"不支持的审核 artifact_type: {artifact_type}")


def _find_review_item(artifact: ReviewArtifact, item_id: str) -> ReviewFields:
    """按稳定开发 ID 查找可完成人工处置的审核项。"""

    if isinstance(artifact, Stage3DocumentReviewArtifact):
        items: list[ReviewFields] = [*artifact.story_events, *artifact.lore_entries]
    elif isinstance(artifact, Stage3RelationReviewArtifact):
        items = [*artifact.relation_types, *artifact.unmerged_observations]
    else:
        items = [*artifact.threads, *artifact.unassigned_updates]
    for item in items:
        identifiers = {
            str(getattr(item, field_name))
            for field_name in (
                "candidate_id",
                "relation_type_id",
                "observation_id",
                "thought_thread_id",
                "update_id",
            )
            if hasattr(item, field_name)
        }
        if item_id in identifiers:
            return item
    raise KeyError(f"未找到审核项: {item_id}")


def _find_identity_item(
    artifact: ReviewArtifact,
    item_id: str,
) -> IdentityReviewItem:
    """查找带 identity_suggestions 的顶层候选。"""

    candidates: list[IdentityReviewItem]
    if isinstance(artifact, Stage3DocumentReviewArtifact):
        candidates = [*artifact.story_events, *artifact.lore_entries]
    elif isinstance(artifact, Stage3RelationReviewArtifact):
        candidates = list(artifact.relation_types)
    else:
        candidates = list(artifact.threads)
    for item in candidates:
        current_id = (
            getattr(item, "candidate_id", None)
            or getattr(item, "relation_type_id", None)
            or getattr(item, "thought_thread_id", None)
        )
        if current_id == item_id:
            return item
    raise KeyError(f"未找到身份候选: {item_id}")


def _save_validated_artifact(path: str | Path, artifact: BaseModel) -> None:
    """重新执行完整 Schema 校验后安全替换审核文件。"""

    validated = artifact.__class__.model_validate(artifact.model_dump(mode="python"))
    safely_write_json_model(validated, path)
