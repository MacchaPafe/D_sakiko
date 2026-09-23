"""验证退出时保留演出宿主，并行清理语音且有兜底超时。"""

from __future__ import annotations

from queue import Queue
import threading
from unittest import TestCase
from unittest.mock import Mock, patch

from desktop_pet.controller import DesktopController


class DesktopShutdownTests(TestCase):
    """不创建真实窗口或模型，检查应用控制器的退出协议。"""

    def setUp(self) -> None:
        """构造带可控事件队列的桌宠控制器宿主。"""
        self.controller = Mock(exiting=False, _farewell_complete=False)
        self.controller.playback_events = Queue()
        self.controller._audio_shutdown_complete = threading.Event()

    def test_quit_keeps_host_alive_until_both_animation_and_audio_complete(
        self,
    ) -> None:
        """告别回执和语音清理都完成后才关闭窗口，重复退出请求幂等。"""
        with (
            patch("runtime.storage_ui.save_before_close", return_value=True),
            patch("desktop_pet.controller.threading.Thread") as thread,
            patch("desktop_pet.controller.time.monotonic", return_value=100.0),
        ):
            DesktopController.quit(self.controller)
            DesktopController.quit(self.controller)
            self.controller.runtime.close.assert_called_once()
            self.controller.router.put.assert_called_once_with({"type": "farewell"})
            thread.return_value.start.assert_called_once()
            self.controller.cleanup.assert_not_called()
            self.controller.pet.shutdown.assert_not_called()
            self.controller.router.close.assert_not_called()
            self.controller.playback_events.put({"type": "playback_complete"})
            DesktopController.drain_events(self.controller)
            self.controller.cleanup.assert_not_called()
            self.controller.playback_events.put({"type": "farewell_complete"})
            DesktopController.drain_events(self.controller)
            self.controller.cleanup.assert_not_called()
            self.controller._audio_shutdown_complete.set()
            DesktopController.drain_events(self.controller)
            self.controller.cleanup.assert_called_once()

    def test_save_failure_leaves_app_running(self) -> None:
        """保存失败且用户未放弃时，不能清理运行时或播放告别。"""
        with patch("runtime.storage_ui.save_before_close", return_value=False):
            DesktopController.quit(self.controller)
        self.assertFalse(self.controller.exiting)
        self.controller.router.put.assert_not_called()
        self.controller.runtime.close.assert_not_called()

    def test_deadline_releases_host_without_animation_or_worker_reply(self) -> None:
        """渲染器或语音 worker 卡住时，达到期限进入强制清理。"""
        self.controller.exiting = True
        self.controller._shutdown_deadline = 10.0
        with patch("desktop_pet.controller.time.monotonic", return_value=10.0):
            DesktopController.drain_events(self.controller)
        self.controller.cleanup.assert_called_once()

    def test_missing_normal_renderer_does_not_wait_for_animation(self) -> None:
        """普通窗口已退出时跳过告别回执等待，仍等待语音清理完成。"""
        self.controller.pet = None
        self.controller.router.process = None
        with (
            patch("runtime.storage_ui.save_before_close", return_value=True),
            patch("desktop_pet.controller.threading.Thread"),
            patch("desktop_pet.controller.time.monotonic", return_value=100.0),
        ):
            DesktopController.quit(self.controller)
            self.assertTrue(self.controller._farewell_complete)
            self.controller._audio_shutdown_complete.set()
            DesktopController.drain_events(self.controller)
        self.controller.cleanup.assert_called_once()
