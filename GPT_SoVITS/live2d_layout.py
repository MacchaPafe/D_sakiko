from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from qconfig import PROJECT_ROOT, d_sakiko_config


Live2DLayoutScene = Literal["single", "theater"]
Live2DLayoutRuntime = Literal["v2", "v3"]

MIN_LIVE2D_SCALE = 0.2
MAX_LIVE2D_SCALE = 5.0
MIN_LIVE2D_OFFSET = -2.0
MAX_LIVE2D_OFFSET = 2.0
ZOOM_STEP = 1.05

_SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Live2DLayout:
    """记录一个 Live2D 模型在指定场景中的缩放和平移。"""

    scale: float
    offset_x: float
    offset_y: float

    def clamped(self) -> "Live2DLayout":
        """返回限制在安全范围内的布局。"""
        return Live2DLayout(
            scale=_clamp(self.scale, MIN_LIVE2D_SCALE, MAX_LIVE2D_SCALE),
            offset_x=_clamp(self.offset_x, MIN_LIVE2D_OFFSET, MAX_LIVE2D_OFFSET),
            offset_y=_clamp(self.offset_y, MIN_LIVE2D_OFFSET, MAX_LIVE2D_OFFSET),
        )

    def moved_by_pixels(self, dx_pixels: int, dy_pixels: int, window_width: int, window_height: int) -> "Live2DLayout":
        """按窗口像素拖动距离生成新的布局。"""
        if window_width <= 0 or window_height <= 0:
            return self
        return Live2DLayout(
            scale=self.scale,
            offset_x=self.offset_x + dx_pixels / window_width * 2.0,
            offset_y=self.offset_y - dy_pixels / window_height * 2.0,
        ).clamped()

    def zoomed(self, wheel_steps: int) -> "Live2DLayout":
        """按滚轮步数生成新的缩放布局。"""
        if wheel_steps == 0:
            return self
        factor = ZOOM_STEP ** wheel_steps
        return Live2DLayout(
            scale=self.scale * factor,
            offset_x=self.offset_x,
            offset_y=self.offset_y,
        ).clamped()

    def to_config_dict(self) -> dict[str, float]:
        """转换为可写入配置文件的字典。"""
        layout = self.clamped()
        return {
            "scale": layout.scale,
            "offset_x": layout.offset_x,
            "offset_y": layout.offset_y,
        }


def _clamp(value: float, min_value: float, max_value: float) -> float:
    """将数值限制在指定闭区间内。"""
    return max(min_value, min(max_value, value))


def _as_float(value: object, default: float) -> float:
    """将配置值转换为浮点数，失败时使用默认值。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    return default


def _candidate_model_paths(model_json_path: str) -> list[Path]:
    """生成模型路径可能对应的绝对路径候选。"""
    raw_path = Path(model_json_path)
    if raw_path.is_absolute():
        return [raw_path.resolve()]
    return [
        (Path.cwd() / raw_path).resolve(),
        (_SCRIPT_DIR / raw_path).resolve(),
        (PROJECT_ROOT / raw_path).resolve(),
    ]


def normalize_model_layout_key(model_json_path: str) -> str:
    """将模型路径规范化为项目相对 posix key。"""
    candidates = _candidate_model_paths(model_json_path)
    resolved_path = candidates[0]
    for candidate in candidates:
        if candidate.exists():
            resolved_path = candidate
            break

    try:
        return resolved_path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved_path.as_posix()


def default_live2d_layout(runtime: Live2DLayoutRuntime, scene: Live2DLayoutScene) -> Live2DLayout:
    """返回指定 runtime 与场景的默认布局。"""
    if scene == "theater":
        return Live2DLayout(scale=0.8, offset_x=0.0, offset_y=0.0)
    if runtime == "v3":
        return Live2DLayout(scale=2.3, offset_x=0.0, offset_y=-0.77)
    return Live2DLayout(scale=1.0, offset_x=0.0, offset_y=0.0)


def layout_from_mapping(mapping: Mapping[str, object], default_layout: Live2DLayout) -> Live2DLayout:
    """从配置映射读取布局，缺失字段使用默认布局。"""
    return Live2DLayout(
        scale=_as_float(mapping.get("scale"), default_layout.scale),
        offset_x=_as_float(mapping.get("offset_x"), default_layout.offset_x),
        offset_y=_as_float(mapping.get("offset_y"), default_layout.offset_y),
    ).clamped()


def get_live2d_layout(model_json_path: str, runtime: Live2DLayoutRuntime, scene: Live2DLayoutScene) -> Live2DLayout:
    """读取模型在指定场景下的布局配置。"""
    default_layout = default_live2d_layout(runtime, scene)
    model_key = normalize_model_layout_key(model_json_path)
    raw_layouts = d_sakiko_config.live2d_model_layouts.value
    if not isinstance(raw_layouts, Mapping):
        return default_layout
    raw_model_layout = raw_layouts.get(model_key)
    if not isinstance(raw_model_layout, Mapping):
        return default_layout
    raw_scene_layout = raw_model_layout.get(scene)
    if not isinstance(raw_scene_layout, Mapping):
        return default_layout
    return layout_from_mapping(raw_scene_layout, default_layout)


def save_live2d_layout(model_json_path: str, scene: Live2DLayoutScene, layout: Live2DLayout) -> None:
    """保存模型在指定场景下的自定义布局。"""
    model_key = normalize_model_layout_key(model_json_path)
    raw_layouts = d_sakiko_config.live2d_model_layouts.value
    layout_data: dict[str, object] = dict(raw_layouts) if isinstance(raw_layouts, Mapping) else {}
    raw_model_layout = layout_data.get(model_key)
    model_layout: dict[str, object] = dict(raw_model_layout) if isinstance(raw_model_layout, Mapping) else {}
    model_layout[scene] = layout.to_config_dict()
    layout_data[model_key] = model_layout
    d_sakiko_config.set(d_sakiko_config.live2d_model_layouts, layout_data)


def reset_live2d_layout(model_json_path: str, scene: Live2DLayoutScene) -> None:
    """删除模型在指定场景下的自定义布局，使其回到默认值。"""
    model_key = normalize_model_layout_key(model_json_path)
    raw_layouts = d_sakiko_config.live2d_model_layouts.value
    if not isinstance(raw_layouts, Mapping):
        return

    layout_data: dict[str, object] = dict(raw_layouts)
    raw_model_layout = layout_data.get(model_key)
    if not isinstance(raw_model_layout, Mapping):
        return

    model_layout: dict[str, object] = dict(raw_model_layout)
    model_layout.pop(scene, None)
    if model_layout:
        layout_data[model_key] = model_layout
    else:
        layout_data.pop(model_key, None)
    d_sakiko_config.set(d_sakiko_config.live2d_model_layouts, layout_data)


def format_live2d_layout_status(layout: Live2DLayout) -> str:
    """格式化布局数值，供 overlay 提示显示。"""
    return f"scale={layout.scale:.2f} offset=({layout.offset_x:.2f}, {layout.offset_y:.2f})"
