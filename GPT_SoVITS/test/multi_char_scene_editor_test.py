"""场景编排界面的保存边界、插句、静音及资源刷新回归测试。"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPalette
from PyQt5.QtWidgets import QDialog, QFileDialog, QGraphicsPathItem, QMessageBox

from GPT_SoVITS.test.multi_char_editor_test import Owner, app, editor
from chat.chat import Message
from live2d_support.multi_char_resources import TheaterResources
from ui.components.multi_char_editor import MultiCharEditor
from ui.components.multi_char_insert_dialog import MultiCharInsertDialog


def last_command(editor):
    """清空测试队列并返回最后一个预览命令。"""
    result = None
    queue = editor.owner.to_live2d_change_character_queue
    while not queue.empty():
        result = queue.get_nowait()
    return result


@pytest.mark.parametrize("names", [("野乃花", "由乃"), ("由乃", "野乃花")])
def test_character_theme_colors_survive_role_order_and_navigation(app, names):
    """相近色相角色仍保留原色，交换站位和切句不会混淆胶囊、轨道头及姓名牌。"""
    expected_colors = {"野乃花": "#ffbbcc", "由乃": "#ee5577"}
    owner = Owner()
    owner.setGeometry(350, 100, 400, 500)
    messages = [Message.from_dict({"character_name": name, "text": "测试台词"}) for name in names]
    chat = SimpleNamespace(message_list=messages)
    slots = [{"character_name": name, "model_json_path": None} for name in names]
    session = MultiCharEditor(owner, chat, messages[0], slots)
    owner.performance_editor = session
    try:
        session.scene_toggle.setChecked(True)
        app.processEvents()
        for index, name in enumerate(names):
            if index:
                session.switch_message(1)
                app.processEvents()
            for role, button in session.visible_checks.items():
                assert button.isChecked()
                # QSS 的 :checked 底色不进入基础 palette，采样左侧内边距中的实际像素。
                image = button.grab().toImage()
                assert image.pixelColor(5, image.height() // 2).name() == expected_colors[role]
            assert session.speaker_label.text() == name
            assert session.speaker_label.palette().color(QPalette.Window).name() == expected_colors[name]

            # 读取实际场景中位于轨道标题区域、纵贯双轨的无边框窄色条。
            strip_colors = {}
            for item in session.view.scene().items():
                if not isinstance(item, QGraphicsPathItem):
                    continue
                rect = item.path().boundingRect()
                if (item.pen().style() == Qt.NoPen
                        and rect.right() < session.view.margin
                        and rect.top() >= session.view.top
                        and rect.height() > session.view.row_height
                        and rect.width() < rect.height() / 4):
                    strip_colors.setdefault(rect.top(), set()).add(item.brush().color().name())
            assert len(strip_colors) == len(names)
            assert [strip_colors[top] for top in sorted(strip_colors)] == [
                {expected_colors[role]} for role in names
            ]
    finally:
        session.finish()
        owner.close()
        app.processEvents()


def test_old_message_scene_remains_absent(editor):
    """打开已有台词只初始化时间轴，不凭空写入场景配置。"""
    assert editor.message.scene_before is None
    assert "scene_before" not in editor.message.as_dict()
    assert all(check.isChecked() for check in editor.visible_checks.values())
    assert not editor.black_check.isEnabled()
    assert not editor.transition_preview.isEnabled()
    command = last_command(editor)
    assert command["action"] == "enter"
    assert command["scene_context"]["index"] == 0
    assert command["scene_context"]["messages"][0]["text"] == editor.message.text


def test_scene_mute_save_survives_timeline_discard(editor):
    """场景和静音即时保存，放弃时间轴不回滚它们或删除原音频引用。"""
    editor.message.audio_path = "existing.wav"
    editor.position = 200
    editor.playing = True
    original_draft = deepcopy(editor.draft)
    editor.visible_checks["A"].setChecked(False)
    editor.visible_checks["B"].setChecked(False)
    editor.muted_check.setChecked(True)
    assert editor.message.scene_before == {"visible_characters": []}
    assert "出场：无" in editor.scene_summary.toolTip()
    assert editor.message.muted
    assert editor.message.audio_path == "existing.wav"
    assert editor.draft == original_draft
    assert editor.position == 200
    assert not editor.playing and not editor.can_resume
    editor.discard()
    assert editor.message.scene_before == {"visible_characters": []}
    assert editor.message.muted


def test_navigation_resolves_inherited_visibility_and_own_black(editor):
    """切句反映继承上场状态，黑场仅属于设置它的那一句。"""
    editor.visible_checks["B"].setChecked(False)
    second = Message.from_dict({"character_name": "B", "text": "第二句"})
    third = Message.from_dict({"character_name": "A", "text": "第三句"})
    editor.chat.message_list.extend([second, third])
    editor.switch_message(1)
    assert editor.visible_checks["A"].isChecked()
    assert not editor.visible_checks["B"].isChecked()
    assert second.scene_before is None
    assert "出场：A ｜" in editor.scene_summary.toolTip()
    assert "背景图片：沿用" in editor.scene_summary.toolTip()
    assert "转场：无" in editor.scene_summary.toolTip()
    editor.black_check.setChecked(True)
    assert second.scene_before["transition"] == {
        "type": "black", "fade_out_ms": 600, "hold_ms": 800, "fade_in_ms": 600,
    }
    assert last_command(editor)["action"] == "scene"
    assert "转场：黑场 2.0s" in editor.scene_summary.toolTip()
    editor.black_duration.setValue(500)
    assert second.scene_before["transition"]["hold_ms"] == 200
    assert "转场：黑场 0.5s" in editor.scene_summary.toolTip()
    editor.preview_transition()
    command = last_command(editor)
    assert command["action"] == "transition"
    assert command["scene_context"]["index"] == 1
    editor.switch_message(1)
    assert not editor.black_check.isChecked()
    assert third.scene_before is None
    assert "出场：A ｜" in editor.scene_summary.toolTip()
    assert "转场：无" in editor.scene_summary.toolTip()


def test_scene_fold_only_changes_layout_and_explains_first_transition(editor, app):
    """展开折叠不写盘或打断预览，首句即便带黑场配置也明确提示不执行。"""
    from PyQt5.QtTest import QTest
    editor.message.scene_before = {
        "background": "stage.png",
        "transition": {"type": "black", "fade_out_ms": 450, "hold_ms": 600, "fade_in_ms": 450},
    }
    editor.sync_scene_controls()
    assert editor.scene_body.isHidden()
    assert editor.scene_summary.isVisible()
    assert "背景图片：stage.png" in editor.scene_summary.toolTip()
    assert "转场：首句不可用" in editor.scene_summary.toolTip()
    before = deepcopy(editor.message.as_dict())
    baseline_saves = editor.owner.saved
    editor.playing = True
    last_command(editor)
    QTest.mouseClick(editor.scene_toggle, Qt.LeftButton)
    app.processEvents()
    assert editor.scene_body.isVisible()
    assert editor.scene_summary.isHidden()
    QTest.mouseClick(editor.scene_toggle, Qt.LeftButton)
    app.processEvents()
    assert editor.scene_body.isHidden()
    assert editor.scene_summary.isVisible()
    assert editor.owner.saved == baseline_saves
    assert editor.message.as_dict() == before
    assert editor.playing
    assert last_command(editor) is None


def test_failed_scene_or_mute_save_restores_only_failed_value(editor):
    """保存失败恢复该次配置和控件，既有时间轴草稿不受影响。"""
    editor.owner.fail = True
    editor.visible_checks["A"].setChecked(False)
    editor.muted_check.setChecked(True)
    assert editor.message.scene_before is None
    assert editor.visible_checks["A"].isChecked()
    assert not editor.message.muted and not editor.muted_check.isChecked()


def test_black_slider_drag_saves_once_after_release(editor):
    """滑块拖动只更新秒数，释放后一次保存并取消预览，步长为 100ms。"""
    from PyQt5.QtWidgets import QSlider
    second = Message.from_dict({"character_name": "B", "text": "第二句"})
    editor.chat.message_list.append(second)
    editor.switch_message(1)
    assert isinstance(editor.black_duration, QSlider)
    assert editor.black_duration.orientation() == Qt.Horizontal
    assert editor.black_duration.minimum() == 500
    assert editor.black_duration.maximum() == 4000
    assert editor.black_duration.singleStep() == 100
    assert editor.black_duration.isHidden()
    assert editor.black_duration_label.isHidden()
    editor.black_check.setChecked(True)
    assert editor.black_duration.value() == 2000
    assert editor.black_duration_label.text() == "2.0 秒"
    baseline_saves = editor.owner.saved
    last_command(editor)
    editor.playing = True
    editor.black_duration.setSliderDown(True)
    editor.black_duration.setValue(2700)
    editor.black_duration.setValue(2756)
    assert editor.black_duration_label.text() == "2.8 秒"
    assert editor.owner.saved == baseline_saves
    assert editor.playing
    assert last_command(editor) is None
    editor.black_duration.setSliderDown(False)
    assert editor.owner.saved == baseline_saves + 1
    assert editor.black_duration.value() == 2800
    assert not editor.playing
    transition = second.scene_before["transition"]
    assert transition["fade_out_ms"] + transition["hold_ms"] + transition["fade_in_ms"] == 2800
    assert last_command(editor)["action"] == "scene"
    editor.black_duration.sliderReleased.emit()
    assert editor.owner.saved == baseline_saves + 1
    editor.black_check.setChecked(False)
    assert editor.black_duration.isHidden()
    assert editor.black_duration_label.isHidden()


def test_black_slider_keyboard_change_saves_one_step(editor):
    """键盘单次方向键调整 100ms，首句保持黑场入口禁用。"""
    from PyQt5.QtTest import QTest
    assert not editor.black_check.isEnabled()
    second = Message.from_dict({"character_name": "B", "text": "第二句"})
    editor.chat.message_list.append(second)
    editor.switch_message(1)
    editor.black_check.setChecked(True)
    baseline_saves = editor.owner.saved
    QTest.keyClick(editor.black_duration, Qt.Key_Right)
    assert editor.black_duration.value() == 2100
    assert editor.black_duration_label.text() == "2.1 秒"
    assert editor.owner.saved == baseline_saves + 1


def test_background_picker_only_accepts_decodable_direct_child(editor, monkeypatch, tmp_path):
    """系统选择器不能通过扩展名或初始目录绕过根目录及图片解码校验。"""
    import ui.components.multi_char_editor as module
    root = tmp_path / "live2d_related"
    root.mkdir()
    valid = root / "school.png"
    image = QImage(4, 4, QImage.Format_ARGB32)
    image.fill(Qt.blue)
    assert image.save(str(valid))
    invalid = root / "broken.jpg"
    invalid.write_text("not an image", encoding="utf-8")
    outside = tmp_path / "outside.png"
    assert image.save(str(outside))
    monkeypatch.setattr(module, "background_root", lambda: root)
    original_path = module.background_path
    monkeypatch.setattr(module, "background_path", lambda filename: original_path(filename, root=root))
    picker = Mock(side_effect=[("", ""), (str(outside), ""), (str(invalid), ""), (str(valid), "")])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", picker)
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    for _ in range(3):
        editor.background_check.setChecked(True)
        assert editor.message.scene_before is None
        assert not editor.background_check.isChecked()
    assert warning.call_count == 2
    editor.background_check.setChecked(True)
    assert editor.message.scene_before == {"background": "school.png"}
    assert editor.background_file.text() == "school.png"
    editor.background_check.setChecked(False)
    assert editor.message.scene_before is None


def test_insert_message_keeps_next_scene_and_existing_audio(editor, monkeypatch):
    """在黑场目标句之前插入台词，黑场仍附在原句，旧音频和事件不串句。"""
    import ui.components.multi_char_editor as module
    first = editor.message
    first.audio_path = "first.wav"
    second = Message.from_dict({"character_name": "B", "text": "后句", "audio_path": "second.wav",
                                "scene_before": {"visible_characters": []}})
    editor.chat.message_list.append(second)
    before_second = second.as_dict()
    dialog = Mock()
    dialog.exec_.return_value = QDialog.Accepted
    dialog.message_data.return_value = {
        "character_name": "", "text": "夜色渐深", "translation": "", "muted": True,
    }
    monkeypatch.setattr(module, "MultiCharInsertDialog", Mock(return_value=dialog))
    editor.insert_message(after=True)
    assert editor.chat.message_list == [first, editor.message, second]
    assert second.as_dict() == before_second
    assert first.audio_path == "first.wav"
    assert editor.message.scene_before is None
    assert editor.message.character_name == "" and editor.message.muted
    assert editor.speaker_label.isHidden()
    assert not editor.regenerate_button.isEnabled()
    assert editor.previous.isEnabled() and editor.next.isEnabled()
    assert editor.message_count.text() == "2/3"
    editor.owner.refresh_chat_display.assert_called_once()


def test_insert_cancel_busy_and_save_failure_do_not_change_script(editor, monkeypatch):
    """取消表单、合成锁定、落盘失败均不得遗留消息或改变当前句。"""
    import ui.components.multi_char_editor as module
    original = editor.message
    dialog = Mock()
    factory = Mock(return_value=dialog)
    monkeypatch.setattr(module, "MultiCharInsertDialog", factory)
    editor.set_audio_busy(True)
    assert not editor.add_message_button.isEnabled()
    editor.insert_message(after=False)
    factory.assert_not_called()
    editor.set_audio_busy(False)
    dialog.exec_.return_value = QDialog.Rejected
    editor.insert_message(after=False)
    assert editor.chat.message_list == [original]
    dialog.exec_.return_value = QDialog.Accepted
    dialog.message_data.return_value = {"character_name": "A", "text": "新句", "muted": False}
    editor.owner.fail = True
    editor.insert_message(after=False)
    assert editor.chat.message_list == [original]
    assert editor.message is original


def test_insert_form_defaults_and_required_text(editor):
    """无角色默认静音，角色默认有声，空白原文不能提交。"""
    dialog = MultiCharInsertDialog(["A", "B"], "A", editor.resource_window)
    try:
        assert not dialog.muted.isChecked()
        assert not dialog.confirm.isEnabled()
        dialog.speaker.setCurrentIndex(dialog.speaker.findData(""))
        assert dialog.muted.isChecked()
        dialog.original.setPlainText("  \n ")
        assert not dialog.confirm.isEnabled()
        dialog.original.setPlainText("场景描述")
        assert dialog.confirm.isEnabled()
        assert dialog.message_data()["character_name"] == ""
    finally:
        dialog.close()
        dialog.deleteLater()


def test_refresh_current_character_does_not_change_preview_or_other_resources(editor, monkeypatch):
    """刷新只替换当前资源目录，保留播放时钟、角色选择、筛选与所有编排。"""
    import ui.components.multi_char_editor as module
    old_b = editor.resources["B"]
    old_a = editor.resources["A"]
    old_draft = deepcopy(editor.draft)
    replacement = TheaterResources("", old_a.direction)
    monkeypatch.setattr(module, "TheaterResources", Mock(return_value=replacement))
    editor.position = 345
    editor.playing = True
    editor.search.setText("smile")
    editor.reload_resources()
    assert editor.resources["B"] is old_b
    assert editor.resources["A"] is replacement
    assert editor.position == 345 and editor.playing
    assert editor.draft == old_draft
    assert editor.search.text() == "smile"
    assert editor.refresh_button.text() == ""
    assert not editor.refresh_button.icon().isNull()
    command = last_command(editor)
    assert command["action"] == "refresh"
    assert command["character"] == "A"


def test_custom_expression_entry_follows_resource_kind_and_model(editor):
    """自定义表情只在 V3 表情页出现，切回动作或 V2 角色后不留下入口。"""
    editor.resources["A"].version = "v3"
    editor.resources["B"].version = "v2"
    editor.refresh_resources()
    assert editor.custom.isHidden()
    editor.types.setCurrentIndex(1)
    assert editor.custom.isVisible() and editor.custom.isEnabled()
    assert editor.custom.toolTip() == "自定义表情"
    editor.types.setCurrentIndex(0)
    assert editor.custom.isHidden()
    editor.types.setCurrentIndex(1)
    editor.characters.setCurrentIndex(1)
    assert editor.custom.isHidden()
    assert not editor.types.isTabEnabled(1)


def test_unowned_text_edit_does_not_offer_borrowed_voice(editor, monkeypatch):
    """无角色消息没有姓名占位，也不在保存原文时提示借用角色合成。"""
    unowned = Message.from_dict({"character_name": "", "text": "旧文本", "muted": True})
    editor.chat.message_list.append(unowned)
    editor.switch_message(1)
    question = Mock()
    monkeypatch.setattr(QMessageBox, "question", question)
    editor.toggle_text_edit()
    editor.original_input.setPlainText("新文本")
    assert editor.save_text()
    question.assert_not_called()
    editor.regenerate_audio()
    editor.owner.regenerate_audio.assert_not_called()
    assert editor.speaker_label.isHidden()
