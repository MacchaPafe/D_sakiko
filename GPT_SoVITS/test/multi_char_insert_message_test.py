"""主控与编排共用插句事务、初始编排和表单主题的回归。"""
from copy import deepcopy
from unittest.mock import Mock

import pytest
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

from GPT_SoVITS.test.multi_char_editor_test import app, editor
from chat.chat import Chat, Message
from chat.chat_meta import ToolCallRecordMeta
from chat.rolling_summary import get_rolling_summary, set_rolling_summary
from live2d_support.multi_char_resources import initial_message_timeline
from ui_small_theater.multi_char_insert_dialog import MultiCharInsertDialog
from ui_small_theater.multi_char_message_edit import insert_saved_message


def insert_chat():
    """同文消息仍具有不同对象身份，摘要扩大到完整轮次。"""
    chat = Chat(message_list=[Message.from_dict({"character_name": "A", "text": "同文"}) for _ in range(3)])
    chat.meta.tool_call_records = [
        ToolCallRecordMeta(tool_call_id=str(index), message_index=index) for index in range(3)
    ]
    set_rolling_summary(chat, text="原摘要", summary_until_message_index=1, model="test")
    return chat


@pytest.mark.parametrize("anchor_index,after", [(0, False), (0, True), (1, False), (1, True), (2, True)])
def test_insert_uses_identity_and_maintains_indices_and_summary(anchor_index, after):
    """各插入点正确顺移工具索引，只有插入摘要覆盖区才使摘要失效。"""
    chat = insert_chat()
    boundary = get_rolling_summary(chat).summary_until_message_index
    original = list(chat.message_list)
    original[1].scene_before = {"visible_characters": [], "background": "keep.png"}
    original_data = [deepcopy(message.as_dict()) for message in original]
    target = Message.from_dict({"character_name": "", "text": "新增", "muted": True})
    index = anchor_index + int(after)
    saves = []

    def save():
        saves.append(([id(message) for message in chat.message_list], deepcopy(chat.meta.to_dict())))
        return True

    assert insert_saved_message(chat, original[anchor_index], target, after, save)
    assert len(saves) == 1 and chat.message_list[index] is target
    assert [message for message in chat.message_list if message is not target] == original
    assert [message.as_dict() for message in original] == original_data
    assert [record.message_index for record in chat.meta.tool_call_records] == [
        position + int(position >= index) for position in range(3)
    ]
    assert (get_rolling_summary(chat) is None) == (index <= boundary)
    assert target.scene_before is None


@pytest.mark.parametrize("raises", [False, True])
def test_failed_insert_restores_original_objects_and_metadata(raises):
    """保存返回失败或抛异常，原列表、工具记录与摘要对象均未被污染。"""
    chat = insert_chat()
    messages = list(chat.message_list)
    original_list = chat.message_list
    original_meta = chat.meta
    original_records = list(chat.meta.tool_call_records)
    meta_data = deepcopy(chat.meta.to_dict())
    target = Message.from_dict({"character_name": "A", "text": "新增"})
    save = Mock(return_value=False, side_effect=OSError("写入失败") if raises else None)
    if raises:
        with pytest.raises(OSError):
            insert_saved_message(chat, messages[1], target, False, save)
    else:
        assert not insert_saved_message(chat, messages[1], target, False, save)
    assert chat.message_list is original_list and len(chat.message_list) == len(messages)
    assert all(actual is expected for actual, expected in zip(chat.message_list, messages))
    assert chat.meta is original_meta and chat.meta.to_dict() == meta_data
    assert all(actual is expected for actual, expected in zip(chat.meta.tool_call_records, original_records))
    save.assert_called_once()


def test_insert_rejects_missing_identity_and_reused_target():
    """同文副本不能充当锚点，已有消息对象不能再插入一次。"""
    chat = insert_chat()
    anchor = chat.message_list[1]
    save = Mock()
    target = Message.from_dict({"text": "新增"})
    assert not insert_saved_message(chat, deepcopy(anchor), target, True, save)
    assert not insert_saved_message(chat, anchor, chat.message_list[0], True, save)
    assert len(chat.message_list) == 3
    save.assert_not_called()


@pytest.mark.parametrize("speaker", ["A", ""])
def test_shared_initial_timeline_matches_editor_without_mutating_message(editor, speaker):
    """两个入口使用同一首句事件与持久颜色规则，无角色消息保留空四轨。"""
    motion = {"at_ms": 0, "resource": {"logical_id": "nod", "file_name": "nod.motion3.json"}}
    resources = {
        name: Mock(defaults=Mock(side_effect=lambda *args: {"motions": [deepcopy(motion)], "expressions": []}))
        for name in editor.names
    }
    palette = ["#123456"]
    editor.resources, editor.palette = resources, palette
    message = Message.from_dict({"character_name": speaker, "text": "新句", "emotion": "normal"})
    before = deepcopy(message.as_dict())
    expected = initial_message_timeline(message, editor.names, resources, palette)
    assert editor.initial_timeline(message) == expected
    assert message.as_dict() == before
    assert set(expected["characters"]) == set(editor.names)
    assert expected["characters"]["B"] == {"motions": [], "expressions": []}
    if speaker:
        assert expected["characters"][speaker]["motions"][0]["color"] == "#123456"
        resources[speaker].defaults.assert_called_with(None, message.emotion.as_string())
    else:
        assert expected["characters"]["A"] == {"motions": [], "expressions": []}
        resources["A"].defaults.assert_not_called()


