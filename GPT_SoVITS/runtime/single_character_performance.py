"""单角色声音、口型与动作协调；宿主负责窗口和 OpenGL 上下文。"""

from __future__ import annotations
from collections import deque
import os
import time
import wave
import pygame
from live2d.utils.lipsync import WavHandler
from live2d_support.motion_semantics import motion_group_for_emotion
from live2d_support.runtime_adapter import Live2DModelProtocol
from log import get_logger


def has_voice_audio(path: object) -> bool:
    """统一识别缺失音频与合成器的静音占位文件。"""
    return (
        isinstance(path, str) and bool(path) and path != "NO_AUDIO"
        and not os.path.normpath(path).endswith(os.path.join("silent_audio", "silence.wav"))
    )


class SingleCharacterPerformance:
    def __init__(self):
        self.motion_is_over = False
        self.wavHandler = WavHandler()
        self.lipSyncN: float = 1.4
        self.live2d_this_turn_motion_complete = True
        self.think_motion_is_over = True
        self.run = True
        self.sakiko_state = True
        self.if_sakiko = False
        self.if_mask = True
        self.is_display_text = True
        self.new_text = ""

        # 解决睁眼太快的突兀问题，强制睁眼过渡
        self.force_eyes_open = False
        self.eye_open_pending = False
        self.eye_open_transition_start = 0.0
        self.eye_open_transition_duration = 0.1
        self.eye_open_param_ids = ("eye_l_open", "eye_r_open")
        self.eye_open_start_values = {
            param_id: 1.0 for param_id in self.eye_open_param_ids
        }

        # 长音频动作循环状态机
        self.long_audio_motion_threshold_seconds = 6.0  # 超过这个时长的音频才会触发
        self.long_audio_motion_repeat_delay_seconds = (
            2.5  # 每次动作结束后等待这么久才触发下一次，防止动作切换过快
        )
        self.long_audio_motion_max_repeats = 2  # 最长音频动作循环的最大重复次数，防止某些极端长的音频导致动作一直循环，这也有点人机
        self.long_audio_motion_repeat_count = 0
        self.long_audio_motion_active = False
        self.long_audio_motion_group = ""
        self.long_audio_next_motion_at = 0.0
        self.long_audio_duration_seconds = 0.0

        self.idle_recover_timer = time.time()
        self.pending = deque()
        self.active_segment = None
        self.cancelled_turns = set()
        self.thinking = False
        self.structured_mode = False
        self.on_event = lambda event: None
        self.on_subtitle = lambda text: None
        self.last_idle = time.time()
        self.audio_started = False
        self.audio_failed = False
        self.start_deadline = 0.0
        self.recording = False
        self.last_interaction = float("-inf")
        # 普通窗口保留字幕；桌宠按播放完成时间自动收起。
        self.subtitle_hide_delay: float | None = None
        self.subtitle_deadline: float | None = None
        self.subtitle_read_seconds = 6.0
        self.text_segment_deadline = 0.0
        self.farewell_started = False
        self.farewell_finished = False
        self.farewell_deadline = 0.0

    def finish_farewell(self, *args: object) -> None:
        """告别动作结束后仅发送一次完成回执。"""
        if self.farewell_finished:
            return
        self.farewell_finished = True
        self.on_event({"type": "farewell_complete"})

    def start_farewell(self, model: Live2DModelProtocol) -> None:
        """清空对话演出后播放告别，缺失动作时立即完成。"""
        if self.farewell_started:
            return
        self.stop()
        self.structured_mode = True
        self.farewell_started = True
        self.farewell_deadline = time.monotonic() + 8.0
        model.set_parameter_value("mouth_open_y", 0.0)
        if not model.StartRandomMotion(
            "bye", 3, self.onStartCallback, self.finish_farewell, position="C"
        ):
            self.finish_farewell()

    def play_interaction(
        self, model: Live2DModelProtocol, *, blocked: bool = False
    ) -> bool:
        """空闲点击时播放通用待机动作，并限制连续点击频率。"""
        now = time.monotonic()
        if self.farewell_started or blocked or self.busy or self.thinking or self.recording or self.audio_busy():
            return False
        if now - self.last_interaction < 1.0:
            return False
        self.last_interaction = now
        for group in ("IDLE", "idle_motion"):
            if model.StartRandomMotion(
                group, 2, self.onStartCallback, self.onFinishCallback, position="C"
            ):
                self.last_idle = time.time()
                return True
        return False

    @staticmethod
    def audio_busy():
        return bool(pygame.mixer.get_init() and pygame.mixer.music.get_busy())

    @property
    def busy(self):
        return self.active_segment is not None or bool(self.pending)

    def command(self, command: dict[str, object], model: Live2DModelProtocol) -> bool:
        kind = command.get("type")
        if kind == "farewell":
            self.start_farewell(model)
            return True
        if self.farewell_started:
            return True
        if kind in {"start_talking", "stop_talking"}:
            self.recording = kind == "start_talking"
        key = (command.get("chat_id"), command.get("turn_id"))
        if kind == "play_segment":
            self.structured_mode = True
            if key not in self.cancelled_turns:
                self.pending.append(dict(command))
            return True
        if kind == "thinking":
            self.structured_mode = True
            self.thinking = True
            self.subtitle_deadline = None
            if self.subtitle_hide_delay is not None:
                self.on_subtitle("")
            return True
        if kind == "generation_finished":
            self.thinking = False
            return True
        if kind == "cancel_turn":
            self.cancelled_turns.add(key)
            self.pending = deque(
                x for x in self.pending if (x.get("chat_id"), x.get("turn_id")) != key
            )
            if (
                self.active_segment is not None
                and (
                    self.active_segment.get("chat_id"),
                    self.active_segment.get("turn_id"),
                )
                == key
            ):
                if pygame.mixer.get_init():
                    pygame.mixer.music.stop()
                self.active_segment = None
            self.thinking = False
            self._reset_long_audio_motion_loop()
            self.wavHandler = WavHandler()
            model.set_parameter_value("mouth_open_y", 0.0)
            self.on_subtitle("")
            self.subtitle_deadline = None
            return True
        return False

    def _start_segment_audio(self, segment: dict[str, object]) -> None:
        if self.active_segment is not segment or self.audio_started:
            return
        self.audio_started = True
        path = segment.get("audio_path")
        if not has_voice_audio(path):
            return
        self.onStartCallback_emotion_version(path)
        self.audio_failed = not self.audio_busy()

    def update_playback(self, model: Live2DModelProtocol) -> None:
        """每帧在模型 Update 之后调用；回执含轮次/段号，不依赖动作回调完成。"""
        if self.farewell_started:
            if time.monotonic() >= self.farewell_deadline:
                self.finish_farewell()
            return
        now = time.time()
        if self.active_segment is None and self.pending:
            segment = self.pending.popleft()
            self.active_segment = segment
            self.audio_started = False
            self.audio_failed = False
            self.start_deadline = now + 0.25
            self.thinking = False
            text = str(segment.get("text") or "")
            translation = str(segment.get("translation") or "")
            self.subtitle_deadline = None
            self.subtitle_read_seconds = max(
                6.0, min(30.0, len(text + translation) / 6.0)
            )
            self.text_segment_deadline = time.monotonic() + self.subtitle_read_seconds
            self.on_subtitle(text + ("\n" + translation if translation else ""))
            group = motion_group_for_emotion(
                str(segment.get("emotion")), default="happiness"
            )
            self._prepare_long_audio_motion_loop(
                group, str(segment.get("audio_path") or "")
            )
            started = model.StartRandomMotion(
                group,
                3,
                lambda *args: self._start_segment_audio(segment),
                self.onFinishCallback,
                position="C",
            )
            if not started:
                self._start_segment_audio(segment)
        segment = self.active_segment
        if segment is not None:
            if not self.audio_started and now >= self.start_deadline:
                self._start_segment_audio(segment)
            text_only = not has_voice_audio(segment.get("audio_path"))
            if self.audio_started and not self.audio_busy() and (
                not (text_only or self.audio_failed)
                or time.monotonic() >= self.text_segment_deadline
            ):
                self.active_segment = None
                self._reset_long_audio_motion_loop()
                if self.subtitle_hide_delay is not None:
                    has_audio = has_voice_audio(segment.get("audio_path"))
                    # 无声音段落已在演出阶段保留阅读时间，结束后直接收起。
                    delay = self.subtitle_hide_delay if has_audio and not self.audio_failed else 0.0
                    self.subtitle_deadline = time.monotonic() + delay
                self.on_event(
                    dict(
                        segment,
                        type="playback_failed"
                        if self.audio_failed
                        else "playback_complete",
                    )
                )
        if (
            self.subtitle_deadline is not None
            and not self.busy
            and not self.thinking
            and time.monotonic() >= self.subtitle_deadline
        ):
            self.subtitle_deadline = None
            self.on_subtitle("")
        if self.thinking and now - self.last_idle > 15:
            model.StartRandomMotion(
                "text_generating",
                3,
                self.onStartCallback_think_motion_version,
                self.onFinishCallback_think_motion_version,
                position="C",
            )
            self.last_idle = now
        elif not self.busy and not self.thinking and now - self.last_idle > 25:
            model.StartRandomMotion(
                "IDLE", 1, self.onStartCallback, self.onFinishCallback, position="C"
            )
            self.last_idle = now
        if (
            not self.busy
            and not self.thinking
            and self.motion_is_over
            and now - self.idle_recover_timer > 2.5
        ):
            model.StartRandomMotion(
                "idle_motion", 1, self.onStartCallback, position="C"
            )
            self.idle_recover_timer = now
        self._update_long_audio_motion_loop(model)
        self._update_eye_open_transition(model)
        mouth = 0.0
        if self.audio_busy() and self.wavHandler.Update():
            mouth = self.wavHandler.GetRms() * self.lipSyncN
            if not __import__("math").isfinite(mouth):
                mouth = 0.0
        model.set_parameter_value("mouth_open_y", mouth)

    def stop(self):
        self.pending.clear()
        self.active_segment = None
        self.thinking = False
        self.recording = False
        self.subtitle_deadline = None
        self.on_subtitle("")
        self._reset_long_audio_motion_loop()
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()

    # 动作播放开始后调用
    def onStartCallback(self, *args):
        self.motion_is_over = False
        self._reset_eye_open_transition()
        # print(f"touched and motion [] is started")

    def onStartCallback_think_motion_version(self, *args):
        self.think_motion_is_over = False
        self._reset_eye_open_transition()

    def onStartCallback_emotion_version(self, audio_file_path, *args):
        self.motion_is_over = False
        self._reset_eye_open_transition()
        # print(f"touched and motion [] is started")
        logger = get_logger(__name__)
        if not audio_file_path or not os.path.isfile(audio_file_path):
            logger.warning("跳过无效音频路径：%s", audio_file_path)
            return
        try:
            # 播放音频
            pygame.mixer.music.load(audio_file_path)
            pygame.mixer.music.play()
        except pygame.error as exc:
            logger.warning("播放音频失败，已跳过：%s，错误：%s", audio_file_path, exc)
            return
        # 处理口型同步
        if (
            audio_file_path != "../reference_audio/silent_audio/silence.wav"
        ):  # 该函数无法处理无声音频
            try:
                self.wavHandler.Start(audio_file_path)
            except Exception as exc:
                logger.warning(
                    "口型同步读取音频失败，已跳过：%s，错误：%s", audio_file_path, exc
                )

    # 动作播放结束后调用
    def onFinishCallback(self, *args):
        # print("motion finished")
        self.motion_is_over = True
        self._queue_eye_open_transition()
        self.idle_recover_timer = time.time()

    def onFinishCallback_think_motion_version(self, *args):
        self.think_motion_is_over = True
        self._queue_eye_open_transition()

    def _reset_eye_open_transition(self):
        self.force_eyes_open = False
        self.eye_open_pending = False
        self.eye_open_transition_start = 0.0
        self.eye_open_start_values = {
            param_id: 1.0 for param_id in self.eye_open_param_ids
        }

    def _queue_eye_open_transition(self):
        self.force_eyes_open = False
        self.eye_open_pending = True
        self.eye_open_transition_start = 0.0

    def _get_model_parameter_value(
        self, model, param_id: str, default: float = 1.0
    ) -> float:
        get_parameter_value = getattr(model, "get_parameter_value", None)
        if callable(get_parameter_value):
            try:
                return float(get_parameter_value(param_id, default))
            except Exception:
                return default
        try:
            for index in range(model.GetParameterCount()):
                param = model.GetParameter(index)
                if getattr(param, "id", "") == param_id:
                    return max(0.0, min(1.0, float(getattr(param, "value", default))))
        except Exception:
            pass
        return default

    def _set_model_eye_open_values(self, model, value_by_param_id):
        set_parameter_value = getattr(model, "set_parameter_value", None)
        if callable(set_parameter_value):
            for param_id, value in value_by_param_id.items():
                try:
                    set_parameter_value(param_id, value)
                except Exception:
                    pass
            return
        try:
            for param_id, value in value_by_param_id.items():
                model.SetParameterValue(param_id, value)
        except Exception:
            pass

    def _update_eye_open_transition(self, model):
        if self.eye_open_pending:
            self.eye_open_start_values = {
                param_id: self._get_model_parameter_value(model, param_id)
                for param_id in self.eye_open_param_ids
            }
            if self.eye_open_start_values.get("eye_l_open", 1.0) > 0.5:
                self._reset_eye_open_transition()
                return
            self.eye_open_transition_start = time.time()
            self.eye_open_pending = False

        if self.eye_open_transition_start <= 0:
            if self.force_eyes_open:
                self._set_model_eye_open_values(
                    model, {param_id: 1.0 for param_id in self.eye_open_param_ids}
                )
                self._reset_eye_open_transition()
            return

        elapsed = time.time() - self.eye_open_transition_start
        progress = max(0.0, min(1.0, elapsed / self.eye_open_transition_duration))
        eye_values = {
            param_id: start_value + (1.0 - start_value) * progress
            for param_id, start_value in self.eye_open_start_values.items()
        }
        self._set_model_eye_open_values(model, eye_values)
        if progress >= 1.0:
            self._set_model_eye_open_values(
                model, {param_id: 1.0 for param_id in self.eye_open_param_ids}
            )
            self._reset_eye_open_transition()

    def _reset_long_audio_motion_loop(self):
        self.long_audio_motion_active = False
        self.long_audio_motion_group = ""
        self.long_audio_next_motion_at = 0.0
        self.long_audio_duration_seconds = 0.0
        self.long_audio_motion_repeat_count = 0

    def _get_audio_duration_seconds(self, audio_file_path: str) -> float:
        if not audio_file_path or not os.path.isfile(audio_file_path):
            return 0.0
        try:
            with wave.open(audio_file_path, "rb") as audio_file:
                frame_rate = audio_file.getframerate()
                if frame_rate <= 0:
                    return 0.0
                return audio_file.getnframes() / frame_rate
        except Exception:
            pass
        try:
            return float(pygame.mixer.Sound(audio_file_path).get_length())
        except Exception:
            return 0.0

    def _prepare_long_audio_motion_loop(self, motion_group: str, audio_file_path: str):
        duration = self._get_audio_duration_seconds(audio_file_path)
        if duration < self.long_audio_motion_threshold_seconds:
            self._reset_long_audio_motion_loop()
            return
        self.long_audio_motion_active = True
        self.long_audio_motion_group = motion_group
        self.long_audio_next_motion_at = 0.0
        self.long_audio_duration_seconds = duration
        self.long_audio_motion_repeat_count = 0

    def _update_long_audio_motion_loop(self, model):
        if not self.long_audio_motion_active:
            return
        if not self.audio_busy():
            self._reset_long_audio_motion_loop()
            return
        if not self.motion_is_over:
            return
        if not self.long_audio_motion_group:
            self._reset_long_audio_motion_loop()
            return
        if self.long_audio_motion_repeat_count >= self.long_audio_motion_max_repeats:
            return

        now = time.time()
        if self.long_audio_next_motion_at <= 0:
            self.long_audio_next_motion_at = (
                now + self.long_audio_motion_repeat_delay_seconds
            )
            return
        if now < self.long_audio_next_motion_at:
            return

        self.motion_is_over = False
        started = model.StartRandomMotion(
            self.long_audio_motion_group,
            3,
            self.onStartCallback,
            self.onFinishCallback,
            position="C",
        )
        if not started:
            self.motion_is_over = True
            self._reset_long_audio_motion_loop()
            return
        self.long_audio_motion_repeat_count += 1
        self.long_audio_next_motion_at = 0.0
