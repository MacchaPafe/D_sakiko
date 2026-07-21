from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PyQt5.QtWidgets import QApplication

from ui_main.components.context_usage_indicator import (
    ContextUsageIndicator,
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


class ContextUsageThresholdTestCase(unittest.TestCase):
    """验证上下文压缩阈值滑块的显示和信号。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_threshold_slider_uses_seventy_to_ninety_percent_range(self) -> None:
        indicator = ContextUsageIndicator()
        received: list[float] = []
        indicator.summaryThresholdChanged.connect(received.append)

        indicator.set_summary_threshold_ratio(0.65)
        popup = indicator._popup
        self.assertEqual(popup.summary_threshold_slider.minimum(), 14)
        self.assertEqual(popup.summary_threshold_slider.maximum(), 18)
        self.assertEqual(popup.summary_threshold_label.text(), "上下文压缩阈值：70%")
        self.assertIn("上下文上限的 70%", popup.summary_threshold_slider.toolTip())

        popup.summary_threshold_slider.setValue(17)
        self.assertEqual(received[-1], 0.85)
        self.assertEqual(popup.summary_threshold_label.text(), "上下文压缩阈值：85%")


if __name__ == "__main__":
    unittest.main()
