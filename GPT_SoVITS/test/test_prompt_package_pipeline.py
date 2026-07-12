"""Prompt Package 离线标注流水线的集成测试。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from rag.pipeline.llm_client import LiteLLMConfig
from rag.pipeline.prompt_package import (
    PromptPackageError,
    load_prompt_package_manifest,
    read_prompt_package_responses,
)
from rag.pipeline.prompt_package_litellm import complete_prompt_package_with_litellm
from rag.pipeline.cli import _normalize_cli_story_year, main as pipeline_cli_main
from rag.pipeline.stage1_speaker_annotation import (
    assemble_stage1_prompt_package,
    load_stage1_annotation_artifact,
    load_stage1_prepared_artifact,
    prepare_stage1_prompt_package,
)
from rag.pipeline.stage2_document_extraction import (
    assemble_stage2_prompt_package,
    load_stage2_annotation_artifact,
    prepare_stage2_prompt_package,
)
from rag.pipeline.stage2_input_builder import load_stage2_input_artifact
from rag.pipeline.stage2b_thought_extraction import (
    assemble_stage2b_prompt_package,
    load_stage2b_annotation_artifact,
    prepare_stage2b_prompt_package,
)
from rag.pipeline.stage3_rag_import import load_stage3_normalized_import_artifact
from rag.pipeline.stage3_relation_aggregation import (
    assemble_stage3_relation_prompt_package,
    prepare_stage3_relation_prompt_package,
)
from rag.pipeline.stage3_thought_pipeline import (
    assemble_stage3_thought_link_prompt_package,
    prepare_stage3_thought_link_prompt_package,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
STAGE1_PREPARED = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_prepared.json"
STAGE1_ANNOTATION = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_pass1_raw.json"
STAGE2_INPUT = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json"
STAGE2_ANNOTATION = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json"
STAGE2B_ANNOTATION = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2b_raw.json"
STAGE3_RAG = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_rag_ready.json"


class PromptPackagePipelineTest(unittest.TestCase):
    """验证五个 LLM 阶段的静态 Prompt Package seam。"""

    def test_cli_story_year_normalizes_non_positive_values(self) -> None:
        """CLI 应把非正剧情学年转换为未知，同时保留正整数。"""

        self.assertIsNone(_normalize_cli_story_year(0))
        self.assertIsNone(_normalize_cli_story_year(-2))
        self.assertEqual(_normalize_cli_story_year(1), 1)
        self.assertEqual(_normalize_cli_story_year(3), 3)

    def test_stage1_roundtrip_from_offline_response(self) -> None:
        """Stage 1 离线 response 应还原既有场景标注。"""

        prepared = load_stage1_prepared_artifact(STAGE1_PREPARED)
        existing = load_stage1_annotation_artifact(STAGE1_ANNOTATION)
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = prepare_stage1_prompt_package(
                prepared,
                STAGE1_PREPARED,
                temp_dir,
                max_scenes=1,
            )
            annotation = existing.results[0].annotation
            assert annotation is not None
            self._write_task_response(temp_dir, manifest.tasks[0].response_file, annotation.model_dump(mode="json"))
            assembled = assemble_stage1_prompt_package(Path(temp_dir) / "manifest.json")
        self.assertEqual(assembled.results[0].annotation, annotation)
        self.assertEqual(assembled.model, "codex-workspace")

    def test_stage2_roundtrip_and_stale_prompt_detection(self) -> None:
        """Stage 2A 应可离线组装，并拒绝被修改的 Prompt。"""

        input_artifact = load_stage2_input_artifact(STAGE2_INPUT)
        existing = load_stage2_annotation_artifact(STAGE2_ANNOTATION)
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = prepare_stage2_prompt_package(
                input_artifact,
                STAGE2_INPUT,
                temp_dir,
                max_scenes=1,
            )
            annotation = existing.results[0].annotation
            assert annotation is not None
            self._write_task_response(temp_dir, manifest.tasks[0].response_file, annotation.model_dump(mode="json"))
            assembled = assemble_stage2_prompt_package(Path(temp_dir) / "manifest.json")
            self.assertEqual(assembled.results[0].annotation, annotation)
            prompt_path = Path(temp_dir) / manifest.tasks[0].prompt_file
            prompt_path.write_text(prompt_path.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
            with self.assertRaises(PromptPackageError):
                read_prompt_package_responses(Path(temp_dir) / "manifest.json")

    def test_render_refuses_to_reuse_existing_package_directory(self) -> None:
        """重复渲染不得让旧 response 静默匹配新的 Prompt。"""

        input_artifact = load_stage2_input_artifact(STAGE2_INPUT)
        with tempfile.TemporaryDirectory() as temp_dir:
            prepare_stage2_prompt_package(
                input_artifact,
                STAGE2_INPUT,
                temp_dir,
                max_scenes=1,
            )
            with self.assertRaises(PromptPackageError):
                prepare_stage2_prompt_package(
                    input_artifact,
                    STAGE2_INPUT,
                    temp_dir,
                    max_scenes=1,
                )

    def test_stage2_offline_cli_roundtrip(self) -> None:
        """Stage 2A 离线 CLI 应完成渲染和组装两个独立命令。"""

        existing = load_stage2_annotation_artifact(STAGE2_ANNOTATION)
        annotation = existing.results[0].annotation
        assert annotation is not None
        with tempfile.TemporaryDirectory() as temp_dir:
            package_dir = Path(temp_dir) / "package"
            output_path = Path(temp_dir) / "assembled.json"
            render_code = pipeline_cli_main(
                [
                    "render-stage2-prompts",
                    "--input",
                    str(STAGE2_INPUT),
                    "--output-dir",
                    str(package_dir),
                    "--max-scenes",
                    "1",
                ]
            )
            manifest = load_prompt_package_manifest(package_dir / "manifest.json")
            self._write_task_response(
                package_dir,
                manifest.tasks[0].response_file,
                annotation.model_dump(mode="json"),
            )
            assemble_code = pipeline_cli_main(
                [
                    "assemble-stage2-responses",
                    "--manifest",
                    str(package_dir / "manifest.json"),
                    "--output",
                    str(output_path),
                    "--model-label",
                    "codex-test",
                ]
            )
            assembled = load_stage2_annotation_artifact(output_path)
        self.assertEqual(render_code, 0)
        self.assertEqual(assemble_code, 0)
        self.assertEqual(assembled.model, "codex-test")
        self.assertEqual(assembled.results[0].annotation, annotation)

    def test_litellm_completion_writes_raw_response_and_skips_existing(self) -> None:
        """通用请求阶段应写入原始回复，并默认跳过已有结果。"""

        input_artifact = load_stage2_input_artifact(STAGE2_INPUT)
        raw_response = '{"scene_id":"scene_001","story_events":[],"character_relations":[],"lore_entries":[],"relation_observations":[]}'
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = prepare_stage2_prompt_package(
                input_artifact,
                STAGE2_INPUT,
                temp_dir,
                max_scenes=1,
            )
            manifest_path = Path(temp_dir) / "manifest.json"
            with patch(
                "rag.pipeline.prompt_package_litellm.LiteLLMJsonClient.complete_text",
                return_value=raw_response,
            ) as complete_text:
                first_report = complete_prompt_package_with_litellm(
                    manifest_path,
                    LiteLLMConfig(model="test/model"),
                )
                second_report = complete_prompt_package_with_litellm(
                    manifest_path,
                    LiteLLMConfig(model="test/model"),
                )
            response_path = Path(temp_dir) / manifest.tasks[0].response_file
            saved_response = response_path.read_text(encoding="utf-8")
        self.assertEqual(first_report.completed_task_ids, (manifest.tasks[0].task_id,))
        self.assertEqual(second_report.skipped_task_ids, (manifest.tasks[0].task_id,))
        self.assertEqual(saved_response, raw_response)
        complete_text.assert_called_once()

    def test_stage2b_roundtrip_from_offline_response(self) -> None:
        """Stage 2B 单窗口离线 response 应复用现有合并逻辑。"""

        input_artifact = load_stage2_input_artifact(STAGE2_INPUT)
        stage2a_artifact = load_stage2_annotation_artifact(STAGE2_ANNOTATION)
        existing = load_stage2b_annotation_artifact(STAGE2B_ANNOTATION)
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = prepare_stage2b_prompt_package(
                input_artifact,
                stage2a_artifact,
                STAGE2_INPUT,
                STAGE2_ANNOTATION,
                temp_dir,
                max_scenes=1,
            )
            annotation = existing.results[0].annotation
            assert annotation is not None
            self.assertEqual(len(manifest.tasks), 1)
            self._write_task_response(temp_dir, manifest.tasks[0].response_file, annotation.model_dump(mode="json"))
            assembled = assemble_stage2b_prompt_package(Path(temp_dir) / "manifest.json")
        self.assertEqual(assembled.results[0].annotation, annotation)

    def test_relation_package_can_assemble_partial_codex_results(self) -> None:
        """关系聚合应允许试标单个角色对并保留其他缺失任务问题。"""

        input_artifact = load_stage2_input_artifact(STAGE2_INPUT)
        annotation_artifact = load_stage2_annotation_artifact(STAGE2_ANNOTATION)
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = prepare_stage3_relation_prompt_package(
                [input_artifact],
                [annotation_artifact],
                [STAGE2_INPUT],
                [STAGE2_ANNOTATION],
                temp_dir,
            )
            self.assertGreater(len(manifest.tasks), 1)
            task = manifest.tasks[0]
            prompt = (Path(temp_dir) / task.prompt_file).read_text(encoding="utf-8")
            observation_ids = list(
                dict.fromkeys(re.findall(r"relation_observation:[0-9a-f]+", prompt))
            )
            self.assertTrue(observation_ids)
            response = {
                "subject_character_name": task.context["subject_character_name"],
                "object_character_name": task.context["object_character_name"],
                "states": [
                    {
                        "relation_type_key": "general_bond",
                        "summary": "当前关系保持稳定。",
                        "speech_hint": "",
                        "object_character_nickname": "",
                        "supporting_observation_ids": observation_ids,
                        "confidence": 0.8,
                        "ambiguity_notes": "",
                    }
                ],
                "unmerged_observations": [],
            }
            self._write_task_response(temp_dir, task.response_file, response)
            assembled = assemble_stage3_relation_prompt_package(
                Path(temp_dir) / "manifest.json",
                allow_partial=True,
            )
        self.assertEqual(assembled.aggregation_model, "codex-workspace")
        self.assertGreaterEqual(len(assembled.character_relation_states), 1)
        self.assertTrue(any("缺少关系聚合 response" in issue.message for issue in assembled.issues))

    def test_thought_link_package_assembles_standalone_decisions(self) -> None:
        """Stage 3 Thought Link 应只处理 unresolved，并接受受限 standalone 决策。"""

        input_artifact = load_stage2_input_artifact(STAGE2_INPUT)
        stage2b_artifact = load_stage2b_annotation_artifact(STAGE2B_ANNOTATION)
        stage3_rag_artifact = load_stage3_normalized_import_artifact(STAGE3_RAG)
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = prepare_stage3_thought_link_prompt_package(
                input_artifact,
                stage2b_artifact,
                stage3_rag_artifact,
                STAGE2_INPUT,
                STAGE2B_ANNOTATION,
                STAGE3_RAG,
                temp_dir,
            )
            for task in manifest.tasks:
                self._write_task_response(
                    temp_dir,
                    task.response_file,
                    {
                        "source_local_id": task.context["source_local_id"],
                        "link_status": "standalone",
                        "target_kind": "standalone_topic",
                        "target_id": None,
                        "thought_aspect": "general_view",
                        "link_confidence": 0.8,
                        "reason_brief": "当前观点不依赖某个可识别的事件事实。",
                    },
                )
            assembled = assemble_stage3_thought_link_prompt_package(
                Path(temp_dir) / "manifest.json"
            )
        self.assertEqual(assembled.linker_model, "codex-workspace")
        self.assertFalse(any("response" in issue.message for issue in assembled.issues))

    @staticmethod
    def _write_task_response(
        package_dir: str | Path,
        relative_path: str,
        payload: dict[str, object],
    ) -> None:
        """把测试 JSON 写入任务声明的 response 位置。"""

        path = Path(package_dir) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
