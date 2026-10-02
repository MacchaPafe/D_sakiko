"""真实 Qt 编辑窗口对祥子两个形态使用通用编辑流程。"""

from __future__ import annotations

import json
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

import pytest
from PyQt5.QtCore import QPoint, Qt, QUrl
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPushButton

import live2d_viewer
from test.test_sakiko_forms import write_model
from live2d_support.mask_actions import mask_actions
from ui.components.live2d_mask_editor import MaskActionsDialog


@pytest.fixture(scope="session")
def app() -> QApplication:
    """保留 Qt 应用，避免模块切换时销毁共享的配置对象。"""
    return QApplication.instance() or QApplication([])


def test_editor_switches_shapes_and_edits_v2_groups_and_v3_descriptions(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通过下拉菜单切换形态，并检查编辑流程及面具入口的显示。"""
    black = write_model(tmp_path / "live2d_related/sakiko/live2D_model_costume", "v2")
    white = write_model(tmp_path / "live2d_related/sakiko/live2D_model")
    monkeypatch.setattr(live2d_viewer, "project_root", str(tmp_path))
    monkeypatch.setattr(live2d_viewer, "show_v3_intro_once", lambda *_args: False)
    character = SimpleNamespace(character_folder_name="sakiko", character_name="祥子", live2d_json=str(white), icon_path=None)
    changes = Queue()
    viewer = live2d_viewer.ViewerGUI([character], Queue(), changes)
    try:
        assert viewer.current_model_json_path == black
        assert not viewer.form_selector.isHidden()
        assert not viewer.mask_button.isHidden()
        assert viewer.pages.currentWidget() is viewer.groups_panel
        viewer.play_motion_cur_mtn_ver(QUrl("group:IDLE"))
        viewer.left_selected_motion_path = str(black.parent / "put_on.mtn")
        viewer.on_add_motion()
        assert json.loads(black.read_text())["motions"]["IDLE"][-1]["file"] == "put_on.mtn"
        assert viewer.right_selected_group == "IDLE"
        viewer.show()
        app.processEvents()
        selector = viewer.form_selector
        selector.showPopup()
        app.processEvents()
        popup = selector.view().window()
        assert popup.x() == selector.mapToGlobal(QPoint(0, 0)).x()
        assert popup.y() >= selector.mapToGlobal(QPoint(0, selector.height() - 1)).y()
        assert popup.width() >= selector.width()
        QTest.keyClick(selector.view(), Qt.Key_Down)
        QTest.keyClick(selector.view(), Qt.Key_Return)
        assert viewer.current_model_json_path == white
        assert viewer.mask_button.isHidden()
        assert not viewer.form_selector.isHidden()
        assert viewer.pages.currentWidget() is viewer.performance_editor
        viewer.performance_editor.descriptions["motions"].setPlainText("祥子的动作说明")
        assert viewer.performance_editor.save_descriptions()
        assert viewer.performance_editor.catalog.descriptions["motions"]
        assert changes.get_nowait()["model_path"] == str(white)
        viewer.form_selector.setCurrentIndex(0)
        assert viewer.current_model_json_path == black
        assert not viewer.mask_button.isHidden()
    finally:
        viewer.form_selector.hidePopup()
        viewer.close()


@pytest.mark.parametrize("version,suffix", [("v2", ".mtn"), ("v3", ".motion3.json")])
def test_mask_editor_previews_and_saves_two_explicit_actions(app, tmp_path, version, suffix):
    path = write_model(tmp_path, version)
    previews = []
    dialog = MaskActionsDialog(path, previews.append)
    try:
        for key, name in (("on", "put_on"), ("off", "take_off")):
            dialog.selectors[key].setCurrentIndex(dialog.selectors[key].findData(name + suffix))
        for button in dialog.findChildren(QPushButton):
            if button.text() == "预览":
                button.click()
        assert previews == [{"file": str(tmp_path / (name + suffix)), "auto_expression": False}
                            for name in ("put_on", "take_off")]
        dialog.save()
        assert mask_actions(str(path)) == {"on": "put_on" + suffix, "off": "take_off" + suffix}
    finally:
        dialog.close()
