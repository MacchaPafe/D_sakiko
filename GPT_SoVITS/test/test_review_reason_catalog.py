"""Stage 3 不收录原因目录测试。"""

from __future__ import annotations

import unittest

from rag.pipeline.review_reason_catalog import (
    disposition_badge_label,
    review_reason_by_key,
    review_reason_options,
)


class TestReviewReasonCatalog(unittest.TestCase):
    """验证中文原因、内容类型过滤和内部处置映射。"""

    def test_story_does_not_offer_transient_state(self) -> None:
        """Story Event 不应因持续时间短而被当作瞬时状态排除。"""

        keys = {option.key for option in review_reason_options("story")}
        self.assertNotIn("exclude:transient_state", keys)
        self.assertIn("exclude:too_trivial", keys)

    def test_relation_offers_transient_state(self) -> None:
        """Relation 应允许使用短期或瞬时状态排除原因。"""

        option = review_reason_by_key("exclude:transient_state", "relation")
        self.assertEqual(option.disposition, "exclude")
        self.assertEqual(option.reason_code, "transient_state")
        self.assertIn("短期", option.label)

    def test_reject_and_exclude_other_are_distinct(self) -> None:
        """两个 other 选项必须保留不同的内部处置语义。"""

        rejected = review_reason_by_key("reject:other", "thought")
        excluded = review_reason_by_key("exclude:other", "thought")
        self.assertEqual(rejected.reason_code, "other")
        self.assertEqual(excluded.reason_code, "other")
        self.assertEqual(rejected.disposition, "reject")
        self.assertEqual(excluded.disposition, "exclude")

    def test_badges_explain_disposition_semantics(self) -> None:
        """队列标签应向用户显示处置含义，而不是裸枚举值。"""

        self.assertEqual(disposition_badge_label("reject"), "Reject · 候选无效")
        self.assertEqual(disposition_badge_label("exclude"), "Exclude · 有效但未收录")


if __name__ == "__main__":
    unittest.main()
