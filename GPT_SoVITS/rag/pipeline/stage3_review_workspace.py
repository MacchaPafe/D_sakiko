"""管理 Stage 3 审核工作台的多文件草稿、保存与来源状态。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import tempfile

from pydantic import BaseModel

from rag.worldbook.builder import ResolvedBuildSpec, load_build_spec
from rag.worldbook.build_models import WorldbookBuildReport
from rag.worldbook.publisher import validate_worldbook_build

from .review_migration import file_sha256, safely_write_json_model
from .review_models import SourceFingerprint
from .schemas import Stage2InputArtifact
from .stage2_input_builder import load_stage2_input_artifact
from .stage3_document_models import Stage3DocumentReviewArtifact
from .stage3_lore_models import Stage3LoreDecisionsArtifact
from .stage3_relation_models import Stage3RelationReviewArtifact
from .stage3_review_operations import ReviewArtifact, ReviewCommand, apply_review_command
from .stage3_source_revalidation import (
    SourceRevalidationPreview,
    build_acceptance_log,
    build_source_projection,
    load_source_acceptance_log,
    projection_differences,
    save_generation_baseline,
    source_acceptance_path,
    validate_current_references,
)
from .stage3_thought_models import Stage3ThoughtReviewArtifact


@dataclass(slots=True)
class ArtifactSlot:
    """保存一个审核文件的路径、草稿与会话历史。"""

    key: str
    label: str
    path: Path
    artifact: ReviewArtifact | None
    loaded_sha256: str | None
    existed_when_loaded: bool
    dirty: bool = False
    undo_stack: list[ReviewArtifact] = field(default_factory=list)
    redo_stack: list[ReviewArtifact] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SaveAllResult:
    """描述一次保存全部的逐文件结果。"""

    saved_keys: tuple[str, ...]
    failed: dict[str, str]


@dataclass(frozen=True, slots=True)
class FreshnessResult:
    """描述一个审核产物的缺失或来源过期状态。"""

    key: str
    missing: bool
    stale_sources: tuple[str, ...]

    @property
    def is_fresh(self) -> bool:
        """返回该产物是否存在且直接来源完全一致。"""

        return not self.missing and not self.stale_sources


@dataclass(frozen=True, slots=True)
class SourceAcceptanceResult:
    """描述一次来源重新确认的保存、审计和剩余过期状态。"""

    slot_key: str
    sidecar_path: Path
    audit_report: WorldbookBuildReport
    stale_slots: tuple[str, ...]
    newly_stale_slots: tuple[str, ...]


class ReviewWorkspace:
    """以世界书构建配置为入口管理完整 Stage 3 审核会话。"""

    def __init__(
        self,
        build_spec_path: str | Path,
        *,
        bootstrap_baselines: bool = True,
    ) -> None:
        """加载构建配置及当前存在的全部审核产物。"""

        self.resolved: ResolvedBuildSpec = load_build_spec(build_spec_path)
        self.slots: dict[str, ArtifactSlot] = {}
        self.current_key: str | None = None
        self._stage2_cache: dict[int, Stage2InputArtifact] = {}
        self.baseline_warnings: list[str] = []
        self._load_slots()
        if bootstrap_baselines:
            self._bootstrap_fresh_baselines()

    def _load_slots(self) -> None:
        """按构建配置创建逐集与全量审核文件槽位。"""

        for episode, _, _, _, rag_path in self.resolved.episodes:
            key = f"document:{episode.episode}"
            self.slots[key] = self._load_slot(
                key,
                f"第 {episode.episode} 集 Story/Lore",
                rag_path,
            )
        self.slots["relation"] = self._load_slot(
            "relation",
            "全量 Relation",
            self.resolved.relation_review,
        )
        self.slots["thought"] = self._load_slot(
            "thought",
            "全量 Thought",
            self.resolved.thought_review,
        )
        self.slots["lore_decisions"] = self._load_slot(
            "lore_decisions",
            "全量 Lore 去重",
            self.resolved.lore_decisions,
        )
        self.current_key = next(iter(self.slots), None)

    @staticmethod
    def _load_slot(key: str, label: str, path: Path) -> ArtifactSlot:
        """读取一个存在的审核文件，缺失文件保留占位。"""

        if not path.exists():
            return ArtifactSlot(
                key=key,
                label=label,
                path=path,
                artifact=None,
                loaded_sha256=None,
                existed_when_loaded=False,
            )
        return ArtifactSlot(
            key=key,
            label=label,
            path=path,
            artifact=load_review_artifact(path),
            loaded_sha256=file_sha256(path),
            existed_when_loaded=True,
        )

    def select(self, key: str) -> None:
        """切换当前审核文件。"""

        if key not in self.slots:
            raise KeyError(f"未知审核文件: {key}")
        self.current_key = key

    def current_slot(self) -> ArtifactSlot:
        """返回当前审核文件槽位。"""

        if self.current_key is None:
            raise ValueError("工作区没有可用审核槽位")
        return self.slots[self.current_key]

    def apply(self, key: str, command: ReviewCommand) -> ReviewArtifact:
        """对指定文件草稿应用一个类型化审核命令。"""

        slot = self.slots[key]
        if slot.artifact is None:
            raise ValueError(f"审核文件尚未生成: {slot.path}")
        before = slot.artifact.model_copy(deep=True)
        updated = apply_review_command(slot.artifact, command)
        slot.undo_stack.append(before)
        slot.redo_stack.clear()
        slot.artifact = updated
        slot.dirty = True
        return updated

    def undo(self, key: str | None = None) -> bool:
        """撤销指定文件的上一个会话内命令。"""

        slot = self.slots[key or self.current_slot().key]
        if slot.artifact is None or not slot.undo_stack:
            return False
        slot.redo_stack.append(slot.artifact.model_copy(deep=True))
        slot.artifact = slot.undo_stack.pop()
        slot.dirty = True
        return True

    def redo(self, key: str | None = None) -> bool:
        """重做指定文件刚撤销的会话内命令。"""

        slot = self.slots[key or self.current_slot().key]
        if slot.artifact is None or not slot.redo_stack:
            return False
        slot.undo_stack.append(slot.artifact.model_copy(deep=True))
        slot.artifact = slot.redo_stack.pop()
        slot.dirty = True
        return True

    def save_current(self) -> Path:
        """完整校验并安全保存当前审核文件。"""

        return self.save(self.current_slot().key)

    def save(self, key: str) -> Path:
        """完整校验并安全保存指定审核文件。"""

        slot = self.slots[key]
        if slot.artifact is None:
            raise ValueError(f"审核文件尚未生成: {slot.path}")
        self._assert_no_external_change(slot)
        validated = validate_review_artifact(slot.artifact)
        safely_write_json_model(validated, slot.path)
        slot.artifact = validated
        slot.loaded_sha256 = file_sha256(slot.path)
        slot.existed_when_loaded = True
        slot.dirty = False
        return slot.path

    def save_all(self) -> SaveAllResult:
        """先校验全部脏文件，再逐文件安全保存并报告结果。"""

        dirty_slots = [slot for slot in self.slots.values() if slot.dirty]
        for slot in dirty_slots:
            if slot.artifact is None:
                raise ValueError(f"脏槽位缺少审核内容: {slot.key}")
            validate_review_artifact(slot.artifact)
            self._assert_no_external_change(slot)
        saved: list[str] = []
        failed: dict[str, str] = {}
        for slot in dirty_slots:
            try:
                self.save(slot.key)
                saved.append(slot.key)
            except OSError as exc:
                failed[slot.key] = f"{type(exc).__name__}: {exc}"
        return SaveAllResult(saved_keys=tuple(saved), failed=failed)

    def reload(self, key: str, discard_dirty: bool = False) -> None:
        """从磁盘重新加载指定文件，并清空该文件会话历史。"""

        slot = self.slots[key]
        if slot.dirty and not discard_dirty:
            raise ValueError("存在未保存草稿，重新加载前必须显式丢弃")
        reloaded = self._load_slot(slot.key, slot.label, slot.path)
        self.slots[key] = reloaded
        self._bootstrap_slot_baseline(reloaded)

    def dirty_keys(self) -> tuple[str, ...]:
        """返回当前所有包含未保存修改的文件键。"""

        return tuple(slot.key for slot in self.slots.values() if slot.dirty)

    def freshness(self, key: str) -> FreshnessResult:
        """比较 artifact 记录的直接来源与构建配置当前文件。"""

        slot = self.slots[key]
        if slot.artifact is None:
            return FreshnessResult(key=key, missing=True, stale_sources=())
        expected = self._expected_sources(key)
        actual = _artifact_sources(slot.artifact)
        stale: list[str] = []
        for source_key, source_path in expected.items():
            fingerprint = actual.get(source_key)
            if not source_path.exists():
                stale.append(f"{source_key[0]}@{source_key[1]}:missing")
            elif fingerprint is None or fingerprint.sha256 != file_sha256(source_path):
                stale.append(f"{source_key[0]}@{source_key[1]}")
        for extra_key in set(actual) - set(expected):
            stale.append(f"unexpected:{extra_key[0]}@{extra_key[1]}")
        return FreshnessResult(
            key=key,
            missing=False,
            stale_sources=tuple(sorted(stale)),
        )

    def all_freshness(self) -> tuple[FreshnessResult, ...]:
        """返回全部审核文件的来源状态。"""

        return tuple(self.freshness(key) for key in self.slots)

    def preview_source_revalidation(
        self,
        key: str | None = None,
    ) -> SourceRevalidationPreview:
        """计算当前来源、消费投影和本地基线之间的可确认差异。"""

        if self.dirty_keys():
            raise ValueError("处理来源变化前必须先保存全部草稿")
        slot = self.slots[key or self.current_slot().key]
        if slot.artifact is None or not slot.path.exists():
            raise ValueError("审核产物尚未生成，不能重新确认来源")
        self._assert_no_external_change(slot)
        freshness = self.freshness(slot.key)
        if freshness.is_fresh:
            raise ValueError("当前审核产物来源未过期，无需重新确认")
        new_fingerprints = self.expected_source_fingerprints(slot.key)
        current_baseline = build_source_projection(
            self.resolved,
            slot.key,
            new_fingerprints,
        )
        baseline_status, log = load_source_acceptance_log(slot.path)
        old_fingerprints = list(slot.artifact.direct_sources)
        projection_equal = False
        differences = []
        if baseline_status == "ready" and log is not None:
            stored = log.current_baseline
            if not _fingerprints_equal(
                stored.source_fingerprints,
                old_fingerprints,
            ):
                baseline_status = "incompatible"
            elif (
                stored.projection_kind == current_baseline.projection_kind
                and stored.projection_version == current_baseline.projection_version
            ):
                projection_equal = (
                    stored.projection_sha256 == current_baseline.projection_sha256
                )
                differences = projection_differences(
                    stored.projection,
                    current_baseline.projection,
                )
            else:
                baseline_status = "incompatible"
        return SourceRevalidationPreview(
            slot_key=slot.key,
            artifact_path=str(slot.path.resolve()),
            artifact_sha256=file_sha256(slot.path),
            build_spec_sha256=file_sha256(self.resolved.path),
            stale_sources=list(freshness.stale_sources),
            baseline_status=baseline_status,
            projection_equal=projection_equal,
            can_accept_automatically=(
                baseline_status == "ready" and projection_equal
            ),
            old_source_fingerprints=old_fingerprints,
            new_source_fingerprints=new_fingerprints,
            current_baseline=current_baseline,
            differences=differences,
        )

    def accept_current_sources(
        self,
        preview: SourceRevalidationPreview,
        *,
        force: bool = False,
        reason: str | None = None,
    ) -> SourceAcceptanceResult:
        """重新校验预览并原子保存当前来源指纹与本地接受记录。"""

        if self.dirty_keys():
            raise ValueError("接受当前来源前必须先保存全部草稿")
        if force and not (reason or "").strip():
            raise ValueError("人工接受来源变化必须填写确认说明")
        slot = self.slots.get(preview.slot_key)
        if slot is None or slot.artifact is None:
            raise ValueError("预览对应的审核产物已经不存在")
        self._assert_no_external_change(slot)
        current = self.preview_source_revalidation(slot.key)
        _assert_same_revalidation_preview(preview, current)
        if not force and not current.can_accept_automatically:
            raise ValueError("消费投影不同或缺少兼容基线，必须显式人工确认")
        candidate = validate_review_artifact(
            slot.artifact.model_copy(
                update={"direct_sources": current.new_source_fingerprints},
                deep=True,
            )
        )
        validate_current_references(self.resolved, slot.key, candidate)
        mode = "manual" if force else "automatic"
        log = build_acceptance_log(
            slot.path,
            current,
            mode=mode,
            reason=reason,
        )
        sidecar = source_acceptance_path(slot.path)
        stale_before = {
            result.key
            for result in self.all_freshness()
            if not result.missing and result.stale_sources
        }
        artifact_before = slot.path.read_bytes()
        sidecar_existed = sidecar.exists()
        sidecar_before = sidecar.read_bytes() if sidecar_existed else None
        try:
            safely_write_json_model(candidate, slot.path)
            safely_write_json_model(log, sidecar)
        except BaseException:
            _write_bytes_atomically(slot.path, artifact_before)
            if sidecar_before is not None:
                _write_bytes_atomically(sidecar, sidecar_before)
            elif sidecar.exists():
                sidecar.unlink()
            raise
        self.reload(slot.key, discard_dirty=True)
        self.select(slot.key)
        audit_report = validate_worldbook_build(self.resolved.path)
        stale_slots = tuple(
            result.key
            for result in self.all_freshness()
            if not result.missing and result.stale_sources
        )
        newly_stale_slots = tuple(
            key for key in stale_slots if key not in stale_before
        )
        return SourceAcceptanceResult(
            slot_key=slot.key,
            sidecar_path=sidecar,
            audit_report=audit_report,
            stale_slots=stale_slots,
            newly_stale_slots=newly_stale_slots,
        )

    def expected_source_fingerprints(self, key: str) -> list[SourceFingerprint]:
        """返回当前 build spec 所声明来源的完整确定性指纹。"""

        fingerprints: list[SourceFingerprint] = []
        for (role, episode), path in self._expected_sources(key).items():
            if not path.exists():
                raise ValueError(f"直接来源缺失，不能接受: {role}@{episode}")
            fingerprints.append(
                SourceFingerprint(
                    role=role,
                    episode=episode,
                    sha256=file_sha256(path),
                )
            )
        return sorted(
            fingerprints,
            key=lambda item: (
                item.role,
                item.episode if item.episode is not None else -1,
            ),
        )

    def record_generation_baseline(self, key: str) -> bool:
        """在正常生成或重生成后尽力写入新的消费投影基线。"""

        slot = self.slots[key]
        try:
            generated = load_review_artifact(slot.path)
            current_fingerprints = self.expected_source_fingerprints(key)
            if not _fingerprints_equal(
                list(generated.direct_sources),
                current_fingerprints,
            ):
                raise ValueError("新生成产物的 direct_sources 与当前来源不一致")
            baseline = build_source_projection(
                self.resolved,
                key,
                current_fingerprints,
            )
            save_generation_baseline(slot.path, baseline)
            return True
        except (OSError, TypeError, ValueError, KeyError) as exc:
            self.baseline_warnings.append(
                f"{key}: 未写入自动比较基线：{type(exc).__name__}: {exc}"
            )
            return False

    def run_audit(self) -> WorldbookBuildReport:
        """在没有未保存草稿时运行只读世界书构建审计。"""

        dirty = self.dirty_keys()
        if dirty:
            raise ValueError(f"运行审计前必须保存草稿: {', '.join(dirty)}")
        return validate_worldbook_build(self.resolved.path)

    def evidence_context(
        self,
        scene_ids: set[str],
        utterance_ids: set[str],
        screen_text_ids: set[str],
    ) -> tuple[str, ...]:
        """从 build spec 的 Stage 2 Input 提取带证据标记的场景上下文。"""

        lines: list[str] = []
        for artifact in self._stage2_artifacts():
            for scene in artifact.scenes:
                contains_requested_evidence = any(
                    utterance.u_id in utterance_ids for utterance in scene.utterances
                ) or any(screen.s_id in screen_text_ids for screen in scene.screen_texts)
                if scene.scene_id not in scene_ids and not contains_requested_evidence:
                    continue
                lines.append(
                    f"场景 {scene.scene_id} · {scene.scene_start_text}–{scene.scene_end_text}"
                )
                if scene.scene_summary_hint:
                    lines.append(f"场景提示：{scene.scene_summary_hint}")
                for screen in scene.screen_texts:
                    marker = "★" if screen.s_id in screen_text_ids else "·"
                    lines.append(f"{marker} [{screen.s_id}] 屏幕字：{screen.text}")
                for utterance in scene.utterances:
                    marker = "★" if utterance.u_id in utterance_ids else "·"
                    speaker = utterance.speaker_name or "未知说话人"
                    text = utterance.zh_text or utterance.jp_text
                    lines.append(f"{marker} [{utterance.u_id}] {speaker}：{text}")
        return tuple(lines)

    def _stage2_artifacts(self) -> tuple[Stage2InputArtifact, ...]:
        """按集数延迟加载并缓存工作区引用的 Stage 2 Input。"""

        artifacts: list[Stage2InputArtifact] = []
        for episode, stage2_path, _, _, _ in self.resolved.episodes:
            if not stage2_path.exists():
                continue
            if episode.episode not in self._stage2_cache:
                self._stage2_cache[episode.episode] = load_stage2_input_artifact(stage2_path)
            artifacts.append(self._stage2_cache[episode.episode])
        return tuple(artifacts)

    def _assert_no_external_change(self, slot: ArtifactSlot) -> None:
        """禁止覆盖加载后被其他进程修改或新建的文件。"""

        exists_now = slot.path.exists()
        if slot.existed_when_loaded != exists_now:
            raise ValueError(f"审核文件存在状态已被外部修改: {slot.path}")
        if exists_now and slot.loaded_sha256 != file_sha256(slot.path):
            raise ValueError(f"审核文件已被外部修改，请重新加载: {slot.path}")

    def _bootstrap_fresh_baselines(self) -> None:
        """为来源仍 fresh 的生成产物尽力建立或更新本地消费投影基线。"""

        for slot in self.slots.values():
            self._bootstrap_slot_baseline(slot)

    def _bootstrap_slot_baseline(self, slot: ArtifactSlot) -> None:
        """为一个 fresh slot 尽力写入与当前生成结果匹配的投影基线。"""

        if slot.artifact is None or not self.freshness(slot.key).is_fresh:
            return
        status, log = load_source_acceptance_log(slot.path)
        if status in {"corrupt", "incompatible"}:
            self.baseline_warnings.append(
                f"{slot.key}: 来源接受 sidecar {status}，未自动覆盖"
            )
            return
        current_fingerprints = self.expected_source_fingerprints(slot.key)
        if status == "ready" and log is not None:
            if _fingerprints_equal(
                log.current_baseline.source_fingerprints,
                list(slot.artifact.direct_sources),
            ):
                return
            self.baseline_warnings.append(
                f"{slot.key}: 本地投影基线与 artifact 来源不一致，"
                "可能存在未完成确认或外部重生成；未自动覆盖"
            )
            return
        try:
            baseline = build_source_projection(
                self.resolved,
                slot.key,
                current_fingerprints,
            )
            save_generation_baseline(slot.path, baseline)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            self.baseline_warnings.append(
                f"{slot.key}: 未写入自动比较基线：{type(exc).__name__}: {exc}"
            )

    def _expected_sources(self, key: str) -> dict[tuple[str, int | None], Path]:
        """返回一个审核文件按逻辑角色声明的当前直接来源。"""

        if key.startswith("document:"):
            episode_number = int(key.split(":", 1)[1])
            episode = next(
                item for item in self.resolved.episodes if item[0].episode == episode_number
            )
            return {
                ("stage2_input", episode_number): episode[1],
                ("stage2a_annotation", episode_number): episode[2],
            }
        if key == "relation":
            return {
                (role, episode.episode): path
                for episode, stage2, stage2a, _, _ in self.resolved.episodes
                for role, path in (
                    ("stage2_input", stage2),
                    ("stage2a_annotation", stage2a),
                )
            }
        if key == "thought":
            return {
                (role, episode.episode): path
                for episode, stage2, _, stage2b, rag in self.resolved.episodes
                for role, path in (
                    ("stage2_input", stage2),
                    ("stage2b_annotation", stage2b),
                    ("stage3_rag", rag),
                )
            }
        if key == "lore_decisions":
            return {
                ("stage3_rag", episode.episode): rag
                for episode, _, _, _, rag in self.resolved.episodes
            }
        raise KeyError(f"未知审核文件: {key}")


def load_review_artifact(path: str | Path) -> ReviewArtifact:
    """按 artifact_type 加载严格审核 artifact。"""

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


def validate_review_artifact(artifact: ReviewArtifact) -> ReviewArtifact:
    """按具体 Pydantic 类型完整校验审核 artifact。"""

    validated: BaseModel = artifact.__class__.model_validate(artifact.model_dump(mode="python"))
    if isinstance(validated, Stage3DocumentReviewArtifact):
        return validated
    if isinstance(validated, Stage3RelationReviewArtifact):
        return validated
    if isinstance(validated, Stage3ThoughtReviewArtifact):
        return validated
    if isinstance(validated, Stage3LoreDecisionsArtifact):
        return validated
    raise TypeError("未知的审核 artifact 类型")


def _artifact_sources(artifact: ReviewArtifact) -> dict[tuple[str, int | None], SourceFingerprint]:
    """把 artifact 直接来源转换为可比较映射。"""

    return {(item.role, item.episode): item for item in artifact.direct_sources}


def _assert_same_revalidation_preview(
    expected: SourceRevalidationPreview,
    current: SourceRevalidationPreview,
) -> None:
    """确认来源、artifact、build spec 与投影在用户确认前没有变化。"""

    if expected.slot_key != current.slot_key:
        raise ValueError("来源确认目标已经变化，请重新预览")
    if expected.artifact_sha256 != current.artifact_sha256:
        raise ValueError("审核 artifact 在预览后发生变化，请重新预览")
    if expected.build_spec_sha256 != current.build_spec_sha256:
        raise ValueError("build spec 在预览后发生变化，请重新预览")
    if expected.new_source_fingerprints != current.new_source_fingerprints:
        raise ValueError("直接来源在预览后发生变化，请重新预览")
    if (
        expected.current_baseline.projection_sha256
        != current.current_baseline.projection_sha256
        or expected.baseline_status != current.baseline_status
    ):
        raise ValueError("消费投影或本地基线在预览后发生变化，请重新预览")


def _fingerprints_equal(
    first: list[SourceFingerprint],
    second: list[SourceFingerprint],
) -> bool:
    """忽略列表顺序比较两组逻辑来源指纹。"""

    first_values = sorted(
        (item.role, item.episode, item.sha256) for item in first
    )
    second_values = sorted(
        (item.role, item.episode, item.sha256) for item in second
    )
    return first_values == second_values


def _write_bytes_atomically(path: Path, payload: bytes) -> None:
    """使用同目录临时文件原子恢复一份原始字节内容。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".rollback",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
