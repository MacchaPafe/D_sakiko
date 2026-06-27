from __future__ import annotations

import gc
import importlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable, ClassVar, Literal, cast

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
    expression_ids: frozenset[str]
    parameter_ids: frozenset[str]

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
            expression_ids=_collect_expression_ids(data, version),
            parameter_ids=frozenset(),
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
        """设置自动呼吸。"""
        getattr(self._require_model(), "SetAutoBreathEnable")(enabled)

    def SetAutoBreathEnable(self, enabled: bool) -> None:
        """兼容旧调用风格，设置自动呼吸。"""
        self.set_auto_breath_enable(enabled)

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

    def start_random_motion(
            self,
            group_name: str,
            priority: int,
            on_start: MotionCallback | None = None,
            on_finish: MotionCallback | None = None,
    ) -> bool:
        """播放随机动作组，动作组缺失时返回 False。"""
        if group_name not in self.motion_groups:
            # 由于主程序调用 IDLE 动作的频率太高了，这个日志会刷屏，暂时去掉
            # logger.warning(
            #     "Live2D 模型不包含动作组 '%s'，已跳过：%s",
            #     group_name,
            #     self.model_json_path,
            # )
            return False
        try:
            getattr(self._require_model(), "StartRandomMotion")(group_name, priority, on_start, on_finish)
            return True
        except Exception:
            logger.exception("播放 Live2D 随机动作失败：%s", group_name)
            return False

    def StartRandomMotion(
            self,
            group_name: str,
            priority: int,
            on_start: MotionCallback | None = None,
            on_finish: MotionCallback | None = None,
    ) -> bool:
        """兼容旧调用风格，播放随机动作组。"""
        return self.start_random_motion(group_name, priority, on_start, on_finish)

    def start_motion(
            self,
            group_name: str,
            motion_index: int,
            priority: int,
            on_start: MotionCallback | None = None,
            on_finish: MotionCallback | None = None,
    ) -> bool:
        """播放指定动作组中的指定动作，动作组缺失时返回 False。"""
        if group_name not in self.motion_groups:
            # 主程序调用 IDLE 动作的频率太高了，这个日志会刷屏，暂时去掉
            # logger.warning(
            #     "Live2D 模型不包含动作组 '%s'，已跳过：%s",
            #     group_name,
            #     self.model_json_path,
            # )
            return False
        try:
            getattr(self._require_model(), "StartMotion")(group_name, motion_index, priority, on_start, on_finish)
            return True
        except Exception:
            logger.exception("播放 Live2D 指定动作失败：%s[%d]", group_name, motion_index)
            return False

    def StartMotion(
            self,
            group_name: str,
            motion_index: int,
            priority: int,
            on_start: MotionCallback | None = None,
            on_finish: MotionCallback | None = None,
    ) -> bool:
        """兼容旧调用风格，播放指定动作。"""
        return self.start_motion(group_name, motion_index, priority, on_start, on_finish)

    def start_motion_file(
            self,
            motion_path: str,
            priority: int = 3,
            on_start: MotionCallback | None = None,
            on_finish: MotionCallback | None = None,
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
                load_result = int(load_extra_motion(group_name, motion_path))
                if load_result < 0:
                    logger.warning("Live2D V3 外部动作加载失败：%s", motion_path)
                    return False
                motion_index = max(0, load_result - 1)
                get_motion_groups = getattr(model, "GetMotionGroups", None)
                if callable(get_motion_groups):
                    motion_groups = get_motion_groups()
                    if isinstance(motion_groups, dict):
                        group_count = motion_groups.get(group_name)
                        if group_count is not None:
                            motion_index = max(0, int(group_count) - 1)
                getattr(model, "StartMotion")(group_name, motion_index, priority, on_start, on_finish)
                self.motion_groups = frozenset((*self.motion_groups, group_name))
                return True
            except Exception:
                logger.exception("播放 Live2D V3 外部动作文件失败：%s", motion_path)
                return False

        logger.warning("当前 Live2D runtime 不支持按文件预览动作：%s", motion_path)
        return False

    def prepare_preview_motion_file(self, motion_path: str) -> bool:
        """为不支持动态加载外部动作的 V3 runtime 写入预览动作组。"""
        if self.version != "v3":
            return False
        motion_path_obj = Path(motion_path)
        model_json_path_obj = Path(self.model_json_path)
        try:
            motion_file = os.path.relpath(motion_path_obj, model_json_path_obj.parent).replace("\\", "/")
        except ValueError:
            motion_file = motion_path_obj.name
        if motion_file.startswith("../"):
            motion_file = motion_path_obj.name

        model_data = _read_model_json(self.model_json_path)
        file_references = model_data.setdefault("FileReferences", {})
        if not isinstance(file_references, dict):
            raise ValueError(f"Live2D V3 模型 FileReferences 格式错误：{self.model_json_path}")
        motions = file_references.setdefault("Motions", {})
        if not isinstance(motions, dict):
            motions = {}
            file_references["Motions"] = motions
        motions[self.PREVIEW_MOTION_GROUP] = [{"File": motion_file}]
        with open(self.model_json_path, "w", encoding="utf-8") as file:
            json.dump(model_data, file, ensure_ascii=False, indent=4)
        return True

    def remove_preview_motion_group(self) -> None:
        """从 model3.json 中移除动作编辑器临时预览动作组。"""
        if self.version != "v3":
            return
        try:
            model_data = _read_model_json(self.model_json_path)
            file_references = model_data.get("FileReferences")
            if not isinstance(file_references, dict):
                return
            motions = file_references.get("Motions")
            if not isinstance(motions, dict):
                return
            if self.PREVIEW_MOTION_GROUP not in motions:
                return
            motions.pop(self.PREVIEW_MOTION_GROUP, None)
            with open(self.model_json_path, "w", encoding="utf-8") as file:
                json.dump(model_data, file, ensure_ascii=False, indent=4)
        except Exception:
            logger.debug("移除 Live2D V3 预览动作组失败：%s", self.model_json_path, exc_info=True)

    def StartMotionFile(
            self,
            motion_path: str,
            priority: int = 3,
            on_start: MotionCallback | None = None,
            on_finish: MotionCallback | None = None,
    ) -> bool:
        """兼容旧调用风格，播放一个外部动作文件。"""
        return self.start_motion_file(motion_path, priority, on_start, on_finish)

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
