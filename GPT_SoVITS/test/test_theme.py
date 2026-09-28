from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from coloraide import Color

from ui_main.theme import derive_theme_palette


class ThemeOnAccentTestCase(unittest.TestCase):
    """验证主题色背景上的黑白文字偏好和对比度兜底。"""

    def test_lightness_preference_selects_white_when_both_colors_pass(self) -> None:
        """临界深蓝色的黑白文字都达标时应遵循明度偏好选择白色。"""
        accent = "#0077DD"
        palette = derive_theme_palette(accent)

        self.assertGreaterEqual(self.contrast(accent, "#000000"), 3.5)
        self.assertGreaterEqual(self.contrast(accent, "#FFFFFF"), 3.5)
        self.assertEqual(palette.on_accent, "#FFFFFF")

    def test_light_background_falls_back_to_black(self) -> None:
        """明亮背景偏好的白字不达标时应回退为黑字。"""
        accent = "#FFEE22"
        palette = derive_theme_palette(accent)

        self.assertLess(self.contrast(accent, "#FFFFFF"), 3.5)
        self.assertEqual(palette.on_accent, "#000000")

    def test_dark_background_keeps_white(self) -> None:
        """深色背景应选择满足最低对比度要求的白字。"""
        accent = "#881188"
        palette = derive_theme_palette(accent)

        self.assertLess(self.contrast(accent, "#000000"), 3.5)
        self.assertEqual(palette.on_accent, "#FFFFFF")

    def test_selected_foreground_meets_minimum_contrast(self) -> None:
        """代表性明暗和临界主题色的最终文字均应满足最低对比度。"""
        accents = (
            "#0077DD",
            "#EE0022",
            "#AA33CC",
            "#FFEE22",
            "#881188",
            "#7799CC",
        )

        for accent in accents:
            with self.subTest(accent=accent):
                palette = derive_theme_palette(accent)
                self.assertGreaterEqual(self.contrast(accent, palette.on_accent), 3.5)

    @staticmethod
    def contrast(first: str, second: str) -> float:
        """计算测试颜色之间的 WCAG 2.1 对比度。"""
        return Color(first).contrast(Color(second), method="wcag21")


if __name__ == "__main__":
    unittest.main()
