from __future__ import annotations

import os
import sys
import unittest

script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from emotion_enum import EmotionEnum
from live2d_support.motion_semantics import (
    build_downloaded_v2_standard_motions,
    direct_motion_keywords,
    motion_group_display_title,
    motion_group_display_titles,
    motion_group_for_emotion,
    rana_motion_slices,
    standard_motion_group_ids,
)


class Live2DMotionSemanticsTestCase(unittest.TestCase):
    """测试 Live2D 标准动作组语义。"""

    def test_display_title_handles_base_and_position_groups(self) -> None:
        """验证基础动作组和位置变体都会生成可读标题。"""
        self.assertEqual(motion_group_display_title("happiness"), "1. 开心")
        self.assertEqual(motion_group_display_title("happiness_L"), "1. 开心（左）")
        self.assertEqual(motion_group_display_title("happiness_C"), "1. 开心（中）")
        self.assertEqual(motion_group_display_title("happiness_R"), "1. 开心（右）")
        self.assertEqual(motion_group_display_title("custom_group_L"), "custom_group_L")

    def test_display_titles_can_include_position_variants(self) -> None:
        """验证批量显示名映射可按需包含位置变体。"""
        titles = motion_group_display_titles(include_position_variants=True)

        self.assertEqual(titles["sadness"], "2. 伤心")
        self.assertEqual(titles["sadness_R"], "2. 伤心（右）")

    def test_motion_group_for_emotion_supports_old_and_new_labels(self) -> None:
        """验证旧标签、新标签和枚举都能转换为标准动作组。"""
        self.assertEqual(motion_group_for_emotion("LABEL_0"), "happiness")
        self.assertEqual(motion_group_for_emotion("like"), "like")
        self.assertEqual(motion_group_for_emotion(EmotionEnum.FEAR), "fear")
        self.assertEqual(motion_group_for_emotion("bye", default=""), "")

    def test_downloaded_v2_builder_preserves_group_order_and_fallback_names(self) -> None:
        """验证下载 V2 模型的动作归组保持既有顺序和 fallback 命名。"""
        motions = build_downloaded_v2_standard_motions(
            ("smile01.mtn", "sad01.mtn"),
            fallback_file_name="idle01.mtn",
        )

        self.assertEqual(list(motions.keys()), list(standard_motion_group_ids()))
        self.assertEqual(motions["happiness"], [{"name": "happiness_0", "file": "smile01.mtn"}])
        self.assertEqual(motions["sadness"], [{"name": "sadness_5", "file": "sad01.mtn"}])
        self.assertEqual(motions["anger"], [{"name": "anger_17", "file": "idle01.mtn"}])

    def test_normalizer_keyword_and_rana_rules_are_exposed(self) -> None:
        """验证规范化所需的关键词和旧 rana 切片可从统一模块读取。"""
        self.assertIn("smile", direct_motion_keywords("happiness"))
        self.assertEqual(rana_motion_slices()[0], ("happiness", 0, 6))
        self.assertEqual(rana_motion_slices()[-1], ("talking_motion", 60, 61))


if __name__ == "__main__":
    unittest.main()
