"""跨端测试在两个独立 Python 进程间保存和恢复测试对话。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from chat import attachments, chat as model
from chat.chat import ChatManager, Message, EmotionEnum


def run(root: Path, action: str) -> None:
    """在临时资料目录使用真正的导入、保存、重启读取和导出入口。"""
    media = root / "reference_audio"
    with patch.object(model, "_reference_audio_dir", return_value=media), \
            patch.object(attachments, "reference_audio_dir", return_value=media):
        if action == "import":
            manager = ChatManager()
            result = manager.import_chats_from_backup(root / "electron.zip")
            assert len(result.imported_chats) == 1
            manager.save(root / "chats.json")
            return
        manager = ChatManager.load(root / "chats.json")
        chat = manager.chat_list[0]
        assert [item.text for item in chat.message_list] == ["问", "同一句", "同一句"]
        assert (root / "work" / chat.message_list[1].audio_path).is_file()
        report = manager.export_chats_to_backup([chat.chat_id], root / "unchanged.zip")
        assert report.exported_audio_count == 1, (report, chat.message_list[1].audio_path)
        chat.message_list[0].text = "改过的问题"
        chat.message_list.append(Message("User", "继续聊", "", EmotionEnum.HAPPINESS, ""))
        manager.export_chats_to_backup([chat.chat_id], root / "edited.zip")
        whole_turn = manager.clone_chat(chat.chat_id, fork_after_message_index=2)
        manager.export_chats_to_backup([whole_turn.chat_id], root / "whole-turn.zip")
        branch = manager.clone_chat(chat.chat_id, fork_after_message_index=1)
        manager.export_chats_to_backup([branch.chat_id], root / "fork.zip")


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2])