def accepted_dialog():
    """避免真实模态交互，仅替代表单输入。"""
    dialog = Mock()
    dialog.exec_.return_value = QDialog.Accepted
    dialog.message_data.return_value = {"character_name": "", "text": "新增台词", "muted": True}
    return dialog


@pytest.mark.parametrize("phase", ["draft", "form"])
@pytest.mark.parametrize("change", ["busy", "close", "switch", "remove"])
def test_editor_insert_rechecks_anchor_after_nested_dialogs(editor, monkeypatch, phase, change):
    """草稿或输入弹窗处理事件后若原句已变，不向过期锚点插入。"""
    old = editor.message
    next_message = Message.from_dict({"character_name": "B", "text": "下一句"})
    next_message.performance_timeline = deepcopy(editor.baseline)
    editor.chat.message_list.append(next_message)
    dialog = accepted_dialog()
    factory = Mock(return_value=dialog)
    monkeypatch.setattr("ui_small_theater.multi_char_editor.MultiCharInsertDialog", factory)
    saved = editor.owner.saved

    def change_state():
        if change == "busy":
            editor.set_audio_busy(True)
        elif change == "close":
            editor.finish()
        elif change == "switch":
            editor.show_message(next_message)
        else:
            editor.chat.delete_message_at(0)
        return True if phase == "draft" else QDialog.Accepted

    if phase == "draft":
        monkeypatch.setattr(editor, "resolve_message_drafts", change_state)
    else:
        dialog.exec_.side_effect = change_state
    editor.insert_message(after=True)
    assert editor.owner.saved == saved
    assert all(message is old or message is next_message for message in editor.chat.message_list)
    if phase == "draft":
        factory.assert_not_called()
    else:
        dialog.deleteLater.assert_called_once()
    if change == "busy":
        editor.set_audio_busy(False)


def test_editor_insert_resolves_anchor_identity_then_selects_before_refresh(editor, monkeypatch):
    """表单期间原句下标移动仍定位原对象，刷新主控时已经选中保存后的新句。"""
    anchor = editor.message
    earlier = Message.from_dict({"character_name": "B", "text": "原锚点前的新句"})
    dialog = accepted_dialog()

    def confirm():
        editor.chat.message_list.insert(0, earlier)
        return QDialog.Accepted

    dialog.exec_.side_effect = confirm
    monkeypatch.setattr("ui_small_theater.multi_char_editor.MultiCharInsertDialog", Mock(return_value=dialog))

    def refresh():
        assert editor.message.text == "新增台词"
        assert editor.message is editor.chat.message_list[2]

    editor.owner.refresh_chat_display.side_effect = refresh
    saved = editor.owner.saved
    editor.insert_message(after=True)
    assert editor.chat.message_list[0] is earlier and editor.chat.message_list[1] is anchor
    assert editor.owner.saved == saved + 1
    editor.owner.refresh_chat_display.assert_called_once()


def test_editor_insert_save_exception_preserves_anchor_and_metadata(editor, monkeypatch):
    """保存异常只显示一次错误，回到原句，摘要和工具索引一起恢复。"""
    original = editor.message
    editor.chat.meta.tool_call_records = [ToolCallRecordMeta(message_index=0)]
    original_meta = editor.chat.meta
    monkeypatch.setattr("ui_small_theater.multi_char_editor.MultiCharInsertDialog", Mock(return_value=accepted_dialog()))
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    editor.owner._save_chat = Mock(side_effect=OSError("写入失败"))
    editor.insert_message(after=False)
    assert editor.message is original and editor.chat.message_list == [original]
    assert editor.chat.meta is original_meta and editor.chat.meta.tool_call_records[0].message_index == 0
    editor.owner.refresh_chat_display.assert_not_called()
    warning.assert_called_once()


def test_insert_dialog_uses_same_theme_with_different_parent_fonts_and_styles(app, editor):
    """主控的大号字体或深色样式不改变同一个插句表单的字号、间距与配色。"""
    parent = QWidget()
    font = QFont(parent.font())
    font.setPixelSize(48)
    parent.setFont(font)
    parent.setStyleSheet("QWidget { font-size: 48px; color: red; background: black; }")
    dialogs = [MultiCharInsertDialog(["A", "B"], "A", source) for source in (parent, editor.resource_window)]
    try:
        for dialog in dialogs:
            dialog.show()
        app.processEvents()
        first, second = dialogs
        assert first.styleSheet() == second.styleSheet()
        assert first.font().pixelSize() == second.font().pixelSize() < 48
        assert first.original.font().pixelSize() == second.original.font().pixelSize()
        assert first.original.height() == second.original.height()
        assert first.confirm.sizeHint() == second.confirm.sizeHint()
        assert first.original.palette().base().color() == second.original.palette().base().color()
        assert "@FONT_PX@" not in first.styleSheet() and "@ROLE_0_COLOR@" not in first.styleSheet()
    finally:
        for dialog in dialogs:
            dialog.close()
            dialog.deleteLater()
        parent.close()
        parent.deleteLater()
        app.processEvents()
