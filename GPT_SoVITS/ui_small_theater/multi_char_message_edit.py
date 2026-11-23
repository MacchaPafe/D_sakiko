"""小剧场主控与编排器共用的台词增删及保存事务。"""
from __future__ import annotations

from copy import deepcopy
from typing import Callable

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox

from chat.chat import Chat, Message
from chat.chat_meta import ChatMeta
from live2d_support.multi_char_scene import cast_run_end, scene_with_cast


def _restore_history(chat: Chat, data: dict[str, object], messages: list[Message],
                     meta: ChatMeta, active: str | None, summary_invalidated: bool) -> None:
    """恢复完整节点及既有台词对象，失败回滚不经过可见列表投影。"""
    timeline = chat._timeline
    timeline.data.clear()
    timeline.data.update(data)
    timeline.invalidate_entries()
    timeline.views = {message.id: message for message in messages}
    timeline.detached.clear()
    timeline.active_assistant_id = active
    timeline.summary_invalidated = summary_invalidated
    chat.meta = meta


def insert_saved_message(chat: Chat, anchor: Message | None, target: Message, after: bool,
                         save: Callable[[], bool], *, roster=None, cast=None) -> bool:
    """按锚点身份插句并同步索引、摘要；失败恢复原列表和元数据对象。"""
    index = (0 if anchor is None and not chat.message_list else
             next((index for index, item in enumerate(chat.message_list) if item is anchor), -1))
    if index < 0 or any(item is target for item in chat.message_list):
        return False
    index += 1 if after and anchor is not None else 0
    original_messages = list(chat.message_list)
    previous_scenes = [message.scene_before for message in original_messages] if roster else []
    original_meta = chat.meta
    original_data = deepcopy(chat._timeline.data)
    active = chat._timeline.active_assistant_id
    summary_invalidated = chat._timeline.summary_invalidated
    staged_meta = deepcopy(original_meta)
    saved = False
    try:
        from chat.rolling_summary import invalidate_rolling_summary_from_message_index

        chat.meta = staged_meta
        invalidate_rolling_summary_from_message_index(chat, index)
        if roster is not None:
            roster.record_opening()
            casts = roster.casts()
            previous = casts[index - 1] if index else roster.opening
            chosen = cast if cast is not None else previous
            if chosen != previous:
                end = cast_run_end(casts, index, previous)
                casts[index:end] = [chosen] * (end - index)
            _write_casts(original_messages, casts)
            target.scene_before = scene_with_cast(target, chosen)
        chat.message_list.insert(index, target)
        for record in chat.meta.tool_call_records:
            if record.message_index >= index:
                record.message_index += 1
        saved = bool(save())
        return saved
    finally:
        if not saved:
            _restore_history(chat, original_data, original_messages, original_meta, active, summary_invalidated)
            for message, scene in zip(original_messages, previous_scenes):
                object.__setattr__(message, "scene_before", scene)


def _write_casts(messages, casts):
    """补齐所有台词的左右快照，删除或点播时不再依赖前一句。"""
    for message, cast in zip(messages, casts):
        scene = scene_with_cast(message, cast)
        if scene != message.scene_before:
            message.scene_before = scene


def save_cast(roster, index, cast, save):
    """一次保存同一段阵容。未写入磁盘时恢复场景和存档视图。"""
    messages = list(roster.chat.message_list)
    casts = roster.casts()
    previous = casts[index]
    if cast == previous:
        return True
    end = cast_run_end(casts, index, previous)
    casts[index:end] = [cast] * (end - index)
    scenes = [message.scene_before for message in messages]
    opening_before = (roster.meta.character_0.character_id, roster.meta.character_1.character_id)
    roster.chat._timeline.check_editable()
    saved = False
    try:
        roster.record_opening()
        _write_casts(messages, casts)
        saved = bool(save())
        return saved
    finally:
        if not saved:
            roster.meta.character_0.character_id, roster.meta.character_1.character_id = opening_before
            for message, scene in zip(messages, scenes):
                if message.scene_before != scene:
                    message.scene_before = scene


