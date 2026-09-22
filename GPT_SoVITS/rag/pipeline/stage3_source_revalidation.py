"""计算 Stage 3 下游消费投影并保存本地来源重新确认记录。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from rag.worldbook.builder import ResolvedBuildSpec

from .review_migration import canonical_json_sha256, safely_write_json_model
from .review_models import SourceFingerprint
from .stage2_document_extraction import load_stage2_annotation_artifact
from .stage2_input_builder import load_stage2_input_artifact
from .stage2b_thought_extraction import load_stage2b_annotation_artifact
from .stage3_document_review import load_stage3_document_review_artifact
from .stage3_lore_models import Stage3LoreDecisionsArtifact
from .stage3_relation_aggregation import _collect_observation_sources
from .stage3_relation_models import Stage3RelationReviewArtifact
from .stage3_thought_aggregation import _collect_updates, _thought_prompt_story_events
from .stage3_thought_models import Stage3ThoughtReviewArtifact


ProjectionKind = Literal[
    "document_inputs",
    "relation_inputs",
    "thought_inputs",
    "lore_decision_inputs",
]
AcceptanceMode = Literal["automatic", "manual"]
BaselineStatus = Literal["ready", "missing", "corrupt", "incompatible"]

SOURCE_ACCEPTANCE_SUFFIX = ".source-acceptances.json"
SOURCE_ACCEPTANCE_FORMAT_VERSION = 0
PROJECTION_VERSION = 1
DIFF_PREVIEW_LIMIT = 500


class SourceProjectionBaseline(BaseModel):
    """保存一个审核产物当前采用的完整规范化消费投影。"""

    model_config = ConfigDict(extra="forbid")

    projection_kind: ProjectionKind
    projection_version: int = PROJECTION_VERSION
    projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection: dict[str, object]
    source_fingerprints: list[SourceFingerprint]
    recorded_at: str


class ProjectionDifference(BaseModel):
    """描述消费投影中的一项新增、删除或修改。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    change: Literal["added", "removed", "modified"]
    before_summary: str | None = None
    after_summary: str | None = None


class SourceAcceptanceRecord(BaseModel):
    """记录一次自动验证或人工覆盖来源变化的决定。"""

    model_config = ConfigDict(extra="forbid")

    accepted_at: str
    mode: AcceptanceMode
    reason: str | None = None
    old_source_fingerprints: list[SourceFingerprint]
    new_source_fingerprints: list[SourceFingerprint]
    old_projection_sha256: str | None = None
    new_projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    differences: list[ProjectionDifference] = Field(default_factory=list)


class SourceAcceptanceLog(BaseModel):
    """保存一个审核 artifact 的本地投影基线和接受历史。"""

    model_config = ConfigDict(extra="forbid")

    format_version: int = SOURCE_ACCEPTANCE_FORMAT_VERSION
    artifact_path: str
    current_baseline: SourceProjectionBaseline
    acceptances: list[SourceAcceptanceRecord] = Field(default_factory=list)


class SourceRevalidationPreview(BaseModel):
    """保存一次来源重新确认预览及并发写入校验依据。"""

    model_config = ConfigDict(extra="forbid")

    slot_key: str
    artifact_path: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stale_sources: list[str]
    baseline_status: BaselineStatus
    projection_equal: bool
    can_accept_automatically: bool
    old_source_fingerprints: list[SourceFingerprint]
    new_source_fingerprints: list[SourceFingerprint]
    current_baseline: SourceProjectionBaseline
    differences: list[ProjectionDifference] = Field(default_factory=list)


def source_acceptance_path(artifact_path: str | Path) -> Path:
    """返回审核 artifact 对应的本地来源接受 sidecar 路径。"""

    resolved = Path(artifact_path)
    return Path(f"{resolved}{SOURCE_ACCEPTANCE_SUFFIX}")


def current_timestamp() -> str:
    """返回适合持久化审计记录的 UTC ISO 时间。"""

    return datetime.now(timezone.utc).isoformat()


