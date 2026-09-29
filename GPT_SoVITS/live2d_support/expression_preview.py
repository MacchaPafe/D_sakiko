"""在渲染进程中管理表情草稿，静态调参不推进自动动画。"""

from __future__ import annotations

import tempfile
import math
import time
from pathlib import Path

from live2d_support.custom_expressions import ExpressionParameter, expression_document, read_document
from live2d_support.performance_catalog import cubism_motion_json, load_performance_catalog, object_mapping
from live2d_support.runtime_adapter import Live2DModelAdapter, NullLive2DModel


class ExpressionPreviewSession:
    """将带会话身份的命令限制在同一个模型实例上。"""

    def __init__(self) -> None:
        """初始化尚未绑定模型的编辑会话。"""
        self.model: Live2DModelAdapter | None = None
        self.session_id = ""
        self.static = False
        self.specs: list[ExpressionParameter] = []
        self.blink = False
        self.temporary: tempfile.TemporaryDirectory[str] | None = None
        self.mouth = False
        self.started_at = 0.0

    def reset(self) -> None:
        """停止临时演出并恢复默认参数和姿势。"""
        if self.model is None or self.model.model is None:
            return
        native = self.model.model
        self.model.reset_performance()
        for method in ("StopAllMotions", "ResetExpressions", "ResetParameters", "ResetPose"):
            getattr(native, method)()

    def close(self) -> None:
        """释放临时文件并恢复进入会话前的自动眨眼设置。"""
        self.reset()
        if self.model is not None and self.model.model is not None:
            self.model.SetAutoBlinkEnable(self.blink)
        self.model = None
        self.static = False
        self.mouth = False
        self.session_id = ""
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None

    def execute(self, model: Live2DModelAdapter | NullLive2DModel,
                command: dict[str, object]) -> dict[str, object]:
        """返回带会话及序号的结果，使界面忽略过期回执。"""
        result = {"type": "expression_editor", "session_id": command.get("session_id"),
                  "sequence": command.get("sequence"), "action": command.get("action"), "ok": False}
        try:
            if not isinstance(model, Live2DModelAdapter) or model.version != "v3":
                raise ValueError("请等待 V3 模型加载完成")
            if Path(str(command.get("model_path"))).resolve() != Path(model.model_json_path).resolve():
                raise ValueError("模型已切换，请重新打开编辑器")
            action = command.get("action")
            if action == "begin":
                self.close()
                self.model = model
                self.session_id = str(command["session_id"])
                self.blink = model.auto_blink_enabled
                self.temporary = tempfile.TemporaryDirectory(prefix="dsakiko-expression-")
                self.specs = []
                document = read_document(Path(model.model_json_path))
                display = object_mapping(document.get("FileReferences")).get("DisplayInfo")
                labels: dict[str, str] = {}
                if isinstance(display, str):
                    try:
                        info = read_document(Path(model.model_json_path).parent / display)
                        labels = {str(item["Id"]): str(item.get("Name", item["Id"]))
                                  for item in info.get("Parameters", []) if isinstance(item, dict) and "Id" in item}
                    except (OSError, ValueError):
                        pass
                for index in range(model.GetParameterCount()):
                    parameter = model.GetParameter(index)
                    key = str(getattr(parameter, "id"))
                    self.specs.append(ExpressionParameter(key, labels.get(key, key),
                        float(getattr(parameter, "min")), float(getattr(parameter, "max")),
                        float(getattr(parameter, "default"))))
                self.reset()
                self.static = True
                return dict(result, ok=True, parameters=[item.payload() for item in self.specs], message="参数已加载")
            if model is not self.model or command.get("session_id") != self.session_id:
                raise ValueError("编辑会话已结束，请重新打开")
            if action == "end":
                self.close()
                return dict(result, ok=True, message="已退出表情编辑")
            if action != "preview":
                raise ValueError("未知编辑命令")
            data = object_mapping(command.get("document"))
            parameters = data.get("Parameters", [])
            if not isinstance(parameters, list):
                raise ValueError("参数格式错误")
            document = expression_document(parameters, float(data.get("FadeInTime", 0.5)),
                                           float(data.get("FadeOutTime", 0.5)), self.specs)
            self.reset()
            self.static = command.get("mode") != "performance"
            self.mouth = bool(command.get("mouth")) and not self.static
            self.started_at = time.monotonic()
            if self.static:
                by_id = {item.id: item for item in self.specs}
                for entry in parameters:
                    key, value, blend = str(entry["Id"]), float(entry["Value"]), str(entry.get("Blend", "Add"))
                    base = by_id[key].default
                    target = value if blend == "Overwrite" else base + value if blend == "Add" else base * value
                    getattr(model.model, "SetParameterValue")(key, target)
            else:
                motion = str(command.get("motion") or "")
                if motion:
                    asset = load_performance_catalog(model.model_json_path).motion(motion)
                    if asset is None or not model._start_performance_motion(asset.file, asset.entry, None):
                        raise ValueError("搭配动作未能启动")
                if self.temporary is None:
                    raise ValueError("临时表情目录已关闭")
                path = Path(self.temporary.name) / "draft.exp3.json"
                path.write_text(cubism_motion_json(document), encoding="utf-8")
                getattr(model.model, "LoadExtraExpression")("__dsakiko_custom_preview__", str(path))
                getattr(model.model, "SetExpression")("__dsakiko_custom_preview__")
                model.performance_expression_active = True
                model._custom_expression_active = True
            return dict(result, ok=True, message="静态编辑" if self.static else "正在预览实际演出")
        except (OSError, ValueError, TypeError, AttributeError, RuntimeError) as error:
            return dict(result, message=str(error))

    def after_update(self) -> None:
        """在演出更新后模拟口型驱动，与实际说话的参数覆盖顺序保持一致。"""
        if not self.mouth or self.model is None or self.model.model is None:
            return
        spec = next((item for item in self.specs if item.id == "ParamMouthOpenY"), None)
        if spec is not None:
            value = max(0.0, math.sin((time.monotonic() - self.started_at) * 9))
            self.model.SetParameterValue(spec.id, spec.minimum + value * (spec.maximum - spec.minimum))
