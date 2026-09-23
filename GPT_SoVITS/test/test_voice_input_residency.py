"""验证语音输入独立计时、异步回收和一次点击恢复录音。"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from unittest import TestCase
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from runtime.drafts import DraftStore
from runtime.voice_input import ASR_IDLE_TIMEOUT_MS, VoiceInputService


class VoiceResidencyTests(TestCase):
    """用可控后台工作代替模型和麦克风，不依赖音频设备。"""

    @classmethod
    def setUpClass(cls) -> None:
        """复用 Qt 应用以接收后台任务和空闲计时器信号。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """为每项测试提供独立草稿、模型和录音流。"""
        self.drafts = DraftStore()
        self.model = Mock(closed=False)
        self.stream = Mock()
        self.loader = Mock(return_value=self.model)
        self.service = VoiceInputService(
            self.drafts,
            model_loader=self.loader,
            stream_factory=Mock(return_value=self.stream),
        )
        self.addCleanup(self.service.close)

    def wait_for(self, predicate: Callable[[], bool]) -> None:
        """有界处理 Qt 事件，等待后台任务可观察的完成状态。"""
        for _ in range(100):
            if predicate():
                return
            QTest.qWait(10)
        self.assertTrue(predicate(), "后台任务未在一秒内完成")

    def load_model(self) -> None:
        """完成初次预加载，保持与程序启动路径一致。"""
        self.service.load()
        self.wait_for(lambda: self.service.state == "idle")

    def test_idle_timer_releases_process_off_gui_thread_and_wakes_once(self) -> None:
        """默认两分钟计时独立于草稿，释放后一次点击即可恢复录音。"""
        self.load_model()
        self.assertEqual(ASR_IDLE_TIMEOUT_MS, 120_000)
        self.assertEqual(self.service.idle_timer.interval(), 120_000)
        self.assertEqual(self.service.idle_timer.timerType(), Qt.PreciseTimer)
        threads: list[int] = []

        def close_model() -> None:
            """记录进程关闭实际执行的线程。"""
            threads.append(threading.get_ident())

        self.model.close.side_effect = close_model
        self.service.idle_timer.start(40)
        for _ in range(3):
            QTest.qWait(10)
            self.drafts.set("a", str(_), [])
        self.wait_for(lambda: self.service.state == "dormant")
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertIsNone(self.service.model)
        self.assertFalse(self.service.closed)
        self.service.toggle("a", 1)
        self.wait_for(lambda: self.service.state == "recording")
        self.assertEqual(self.loader.call_count, 2)
        self.stream.start.assert_called_once()
        self.assertFalse(self.service.idle_timer.isActive())

    def test_recording_and_transcribing_are_not_reclaimed(self) -> None:
        """长录音与识别期间不回收，结果完成后重新获得完整空闲期。"""
        self.load_model()
        self.service.start("a", 0)
        self.service._release_idle_model()
        self.model.close.assert_not_called()
        self.assertFalse(self.service.idle_timer.isActive())
        gate = threading.Event()
        self.addCleanup(gate.set)

        def transcribe(*args: object, **kwargs: object) -> tuple[list[object], None]:
            """暂停识别以检查进行中的任务不被计时器回收。"""
            if not gate.wait(2):
                raise TimeoutError("测试识别等待超时")
            return [], None

        self.model.transcribe.side_effect = transcribe
        self.service._audio(np.ones((3200, 1), dtype=np.float32), 3200, None, None)
        self.service.finish()
        self.service._release_idle_model()
        self.assertEqual(self.service.state, "transcribing")
        self.model.close.assert_not_called()
        self.assertFalse(self.service.idle_timer.isActive())
        gate.set()
        self.wait_for(lambda: self.service.state == "idle")
        self.assertGreater(self.service.idle_timer.remainingTime(), 119_000)

    def test_click_during_release_waits_then_loads_without_overlap(self) -> None:
        """旧模型释放完成前缓存录音意图，不同时运行两个模型进程。"""
        self.load_model()
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.model.close.side_effect = lambda: gate.wait(2)
        self.service._release_idle_model()
        self.service.toggle("a", 0)
        self.service.load()
        self.assertEqual(self.service.state, "preparing")
        self.assertEqual(self.loader.call_count, 1)
        gate.set()
        self.wait_for(lambda: self.service.state == "recording")
        self.assertEqual(self.loader.call_count, 2)
        self.stream.start.assert_called_once()

    def test_loading_can_be_cancelled_and_duplicate_load_is_ignored(self) -> None:
        """取消预备录音只取消用户意图，加载结果可以继续空闲回收。"""
        gate = threading.Event()
        self.addCleanup(gate.set)

        def load_model() -> Mock:
            """暂停模型加载以模拟用户在准备期间取消。"""
            gate.wait(2)
            return self.model

        self.loader.side_effect = load_model
        self.service.toggle("a", 0)
        self.service.load()
        self.assertEqual(self.service.state, "preparing")
        self.service.toggle("b", 0)
        gate.set()
        self.wait_for(lambda: self.service.state == "idle")
        self.loader.assert_called_once()
        self.stream.start.assert_not_called()
        self.assertTrue(self.service.idle_timer.isActive())

    def test_reload_keeps_clicked_draft_revision_and_cursor(self) -> None:
        """加载期间草稿变化或切换对话时，识别仍写回发起时的归属。"""
        self.drafts.set("a", "旧草稿", [])
        original_revision = self.drafts.get("a").text_revision
        gate = threading.Event()
        self.addCleanup(gate.set)

        def load_model() -> Mock:
            """模拟恢复模型时仍可操作其他对话。"""
            gate.wait(2)
            return self.model

        self.loader.side_effect = load_model
        self.service.toggle("a", 1)
        self.drafts.set("a", "已编辑", [])
        self.drafts.set("b", "另一个对话", [])
        gate.set()
        self.wait_for(lambda: self.service.state == "recording")
        self.assertEqual(self.service.target, ("a", original_revision, 1))
        self.service._close_stream()
        self.service._accept_result((self.service.target, "识别内容", None))
        self.assertEqual(self.drafts.get("a").text, "已编辑识别内容")
        self.assertEqual(self.drafts.get("b").text, "另一个对话")

    def test_shutdown_during_reload_discards_recording_intent(self) -> None:
        """退出后迟到的模型会被关闭，不能自动打开麦克风。"""
        gate = threading.Event()
        self.addCleanup(gate.set)

        def load_model() -> Mock:
            """推迟初始化结束直到服务已经关闭。"""
            gate.wait(2)
            return self.model

        self.loader.side_effect = load_model
        self.service.toggle("a", 0)
        self.service.close()
        gate.set()
        self.wait_for(lambda: self.model.close.called)
        self.assertEqual(self.service.state, "closed")
        self.stream.start.assert_not_called()
        self.assertFalse(self.service.idle_timer.isActive())

    def test_failed_reload_clears_intent_and_retry_records(self) -> None:
        """加载失败后仍可一键重试，不遗留失败会话的自动录音请求。"""
        self.loader.side_effect = [RuntimeError("模型暂不可用"), self.model]
        self.service.toggle("a", 0)
        self.wait_for(lambda: self.service.state == "unavailable")
        self.assertIsNone(self.service._pending_target)
        self.assertFalse(self.service.idle_timer.isActive())
        self.service.toggle("b", 0)
        self.wait_for(lambda: self.service.state == "recording")
        self.assertEqual(self.service.target[0], "b")

    def test_shutdown_during_release_does_not_reload(self) -> None:
        """释放过程中退出会取消待录音意图，后台关闭完成后不再唤醒。"""
        self.load_model()
        gate, finished = threading.Event(), threading.Event()
        self.addCleanup(gate.set)

        def close_model() -> None:
            """保持释放任务未完成，直到测试发起退出。"""
            gate.wait(2)
            finished.set()

        self.model.close.side_effect = close_model
        self.service._release_idle_model()
        self.service.toggle("a", 0)
        self.service.close()
        gate.set()
        self.wait_for(finished.is_set)
        QTest.qWait(20)
        self.assertEqual(self.service.state, "closed")
        self.loader.assert_called_once()
        self.stream.start.assert_not_called()
