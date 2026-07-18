"""构建并迁移单集 Story Event 与 Lore Entry 审核产物。"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from .review_migration import (
    blocked_removed_ids,
    build_source_fingerprint,
    canonical_json_sha256,
    safely_write_json_model,
    write_migration_report,
)
from .review_models import (
    IdentitySuggestion,
    MigrationCounts,
    ReviewMigrationReport,
    SourceFingerprint,
    copy_completed_review,
)
from .schemas import (
    LoreEntryImportRecord,
    Stage3NormalizedImportArtifact,
    StoryEventImportRecord,
)
from .stage2_document_extraction import load_stage2_annotation_artifact
from .stage2_input_builder import load_stage2_input_artifact
from .stage3_document_models import (
    LoreEntryReviewRecord,
    Stage3DocumentReviewArtifact,
    StoryEventReviewRecord,
)
from .stage3_rag_import import build_stage3_normalized_import_artifact


class ReviewMigrationBlockedError(ValueError):
    """表示已发布候选消失，且本次未取得删除许可。"""


def _story_basis(record: StoryEventImportRecord) -> str:
    """计算 Story Event 候选的统一审核基础摘要。"""

    return canonical_json_sha256(
        {
            "source_scene_id": record.source_scene_id,
            "source_local_id": record.source_local_id,
            "evidence_u_ids": sorted(set(record.evidence_u_ids)),
            "evidence_s_ids": sorted(set(record.evidence_s_ids)),
            "generated_document": record.document.model_dump(mode="json"),
        }
    )


def _lore_basis(record: LoreEntryImportRecord) -> str:
    """计算 Lore Entry 候选的统一审核基础摘要。"""

    return canonical_json_sha256(
        {
            "source_scene_id": record.source_scene_id,
            "source_local_id": record.source_local_id,
            "evidence_u_ids": sorted(set(record.evidence_u_ids)),
            "evidence_s_ids": sorted(set(record.evidence_s_ids)),
            "generated_document": record.document.model_dump(mode="json"),
        }
    )


def _evidence_similarity(first: set[str], second: set[str]) -> float:
    """用证据 ID 的 Jaccard 相似度生成非权威身份建议。"""

    union = first | second
    if not union:
        return 0.0
    return len(first & second) / len(union)


def _new_story_record(record: StoryEventImportRecord) -> StoryEventReviewRecord:
    """把旧规范化记录转换为新的 Story Event 审核记录。"""

    return StoryEventReviewRecord(
        candidate_id=f"story_candidate:{uuid4()}",
        legacy_source_id=record.point_id,
        source_scene_id=record.source_scene_id,
        source_local_id=record.source_local_id,
        evidence_u_ids=sorted(set(record.evidence_u_ids)),
        evidence_s_ids=sorted(set(record.evidence_s_ids)),
        confidence=record.confidence,
        generated_document=record.document,
        review_basis_sha256=_story_basis(record),
    )


def _new_lore_record(record: LoreEntryImportRecord) -> LoreEntryReviewRecord:
    """把旧规范化记录转换为新的 Lore Entry 审核记录。"""

    return LoreEntryReviewRecord(
        candidate_id=f"lore_candidate:{uuid4()}",
        legacy_source_id=record.point_id,
        source_scene_id=record.source_scene_id,
        source_local_id=record.source_local_id,
        evidence_u_ids=sorted(set(record.evidence_u_ids)),
        evidence_s_ids=sorted(set(record.evidence_s_ids)),
        confidence=record.confidence,
        generated_document=record.document,
        review_basis_sha256=_lore_basis(record),
    )


def _migrate_story_records(
    current: list[StoryEventReviewRecord],
    previous: list[StoryEventReviewRecord],
    report: ReviewMigrationReport,
) -> tuple[set[str], set[str]]:
    """迁移 Story Event 稳定身份和审核结果。"""

    previous_by_source = {
        (item.source_scene_id, item.source_local_id): item
        for item in previous
    }
    matched_previous: set[str] = set()
    pending_previous: set[str] = set()
    for item in current:
        old = previous_by_source.get((item.source_scene_id, item.source_local_id))
        if old is not None:
            item.candidate_id = old.candidate_id
            item.legacy_source_id = old.legacy_source_id or item.legacy_source_id
            item.previous_review_reference = old.candidate_id
            matched_previous.add(old.candidate_id)
            if item.review_basis_sha256 == old.review_basis_sha256:
                copy_completed_review(item, old)
                item.reviewed_document = old.reviewed_document
                report.migrated_ids.append(item.candidate_id)
            else:
                item.previous_reviewed_document = old.reviewed_document
                report.reset_ids.append(item.candidate_id)
            continue
        current_evidence = set(item.evidence_u_ids) | set(item.evidence_s_ids)
        suggestions: list[IdentitySuggestion] = []
        for candidate in previous:
            if candidate.candidate_id in matched_previous:
                continue
            previous_evidence = set(candidate.evidence_u_ids) | set(candidate.evidence_s_ids)
            score = _evidence_similarity(current_evidence, previous_evidence)
            if score <= 0.0:
                continue
            suggestions.append(
                IdentitySuggestion(
                    previous_id=candidate.candidate_id,
                    candidate_id=item.candidate_id,
                    reason="Story Event 证据集合存在重叠，需要确认是否为同一候选。",
                    confidence=score,
                )
            )
            pending_previous.add(candidate.candidate_id)
        if suggestions:
            item.identity_suggestions = sorted(suggestions, key=lambda value: value.confidence, reverse=True)
            item.review_status = "needs_followup"
            report.pending_identity_ids.append(item.candidate_id)
    return matched_previous, pending_previous


def _migrate_lore_records(
    current: list[LoreEntryReviewRecord],
    previous: list[LoreEntryReviewRecord],
    report: ReviewMigrationReport,
) -> tuple[set[str], set[str]]:
    """迁移 Lore Entry 稳定身份和审核结果。"""

    previous_by_source = {
        (item.source_scene_id, item.source_local_id): item
        for item in previous
    }
    matched_previous: set[str] = set()
    pending_previous: set[str] = set()
    for item in current:
        old = previous_by_source.get((item.source_scene_id, item.source_local_id))
        if old is not None:
            item.candidate_id = old.candidate_id
            item.legacy_source_id = old.legacy_source_id or item.legacy_source_id
            item.previous_review_reference = old.candidate_id
            matched_previous.add(old.candidate_id)
            if item.review_basis_sha256 == old.review_basis_sha256:
                copy_completed_review(item, old)
                item.reviewed_document = old.reviewed_document
                report.migrated_ids.append(item.candidate_id)
            else:
                item.previous_reviewed_document = old.reviewed_document
                report.reset_ids.append(item.candidate_id)
            continue
        current_evidence = set(item.evidence_u_ids) | set(item.evidence_s_ids)
        suggestions: list[IdentitySuggestion] = []
        for candidate in previous:
            if candidate.candidate_id in matched_previous:
                continue
            previous_evidence = set(candidate.evidence_u_ids) | set(candidate.evidence_s_ids)
            score = _evidence_similarity(current_evidence, previous_evidence)
            if score <= 0.0:
                continue
            suggestions.append(
                IdentitySuggestion(
                    previous_id=candidate.candidate_id,
                    candidate_id=item.candidate_id,
                    reason="Lore Entry 证据集合存在重叠，需要确认是否为同一候选。",
                    confidence=score,
                )
            )
            pending_previous.add(candidate.candidate_id)
        if suggestions:
            item.identity_suggestions = sorted(suggestions, key=lambda value: value.confidence, reverse=True)
            item.review_status = "needs_followup"
            report.pending_identity_ids.append(item.candidate_id)
    return matched_previous, pending_previous


def build_stage3_document_review_artifact(
    normalized: Stage3NormalizedImportArtifact,
    direct_sources: list[SourceFingerprint],
    previous: Stage3DocumentReviewArtifact | None = None,
    allowed_removed_ids: set[str] | None = None,
    allow_all_removed: bool = False,
    output_path: str = "",
) -> tuple[Stage3DocumentReviewArtifact, ReviewMigrationReport]:
    """从旧内部规范化结果构建新的 Story/Lore 审核产物。"""

    story_records = [_new_story_record(record) for record in normalized.story_events]
    lore_records = [_new_lore_record(record) for record in normalized.lore_entries]
    report = ReviewMigrationReport(
        artifact_type="stage3_document_review",
        output_path=output_path,
        succeeded=True,
    )
    matched_story: set[str] = set()
    pending_story: set[str] = set()
    matched_lore: set[str] = set()
    pending_lore: set[str] = set()
    if previous is not None:
        if previous.metadata.series_id != normalized.metadata.series_id or previous.metadata.episode != normalized.metadata.episode:
            raise ValueError("previous Story/Lore Review 的系列或集数与当前输入不兼容")
        matched_story, pending_story = _migrate_story_records(story_records, previous.story_events, report)
        matched_lore, pending_lore = _migrate_lore_records(lore_records, previous.lore_entries, report)
        disappeared = {
            item.candidate_id for item in previous.story_events if item.candidate_id not in matched_story | pending_story
        } | {
            item.candidate_id for item in previous.lore_entries if item.candidate_id not in matched_lore | pending_lore
        }
        completed_publish = {
            item.candidate_id
            for item in [*previous.story_events, *previous.lore_entries]
            if item.review_status == "completed" and item.disposition == "publish"
        }
        blocked = blocked_removed_ids(
            completed_publish,
            disappeared,
            allowed_removed_ids or set(),
            allow_all_removed,
        )
        report.disappeared_ids = sorted(disappeared)
        report.blocked_removed_ids = blocked
        if blocked:
            report.succeeded = False
            report.blocked_reason = "已发布候选从新结果中消失，需要显式删除许可。"
    all_current_ids = {item.candidate_id for item in [*story_records, *lore_records]}
    inherited_ids = matched_story | matched_lore
    report.added_ids = sorted(all_current_ids - inherited_ids)
    report.counts = MigrationCounts(
        old_candidates=0 if previous is None else len(previous.story_events) + len(previous.lore_entries),
        new_candidates=len(story_records) + len(lore_records),
        migrated_reviews=len(report.migrated_ids),
        reset_reviews=len(report.reset_ids),
        added_candidates=len(report.added_ids),
        disappeared_candidates=len(report.disappeared_ids),
        pending_identities=len(report.pending_identity_ids),
    )
    artifact = Stage3DocumentReviewArtifact(
        metadata=normalized.metadata,
        direct_sources=direct_sources,
        story_events=story_records,
        lore_entries=lore_records,
        issues=normalized.issues,
    )
    return artifact, report


def load_stage3_document_review_artifact(path: str | Path) -> Stage3DocumentReviewArtifact:
    """读取严格的新 Story/Lore 审核产物。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return Stage3DocumentReviewArtifact.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("输入不是新版 Story/Lore Review；请先运行 upgrade-stage3-review-schema") from exc


