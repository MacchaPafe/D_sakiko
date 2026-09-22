"""从多集 Story/Lore Review 生成可审核的 Lore 去重决定。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError
from rapidfuzz import fuzz

from .review_migration import canonical_json_sha256, safely_write_json_model
from .review_models import SourceFingerprint
from .stage3_document_models import LoreEntryReviewRecord
from .stage3_document_review import load_stage3_document_review_artifact
from .stage3_lore_models import LoreDedupDecisionRecord, Stage3LoreDecisionsArtifact


def build_stage3_lore_decisions(
    rag_paths: list[str | Path],
    previous_path: str | Path | None = None,
) -> Stage3LoreDecisionsArtifact:
    """聚合已审核发布的 Lore 候选并生成自动或待人工决定的去重组。"""

    if not rag_paths:
        raise ValueError("至少需要一份 Story/Lore Review")
    artifacts = [load_stage3_document_review_artifact(path) for path in rag_paths]
    episodes = [artifact.metadata.episode for artifact in artifacts]
    if len(episodes) != len(set(episodes)):
        raise ValueError("Lore decisions 输入集数不得重复")
    unfinished = [
        record.candidate_id
        for artifact in artifacts
        for record in artifact.lore_entries
        if record.review_status != "completed" or record.disposition is None
    ]
    if unfinished:
        preview = ", ".join(unfinished[:10])
        raise ValueError(f"仍有 {len(unfinished)} 条 Lore 未审核，无法生成最终去重决定: {preview}")
    records = [
        record
        for artifact in artifacts
        for record in artifact.lore_entries
        if record.disposition == "publish"
    ]
    groups = _connected_duplicate_groups(records)
    decisions = [_new_decision(group) for group in groups if len(group) > 1]
    previous = None if previous_path is None else load_stage3_lore_decisions(previous_path)
    if previous is not None:
        previous_by_candidates = {
            tuple(sorted(item.candidate_ids)): item
            for item in previous.decisions
        }
        for decision in decisions:
            old = previous_by_candidates.get(tuple(sorted(decision.candidate_ids)))
            if old is None or old.decision_basis_sha256 != decision.decision_basis_sha256:
                continue
            decision.status = old.status
            decision.action = old.action
            decision.primary_candidate_id = old.primary_candidate_id
            decision.reviewed_document = old.reviewed_document
            decision.review_notes = old.review_notes
    direct_sources = [
        SourceFingerprint(
            role="stage3_rag",
            episode=artifact.metadata.episode,
            sha256=_file_sha256(Path(path)),
        )
        for path, artifact in zip(rag_paths, artifacts)
    ]
    return Stage3LoreDecisionsArtifact(
        episodes=sorted(episodes),
        direct_sources=sorted(direct_sources, key=lambda item: item.episode or 0),
        decisions=sorted(decisions, key=lambda item: item.group_id),
    )


def load_stage3_lore_decisions(path: str | Path) -> Stage3LoreDecisionsArtifact:
    """读取严格的跨集 Lore decisions artifact。"""

    try:
        return Stage3LoreDecisionsArtifact.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ValueError("输入不是新版 Stage 3 Lore decisions") from exc


def save_stage3_lore_decisions(
    artifact: Stage3LoreDecisionsArtifact,
    path: str | Path,
) -> None:
    """安全保存可版本管理的 Lore decisions artifact。"""

    safely_write_json_model(artifact, path)


def _connected_duplicate_groups(
    records: list[LoreEntryReviewRecord],
) -> list[list[LoreEntryReviewRecord]]:
    """把相同范围内标题相似的 Lore 候选合成互斥连通组。"""

    parent = list(range(len(records)))

    def find(index: int) -> int:
        """查找并压缩候选所属集合根节点。"""

        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        """合并两个候选集合。"""

        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_index, left in enumerate(records):
        left_document = left.effective_document()
        for right_index in range(left_index + 1, len(records)):
            right_document = records[right_index].effective_document()
            if _scope_key(left_document.model_dump(mode="json")) != _scope_key(right_document.model_dump(mode="json")):
                continue
            left_title = _normalize_title(left_document.title)
            right_title = _normalize_title(right_document.title)
            if left_title == right_title or fuzz.WRatio(left_title, right_title) >= 72 or fuzz.partial_ratio(left_title, right_title) >= 90:
                union(left_index, right_index)
    grouped: dict[int, list[LoreEntryReviewRecord]] = {}
    for index, record in enumerate(records):
        grouped.setdefault(find(index), []).append(record)
    return list(grouped.values())


def _new_decision(records: list[LoreEntryReviewRecord]) -> LoreDedupDecisionRecord:
    """为一个重复候选组创建自动或待人工决定。"""

    ordered = sorted(records, key=lambda item: item.candidate_id)
    payloads = [item.effective_document().model_dump(mode="json") for item in ordered]
    basis = canonical_json_sha256(
        {
            "candidate_ids": [item.candidate_id for item in ordered],
            "documents": payloads,
        }
    )
    group_id = f"lore_group:{basis[:16]}"
    serialized = {json.dumps(payload, ensure_ascii=False, sort_keys=True) for payload in payloads}
    if len(serialized) == 1:
        return LoreDedupDecisionRecord(
            group_id=group_id,
            decision_basis_sha256=basis,
            candidate_ids=[item.candidate_id for item in ordered],
            status="automatic",
            action="auto_merge_identical",
            primary_candidate_id=ordered[0].candidate_id,
        )
    return LoreDedupDecisionRecord(
        group_id=group_id,
        decision_basis_sha256=basis,
        candidate_ids=[item.candidate_id for item in ordered],
    )


def _normalize_title(value: str) -> str:
    """生成用于相似匹配的紧凑标题。"""

    return "".join(value.strip().lower().split())


def _scope_key(payload: dict[str, object]) -> tuple[str, str, str, str]:
    """从 Lore 文档提取机械适用范围键。"""

    series_ids = payload.get("series_ids")
    series_key = json.dumps(series_ids, ensure_ascii=False, sort_keys=True)
    years_key = json.dumps(payload.get("applicable_story_years"), ensure_ascii=False, sort_keys=True)
    return (
        str(payload.get("scope_type", "")),
        series_key,
        years_key,
        str(payload.get("canon_branch", "")),
    )


def _file_sha256(path: Path) -> str:
    """计算 Lore decisions 直接输入文件摘要。"""

    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
