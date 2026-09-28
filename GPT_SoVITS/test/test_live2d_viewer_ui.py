"""验证编辑器交互与首次提示的行为，不约束介绍文案。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from queue import Queue
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PyQt5.QtCore import QLockFile, Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live2d_support.performance_catalog import read_config, save_config, performance_config_path
from live2d_support.runtime_adapter import Live2DModelAdapter, NullLive2DModel
from live2d_support.viewer_preview import execute_viewer_preview
from ui.components.live2d_performance_editor import Live2DPerformanceEditor, PerformancePresetDialog
from ui.components.live2d_viewer_widgets import show_v3_intro_once
from live2d_viewer import ViewerGUI
from qconfig import DSakikoConfig


@pytest.fixture(scope="module")
def app() -> QApplication:
    """保留单个 Qt 应用直到本模块测试结束。"""
    return QApplication.instance() or QApplication([])


@pytest.fixture
def models(tmp_path: Path) -> tuple[Path, Path]:
    """创建最小的两代模型用于界面和队列验证，不初始化 OpenGL。"""
    v3 = tmp_path / "new/new.model3.json"
    v3.parent.mkdir()
    files = ["mtn_idle_C.motion3.json", "mtn_nod_C.motion3.json"]
    for file in files:
        (v3.parent / file).write_text('{"Meta":{"Loop":false},"Curves":[]}')
    for name in ("exp_idle01", "exp_smile01"):
        (v3.parent / (name + ".exp3.json")).write_text('{"Parameters":[]}')
    save_config(v3, {"Version": 3, "FileReferences": {"Motions": {"idle_motion": [{"File": files[0]}],
                 "happiness": [{"File": files[1]}]}, "Expressions": [
                 {"Name": name, "File": name + ".exp3.json"} for name in ("exp_idle01", "exp_smile01")]}})
    v2 = tmp_path / "old/old.model.json"
    v2.parent.mkdir()
    v2.write_text('{"model":"test.moc","motions":{"happiness":[{"file":"smile.mtn"}]}}')
    (v2.parent / "smile.mtn").write_text('')
    return v2, v3


def test_intro_only_for_first_v3_and_persists(app: QApplication, tmp_path: Path) -> None:
    """只验证出现次数和配置落盘，不判断提示标题或正文内容。"""
    path = tmp_path / "config.json"
    path.write_text('{}')
    config = DSakikoConfig()
    previous = config.live2d_viewer_v3_intro_seen.value
    config.file = path
    config.lock = QLockFile(str(tmp_path / "config.lock"))
    config.live2d_viewer_v3_intro_seen.value = False
    parent = Live2DPerformanceEditor(lambda _command: None)
    try:
        with patch("ui.components.live2d_viewer_widgets.QMessageBox.information", return_value=QMessageBox.Ok) as show:
            assert not show_v3_intro_once(parent, "v2", config)
            assert not show_v3_intro_once(parent, None, config)
            show.assert_not_called()
            assert show_v3_intro_once(parent, "v3", config)
            assert not show_v3_intro_once(parent, "v3", config)
            show.assert_called_once()
            assert json.loads(path.read_text())["ui_state"]["live2d_viewer_v3_intro_seen"] is True
            restored = DSakikoConfig()
            restored.load(path)
            assert not show_v3_intro_once(parent, "v3", restored)
            show.assert_called_once()
    finally:
        config.live2d_viewer_v3_intro_seen.value = previous
        parent.close()


def test_lists_click_preview_and_preserve_all_description_drafts(app: QApplication, models: tuple[Path, Path]) -> None:
    """初始不播放，动作和表情点击只更新对应通道，跨选项草稿一起保存。"""
    sent: list[dict[str, object]] = []
    editor = Live2DPerformanceEditor(sent.append)
    editor.load_model(models[1])
    editor.resize(700, 600)
    editor.show()
    app.processEvents()
    assert not sent
    assert editor.selected_id("motions") == "mtn_idle"
    editor.descriptions["motions"].setPlainText("第一条动作说明")
    editor.select_resource("motions", "mtn_nod")
    editor.descriptions["motions"].setPlainText("第二条动作说明")
    editor.select_resource("motions", "mtn_idle")
    assert editor.descriptions["motions"].toPlainText() == "第一条动作说明"
    for kind in ("motions", "expressions"):
        selector = editor.selectors[kind]
        QTest.mouseClick(selector.viewport(), Qt.LeftButton, pos=selector.visualItemRect(selector.currentItem()).center())
        assert ("motion" in sent[-1]) == (kind == "motions")
    assert editor.save_descriptions()
    assert read_config(performance_config_path(models[1]))["motions"] == {
        "mtn_idle": "第一条动作说明", "mtn_nod": "第二条动作说明"}
    assert not editor.drafts
    editor.set_direction("L")
    editor.load_model(models[1])
    assert editor.direction == "L"
    editor.load_model(models[0])
    assert editor.direction == "C"
    editor.close()


def test_presets_preview_does_not_change_main_selection(app: QApplication, models: tuple[Path, Path]) -> None:
    """试播次级对话框的搭配不移动主列表，也不保存主页面草稿。"""
    sent: list[dict[str, object]] = []
    editor = Live2DPerformanceEditor(sent.append)
    editor.load_model(models[1])
    original = editor.selected_id("motions")
    editor.descriptions["motions"].setPlainText("未保存说明")
    dialog = PerformancePresetDialog(editor, new=True)
    dialog.motion.setCurrentText("mtn_nod")
    dialog.expression.setCurrentText("exp_smile01")
    dialog.name.setText("点头微笑")
    dialog.preview()
    assert sent[-1]["motion"] == "mtn_nod"
    assert editor.selected_id("motions") == original
    assert dialog.save_preset()
    assert editor.drafts
    assert "motions" not in read_config(performance_config_path(models[1]))
    dialog.apply_selection()
    assert dialog.applied_selection == ("mtn_nod", "exp_smile01")
    editor.drafts.clear()
    editor.close()


def test_model_specific_pages_and_cancelled_navigation(app: QApplication, models: tuple[Path, Path]) -> None:
    """按版本切页，取消切换保留草稿，直接选择正确的角色索引。"""
    characters = [SimpleNamespace(character_name=name, character_folder_name=name, live2d_json=str(path), icon_path=None)
                  for name, path in zip(("旧角色", "新角色"), models)]
    motions, changes, results = Queue(), Queue(), Queue()
    with patch("live2d_viewer.show_v3_intro_once") as intro:
        window = ViewerGUI(characters, motions, changes, results)
        window.show()
        app.processEvents()
        assert window.pages.currentWidget() is window.groups_panel
        intro.assert_not_called()
        assert window.select_character(1)
        app.processEvents()
        assert window.pages.currentWidget() is window.performance_editor
        assert window.details_button.isVisible()
        assert window.performance_editor.preview_actions.indexOf(window.details_button) >= 0
        assert window.footer_panel.isHidden()
        assert changes.get_nowait()["index"] == 1
        assert intro.call_count == 1
        window.performance_editor.descriptions["motions"].setPlainText("还未保存")
        with patch.object(window.performance_editor, "save_descriptions", return_value=False), \
                patch("ui.components.live2d_performance_editor.confirm_description_changes", return_value=False):
            assert not window.select_character(0)
            assert window.current_char_index == 1
            assert not window.close()
        assert window.performance_editor.drafts
        assert window.select_character(0)
        assert read_config(performance_config_path(models[1]))["motions"]["mtn_idle"] == "还未保存"
        assert window.pages.currentWidget() is window.groups_panel
        assert window.details_button.isVisible()
        assert window.footer_layout.indexOf(window.details_button) >= 0
        assert window.footer_panel.isVisible()
        window.close()
        app.processEvents()


def test_automatic_settings_visible_on_reopen_and_return_to_v2(app: QApplication, models: tuple[Path, Path]) -> None:
    """实际打开模态窗口时显示动作和分组，重开及返回 V2 后仍可使用。"""
    characters = [SimpleNamespace(character_name=name, character_folder_name=name, live2d_json=str(path), icon_path=None)
                  for name, path in zip(("旧角色", "新角色"), models)]
    with patch("live2d_viewer.show_v3_intro_once"):
        window = ViewerGUI(characters, Queue(), Queue(), Queue())
        window.select_character(1)
        window.show()
        app.processEvents()
        observations: list[tuple[bool, bool, bool, bool]] = []

        def inspect_dialog() -> None:
            """在模态事件循环中记录真实可见性并关闭，断言在循环外执行。"""
            dialog = app.activeModalWidget()
            observations.append((window.all_mnt_display.isVisible(), window.current_mnt_display.isVisible(),
                                 window.btn_add_motion.isVisible(), window.groups_panel.window() is dialog))
            if isinstance(dialog, QDialog):
                dialog.accept()

        try:
            for _ in range(2):
                QTimer.singleShot(0, inspect_dialog)
                window.open_automatic_settings()
                assert window.pages.currentWidget() is window.performance_editor
                assert window.groups_panel.parentWidget() is window.pages
            assert observations == [(True, True, True, True)] * 2
            assert window.all_mnt_display.toPlainText().strip()
            assert window.current_mnt_display.toPlainText().strip()
            assert window.select_character(0)
            app.processEvents()
            assert window.all_mnt_display.isVisible()
            assert window.current_mnt_display.isVisible()
        finally:
            window.close()


def test_preset_switch_preserves_edits_on_cancel_or_save_failure(app: QApplication, models: tuple[Path, Path]) -> None:
    """切换预设遇到取消或写入失败时不丢表单，保存成功后才切换。"""
    editor = Live2DPerformanceEditor(lambda _command: None)
    editor.load_model(models[1])
    dialog = PerformancePresetDialog(editor, new=True)
    dialog.name.setText("第一组")
    assert dialog.save_preset()
    saved_id = dialog._selected_id
    dialog.name.setText("修改中的第一组")
    with patch("ui.components.live2d_performance_editor.confirm_description_changes", return_value=False):
        dialog.items.setCurrentRow(0)
    assert dialog._selected_id == saved_id
    assert dialog.items.currentItem().data(Qt.UserRole) == saved_id
    assert dialog.name.text() == "修改中的第一组"
    with patch("ui.components.live2d_performance_editor.confirm_description_changes", side_effect=lambda _parent, save: save()), \
            patch("ui.components.live2d_performance_editor.save_config", side_effect=OSError("写入失败")):
        dialog.items.setCurrentRow(0)
    assert dialog._dirty
    assert dialog._selected_id == saved_id
    with patch("ui.components.live2d_performance_editor.confirm_description_changes", side_effect=lambda _parent, save: save()):
        dialog.items.setCurrentRow(0)
    assert dialog._selected_id == ""
    assert dialog.items.currentRow() == 0
    assert read_config(performance_config_path(models[1]))["presets"][0]["name"] == "修改中的第一组"
    dialog.close()
    editor.close()


def test_shared_description_preserves_other_drafts(app: QApplication, models: tuple[Path, Path]) -> None:
    """共享一项说明不保存另一项草稿，本地覆盖移除后可恢复共享说明。"""
    editor = Live2DPerformanceEditor(lambda _command: None)
    editor.load_model(models[1])
    editor.descriptions["motions"].setPlainText("共享动作说明")
    editor.descriptions["expressions"].setPlainText("未保存表情说明")
    editor.save_shared_description("motions")
    assert editor.catalog.descriptions["motions"][editor.selected_id("motions")] == "共享动作说明"
    assert ("expressions", editor.selected_id("expressions")) in editor.drafts
    assert "expressions" not in read_config(performance_config_path(models[1]))
    editor.descriptions["motions"].setPlainText("当前模型覆盖")
    assert editor.save_descriptions()
    editor.restore_description("motions")
    assert not editor.drafts
    assert editor.selected_id("motions") not in read_config(performance_config_path(models[1]))["motions"]
    assert editor.descriptions["motions"].toPlainText() == "共享动作说明"
    editor.close()


def test_description_autosave_preserves_cursor_and_flushes_on_leave(app: QApplication, models: tuple[Path, Path]) -> None:
    """延迟自动保存不打断编辑，离开时立即保存最后输入且不串到新模型。"""
    editor = Live2DPerformanceEditor(lambda _command: None)
    editor.load_model(models[1])
    field = editor.descriptions["motions"]
    field.setPlainText("自动保存的动作说明")
    cursor = field.textCursor()
    cursor.setPosition(3)
    field.setTextCursor(cursor)
    QTest.qWait(850)
    assert not editor.drafts
    assert field.textCursor().position() == 3
    assert read_config(performance_config_path(models[1]))["motions"]["mtn_idle"] == "自动保存的动作说明"
    editor.select_resource("motions", "mtn_nod")
    field.setPlainText("离开前的最后输入")
    assert editor.confirm_leave()
    editor.load_model(models[0])
    QTest.qWait(750)
    assert not performance_config_path(models[0]).exists()
    assert read_config(performance_config_path(models[1]))["motions"]["mtn_nod"] == "离开前的最后输入"
    editor.close()


def test_failed_autosave_keeps_draft_and_restore_fills_immediately(app: QApplication, models: tuple[Path, Path]) -> None:
    """写入失败保留草稿，恢复默认即使落盘失败也立即显示继承内容。"""
    editor = Live2DPerformanceEditor(lambda _command: None)
    editor.load_model(models[1])
    field = editor.descriptions["motions"]
    inherited = field.toPlainText()
    field.setPlainText("本地覆盖")
    assert editor.save_descriptions()
    with patch("ui.components.live2d_performance_editor.save_config", side_effect=OSError("写入失败")), \
            patch("ui.components.live2d_performance_editor.QMessageBox.warning") as warning:
        field.setPlainText("待保存草稿")
        QTest.qWait(850)
        assert warning.call_count == 1
        assert editor.drafts[("motions", "mtn_idle")] == "待保存草稿"
        editor.restore_description("motions")
        assert field.toPlainText() == inherited
        assert editor.drafts[("motions", "mtn_idle")] == ""
    assert editor.confirm_leave()
    assert "mtn_idle" not in read_config(performance_config_path(models[1]))["motions"]
    editor.close()


@pytest.mark.parametrize("model_index", [0, 1])
def test_preview_feedback_ignores_old_request(app: QApplication, models: tuple[Path, Path], model_index: int) -> None:
    """先显示等待回执，过期成功不能覆盖最新失败状态。"""
    characters = [SimpleNamespace(character_name="角色", character_folder_name="role", live2d_json=str(models[model_index]), icon_path=None)]
    motions, results = Queue(), Queue()
    window = ViewerGUI(characters, motions, Queue(), results)
    window.send_preview("first.mtn")
    first = motions.get_nowait()
    window.send_preview("second.mtn")
    second = motions.get_nowait()
    results.put({"request_id": first["request_id"], "ok": True, "message": "旧结果"})
    results.put({"request_id": second["request_id"], "ok": False, "message": "资源加载失败"})
    window.poll_preview_results()
    assert window.status_label.text() == "资源加载失败"
    assert window._preview_request_id is None
    if model_index == 1:
        assert window.details_button.isChecked()
        assert "资源加载失败" in window.message_box.toPlainText()
    window.close()


def test_renderer_reports_failure_and_expression_only(models: tuple[Path, Path]) -> None:
    """渲染端不把缺模型和加载失败回报为成功，表情预览不重播动作。"""
    command = {"request_id": "p", "type": "performance", "model_path": str(models[1]), "expression": "exp_smile01"}
    assert not execute_viewer_preview(NullLive2DModel(), command)["ok"]
    native = Mock()
    model = Live2DModelAdapter(str(models[1]), "v3", ModuleType("fake"), native, frozenset(), {},
                              frozenset({"exp_smile01"}), frozenset(), {})
    assert execute_viewer_preview(model, command)["ok"]
    native.StartMotion.assert_not_called()
    assert not execute_viewer_preview(model, dict(command, model_path=str(models[0])))["ok"]
    native.LoadExtraMotion.return_value = -1
    assert not execute_viewer_preview(model, dict(command, motion="mtn_nod"))["ok"]