def normalize_stage3_documents_from_files(
    input_path: str | Path,
    annotation_path: str | Path,
    output_path: str | Path,
    previous_path: str | Path | None = None,
    fresh: bool = False,
    allowed_removed_ids: set[str] | None = None,
    allow_all_removed: bool = False,
) -> tuple[Stage3DocumentReviewArtifact, ReviewMigrationReport]:
    """从文件构建、迁移并安全保存 Story/Lore 审核产物。"""

    output = Path(output_path)
    resolved_previous: Path | None = None
    if not fresh:
        if previous_path is not None:
            resolved_previous = Path(previous_path)
        elif output.exists():
            resolved_previous = output
    previous = None if resolved_previous is None else load_stage3_document_review_artifact(resolved_previous)
    input_artifact = load_stage2_input_artifact(input_path)
    annotation_artifact = load_stage2_annotation_artifact(annotation_path)
    normalized = build_stage3_normalized_import_artifact(input_artifact, annotation_artifact)
    direct_sources = [
        build_source_fingerprint("stage2_input", input_path, normalized.metadata.episode),
        build_source_fingerprint("stage2a_annotation", annotation_path, normalized.metadata.episode),
    ]
    artifact, report = build_stage3_document_review_artifact(
        normalized,
        direct_sources,
        previous=previous,
        allowed_removed_ids=allowed_removed_ids,
        allow_all_removed=allow_all_removed,
        output_path=str(output.resolve()),
    )
    write_migration_report(report, output)
    if not report.succeeded:
        raise ReviewMigrationBlockedError(report.blocked_reason or "审核迁移被阻断")
    safely_write_json_model(artifact, output)
    return artifact, report
