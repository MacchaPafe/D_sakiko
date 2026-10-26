"""小剧场四轨调度与预览协议；只在现有 Live2D 渲染线程调用。"""
from __future__ import annotations

import io
import logging
import time
from pathlib import Path

from performance_types import timeline_tracks, valid_timeline_events
from live2d_support.multi_char_resources import TheaterResources, audio_span

logger = logging.getLogger(__name__)


class TimelineCursor:
    """按起点消费事件，定位不追溯，卡顿每轨只提交最后一个点。"""

    def __init__(self, timeline: object, duration: int, offset: int = 0):
        """建立本次播放的独立游标，不修改输入的存档对象。"""
        self.duration = duration
        self.tracks = {}
        self.positions = {}
        for name, tracks in timeline_tracks(timeline).items():
            for kind in ("motions", "expressions"):
                key = (name, kind)
                self.tracks[key] = [event for event in valid_timeline_events(tracks.get(kind), kind)
                                    if offset <= event["at_ms"] < duration]
                self.positions[key] = 0

    def advance(self, position: int) -> list:
        """同刻先动作再表情；过句末不补播，返回每轨最后一个到期事件。"""
        if position >= self.duration:
            return []
        result = []
        for key, events in self.tracks.items():
            index = self.positions[key]
            latest = None
            while index < len(events) and events[index]["at_ms"] <= position:
                latest = events[index]
                index += 1
            self.positions[key] = index
            if latest is not None:
                result.append((key[0], key[1], latest))
        return sorted(result, key=lambda item: (item[2]["at_ms"], item[1] != "motions"))


