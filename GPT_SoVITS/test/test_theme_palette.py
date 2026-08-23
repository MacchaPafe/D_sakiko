from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

from coloraide import Color


script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from ui_main.theme import derive_theme_palette


class ThemePaletteContrastTestCase(unittest.TestCase):
    """测试指定亮黄色角色与其余角色的按钮文字策略。"""

    SPECIAL_DARK_SEEDS = {"#FFEE88", "#FFEE22", "#FFEEAA", "#FFDD88", "#FFEE55"}

    def test_only_selected_yellow_accents_use_tinted_dark_foreground(self) -> None:
        """仅五个指定亮黄色使用带色相深灰字，并保持清晰状态色。"""
        for seed in self.SPECIAL_DARK_SEEDS:
            with self.subTest(seed=seed):
                palette = derive_theme_palette(seed)
                self.assertNotIn(palette.on_accent, {"#000000", "#FFFFFF"})
                states = (palette.accent, palette.accent_hover, palette.accent_pressed)
                self.assertEqual(len(set(states)), 3)
                for background in states:
                    ratio = Color(background).contrast(Color(palette.on_accent), method="wcag21")
                    self.assertGreaterEqual(ratio, 4.5)

    def test_every_other_preset_accent_uses_white_foreground(self) -> None:
        """ui_constants 中其余全部预设色始终使用白字。"""
        constants_path = Path(script_dir) / "ui_constants.py"
        preset_seeds = set(re.findall(
            r'"theme_color": "(#[0-9A-Fa-f]{6})"',
            constants_path.read_text(encoding="utf-8"),
        ))

        self.assertEqual(len(preset_seeds), 50)
        for seed in preset_seeds - self.SPECIAL_DARK_SEEDS:
            with self.subTest(seed=seed):
                self.assertEqual(derive_theme_palette(seed).on_accent, "#FFFFFF")


if __name__ == "__main__":
    unittest.main()
