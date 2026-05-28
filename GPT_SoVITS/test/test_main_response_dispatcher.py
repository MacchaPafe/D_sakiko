from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from chat_flow.audio_synthesizer import ChatAudioSettings
from chat_flow.audio_synthesizer import ChatAudioSynthesisResult
from chat_flow.audio_synthesizer import NO_AUDIO
from chat_flow.dispatcher import MainResponseDispatcher
from chat_flow.events import ConversationUiEventType
from chat_flow.events import DispatcherInputEvent
from chat_flow.events import DispatcherInputEventType
from chat_flow.events import TurnStatus
from chat_flow.events import UiMessageEventType


class FakeQueue:
    """记录测试期间被写入的队列项目。"""

    def __init__(self) -> None:
        """初始化空项目列表。"""
        self.items: list[object] = []

    def put(self, item: object) -> None:
        """记录一次队列写入。"""
        self.items.append(item)


class FakeInputQueue:
    """提供测试用输入队列。"""

    def __init__(self, items: list[object]) -> None:
        """保存待读取项目。"""
        self.items = list(items)

    def get(self) -> object:
        """读取下一个项目。"""
        return self.items.pop(0)

    def empty(self) -> bool:
        """返回是否仍有项目。"""
        return not self.items


class FakeSynthesizer:
    """按顺序返回预设语音合成结果。"""

    def __init__(self, results: list[ChatAudioSynthesisResult]) -> None:
        """保存预设结果和调用记录。"""
        self.results = results
        self.calls: list[tuple[str, ChatAudioSettings]] = []

    def synthesize_segment(self, *, draft, character, settings, status_callback):
        """返回下一条预设结果。"""
        self.calls.append((draft.text, settings))
        return self.results[len(self.calls) - 1]


