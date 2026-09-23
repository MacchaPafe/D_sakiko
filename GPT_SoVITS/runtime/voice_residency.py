"""语音模型驻留策略；使用单调时钟，不依赖 Qt 或推理框架。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VoiceResidencyPolicy:
    """记录桌宠空闲和连续关闭语音的时间，供串行调度器安全释放模型。"""

    last_activity: float
    pet_mode: bool = False
    busy: bool = False
    silent_since: float | None = None

    def observe(
        self, now: float, *, pet_mode: bool, busy: bool, voice_enabled: bool
    ) -> None:
        """活动或形态切换时重新计时，恢复语音时取消静音卸载倒计时。"""
        if pet_mode != self.pet_mode or busy or self.busy:
            self.last_activity = now
        self.pet_mode = pet_mode
        self.busy = busy
        if voice_enabled:
            self.silent_since = None
        elif self.silent_since is None:
            self.silent_since = now

    def pet_idle(self, now: float, enabled: bool) -> bool:
        """判断是否已在桌宠形态连续空闲两分钟。"""
        return (
            enabled
            and self.pet_mode
            and not self.busy
            and now - self.last_activity >= 120.0
        )

    def should_unload(self, now: float, idle_enabled: bool) -> bool:
        """静音持续一分钟或桌宠空闲到期时申请释放，由串行调度器等待安全点。"""
        return self.pet_idle(now, idle_enabled) or (
            self.silent_since is not None and now - self.silent_since >= 60.0
        )
