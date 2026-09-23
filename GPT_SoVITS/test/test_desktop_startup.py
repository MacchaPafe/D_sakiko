"""验证按启动偏好选择唯一演出宿主、失败回退与首次桌宠退出。"""

from __future__ import annotations

import os
from queue import Queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QWidget

from desktop_pet.controller import DesktopController
from runtime.presentation import PresentationRouter


class StartupHost(QWidget):
    """提供控制器所需的聊天宿主接口，不启动网络或语音服务。"""

    def __init__(self) -> None:
        """构造可观察的服务和窗口显示状态。"""
        super().__init__()
        self.current_chat_id = "test"
        self.current_character = Mock()
        self.current_character.has_valid_voice_model.return_value = True
        self.dp_chat = SimpleNamespace(sakiko_state=True, if_generate_audio=True)
        self.audio_gen = Mock()
        self.voice_input = SimpleNamespace(state="idle")
        self.regen_thread = None
        self.chat_manager = Mock()


class DesktopStartupTests(TestCase):
    """使用真实控制器与路由器，替换图形宿主及普通窗口子进程。"""

    @classmethod
    def setUpClass(cls) -> None:
        """初始化共享离屏 Qt 应用。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """创建隔离的启动配置、演出队列和控制器。"""
        self.host = StartupHost()
        self.config = SimpleNamespace(
            start_in_pet_mode=SimpleNamespace(value=False),
            voice_output_enabled=SimpleNamespace(value=True, valueChanged=Mock()),
        )
        self.tray_patch = patch("desktop_pet.controller.QSystemTrayIcon")
        self.tray = self.tray_patch.start()
        self.addCleanup(self.tray_patch.stop)
        self.tray.isSystemTrayAvailable.return_value = True
        config_patch = patch("desktop_pet.controller.d_sakiko_config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.pet_patch = patch("desktop_pet.controller.PetWindow")
        self.pet_factory = self.pet_patch.start()
        self.addCleanup(self.pet_patch.stop)
        self.pet = self.pet_factory.return_value
        self.pet.renderer.isValid.return_value = True
        self.process = Mock()
        self.process.ready_event.wait.return_value = True
        self.process.is_alive.return_value = True
        self.router = PresentationRouter(Queue())
        self.router.factory = Mock(return_value=self.process)
        self.router.put(
            dict(
                type="switch_live2d",
                model_json="character.model3.json",
                sakiko_state=False,
            )
        )
        self.runtime = Mock(busy=False)
        self.events: Queue[dict[str, object]] = Queue()
        self.controller = DesktopController(
            self.host,
            self.router,
            self.events,
            Queue(),
            self.runtime,
            SimpleNamespace(value=True),
        )
        self.controller.poll.stop()
        self.controller.voice_poll.stop()
        self.addCleanup(self.host.close)
        self.addCleanup(self.host.deleteLater)

    def test_default_start_opens_normal_window_without_creating_pet(self) -> None:
        """默认只启动普通窗口，保留当前角色配置。"""
        self.controller.start()
        self.process.start.assert_called_once()
        self.pet_factory.assert_not_called()
        self.assertTrue(self.host.isVisible())
        self.assertFalse(self.controller.pet_mode)
        self.assertIs(self.router.process, self.process)
        self.assertEqual(
            self.router.normal_queue.get_nowait()["model_json"], "character.model3.json"
        )

    def test_pet_start_skips_normal_process_and_chat_show_then_tray_can_open_chat(
        self,
    ) -> None:
        """直接启动桌宠不闪出聊天窗口，托盘仍可打开聊天。"""
        self.config.start_in_pet_mode.value = True
        with patch.object(self.host, "show", wraps=self.host.show) as show:
            self.controller.start()
            show.assert_not_called()
        self.router.factory.assert_not_called()
        self.assertIsNone(self.router.process)
        self.assertTrue(self.controller.pet_mode)
        self.assertIs(self.router.target, self.controller.pet_commands)
        model = self.controller.pet_commands.get_nowait()
        self.assertEqual(model["model_json"], "character.model3.json")
        self.assertTrue(model["sakiko_state"])
        self.assertTrue(self.controller.pet_commands.empty())
        self.tray.return_value.activated.connect.call_args.args[0](self.tray.Trigger)
        self.assertTrue(self.host.isVisible())
        self.assertTrue(self.controller.pet_mode)
        self.host.audio_gen.update_voice_activity.assert_called_with(
            pet_mode=True, busy=False
        )

    def test_missing_tray_falls_back_without_changing_preference(self) -> None:
        """无托盘时打开普通窗口并提示，保留桌宠启动偏好。"""
        self.config.start_in_pet_mode.value = True
        self.tray.isSystemTrayAvailable.return_value = False
        with patch("desktop_pet.controller.QMessageBox.warning") as warning:
            self.controller.start()
        self.pet_factory.assert_not_called()
        self.process.start.assert_called_once()
        self.assertTrue(self.host.isVisible())
        self.assertFalse(self.controller.pet_mode)
        self.assertTrue(self.config.start_in_pet_mode.value)
        self.assertIn("托盘", warning.call_args.args[2])

    def test_invalid_opengl_releases_pet_and_falls_back(self) -> None:
        """OpenGL 初始化失败先清理临时桌宠，再启动普通窗口。"""
        self.config.start_in_pet_mode.value = True
        self.pet.renderer.isValid.return_value = False
        with patch("desktop_pet.controller.QMessageBox.warning") as warning:
            self.controller.start()
        self.pet.shutdown.assert_called_once()
        self.process.start.assert_called_once()
        self.assertIsNone(self.controller.pet)
        self.assertFalse(self.controller.pet_mode)
        self.assertIs(self.router.target, self.router.normal_queue)
        self.assertTrue(self.host.isVisible())
        self.assertIn("OpenGL", warning.call_args.args[2])

    def test_both_renderers_failing_still_shows_chat_and_reports_both_errors(
        self,
    ) -> None:
        """两种宿主均初始化失败也必须提供可见的恢复入口。"""
        self.config.start_in_pet_mode.value = True
        self.pet_factory.side_effect = RuntimeError("桌宠创建错误")
        self.router.factory.side_effect = RuntimeError("普通窗口创建错误")
        with patch("desktop_pet.controller.QMessageBox.warning") as warning:
            self.controller.start()
        self.assertTrue(self.host.isVisible())
        self.assertIn("桌宠创建错误", warning.call_args.args[2])
        self.assertIn("普通窗口创建错误", warning.call_args.args[2])

    def test_manual_switch_does_not_change_startup_preference(self) -> None:
        """启动桌宠后可首次创建普通窗口，临时切换不覆盖启动设置。"""
        self.config.start_in_pet_mode.value = True
        self.controller.start()
        self.controller.toggle_mode()
        self.assertFalse(self.controller.pet_mode)
        self.process.start.assert_called_once()
        self.pet.shutdown.assert_called_once()
        self.assertTrue(self.config.start_in_pet_mode.value)

    def test_live_preference_change_does_not_switch_current_mode(self) -> None:
        """配置只在启动时选择形态，运行中的偏好修改不切换宿主。"""
        self.controller.start()
        self.config.start_in_pet_mode.value = True
        self.controller.update_voice_activity()
        self.assertFalse(self.controller.pet_mode)
        self.pet_factory.assert_not_called()

    def test_direct_pet_start_can_wait_for_farewell_without_normal_process(
        self,
    ) -> None:
        """从未启动普通进程时仍能发送告别，等待动画回执后完成退出。"""
        self.config.start_in_pet_mode.value = True
        self.controller.start()
        self.controller.pet_commands.get_nowait()
        with (
            patch("runtime.storage_ui.save_before_close", return_value=True),
            patch.object(self.controller, "cleanup") as cleanup,
        ):
            self.controller.quit()
            self.assertTrue(self.controller._audio_shutdown_complete.wait(1.0))
            self.assertEqual(
                self.controller.pet_commands.get_nowait(), {"type": "farewell"}
            )
            self.controller.drain_events()
            cleanup.assert_not_called()
            self.events.put({"type": "farewell_complete"})
            self.controller.drain_events()
            cleanup.assert_called_once()
        self.router.factory.assert_not_called()
