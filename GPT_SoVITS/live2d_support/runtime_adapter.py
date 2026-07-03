from __future__ import annotations

import gc
import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable, ClassVar, Iterable, Literal, cast

from live2d_support.expression_policy import (
    select_expression_for_motion,
    select_supported_expression as select_supported_expression_id,
    semantic_expression_candidates,
)
from live2d_support.motion_capabilities import (
    Live2DMotionCapabilities,
    motion_capabilities_from_motion_files_by_group,
)
from live2d_support.motion_selection import (
    MotionPosition,
    resolve_positioned_motion_group as resolve_positioned_motion_group_name,
    select_random_motion as select_random_motion_entry,
)
from log import get_logger


Live2DVersion = Literal["v2", "v3"]
MotionCallback = Callable[..., object]

logger = get_logger(__name__)

RUNTIME_MODULE_BY_VERSION: dict[Live2DVersion, str] = {
    "v2": "live2d.v2cpp",
    "v3": "live2d.v3",
}

PARAMETER_CANDIDATES: dict[str, tuple[str, str]] = {
    "mouth_open_y": ("PARAM_MOUTH_OPEN_Y", "ParamMouthOpenY"),
    "eye_l_open": ("PARAM_EYE_L_OPEN", "ParamEyeLOpen"),
    "eye_r_open": ("PARAM_EYE_R_OPEN", "ParamEyeROpen"),
}

BREATH_PARAMETER_ONLY_METHOD = "SetAutoBreathParameterOnlyEnable"


