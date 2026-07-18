"""Relation Type 级跨集审核产物测试。"""

from __future__ import annotations

from pathlib import Path
import unittest

from rag.pipeline.review_models import SourceFingerprint, complete_review
from rag.pipeline.stage3_relation_aggregation import load_stage3_relation_aggregation_artifact
from rag.pipeline.stage3_relation_review import build_stage3_relation_review_artifact


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PATH = PROJECT_ROOT / "GPT_SoVITS" / "rag" / "pipeline" / "data" / "annotations_stage3" / "ep01_relation_states_review_20260717.json"


class TestStage3RelationReview(unittest.TestCase):
    """验证旧 State 结果到 Relation Type 序列的转换和迁移。"""

    def setUp(self) -> None:
        """读取已有 Relation 聚合样例。"""

        self.legacy = load_stage3_relation_aggregation_artifact(SAMPLE_PATH)
        self.sources = [
            SourceFingerprint(role="stage2_input", episode=1, sha256="4" * 64),
            SourceFingerprint(role="stage2a_annotation", episode=1, sha256="5" * 64),
        ]

    def test_groups_states_into_relation_type_sequences(self) -> None:
        """相同有向角色对和语义标签的 State 应形成一条序列。"""

        artifact, _ = build_stage3_relation_review_artifact(self.legacy, self.sources)
        self.assertTrue(artifact.relation_types)
        for relation_type in artifact.relation_types:
            self.assertTrue(relation_type.generated_sequence)
            visible_from_values = [state.visible_from for state in relation_type.generated_sequence]
            self.assertEqual(visible_from_values, sorted(visible_from_values))
            self.assertTrue(relation_type.relation_type_id.startswith("relation_type:"))
            self.assertTrue(
                all(state.relation_state_id.startswith("relation_state:") for state in relation_type.generated_sequence)
            )

    def test_same_basis_migrates_type_and_state_ids(self) -> None:
        """相同机器序列应继承 Type、State 身份和人工审核。"""

        first, _ = build_stage3_relation_review_artifact(self.legacy, self.sources)
        relation_type = first.relation_types[0]
        complete_review(relation_type, "publish")
        previous_state_ids = [state.relation_state_id for state in relation_type.generated_sequence]
        second, report = build_stage3_relation_review_artifact(
            self.legacy,
            self.sources,
            previous=first,
        )
        migrated = second.relation_types[0]
        self.assertEqual(migrated.relation_type_id, relation_type.relation_type_id)
        self.assertEqual(
            [state.relation_state_id for state in migrated.generated_sequence],
            previous_state_ids,
        )
        self.assertEqual(migrated.disposition, "publish")
        self.assertIn(migrated.relation_type_id, report.migrated_ids)


if __name__ == "__main__":
    unittest.main()
