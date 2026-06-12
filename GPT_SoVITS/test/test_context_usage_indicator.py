from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ui_main.components.context_usage_indicator import (
    ContextUsageSizing,
    resolve_context_usage_sizing,
)


class ContextUsageSizingTestCase(unittest.TestCase):
    """验证上下文用量组件的跨平台尺寸策略。"""

    def test_macos_uses_stable_default_font_size(self) -> None:
        """macOS 应固定弹窗尺寸并保留圆环缩放。"""
        sizing = resolve_context_usage_sizing(1117, "darwin")

        self.assertEqual(sizing.indicator_size, 20)
        self.assertEqual(sizing.popup_width, 188)
        self.assertEqual(sizing.popup_font_size, 12)

    def test_windows_keeps_screen_height_scaling(self) -> None:
        """Windows 应保留高分辨率屏幕的尺寸补偿。"""
        sizing = resolve_context_usage_sizing(1440, "win32")

        self.assertEqual(sizing.indicator_size, 21)
        self.assertEqual(sizing.popup_width, 331)
        self.assertEqual(sizing.popup_font_size, 24)


if __name__ == "__main__":
    unittest.main()
