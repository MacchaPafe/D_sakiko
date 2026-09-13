from __future__ import annotations

from PyQt5.QtWidgets import QMessageBox, QWidget

from chat.chat import ChatManager


def save_before_close(manager: ChatManager, parent: QWidget) -> bool:
    """保存成功或用户明确放弃后才允许窗口退出。"""
    while True:
        try:
            manager.save()
            return True
        except Exception as exc:
            answer = QMessageBox.warning(
                parent, "聊天记录尚未保存",
                f"保存失败：{exc}\n未保存内容仍在内存中。请选择重试、放弃未保存内容退出，或取消退出。",
                QMessageBox.Retry | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Retry,
            )
            if answer == QMessageBox.Discard:
                return True
            if answer != QMessageBox.Retry:
                return False
