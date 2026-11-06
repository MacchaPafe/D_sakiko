"""小剧场单句删除的持久保存、失败恢复和确认边界。"""
import os
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt5.QtWidgets import QApplication, QMessageBox
from chat.chat import Chat, Message
from chat.chat_meta import ToolCallRecordMeta
from chat.rolling_summary import get_rolling_summary, set_rolling_summary
from ui_small_theater.multi_char_message_edit import confirm_message_deletion, delete_saved_message


def deletion_chat():
    """使用同文消息和带索引的元数据，覆盖身份定位及摘要失效。"""
    messages = [Message.from_dict({"character_name": "A", "text": "相同台词"}) for _ in range(3)]
    chat = Chat(message_list=messages)
    chat.meta.tool_call_records = [
        ToolCallRecordMeta(tool_call_id=str(index), message_index=index) for index in range(3)
    ]
    set_rolling_summary(chat, text="已有摘要", summary_until_message_index=2, model="test")
    return chat


def test_delete_saves_exact_message_and_updates_metadata_without_removing_audio(tmp_path):
    """只删指定对象，顺移工具记录并失效摘要；原音频文件及剩余引用保留。"""
    chat = deletion_chat()
    first, deleted, last = chat.message_list
    audio = tmp_path / "shared.wav"
    audio.write_bytes(b"audio")
    first.audio_path = deleted.audio_path = str(audio)
    deleted.scene_before = {"visible_characters": []}
    before_last = deepcopy(last.as_dict())
    snapshot = []

    def save():
        snapshot.append(([item is deleted for item in chat.message_list], chat.meta.to_dict()))
        return True

    assert delete_saved_message(chat, deleted, save)
    assert len(chat.message_list) == 2
    assert chat.message_list[0] is first and chat.message_list[1] is last
    assert snapshot[0][0] == [False, False]
    assert get_rolling_summary(chat) is None
    assert [(record.tool_call_id, record.message_index) for record in chat.meta.tool_call_records] == [
        ("0", 0), ("2", 1),
    ]
    assert last.as_dict() == before_last
    assert audio.read_bytes() == b"audio" and first.audio_path == str(audio)


@pytest.mark.parametrize("failure", ["save_false", "save_exception", "delete_exception"])
def test_delete_failure_restores_list_and_original_metadata_objects(monkeypatch, failure):
    """删除或保存失败恢复原消息身份、列表、摘要及工具记录，不留下半次删除。"""
    chat = deletion_chat()
    messages = list(chat.message_list)
    original_list = chat.message_list
    original_meta = chat.meta
    original_records = list(chat.meta.tool_call_records)
    original_data = deepcopy(chat.meta.to_dict())
    save = Mock(return_value=False)
    if failure == "save_exception":
        save.side_effect = OSError("存档写入失败")
    if failure == "delete_exception":
        delete = chat.delete_message_at

        def broken_delete(index):
            delete(index)
            raise RuntimeError("删除失败")

        monkeypatch.setattr(chat, "delete_message_at", broken_delete)
    if failure == "save_false":
        assert not delete_saved_message(chat, messages[1], save)
    else:
        with pytest.raises((OSError, RuntimeError)):
            delete_saved_message(chat, messages[1], save)
    assert chat.message_list is original_list
    assert all(actual is expected for actual, expected in zip(chat.message_list, messages))
    assert len(chat.message_list) == len(messages)
    assert chat.meta is original_meta
    assert all(actual is expected for actual, expected in zip(chat.meta.tool_call_records, original_records))
    assert chat.meta.to_dict() == original_data
    assert save.call_count == (0 if failure == "delete_exception" else 1)


def test_delete_ignores_equal_message_outside_chat():
    """同文但不在剧本中的对象不可误删，也不发起存档。"""
    chat = deletion_chat()
    messages = list(chat.message_list)
    save = Mock()
    assert not delete_saved_message(chat, deepcopy(messages[1]), save)
    assert all(actual is expected for actual, expected in zip(chat.message_list, messages))
    save.assert_not_called()


@pytest.mark.parametrize("discard_drafts", [False, True])
@pytest.mark.parametrize("choice", ["取消", "删除本句", None])
def test_confirmation_defaults_to_cancel_and_explains_scene_and_drafts(monkeypatch, discard_drafts, choice):
    """确认与关闭均无隐式删除，明确删除范围且编排入口仅多一段草稿说明。"""
    app = QApplication.instance() or QApplication([])
    dialogs = []

    def execute(dialog):
        assert dialog.defaultButton().text() == "取消"
        assert dialog.escapeButton().text() == "取消"
        assert "句前场景变化" in dialog.informativeText()
        assert ("草稿也会丢弃" in dialog.informativeText()) == discard_drafts
        dialogs.append(dialog)
        return 0

    monkeypatch.setattr(QMessageBox, "exec_", execute)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda dialog: next(
        (button for button in dialog.buttons() if button.text() == choice), None))
    message = Message.from_dict({"character_name": "A", "text": "待删台词"})
    assert confirm_message_deletion(None, message, discard_drafts) == (choice == "删除本句")
    assert len(dialogs) == 1
    app.processEvents()