def build_source_projection(
    resolved: ResolvedBuildSpec,
    slot_key: str,
    source_fingerprints: list[SourceFingerprint],
) -> SourceProjectionBaseline:
    """按审核类型构建与真实下游消费逻辑共享的规范化投影。"""

    if slot_key.startswith("document:"):
        projection_kind: ProjectionKind = "document_inputs"
        episode_number = int(slot_key.split(":", 1)[1])
        episode = next(
            item for item in resolved.episodes if item[0].episode == episode_number
        )
        projection = {
            "stage2_input": load_stage2_input_artifact(episode[1]).model_dump(mode="json"),
            "stage2a_annotation": load_stage2_annotation_artifact(episode[2]).model_dump(
                mode="json"
            ),
        }
    elif slot_key == "relation":
        projection_kind = "relation_inputs"
        input_artifacts = [
            load_stage2_input_artifact(item[1]) for item in resolved.episodes
        ]
        annotation_artifacts = [
            load_stage2_annotation_artifact(item[2]) for item in resolved.episodes
        ]
        issues = []
        sources = _collect_observation_sources(
            input_artifacts,
            annotation_artifacts,
            issues,
        )
        projection = {
            "observations": [
                source.record.model_dump(mode="json") for source in sources
            ],
            "issues": [issue.model_dump(mode="json") for issue in issues],
        }
    elif slot_key == "thought":
        projection_kind = "thought_inputs"
        input_artifacts = [
            load_stage2_input_artifact(item[1]) for item in resolved.episodes
        ]
        stage2b_artifacts = [
            load_stage2b_annotation_artifact(item[3]) for item in resolved.episodes
        ]
        rag_artifacts = [
            load_stage3_document_review_artifact(item[4]) for item in resolved.episodes
        ]
        updates, event_facts, issues = _collect_updates(
            input_artifacts,
            stage2b_artifacts,
            rag_artifacts,
        )
        projection = {
            "updates": [item.model_dump(mode="json") for item in updates],
            "story_events": _thought_prompt_story_events(rag_artifacts),
            "event_facts": event_facts,
            "issues": [issue.model_dump(mode="json") for issue in issues],
        }
    elif slot_key == "lore_decisions":
        projection_kind = "lore_decision_inputs"
        rag_artifacts = [
            load_stage3_document_review_artifact(item[4]) for item in resolved.episodes
        ]
        projection = {
            "published_lore": [
                {
                    "candidate_id": record.candidate_id,
                    "document": record.effective_document().model_dump(mode="json"),
                }
                for artifact in rag_artifacts
                for record in artifact.lore_entries
                if record.disposition == "publish"
            ]
        }
    else:
        raise KeyError(f"未知审核文件: {slot_key}")
    projection_sha256 = canonical_json_sha256(projection)
    return SourceProjectionBaseline(
        projection_kind=projection_kind,
        projection_sha256=projection_sha256,
        projection=projection,
        source_fingerprints=sorted(
            source_fingerprints,
            key=lambda item: (item.role, item.episode if item.episode is not None else -1),
        ),
        recorded_at=current_timestamp(),
    )


def projection_differences(
    previous: dict[str, object],
    current: dict[str, object],
) -> list[ProjectionDifference]:
    """递归计算两份规范化消费投影的完整字段差异摘要。"""

    differences: list[ProjectionDifference] = []
    _append_projection_differences(previous, current, "$", differences)
    return differences


def load_source_acceptance_log(
    artifact_path: str | Path,
) -> tuple[BaselineStatus, SourceAcceptanceLog | None]:
    """读取本地 sidecar，并把缺失、损坏和版本不兼容显式区分。"""

    path = source_acceptance_path(artifact_path)
    if not path.exists():
        return "missing", None
    try:
        log = SourceAcceptanceLog.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "corrupt", None
    baseline = log.current_baseline
    if (
        baseline.projection_version != PROJECTION_VERSION
        or log.format_version != SOURCE_ACCEPTANCE_FORMAT_VERSION
    ):
        return "incompatible", log
    return "ready", log


def save_generation_baseline(
    artifact_path: str | Path,
    baseline: SourceProjectionBaseline,
) -> Path:
    """为 fresh 产物补写当前基线，并保留已有人工接受历史。"""

    path = source_acceptance_path(artifact_path)
    _, existing = load_source_acceptance_log(artifact_path)
    log = SourceAcceptanceLog(
        artifact_path=str(Path(artifact_path).resolve()),
        current_baseline=baseline,
        acceptances=[] if existing is None else existing.acceptances,
    )
    safely_write_json_model(log, path)
    return path


def build_acceptance_log(
    artifact_path: str | Path,
    preview: SourceRevalidationPreview,
    *,
    mode: AcceptanceMode,
    reason: str | None,
) -> SourceAcceptanceLog:
    """根据确认预览构造新的本地基线和完整接受记录。"""

    _, existing = load_source_acceptance_log(artifact_path)
    history = [] if existing is None else list(existing.acceptances)
    history.append(
        SourceAcceptanceRecord(
            accepted_at=current_timestamp(),
            mode=mode,
            reason=reason.strip() if reason is not None else None,
            old_source_fingerprints=preview.old_source_fingerprints,
            new_source_fingerprints=preview.new_source_fingerprints,
            old_projection_sha256=(
                None
                if preview.baseline_status != "ready"
                else (
                    existing.current_baseline.projection_sha256
                    if existing is not None
                    else None
                )
            ),
            new_projection_sha256=preview.current_baseline.projection_sha256,
            differences=preview.differences,
        )
    )
    return SourceAcceptanceLog(
        artifact_path=str(Path(artifact_path).resolve()),
        current_baseline=preview.current_baseline,
        acceptances=history,
    )


