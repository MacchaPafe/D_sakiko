"""测试 Stage3 lore 去重流程。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rag.pipeline.cli import main as pipeline_cli_main
from rag.pipeline.stage2_document_extraction import load_stage2_annotation_artifact
from rag.pipeline.stage2_input_builder import load_stage2_input_artifact
from rag.pipeline.stage3_lore_deduplicate import (
    apply_lore_dedup_decisions,
    build_canonical_lore_point_id,
    build_lore_dedup_review_from_directory,
)
from rag.pipeline.stage3_rag_import import (
    build_stage3_normalized_import_artifact,
    load_stage3_normalized_import_artifact,
    save_stage3_normalized_import_artifact,
)
from rag.pipeline.schemas import Stage3NormalizedImportArtifact


ROOT_DIR = Path(__file__).resolve().parents[2]
SAMPLE_STAGE2_INPUT_PATH = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json"
SAMPLE_PASS2_RAW_PATH = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json"


def _build_sample_stage3_artifact() -> Stage3NormalizedImportArtifact:
    """构造测试用的 stage3 artifact。"""

    input_artifact = load_stage2_input_artifact(SAMPLE_STAGE2_INPUT_PATH)
    annotation_artifact = load_stage2_annotation_artifact(SAMPLE_PASS2_RAW_PATH)
    return build_stage3_normalized_import_artifact(
        input_artifact=input_artifact,
        annotation_artifact=annotation_artifact,
    )


def _artifact_with_single_lore(
    artifact: Stage3NormalizedImportArtifact,
    lore_index: int,
) -> Stage3NormalizedImportArtifact:
    """复制 artifact，并只保留指定 lore 条目。"""

    copied_artifact = artifact.model_copy(deep=True)
    copied_artifact.story_events = []
    copied_artifact.character_relations = []
    copied_artifact.issues = []
    copied_artifact.lore_entries = [copied_artifact.lore_entries[lore_index]]
    return copied_artifact


class RagLoreDedupTest(unittest.TestCase):
    """测试 lore 去重模块与 CLI。"""

    def test_build_canonical_lore_point_id_escapes_delimiters(self) -> None:
        """canonical lore point id 应保留可读标题并转义分隔符。"""

        artifact = _build_sample_stage3_artifact()
        record = artifact.lore_entries[0].model_copy(deep=True)
        record.document.title = " A:B / 羽丘 "

        point_id = build_canonical_lore_point_id(record)

        self.assertEqual(point_id, "lore_entry:its_mygo:s3:main:A%3AB_%2F_羽丘")

    def test_build_review_detects_fuzzy_lore_title_candidates(self) -> None:
        """review 生成应能用 rapidfuzz 找出疑似重复标题。"""

        artifact = _build_sample_stage3_artifact()
        first_artifact = _artifact_with_single_lore(artifact, 0)
        second_artifact = _artifact_with_single_lore(artifact, 0)
        first_artifact.lore_entries[0].document.title = "RAS"
        second_artifact.lore_entries[0].document.title = "RAISE A SUILEN"

        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir)
            save_stage3_normalized_import_artifact(first_artifact, input_dir / "first.json")
            save_stage3_normalized_import_artifact(second_artifact, input_dir / "second.json")

            review = build_lore_dedup_review_from_directory(input_dir)

        self.assertEqual(len(review.fuzzy_groups), 1)
        self.assertEqual(review.fuzzy_groups[0].group_type, "fuzzy")

    def test_apply_lore_dedup_decisions_merges_identical_records(self) -> None:
        """应用自动决策后应保留主记录并合并证据链。"""

        artifact = _build_sample_stage3_artifact()
        first_artifact = _artifact_with_single_lore(artifact, 0)
        second_artifact = _artifact_with_single_lore(artifact, 0)
        first_artifact.lore_entries[0].evidence_u_ids = ["ep01_u0006"]
        second_artifact.lore_entries[0].evidence_u_ids = ["ep01_u0019"]

        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir)
            first_path = input_dir / "first.json"
            second_path = input_dir / "second.json"
            save_stage3_normalized_import_artifact(first_artifact, first_path)
            save_stage3_normalized_import_artifact(second_artifact, second_path)
            review = build_lore_dedup_review_from_directory(input_dir)

            deduped_artifacts = apply_lore_dedup_decisions(
                [(first_path, first_artifact), (second_path, second_artifact)],
                list(review.automatic_decisions),
            )

        total_lore_entries = sum(len(deduped_artifact.lore_entries) for _, deduped_artifact in deduped_artifacts)
        self.assertEqual(total_lore_entries, 1)
        self.assertEqual(
            deduped_artifacts[0][1].lore_entries[0].evidence_u_ids,
            ["ep01_u0006", "ep01_u0019"],
        )

    def test_cli_build_and_apply_lore_dedup(self) -> None:
        """CLI 应能生成 review/decisions 并输出 deduped stage3 目录。"""

        artifact = _build_sample_stage3_artifact()
        first_artifact = _artifact_with_single_lore(artifact, 0)
        second_artifact = _artifact_with_single_lore(artifact, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "output"
            review_path = temp_path / "lore_dedup_review.json"
            decisions_path = temp_path / "lore_dedup_decisions.json"
            input_dir.mkdir()
            save_stage3_normalized_import_artifact(first_artifact, input_dir / "first.json")
            save_stage3_normalized_import_artifact(second_artifact, input_dir / "second.json")

            review_exit_code = pipeline_cli_main(
                [
                    "build-lore-dedup-review",
                    "--input-dir",
                    str(input_dir),
                    "--review-output",
                    str(review_path),
                    "--decisions-output",
                    str(decisions_path),
                ]
            )
            apply_exit_code = pipeline_cli_main(
                [
                    "apply-lore-dedup-decisions",
                    "--input-dir",
                    str(input_dir),
                    "--decisions",
                    str(decisions_path),
                    "--output-dir",
                    str(output_dir),
                ]
            )

            self.assertEqual(review_exit_code, 0)
            self.assertEqual(apply_exit_code, 0)
            self.assertTrue(review_path.exists())
            self.assertTrue(decisions_path.exists())
            first_output = load_stage3_normalized_import_artifact(output_dir / "first.json")
            second_output = load_stage3_normalized_import_artifact(output_dir / "second.json")

        self.assertEqual(len(first_output.lore_entries), 1)
        self.assertEqual(len(second_output.lore_entries), 0)


if __name__ == "__main__":
    unittest.main()