def save_speaker(chat, message, role, name, save):
    """保存稳定说话人身份，失败后恢复原台词和协议状态。"""
    timeline = chat._timeline
    previous_data = deepcopy(timeline.data)
    previous = (message.character_name, message.speaker_id)
    previous_summary = timeline.summary_invalidated
    saved = False
    try:
        message.set_speaker(name, role)
        saved = bool(save())
        return saved
    finally:
        if not saved:
            timeline.data.clear()
            timeline.data.update(previous_data)
            timeline.invalidate_entries()
            timeline.summary_invalidated = previous_summary
            object.__setattr__(message, "character_name", previous[0])
            object.__setattr__(message, "speaker_id", previous[1])


def save_emotion(chat, message, emotion, save):
    """立即保存本句情绪，写盘失败时恢复消息和协议状态。"""
    timeline = chat._timeline
    previous_data = deepcopy(timeline.data)
    previous_emotion = message.emotion
    previous_summary = timeline.summary_invalidated
    saved = False
    try:
        message.emotion = emotion
        saved = bool(save())
        return saved
    finally:
        if not saved:
            timeline.data.clear()
            timeline.data.update(previous_data)
            timeline.invalidate_entries()
            timeline.summary_invalidated = previous_summary
            object.__setattr__(message, "emotion", previous_emotion)


def delete_saved_message(chat: Chat, message: Message, save: Callable[[], bool], *, roster=None) -> bool:
    """按对象身份删除并保存；失败恢复原列表和元数据，不删除音频文件。"""
    index = next((index for index, item in enumerate(chat.message_list) if item is message), -1)
    if index < 0:
        return False
    original_messages = list(chat.message_list)
    previous_scenes = [item.scene_before for item in original_messages] if roster else []
    original_meta = chat.meta
    original_data = deepcopy(chat._timeline.data)
    active = chat._timeline.active_assistant_id
    summary_invalidated = chat._timeline.summary_invalidated
    # 删除同时会更新摘要及工具记录。在副本上修改，失败连原元数据对象也不污染。
    staged_meta = deepcopy(original_meta)
    saved = False
    try:
        chat.meta = staged_meta
        if roster is not None:
            roster.record_opening()
            _write_casts(original_messages, roster.casts())
        chat.delete_message_at(index)
        saved = bool(save())
        return saved
    finally:
        if not saved:
            _restore_history(chat, original_data, original_messages, original_meta, active, summary_invalidated)
            for item, scene in zip(original_messages, previous_scenes):
                object.__setattr__(item, "scene_before", scene)


def confirm_message_deletion(parent, message, discard_drafts=False) -> bool:
    """只确认一次，明确句前场景及当前草稿的归属，默认和退出操作均为取消。"""
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("删除本句")
    dialog.setIcon(QMessageBox.Warning)
    dialog.setTextFormat(Qt.PlainText)
    dialog.setText("确定删除本句吗？")
    excerpt = " ".join((message.text or "").split())
    if len(excerpt) > 80:
        excerpt = excerpt[:80] + "…"
    details = (
        "本句的文本、音频引用、演出编排和句前场景变化将一起删除。"
    )
    if discard_drafts:
        details += "\n本句未保存的文本和编排草稿也会丢弃。"
    if excerpt:
        details = f"「{excerpt}」\n\n{details}"
    dialog.setInformativeText(details)
    delete = dialog.addButton("删除本句", QMessageBox.DestructiveRole)
    cancel = dialog.addButton("取消", QMessageBox.RejectRole)
    dialog.setDefaultButton(cancel)
    dialog.setEscapeButton(cancel)
    try:
        dialog.exec_()
        return dialog.clickedButton() is delete
    finally:
        dialog.deleteLater()
