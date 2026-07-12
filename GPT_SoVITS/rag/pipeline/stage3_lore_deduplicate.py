"""Stage3 lore 条目的规范化去重工具。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Literal, cast
from urllib.parse import quote

from rapidfuzz import fuzz

from .schemas import LoreEntryImportRecord, Stage3NormalizedImportArtifact
from .stage3_rag_import import (
    load_stage3_artifacts_from_directory,
    save_stage3_artifacts_to_directory,
)


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]
LoreDedupAction = Literal["auto_merge_identical", "merge", "keep_separate", "drop"]

FUZZY_WRATIO_THRESHOLD = 72.0
FUZZY_PARTIAL_RATIO_THRESHOLD = 90.0


@dataclass(frozen=True, slots=True)
class LoreScopeKey:
    """表示 lore 条目的适用范围身份。"""

    scope_type: str
    series_ids: tuple[str, ...]
    applicable_story_years: tuple[int, ...]
    canon_branch: str

    def to_json(self) -> JsonObject:
        """将范围身份转换为可写入 JSON 的字典。"""

        return {
            "scope_type": self.scope_type,
            "series_ids": list(self.series_ids),
            "applicable_story_years": list(self.applicable_story_years),
            "canon_branch": self.canon_branch,
        }


@dataclass(frozen=True, slots=True)
class LoreRecordSnapshot:
    """表示 review 文件中展示的一条 lore 候选快照。"""

    record_id: str
    file_name: str
    lore_index: int
    original_point_id: str
    canonical_point_id: str
    canonical_title: str
    scope_key: LoreScopeKey
    source_scene_id: str
    source_local_id: str
    title: str
    content: str
    retrieval_text: str
    tags: tuple[str, ...]
    evidence_u_ids: tuple[str, ...]
    evidence_s_ids: tuple[str, ...]
    confidence: float

    def to_json(self) -> JsonObject:
        """将候选快照转换为可写入 JSON 的字典。"""

        return {
            "record_id": self.record_id,
            "file_name": self.file_name,
            "lore_index": self.lore_index,
            "original_point_id": self.original_point_id,
            "canonical_point_id": self.canonical_point_id,
            "canonical_title": self.canonical_title,
            "scope_key": self.scope_key.to_json(),
            "source_scene_id": self.source_scene_id,
            "source_local_id": self.source_local_id,
            "title": self.title,
            "content": self.content,
            "retrieval_text": self.retrieval_text,
            "tags": list(self.tags),
            "evidence_u_ids": list(self.evidence_u_ids),
            "evidence_s_ids": list(self.evidence_s_ids),
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class LoreReviewGroup:
    """表示需要人工或自动处理的一组 lore 候选。"""

    group_id: str
    group_type: str
    reason: str
    records: tuple[LoreRecordSnapshot, ...]
    requires_decision: bool
    canonical_point_id: str | None = None
    similarity_score: float | None = None

    def to_json(self) -> JsonObject:
        """将 review 分组转换为可写入 JSON 的字典。"""

        payload: JsonObject = {
            "group_id": self.group_id,
            "group_type": self.group_type,
            "reason": self.reason,
            "requires_decision": self.requires_decision,
            "records": [record.to_json() for record in self.records],
        }
        if self.canonical_point_id is not None:
            payload["canonical_point_id"] = self.canonical_point_id
        if self.similarity_score is not None:
            payload["similarity_score"] = self.similarity_score
        return payload


@dataclass(frozen=True, slots=True)
class LoreDedupDecision:
    """表示一条可重复执行的 lore 去重人工决策。"""

    group_id: str
    action: LoreDedupAction
    primary_record_id: str | None = None
    merged_record_ids: tuple[str, ...] = field(default_factory=tuple)
    target_point_id: str | None = None
    edited_title: str | None = None
    edited_content: str | None = None
    edited_retrieval_text: str | None = None

    def to_json(self) -> JsonObject:
        """将去重决策转换为可写入 JSON 的字典。"""

        return {
            "group_id": self.group_id,
            "action": self.action,
            "primary_record_id": self.primary_record_id,
            "merged_record_ids": list(self.merged_record_ids),
            "target_point_id": self.target_point_id,
            "edited_title": self.edited_title,
            "edited_content": self.edited_content,
            "edited_retrieval_text": self.edited_retrieval_text,
        }

    @classmethod
    def from_json(cls, payload: JsonObject) -> "LoreDedupDecision":
        """从 JSON 字典中读取一条去重决策。"""

        action = _required_action(payload, "action")
        return cls(
            group_id=_required_str(payload, "group_id"),
            action=action,
            primary_record_id=_optional_str(payload, "primary_record_id"),
            merged_record_ids=tuple(_optional_str_list(payload, "merged_record_ids")),
            target_point_id=_optional_str(payload, "target_point_id"),
            edited_title=_optional_str(payload, "edited_title"),
            edited_content=_optional_str(payload, "edited_content"),
            edited_retrieval_text=_optional_str(payload, "edited_retrieval_text"),
        )


@dataclass(frozen=True, slots=True)
class LoreDedupReviewArtifact:
    """表示 lore 去重 review 队列和自动决策草稿。"""

    input_files: tuple[str, ...]
    exact_groups: tuple[LoreReviewGroup, ...]
    fuzzy_groups: tuple[LoreReviewGroup, ...]
    automatic_decisions: tuple[LoreDedupDecision, ...]

    def to_json(self) -> JsonObject:
        """将 review artifact 转换为可写入 JSON 的字典。"""

        return {
            "review_version": 1,
            "input_files": list(self.input_files),
            "exact_groups": [group.to_json() for group in self.exact_groups],
            "fuzzy_groups": [group.to_json() for group in self.fuzzy_groups],
            "automatic_decisions": [decision.to_json() for decision in self.automatic_decisions],
        }


@dataclass(slots=True)
class _MutableLoreRecord:
    """表示应用决策时可被修改的一条 lore 记录。"""

    file_name: str
    lore_index: int
    artifact: Stage3NormalizedImportArtifact
    record: LoreEntryImportRecord

    @property
    def record_id(self) -> str:
        """返回当前记录在 review/decision 文件中的稳定标识。"""

        return build_lore_record_id(self.file_name, self.lore_index)


def normalize_lore_title(title: str) -> str:
    """将 lore 标题规范化为用于身份判定的标题。"""

    normalized = re.sub(r"\s+", " ", title.strip())
    return _strip_wrapping_marks(normalized)


def normalize_lore_text(value: str) -> str:
    """将 lore 文本规范化为用于机械重复判断的文本。"""

    return re.sub(r"\s+", " ", value.strip())


def build_lore_record_id(file_name: str, lore_index: int) -> str:
    """构造 review/decision 文件中使用的记录标识。"""

    return f"{file_name}:lore:{lore_index}"


def build_lore_title_slug(title: str) -> str:
    """构造 canonical lore point id 中可读且安全的标题片段。"""

    normalized = normalize_lore_title(title)
    normalized = re.sub(r"\s+", "_", normalized)
    return "".join(_quote_lore_slug_character(character) for character in normalized)


def build_lore_scope_key(record: LoreEntryImportRecord) -> LoreScopeKey:
    """从 lore 记录中构造范围身份。"""

    document = record.document
    series_ids = tuple(str(series_id.value) for series_id in (document.series_ids or []))
    applicable_story_years = tuple(document.applicable_story_years or [])
    return LoreScopeKey(
        scope_type=str(document.scope_type.value),
        series_ids=series_ids,
        applicable_story_years=applicable_story_years,
        canon_branch=str(document.canon_branch.value),
    )


def build_canonical_lore_point_id(record: LoreEntryImportRecord) -> str:
    """根据 lore 记录的 canonical lore key 构造稳定 point_id。"""

    scope_key = build_lore_scope_key(record)
    series_segment = _build_series_segment(scope_key)
    season_segment = _build_season_segment(scope_key)
    title_slug = build_lore_title_slug(record.document.title)
    return "lore_entry:{0}:{1}:{2}:{3}".format(
        series_segment,
        season_segment,
        scope_key.canon_branch,
        title_slug,
    )


def canonicalize_lore_point_ids(artifact: Stage3NormalizedImportArtifact) -> Stage3NormalizedImportArtifact:
    """将 artifact 中全部 lore point_id 改写为 canonical point id。"""

    for record in artifact.lore_entries:
        record.point_id = build_canonical_lore_point_id(record)
    return artifact


def build_lore_dedup_review(
    artifacts: list[tuple[Path, Stage3NormalizedImportArtifact]],
) -> LoreDedupReviewArtifact:
    """根据一组 stage3 artifact 构造 lore 去重 review artifact。"""

    snapshots = _build_snapshots(artifacts)
    exact_groups, automatic_decisions = _build_exact_review_groups(snapshots)
    fuzzy_groups = _build_fuzzy_review_groups(snapshots)
    return LoreDedupReviewArtifact(
        input_files=tuple(path.name for path, _ in artifacts),
        exact_groups=tuple(exact_groups),
        fuzzy_groups=tuple(fuzzy_groups),
        automatic_decisions=tuple(automatic_decisions),
    )


def build_lore_dedup_review_from_directory(input_dir: str | Path) -> LoreDedupReviewArtifact:
    """从目录读取 stage3 artifacts 并构造 lore 去重 review artifact。"""

    return build_lore_dedup_review(load_stage3_artifacts_from_directory(input_dir))


def save_lore_dedup_review_artifact(artifact: LoreDedupReviewArtifact, output_path: str | Path) -> None:
    """保存 lore 去重 review artifact。"""

    _write_json(output_path, artifact.to_json())


def save_lore_dedup_decisions(decisions: list[LoreDedupDecision], output_path: str | Path) -> None:
    """保存 lore 去重 decisions 文件。"""

    _write_json(
        output_path,
        {
            "decision_version": 1,
            "decisions": [decision.to_json() for decision in decisions],
        },
    )


def load_lore_dedup_decisions(input_path: str | Path) -> list[LoreDedupDecision]:
    """读取 lore 去重 decisions 文件。"""

    payload = _read_json_object(input_path)
    raw_decisions = payload.get("decisions", [])
    if not isinstance(raw_decisions, list):
        raise ValueError("decisions 字段必须是列表")

    decisions: list[LoreDedupDecision] = []
    for raw_decision in raw_decisions:
        if not isinstance(raw_decision, dict):
            raise ValueError("decisions 中的每一项都必须是对象")
        decisions.append(LoreDedupDecision.from_json(cast(JsonObject, raw_decision)))
    return decisions


def apply_lore_dedup_decisions(
    artifacts: list[tuple[Path, Stage3NormalizedImportArtifact]],
    decisions: list[LoreDedupDecision],
) -> list[tuple[Path, Stage3NormalizedImportArtifact]]:
    """应用 lore 去重决策并返回去重后的 artifacts。"""

    mutable_records = _build_mutable_records(artifacts)
    records_by_id = {record.record_id: record for record in mutable_records}
    removed_record_ids: set[str] = set()

    for mutable_record in mutable_records:
        mutable_record.record.point_id = build_canonical_lore_point_id(mutable_record.record)

    for decision in decisions:
        _apply_lore_dedup_decision(decision, records_by_id, removed_record_ids)

    _auto_merge_remaining_identical_records(mutable_records, removed_record_ids)
    _fail_on_unresolved_point_id_conflicts(mutable_records, removed_record_ids)
    _remove_records_from_artifacts(artifacts, removed_record_ids)
    return artifacts


def apply_lore_dedup_decisions_from_files(
    input_dir: str | Path,
    decisions_path: str | Path,
) -> list[tuple[Path, Stage3NormalizedImportArtifact]]:
    """从文件读取 stage3 artifacts 与 decisions 并应用去重。"""

    artifacts = load_stage3_artifacts_from_directory(input_dir)
    decisions = load_lore_dedup_decisions(decisions_path)
    return apply_lore_dedup_decisions(artifacts, decisions)


def save_deduped_stage3_artifacts(
    artifacts: list[tuple[Path, Stage3NormalizedImportArtifact]],
    output_dir: str | Path,
) -> None:
    """将去重后的 stage3 artifacts 保存到新目录。"""

    save_stage3_artifacts_to_directory(artifacts, output_dir)


def _strip_wrapping_marks(value: str) -> str:
    """移除标题首尾常见包裹符号。"""

    wrapping_pairs = {
        "「": "」",
        "『": "』",
        "《": "》",
        "〈": "〉",
        "“": "”",
        "\"": "\"",
        "'": "'",
    }
    result = value
    changed = True
    while changed and len(result) >= 2:
        changed = False
        for left_mark, right_mark in wrapping_pairs.items():
            if result.startswith(left_mark) and result.endswith(right_mark):
                result = result[len(left_mark) : -len(right_mark)].strip()
                changed = True
                break
    return result


def _quote_lore_slug_character(character: str) -> str:
    """对 point_id 标题片段中的单个字符做必要转义。"""

    if character.isalnum() or character in {"*", "!", "?", "-", "_", "."}:
        return character
    return quote(character, safe="")


def _build_series_segment(scope_key: LoreScopeKey) -> str:
    """构造 canonical point id 中的 series 片段。"""

    if scope_key.scope_type == "global":
        return "global"
    if not scope_key.series_ids:
        return "series_unknown"
    return "+".join(scope_key.series_ids)


def _build_season_segment(scope_key: LoreScopeKey) -> str:
    """构造 canonical point id 中的 season 片段。"""

    if not scope_key.applicable_story_years:
        return "all_seasons"
    return "+".join(f"y{story_year}" for story_year in scope_key.applicable_story_years)


def _build_snapshots(
    artifacts: list[tuple[Path, Stage3NormalizedImportArtifact]],
) -> list[LoreRecordSnapshot]:
    """从 artifacts 中构造全部 lore 记录快照。"""

    snapshots: list[LoreRecordSnapshot] = []
    for path, artifact in artifacts:
        for lore_index, record in enumerate(artifact.lore_entries):
            snapshots.append(_build_snapshot(path.name, lore_index, record))
    return snapshots


def _build_snapshot(file_name: str, lore_index: int, record: LoreEntryImportRecord) -> LoreRecordSnapshot:
    """从单条 lore 记录构造 review 快照。"""

    scope_key = build_lore_scope_key(record)
    return LoreRecordSnapshot(
        record_id=build_lore_record_id(file_name, lore_index),
        file_name=file_name,
        lore_index=lore_index,
        original_point_id=record.point_id,
        canonical_point_id=build_canonical_lore_point_id(record),
        canonical_title=normalize_lore_title(record.document.title),
        scope_key=scope_key,
        source_scene_id=record.source_scene_id,
        source_local_id=record.source_local_id,
        title=record.document.title,
        content=record.document.content,
        retrieval_text=record.document.retrieval_text,
        tags=tuple(record.document.tags),
        evidence_u_ids=tuple(record.evidence_u_ids),
        evidence_s_ids=tuple(record.evidence_s_ids),
        confidence=float(record.confidence),
    )


def _build_exact_review_groups(
    snapshots: list[LoreRecordSnapshot],
) -> tuple[list[LoreReviewGroup], list[LoreDedupDecision]]:
    """构造 exact duplicate 分组与可自动执行的决策。"""

    grouped_snapshots: dict[str, list[LoreRecordSnapshot]] = {}
    for snapshot in snapshots:
        grouped_snapshots.setdefault(snapshot.canonical_point_id, []).append(snapshot)

    groups: list[LoreReviewGroup] = []
    automatic_decisions: list[LoreDedupDecision] = []
    for canonical_point_id, group_snapshots in sorted(grouped_snapshots.items()):
        if len(group_snapshots) <= 1:
            continue

        records = tuple(group_snapshots)
        is_automatic = _records_have_equivalent_lore_text(records)
        reason = (
            "同一 canonical point id 且文本完全等价，可自动合并"
            if is_automatic
            else "同一 canonical point id 但文本不同，需要人工选择主记录或编辑结果"
        )
        groups.append(
            LoreReviewGroup(
                group_id=canonical_point_id,
                group_type="exact",
                reason=reason,
                records=records,
                requires_decision=not is_automatic,
                canonical_point_id=canonical_point_id,
            )
        )
        if is_automatic:
            automatic_decisions.append(
                LoreDedupDecision(
                    group_id=canonical_point_id,
                    action="auto_merge_identical",
                    primary_record_id=records[0].record_id,
                    merged_record_ids=tuple(record.record_id for record in records),
                )
            )
    return groups, automatic_decisions


def _build_fuzzy_review_groups(snapshots: list[LoreRecordSnapshot]) -> list[LoreReviewGroup]:
    """构造 fuzzy duplicate review 分组。"""

    canonical_groups: dict[str, list[LoreRecordSnapshot]] = {}
    for snapshot in snapshots:
        canonical_groups.setdefault(snapshot.canonical_point_id, []).append(snapshot)

    representatives = [group[0] for group in canonical_groups.values()]
    fuzzy_groups: list[LoreReviewGroup] = []
    fuzzy_index = 1
    for left_index, left_snapshot in enumerate(representatives):
        for right_snapshot in representatives[left_index + 1 :]:
            if left_snapshot.scope_key != right_snapshot.scope_key:
                continue
            if left_snapshot.canonical_point_id == right_snapshot.canonical_point_id:
                continue
            similarity_score = _fuzzy_title_similarity(left_snapshot.canonical_title, right_snapshot.canonical_title)
            if similarity_score < FUZZY_WRATIO_THRESHOLD:
                partial_score = float(
                    fuzz.partial_ratio(left_snapshot.canonical_title, right_snapshot.canonical_title)
                )
                if partial_score < FUZZY_PARTIAL_RATIO_THRESHOLD:
                    continue
                similarity_score = partial_score

            records = tuple(
                canonical_groups[left_snapshot.canonical_point_id]
                + canonical_groups[right_snapshot.canonical_point_id]
            )
            fuzzy_groups.append(
                LoreReviewGroup(
                    group_id=f"fuzzy:{fuzzy_index:04d}",
                    group_type="fuzzy",
                    reason="标题相似，建议人工确认是否为同一 lore entry",
                    records=records,
                    requires_decision=True,
                    similarity_score=round(similarity_score, 3),
                )
            )
            fuzzy_index += 1
    return fuzzy_groups


def _fuzzy_title_similarity(left_title: str, right_title: str) -> float:
    """使用 rapidfuzz 计算标题相似度。"""

    return float(fuzz.WRatio(left_title, right_title))


def _records_have_equivalent_lore_text(records: tuple[LoreRecordSnapshot, ...]) -> bool:
    """判断一组 lore 记录是否属于机械重复。"""

    first = records[0]
    first_title = normalize_lore_text(first.title)
    first_content = normalize_lore_text(first.content)
    first_retrieval_text = normalize_lore_text(first.retrieval_text)
    for record in records[1:]:
        if normalize_lore_text(record.title) != first_title:
            return False
        if normalize_lore_text(record.content) != first_content:
            return False
        if normalize_lore_text(record.retrieval_text) != first_retrieval_text:
            return False
    return True


def _build_mutable_records(
    artifacts: list[tuple[Path, Stage3NormalizedImportArtifact]],
) -> list[_MutableLoreRecord]:
    """从 artifacts 中构造可变 lore 记录索引。"""

    mutable_records: list[_MutableLoreRecord] = []
    for path, artifact in artifacts:
        for lore_index, record in enumerate(artifact.lore_entries):
            mutable_records.append(
                _MutableLoreRecord(
                    file_name=path.name,
                    lore_index=lore_index,
                    artifact=artifact,
                    record=record,
                )
            )
    return mutable_records


def _apply_lore_dedup_decision(
    decision: LoreDedupDecision,
    records_by_id: dict[str, _MutableLoreRecord],
    removed_record_ids: set[str],
) -> None:
    """将单条 lore 去重决策应用到可变记录集合。"""

    if decision.action == "keep_separate":
        return
    if decision.action == "drop":
        record_ids = _decision_record_ids(decision)
        if not record_ids:
            raise ValueError(f"{decision.group_id} 的 drop 决策缺少记录 id")
        removed_record_ids.update(record_ids)
        return
    if decision.action in {"auto_merge_identical", "merge"}:
        _apply_merge_decision(decision, records_by_id, removed_record_ids)
        return
    raise ValueError(f"不支持的 lore 去重动作: {decision.action}")


def _decision_record_ids(decision: LoreDedupDecision) -> set[str]:
    """读取一条决策涉及的全部记录 id。"""

    record_ids = set(decision.merged_record_ids)
    if decision.primary_record_id is not None:
        record_ids.add(decision.primary_record_id)
    return record_ids


def _apply_merge_decision(
    decision: LoreDedupDecision,
    records_by_id: dict[str, _MutableLoreRecord],
    removed_record_ids: set[str],
) -> None:
    """应用 merge 或 auto merge 决策。"""

    if decision.primary_record_id is None:
        raise ValueError(f"{decision.group_id} 的 merge 决策缺少 primary_record_id")
    primary_record = _get_existing_record(decision.primary_record_id, records_by_id)
    merged_record_ids = set(decision.merged_record_ids)
    merged_record_ids.add(decision.primary_record_id)
    if len(merged_record_ids) <= 1:
        return

    merged_records = [_get_existing_record(record_id, records_by_id) for record_id in sorted(merged_record_ids)]
    _merge_lore_record_payload(primary_record.record, [record.record for record in merged_records])

    if decision.edited_title is not None:
        primary_record.record.document.title = normalize_lore_title(decision.edited_title)
    if decision.edited_content is not None:
        primary_record.record.document.content = normalize_lore_text(decision.edited_content)
    if decision.edited_retrieval_text is not None:
        primary_record.record.document.retrieval_text = normalize_lore_text(decision.edited_retrieval_text)

    primary_record.record.point_id = decision.target_point_id or build_canonical_lore_point_id(primary_record.record)
    for record_id in merged_record_ids:
        if record_id != decision.primary_record_id:
            removed_record_ids.add(record_id)


def _get_existing_record(record_id: str, records_by_id: dict[str, _MutableLoreRecord]) -> _MutableLoreRecord:
    """根据 record_id 读取可变记录，缺失时抛出清晰异常。"""

    try:
        return records_by_id[record_id]
    except KeyError as exc:
        raise ValueError(f"decisions 中引用了不存在的 lore record_id: {record_id}") from exc


def _merge_lore_record_payload(primary_record: LoreEntryImportRecord, records: list[LoreEntryImportRecord]) -> None:
    """将多条 lore 记录的 tags 与证据链合并到主记录。"""

    merged_tags: list[str] = []
    merged_evidence_u_ids: list[str] = []
    merged_evidence_s_ids: list[str] = []
    for record in records:
        merged_tags.extend(record.document.tags)
        merged_evidence_u_ids.extend(record.evidence_u_ids)
        merged_evidence_s_ids.extend(record.evidence_s_ids)

    primary_record.document.tags = _unique_strings(merged_tags)
    primary_record.evidence_u_ids = _unique_strings(merged_evidence_u_ids)
    primary_record.evidence_s_ids = _unique_strings(merged_evidence_s_ids)


def _auto_merge_remaining_identical_records(
    mutable_records: list[_MutableLoreRecord],
    removed_record_ids: set[str],
) -> None:
    """自动合并 decisions 未显式处理的机械重复记录。"""

    grouped_records: dict[str, list[_MutableLoreRecord]] = {}
    for mutable_record in mutable_records:
        if mutable_record.record_id in removed_record_ids:
            continue
        grouped_records.setdefault(mutable_record.record.point_id, []).append(mutable_record)

    for group in grouped_records.values():
        if len(group) <= 1:
            continue
        snapshots = tuple(
            _build_snapshot(mutable_record.file_name, mutable_record.lore_index, mutable_record.record)
            for mutable_record in group
        )
        if not _records_have_equivalent_lore_text(snapshots):
            continue
        primary_record = group[0]
        _merge_lore_record_payload(primary_record.record, [mutable_record.record for mutable_record in group])
        for duplicate_record in group[1:]:
            removed_record_ids.add(duplicate_record.record_id)


def _fail_on_unresolved_point_id_conflicts(
    mutable_records: list[_MutableLoreRecord],
    removed_record_ids: set[str],
) -> None:
    """在仍存在同 point_id 多记录冲突时中止输出。"""

    grouped_records: dict[str, list[_MutableLoreRecord]] = {}
    for mutable_record in mutable_records:
        if mutable_record.record_id in removed_record_ids:
            continue
        grouped_records.setdefault(mutable_record.record.point_id, []).append(mutable_record)

    conflicts = {
        point_id: records
        for point_id, records in grouped_records.items()
        if len(records) > 1
    }
    if not conflicts:
        return

    conflict_lines = []
    for point_id, records in sorted(conflicts.items()):
        record_ids = ", ".join(record.record_id for record in records)
        conflict_lines.append(f"{point_id}: {record_ids}")
    raise ValueError("存在未解决的 lore point_id 冲突，请补充 decisions:\n" + "\n".join(conflict_lines))


def _remove_records_from_artifacts(
    artifacts: list[tuple[Path, Stage3NormalizedImportArtifact]],
    removed_record_ids: set[str],
) -> None:
    """从 artifacts 中删除已被合并或丢弃的 lore 记录。"""

    for path, artifact in artifacts:
        retained_records: list[LoreEntryImportRecord] = []
        for lore_index, record in enumerate(artifact.lore_entries):
            record_id = build_lore_record_id(path.name, lore_index)
            if record_id not in removed_record_ids:
                retained_records.append(record)
        artifact.lore_entries = retained_records


def _unique_strings(values: list[str]) -> list[str]:
    """对字符串列表做保序去重并移除空白项。"""

    result: list[str] = []
    seen_values: set[str] = set()
    for value in values:
        normalized = normalize_lore_text(value)
        if not normalized or normalized in seen_values:
            continue
        seen_values.add(normalized)
        result.append(normalized)
    return result


def _read_json_object(input_path: str | Path) -> JsonObject:
    """读取 JSON 对象文件。"""

    path = Path(input_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} 的顶层 JSON 必须是对象")
    return cast(JsonObject, payload)


def _write_json(output_path: str | Path, payload: JsonObject) -> None:
    """写入格式化 JSON 对象。"""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _required_str(payload: JsonObject, field_name: str) -> str:
    """从 JSON 对象中读取必填字符串字段。"""

    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} 字段必须是非空字符串")
    return value


def _optional_str(payload: JsonObject, field_name: str) -> str | None:
    """从 JSON 对象中读取可选字符串字段。"""

    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 字段必须是字符串或 null")
    return value


def _optional_str_list(payload: JsonObject, field_name: str) -> list[str]:
    """从 JSON 对象中读取可选字符串列表字段。"""

    value = payload.get(field_name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} 字段必须是字符串列表")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} 字段必须是字符串列表")
        result.append(item)
    return result


def _required_action(payload: JsonObject, field_name: str) -> LoreDedupAction:
    """从 JSON 对象中读取必填去重动作字段。"""

    value = _required_str(payload, field_name)
    if value not in {"auto_merge_identical", "merge", "keep_separate", "drop"}:
        raise ValueError(f"{field_name} 字段包含不支持的去重动作: {value}")
    return cast(LoreDedupAction, value)
