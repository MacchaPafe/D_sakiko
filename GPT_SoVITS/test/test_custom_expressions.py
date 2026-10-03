"""覆盖自定义资源生命周期、草稿交互和运行中模型刷新。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from queue import Queue
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PyQt5.QtCore import QPoint, QPointF, Qt
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live2d_support.custom_expressions import (
    CustomExpressionStore, ExpressionParameter, expression_document, read_document,
)
from live2d_support.expression_preview import ExpressionPreviewSession
from live2d_support.model_normalizer import normalize_model3_for_project
from live2d_support.model_importer import import_live2d_model
from live2d_support.performance_catalog import load_performance_catalog, performance_config_path
from live2d_support.runtime_adapter import Live2DModelAdapter
from ui.components.live2d_expression_editor import CustomExpressionDialog, ParameterRow

SPECS = [ExpressionParameter("ParamEyeLOpen", "左眼开合", 0, 1, 1),
         ExpressionParameter("ParamMouthForm", "嘴形", -1, 1, 0)]


@pytest.fixture
def model_path(tmp_path: Path) -> Path:
    """创建具有原始表情及未知字段的模型入口。"""
    (tmp_path / "original.exp3.json").write_text(json.dumps({"Type": "Live2D Expression", "Parameters": [
        {"Id": "ParamEyeLOpen", "Value": 0.5, "Blend": "Multiply"}]}))
    path = tmp_path / "sample.model3.json"
    path.write_text(json.dumps({"Version": 3, "UserMetadata": {"keep": True}, "FileReferences": {
        "Expressions": [{"Name": "original", "File": "original.exp3.json"}], "Motions": {}}}))
    return path


def document(value: float = 0.3) -> dict[str, object]:
    """创建仅覆盖嘴形的有效表情，不写入其他参数。"""
    return expression_document([{"Id": "ParamMouthForm", "Value": value, "Blend": "Overwrite"}], 0.2, 0.4, SPECS)


def test_save_update_delete_and_ai_projection(model_path: Path) -> None:
    """创建后 AI 可选，修改保留元数据，删除使预设失效而保留原资源。"""
    store = CustomExpressionStore(model_path)
    original = (model_path.parent / "original.exp3.json").read_bytes()
    store.save("soft_smile", "克制地微笑", document(), SPECS)
    catalog = load_performance_catalog(model_path)
    assert catalog.prompt_projection()["expressions"]["soft_smile"] == "克制地微笑"
    assert read_document(model_path)["UserMetadata"] == {"keep": True}
    assert read_document(store.target("soft_smile"))["Parameters"] == document()["Parameters"]
    config = read_document(store.config_path)
    config["presets"] = [{"id": "p", "name": "轻笑", "motion": "nod", "expression": "soft_smile"}]
    store.config_path.write_text(json.dumps(config))
    assert store.referenced_presets("soft_smile") == ["轻笑"]
    store.save("soft_smile", "稍微微笑", document(0.4), SPECS, existing=True)
    assert read_document(store.target("soft_smile"))["Parameters"][0]["Value"] == 0.4
    store.delete("soft_smile")
    assert not store.target("soft_smile").exists()
    catalog = load_performance_catalog(model_path)
    assert "soft_smile" not in catalog.expressions
    assert len(catalog.presets) == 1 and not catalog.valid_presets()
    assert (model_path.parent / "original.exp3.json").read_bytes() == original


@pytest.mark.parametrize("name", ["../bad", "auto", "with space", "中文", "1smile", "a" * 65, "original"])
def test_reject_unsafe_or_conflicting_names(model_path: Path, name: str) -> None:
    """非法路径、保留名称和原模型名称都不能覆盖资源。"""
    store = CustomExpressionStore(model_path)
    before = model_path.read_bytes()
    with pytest.raises(ValueError):
        store.save(name, "描述", document(), SPECS)
    assert model_path.read_bytes() == before


def test_write_failure_rolls_back_all_files(model_path: Path) -> None:
    """最后一步写入失败后，模型、描述和表情内容都恢复原样。"""
    store = CustomExpressionStore(model_path)
    store.save("smile", "微笑", document(), SPECS)
    before = {path: path.read_bytes() for path in (model_path, store.config_path, store.target("smile"))}
    from live2d_support import custom_expressions
    original = custom_expressions.os.replace

    def failing_write(source: Path, path: Path) -> None:
        """只在模型提交时模拟磁盘故障，允许回滚先前文件。"""
        if path == model_path:
            raise OSError("模拟写入失败")
        original(source, path)

    with patch.object(custom_expressions.os, "replace", side_effect=failing_write), pytest.raises(OSError):
        store.save("smile", "已修改", document(0.8), SPECS, existing=True)
    assert all(path.read_bytes() == content for path, content in before.items())


def test_corrupt_config_and_original_resource_remain_untouched(model_path: Path) -> None:
    """损坏配置不能被空配置覆盖，原表情不能被删除或当作自定义修改。"""
    store = CustomExpressionStore(model_path)
    with pytest.raises(ValueError):
        store.delete("original")
    with pytest.raises(ValueError):
        store.save("original", "原表情", document(), SPECS, existing=True)
    store.config_path.write_text("{broken")
    with pytest.raises(ValueError):
        store.save("smile", "微笑", document(), SPECS)
    assert store.config_path.read_text() == "{broken"


def test_normalization_preserves_custom_ownership(model_path: Path) -> None:
    """首次规范化后，自定义文件仍能继续编辑和删除。"""
    store = CustomExpressionStore(model_path)
    store.save("smile", "微笑", document(), SPECS)
    normalize_model3_for_project(str(model_path))
    assert load_performance_catalog(model_path).expressions["smile"] == store.owned()["smile"]
    store.save("smile", "新描述", document(), SPECS, existing=True)
    store.delete("smile")


def test_import_keeps_custom_expression_editable(model_path: Path, tmp_path: Path) -> None:
    """导入模型后仍保留自定义所有权、描述及可编辑文件位置。"""
    source = CustomExpressionStore(model_path)
    source.save("smile", "微笑", document(), SPECS)
    root = tmp_path / "imported"
    (root / "tomori").mkdir(parents=True)
    result = import_live2d_model(str(model_path), "tomori", str(root))
    target = CustomExpressionStore(Path(result.model_json_path))
    target.save("smile", "导入后修改", document(0.8), SPECS, existing=True)
    assert load_performance_catalog(target.model_path).descriptions["expressions"]["smile"] == "导入后修改"
    assert read_document(source.target("smile"))["Parameters"][0]["Value"] == 0.3
    target.delete("smile")
    assert source.target("smile").exists()


@pytest.mark.parametrize("entry", [
    {"Id": "missing", "Value": 0}, {"Id": "ParamMouthForm", "Value": float("nan")},
    {"Id": "ParamMouthForm", "Value": 4, "Blend": "Overwrite"},
    {"Id": "ParamMouthForm", "Value": 0, "Blend": "Unknown"},
])
def test_invalid_parameters_rejected(entry: dict[str, object]) -> None:
    """缺失参数、非有限数、越界值和未知混合方式不能保存。"""
    with pytest.raises(ValueError):
        expression_document([entry], 0.5, 0.5, SPECS)


def adapter(model_path: Path) -> Live2DModelAdapter:
    """用可观察原生对象构建完整适配器，保持真实解析逻辑。"""
    native = Mock()
    native.GetParamCount.return_value = len(SPECS)
    native.GetParamIds.return_value = [spec.id for spec in SPECS]
    native.GetParamValueByIndex.return_value = 0.0
    native.GetParamMinByIndex.side_effect = lambda index: SPECS[index].minimum
    native.GetParamMaxByIndex.side_effect = lambda index: SPECS[index].maximum
    native.GetParamDefaultByIndex.side_effect = lambda index: SPECS[index].default
    return Live2DModelAdapter(str(model_path), "v3", ModuleType("fake"), native, frozenset(), {},
                              frozenset({"original"}), frozenset(item.id for item in SPECS), {})


def test_running_adapter_loads_new_and_modified_expression(model_path: Path) -> None:
    """已创建的模型能加载新增表情，同 ID 修改不会被去重吞掉。"""
    model = adapter(model_path)
    native = model.model
    store = CustomExpressionStore(model_path)
    store.save("smile", "微笑", document(), SPECS)
    selection = {"expression": "smile"}
    model.apply_performance(selection, "like", context="same")
    assert model.performance_state.expression == "smile"
    assert native.LoadExtraExpression.call_count == 1
    model.apply_performance(selection, "like", context="same")
    assert native.SetExpression.call_count == 1
    store.save("smile", "微笑", document(0.6), SPECS, existing=True)
    model.apply_performance(selection, "like", context="same")
    assert native.LoadExtraExpression.call_count == native.SetExpression.call_count == 2
    store.delete("smile")
    assert not model.SetExpression("smile")


def test_preview_session_static_performance_and_stale_commands(model_path: Path) -> None:
    """静态混合按默认基准计算，演出读取同份草稿，旧会话不能修改模型。"""
    model = adapter(model_path)
    native = model.model
    session = ExpressionPreviewSession()
    command = {"type": "expression_editor", "session_id": "one", "sequence": 1, "model_path": str(model_path)}
    assert session.execute(model, dict(command, action="begin"))["ok"]
    assert session.static
    data = expression_document([{"Id": "ParamEyeLOpen", "Value": 0.4, "Blend": "Multiply"}], 0.5, 0.5, SPECS)
    assert session.execute(model, dict(command, action="preview", document=data, mode="static"))["ok"]
    native.SetParamById.assert_called_with("ParamEyeLOpen", 0.4)
    assert not session.execute(model, dict(command, session_id="old", action="end"))["ok"]
    assert session.static
    assert session.execute(model, dict(command, action="preview", document=data, mode="performance"))["ok"]
    assert not session.static
    path = Path(native.LoadExtraExpression.call_args.args[1])
    assert read_document(path) == data
    assert session.execute(model, dict(command, action="end"))["ok"]
    assert not path.exists() and session.model is None


@pytest.fixture(scope="session")
def app() -> QApplication:
    """保留 Qt 应用用于真实控件交互。"""
    return QApplication.instance() or QApplication([])


def test_parameter_reset_inclusion_and_blend_conversion(app: QApplication) -> None:
    """覆盖、叠加、相乘切换保持目标值，重置不会取消纳入。"""
    parent = QWidget()
    row = ParameterRow(SPECS[0], lambda: None, parent)
    row.value.setValue(0.4)
    assert row.included.isChecked()
    row.blend.setCurrentIndex(row.blend.findData("Add"))
    assert row.value.value() == -0.6
    row.blend.setCurrentIndex(row.blend.findData("Multiply"))
    assert row.value.value() == 0.4
    row.reset_value()
    assert row.value.value() == 1 and row.included.isChecked()
    row.included.setChecked(False)
    row.reset_value()
    assert not row.included.isChecked()
    parent.close()


def test_dialog_copy_save_cancel_and_close(app: QApplication, model_path: Path) -> None:
    """打开原资源即提供可编辑草稿，保存立即进入目录，取消保留修改。"""
    sent: list[dict[str, object]] = []
    confirm = Mock(return_value=False)
    parent = QWidget()
    dialog = CustomExpressionDialog(model_path, "original", sent.append, confirm, parent)
    dialog.show()
    app.processEvents()
    dialog.receive({"session_id": dialog.session_id, "action": "begin", "ok": True,
                    "parameters": [item.payload() for item in SPECS]})
    assert dialog.save_button.isEnabled() and not dialog.delete_button.isEnabled()
    assert dialog.selected == "" and not dialog.dirty
    assert dialog.name.text() == "original_custom"
    assert not CustomExpressionStore(model_path).target("original_custom").exists()
    assert next(row for row in dialog.rows if row.spec.id == "ParamEyeLOpen").mode() == "Multiply"
    dialog.name.setText("gentle")
    dialog.description.setPlainText("温柔地看着对方")
    assert dialog.save_expression()
    assert dialog.name.isReadOnly() and dialog.delete_button.isEnabled()
    dialog.description.setPlainText("尚未保存的描述")
    dialog.selector.setCurrentIndex(dialog.selector.findData("original"))
    assert dialog.selected == "gentle" and dialog.dirty
    dialog.reject()
    assert dialog.isVisible() and not dialog.closed
    assert not any(item.get("action") == "end" for item in sent)
    confirm.return_value = True
    dialog.reject()
    assert dialog.closed and sent[-1]["action"] == "end"
    assert load_performance_catalog(model_path).descriptions["expressions"]["gentle"] == "温柔地看着对方"
    parent.close()


def test_wheel_scrolls_without_editing_parameters(app: QApplication, model_path: Path) -> None:
    """鼠标滚轮经过滑条、数值框和混合选项时，只滚动参数列表。"""
    parent = QWidget()
    dialog = CustomExpressionDialog(model_path, "", lambda _command: None,
                                    lambda _parent, _save: True, parent)
    dialog.show()
    app.processEvents()
    specs = [ExpressionParameter(f"ParamTest{index}", f"测试 {index}", -1, 1, 0) for index in range(40)]
    dialog.receive({"session_id": dialog.session_id, "action": "begin", "ok": True,
                    "parameters": [item.payload() for item in specs]})
    app.processEvents()
    row = dialog.rows[0]
    scrollbar = dialog.scroll.verticalScrollBar()
    assert scrollbar.maximum() > 0
    for control in (row.slider, row.value, row.blend):
        for pixels, angle, phase in ((QPoint(), QPoint(0, -120), Qt.NoScrollPhase),
                                      (QPoint(0, -30), QPoint(), Qt.ScrollUpdate)):
            scrollbar.setValue(0)
            control.setFocus()
            center = control.rect().center()
            event = QWheelEvent(QPointF(center), QPointF(control.mapToGlobal(center)), pixels, angle,
                                Qt.NoButton, Qt.NoModifier, phase, False)
            QApplication.sendEvent(control, event)
            assert scrollbar.value() > 0
            assert row.value.value() == 0 and row.mode() == "Overwrite"
            assert not row.included.isChecked() and not dialog.dirty
    dialog.reject()
    parent.close()


def test_modes_are_directly_clickable_and_auto_draft_can_be_discarded(app: QApplication, model_path: Path) -> None:
    """两个完整可见的模式可直接切换，自动准备的未修改草稿关闭时无需确认。"""
    parent = QWidget()
    confirm = Mock(return_value=False)
    sent: list[dict[str, object]] = []
    dialog = CustomExpressionDialog(model_path, "original", sent.append, confirm, parent)
    dialog.show()
    app.processEvents()
    dialog.receive({"session_id": dialog.session_id, "action": "begin", "ok": True,
                    "parameters": [item.payload() for item in SPECS]})
    app.processEvents()
    QTest.mouseClick(dialog.mode, Qt.LeftButton, pos=dialog.mode.tabRect(1).center())
    dialog.preview()
    assert sent[-1]["mode"] == "performance" and dialog.motion.isEnabled()
    QTest.mouseClick(dialog.mode, Qt.LeftButton, pos=dialog.mode.tabRect(0).center())
    dialog.preview()
    assert sent[-1]["mode"] == "static" and not dialog.motion.isEnabled()
    dialog.reject()
    confirm.assert_not_called()
    assert dialog.closed and not CustomExpressionStore(model_path).target("original_custom").exists()
    parent.close()


def test_dialog_deletion_requires_confirmation(app: QApplication, model_path: Path) -> None:
    """删除取消不改文件，确认后回到新草稿并保留原模型表情。"""
    store = CustomExpressionStore(model_path)
    store.save("smile", "微笑", document(), SPECS)
    parent = QWidget()
    dialog = CustomExpressionDialog(model_path, "smile", lambda _command: None,
                                    lambda _parent, _save: True, parent)
    dialog.receive({"session_id": dialog.session_id, "action": "begin", "ok": True,
                    "parameters": [item.payload() for item in SPECS]})
    with patch("ui.components.live2d_expression_editor.QMessageBox.question", return_value=QMessageBox.No):
        dialog.delete_expression()
    assert store.target("smile").exists()
    with patch("ui.components.live2d_expression_editor.QMessageBox.question", return_value=QMessageBox.Yes):
        dialog.delete_expression()
    assert not store.target("smile").exists() and dialog.selected == ""
    assert "original" in dialog.catalog.expressions
    dialog.reject()
    parent.close()


def test_viewer_routes_editor_results_separately(app: QApplication, model_path: Path) -> None:
    """跨进程回执到达对话框通道，不覆盖普通预览请求状态。"""
    from live2d_viewer import ViewerGUI

    character = SimpleNamespace(character_name="角色", character_folder_name="role",
                                live2d_json=str(model_path), icon_path=None)
    motions: Queue[object] = Queue()
    changes: Queue[object] = Queue()
    results: Queue[object] = Queue()
    window = ViewerGUI([character], motions, changes, results)
    received: list[object] = []
    window.performance_editor.expressionResult.connect(received.append)
    try:
        window.send_preview("regular.motion3.json")
        regular = motions.get_nowait()
        window.send_preview({"type": "expression_editor", "action": "begin", "session_id": "test"})
        editor = motions.get_nowait()
        assert editor["model_path"] == str(model_path.resolve())
        assert window._preview_request_id == regular["request_id"]
        reply = {"type": "expression_editor", "action": "begin", "session_id": "test", "ok": True,
                 "parameters": [item.payload() for item in SPECS]}
        results.put(reply)
        window.poll_preview_results()
        assert received == [reply]
        assert window._preview_request_id == regular["request_id"]
    finally:
        window.close()
