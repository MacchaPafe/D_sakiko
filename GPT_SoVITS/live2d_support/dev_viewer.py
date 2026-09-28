from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from live2d_support.runtime_adapter import Live2DModelAdapter

Direction = Literal['C', 'L', 'R', 'all']


def fit_viewer_window(desktop: tuple[int, int], logical: tuple[int, int], scale: float = 1.0) -> tuple[int, int]:
    """按桌面尺寸限制窗口大小，为标题栏、任务栏和屏幕边缘预留空间。"""
    available_width = max(1, desktop[0] - 60)
    available_height = max(1, desktop[1] - 110)
    fitted = min(scale, available_width / logical[0], available_height / logical[1])
    return max(1, int(logical[0] * fitted)), max(1, int(logical[1] * fitted))


@dataclass(frozen=True)
class ViewerViewport:
    """将固定逻辑界面等比映射到窗口，统一渲染区域与鼠标坐标。"""

    window: tuple[int, int]
    logical: tuple[int, int]

    @property
    def content(self) -> tuple[int, int, int, int]:
        """返回采用左上角坐标的居中内容区域，额外空间作为留白。"""
        scale = min(max(1, self.window[0]) / self.logical[0], max(1, self.window[1]) / self.logical[1])
        width, height = max(1, int(self.logical[0] * scale)), max(1, int(self.logical[1] * scale))
        return (self.window[0] - width) // 2, (self.window[1] - height) // 2, width, height

    @property
    def gl_rect(self) -> tuple[int, int, int, int]:
        """转换为 OpenGL 采用的左下角视口坐标。"""
        x, y, width, height = self.content
        return x, self.window[1] - y - height, width, height

    def model_rect(self, logical_width: int) -> tuple[int, int, int, int]:
        """返回与侧栏边界对齐的模型渲染区域。"""
        x, y, width, height = self.gl_rect
        return x, y, max(1, round(width * logical_width / self.logical[0])), height

    def to_logical(self, position: tuple[int, int]) -> tuple[int, int] | None:
        """将鼠标映射回逻辑坐标，内容留白区域不响应点击或滚动。"""
        x, y, width, height = self.content
        if not (x <= position[0] < x + width and y <= position[1] < y + height):
            return None
        return int((position[0] - x) * self.logical[0] / width), int((position[1] - y) * self.logical[1] / height)


@dataclass(frozen=True)
class MotionEntry:
    """保留真实动作组、索引及文件，避免同名动作之间发生混淆。"""

    group: str
    index: int
    path: Path
    duration: float

    @property
    def name(self) -> str:
        """返回去除完整动作扩展名后的名称。"""
        return self.path.name.removesuffix('.motion3.json')

    @property
    def direction(self) -> str | None:
        """优先从文件名、其次从动作组识别方向后缀。"""
        for name in (self.name, self.group):
            if name.endswith(('_C', '_L', '_R')):
                return name[-1]
        return None


@dataclass(frozen=True)
class ExpressionEntry:
    """记录表情 ID 与文件位置。"""

    name: str
    path: Path


@dataclass(frozen=True)
class ModelResources:
    """记录通过预检查的 V3 模型资源和可恢复的问题。"""

    path: Path
    motions: tuple[MotionEntry, ...]
    expressions: tuple[ExpressionEntry, ...]
    warnings: tuple[str, ...]


def read_resources(path: Path) -> ModelResources:
    """预检查模型必需文件并列出可播放资源，允许空动作及表情列表。"""
    path = path.resolve()
    if not path.name.lower().endswith('.model3.json'):
        raise ValueError('仅支持 Cubism V3 的 .model3.json 模型')
    data: object = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('Version') != 3:
        raise ValueError('模型配置必须是 Version 3')
    refs = data.get('FileReferences')
    if not isinstance(refs, dict):
        raise ValueError('模型缺少 FileReferences')
    moc, textures = refs.get('Moc'), refs.get('Textures')
    if not isinstance(moc, str) or not isinstance(textures, list) or not textures:
        raise ValueError('模型缺少 Moc 或 Textures')
    for name in [moc, *textures]:
        if not isinstance(name, str) or not (path.parent / name).is_file():
            raise ValueError(f'模型必需文件不存在：{name}')
    motions: list[MotionEntry] = []
    expressions: list[ExpressionEntry] = []
    warnings: list[str] = []
    groups = refs.get('Motions', {})
    if not isinstance(groups, dict):
        raise ValueError('Motions 必须是动作组对象')
    for group, entries in groups.items():
        if not isinstance(entries, list):
            raise ValueError(f'动作组格式错误：{group}')
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or not isinstance(entry.get('File'), str):
                raise ValueError(f'动作条目格式错误：{group}[{index}]')
            file = path.parent / entry['File']
            try:
                motion: object = json.loads(file.read_text(encoding='utf-8-sig'))
                meta = motion.get('Meta') if isinstance(motion, dict) else None
                duration = meta.get('Duration') if isinstance(meta, dict) else None
                if isinstance(duration, bool) or not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration <= 0:
                    raise ValueError('无效的动作时长')
                motions.append(MotionEntry(str(group), index, file, float(duration)))
            except (OSError, ValueError) as error:
                warnings.append(f'跳过动作 {file.name}：{error}')
    entries = refs.get('Expressions', [])
    if not isinstance(entries, list):
        raise ValueError('Expressions 必须是列表')
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('Name'), str) or not isinstance(entry.get('File'), str):
            raise ValueError('表情条目格式错误')
        file = path.parent / entry['File']
        if file.is_file():
            expressions.append(ExpressionEntry(entry['Name'], file))
        else:
            warnings.append(f'跳过缺失表情：{file.name}')
    return ModelResources(path, tuple(sorted(motions, key=lambda item: (item.name, item.group, item.index))),
                          tuple(sorted(expressions, key=lambda item: item.name)), tuple(warnings))