class MultiCharPerformance:
    """绑定当前模型槽位，统一正式四轨播放和独占编辑预览。"""

    def __init__(self, models, slots, send, music, direction=None):
        """注入模型/槽位查询及音频通道，不新建渲染器。"""
        self.models = models
        self.slots = slots
        self.send = send
        self.music = music
        self.direction = direction
        self.resources = {}
        self.cursor = None
        self.preview = False
        self.paused = False
        self.playing = False
        self.session = ""
        self.sequence = 0
        self.turn = {}
        self.span = None
        self.started = 0.0
        self.offset = 0
        self.pause_at = 0.0
        self.expression_set = set()
        self.handoff = set()
        self.last_report = 0.0
        self.audio_buffer = None
        self.samples = None
        self.sample_rate = 0
        self.pause_position = 0
        self.managed = set()

    def bindings(self):
        """按角色名绑定模型，不把左右位置当作角色身份。"""
        return {slot["character_name"]: model for slot, model in zip(self.slots(), self.models())
                if model is not None}

    def catalog(self, name):
        """按实际模型路径缓存资源，换装后自然失效。"""
        model = self.bindings().get(name)
        if model is None:
            return None
        direction = self.direction(name) if self.direction else "C"
        key = (name, model.model_json_path, direction)
        if key not in self.resources:
            self.resources[key] = TheaterResources(model.model_json_path, direction)
        return self.resources[key]

    def is_managed_model(self, model):
        """按对象身份判断模型是否由编排接管，不要求模型对象可哈希。"""
        return any(name in self.managed and bound is model
                   for name, bound in self.bindings().items())

    def reset(self, name, motion=True, expression=True):
        """重置指定角色通道，其他角色及独立表情不受动作重置影响。"""
        model = self.bindings().get(name)
        if model is None:
            return
        native = model.model
        if motion:
            native.StopAllMotions()
            native.ResetAllParameters()
            native.SaveParameters()
            model._performance_generation += 1
            model.StartRandomMotion("idle_motion", 3, auto_expression=False)
        if expression and model.version == "v3":
            native.ResetExpressions()
            model._default_expression_active = False
            model.performance_expression_active = False
            model._custom_expression_active = False
            model._restore_default_expression()

    def reset_all(self):
        """停止双人演出并清理旧回调，定位与退出共用。"""
        for name in self.bindings():
            self.reset(name)
        self.cursor = None

    def begin(self, turn, preview=False, offset=0):
        """开始本句事件调度，正式播放的音频由宿主启动。"""
        self.turn = turn
        self.span = audio_span(str(turn.get("audio_path") or ""), str(turn.get("text") or ""))
        self.offset = offset
        self.started = time.monotonic()
        self.expression_set.clear()
        self.handoff.clear()
        self.playing = True
        self.paused = False
        self.cursor = TimelineCursor(turn.get("performance_timeline"), self.span.duration_ms, offset)
        self.managed.update(timeline_tracks(turn.get("performance_timeline")))
        if preview:
            self.reset_all()
            self.cursor = TimelineCursor(turn.get("performance_timeline"), self.span.duration_ms, offset)
        elif offset == 0:
            for name, tracks in timeline_tracks(turn.get("performance_timeline")).items():
                self.reset(name, motion=tracks.get("motions") == [], expression=tracks.get("expressions") == [])
        if not preview:
            self.tick()

    def event(self, name, kind, event):
        """精确播放有效引用；缺失资源跳过且不重置上句状态。"""
        model = self.bindings().get(name)
        resources = self.catalog(name)
        if model is None or resources is None:
            return
        if kind == "expressions" and model.version != "v3":
            return
        resource = event["resource"]
        if kind == "expressions" and resource is None:
            self.reset(name, motion=False)
            self.expression_set.add(name)
            return
        item = resources.resolve(kind, resource)
        if item is None:
            logger.warning("跳过失效编排资源 %s %s", name, resource)
            return
        if kind == "expressions":
            if model.SetExpression(item["resource"]["logical_id"]):
                model.performance_expression_active = True
                self.expression_set.add(name)
            return
        if model.version == "v3":
            asset = resources.catalog.motion(item["resource"]["logical_id"], resources.direction)
            started = model._start_performance_motion(item["file"], asset.entry if asset else {}, None)
        else:
            started = model.StartMotionFile(str(resources.path.parent / item["file"]), 3, auto_expression=False)
        if started:
            if name not in self.handoff and name not in self.expression_set:
                self.reset(name, motion=False)
            self.handoff.add(name)

    def position(self):
        """读取本句主时钟，暂停时使用冻结时刻。"""
        if self.span is None:
            return self.offset
        if self.paused:
            return self.pause_position
        now = time.monotonic()
        if self.span.path:
            elapsed = self.music.get_pos()
            if elapsed >= 0:
                return self.offset + elapsed
        return self.offset + round((now - self.started) * 1000)

    def tick(self):
        """每帧消费事件；自然结束保持运行时保存的尾帧，不启动待机动作。"""
        if self.paused:
            return
        now = time.monotonic()
        if self.playing and self.cursor:
            position = self.position()
            if position >= self.cursor.duration:
                self.playing = False
                self.cursor = None
                if self.preview:
                    self.music.stop()
                    self.offset = self.span.duration_ms
                    # 自然播完保留画面；停止、定位和退出仍显式复位。
                    self.pause_position = self.offset
                    self.paused = True
            else:
                for name, kind, event in self.cursor.advance(position):
                    self.event(name, kind, event)
        if self.preview and now - self.last_report >= 0.08:
            self.last_report = now
            self.send({"type": "multi_char_preview", "session": self.session,
                       "sequence": self.sequence, "position": self.position() if self.playing else self.offset,
                       "playing": self.playing and not self.paused})

    def load_audio(self, offset):
        """用内存 WAV 从定位点播放，兼容 pygame 对 WAV 不支持 start 的限制。"""
        self.music.stop()
        self.samples = None
        if not self.span.path or offset >= self.span.duration_ms:
            return
        import soundfile as sf
        with sf.SoundFile(self.span.path) as audio:
            audio.seek(min(audio.frames, round(offset * audio.samplerate / 1000)))
            samples = audio.read(dtype="float32")
            self.samples = samples
            self.sample_rate = audio.samplerate
            self.audio_buffer = io.BytesIO()
            sf.write(self.audio_buffer, samples, audio.samplerate, format="WAV", subtype="PCM_16")
        self.audio_buffer.seek(0)
        self.music.load(self.audio_buffer, "wav")
        self.music.play()

    def mouth(self):
        """返回预览说话者和当前音频 RMS，听者及无声不驱动语音口型。"""
        if not self.preview or not self.playing or self.paused or self.samples is None:
            return None
        import numpy as np
        start = max(0, round(self.music.get_pos() * self.sample_rate / 1000))
        segment = self.samples[start:start + max(1, self.sample_rate // 30)]
        if not len(segment):
            return None
        model = self.bindings().get(self.turn.get("character_name"))
        return (model, float(np.sqrt(np.mean(segment * segment)))) if model else None

    def command(self, command):
        """处理带编辑会话标识的独占预览命令，丢弃旧窗口请求。"""
        action = command.get("action")
        if action == "enter":
            self.session = command["session"]
            self.sequence = command.get("sequence", 0)
            self.preview = True
            self.playing = False
            self.paused = False
            self.offset = 0
            self.music.stop()
            self.reset_all()
            return
        if command.get("session") != self.session or not self.preview:
            return
        self.sequence = command.get("sequence", self.sequence)
        if action in ("play", "seek"):
            self.music.stop()
            turn = command["turn"]
            self.begin(turn, preview=True, offset=int(command.get("position", 0)))
            self.load_audio(self.offset)
            if action == "seek" and not command.get("resume", False):
                self.pause_position = self.offset
                self.paused = True
                self.pause_at = time.monotonic()
                self.music.pause()
        elif action == "pause":
            if self.playing and not self.paused:
                self.pause_position = self.position()
                self.pause_at = time.monotonic()
                self.paused = True
                self.music.pause()
        elif action == "resume":
            if self.paused:
                delay = time.monotonic() - self.pause_at
                self.started += delay
                self.paused = False
                self.music.unpause()
        elif action in ("stop", "exit", "resource", "refresh"):
            self.music.stop()
            self.playing = False
            self.paused = False
            self.reset_all()
            if action == "stop":
                self.offset = 0
            if action == "resource":
                self.offset = int(command.get("position", self.offset))
                self.expression_set.clear()
                self.handoff.clear()
                self.event(command["character"], command["kind"], {"resource": command["resource"]})
            if action == "exit":
                self.preview = False
                self.session = ""
            if action == "refresh":
                self.resources.clear()
