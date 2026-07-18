"""为 Stage 3 审核工作台提供显式、可调用的重生成步骤。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from rag.worldbook.builder import ResolvedBuildSpec, load_build_spec

from .prompt_package import PromptPackageManifest, load_prompt_package_manifest
from .review_migration import build_source_fingerprint
from .review_models import ReviewMigrationReport
from .stage2_document_extraction import load_stage2_annotation_artifact
from .stage2_input_builder import load_stage2_input_artifact
from .stage3_document_review import normalize_stage3_documents_from_files
from .stage3_lore_review import build_stage3_lore_decisions, save_stage3_lore_decisions
from .stage3_relation_aggregation import (
    DEFAULT_TEMPLATE_PATH as DEFAULT_RELATION_TEMPLATE_PATH,
    assemble_stage3_relation_prompt_package,
    prepare_stage3_relation_prompt_package,
)
from .stage3_relation_review import (
    build_stage3_relation_review_artifact,
    load_stage3_relation_review_artifact,
    save_stage3_relation_review_artifact,
)
from .stage3_thought_aggregation import (
    DEFAULT_TEMPLATE_PATH as DEFAULT_THOUGHT_TEMPLATE_PATH,
    assemble_stage3_thought_prompt_package,
    load_stage3_thought_review_artifact,
    prepare_stage3_thought_prompt_package,
    save_stage3_thought_review_artifact,
)
from .stage2b_thought_extraction import load_stage2b_annotation_artifact
from .stage3_document_review import load_stage3_document_review_artifact


@dataclass(frozen=True, slots=True)
class RegenerationResult:
    """描述一次已完成重生成步骤的输出与迁移摘要。"""

    slot_key: str
    phase: str
    output_path: Path
    task_count: int | None = None
    migration_report: ReviewMigrationReport | None = None

    def summary(self) -> str:
        """返回适合界面展示的简短中文摘要。"""

        if self.migration_report is not None:
            counts = self.migration_report.counts
            return (
                f"保留审核 {counts.migrated_reviews}，重置 {counts.reset_reviews}，"
                f"新增 {counts.added_candidates}，消失 {counts.disappeared_candidates}"
            )
        if self.task_count is not None:
            return f"已生成 {self.task_count} 个 Prompt 任务"
        return f"已写入 {self.output_path}"


class Stage3ReviewRegenerator:
    """按 build spec 执行文档、长期数据和 Lore 决策重生成。"""

    def __init__(self, build_spec_path: str | Path) -> None:
        """加载并保存重生成所需的解析后构建配置。"""

        self.resolved: ResolvedBuildSpec = load_build_spec(build_spec_path)

    def regenerate_document(
        self,
        episode_number: int,
        allowed_removed_ids: set[str] | None = None,
        allow_all_removed: bool = False,
    ) -> RegenerationResult:
        """重新生成一集 Story/Lore Review 并迁移可复用审核。"""

        episode = next(
            (
                value
                for value in self.resolved.episodes
                if value[0].episode == episode_number
            ),
            None,
        )
        if episode is None:
            raise KeyError(f"build spec 不包含第 {episode_number} 集")
        _, stage2_path, stage2a_path, _, output_path = episode
        _, report = normalize_stage3_documents_from_files(
            input_path=stage2_path,
            annotation_path=stage2a_path,
            output_path=output_path,
            allowed_removed_ids=allowed_removed_ids,
            allow_all_removed=allow_all_removed,
        )
        return RegenerationResult(
            slot_key=f"document:{episode_number}",
            phase="regenerate",
            output_path=output_path,
            migration_report=report,
        )

    def rebuild_lore_decisions(self) -> RegenerationResult:
        """从全部已审核 Story/Lore Review 重新扫描 Lore 重复组。"""

        rag_paths = [value[4] for value in self.resolved.episodes]
        previous_path = (
            self.resolved.lore_decisions
            if self.resolved.lore_decisions.exists()
            else None
        )
        artifact = build_stage3_lore_decisions(rag_paths, previous_path=previous_path)
        save_stage3_lore_decisions(artifact, self.resolved.lore_decisions)
        return RegenerationResult(
            slot_key="lore_decisions",
            phase="regenerate",
            output_path=self.resolved.lore_decisions,
            task_count=len(artifact.decisions),
        )

    def render_relation_prompts(
        self,
        output_dir: str | Path,
        template_path: str | Path = DEFAULT_RELATION_TEMPLATE_PATH,
    ) -> RegenerationResult:
        """为 build spec 的全部有向角色对渲染 Relation Prompt Package。"""

        input_paths = [value[1] for value in self.resolved.episodes]
        annotation_paths = [value[2] for value in self.resolved.episodes]
        manifest = prepare_stage3_relation_prompt_package(
            input_artifacts=[load_stage2_input_artifact(path) for path in input_paths],
            annotation_artifacts=[
                load_stage2_annotation_artifact(path) for path in annotation_paths
            ],
            input_paths=input_paths,
            annotation_paths=annotation_paths,
            output_dir=output_dir,
            template_path=template_path,
        )
        return _prompt_result("relation", output_dir, manifest)

    def assemble_relation_prompts(
        self,
        manifest_path: str | Path,
        model_label: str = "codex-workspace",
        allow_partial: bool = False,
        allow_stale: bool = False,
        allowed_removed_ids: set[str] | None = None,
        allow_all_removed: bool = False,
    ) -> RegenerationResult:
        """校验 Relation responses、组装 Review 并迁移旧审核。"""

        legacy = assemble_stage3_relation_prompt_package(
            manifest_path=manifest_path,
            model_label=model_label,
            allow_partial=allow_partial,
            allow_stale=allow_stale,
        )
        manifest = load_prompt_package_manifest(manifest_path)
        input_paths = _manifest_paths(manifest, "input_paths")
        annotation_paths = _manifest_paths(manifest, "annotation_paths")
        if len(input_paths) != len(annotation_paths):
            raise ValueError("Relation manifest 的 input_paths 与 annotation_paths 数量不一致")
        direct_sources = [
            fingerprint
            for input_path, annotation_path in zip(input_paths, annotation_paths)
            for fingerprint in (
                build_source_fingerprint(
                    "stage2_input",
                    input_path,
                    load_stage2_input_artifact(input_path).metadata.episode,
                ),
                build_source_fingerprint(
                    "stage2a_annotation",
                    annotation_path,
                    load_stage2_input_artifact(input_path).metadata.episode,
                ),
            )
        ]
        previous = (
            load_stage3_relation_review_artifact(self.resolved.relation_review)
            if self.resolved.relation_review.exists()
            else None
        )
        artifact, report = build_stage3_relation_review_artifact(
            legacy,
            direct_sources,
            previous=previous,
            output_path=str(self.resolved.relation_review),
            allowed_removed_ids=allowed_removed_ids,
            allow_all_removed=allow_all_removed,
        )
        save_stage3_relation_review_artifact(
            artifact,
            report,
            self.resolved.relation_review,
        )
        return RegenerationResult(
            slot_key="relation",
            phase="assemble",
            output_path=self.resolved.relation_review,
            migration_report=report,
        )

    def render_thought_prompts(
        self,
        output_dir: str | Path,
        template_path: str | Path = DEFAULT_THOUGHT_TEMPLATE_PATH,
    ) -> RegenerationResult:
        """为 build spec 的全部角色渲染跨集 Thought Prompt Package。"""

        input_paths = [value[1] for value in self.resolved.episodes]
        stage2b_paths = [value[3] for value in self.resolved.episodes]
        rag_paths = [value[4] for value in self.resolved.episodes]
        manifest = prepare_stage3_thought_prompt_package(
            input_artifacts=[load_stage2_input_artifact(path) for path in input_paths],
            stage2b_artifacts=[
                load_stage2b_annotation_artifact(path) for path in stage2b_paths
            ],
            rag_artifacts=[
                load_stage3_document_review_artifact(path) for path in rag_paths
            ],
            input_paths=input_paths,
            stage2b_paths=stage2b_paths,
            rag_paths=rag_paths,
            output_dir=output_dir,
            template_path=template_path,
        )
        return _prompt_result("thought", output_dir, manifest)

    def assemble_thought_prompts(
        self,
        manifest_path: str | Path,
        model_label: str = "codex-workspace",
        allow_partial: bool = False,
        allow_stale: bool = False,
        allowed_removed_ids: set[str] | None = None,
        allow_all_removed: bool = False,
    ) -> RegenerationResult:
        """校验 Thought responses、组装 Review 并迁移旧审核。"""

        previous = (
            load_stage3_thought_review_artifact(self.resolved.thought_review)
            if self.resolved.thought_review.exists()
            else None
        )
        artifact, report = assemble_stage3_thought_prompt_package(
            manifest_path=manifest_path,
            model_label=model_label,
            allow_partial=allow_partial,
            allow_stale=allow_stale,
            previous=previous,
            output_path=str(self.resolved.thought_review),
            allowed_removed_ids=allowed_removed_ids,
            allow_all_removed=allow_all_removed,
        )
        save_stage3_thought_review_artifact(
            artifact,
            report,
            self.resolved.thought_review,
        )
        return RegenerationResult(
            slot_key="thought",
            phase="assemble",
            output_path=self.resolved.thought_review,
            migration_report=report,
        )


def _prompt_result(
    slot_key: str,
    output_dir: str | Path,
    manifest: PromptPackageManifest,
) -> RegenerationResult:
    """把 Prompt Package manifest 投影为统一重生成结果。"""

    return RegenerationResult(
        slot_key=slot_key,
        phase="render",
        output_path=Path(output_dir).resolve() / "manifest.json",
        task_count=len(manifest.tasks),
    )


def _manifest_paths(
    manifest: PromptPackageManifest,
    parameter_name: str,
) -> list[str]:
    """从 Prompt Package 参数读取严格字符串路径数组。"""

    raw_value = manifest.parameters.get(parameter_name)
    if raw_value is None:
        raise ValueError(f"Prompt Package 缺少参数: {parameter_name}")
    payload = json.loads(raw_value)
    if not isinstance(payload, list) or not all(isinstance(value, str) for value in payload):
        raise ValueError(f"Prompt Package 参数不是字符串数组: {parameter_name}")
    return list(payload)
