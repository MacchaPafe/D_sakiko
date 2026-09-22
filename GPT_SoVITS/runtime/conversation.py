"""普通 Qt 与桌宠共享的轮次、TTS 和演出编排，不依赖聊天控件。"""

from __future__ import annotations

from copy import deepcopy
from collections import deque
import re
import threading
import uuid


def clean_text_for_audio(text: str) -> str:
    text = (
        re.sub(r"（.*?）|\(.*?\)|\[.*?]", "", text)
        .replace("「", "")
        .replace("」", "")
        .strip()
    )
    return text if text and not re.fullmatch(r"[\W_]+", text) else "不能送去合成"


class RuntimeEventSink:
    """提供生成器沿用的 Queue.put 接口，将完成事件交给公共运行时。"""

    def __init__(self, runtime):
        self.runtime = runtime

    def put(self, event):
        self.runtime.accept_event(event)


class RuntimeInputQueue:
    """让引擎的提醒回调也遵守公共忙碌状态；普通命令仍沿用原队列。"""

    def __init__(self, runtime):
        self.runtime = runtime

    def put(self, event):
        if isinstance(event, str):
            command = self.runtime.engine._normalize_input_command(event)
            if command and command.get("type") == "send_message":
                self.runtime.queue_internal(command["chat_id"], event)
                return
        self.runtime.commands.put(event)

    def get(self, *args, **kwargs):
        return self.runtime.commands.get(*args, **kwargs)

    def empty(self):
        return self.runtime.commands.empty()


