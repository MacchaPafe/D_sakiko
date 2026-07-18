"""Story Event 与 Lore Entry 新审核产物测试。"""

from __future__ import annotations

from pathlib import Path
import unittest

from rag.pipeline.review_models import SourceFingerprint, complete_review
from rag.pipeline.stage3_document_review import build_stage3_document_review_artifact
from rag.pipeline.stage3_rag_import import load_stage3_normalized_import_artifact


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PATH = PROJECT_ROOT / "GPT_SoVITS" / "rag" / "pipeline" / "data" / "annotations_stage3" / "ep01_rag_ready.json"


class TestStage3DocumentReview(unittest.TestCase):
    """验证候选身份、审核迁移与删除保护。"""

    def setUp(self) -> None:
        """读取现有内部规范化样例作为转换输入。"""

        self.normalized = load_stage3_normalized_import_artifact(SAMPLE_PATH)
        self.sources = [
            SourceFingerprint(role="stage2_input", episode=1, sha256="1" * 64),
            SourceFingerprint(role="stage2a_annotation", episode=1, sha256="2" * 64),
        ]

    def test_same_basis_migrates_identity_and_review(self) -> None:
        """审核基础未变时应完整迁移 candidate ID 与人工处置。"""

        first, _ = build_stage3_document_review_artifact(self.normalized, self.sources)
        candidate = first.story_events[0]
        complete_review(candidate, "publish")
        second, report = build_stage3_document_review_artifact(
            self.normalized,
            self.sources,
            previous=first,
        )
        migrated = second.story_events[0]
        self.assertEqual(migrated.candidate_id, candidate.candidate_id)
        self.assertEqual(migrated.review_status, "completed")
        self.assertEqual(migrated.disposition, "publish")
        self.assertIn(candidate.candidate_id, report.migrated_ids)

    def test_changed_machine_document_resets_review_but_keeps_identity(self) -> None:
        """同一来源的机器文档变化应保留身份但撤销审核。"""

        first, _ = build_stage3_document_review_artifact(self.normalized, self.sources)
        candidate = first.story_events[0]
        complete_review(candidate, "publish")
        changed = self.normalized.model_copy(deep=True)
        changed.story_events[0].document.summary += " 新证据。"
        second, report = build_stage3_document_review_artifact(
            changed,
            self.sources,
            previous=first,
        )
        reset = second.story_events[0]
        self.assertEqual(reset.candidate_id, candidate.candidate_id)
        self.assertEqual(reset.review_status, "unreviewed")
        self.assertIsNone(reset.disposition)
        self.assertIn(candidate.candidate_id, report.reset_ids)

    def test_published_candidate_disappearance_is_blocked(self) -> None:
        """已完成发布的候选消失时应要求一次性许可。"""

        first, _ = build_stage3_document_review_artifact(self.normalized, self.sources)
        candidate = first.story_events[0]
        complete_review(candidate, "publish")
        changed = self.normalized.model_copy(deep=True)
        changed.story_events = changed.story_events[1:]
        _, report = build_stage3_document_review_artifact(
            changed,
            self.sources,
            previous=first,
        )
        self.assertFalse(report.succeeded)
        self.assertIn(candidate.candidate_id, report.blocked_removed_ids)


if __name__ == "__main__":
    unittest.main()
