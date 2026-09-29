"""显式启用的真实 Cubism 渲染检查，不在无图形环境中自动运行。"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.mark.skipif(os.environ.get("LIVE2D_EXPRESSION_RENDER_TEST") != "1", reason="需要真实 OpenGL 上下文")
def test_real_expression_roundtrip_and_dialog(tmp_path: Path) -> None:
    """复制真实模型，检查静态参数、导出再加载及非空像素，并导出 UI 截图。"""
    import numpy as np
    import pygame
    from OpenGL.GL import GL_COLOR_BUFFER_BIT, GL_RGB, GL_UNSIGNED_BYTE, glClear, glClearColor, glReadPixels, glViewport
    from PyQt5.QtWidgets import QApplication, QStyle, QStyleOptionSlider, QWidget
    from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtTest import QTest

    from live2d_support.custom_expressions import CustomExpressionStore, expression_document
    from live2d_support.expression_preview import ExpressionPreviewSession
    from live2d_support.runtime_session import Live2DRuntimeSession
    from ui.components.live2d_expression_editor import CustomExpressionDialog
    from ui.components.live2d_viewer_widgets import VIEWER_STYLE

    root = Path(__file__).resolve().parents[2]
    source = root / "live2d_related/tomori/extra_model/adv_live2d_tomori_001_casual_spring_01_extracted"
    copied = tmp_path / "model"
    shutil.copytree(source, copied)
    model_path = next(copied.glob("*.model3.json"))
    output = Path(os.environ.get("LIVE2D_EXPRESSION_RENDER_OUTPUT", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    pygame.init()
    pygame.display.set_mode((700, 850), pygame.OPENGL | pygame.DOUBLEBUF | pygame.HIDDEN)
    glViewport(0, 0, 700, 850)
    runtime = Live2DRuntimeSession()
    session = ExpressionPreviewSession()
    model = runtime.create_model(str(model_path))
    try:
        model.Resize(700, 850)
        model.SetScale(1.0)
        model.SetAutoBreathEnable(False)
        model.SetAutoBlinkEnable(False)
        command = {"model_path": str(model_path), "session_id": "render", "sequence": 1}
        result = session.execute(model, dict(command, action="begin"))
        assert result["ok"], result
        mouth = next(spec for spec in session.specs if spec.id == "ParamMouthForm")
        eye_l = next(spec for spec in session.specs if spec.id == "ParamEyeLOpen")
        eye_r = next(spec for spec in session.specs if spec.id == "ParamEyeROpen")
        data = expression_document([
            {"Id": eye_l.id, "Value": 0, "Blend": "Multiply"},
            {"Id": eye_r.id, "Value": eye_r.minimum, "Blend": "Overwrite"},
            {"Id": mouth.id, "Value": mouth.maximum - mouth.default, "Blend": "Add"}], 0, 0, session.specs)

        def frame(name: str) -> np.ndarray:
            """从真实 OpenGL 帧缓冲读取像素并保存画面。"""
            glClearColor(0.94, 0.95, 0.96, 1.0)
            glClear(GL_COLOR_BUFFER_BIT)
            model.Draw()
            pixels = bytes(glReadPixels(0, 0, 700, 850, GL_RGB, GL_UNSIGNED_BYTE))
            surface = pygame.image.fromstring(pixels, (700, 850), "RGB", True)
            pygame.image.save(surface, str(output / name))
            return np.frombuffer(pixels, dtype=np.uint8).copy()

        neutral = frame("neutral.png")
        assert session.execute(model, dict(command, action="preview", document=data, mode="static"))["ok"]
        static = frame("static-expression.png")
        assert np.std(static) > 15
        assert np.count_nonzero(neutral != static) > 100
        store = CustomExpressionStore(model_path)
        store.save("render_check", "闭眼微笑", data, session.specs)
        session.close()
        assert model.SetExpression("render_check")
        for _ in range(12):
            model.Update()
        rendered = frame("saved-expression.png")
        eye_index = next(index for index in range(model.GetParameterCount()) if model.GetParameter(index).id == eye_l.id)
        assert model.GetParameter(eye_index).value == pytest.approx(eye_l.minimum, abs=1e-4)
        assert np.mean(np.abs(static.astype(float) - rendered.astype(float))) < 3

        # 在同一个已加载实例上替换相同 ID，检查新操作数真正生效。
        opened = expression_document([{"Id": eye_l.id, "Value": eye_l.maximum, "Blend": "Overwrite"}],
                                     0, 0, session.specs)
        store.save("render_check", "睁眼", opened, session.specs, existing=True)
        assert model.SetExpression("render_check")
        model.Update()
        assert model.GetParameter(eye_index).value == pytest.approx(eye_l.maximum, abs=1e-4)
        store.save("render_check", "闭眼微笑", data, session.specs, existing=True)

        from live2d_support.performance_catalog import load_performance_catalog
        result = session.execute(model, dict(command, action="begin"))
        assert result["ok"]
        motion = next(iter(load_performance_catalog(model_path).motions))
        result = session.execute(model, dict(command, action="preview", mode="performance",
                                            document=data, motion=motion, mouth=True))
        assert result["ok"], result
        session.started_at -= 0.1
        model.Update()
        session.after_update()
        mouth_index = next(index for index in range(model.GetParameterCount())
                           if model.GetParameter(index).id == "ParamMouthOpenY")
        assert model.GetParameter(mouth_index).value > 0
        assert np.std(frame("motion-expression-mouth.png")) > 15
        session.close()

        app = QApplication.instance() or QApplication([])
        parent = QWidget()
        parent.setStyleSheet(VIEWER_STYLE)
        sent: list[dict[str, object]] = []
        dialog = CustomExpressionDialog(model_path, "render_check", sent.append, lambda _parent, _save: True, parent)
        dialog.show()
        app.processEvents()
        dialog.receive({"action": "begin", "ok": True, "session_id": dialog.session_id,
                        "parameters": [spec.payload() for spec in session.specs]})
        app.processEvents()
        dialog.grab().save(str(output / "expression-dialog.png"))
        assert dialog.save_button.isVisible() and dialog.save_button.isEnabled()
        assert dialog.rows and dialog.scroll.viewport().height() > 100
        slider = dialog.rows[0].slider
        for value in (1000, 8000, 2000, 9000, 4000):
            slider.setValue(value)
            QTest.qWait(30)
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        center = slider.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderHandle, slider).center()
        QTest.mousePress(slider, Qt.LeftButton, pos=center)
        assert slider.isSliderDown()
        target = QPoint(slider.width() - 10, center.y())
        # Cocoa 的 QTest.mouseMove 不携带合成按键状态，显式模拟按住左键的移动。
        QApplication.sendEvent(slider, QMouseEvent(QEvent.MouseMove, QPointF(target),
            QPointF(slider.mapToGlobal(target)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        QTest.mouseRelease(slider, Qt.LeftButton, pos=target)
        app.processEvents()
        assert slider.value() > 8000
        dialog.grab().save(str(output / "expression-dialog-dragged.png"))
        QTest.mouseClick(dialog.mode, Qt.LeftButton, pos=dialog.mode.tabRect(1).center())
        app.processEvents()
        assert dialog.preview_mode() == "performance"
        dialog.grab().save(str(output / "expression-dialog-performance.png"))
        assert dialog.mode.tabRect(1).right() < dialog.mode.width()
        dialog.reject()
        parent.close()
        print(f"Verified {len(session.specs)} parameters; screenshots: {output}")
    finally:
        session.close()
        model.dispose()
        runtime.close()
        pygame.quit()
