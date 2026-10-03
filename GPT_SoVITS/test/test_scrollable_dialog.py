from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtGui import QScreen
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMenu, QPushButton, QScrollArea, QWidget

from qtUI import ChatGUI, MoreFunctionWindow, SettingWindow
from ui_main.custom_widgets.scrollable_dialog import ScrollableDialog
from ui_main.theme import derive_theme_palette


class SettingsParent(QWidget):
    """仅提供设置窗口实际使用的宿主接口，避免启动聊天和音频服务。"""

    def __init__(self) -> None:
        """记录设置操作并模拟角色资源状态。"""
        super().__init__()
        self.current_character = Mock(live2d_json="model.json")
        self.current_character.has_valid_voice_model.return_value = False
        self.commands: list[tuple[str, str]] = []

    def run_input_command_text(self, command: str, source: str) -> None:
        """记录按钮触发的原有业务命令。"""
        self.commands.append((command, source))

    def toggle_live2d_text_display(self) -> None:
        """提供不启动渲染器的文本显隐接口。"""

    def get_current_l2d_fps(self) -> int:
        """返回测试帧率。"""
        return 30

    def _create_sakiko_mask_menu(self) -> QMenu:
        """提供真实的原生菜单供按钮挂接。"""
        return QMenu(self)


class ScrollableDialogTests(unittest.TestCase):
    """通过真实 Qt 布局验证小屏可达性、宽度重排和屏幕边界。"""

    @classmethod
    def setUpClass(cls) -> None:
        """创建共用 Qt 应用。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """统一使用有足够宽度的逻辑工作区，隔离测试机屏幕配置。"""
        screen_patch = patch.object(QScreen, "availableGeometry", return_value=QRect(0, 0, 1280, 960))
        screen_patch.start()
        self.addCleanup(screen_patch.stop)

    def show_dialog(self, dialog: ScrollableDialog, width: int = 420, height: int = 360) -> None:
        """显示并缩小弹窗，让延迟布局和滚动范围完成更新。"""
        self.addCleanup(dialog.close)
        dialog.show()
        dialog.resize(width, height)
        QTest.qWait(30)

    def make_more_dialog(self) -> MoreFunctionWindow:
        """启用所有可选入口，覆盖内容最多的情况。"""
        return MoreFunctionWindow(
            lambda: None, derive_theme_palette("#7799CC"),
            check_update_fun=lambda: None, check_repair_fun=lambda: None,
            feedback_fun=lambda: None, feedback_history_fun=lambda: None,
            feedback_admin_fun=lambda: None, toggle_pet_fun=lambda: None,
        )

    def assert_buttons_reachable(self, dialog: ScrollableDialog) -> None:
        """逐个滚动到按钮并确认其完整位于视口内且没有挤压文字。"""
        scroll = dialog.scroll_area
        self.assertIs(type(scroll), QScrollArea)
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        for button in dialog.content_widget.findChildren(QPushButton):
            with self.subTest(button=button.text()):
                scroll.ensureWidgetVisible(button)
                self.app.processEvents()
                rect = QRect(button.mapTo(scroll.viewport(), QPoint()), button.size())
                self.assertTrue(scroll.viewport().rect().contains(rect), (rect, scroll.viewport().rect()))
                self.assertGreaterEqual(button.width(), button.minimumSizeHint().width())
                self.assertGreaterEqual(button.height(), button.minimumSizeHint().height())

    def test_more_functions_scroll_and_fixed_exit(self) -> None:
        """全部功能可以滚动访问，退出按钮始终固定且保留回调。"""
        dialog = self.make_more_dialog()
        self.show_dialog(dialog)
        exit_position = dialog.close_program_button.pos()
        self.assert_buttons_reachable(dialog)
        self.assertEqual(dialog.close_program_button.pos(), exit_position)
        self.assertTrue(dialog.rect().contains(dialog.close_program_button.geometry()))
        calls: list[str] = []
        dialog.close_program_button.clicked.connect(lambda: calls.append("exit"))
        QTest.mouseClick(dialog.close_program_button, Qt.LeftButton)
        self.assertEqual(calls, ["exit"])
        self.assertFalse(dialog.isVisible())

    def test_settings_scroll_and_existing_actions(self) -> None:
        """设置项可达且角色状态切换和资源禁用状态不受布局影响。"""
        parent = SettingsParent()
        setattr(parent, "screen", QRect(0, 0, 1920, 1080))
        self.addCleanup(parent.close)
        dialog = SettingWindow(cast(ChatGUI, parent), "#7799CC", Mock())
        self.show_dialog(dialog)
        self.assert_buttons_reachable(dialog)
        self.assertFalse(dialog.change_reference_audio_btn.isEnabled())
        dialog.scroll_area.ensureWidgetVisible(dialog.convert_sakiko_state_btn)
        QTest.mouseClick(dialog.convert_sakiko_state_btn, Qt.LeftButton)
        self.assertEqual(parent.commands, [("conv", "setting_button")])

    def test_feedback_reflows_and_keeps_focus_and_callback(self) -> None:
        """较长按钮在缩窄后竖排，恢复宽度后横排，原控件与连接保持有效。"""
        calls: list[str] = []
        dialog = MoreFunctionWindow(
            lambda: None, derive_theme_palette("#7799CC"),
            feedback_fun=lambda: calls.append("feedback"), feedback_history_fun=lambda: None,
        )
        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        first, second = buttons["反馈建议"], buttons["已提交反馈"]
        first.setText("提交使用过程中的反馈建议")
        second.setText("查看此前已经提交的反馈")
        self.show_dialog(dialog, 800, 800)
        self.assertEqual(first.y(), second.y())
        first.setFocus()
        dialog.resize(360, 360)
        QTest.qWait(30)
        self.assertGreater(second.y(), first.geometry().bottom())
        self.assertIs(dialog.focusWidget(), first)
        self.assert_buttons_reachable(dialog)
        dialog.resize(800, 800)
        QTest.qWait(30)
        self.assertEqual(first.y(), second.y())
        QTest.mouseClick(first, Qt.LeftButton)
        self.assertEqual(calls, ["feedback"])

    def test_window_fits_work_area_and_reacts_to_screen_changes(self) -> None:
        """窗口完整边框位于工作区内，负坐标副屏和工作区缩小同样适用。"""
        for height in (600, 720, 960, 1200):
            with self.subTest(height=height):
                area = QRect(-1280, 30, 1280, height)
                with patch.object(QScreen, "availableGeometry", return_value=area):
                    dialog = self.make_more_dialog()
                    self.show_dialog(dialog, 420, 1500)
                    self.assertTrue(area.contains(dialog.frameGeometry()))
                    smaller = QRect(-1280, 30, 1280, 480)
                    with patch.object(QScreen, "availableGeometry", return_value=smaller):
                        screen = dialog.screen()
                        screen.availableGeometryChanged.emit(smaller)
                        QTest.qWait(30)
                        self.assertTrue(smaller.contains(dialog.frameGeometry()))
                        self.assert_buttons_reachable(dialog)
                    dialog.close()

    def test_reopening_dialog_does_not_accumulate_screen_connections(self) -> None:
        """弹窗重复显示关闭后仍能正常响应屏幕变化。"""
        dialog = self.make_more_dialog()
        self.show_dialog(dialog)
        dialog.close()
        self.show_dialog(dialog)
        dialog.windowHandle().screenChanged.emit(dialog.screen())
        self.app.processEvents()
        self.assert_buttons_reachable(dialog)


if __name__ == "__main__":
    unittest.main()