@dataclass(frozen=True)
class PreviewSelection:
    """保存一次播放使用的动作与表情快照。"""

    motion: MotionEntry | None = None
    expression: str | None = None

    @property
    def playable(self) -> bool:
        """判断当前组合是否至少选择了一项内容。"""
        return self.motion is not None or self.expression is not None

    @property
    def label(self) -> str:
        """生成人可读的组合摘要。"""
        return f'{self.motion.name if self.motion else "无动作"} + {self.expression or "无表情"}'


@dataclass
class PreviewState:
    """隔离待播放选择、当前演出及方向筛选状态。"""

    selected: PreviewSelection = field(default_factory=PreviewSelection)
    playing: PreviewSelection | None = None
    direction: Direction = 'C'
    blink: bool = True
    breath: bool = True
    mouth: bool = False

    def visible_motions(self, resources: ModelResources | None) -> tuple[MotionEntry, ...]:
        """仅筛选显示列表，不修改选择或播放状态。"""
        if resources is None:
            return ()
        return tuple(motion for motion in resources.motions if self.direction == 'all' or motion.direction == self.direction)


class PreviewController:
    """在渲染线程处理模型切换、组合播放和状态重置。"""

    def __init__(self, factory: Callable[[str], Live2DModelAdapter], size: tuple[int, int]) -> None:
        """注入模型工厂，使播放行为可在不创建图形窗口时验证。"""
        self.factory = factory
        self.size = size
        self.model: Live2DModelAdapter | None = None
        self.resources: ModelResources | None = None
        self.state = PreviewState()

    def load(self, path: Path, preserve_selection: bool = False) -> None:
        """成功准备新模型后才替换旧模型，重置时可保留待播放组合。"""
        resources = read_resources(path)
        candidate = self.factory(str(resources.path))
        try:
            if candidate.version != 'v3':
                raise ValueError('仅支持 V3 模型')
            candidate.Resize(*self.size)
            candidate.SetScale(1.0)
            candidate.SetOffset(0.0, 0.0)
            candidate.SetAutoBlinkEnable(self.state.blink)
            candidate.SetAutoBreathEnable(self.state.breath)
        except Exception:
            candidate.dispose()
            raise
        old = self.model
        self.model, self.resources = candidate, resources
        self.state.playing = None
        if not preserve_selection:
            self.state.selected = PreviewSelection()
            self.state.direction = 'C'
        else:
            previous = self.state.selected
            motion = next((item for item in resources.motions if previous.motion and (item.group, item.index, item.path) == (previous.motion.group, previous.motion.index, previous.motion.path)), None)
            expression = previous.expression if any(item.name == previous.expression for item in resources.expressions) else None
            self.state.selected = PreviewSelection(motion, expression)
        if old is not None:
            old.dispose()

    def clear_playback(self) -> None:
        """停止动作并清除表情、参数和姿势，避免组合之间相互残留。"""
        if self.model is None:
            return
        native = self.model.model
        methods = ('StopAllMotions', 'ResetExpressions', 'ResetParameters', 'ResetPose')
        for name in methods:
            if not callable(getattr(native, name, None)):
                raise RuntimeError(f'当前 live2d-py 缺少 {name}，无法可靠重置演出')
        for name in methods:
            getattr(native, name)()
        self.state.playing = None

    def play(self) -> bool:
        """在同一次帧更新之前启动动作和手动表情，禁止自动匹配。"""
        selection = self.state.selected
        if self.model is None or not selection.playable:
            return False
        self.clear_playback()
        try:
            if selection.motion and not self.model.StartMotion(
                selection.motion.group, selection.motion.index, priority=3, auto_expression=False
            ):
                raise RuntimeError('动作启动失败')
            if selection.expression and not self.model.SetExpression(selection.expression):
                raise RuntimeError('表情启动失败')
        except Exception:
            self.clear_playback()
            raise
        self.state.playing = selection
        return True

    def reset(self) -> None:
        """重新创建当前模型，连同物理状态一起恢复，同时保留选择。"""
        if self.resources is not None:
            self.load(self.resources.path, preserve_selection=True)

    def close(self) -> None:
        """在运行时会话关闭前释放最后一个模型。"""
        if self.model is not None:
            self.model.dispose()
            self.model = None
