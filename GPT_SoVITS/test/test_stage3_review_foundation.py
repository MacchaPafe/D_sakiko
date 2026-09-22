"""统一 Stage 3 审核模型与迁移基础测试。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from rag.pipeline.review_migration import (
    blocked_removed_ids,
    canonical_json_sha256,
    migration_report_path,
    safely_write_json_model,
)
from rag.pipeline.review_models import ReviewFields, complete_review, reset_review_fields


class TestStage3ReviewFoundation(unittest.TestCase):
    """验证统一审核状态、摘要与安全写入规则。"""

    def test_review_decision_requires_completed_status(self) -> None:
        """未完成审核时不得保存最终发布处置。"""

        with self.assertRaises(ValidationError):
            ReviewFields(
                disposition="publish",
                review_basis_sha256="0" * 64,
            )

    def test_complete_and_reset_review(self) -> None:
        """内容修改应撤销处置但保留模型建议。"""

        fields = ReviewFields(
            suggested_disposition="publish",
            review_basis_sha256="1" * 64,
        )
        complete_review(fields, "publish")
        self.assertEqual(fields.review_status, "completed")
        self.assertEqual(fields.disposition, "publish")
        reset_review_fields(fields)
        self.assertEqual(fields.review_status, "unreviewed")
        self.assertIsNone(fields.disposition)
        self.assertEqual(fields.suggested_disposition, "publish")

    def test_reject_and_exclude_reason_codes_are_separate(self) -> None:
        """拒绝与排除不得混用结构化原因。"""

        fields = ReviewFields(review_basis_sha256="2" * 64)
        with self.assertRaises(ValidationError):
            complete_review(fields, "reject", "too_trivial")
        complete_review(fields, "exclude", "too_trivial")
        self.assertEqual(fields.disposition, "exclude")

    def test_canonical_hash_ignores_mapping_order(self) -> None:
        """同一语义 JSON 的审核摘要应稳定。"""

        first = {"a": 1, "b": [2, 3]}
        second = {"b": [2, 3], "a": 1}
        self.assertEqual(canonical_json_sha256(first), canonical_json_sha256(second))

    def test_safe_write_and_report_path(self) -> None:
        """安全写入应生成合法 JSON，报告路径应可机械推导。"""

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "review.json"
            fields = ReviewFields(review_basis_sha256="3" * 64)
            safely_write_json_model(fields, output)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["review_status"], "unreviewed")
            self.assertEqual(migration_report_path(output), Path(f"{output}.migration-report.json"))

    def test_removed_publish_candidates_require_explicit_permission(self) -> None:
        """已发布候选消失时必须提供当次删除许可。"""

        blocked = blocked_removed_ids(
            completed_publish_ids={"a", "b"},
            disappeared_ids={"b", "c"},
            allowed_ids=set(),
            allow_all_removed=False,
        )
        self.assertEqual(blocked, ["b"])
        self.assertEqual(
            blocked_removed_ids({"a"}, {"a"}, set(), allow_all_removed=True),
            [],
        )


if __name__ == "__main__":
    unittest.main()
