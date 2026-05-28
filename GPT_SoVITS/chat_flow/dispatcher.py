from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from chat_flow.events import ApplicationQuitUiMessageEvent
from chat_flow.audio_synthesizer import ChatAudioSettings
from chat_flow.audio_synthesizer import ChatAudioSynthesizer
from chat_flow.events import AssistantSegmentDraft
from chat_flow.events import ConversationUiEvent
from chat_flow.events import ConversationUiEventType
from chat_flow.events import DispatcherInputEvent
from chat_flow.events import DispatcherInputEventType
from chat_flow.events import JsonValue
from chat_flow.events import TransientConversationMessageEvent
from chat_flow.events import TurnPhase
from chat_flow.events import TurnStatus
from chat_flow.state_updater import ConversationStateUpdater


class QueueReader(Protocol):
    """描述 dispatcher 输入队列需要的读取接口。"""

    def empty(self) -> bool:
        """返回队列是否为空。"""

    def get(self) -> object:
        """读取一个队列载荷。"""


class QueueWriter(Protocol):
    """描述 dispatcher 输出队列需要的写入接口。"""

    def put(self, item: object) -> None:
        """写入一个队列载荷。"""


class PlaybackGate(Protocol):
    """描述等待 Live2D 播放空闲的抽象接口。"""

    def wait_until_ready(self, should_cancel: Callable[[], bool]) -> bool:
        """等待播放门打开，若等待期间取消则返回 False。"""


class CancellationProvider(Protocol):
    """描述查询和清理对话取消状态的接口。"""

    def is_cancelled(self, chat_id: str, turn_id: str) -> bool:
        """返回指定轮次是否已取消。"""

    def clear_cancelled(self, chat_id: str, turn_id: str) -> None:
        """清理指定轮次的取消状态。"""


class ThinkingIndicator(Protocol):
    """描述停止 Live2D 思考指示器的接口。"""

    def stop(self) -> None:
        """停止思考指示器。"""


@dataclass(frozen=True)
class ModelResponseSnapshot:
    """保存 dispatcher 处理模型回复所需的不可变快照。"""

    chat_id: str
    turn_id: str
    character_name: str
    sakiko_state: bool
    audio_language_choice: str
    audio_enabled: bool
    turn_complete: bool
    segments: list[AssistantSegmentDraft]


class MotionValuePlaybackGate:
    """用现有 Live2D motion_complete_value 实现播放等待门。"""

    def __init__(self, motion_complete_value: object, *, poll_interval: float = 0.2) -> None:
        """保存共享播放完成值和轮询间隔。"""
        self._motion_complete_value = motion_complete_value
        self._poll_interval = poll_interval

    def wait_until_ready(self, should_cancel: Callable[[], bool]) -> bool:
        """等待 Live2D 播放完成；取消时提前返回 False。"""
        while not bool(getattr(self._motion_complete_value, "value", False)):
            if should_cancel():
                return False
            time.sleep(self._poll_interval)
        return True


class DpChatCancellationProvider:
    """用现有文本生成对象适配取消状态查询。"""

    def __init__(self, dp_chat: object) -> None:
        """保存提供取消方法的文本生成对象。"""
        self._dp_chat = dp_chat

    def is_cancelled(self, chat_id: str, turn_id: str) -> bool:
        """查询当前轮次是否已取消。"""
        checker = getattr(self._dp_chat, "is_turn_cancelled", None)
        if not callable(checker):
            return False
        return bool(checker(chat_id, turn_id))

    def clear_cancelled(self, chat_id: str, turn_id: str) -> None:
        """清理已完成收尾的取消状态。"""
        clearer = getattr(self._dp_chat, "clear_cancelled_turn", None)
        if callable(clearer):
            clearer(chat_id, turn_id)


class QueueThinkingIndicator:
    """用现有思考队列消费一次停止信号。"""

    def __init__(self, queue: object) -> None:
        """保存思考指示器队列。"""
        self._queue = queue

    def stop(self) -> None:
        """消费一次思考队列信号；失败时保持静默。"""
        try:
            getter = getattr(self._queue, "get", None)
            if callable(getter):
                getter()
        except Exception:
            return


