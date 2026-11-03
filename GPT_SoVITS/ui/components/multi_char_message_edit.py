"""小剧场主控与编排器共用的台词增删及保存事务。"""
from copy import deepcopy

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox


def insert_saved_message(chat, anchor, target, after, save) -> bool:
    """按锚点身份插句并同步索引、摘要；失败恢复原列表和元数据对象。"""
    index = next((index for index, item in enumerate(chat.message_list) if item is anchor), -1)
    if index < 0 or any(item is target for item in chat.message_list):
        return False
    index += 1 if after else 0
    original_messages = list(chat.message_list)
    original_meta = chat.meta
    staged_meta = deepcopy(original_meta)
    saved = False
    try:
        from chat.rolling_summary import invalidate_rolling_summary_from_message_index

        chat.meta = staged_meta
        invalidate_rolling_summary_from_message_index(chat, index)
        chat.message_list.insert(index, target)
        for record in chat.meta.tool_call_records:
            if record.message_index >= index:
                record.message_index += 1
        saved = bool(save())
        return saved
    finally:
        if not saved:
            chat.message_list[:] = original_messages
            chat.meta = original_meta


def delete_saved_message(chat, message, save) -> bool:
    """按对象身份删除并保存；失败恢复原列表和元数据，不删除音频文件。"""
    index = next((index for index, item in enumerate(chat.message_list) if item is message), -1)
    if index < 0:
        return False
    original_messages = list(chat.message_list)
    original_meta = chat.meta
    # 删除同时会更新摘要及工具记录。在副本上修改，失败连原元数据对象也不污染。
    staged_meta = deepcopy(original_meta)
    saved = False
    try:
        chat.meta = staged_meta
        chat.delete_message_at(index)
        saved = bool(save())
        return saved
    finally:
        if not saved:
            chat.message_list[:] = original_messages
            chat.meta = original_meta


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