def _read_model_json(model_json_path: str) -> dict[str, object]:
    """读取 Live2D 模型 JSON，并保证顶层是对象。"""
    with open(model_json_path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Live2D 模型 JSON 顶层不是对象：{model_json_path}")
    return cast(dict[str, object], data)


def detect_live2d_runtime_version(model_json_path: str) -> Live2DVersion:
    """根据模型 JSON 结构判断应使用的 Live2D runtime 版本。"""
    path = Path(model_json_path)
    if path.name.endswith(".model3.json"):
        return "v3"

    data = _read_model_json(model_json_path)
    file_references = data.get("FileReferences")
    if isinstance(file_references, dict):
        moc = file_references.get("Moc")
        if isinstance(moc, str) and moc.endswith(".moc3"):
            return "v3"
        return "v3"

    model_file = data.get("model")
    if isinstance(model_file, str):
        return "v2"

    raise ValueError(f"无法识别 Live2D 模型版本：{model_json_path}")


def load_live2d_runtime(version: Live2DVersion) -> ModuleType:
    """按 Live2D 版本导入 runtime 模块。"""
    return importlib.import_module(RUNTIME_MODULE_BY_VERSION[version])


def _call_noarg(target: object, method_name: str) -> None:
    """如果目标对象存在指定无参方法，则调用它。"""
    method = getattr(target, method_name, None)
    if callable(method):
        method()


def _call_breath_parameter_only(target: object, enabled: bool) -> bool:
    """调用 runtime 支持的仅 ParamBreath 自动呼吸方法。"""
    method = getattr(target, BREATH_PARAMETER_ONLY_METHOD, None)
    if not callable(method):
        return False
    method(enabled)
    return True


def initialize_live2d_runtime(runtime: ModuleType) -> None:
    """初始化 Live2D runtime 及其 OpenGL 资源。"""
    _call_noarg(runtime, "init")
    _call_noarg(runtime, "glInit")


def release_live2d_runtime(runtime: ModuleType | None) -> None:
    """释放 Live2D runtime 及其 OpenGL 资源。"""
    if runtime is None:
        return
    _call_noarg(runtime, "dispose")
    _call_noarg(runtime, "glRelease")


def _collect_motion_groups(data: dict[str, object], version: Live2DVersion) -> frozenset[str]:
    """从模型 JSON 中收集动作组名。"""
    if version == "v3":
        file_references = data.get("FileReferences")
        if not isinstance(file_references, dict):
            return frozenset()
        motions = file_references.get("Motions")
    else:
        motions = data.get("motions")

    if not isinstance(motions, dict):
        return frozenset()
    return frozenset(str(group_name) for group_name in motions.keys())


def _collect_motion_files_by_group(data: dict[str, object], version: Live2DVersion) -> dict[str, tuple[str, ...]]:
    """从模型 JSON 中收集每个动作组对应的动作文件名。"""
    if version == "v3":
        file_references = data.get("FileReferences")
        if not isinstance(file_references, dict):
            return {}
        motions = file_references.get("Motions")
    else:
        motions = data.get("motions")

    if not isinstance(motions, dict):
        return {}

    motion_files_by_group: dict[str, tuple[str, ...]] = {}
    for group_name, entries in motions.items():
        if not isinstance(group_name, str) or not isinstance(entries, list):
            continue
        motion_files: list[str] = []
        for entry in entries:
            if isinstance(entry, str):
                motion_files.append(entry)
                continue
            if not isinstance(entry, dict):
                continue
            motion_file = entry.get("File")
            if not isinstance(motion_file, str):
                motion_file = entry.get("file")
            if isinstance(motion_file, str) and motion_file:
                motion_files.append(motion_file)
        motion_files_by_group[group_name] = tuple(motion_files)
    return motion_files_by_group


def _collect_expression_ids(data: dict[str, object], version: Live2DVersion) -> frozenset[str]:
    """从模型 JSON 中收集表情 ID。"""
    if version == "v3":
        file_references = data.get("FileReferences")
        if not isinstance(file_references, dict):
            return frozenset()
        expressions = file_references.get("Expressions")
        name_keys = ("Name", "name")
    else:
        expressions = data.get("expressions")
        name_keys = ("name", "Name")

    if not isinstance(expressions, list):
        return frozenset()

    expression_ids: set[str] = set()
    for expression in expressions:
        if not isinstance(expression, dict):
            continue
        for key in name_keys:
            value = expression.get(key)
            if isinstance(value, str) and value:
                expression_ids.add(value)
                break
    return frozenset(expression_ids)


@dataclass
class Live2DModelAdapter:
    """封装单个 Live2D 模型实例，隐藏 v2/v3 API 差异。"""

    PREVIEW_MOTION_GROUP: ClassVar[str] = "__dsakiko_motion_preview__"

    model_json_path: str
    version: Live2DVersion
    runtime: ModuleType
    model: object | None
    motion_groups: frozenset[str]
    motion_files_by_group: dict[str, tuple[str, ...]]
    expression_ids: frozenset[str]
    parameter_ids: frozenset[str]
    preview_motion_indices_by_path: dict[str, int]

    @classmethod
    def create(cls, model_json_path: str) -> Live2DModelAdapter:
        """创建并加载一个 Live2D 模型 adapter。"""
        version = detect_live2d_runtime_version(model_json_path)
        runtime = load_live2d_runtime(version)
        data = _read_model_json(model_json_path)
        model_class = getattr(runtime, "LAppModel")
        model = model_class()
        getattr(model, "LoadModelJson")(model_json_path)
        adapter = cls(
            model_json_path=model_json_path,
            version=version,
            runtime=runtime,
            model=model,
            motion_groups=_collect_motion_groups(data, version),
            motion_files_by_group=_collect_motion_files_by_group(data, version),
            expression_ids=_collect_expression_ids(data, version),
            parameter_ids=frozenset(),
            preview_motion_indices_by_path={},
        )
        adapter.refresh_parameter_ids()
        return adapter

    def _require_model(self) -> object:
        """返回当前模型实例，若已释放则抛出错误。"""
        if self.model is None:
            raise RuntimeError("Live2D 模型 adapter 已释放。")
        return self.model

    def dispose(self) -> None:
        """释放当前模型实例持有的资源。"""
        if self.model is None:
            return
        _call_noarg(self.model, "StopAllMotions")
        _call_noarg(self.model, "DestroyRenderer")
        self.model = None
        gc.collect()

    def refresh_parameter_ids(self) -> None:
        """从模型实例读取当前参数 ID 集合。"""
        model = self._require_model()
        parameter_ids: set[str] = set()
        try:
            count = int(getattr(model, "GetParameterCount")())
            for index in range(count):
                parameter = getattr(model, "GetParameter")(index)
                parameter_id = getattr(parameter, "id", "")
                if isinstance(parameter_id, str) and parameter_id:
                    parameter_ids.add(parameter_id)
        except Exception:
            logger.debug("读取 Live2D 参数列表失败：%s", self.model_json_path, exc_info=True)
        self.parameter_ids = frozenset(parameter_ids)

    def resize(self, width: int, height: int) -> None:
        """调整模型视口尺寸。"""
        getattr(self._require_model(), "Resize")(width, height)

    def Resize(self, width: int, height: int) -> None:
        """兼容旧调用风格，调整模型视口尺寸。"""
        self.resize(width, height)

    def update(self) -> None:
        """更新模型状态。"""
        getattr(self._require_model(), "Update")()

    def Update(self) -> None:
        """兼容旧调用风格，更新模型状态。"""
        self.update()

    def draw(self) -> None:
        """绘制模型。"""
        getattr(self._require_model(), "Draw")()

    def Draw(self) -> None:
        """兼容旧调用风格，绘制模型。"""
        self.draw()

    def set_auto_blink_enable(self, enabled: bool) -> None:
        """设置自动眨眼。"""
        getattr(self._require_model(), "SetAutoBlinkEnable")(enabled)

    def SetAutoBlinkEnable(self, enabled: bool) -> None:
        """兼容旧调用风格，设置自动眨眼。"""
        self.set_auto_blink_enable(enabled)

    def set_auto_breath_enable(self, enabled: bool) -> None:
        """设置自动呼吸，V3 开启时优先只驱动 ParamBreath。"""
        model = self._require_model()
        if self.version == "v3" and enabled:
            if _call_breath_parameter_only(model, True):
                return
            logger.warning(
                "当前 live2d.v3 runtime 不支持仅 ParamBreath 自动呼吸 API，"
                "将回退到完整 AutoBreath：%s",
                self.model_json_path,
            )
        getattr(model, "SetAutoBreathEnable")(enabled)

    def set_auto_breath_parameter_only_enable(self, enabled: bool) -> bool:
        """设置仅 ParamBreath 自动呼吸，runtime 不支持时返回 False。"""
        return _call_breath_parameter_only(self._require_model(), enabled)

    def SetAutoBreathParameterOnlyEnable(self, enabled: bool) -> bool:
        """兼容旧调用风格，设置仅 ParamBreath 自动呼吸。"""
        return self.set_auto_breath_parameter_only_enable(enabled)

    def SetAutoBreathEnable(self, enabled: bool) -> None:
        """兼容旧调用风格，设置自动呼吸。"""
        self.set_auto_breath_enable(enabled)

    def set_offset(self, offset_x: float, offset_y: float) -> bool:
        """设置模型显示平移，runtime 不支持时返回 False。"""
        set_offset = getattr(self._require_model(), "SetOffset", None)
        if not callable(set_offset):
            return False
        try:
            set_offset(offset_x, offset_y)
            return True
        except Exception:
            logger.debug("设置 Live2D 模型平移失败：%s", self.model_json_path, exc_info=True)
            return False

    def SetOffset(self, offset_x: float, offset_y: float) -> bool:
        """兼容旧调用风格，设置模型显示平移。"""
        return self.set_offset(offset_x, offset_y)

    def set_scale(self, scale: float) -> bool:
        """设置模型显示缩放，runtime 不支持时返回 False。"""
        set_scale = getattr(self._require_model(), "SetScale", None)
        if not callable(set_scale):
            return False
        try:
            set_scale(scale)
            return True
        except Exception:
            logger.debug("设置 Live2D 模型缩放失败：%s", self.model_json_path, exc_info=True)
            return False

    def SetScale(self, scale: float) -> bool:
        """兼容旧调用风格，设置模型显示缩放。"""
        return self.set_scale(scale)

    def set_expression_if_supported(self, expression_id: str) -> bool:
        """在模型支持指定表情时设置表情。"""
        if not expression_id:
            return False
        if expression_id not in self.expression_ids:
            logger.warning(
                "Live2D 模型不包含表情 '%s'，已跳过：%s",
                expression_id,
                self.model_json_path,
            )
            return False
        try:
            getattr(self._require_model(), "SetExpression")(expression_id)
            return True
        except Exception:
            logger.exception("设置 Live2D 表情失败：%s", expression_id)
            return False

    def SetExpression(self, expression_id: str) -> bool:
        """兼容旧调用风格，安全设置表情。"""
        return self.set_expression_if_supported(expression_id)

    def select_supported_expression(self, candidates: Iterable[str]) -> str | None:
        """从候选表情 ID 中选择当前模型支持的第一个表情。"""
        return select_supported_expression_id(candidates, self.expression_ids)

    def set_semantic_expression(self, semantic_name: str) -> bool:
        """按语义名设置表情，允许不同模型使用不同实际表情 ID。"""
        candidates = semantic_expression_candidates(semantic_name)
        if candidates is None:
            logger.warning("未知 Live2D 语义表情 '%s'，已跳过。", semantic_name)
            return False
        expression_id = self.select_supported_expression(candidates)
        if expression_id is None:
            logger.debug(
                "Live2D 模型没有可用于语义表情 '%s' 的候选：%s",
                semantic_name,
                self.model_json_path,
            )
            return False
        logger.debug(
            "Live2D 语义表情 '%s' 解析为 '%s'：%s",
            semantic_name,
            expression_id,
            self.model_json_path,
        )
        return self.set_expression_if_supported(expression_id)

    def SetSemanticExpression(self, semantic_name: str) -> bool:
        """兼容旧调用风格，按语义名设置表情。"""
        return self.set_semantic_expression(semantic_name)

    def resolve_positioned_motion_group(self, group_name: str, position: MotionPosition | None) -> str:
        """根据位置参数选择动作组，缺少位置组时回退到基础组。"""
        return resolve_positioned_motion_group_name(group_name, position, self.motion_groups)

    def motion_capabilities(self) -> Live2DMotionCapabilities:
        """返回当前模型持久动作组声明出的方向动作能力。"""
        return motion_capabilities_from_motion_files_by_group(
            self.motion_files_by_group,
            ignored_groups={self.PREVIEW_MOTION_GROUP},
        )

    def supports_positioned_motion(self, position: MotionPosition) -> bool:
        """判断当前模型是否支持指定方向的持久动作组。"""
        return self.motion_capabilities().supports_position(position)

    def supports_group_positioned_motion(self, group_name: str, position: MotionPosition) -> bool:
        """判断当前模型的指定标准动作组是否支持指定方向。"""
        return self.motion_capabilities().supports_group_position(group_name, position)

    def _motion_file_at(self, group_name: str, motion_index: int) -> str | None:
        """读取动作组中指定 index 对应的动作文件名。"""
        motion_files = self.motion_files_by_group.get(group_name)
        if motion_files is None:
            return None
        if motion_index < 0 or motion_index >= len(motion_files):
            return None
        return motion_files[motion_index]

    def _select_random_motion(self, group_name: str | None, position: MotionPosition | None) -> tuple[str, int] | None:
        """按 adapter 记录的动作文件列表选择一个随机动作。"""
        return select_random_motion_entry(group_name, position, self.motion_groups, self.motion_files_by_group)

    def _select_expression_for_motion(self, group_name: str, motion_file: str | None) -> str | None:
        """根据动作文件名和动作组名选择当前模型支持的自动表情。"""
        return select_expression_for_motion(group_name, motion_file, self.expression_ids)

    def _apply_auto_expression_for_motion(self, group_name: str, motion_index: int) -> None:
        """为即将播放的 V3 动作应用自动表情。"""
        if self.version != "v3":
            return
        motion_file = self._motion_file_at(group_name, motion_index)
        expression_id = self._select_expression_for_motion(group_name, motion_file)
        if expression_id is None:
            logger.debug(
                "未找到适用于 Live2D 动作的自动表情：%s[%d] %s",
                group_name,
                motion_index,
                self.model_json_path,
            )
            return
        logger.debug(
            "Live2D 自动表情：%s[%d] file=%s expression=%s",
            group_name,
            motion_index,
            motion_file or "",
            expression_id,
        )
        self.set_expression_if_supported(expression_id)

    def start_random_motion(
            self,
            group_name: str | None = None,
            priority: int = 3,
            on_start: MotionCallback | None = None,
            on_finish: MotionCallback | None = None,
            position: MotionPosition | None = None,
            auto_expression: bool = True,
    ) -> bool:
        """播放随机动作组，动作组缺失时返回 False。"""
        if self.version == "v3":
            selected_motion = self._select_random_motion(group_name, position)
            if selected_motion is None:
                logger.warning(
                    "Live2D 模型不包含可播放动作组 '%s'，已跳过：%s",
                    group_name or "",
                    self.model_json_path,
                )
                return False
            resolved_group_name, motion_index = selected_motion
            return self.start_motion(
                resolved_group_name,
                motion_index,
                priority,
                on_start,
                on_finish,
                None,
                auto_expression,
            )

        resolved_group_name = self.resolve_positioned_motion_group(group_name or "", position)
        if group_name and resolved_group_name not in self.motion_groups:
            logger.warning(
                "Live2D 模型不包含动作组 '%s'，已跳过：%s",
                resolved_group_name,
                self.model_json_path,
            )
            return False
        try:
            getattr(self._require_model(), "StartRandomMotion")(resolved_group_name, priority, on_start, on_finish)
            return True
        except Exception:
            logger.exception("播放 Live2D 随机动作失败：%s", resolved_group_name)
            return False

    def StartRandomMotion(
            self,
            group_name: str | None = None,
            priority: int = 3,
            on_start: MotionCallback | None = None,
            on_finish: MotionCallback | None = None,
            position: MotionPosition | None = None,
            auto_expression: bool = True,
    ) -> bool:
        """兼容旧调用风格，播放随机动作组。"""
        return self.start_random_motion(group_name, priority, on_start, on_finish, position, auto_expression)

    def start_motion(
            self,
            group_name: str,
            motion_index: int,
            priority: int,
            on_start: MotionCallback | None = None,
            on_finish: MotionCallback | None = None,
            position: MotionPosition | None = None,
            auto_expression: bool = True,
    ) -> bool:
        """播放指定动作组中的指定动作，动作组缺失时返回 False。"""
        resolved_group_name = self.resolve_positioned_motion_group(group_name, position)
        if resolved_group_name not in self.motion_groups:
            logger.warning(
                "Live2D 模型不包含动作组 '%s'，已跳过：%s",
                resolved_group_name,
                self.model_json_path,
            )
            return False
        if self.version == "v3":
            motion_files = self.motion_files_by_group.get(resolved_group_name, ())
            if motion_files and (motion_index < 0 or motion_index >= len(motion_files)):
                logger.warning(
                    "Live2D 模型动作 index 越界 '%s[%d]'，已跳过：%s",
                    resolved_group_name,
                    motion_index,
                    self.model_json_path,
                )
                return False
            if auto_expression:
                self._apply_auto_expression_for_motion(resolved_group_name, motion_index)
        try:
            getattr(self._require_model(), "StartMotion")(resolved_group_name, motion_index, priority, on_start, on_finish)
            return True
        except Exception:
            logger.exception("播放 Live2D 指定动作失败：%s[%d]", resolved_group_name, motion_index)
            return False

    def StartMotion(
            self,
            group_name: str,
            motion_index: int,
            priority: int,
            on_start: MotionCallback | None = None,
            on_finish: MotionCallback | None = None,
            position: MotionPosition | None = None,
            auto_expression: bool = True,
    ) -> bool:
        """兼容旧调用风格，播放指定动作。"""
        return self.start_motion(group_name, motion_index, priority, on_start, on_finish, position, auto_expression)

    def start_motion_file(
            self,
            motion_path: str,
            priority: int = 3,
            on_start: MotionCallback | None = None,
            on_finish: MotionCallback | None = None,
            auto_expression: bool = True,
    ) -> bool:
        """播放一个外部动作文件，用于动作编辑器预览。"""
        model = self._require_model()
        load_motion = getattr(model, "LoadMotion", None)
        start_loaded_motion = getattr(model, "StartLoadedMotion", None)
        if callable(load_motion) and callable(start_loaded_motion):
            try:
                motion_no = load_motion(motion_path)
                start_loaded_motion(motion_no)
                return True
            except Exception:
                logger.exception("播放 Live2D 外部动作文件失败：%s", motion_path)
                return False

        load_extra_motion = getattr(model, "LoadExtraMotion", None)
        if callable(load_extra_motion):
            group_name = self.PREVIEW_MOTION_GROUP
            try:
                motion_key = str(Path(motion_path).resolve())
                motion_index = self.preview_motion_indices_by_path.get(motion_key)
                if motion_index is None:
                    load_result = int(load_extra_motion(group_name, motion_path))
                    if load_result < 0:
                        logger.warning("Live2D V3 外部动作加载失败：%s", motion_path)
                        return False
                    motion_index = load_result
                    self.preview_motion_indices_by_path[motion_key] = motion_index
                if auto_expression and self.version == "v3":
                    expression_id = self._select_expression_for_motion(group_name, motion_path)
                    if expression_id is not None:
                        logger.debug(
                            "Live2D 预览动作自动表情：file=%s expression=%s",
                            motion_path,
                            expression_id,
                        )
                        self.set_expression_if_supported(expression_id)
                getattr(model, "StartMotion")(group_name, motion_index, priority, on_start, on_finish)
                self.motion_groups = frozenset((*self.motion_groups, group_name))
                motion_files = list(self.motion_files_by_group.get(group_name, ()))
                if motion_index == len(motion_files):
                    motion_files.append(motion_path)
                    self.motion_files_by_group[group_name] = tuple(motion_files)
                elif 0 <= motion_index < len(motion_files):
                    motion_files[motion_index] = motion_path
                    self.motion_files_by_group[group_name] = tuple(motion_files)
                return True
            except Exception:
                logger.exception("播放 Live2D V3 外部动作文件失败：%s", motion_path)
                return False

        logger.warning("当前 Live2D runtime 不支持按文件预览动作：%s", motion_path)
        return False

    def StartMotionFile(
            self,
            motion_path: str,
            priority: int = 3,
            on_start: MotionCallback | None = None,
            on_finish: MotionCallback | None = None,
            auto_expression: bool = True,
    ) -> bool:
        """兼容旧调用风格，播放一个外部动作文件。"""
        return self.start_motion_file(motion_path, priority, on_start, on_finish, auto_expression)

    def resolve_parameter_id(self, semantic_name: str) -> str | None:
        """把语义参数名解析为当前模型实际参数 ID。"""
        candidates = PARAMETER_CANDIDATES.get(semantic_name)
        if candidates is None:
            return None
        preferred = candidates[0] if self.version == "v2" else candidates[1]
        if preferred in self.parameter_ids:
            return preferred
        for candidate in candidates:
            if candidate in self.parameter_ids:
                return candidate
        return None

    def set_parameter_value(self, semantic_name: str, value: float) -> bool:
        """按语义名设置模型参数值。"""
        parameter_id = self.resolve_parameter_id(semantic_name)
        if parameter_id is None:
            logger.debug(
                "Live2D 模型不包含参数 '%s'，已跳过：%s",
                semantic_name,
                self.model_json_path,
            )
            return False
        try:
            getattr(self._require_model(), "SetParameterValue")(parameter_id, value)
            return True
        except Exception:
            logger.debug("设置 Live2D 参数失败：%s", parameter_id, exc_info=True)
            return False

    def set_parameter_value_by_id(self, parameter_id: str, value: float) -> bool:
        """按实际参数 ID 设置模型参数值。"""
        resolved_parameter_id = parameter_id
        if resolved_parameter_id not in self.parameter_ids:
            for semantic_name in PARAMETER_CANDIDATES:
                if parameter_id in PARAMETER_CANDIDATES[semantic_name]:
                    semantic_parameter_id = self.resolve_parameter_id(semantic_name)
                    if semantic_parameter_id is not None:
                        resolved_parameter_id = semantic_parameter_id
                    break
        if resolved_parameter_id not in self.parameter_ids:
            logger.debug(
                "Live2D 模型不包含参数 ID '%s'，已跳过：%s",
                parameter_id,
                self.model_json_path,
            )
            return False
        try:
            getattr(self._require_model(), "SetParameterValue")(resolved_parameter_id, value)
            return True
        except Exception:
            logger.debug("设置 Live2D 参数失败：%s", resolved_parameter_id, exc_info=True)
            return False

    def SetParameterValue(self, parameter_id: str, value: float) -> bool:
        """兼容旧调用风格，按参数 ID 设置模型参数值。"""
        return self.set_parameter_value_by_id(parameter_id, value)

    def get_parameter_value(self, semantic_name: str, default: float = 1.0) -> float:
        """按语义名读取模型参数值。"""
        parameter_id = self.resolve_parameter_id(semantic_name)
        if parameter_id is None:
            return default
        model = self._require_model()
        try:
            count = int(getattr(model, "GetParameterCount")())
            for index in range(count):
                parameter = getattr(model, "GetParameter")(index)
                if getattr(parameter, "id", "") == parameter_id:
                    value = float(getattr(parameter, "value", default))
                    return max(0.0, min(1.0, value))
        except Exception:
            logger.debug("读取 Live2D 参数失败：%s", parameter_id, exc_info=True)
        return default

    def GetParameterCount(self) -> int:
        """兼容旧调用风格，返回模型参数数量。"""
        return int(getattr(self._require_model(), "GetParameterCount")())

    def GetParameter(self, index: int) -> object:
        """兼容旧调用风格，返回指定索引的模型参数。"""
        return getattr(self._require_model(), "GetParameter")(index)