class MainResponseDispatcher:
    """包装主回复队列循环，并同步处理模型回复事件。"""

    def __init__(
        self,
        *,
        input_queue: QueueReader,
        conversation_ui_queue: QueueWriter,
        status_queue: QueueWriter,
        playback_audio_queue: QueueWriter,
        emotion_queue: QueueWriter,
        completion_queue: QueueWriter,
        synthesizer: ChatAudioSynthesizer,
        state_updater: ConversationStateUpdater,
        character_resolver: Callable[[str], object | None],
        playback_gate: PlaybackGate,
        cancellation_provider: CancellationProvider,
        thinking_indicator: ThinkingIndicator,
        ui_message_queue: QueueWriter | None = None,
        live2d_exit_queue: QueueWriter | None = None,
        audio_worker_shutdown: Callable[[], None] | None = None,
        live2d_process_join: Callable[[], None] | None = None,
    ) -> None:
        """保存 dispatcher 的队列和可替换依赖。"""
        self._input_queue = input_queue
        self._conversation_ui_queue = conversation_ui_queue
        self._status_queue = status_queue
        self._playback_audio_queue = playback_audio_queue
        self._emotion_queue = emotion_queue
        self._completion_queue = completion_queue
        self._ui_message_queue = ui_message_queue
        self._live2d_exit_queue = live2d_exit_queue
        self._audio_worker_shutdown = audio_worker_shutdown
        self._live2d_process_join = live2d_process_join
        self._synthesizer = synthesizer
        self._state_updater = state_updater
        self._character_resolver = character_resolver
        self._playback_gate = playback_gate
        self._cancellation_provider = cancellation_provider
        self._thinking_indicator = thinking_indicator
        self.should_stop = False

    def run_loop_once(self) -> bool:
        """处理输入队列中的一个事件；队列为空时返回 False。"""
        if self._input_queue.empty():
            return False
        payload = self._input_queue.get()
        try:
            event = DispatcherInputEvent.from_payload(payload)
        except (TypeError, ValueError):
            self._status_queue.put("收到无效调度器输入，已跳过。")
            return True
        if event.event_type == DispatcherInputEventType.MODEL_RESPONSE:
            self.process_model_response(event.to_dict())
            return True
        if event.event_type == DispatcherInputEventType.EXIT:
            self._handle_exit_requested(event.data)
            return True
        return True

    def _handle_exit_requested(self, data: dict[str, JsonValue]) -> None:
        """处理应用退出请求事件。"""
        character_name = str(data.get("character_name") or "")
        self._conversation_ui_queue.put(TransientConversationMessageEvent(
            character_name=character_name,
            text="（再见）",
        ).to_dict())
        if self._audio_worker_shutdown is not None:
            self._audio_worker_shutdown()
        if self._live2d_exit_queue is not None:
            self._live2d_exit_queue.put("bye")
        if self._live2d_process_join is not None:
            self._live2d_process_join()
        if self._ui_message_queue is not None:
            self._ui_message_queue.put(ApplicationQuitUiMessageEvent())
        self.should_stop = True

    def process_model_response(self, payload: dict[str, object]) -> None:
        """同步处理一个模型回复事件。"""
        snapshot = self._parse_model_response(payload)
        character = self._character_resolver(snapshot.character_name)
        if character is None:
            self._completion_queue.put("yes")
            if snapshot.turn_complete:
                self._emit_turn_complete(snapshot, TurnStatus.ERROR)
            return

        turn_status = TurnStatus.OK
        first_segment = True
        self._emit_turn_phase(snapshot, TurnPhase.TTS)
        try:
            for index, draft in enumerate(snapshot.segments):
                if self._is_cancelled(snapshot):
                    turn_status = TurnStatus.CANCELLED
                    self._state_updater.mark_remaining_no_audio(
                        chat_id=snapshot.chat_id,
                        segment_drafts=snapshot.segments,
                        start_index=index,
                    )
                    break

                self._status_queue.put(f"正在合成语音...{index + 1}/{len(snapshot.segments)}")
                result = self._synthesizer.synthesize_segment(
                    draft=draft,
                    character=character,
                    settings=ChatAudioSettings(
                        audio_enabled=snapshot.audio_enabled,
                        sakiko_state=snapshot.sakiko_state,
                        audio_language_choice=snapshot.audio_language_choice,
                    ),
                    status_callback=self._status_queue.put,
                )

                if first_segment:
                    self._thinking_indicator.stop()
                    first_segment = False

                if self._is_cancelled(snapshot):
                    turn_status = TurnStatus.CANCELLED
                    self._state_updater.mark_remaining_no_audio(
                        chat_id=snapshot.chat_id,
                        segment_drafts=snapshot.segments,
                        start_index=index,
                    )
                    break

                if not self._playback_gate.wait_until_ready(lambda: self._is_cancelled(snapshot)):
                    turn_status = TurnStatus.CANCELLED
                    self._state_updater.mark_remaining_no_audio(
                        chat_id=snapshot.chat_id,
                        segment_drafts=snapshot.segments,
                        start_index=index,
                    )
                    break

                self._state_updater.set_segment_result(
                    chat_id=snapshot.chat_id,
                    message_index=draft.message_index,
                    audio_path=result.display_audio_path,
                    translation=draft.translation,
                )
                self._playback_audio_queue.put(result.playback_audio_path)

                if not self._playback_gate.wait_until_ready(lambda: self._is_cancelled(snapshot)):
                    turn_status = TurnStatus.CANCELLED
                    self._state_updater.mark_remaining_no_audio(
                        chat_id=snapshot.chat_id,
                        segment_drafts=snapshot.segments,
                        start_index=index + 1,
                    )
                    break

                self._emit_segment_ready(snapshot, draft, result.display_audio_path)
                self._emotion_queue.put(draft.emotion)
        except Exception:
            turn_status = TurnStatus.ERROR
            self._status_queue.put("语音合成流程出错。")
        finally:
            if first_segment:
                self._thinking_indicator.stop()
            self._completion_queue.put("yes")
            if snapshot.turn_complete:
                self._emit_turn_complete(snapshot, turn_status)
            if turn_status == TurnStatus.CANCELLED:
                self._cancellation_provider.clear_cancelled(snapshot.chat_id, snapshot.turn_id)

    def _emit_turn_phase(self, snapshot: ModelResponseSnapshot, phase: TurnPhase) -> None:
        """发送 assistant 轮次阶段事件。"""
        self._conversation_ui_queue.put(ConversationUiEvent(
            event_type=ConversationUiEventType.ASSISTANT_TURN_PHASE,
            chat_id=snapshot.chat_id,
            turn_id=snapshot.turn_id,
            data={
                "phase": phase.value,
            },
        ).to_dict())

    def _emit_segment_ready(
        self,
        snapshot: ModelResponseSnapshot,
        draft: AssistantSegmentDraft,
        display_audio_path: str,
    ) -> None:
        """发送 assistant 片段可展示事件。"""
        self._conversation_ui_queue.put(ConversationUiEvent(
            event_type=ConversationUiEventType.ASSISTANT_SEGMENT_READY,
            chat_id=snapshot.chat_id,
            turn_id=snapshot.turn_id,
            data={
                "message_index": draft.message_index,
                "character_name": snapshot.character_name,
                "text": draft.text,
                "translation": draft.translation,
                "emotion": draft.emotion,
                "audio_path": display_audio_path,
            },
        ).to_dict())

    def _emit_turn_complete(self, snapshot: ModelResponseSnapshot, status: TurnStatus) -> None:
        """发送 assistant 轮次完成事件。"""
        self._conversation_ui_queue.put(ConversationUiEvent(
            event_type=ConversationUiEventType.ASSISTANT_TURN_COMPLETE,
            chat_id=snapshot.chat_id,
            turn_id=snapshot.turn_id,
            data={"status": status.value},
        ).to_dict())

    def _is_cancelled(self, snapshot: ModelResponseSnapshot) -> bool:
        """查询当前轮次是否已取消。"""
        return self._cancellation_provider.is_cancelled(snapshot.chat_id, snapshot.turn_id)

    def _parse_model_response(self, payload: dict[str, object]) -> ModelResponseSnapshot:
        """将队列 payload 解析为模型回复快照。"""
        segments_raw = payload.get("segments")
        if not isinstance(segments_raw, list):
            segments_raw = []
        segments = [self._parse_segment(item) for item in segments_raw if isinstance(item, dict)]
        return ModelResponseSnapshot(
            chat_id=str(payload.get("chat_id") or ""),
            turn_id=str(payload.get("turn_id") or ""),
            character_name=str(payload.get("character_name") or ""),
            sakiko_state=bool(payload.get("sakiko_state", False)),
            audio_language_choice=str(payload.get("audio_language_choice") or ""),
            audio_enabled=bool(payload.get("if_generate_audio", True)),
            turn_complete=bool(payload.get("turn_complete", True)),
            segments=segments,
        )

    @staticmethod
    def _parse_segment(payload: dict[str, object]) -> AssistantSegmentDraft:
        """将片段 payload 解析为 assistant 片段草稿。"""
        message_index = payload.get("message_index")
        return AssistantSegmentDraft(
            message_index=message_index if isinstance(message_index, int) else -1,
            text=str(payload.get("text") or ""),
            translation=str(payload.get("translation") or ""),
            emotion=str(payload.get("emotion") or "LABEL_0"),
            force_no_audio=bool(payload.get("force_no_audio", False)),
        )
