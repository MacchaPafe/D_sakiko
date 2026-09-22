"""聊天输入区选项 Chip 控件测试。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QToolButton, QWidget

from ui_main.components.input_option_chips import (
    ChoiceChip,
    SplitToggleChip,
    ToggleChip,
)
from ui_main.theme import derive_theme_palette


class InputOptionChipTest(unittest.TestCase):
    """验证输入区语义控件的状态、键盘和可访问性约定。"""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        """创建无界面的 Qt 应用。"""

        existing = QApplication.instance()
        cls.app = existing if isinstance(existing, QApplication) else QApplication([])

    def setUp(self) -> None:
        """创建测试父窗口和主题色板。"""

        self.parent = QWidget()
        self.palette = derive_theme_palette("#7799CC")

    def tearDown(self) -> None:
        """销毁测试窗口并处理延迟删除事件。"""

        self.parent.close()
        self.parent.deleteLater()
        self.app.processEvents()

    def test_toggle_chip_keeps_fixed_label_and_toggles_with_space(self) -> None:
        """二元 Chip 应以固定文案和键盘选中状态表达开关。"""

        chip = ToggleChip(
            "工具",
            accessible_name="工具调用",
            height=28,
            parent=self.parent,
        )
        chip.set_theme_palette(self.palette)
        self.parent.show()
        chip.setFocus()

        QTest.keyClick(chip, Qt.Key_Space)

        self.assertTrue(chip.isChecked())
        self.assertEqual(chip.text(), "工具")
        self.assertEqual(chip.accessibleName(), "工具调用")
        self.assertEqual(chip.focusPolicy(), Qt.StrongFocus)
        self.assertFalse(chip.icon().isNull())

    def test_choice_chip_is_menu_choice_not_toggle(self) -> None:
        """模式 Chip 应整块打开菜单，且自身不携带二元选中状态。"""

        chip = ChoiceChip(
            accessible_name="思考模式",
            height=28,
            parent=self.parent,
        )
        chip.setText("自动 · 默认")
        chip.set_theme_palette(self.palette)

        self.assertFalse(chip.isCheckable())
        self.assertEqual(chip.popupMode(), QToolButton.InstantPopup)
        self.assertEqual(chip.accessibleName(), "思考模式")
        self.assertIn("menu-indicator", chip.styleSheet())

    def test_split_toggle_reserves_separate_menu_button(self) -> None:
        """世界书 Chip 应分别提供主区切换和不小于 24 像素的箭头区。"""

        chip = SplitToggleChip(
            "世界书",
            accessible_name="世界书",
            height=28,
            parent=self.parent,
        )
        chip.set_theme_palette(self.palette)

        self.assertTrue(chip.isCheckable())
        self.assertEqual(chip.popupMode(), QToolButton.MenuButtonPopup)
        self.assertIn("width: 24px", chip.styleSheet())
        self.assertEqual(chip.accessibleName(), "世界书")


if __name__ == "__main__":
    unittest.main()
