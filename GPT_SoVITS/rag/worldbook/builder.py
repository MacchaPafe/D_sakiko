"""从审核产物确定性构建正式世界书 staging 包。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from rag.pipeline.review_migration import file_sha256
from rag.pipeline.review_models import ReviewFields, SourceFingerprint
from rag.pipeline.stage3_document_models import LoreEntryReviewRecord, Stage3DocumentReviewArtifact
from rag.pipeline.stage3_document_review import load_stage3_document_review_artifact
from rag.pipeline.stage3_lore_models import LoreDedupDecisionRecord, Stage3LoreDecisionsArtifact
from rag.pipeline.stage3_relation_models import Stage3RelationReviewArtifact
from rag.pipeline.stage3_relation_review import load_stage3_relation_review_artifact
from rag.pipeline.stage3_thought_aggregation import load_stage3_thought_review_artifact
from rag.pipeline.stage3_thought_models import Stage3ThoughtReviewArtifact
from rag.pipeline.schemas import LoreEntryPayload

from .build_models import EpisodeBuildInput, WorldbookBuildSpec
from .hashing import file_sha256 as output_file_sha256
from .identity_map import IdentityResolver
from .models import ContentFileRecord, EntryType, WorldbookEntry, WorldbookManifest


_ENTRY_FILES: tuple[tuple[EntryType, str], ...] = (
    ("story_event", "story_events.json"),
    ("character_relation", "character_relations.json"),
    ("lore_entry", "lore_entries.json"),
    ("character_thought", "character_thoughts.json"),
)


@dataclass(frozen=True, slots=True)
class ResolvedBuildSpec:
    """保存 build spec 及其相对配置文件解析后的路径。"""

    path: Path
    spec: WorldbookBuildSpec
    episodes: tuple[tuple[EpisodeBuildInput, Path, Path, Path, Path], ...]
    relation_review: Path
    thought_review: Path
    lore_decisions: Path
    id_map: Path
    official_root: Path
    build_root: Path
    build_report: Path


@dataclass(frozen=True, slots=True)
class BuiltWorldbookPackage:
    """保存一次 staging 构建的产物和使用的身份集合。"""

    manifest: WorldbookManifest
    entries: tuple[WorldbookEntry, ...]
    package_dir: Path
    input_sha256: dict[str, str]


def load_build_spec(path: str | Path) -> ResolvedBuildSpec:
    """读取单包 build spec，并相对配置文件解析全部路径。"""

    spec_path = Path(path).resolve()
    try:
        spec = WorldbookBuildSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ValueError(f"无效的 worldbook build spec: {spec_path}") from exc
    if spec.format_version != 0:
        raise ValueError(f"不支持 build spec v{spec.format_version}")
    base = spec_path.parent

    def resolve(value: str) -> Path:
        """相对 build spec 目录解析单个路径。"""

        candidate = Path(value)
        return (base / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()

    episode_paths = tuple(
        (
            item,
            resolve(item.stage2_input),
            resolve(item.stage2a_annotation),
            resolve(item.stage2b_annotation),
            resolve(item.rag_artifact),
        )
        for item in sorted(spec.episodes, key=lambda value: value.episode)
    )
    return ResolvedBuildSpec(
        path=spec_path,
        spec=spec,
        episodes=episode_paths,
        relation_review=resolve(spec.relation_review),
        thought_review=resolve(spec.thought_review),
        lore_decisions=resolve(spec.lore_decisions),
        id_map=resolve(spec.id_map),
        official_root=resolve(spec.official_root),
        build_root=resolve(spec.build_root),
        build_report=resolve(spec.build_report),
    )


def build_staging_package(
    resolved: ResolvedBuildSpec,
    resolver: IdentityResolver,
    package_dir: Path,
) -> BuiltWorldbookPackage:
    """校验审核门槛并把四类最终投影写入 staging。"""

    document_artifacts = [load_stage3_document_review_artifact(paths[4]) for paths in resolved.episodes]
    relation_artifact = load_stage3_relation_review_artifact(resolved.relation_review)
    thought_artifact = load_stage3_thought_review_artifact(resolved.thought_review)
    try:
        lore_decisions = Stage3LoreDecisionsArtifact.model_validate_json(
            resolved.lore_decisions.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ValueError("无效的全量 Lore decisions artifact") from exc
    input_sha256 = _collect_input_hashes(resolved)
    _validate_scope_and_freshness(
        resolved,
        document_artifacts,
        relation_artifact,
        thought_artifact,
        lore_decisions,
    )
    _validate_review_completion(
        resolved,
        document_artifacts,
        relation_artifact,
        thought_artifact,
        lore_decisions,
    )
    entries: list[WorldbookEntry] = []
    story_entry_ids: dict[str, UUID] = {}
    for artifact in document_artifacts:
        for record in artifact.story_events:
            if record.disposition != "publish":
                continue
            entry_id = resolver.resolve(record.candidate_id)
            story_entry_ids[record.candidate_id] = entry_id
            entries.append(
                WorldbookEntry(
                    entry_id=entry_id,
                    entry_type="story_event",
                    schema_version=0,
                    content=record.effective_document().model_dump(mode="json"),
                )
            )
    lore_records = {
        record.candidate_id: record
        for artifact in document_artifacts
        for record in artifact.lore_entries
        if record.disposition == "publish"
    }
    for candidate_id, record, document in _apply_lore_decisions(lore_records, lore_decisions):
        entries.append(
            WorldbookEntry(
                entry_id=resolver.resolve(candidate_id),
                entry_type="lore_entry",
                schema_version=0,
                content=document.model_dump(mode="json"),
            )
        )
    for relation_type in relation_artifact.relation_types:
        if relation_type.disposition != "publish":
            continue
        relation_type_key = resolver.resolve(relation_type.relation_type_id)
        for state in relation_type.effective_sequence():
            entries.append(
                WorldbookEntry(
                    entry_id=resolver.resolve(state.relation_state_id),
                    entry_type="character_relation",
                    schema_version=0,
                    content={
                        "subject_character_id": relation_type.subject_character_id.value,
                        "object_character_id": relation_type.object_character_id.value,
                        "series_id": relation_type.series_id,
                        "timeline_id": relation_type.timeline_id,
                        "canon_branch": relation_type.canon_branch.value,
                        "relation_type_key": str(relation_type_key),
                        "state_summary": state.state_summary,
                        "speech_hint": state.speech_hint,
                        "object_character_nickname": state.object_character_nickname,
                        "visible_from": state.visible_from,
                        "visible_to": state.visible_to,
                        "tags": state.tags,
                        "retrieval_text": state.retrieval_text,
                    },
                )
            )
    for thread in thought_artifact.threads:
        if thread.disposition != "publish":
            continue
        thread_key = resolver.resolve(thread.thought_thread_id)
        for state in thread.effective_sequence():
            event_ids: list[str] = []
            for candidate_id in state.story_event_candidate_ids:
                target = story_entry_ids.get(candidate_id)
                if target is None:
                    raise ValueError(f"Thought 引用了未发布的 Story Event: {candidate_id}")
                event_ids.append(str(target))
            entries.append(
                WorldbookEntry(
                    entry_id=resolver.resolve(state.thought_state_id),
                    entry_type="character_thought",
                    schema_version=0,
                    content={
                        "character_id": thread.character_id.value,
                        "series_id": thread.series_id,
                        "timeline_id": thread.timeline_id,
                        "canon_branch": thread.canon_branch.value,
                        "thought_thread_key": str(thread_key),
                        "canonical_subject": thread.canonical_subject,
                        "thought_aspect": thread.thought_aspect,
                        "thought_text": state.thought_text,
                        "epistemic_status": state.epistemic_status,
                        "visible_from": state.visible_from,
                        "visible_to": state.visible_to,
                        "story_event_entry_ids": sorted(set(event_ids)),
                        "tags": state.tags,
                        "retrieval_text": state.retrieval_text,
                    },
                )
            )
    manifest = _write_package(resolved.spec, package_dir, entries)
    return BuiltWorldbookPackage(
        manifest=manifest,
        entries=tuple(sorted(entries, key=lambda item: str(item.entry_id))),
        package_dir=package_dir,
        input_sha256=input_sha256,
    )


def _collect_input_hashes(resolved: ResolvedBuildSpec) -> dict[str, str]:
    """计算构建报告使用的全部输入摘要。"""

    paths = [resolved.path, resolved.relation_review, resolved.thought_review, resolved.lore_decisions]
    for _, stage2, stage2a, stage2b, rag in resolved.episodes:
        paths.extend([stage2, stage2a, stage2b, rag])
    return {str(path): file_sha256(path) for path in sorted(set(paths), key=str)}


def _expected_sources(
    resolved: ResolvedBuildSpec,
    roles: tuple[str, ...],
) -> list[SourceFingerprint]:
    """按逻辑角色建立构建配置当前直接来源摘要。"""

    fingerprints: list[SourceFingerprint] = []
    for episode, stage2, stage2a, stage2b, rag in resolved.episodes:
        role_paths = {
            "stage2_input": stage2,
            "stage2a_annotation": stage2a,
            "stage2b_annotation": stage2b,
            "stage3_rag": rag,
        }
        for role in roles:
            fingerprints.append(
                SourceFingerprint(role=role, episode=episode.episode, sha256=file_sha256(role_paths[role]))
            )
    return fingerprints


def _assert_sources_equal(
    label: str,
    actual: list[SourceFingerprint],
    expected: list[SourceFingerprint],
) -> None:
    """要求审核产物直接来源与当前配置内容完全一致。"""

    actual_keys = sorted((item.role, item.episode, item.sha256) for item in actual)
    expected_keys = sorted((item.role, item.episode, item.sha256) for item in expected)
    if actual_keys != expected_keys:
        raise ValueError(f"{label} 的直接来源摘要已过期，请重新生成或复核该产物")


def _validate_scope_and_freshness(
    resolved: ResolvedBuildSpec,
    documents: list[Stage3DocumentReviewArtifact],
    relations: Stage3RelationReviewArtifact,
    thoughts: Stage3ThoughtReviewArtifact,
    lore_decisions: Stage3LoreDecisionsArtifact,
) -> None:
    """校验集数、系列、时间线、分支和直接来源摘要。"""

    spec = resolved.spec
    expected_episodes = [item[0].episode for item in resolved.episodes]
    if len(documents) != len(expected_episodes):
        raise ValueError("每集必须恰好提供一份 Story/Lore Review")
    for expected_episode, artifact in zip(expected_episodes, documents):
        metadata = artifact.metadata
        if metadata.episode != expected_episode:
            raise ValueError("RAG artifact 与 build spec 集数不一致")
        if metadata.series_id != spec.series_id or metadata.timeline_id != spec.timeline_id or metadata.canon_branch != spec.canon_branch:
            raise ValueError("RAG artifact 的系列、时间线或分支与 build spec 不一致")
        if spec.story_year is not None and metadata.story_year != spec.story_year:
            raise ValueError("RAG artifact 的 story_year 与 build spec 不一致")
        episode_tuple = next(item for item in resolved.episodes if item[0].episode == expected_episode)
        expected = [
            SourceFingerprint(role="stage2_input", episode=expected_episode, sha256=file_sha256(episode_tuple[1])),
            SourceFingerprint(role="stage2a_annotation", episode=expected_episode, sha256=file_sha256(episode_tuple[2])),
        ]
        _assert_sources_equal(f"第 {expected_episode} 集 Story/Lore Review", artifact.direct_sources, expected)
    if sorted(relations.metadata.episodes) != expected_episodes:
        raise ValueError("Relation Review 的 episode coverage 不完整")
    if relations.metadata.series_id != spec.series_id.value or relations.metadata.timeline_id != spec.timeline_id or relations.metadata.canon_branch != spec.canon_branch.value:
        raise ValueError("Relation Review 的范围与 build spec 不一致")
    if sorted(thoughts.metadata.episodes) != expected_episodes:
        raise ValueError("Thought Review 的 episode coverage 不完整")
    if thoughts.metadata.series_id != spec.series_id.value or thoughts.metadata.timeline_id != spec.timeline_id or thoughts.metadata.canon_branch != spec.canon_branch:
        raise ValueError("Thought Review 的范围与 build spec 不一致")
    if sorted(lore_decisions.episodes) != expected_episodes:
        raise ValueError("Lore decisions 的 episode coverage 不完整")
    _assert_sources_equal(
        "Relation Review",
        relations.direct_sources,
        _expected_sources(resolved, ("stage2_input", "stage2a_annotation")),
    )
    _assert_sources_equal(
        "Thought Review",
        thoughts.direct_sources,
        _expected_sources(resolved, ("stage2_input", "stage2b_annotation", "stage3_rag")),
    )
    _assert_sources_equal(
        "Lore decisions",
        lore_decisions.direct_sources,
        _expected_sources(resolved, ("stage3_rag",)),
    )


def _assert_reviewed(label: str, fields: ReviewFields) -> None:
    """要求候选处于可发布门槛终态。"""

    if fields.review_status != "completed" or fields.disposition is None:
        raise ValueError(f"{label} 尚未完成审核")


def _validate_review_completion(
    resolved: ResolvedBuildSpec,
    documents: list[Stage3DocumentReviewArtifact],
    relations: Stage3RelationReviewArtifact,
    thoughts: Stage3ThoughtReviewArtifact,
    lore_decisions: Stage3LoreDecisionsArtifact,
) -> None:
    """验证四类候选终态、身份歧义、覆盖关系和序列结构。"""

    unfinished: list[tuple[str, Path, str]] = []
    for episode_paths, artifact in zip(resolved.episodes, documents):
        artifact_path = episode_paths[4]
        for record in [*artifact.story_events, *artifact.lore_entries]:
            if record.review_status != "completed" or record.disposition is None:
                unfinished.append((record.candidate_id, artifact_path, "review-stage3-item"))
    for record in [*relations.relation_types, *relations.unmerged_observations]:
        item_id = getattr(record, "relation_type_id", None) or record.observation_id
        if record.review_status != "completed" or record.disposition is None:
            unfinished.append((str(item_id), resolved.relation_review, "review-stage3-item"))
    for record in [*thoughts.threads, *thoughts.unassigned_updates]:
        item_id = getattr(record, "thought_thread_id", None) or record.update_id
        if record.review_status != "completed" or record.disposition is None:
            unfinished.append((str(item_id), resolved.thought_review, "review-stage3-item"))
    for decision in lore_decisions.decisions:
        if decision.status == "pending":
            unfinished.append((decision.group_id, resolved.lore_decisions, "review-lore-decision"))
    if unfinished:
        command_lines: list[str] = []
        for item_id, artifact_path, command in unfinished:
            if command == "review-lore-decision":
                command_lines.append(
                    f"PYTHONPATH=GPT_SoVITS python -m rag.pipeline {command} --artifact {artifact_path} --group-id '{item_id}' --action <merge|keep_separate|drop>"
                )
            else:
                command_lines.append(
                    f"PYTHONPATH=GPT_SoVITS python -m rag.pipeline {command} --artifact {artifact_path} --item-id '{item_id}' --disposition <publish|reject|exclude>"
                )
        raise ValueError(
            f"共有 {len(unfinished)} 个审核项未完成：\n" + "\n".join(command_lines)
        )

    for artifact in documents:
        if artifact.issues:
            raise ValueError("Story/Lore Review 仍包含规范化问题")
        for record in artifact.story_events:
            _assert_reviewed(record.candidate_id, record)
            if record.identity_suggestions:
                raise ValueError(f"Story Event 身份继承尚未确认: {record.candidate_id}")
        for record in artifact.lore_entries:
            _assert_reviewed(record.candidate_id, record)
            if record.identity_suggestions:
                raise ValueError(f"Lore Entry 身份继承尚未确认: {record.candidate_id}")
    if relations.issues:
        raise ValueError("Relation Review 仍包含聚合问题")
    relation_coverage: dict[str, int] = {}
    for record in relations.relation_types:
        _assert_reviewed(record.relation_type_id, record)
        if record.identity_suggestions:
            raise ValueError(f"Relation Type 身份继承尚未确认: {record.relation_type_id}")
        if record.disposition == "publish" and not record.effective_sequence():
            raise ValueError(f"发布的 Relation Type 不得为空: {record.relation_type_id}")
        _validate_sequence_windows(record.relation_type_id, [(item.visible_from, item.visible_to) for item in record.effective_sequence()])
        for observation_id in record.covered_observation_ids:
            relation_coverage[observation_id] = relation_coverage.get(observation_id, 0) + 1
    for decision in relations.unmerged_observations:
        _assert_reviewed(decision.observation_id, decision)
        if decision.disposition == "publish":
            raise ValueError("未合并 Relation Observation 不能直接发布")
        relation_coverage[decision.observation_id] = relation_coverage.get(decision.observation_id, 0) + 1
    relation_ids = {item.observation_id for item in relations.observations}
    if set(relation_coverage) != relation_ids or any(count != 1 for count in relation_coverage.values()):
        raise ValueError("Relation Observation 必须恰好被一个 Type 覆盖或被单独拒绝／排除")
    if thoughts.issues:
        raise ValueError("Thought Review 仍包含聚合问题")
    thought_coverage: dict[str, int] = {}
    for thread in thoughts.threads:
        _assert_reviewed(thread.thought_thread_id, thread)
        if thread.identity_suggestions:
            raise ValueError(f"Thought Thread 身份继承尚未确认: {thread.thought_thread_id}")
        if thread.disposition == "publish" and not thread.effective_sequence():
            raise ValueError(f"发布的 Thought Thread 不得为空: {thread.thought_thread_id}")
        _validate_sequence_windows(thread.thought_thread_id, [(item.visible_from, item.visible_to) for item in thread.effective_sequence()])
        for update_id in thread.covered_update_ids:
            thought_coverage[update_id] = thought_coverage.get(update_id, 0) + 1
    for decision in thoughts.unassigned_updates:
        _assert_reviewed(decision.update_id, decision)
        if decision.disposition == "publish":
            raise ValueError("未归属 Thought Update 不能直接发布")
        thought_coverage[decision.update_id] = thought_coverage.get(decision.update_id, 0) + 1
    update_ids = {item.update_id for item in thoughts.updates}
    if set(thought_coverage) != update_ids or any(count != 1 for count in thought_coverage.values()):
        raise ValueError("Thought Update 必须恰好被一个 Thread 覆盖或被单独拒绝／排除")
    for decision in lore_decisions.decisions:
        if decision.status == "pending":
            raise ValueError(f"Lore 去重决策尚未完成: {decision.group_id}")


def _validate_sequence_windows(label: str, windows: list[tuple[int, int]]) -> None:
    """要求状态序列按时间递增且互不重叠。"""

    ordered = sorted(windows)
    for index, (visible_from, visible_to) in enumerate(ordered):
        if visible_from > visible_to:
            raise ValueError(f"{label} 存在反向时间窗口")
        if index and ordered[index - 1][1] >= visible_from:
            raise ValueError(f"{label} 的状态时间窗口重叠")


def _apply_lore_decisions(
    records: dict[str, LoreEntryReviewRecord],
    artifact: Stage3LoreDecisionsArtifact,
) -> list[tuple[str, LoreEntryReviewRecord, LoreEntryPayload]]:
    """应用完整 Lore 去重决定并返回最终候选。"""

    effective_documents = {candidate_id: record.effective_document() for candidate_id, record in records.items()}
    removed: set[str] = set()
    seen: set[str] = set()
    for decision in artifact.decisions:
        unknown = set(decision.candidate_ids) - records.keys()
        if unknown:
            raise ValueError(f"Lore decision 引用了不存在或未发布的候选: {sorted(unknown)}")
        overlap = seen & set(decision.candidate_ids)
        if overlap:
            raise ValueError(f"Lore 候选出现在多个去重组: {sorted(overlap)}")
        seen.update(decision.candidate_ids)
        if decision.action == "keep_separate":
            continue
        if decision.action == "drop":
            removed.update(decision.candidate_ids)
            continue
        primary_id = decision.primary_candidate_id
        if primary_id is None:
            raise ValueError(f"Lore 合并决策缺少主候选: {decision.group_id}")
        if decision.action == "auto_merge_identical":
            payloads = {
                json.dumps(effective_documents[item].model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
                for item in decision.candidate_ids
            }
            if len(payloads) != 1:
                raise ValueError(f"自动 Lore 合并组并非完全等价: {decision.group_id}")
        if decision.reviewed_document is not None:
            effective_documents[primary_id] = decision.reviewed_document
        removed.update(set(decision.candidate_ids) - {primary_id})
    return [
        (candidate_id, record, effective_documents[candidate_id])
        for candidate_id, record in sorted(records.items())
        if candidate_id not in removed
    ]


def _write_package(
    spec: WorldbookBuildSpec,
    package_dir: Path,
    entries: list[WorldbookEntry],
) -> WorldbookManifest:
    """以固定分片顺序和 JSON 编码写入正式包。"""

    content_dir = package_dir / "content"
    content_dir.mkdir(parents=True, exist_ok=True)
    records: list[ContentFileRecord] = []
    for entry_type, file_name in _ENTRY_FILES:
        typed_entries = sorted(
            (item for item in entries if item.entry_type == entry_type),
            key=lambda item: str(item.entry_id),
        )
        if not typed_entries:
            continue
        output_path = content_dir / file_name
        _write_deterministic_json(
            output_path,
            {"entries": [item.model_dump(mode="json") for item in typed_entries]},
        )
        records.append(
            ContentFileRecord(
                path=f"content/{file_name}",
                sha256=output_file_sha256(output_path),
                entry_type=entry_type,
            )
        )
    manifest = WorldbookManifest(
        package_id=spec.package_id,
        package_version=spec.package_version,
        display_name=spec.display_name,
        package_type=spec.package_type,
        timeline_id=spec.timeline_id,
        dependencies=sorted(spec.dependencies, key=lambda item: item.package_id),
        content_files=records,
    )
    _write_deterministic_json(package_dir / "manifest.json", manifest.model_dump(mode="json"))
    return manifest


def _write_deterministic_json(path: Path, value: object) -> None:
    """用固定键顺序、缩进和结尾换行写 JSON。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