def validate_current_references(
    resolved: ResolvedBuildSpec,
    slot_key: str,
    artifact: (
        Stage3RelationReviewArtifact
        | Stage3ThoughtReviewArtifact
        | Stage3LoreDecisionsArtifact
        | BaseModel
    ),
) -> None:
    """拒绝人工来源确认掩盖 episode 范围或直接候选引用错误。"""

    expected_episodes = sorted(item[0].episode for item in resolved.episodes)
    if slot_key.startswith("document:"):
        episode_number = int(slot_key.split(":", 1)[1])
        metadata = getattr(artifact, "metadata", None)
        if metadata is None or getattr(metadata, "episode", None) != episode_number:
            raise ValueError("Story/Lore Review 的 episode 与 build spec 不一致")
        return
    if slot_key == "relation":
        if not isinstance(artifact, Stage3RelationReviewArtifact):
            raise TypeError("Relation slot 的 artifact 类型错误")
        if sorted(artifact.metadata.episodes) != expected_episodes:
            raise ValueError("Relation Review 的 episode coverage 不完整")
        return
    rag_artifacts = [
        load_stage3_document_review_artifact(item[4]) for item in resolved.episodes
    ]
    if slot_key == "thought":
        if not isinstance(artifact, Stage3ThoughtReviewArtifact):
            raise TypeError("Thought slot 的 artifact 类型错误")
        if sorted(artifact.metadata.episodes) != expected_episodes:
            raise ValueError("Thought Review 的 episode coverage 不完整")
        published_story_ids = {
            record.candidate_id
            for rag_artifact in rag_artifacts
            for record in rag_artifact.story_events
            if record.disposition == "publish"
        }
        dangling = sorted(
            {
                candidate_id
                for thread in artifact.threads
                if thread.disposition == "publish"
                for state in thread.effective_sequence()
                for candidate_id in state.story_event_candidate_ids
                if candidate_id not in published_story_ids
            }
        )
        if dangling:
            raise ValueError(f"Thought 引用了未发布的 Story Event: {dangling}")
        return
    if slot_key == "lore_decisions":
        if not isinstance(artifact, Stage3LoreDecisionsArtifact):
            raise TypeError("Lore decisions slot 的 artifact 类型错误")
        if sorted(artifact.episodes) != expected_episodes:
            raise ValueError("Lore decisions 的 episode coverage 不完整")
        published_lore_ids = {
            record.candidate_id
            for rag_artifact in rag_artifacts
            for record in rag_artifact.lore_entries
            if record.disposition == "publish"
        }
        unknown = sorted(
            {
                candidate_id
                for decision in artifact.decisions
                for candidate_id in decision.candidate_ids
                if candidate_id not in published_lore_ids
            }
        )
        if unknown:
            raise ValueError(f"Lore decision 引用了不存在或未发布的候选: {unknown}")
        return
    raise KeyError(f"未知审核文件: {slot_key}")


def _append_projection_differences(
    previous: object,
    current: object,
    path: str,
    differences: list[ProjectionDifference],
) -> None:
    """递归追加投影差异，并保留确定性的路径顺序。"""

    if isinstance(previous, dict) and isinstance(current, dict):
        previous_mapping = {str(key): value for key, value in previous.items()}
        current_mapping = {str(key): value for key, value in current.items()}
        for key in sorted(previous_mapping.keys() | current_mapping.keys()):
            child_path = f"{path}.{key}"
            if key not in previous_mapping:
                differences.append(
                    ProjectionDifference(
                        path=child_path,
                        change="added",
                        after_summary=_summarize_value(current_mapping[key]),
                    )
                )
            elif key not in current_mapping:
                differences.append(
                    ProjectionDifference(
                        path=child_path,
                        change="removed",
                        before_summary=_summarize_value(previous_mapping[key]),
                    )
                )
            else:
                _append_projection_differences(
                    previous_mapping[key],
                    current_mapping[key],
                    child_path,
                    differences,
                )
        return
    if isinstance(previous, list) and isinstance(current, list):
        for index in range(max(len(previous), len(current))):
            child_path = f"{path}[{index}]"
            if index >= len(previous):
                differences.append(
                    ProjectionDifference(
                        path=child_path,
                        change="added",
                        after_summary=_summarize_value(current[index]),
                    )
                )
            elif index >= len(current):
                differences.append(
                    ProjectionDifference(
                        path=child_path,
                        change="removed",
                        before_summary=_summarize_value(previous[index]),
                    )
                )
            else:
                _append_projection_differences(
                    previous[index],
                    current[index],
                    child_path,
                    differences,
                )
        return
    if previous != current:
        differences.append(
            ProjectionDifference(
                path=path,
                change="modified",
                before_summary=_summarize_value(previous),
                after_summary=_summarize_value(current),
            )
        )


def _summarize_value(value: object) -> str:
    """把差异值压缩成适合界面和本地历史的有限长度文本。"""

    rendered = repr(value)
    if len(rendered) <= DIFF_PREVIEW_LIMIT:
        return rendered
    return f"{rendered[:DIFF_PREVIEW_LIMIT]}…"