class ConversationRuntime:
    def __init__(self, engine, audio, characters, commands, events, presentation):
        self.engine = engine
        self.audio = audio
        self.characters = {c.character_name: c for c in characters}
        self.commands = commands
        self.events = events
        self.presentation = presentation
        self.chat_manager = engine.chat_manager
        self._lock = threading.RLock()
        self.active = None
        self._pending = set()
        self._generation_done = False
        self._status = "ok"
        self._sequence = 0
        self._closed = False
        self.event_sink = RuntimeEventSink(self)
        self.input_queue = RuntimeInputQueue(self)
        self._internal = deque()

    def queue_internal(self, chat_id, text):
        with self._lock:
            if not self._closed:
                self._internal.append((chat_id, text))

    def take_internal(self, chat_id):
        with self._lock:
            if self.busy or self._closed:
                return None
            for item in tuple(self._internal):
                if self.chat_manager.get_chat_by_id(item[0]) is None:
                    self._internal.remove(item)
                elif item[0] == chat_id:
                    self._internal.remove(item)
                    return item[1]
            return None

    @property
    def busy(self):
        with self._lock:
            return self.active is not None

    def matches(self, event):
        with self._lock:
            return self.active == (
                str(event.get("chat_id") or ""),
                str(event.get("turn_id") or ""),
            )

    def submit(
        self,
        chat_id,
        text,
        *,
        append_user_message=True,
        image_source_paths=(),
        draft_attachments=(),
        worldbook_snapshot=None,
    ):
        with self._lock:
            if self._closed or self.active is not None:
                raise RuntimeError("请等待当前回复或播放完成。")
            if self.chat_manager.get_chat_by_id(chat_id) is None:
                raise ValueError("对话不存在。")
            turn_id = uuid.uuid4().hex
            payload = deepcopy(
                dict(
                    type="send_message",
                    chat_id=chat_id,
                    turn_id=turn_id,
                    text=text,
                    append_user_message=append_user_message,
                    image_source_paths=list(image_source_paths),
                    draft_attachments=list(draft_attachments),
                    worldbook_snapshot=worldbook_snapshot,
                )
            )
            self._submitted = payload
            self._committed = False
            self.active = (chat_id, turn_id)
            self._pending.clear()
            self._generation_done = False
            self._status = "ok"
            self._sequence = 0
            self._message_indices = set()
            self._voice_options = {
                key: float(value)
                for key, value in [
                    ("speed_factor", getattr(self.audio, "speed", None)),
                    ("fragment_interval", getattr(self.audio, "pause_second", None)),
                ]
                if isinstance(value, (int, float))
            }
            try:
                self.commands.put(payload)
                self.presentation.put(
                    dict(type="thinking", chat_id=chat_id, turn_id=turn_id)
                )
            except Exception:
                self.active = None
                raise
            return turn_id

    def switch_chat(self, chat_id):
        with self._lock:
            if self.busy:
                raise RuntimeError("回复尚未完成，不能切换对话。")
            self.commands.put(dict(type="switch_chat", chat_id=chat_id))

    def cancel(self):
        with self._lock:
            if self.active is None:
                return
            chat_id, turn_id = self.active
            self.engine.request_cancel_turn(chat_id, turn_id)
            chat = self.chat_manager.get_chat_by_id(chat_id)
            if chat is not None:
                for index in getattr(self, "_message_indices", set()):
                    if (
                        isinstance(index, int)
                        and 0 <= index < len(chat.message_list)
                        and not chat.message_list[index].audio_path
                    ):
                        chat.message_list[index].audio_path = "NO_AUDIO"
            self.presentation.put(
                dict(type="cancel_turn", chat_id=chat_id, turn_id=turn_id)
            )
            self.active = None
            self._pending.clear()

    def accept_event(self, event):
        with self._lock:
            if not isinstance(event, dict):
                self.events.put(event)
                return
            if event.get("type") == "user_message_committed":
                if self.matches(event):
                    self._committed = True
                # 保存成功是草稿确认，即使用户刚停止回复也必须确认原提交。
                self.events.put(event)
                return
            if event.get("type") == "assistant_turn_complete":
                if self.matches(event):
                    self._generation_done = True
                    self._status = str(event.get("status") or "ok")
                    self._finish_if_ready()
                return
            if event.get("turn_id") and not self.matches(event):
                return
            self.events.put(event)

    def playback_event(self, event):
        with self._lock:
            if not self.matches(event):
                return
            if event.get("type") in ("playback_complete", "playback_failed"):
                self._pending.discard(event.get("segment_id"))
                if event.get("type") == "playback_failed":
                    self.events.put(
                        dict(
                            type="assistant_turn_error",
                            chat_id=self.active[0],
                            turn_id=self.active[1],
                            message="音频播放失败，已保留文字。",
                        )
                    )
                self._finish_if_ready()

    def _finish_if_ready(self):
        if self.active is None or not self._generation_done or self._pending:
            return
        chat_id, turn_id = self.active
        status = self._status
        try:
            self.chat_manager.save()
        except Exception as error:
            status = "error"
            self.events.put(
                dict(
                    type="assistant_turn_error",
                    chat_id=chat_id,
                    turn_id=turn_id,
                    message=f"保存失败，内容仍在内存中：{error}",
                )
            )
        self.active = None
        self.presentation.put(
            dict(type="generation_finished", chat_id=chat_id, turn_id=turn_id)
        )
        self.events.put(
            dict(
                type="assistant_turn_complete",
                chat_id=chat_id,
                turn_id=turn_id,
                status=status,
            )
        )

    def process_response(self, payload):
        """在 TTS 工作线程中逐段处理，返回路径与轮次绑定，不依赖共享 audio_file_path。"""
        with self._lock:
            if not self.matches(payload):
                return
            character = self.characters.get(str(payload.get("character_name") or ""))
            segments = payload.get("segments")
            if character is None or not isinstance(segments, list):
                self.accept_event(
                    dict(payload, type="assistant_turn_complete", status="error")
                )
                return
            self._message_indices.update(
                s.get("message_index") for s in segments if isinstance(s, dict)
            )
            if not self._committed:
                self.accept_event(
                    dict(
                        type="user_message_committed",
                        chat_id=payload["chat_id"],
                        turn_id=payload["turn_id"],
                        draft_attachment_ids=[],
                    )
                )
            self.accept_event(
                dict(
                    payload,
                    type="assistant_turn_phase",
                    phase="tts",
                    message_indices=[
                        s.get("message_index") for s in segments if isinstance(s, dict)
                    ],
                )
            )
            voice_options = dict(self._voice_options)
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict) or not self.matches(payload):
                continue
            audio_path = "NO_AUDIO"
            if payload.get(
                "if_generate_audio", self.engine.if_generate_audio
            ) and not segment.get("force_no_audio"):
                for attempt in range(2):
                    if not self.matches(payload):
                        return
                    try:
                        audio_path = self.audio.generate_audio_for_character_sync(
                            clean_text_for_audio(str(segment.get("text") or "")),
                            character,
                            bool(payload.get("sakiko_state", self.engine.sakiko_state)),
                            str(
                                payload.get("audio_language_choice")
                                or self.engine.audio_language_choice
                            ),
                            segment_index=index + 1,
                            segment_total=len(segments),
                            emotion=str(segment.get("emotion") or "LABEL_0"),
                            pronunciation_overrides=segment.get(
                                "pronunciation_overrides"
                            )
                            or payload.get("pronunciation_overrides"),
                            generation_options=voice_options,
                        )
                        break
                    except Exception as error:
                        self.accept_event(
                            dict(
                                payload,
                                type="assistant_turn_error",
                                message=f"语音合成失败{'，重试中' if attempt == 0 else '，保留文字'}：{error}",
                            )
                        )
            with self._lock:
                if not self.matches(payload):
                    return
                self._sequence += 1
                event = dict(
                    type="assistant_segment_ready",
                    chat_id=payload["chat_id"],
                    turn_id=payload["turn_id"],
                    segment_id=self._sequence,
                    character_name=payload["character_name"],
                    message_index=segment.get("message_index", -1),
                    text=str(segment.get("text") or ""),
                    translation=str(segment.get("translation") or ""),
                    emotion=str(segment.get("emotion") or "LABEL_0"),
                    audio_path=audio_path or "NO_AUDIO",
                )
                chat = self.chat_manager.get_chat_by_id(payload["chat_id"])
                message_index = event["message_index"]
                if (
                    chat is not None
                    and isinstance(message_index, int)
                    and 0 <= message_index < len(chat.message_list)
                ):
                    chat.message_list[message_index].audio_path = event["audio_path"]
                    chat.message_list[message_index].translation = event["translation"]
                self._pending.add(self._sequence)
                self.events.put(event)
                self.presentation.put(dict(event, type="play_segment"))
        if payload.get("turn_complete", True):
            self.accept_event(
                dict(payload, type="assistant_turn_complete", status="ok")
            )

    def replay(self, chat_id, audio_path, emotion, text="", translation=""):
        with self._lock:
            if self.busy or self._closed:
                raise RuntimeError("请等待当前回复或播放完成。")
            turn_id = uuid.uuid4().hex
            self.active = (chat_id, turn_id)
            self._pending = {1}
            self._generation_done = True
            self._status = "ok"
            self.presentation.put(
                dict(
                    type="play_segment",
                    chat_id=chat_id,
                    turn_id=turn_id,
                    segment_id=1,
                    audio_path=audio_path,
                    emotion=emotion,
                    text=text,
                    translation=translation,
                )
            )

    def close(self):
        with self._lock:
            self.cancel()
            self._closed = True