class FakeStateUpdater:
    """记录 dispatcher 请求的状态写入。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.segment_results: list[tuple[str, int, str, str]] = []
        self.remaining_marks: list[tuple[str, int]] = []

    def set_segment_result(
        self,
        *,
        chat_id: str,
        message_index: int,
        audio_path: str,
        translation: str,
    ):
        """记录片段生成结果写入。"""
        self.segment_results.append((chat_id, message_index, audio_path, translation))
        return FakeUpdateResult(True, 1)

    def mark_remaining_no_audio(self, *, chat_id: str, segment_drafts: list[object], start_index: int = 0):
        """记录剩余无音频标记。"""
        self.remaining_marks.append((chat_id, start_index))
        return FakeUpdateResult(True, len(segment_drafts[start_index:]))


class FakeUpdateResult:
    """提供测试用更新结果。"""

    def __init__(self, success: bool, updated_count: int) -> None:
        """保存结果字段。"""
        self.success = success
        self.updated_count = updated_count


class FakePlaybackGate:
    """记录播放等待次数，并可模拟取消。"""

    def __init__(self, wait_results: list[bool] | None = None) -> None:
        """保存等待结果。"""
        self.wait_results = wait_results or []
        self.wait_count = 0

    def wait_until_ready(self, should_cancel) -> bool:
        """返回预设等待结果。"""
        self.wait_count += 1
        if should_cancel():
            return False
        if self.wait_results:
            return self.wait_results.pop(0)
        return True


class FakeCancellationProvider:
    """提供可控的取消状态。"""

    def __init__(self, cancel_after_checks: int | None = None) -> None:
        """保存取消触发检查次数。"""
        self.cancel_after_checks = cancel_after_checks
        self.check_count = 0
        self.cleared: list[tuple[str, str]] = []

    def is_cancelled(self, chat_id: str, turn_id: str) -> bool:
        """按检查次数返回是否取消。"""
        self.check_count += 1
        return self.cancel_after_checks is not None and self.check_count >= self.cancel_after_checks

    def clear_cancelled(self, chat_id: str, turn_id: str) -> None:
        """记录取消状态清理。"""
        self.cleared.append((chat_id, turn_id))


class FakeThinkingIndicator:
    """记录停止思考指示器的次数。"""

    def __init__(self) -> None:
        """初始化停止次数。"""
        self.stop_count = 0

    def stop(self) -> None:
        """记录一次停止动作。"""
        self.stop_count += 1


class FakeAudioWorker:
    """记录音频 worker 关闭动作。"""

    def __init__(self) -> None:
        """初始化关闭次数。"""
        self.shutdown_count = 0

    def shutdown(self) -> None:
        """记录一次关闭动作。"""
        self.shutdown_count += 1


class FakeLive2DProcess:
    """记录 Live2D 进程等待动作。"""

    def __init__(self) -> None:
        """初始化 join 次数。"""
        self.join_count = 0

    def join(self) -> None:
        """记录一次等待动作。"""
        self.join_count += 1


class MainResponseDispatcherTestCase(unittest.TestCase):
    """验证主回复 dispatcher 的同步调度行为。"""

    def test_process_model_response_dispatches_multi_segment_flow(self) -> None:
        """正常多段回复应按阶段、播放、展示、完成顺序调度。"""
        ui_queue = FakeQueue()
        playback_queue = FakeQueue()
        emotion_queue = FakeQueue()
        completion_queue = FakeQueue()
        state_updater = FakeStateUpdater()
        thinking = FakeThinkingIndicator()
        dispatcher = self._dispatcher(
            ui_queue=ui_queue,
            playback_queue=playback_queue,
            emotion_queue=emotion_queue,
            completion_queue=completion_queue,
            state_updater=state_updater,
            thinking_indicator=thinking,
            synthesizer=FakeSynthesizer([
                ChatAudioSynthesisResult("/tmp/one.wav", "/tmp/one.wav", True),
                ChatAudioSynthesisResult(NO_AUDIO, "/tmp/silence.wav", False),
            ]),
        )

        dispatcher.process_model_response(self._model_response_payload())

        self.assertEqual([item["type"] for item in ui_queue.items], [
            ConversationUiEventType.ASSISTANT_TURN_PHASE.value,
            ConversationUiEventType.ASSISTANT_SEGMENT_READY.value,
            ConversationUiEventType.ASSISTANT_SEGMENT_READY.value,
            ConversationUiEventType.ASSISTANT_TURN_COMPLETE.value,
        ])
        self.assertEqual(playback_queue.items, ["/tmp/one.wav", "/tmp/silence.wav"])
        self.assertEqual(emotion_queue.items, ["LABEL_0", "LABEL_1"])
        self.assertEqual(completion_queue.items, ["yes"])
        self.assertEqual(thinking.stop_count, 1)
        self.assertEqual(state_updater.segment_results, [
            ("chat-1", 0, "/tmp/one.wav", "译一"),
            ("chat-1", 1, NO_AUDIO, "译二"),
        ])

    def test_process_model_response_marks_remaining_no_audio_on_cancellation(self) -> None:
        """取消时应标记剩余片段、发送取消完成事件并清理取消状态。"""
        ui_queue = FakeQueue()
        completion_queue = FakeQueue()
        state_updater = FakeStateUpdater()
        cancellation = FakeCancellationProvider(cancel_after_checks=1)
        dispatcher = self._dispatcher(
            ui_queue=ui_queue,
            completion_queue=completion_queue,
            state_updater=state_updater,
            cancellation_provider=cancellation,
        )

        dispatcher.process_model_response(self._model_response_payload())

        self.assertEqual(state_updater.remaining_marks, [("chat-1", 0)])
        self.assertEqual(ui_queue.items[-1]["status"], TurnStatus.CANCELLED.value)
        self.assertEqual(completion_queue.items, ["yes"])
        self.assertEqual(cancellation.cleared, [("chat-1", "turn-1")])

    def test_process_model_response_signals_completion_on_synthesis_fallback(self) -> None:
        """语音合成降级仍应写入 NO_AUDIO、发送展示事件并完成轮次。"""
        ui_queue = FakeQueue()
        completion_queue = FakeQueue()
        state_updater = FakeStateUpdater()
        dispatcher = self._dispatcher(
            ui_queue=ui_queue,
            completion_queue=completion_queue,
            state_updater=state_updater,
            synthesizer=FakeSynthesizer([
                ChatAudioSynthesisResult(NO_AUDIO, "/tmp/silence.wav", False),
                ChatAudioSynthesisResult(NO_AUDIO, "/tmp/silence.wav", False),
            ]),
        )

        dispatcher.process_model_response(self._model_response_payload())

        self.assertEqual(completion_queue.items, ["yes"])
        self.assertEqual(state_updater.segment_results[0][2], NO_AUDIO)
        self.assertEqual(ui_queue.items[-1]["status"], TurnStatus.OK.value)

    def test_loop_once_reads_dispatcher_input_event(self) -> None:
        """循环包装器应从输入队列读取 typed dispatcher 事件。"""
        ui_queue = FakeQueue()
        input_queue = FakeInputQueue([
            DispatcherInputEvent(
                DispatcherInputEventType.MODEL_RESPONSE,
                self._model_response_payload(include_type=False),
            ).to_dict()
        ])
        dispatcher = self._dispatcher(input_queue=input_queue, ui_queue=ui_queue)

        handled = dispatcher.run_loop_once()

        self.assertTrue(handled)
        self.assertFalse(input_queue.items)
        self.assertEqual(ui_queue.items[-1]["type"], ConversationUiEventType.ASSISTANT_TURN_COMPLETE.value)

    def test_loop_once_rejects_old_bare_text_payload_without_stopping(self) -> None:
        """旧裸文本回复载荷应被报告为错误并跳过。"""
        status_queue = FakeQueue()
        input_queue = FakeInputQueue(["旧版文本回复"])
        dispatcher = self._dispatcher(input_queue=input_queue, status_queue=status_queue)

        handled = dispatcher.run_loop_once()

        self.assertTrue(handled)
        self.assertEqual(status_queue.items, ["收到无效调度器输入，已跳过。"])

    def test_loop_once_rejects_unknown_dict_payload_without_stopping(self) -> None:
        """未知字典载荷应被报告为错误并跳过。"""
        status_queue = FakeQueue()
        input_queue = FakeInputQueue([{"type": "unknown"}])
        dispatcher = self._dispatcher(input_queue=input_queue, status_queue=status_queue)

        handled = dispatcher.run_loop_once()

        self.assertTrue(handled)
        self.assertEqual(status_queue.items, ["收到无效调度器输入，已跳过。"])

    def test_loop_once_handles_application_exit_sequence(self) -> None:
        """应用退出事件应展示临时再见、关闭音频、请求 Live2D 退出并通知 Qt。"""
        ui_queue = FakeQueue()
        ui_message_queue = FakeQueue()
        live2d_queue = FakeQueue()
        audio_worker = FakeAudioWorker()
        live2d_process = FakeLive2DProcess()
        input_queue = FakeInputQueue([
            DispatcherInputEvent(
                DispatcherInputEventType.EXIT,
                {"character_name": "祥子"},
            ).to_dict()
        ])
        dispatcher = self._dispatcher(
            input_queue=input_queue,
            ui_queue=ui_queue,
            ui_message_queue=ui_message_queue,
            live2d_exit_queue=live2d_queue,
            audio_worker=audio_worker,
            live2d_process=live2d_process,
        )

        handled = dispatcher.run_loop_once()

        self.assertTrue(handled)
        self.assertTrue(dispatcher.should_stop)
        self.assertEqual(ui_queue.items[0]["type"], ConversationUiEventType.TRANSIENT_CONVERSATION_MESSAGE.value)
        self.assertEqual(ui_queue.items[0]["character_name"], "祥子")
        self.assertEqual(ui_queue.items[0]["text"], "（再见）")
        self.assertEqual(audio_worker.shutdown_count, 1)
        self.assertEqual(live2d_queue.items, ["bye"])
        self.assertEqual(live2d_process.join_count, 1)
        self.assertEqual(ui_message_queue.items[0].event_type, UiMessageEventType.APPLICATION_QUIT)

    def _dispatcher(
        self,
        *,
        input_queue: FakeInputQueue | None = None,
        ui_queue: FakeQueue | None = None,
        status_queue: FakeQueue | None = None,
        playback_queue: FakeQueue | None = None,
        emotion_queue: FakeQueue | None = None,
        completion_queue: FakeQueue | None = None,
        ui_message_queue: FakeQueue | None = None,
        live2d_exit_queue: FakeQueue | None = None,
        audio_worker: FakeAudioWorker | None = None,
        live2d_process: FakeLive2DProcess | None = None,
        state_updater: FakeStateUpdater | None = None,
        synthesizer: FakeSynthesizer | None = None,
        playback_gate: FakePlaybackGate | None = None,
        cancellation_provider: FakeCancellationProvider | None = None,
        thinking_indicator: FakeThinkingIndicator | None = None,
    ) -> MainResponseDispatcher:
        """创建带默认 fake 依赖的 dispatcher。"""
        return MainResponseDispatcher(
            input_queue=input_queue or FakeInputQueue([]),
            conversation_ui_queue=ui_queue or FakeQueue(),
            status_queue=status_queue or FakeQueue(),
            playback_audio_queue=playback_queue or FakeQueue(),
            emotion_queue=emotion_queue or FakeQueue(),
            completion_queue=completion_queue or FakeQueue(),
            ui_message_queue=ui_message_queue or FakeQueue(),
            live2d_exit_queue=live2d_exit_queue or FakeQueue(),
            audio_worker_shutdown=(audio_worker or FakeAudioWorker()).shutdown,
            live2d_process_join=(live2d_process or FakeLive2DProcess()).join,
            synthesizer=synthesizer or FakeSynthesizer([
                ChatAudioSynthesisResult("/tmp/one.wav", "/tmp/one.wav", True),
                ChatAudioSynthesisResult("/tmp/two.wav", "/tmp/two.wav", True),
            ]),
            state_updater=state_updater or FakeStateUpdater(),
            character_resolver=lambda _name: object(),
            playback_gate=playback_gate or FakePlaybackGate(),
            cancellation_provider=cancellation_provider or FakeCancellationProvider(),
            thinking_indicator=thinking_indicator or FakeThinkingIndicator(),
        )

    def _model_response_payload(self, *, include_type: bool = True) -> dict[str, object]:
        """构造测试用模型回复事件 payload。"""
        payload: dict[str, object] = {
            "chat_id": "chat-1",
            "turn_id": "turn-1",
            "character_name": "祥子",
            "sakiko_state": True,
            "audio_language_choice": "日英混合",
            "if_generate_audio": True,
            "turn_complete": True,
            "segments": [
                {
                    "message_index": 0,
                    "text": "一",
                    "translation": "译一",
                    "emotion": "LABEL_0",
                    "force_no_audio": False,
                },
                {
                    "message_index": 1,
                    "text": "二",
                    "translation": "译二",
                    "emotion": "LABEL_1",
                    "force_no_audio": False,
                },
            ],
        }
        if include_type:
            payload["type"] = "model_response"
        return payload


if __name__ == "__main__":
    unittest.main()
