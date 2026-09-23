"""验证桌宠交互、消息隔离和播放后的字幕生命周期。"""

from __future__ import annotations

import os
from queue import Queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt5.QtGui import QMouseEvent, QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from desktop_pet.window import PetWindow
from desktop_pet.controller import DesktopController
from desktop_pet.focus import PetFocus
from runtime.drafts import DraftStore
from runtime.single_character_performance import SingleCharacterPerformance
from runtime.voice_input import VoiceInputService
from ui_main.theme import derive_theme_palette


class PetInteractionTests(TestCase):
    """验证真实 Qt 控件的缩放、菜单和消息显示行为。"""

    @classmethod
    def setUpClass(cls) -> None:
        """共享测试应用，使用离屏窗口避免干扰桌面。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """创建独立草稿、语音服务和桌宠视图。"""
        self.drafts = DraftStore()
        self.voice = VoiceInputService(self.drafts)
        self.voice._state("idle")
        self.host = Mock(
            current_chat_id="test", drafts=self.drafts, voice_input=self.voice
        )
        self.host._theme_palette = derive_theme_palette("#7799CC")
        self.host.is_response_active.return_value = False
        self.host.isVisible.return_value = False
        self.host._current_model_supports_vision.return_value = True
        self.host.desktop_controller = None
        self.pet = PetWindow(self.host, Queue(), Queue(), SimpleNamespace(value=True))
        self.pet.base_bounds = QRect(40, 35, 300, 430)
        self.pet.set_zoom(1.0)
        self.addCleanup(self.voice.close)
        self.addCleanup(self.pet.shutdown)

    def test_zoom_keeps_foot_and_input_size_and_does_not_recalibrate(self) -> None:
        """缩放保持脚底位置和输入字号，不重新抓帧标定模型。"""
        self.pet.move(100, 10)
        foot = self.pet.mapToGlobal(QPoint(self.pet.width() // 2, self.pet.anchor))
        font = self.pet.input.font()
        with patch.object(self.pet.renderer, "fit_and_bounds") as fit:
            self.pet.set_zoom(0.6)
            fit.assert_not_called()
        self.assertEqual(self.pet.renderer.size().width(), 228)
        self.assertEqual(
            self.pet.mapToGlobal(QPoint(self.pet.width() // 2, self.pet.anchor)), foot
        )
        self.assertEqual(self.pet.input.font(), font)
        self.pet.set_zoom(10.0)
        self.assertLessEqual(self.pet.zoom, 1.6)
        self.assertEqual(
            self.pet.mapToGlobal(QPoint(self.pet.width() // 2, self.pet.anchor)), foot
        )
        self.pet.reset_zoom()
        self.assertLessEqual(self.pet.zoom, 1.0)

    def test_drag_to_edge_is_not_clamped_by_release_input_zoom_or_tray(self) -> None:
        """透明窗口可跨越屏幕边缘，松手、输入和托盘恢复均不回弹。"""
        renderer = self.pet.renderer
        self.pet.move(30, 30)
        center = renderer.hit_bounds.center()
        global_center = renderer.mapToGlobal(center)
        delta = QPoint(-140, -140)
        renderer.mousePressEvent(
            QMouseEvent(
                QEvent.MouseButtonPress,
                QPointF(center),
                QPointF(global_center),
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        renderer.mouseMoveEvent(
            QMouseEvent(
                QEvent.MouseMove,
                QPointF(center + delta),
                QPointF(global_center + delta),
                Qt.NoButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        moved = self.pet.pos()
        self.assertEqual(moved.x(), -110)
        self.assertEqual(moved.y(), -110)
        renderer.mouseReleaseEvent(
            QMouseEvent(
                QEvent.MouseButtonRelease,
                QPointF(center),
                QPointF(global_center + delta),
                Qt.LeftButton,
                Qt.NoButton,
                Qt.NoModifier,
            )
        )
        self.assertEqual(self.pet.pos(), moved)
        self.pet.expand()
        self.assertEqual(self.pet.pos(), moved)
        foot = self.pet.mapToGlobal(QPoint(self.pet.width() // 2, self.pet.anchor))
        self.pet.set_zoom(0.8)
        self.assertEqual(
            self.pet.mapToGlobal(QPoint(self.pet.width() // 2, self.pet.anchor)), foot
        )
        before_show = self.pet.pos()
        self.pet.hide()
        controller = Mock(pet_mode=True, pet=self.pet)
        DesktopController.show_pet(controller)
        self.assertEqual(self.pet.pos(), before_show)

    def test_released_voice_can_wake_and_preparing_can_cancel(self) -> None:
        """模型释放后按钮可用，准备录音期间离开角色仍保留取消入口。"""
        from qtUI import ChatGUI

        chat = Mock()
        for state in ("dormant", "unloading", "preparing"):
            self.voice._state(state)
            self.pet.hovered = False
            self.pet.refresh_state()
            self.assertTrue(self.pet.voice_button.isEnabled())
            self.assertTrue(self.pet.record_button.isEnabled())
            ChatGUI._voice_state_changed(chat, state)
            chat.voice_button.setEnabled.assert_called_with(True)
        self.assertFalse(self.pet.tools.isHidden())
        self.assertIn("取消", self.pet.record_button.toolTip())
        chat.voice_button.setText.assert_called_with("取消")

    def test_native_focus_loss_collapses_without_losing_draft(self) -> None:
        """原生非激活面板不调用应用激活，失去键盘焦点时保留草稿。"""
        focus = Mock(spec=PetFocus, nonactivating=True)
        focus.has_input_focus.return_value = True
        self.pet._focus = focus
        with patch.object(self.pet, "activateWindow") as activate:
            self.pet.expand()
            focus.request_input.assert_called_once()
            activate.assert_not_called()
        self.pet.input.text_edit.setPlainText("保留中文草稿")
        focus.has_input_focus.return_value = False
        self.pet.popup_open = True
        self.pet.refresh_state()
        self.assertTrue(self.pet.expanded)
        self.pet.popup_open = False
        self.pet.refresh_state()
        self.assertFalse(self.pet.expanded)
        focus.release_input.assert_called_once()
        self.assertEqual(self.drafts.get("test").text, "保留中文草稿")
        focus.has_input_focus.return_value = True
        self.pet.expand()
        self.assertEqual(self.pet.input.toPlainText(), "保留中文草稿")

    def test_wheel_and_click_only_apply_to_character_and_busy_click_is_ignored(
        self,
    ) -> None:
        """角色滚轮与单击生效，透明边缘和忙碌期间不会触发。"""
        renderer = self.pet.renderer
        center = renderer.hit_bounds.center()
        wheel = QWheelEvent(
            QPointF(center),
            QPointF(renderer.mapToGlobal(center)),
            QPoint(),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.NoScrollPhase,
            False,
        )
        renderer.wheelEvent(wheel)
        self.assertLess(self.pet.zoom, 1.0)
        zoom = self.pet.zoom
        outside = QWheelEvent(
            QPointF(0, 0),
            QPointF(0, 0),
            QPoint(),
            QPoint(0, 120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.NoScrollPhase,
            False,
        )
        renderer.wheelEvent(outside)
        self.assertEqual(self.pet.zoom, zoom)
        QTest.mouseClick(renderer, Qt.LeftButton, pos=renderer.hit_bounds.center())
        self.assertTrue(renderer.interaction_requested)
        renderer.interaction_requested = False
        self.host.is_response_active.return_value = True
        QTest.mouseClick(renderer, Qt.LeftButton, pos=renderer.hit_bounds.center())
        self.assertFalse(renderer.interaction_requested)

    def test_notice_expires_without_overwriting_or_reviving_subtitle(self) -> None:
        """短通知与字幕互不覆盖，过期消息不会在收起面板后复活。"""
        self.pet._subtitle("角色回复")
        self.pet.show_status("请等待播放完成")
        self.pet.notice_timer.start(1)
        QTest.qWait(20)
        self.assertTrue(self.pet.notice.isHidden())
        self.assertEqual(self.pet.subtitle.text(), "角色回复")
        self.pet.expand()
        self.pet._subtitle("")
        self.pet.collapse()
        self.assertTrue(self.pet.subtitle.isHidden())
        self.assertTrue(self.pet.notice.isHidden())

    def test_recognition_does_not_reactivate_inactive_mac_tool_window(self) -> None:
        """macOS 工具窗口保留活动标志时，识别完成也不能抢回应用焦点。"""
        with (
            patch.object(self.pet, "isActiveWindow", return_value=True),
            patch.object(self.pet, "isVisible", return_value=True),
            patch.object(self.pet, "expand") as expand,
            patch(
                "desktop_pet.window.QApplication.applicationState",
                return_value=Qt.ApplicationInactive,
            ),
        ):
            self.pet.recognized("test")
            expand.assert_not_called()

    def test_compact_panel_keeps_buttons_inside_with_long_error(self) -> None:
        """小桌宠显示长文本和错误时保留可见发送按钮与草稿。"""
        self.pet.set_zoom(0.6)
        self.pet.expand()
        self.pet.refresh_state()
        self.assertLess(self.pet.panel.height(), 130)
        self.pet.input.text_edit.setPlainText("长文本\n" * 20)
        self.pet.show_input_error("错误详情\n" * 40)
        QTest.qWait(20)
        self.pet.refresh_state()
        self.pet.panel.layout().activate()
        bottom = self.pet.send_button.mapTo(
            self.pet.panel, QPoint(0, self.pet.send_button.height())
        ).y()
        self.assertLessEqual(bottom, self.pet.panel.height())
        self.assertLessEqual(self.pet.panel.geometry().bottom(), self.pet.height())
        self.assertGreater(self.pet.input_scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.drafts.get("test").text, "长文本\n" * 20)

    def test_menu_separates_open_and_switch_and_disables_switch_when_busy(self) -> None:
        """右键提供两个不同入口，并保护忙碌时的形态切换。"""
        opened, switched = Mock(), Mock()
        self.pet.openChat.connect(opened)
        self.pet.switchToWindow.connect(switched)
        menu = self.pet.create_context_menu()
        actions = {action.text(): action for action in menu.actions()}
        actions["打开聊天窗口"].trigger()
        opened.assert_called_once()
        switched.assert_not_called()
        actions["切换到窗口对话"].trigger()
        switched.assert_called_once()
        menu.deleteLater()
        self.host.is_response_active.return_value = True
        menu = self.pet.create_context_menu()
        self.assertFalse(
            next(
                action for action in menu.actions() if action.text() == "切换到窗口对话"
            ).isEnabled()
        )
        menu.deleteLater()

    def test_qt_settings_stay_in_qt_and_only_hidden_runtime_errors_reach_pet(
        self,
    ) -> None:
        """设置通知不再镜像到桌宠，必要错误只在主窗口隐藏时转发。"""
        from qtUI import ChatGUI

        host = Mock()
        host.isVisible.return_value = False
        pet = host.desktop_controller.pet
        ChatGUI._set_message_box_text(host, "已更新世界书设置")
        pet.show_status.assert_not_called()
        ChatGUI._set_message_box_text(host, "语音合成失败", notify_pet=True)
        pet.show_status.assert_called_once_with("语音合成失败")
        pet.reset_mock()
        host.isVisible.return_value = True
        ChatGUI._set_message_box_text(host, "语音合成失败", notify_pet=True)
        pet.show_status.assert_not_called()

    def test_switch_back_reuses_controller_and_preserves_pet_if_normal_start_fails(
        self,
    ) -> None:
        """普通窗口启动失败时保留桌宠，成功后才释放原桌宠。"""
        controller = Mock()
        controller.exiting = False
        controller.can_switch_mode.return_value = True
        controller.router.latest_model = None
        controller.pet_mode = True
        original_pet = controller.pet
        controller.router.start_normal.side_effect = RuntimeError("无法启动")
        with patch("desktop_pet.controller.QMessageBox.warning"):
            DesktopController.toggle_mode(controller)
        self.assertIs(controller.pet, original_pet)
        self.assertTrue(controller.pet_mode)
        original_pet.shutdown.assert_not_called()
        controller.router.start_normal.side_effect = None
        DesktopController.toggle_mode(controller)
        original_pet.shutdown.assert_called_once()
        self.assertIsNone(controller.pet)
        self.assertFalse(controller.pet_mode)
        controller.show_chat.assert_called_once()


class PerformanceInteractionTests(TestCase):
    """验证两个窗口共用的动作规则与桌宠字幕计时。"""

    def setUp(self) -> None:
        """使用可控时钟与模拟模型隔离声音设备和动作文件。"""
        self.player = SingleCharacterPerformance()
        self.model = Mock()
        self.audio = patch.object(self.player, "audio_busy", return_value=False)
        self.audio.start()
        self.addCleanup(self.audio.stop)

    def test_all_characters_use_idle_fallback_cooldown_and_busy_guards(self) -> None:
        """非祥子角色也会播放动作，并遵循回退、冷却与忙碌保护。"""
        self.assertFalse(self.player.if_sakiko)
        self.model.StartRandomMotion.side_effect = [False, True]
        with patch(
            "runtime.single_character_performance.time.monotonic", return_value=10.0
        ):
            self.assertTrue(self.player.play_interaction(self.model))
            self.assertFalse(self.player.play_interaction(self.model))
        self.assertEqual(
            [call.args[0] for call in self.model.StartRandomMotion.call_args_list],
            ["IDLE", "idle_motion"],
        )
        self.assertTrue(
            all(
                call.args[1] == 2
                for call in self.model.StartRandomMotion.call_args_list
            )
        )
        self.model.reset_mock()
        self.player.recording = True
        self.assertFalse(self.player.play_interaction(self.model))
        self.player.recording = False
        self.player.thinking = True
        self.assertFalse(self.player.play_interaction(self.model))
        self.player.thinking = False
        self.assertFalse(self.player.play_interaction(self.model, blocked=True))
        self.model.StartRandomMotion.assert_not_called()

    def test_text_only_subtitle_gets_reading_time_and_new_reply_resets_timer(
        self,
    ) -> None:
        """无音频长回复保留阅读时间，新回复和取消均清理旧计时。"""
        self.player.subtitle_hide_delay = 6.0
        subtitle = Mock()
        self.player.on_subtitle = subtitle
        self.model.StartRandomMotion.return_value = False
        text = "文" * 120
        segment = dict(
            type="play_segment",
            chat_id="a",
            turn_id="t",
            text=text,
            audio_path="NO_AUDIO",
            emotion="LABEL_0",
        )
        with patch(
            "runtime.single_character_performance.time.monotonic", return_value=10.0
        ):
            self.player.command(segment, self.model)
            self.player.update_playback(self.model)
        self.assertIsNone(self.player.subtitle_deadline)
        self.assertEqual(self.player.text_segment_deadline, 30.0)
        self.assertTrue(self.player.busy)
        with patch(
            "runtime.single_character_performance.time.monotonic", return_value=29.0
        ):
            self.player.update_playback(self.model)
        subtitle.assert_called_once_with(text)
        with patch(
            "runtime.single_character_performance.time.monotonic", return_value=31.0
        ):
            self.player.update_playback(self.model)
        self.assertEqual(subtitle.call_args.args, ("",))
        self.assertFalse(self.player.busy)
        self.player.command(segment, self.model)
        with patch(
            "runtime.single_character_performance.time.monotonic", return_value=40.0
        ):
            self.player.update_playback(self.model)
        self.player.command(
            dict(type="thinking", chat_id="a", turn_id="next"), self.model
        )
        self.assertIsNone(self.player.subtitle_deadline)

    def test_voiced_subtitle_timer_starts_only_after_playback(self) -> None:
        """真实音频仍在播放时不计时，结束后再保留六秒。"""
        self.player.subtitle_hide_delay = 6.0
        self.model.StartRandomMotion.return_value = False
        segment = dict(
            type="play_segment",
            chat_id="a",
            turn_id="t",
            text="回复",
            audio_path="voice.wav",
            emotion="LABEL_0",
        )
        with (
            patch.object(self.player, "onStartCallback_emotion_version"),
            patch.object(self.player, "audio_busy", return_value=True),
            patch.object(self.player.wavHandler, "Update", return_value=False),
        ):
            self.player.command(segment, self.model)
            self.player.update_playback(self.model)
            self.assertIsNone(self.player.subtitle_deadline)
        with patch(
            "runtime.single_character_performance.time.monotonic", return_value=100.0
        ):
            self.player.update_playback(self.model)
        self.assertEqual(self.player.subtitle_deadline, 106.0)
