"""执行编辑器预览命令并返回实际播放结果，不依赖 Qt。"""

from __future__ import annotations

import logging
from pathlib import Path

from live2d_support.runtime_adapter import Live2DModelAdapter, NullLive2DModel


def execute_viewer_preview(model: Live2DModelAdapter | NullLive2DModel, command: object) -> dict[str, object]:
    """模型未就绪、目标已切换或通道失败时返回失败，不把已选中当成已播放。"""
    payload = command if isinstance(command, dict) else {"file": str(command)}
    result: dict[str, object] = {"request_id": payload.get("request_id"), "ok": False}
    if not isinstance(model, Live2DModelAdapter):
        return dict(result, message="模型尚未加载，无法预览")
    target = payload.get("model_path")
    if target and Path(str(target)).resolve() != Path(model.model_json_path).resolve():
        return dict(result, message="模型已切换，请重新选择预览")
    try:
        if payload.get("type") == "performance":
            motion, expression = str(payload.get("motion") or ""), str(payload.get("expression") or "")
            if motion:
                model.reset_performance()
                direction = payload.get("direction")
                model.apply_performance(payload, "like", direction=direction if direction in {"C", "L", "R"} else "C", context="preview")
                state = model.performance_state
                ok = bool(state and state.motion_id == motion and state.motion_file
                          and (not expression or state.expression == expression))
                label = f"{motion} · {expression}" if expression else motion
            else:
                ok = model.SetExpression(expression)
                if ok:
                    model.performance_expression_active = True
                label = expression
        else:
            file = str(payload.get("file") or "")
            ok = model.StartMotionFile(file)
            label = Path(file).name
        return dict(result, ok=ok, message=f"正在预览：{label}" if ok else f"预览未完整启动：{label}，请检查资源")
    except Exception:
        logging.getLogger(__name__).exception("编辑器预览失败")
        return dict(result, message="预览失败，请检查模型资源或查看日志")
