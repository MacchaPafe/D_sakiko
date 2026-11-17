"""在现有 pygame 混音器中排队播放单段台词的 PCM。"""

from __future__ import annotations

from array import array
from collections import deque
from dataclasses import dataclass
from io import BytesIO
from math import sqrt
import sys
import time
import wave

import pygame


@dataclass
class _SoundChunk:
    """保留 SDL 音频块及其 20 毫秒口型振幅。"""

    sound: pygame.mixer.Sound
    levels: list[float]


class SegmentPCM:
    """用一个混音通道连续供应首段语音，并跨合成间隙保持段落有效。"""

    def __init__(self) -> None:
        self.chunks: deque[tuple[int, bytes]] = deque()
        self.channel: pygame.mixer.Channel | None = None
        self.current: _SoundChunk | None = None
        self.queued: _SoundChunk | None = None
        self.started_at = 0.0
        self.ended = False
        self.failed = False

    @staticmethod
    def _make_sound(sample_rate: int, pcm: bytes) -> _SoundChunk:
        """让混音器转换采样率和声道，口型振幅按原始采样计算。"""
        buffer = BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
            wav.writeframes(pcm)
        buffer.seek(0)
        samples = array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        width = max(1, sample_rate // 50)
        levels = []
        for offset in range(0, len(samples), width):
            frame = samples[offset:offset + width]
            levels.append(sqrt(sum(value * value for value in frame) / len(frame)) / 32768)
        return _SoundChunk(pygame.mixer.Sound(file=buffer), levels)

    def add(self, sample_rate: int, pcm: bytes) -> None:
        """登记还未交给声卡的音频。"""
        if not self.ended and sample_rate > 0 and pcm and len(pcm) % 2 == 0:
            self.chunks.append((sample_rate, pcm))

    def finish(self, error: bool = False) -> None:
        """标记合成结束，已有音频仍由混音器播完。"""
        self.ended = True
        self.failed = self.failed or error

    def pump(self) -> None:
        """每帧填补 SDL 的一个队列槽，避免在块切换时等下一帧。"""
        if self.channel is None and self.chunks:
            self.channel = pygame.mixer.find_channel()
            if self.channel is None:
                raise pygame.error("没有可用的 PCM 播放通道")
        channel = self.channel
        if channel is None:
            return
        if self.queued is not None and channel.get_queue() is None:
            self.current, self.queued = self.queued, None
            self.started_at = time.monotonic()
        if not self.chunks or self.queued is not None:
            return
        chunk = self._make_sound(*self.chunks.popleft())
        if channel.get_busy():
            channel.queue(chunk.sound)
            self.queued = chunk
        else:
            channel.play(chunk.sound)
            self.current = chunk
            self.started_at = time.monotonic()

    def busy(self) -> bool:
        """合成仍在继续或任一音频块尚未播完时占用当前段。"""
        return not self.ended or bool(self.chunks) or bool(self.channel and self.channel.get_busy())

    def rms(self) -> float:
        """返回声卡当前播放位置对应的口型振幅。"""
        if self.current is None or self.channel is None or not self.channel.get_busy():
            return 0.0
        index = int((time.monotonic() - self.started_at) * 50)
        return self.current.levels[index] if 0 <= index < len(self.current.levels) else 0.0

    def stop(self) -> None:
        """取消当前段并释放队列中的声音。"""
        if self.channel is not None and pygame.mixer.get_init():
            self.channel.stop()
        self.channel = None
        self.chunks.clear()
        self.current = self.queued = None
        self.ended = True
