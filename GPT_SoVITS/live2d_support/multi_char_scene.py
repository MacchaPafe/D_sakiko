"""小剧场舞台继承、背景引用和句前时序的纯逻辑，不依赖 Qt 或 OpenGL。"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Iterable, Sequence


ACTOR_TRANSITION_MS = 300
SCENE_GAP_MS = 500
BLACK_DEFAULT_MS = 2000
BLACK_MIN_MS = 500
BLACK_MAX_MS = 4000


def background_root() -> Path:
    """返回固定的应用资源根目录，不受进程工作目录影响。"""
    return Path(__file__).resolve().parents[2] / "live2d_related"


def background_path(filename: str, root: Path | None = None) -> Path:
    """校验背景文件名并返回根目录中的真实路径；文件存在性和解码由调用方处理。"""
    if not isinstance(filename, str) or not filename:
        raise ValueError("背景图片文件名不能为空")
    if any(separator in filename for separator in ("/", "\\", ":")):
        raise ValueError("背景只能引用 live2d_related 根目录内的图片文件名")
    name = Path(filename)
    if name.suffix.lower() not in (".png", ".jpg"):
        raise ValueError("背景图片只支持 PNG 或 JPG")
    directory = (Path(root) if root is not None else background_root()).resolve()
    candidate = (directory / name).resolve()
    if candidate.parent != directory:
        raise ValueError("背景图片必须直接位于 live2d_related 根目录")
    return candidate


def _scene_from_message(message: object) -> dict:
    """读取消息的句前配置，损坏结构仅在本次运行投影中视为未配置。"""
    if isinstance(message, dict):
        scene = message.get("scene_before")
    else:
        scene = getattr(message, "scene_before", None)
    return scene if isinstance(scene, dict) else {}


def resolve_scene(
        messages: Sequence[object],
        index: int,
        characters: Iterable[str],
        base_background: str = "",
) -> dict:
    """从本次会话起点合并截至目标句的舞台状态；负索引返回初始双人舞台。"""
    names = list(dict.fromkeys(name for name in characters if name))
    state = {"visible_characters": names[:], "background": base_background}
    for message in messages[:max(0, index + 1)]:
        scene = _scene_from_message(message)
        visible = scene.get("visible_characters")
        if isinstance(visible, list):
            # 空数组明确表示空舞台；未知名字不会变成额外演员。
            state["visible_characters"] = [name for name in names if name in visible]
        background = scene.get("background")
        if isinstance(background, str) and background:
            state["background"] = background
    return state


def resolve_before(
        messages: Sequence[object],
        index: int,
        characters: Iterable[str],
        base_background: str = "",
) -> dict:
    """解析目标句句前阶段开始前的舞台状态，不继承上一句黑场参数。"""
    return resolve_scene(messages, index - 1, characters, base_background)


def transition_from_total(total_ms: int = BLACK_DEFAULT_MS) -> dict:
    """将界面总时长拆为 30% 淡出、40% 全黑、30% 淡入并限制到允许范围。"""
    total = round(max(BLACK_MIN_MS, min(BLACK_MAX_MS, total_ms)))
    fade = round(total * 0.3)
    return {
        "type": "black",
        "fade_out_ms": fade,
        "hold_ms": total - fade * 2,
        "fade_in_ms": fade,
    }


@dataclass(frozen=True)
class SceneTiming:
    """句前共用时钟：黑场、胶囊运动与人物显现，不占用句内时间轴。"""

    duration_ms: int
    change_at_ms: int = 0
    fade_out_ms: int = 0
    hold_ms: int = 0
    fade_in_ms: int = 0
    capsule_hold_ms: int = 0
    capsule_text: str = ""

    @property
    def black_duration_ms(self) -> int:
        return self.fade_out_ms + self.hold_ms + self.fade_in_ms

    @property
    def capsule_end_ms(self) -> int:
        return self.black_duration_ms + 800 + self.capsule_hold_ms

    @classmethod
    def from_scene(cls, scene_before: object, first: bool = False, actors: bool = True) -> "SceneTiming":
        """黑场先于胶囊，首句跳过黑场但允许胶囊标题。"""
        scene = scene_before if isinstance(scene_before, dict) else {}
        transition = scene.get("transition")
        black = {}
        if not first and isinstance(transition, dict) and transition.get("type") == "black":
            parts = [transition.get(key) for key in ("fade_out_ms", "hold_ms", "fade_in_ms")]
            total = sum(parts) if all(type(part) in (int, float) and isfinite(part) and part >= 0
                                      for part in parts) else BLACK_DEFAULT_MS
            black = transition_from_total(total)
        fade_out, hold, fade_in = [black.get(key, 0) for key in ("fade_out_ms", "hold_ms", "fade_in_ms")]
        black_duration = fade_out + hold + fade_in
        capsule = scene.get("capsule") or {}
        if capsule.get("enabled"):
            capsule_hold = max(500, min(5000, int(capsule.get("hold_ms", 2500))))
            return cls(duration_ms=black_duration + 800 + capsule_hold + (ACTOR_TRANSITION_MS if actors else 0),
                       change_at_ms=fade_out, fade_out_ms=fade_out, hold_ms=hold, fade_in_ms=fade_in,
                       capsule_hold_ms=capsule_hold, capsule_text=str(capsule.get("text", "")))
        if first:
            return cls(0)
        return cls(black_duration or SCENE_GAP_MS, fade_out, fade_out, hold, fade_in)

    def capsule_offset(self, elapsed_ms: float) -> float | None:
        """右侧画外为 1，居中为 0，左侧画外为 -1；不在胶囊阶段返回 None。"""
        position = elapsed_ms - self.black_duration_ms
        if not self.capsule_hold_ms or position < 0 or position >= 800 + self.capsule_hold_ms:
            return None
        if position < 400:
            return 1 - position / 400
        return -max(0.0, (position - 400 - self.capsule_hold_ms) / 400)

    def black_opacity(self, elapsed_ms: float) -> float:
        """返回覆盖整个画面的黑幕透明度，普通间隔和阶段结束时均为零。"""
        if not self.fade_out_ms or elapsed_ms <= 0 or elapsed_ms >= self.black_duration_ms:
            return 0.0
        if elapsed_ms < self.fade_out_ms:
            return elapsed_ms / self.fade_out_ms
        fade_in_start = self.fade_out_ms + self.hold_ms
        if elapsed_ms < fade_in_start:
            return 1.0
        return (self.black_duration_ms - elapsed_ms) / self.fade_in_ms

    def actors_progress(self, elapsed_ms: float) -> float:
        """普通阶段并行调整人物；启用胶囊后延至滑出结束再统一显现。"""
        if self.duration_ms == 0:
            return 1.0
        start = self.capsule_end_ms if self.capsule_hold_ms else self.change_at_ms
        return max(0.0, min(1.0, (elapsed_ms - start) / ACTOR_TRANSITION_MS))

    def finished(self, elapsed_ms: float) -> bool:
        """判断句前阶段是否结束；结束后才允许启动本句音频与句内时间轴。"""
        return elapsed_ms >= self.duration_ms
