"""反馈的本地导入与来源追踪；不扫描角色、不修改原始反馈。"""

from __future__ import annotations

import time
from typing import cast

from character import CharacterAttributes
from chat.chat import Chat, ChatManager
from feedback.protocol import Json, feedback_chat_dict, validate_payload

SOURCE_KEY = "feedback_source"


def source_of(chat: Chat) -> dict[str, object] | None:
    """读取本地来源信息；旧对话没有此字段。"""
    source = chat.meta.extra.get(SOURCE_KEY)
    return source if isinstance(source, dict) else None


def find_imported_chat(manager: ChatManager, endpoint: str, feedback_id: str) -> Chat | None:
    """只复用原导入记录，复制、分叉或备份重导入不冒充它。"""
    for chat in manager.single_character_chats():
        source = source_of(chat)
        if source and source.get("endpoint") == endpoint and source.get("feedback_id") == feedback_id and source.get("local_chat_id") == chat.chat_id:
            return chat
    return None


def import_feedback(manager: ChatManager, detail: dict[str, Json], endpoint: str,
                    character: CharacterAttributes) -> Chat:
    """创建并持久保存独立文本对话；保存失败则撤销本次新增。"""
    feedback_id = str(detail["feedback_id"])
    existing = find_imported_chat(manager, endpoint, feedback_id)
    if existing is not None:
        return existing
    if int(str(detail["expires_at"])) <= time.time():
        raise ValueError("反馈已到期，不能导入。")
    payload = cast(dict[str, Json], detail["payload"])
    validate_payload(payload)
    chat = Chat.from_dict(feedback_chat_dict(payload, character.character_name))
    chat.name = manager._unique_chat_name("反馈 · " + chat.name)
    conversation = cast(dict[str, Json], payload["conversation"])
    chat.meta.extra[SOURCE_KEY] = {
        "endpoint": endpoint, "feedback_id": feedback_id, "expires_at": detail["expires_at"],
        "local_chat_id": chat.chat_id, "imported_at": int(time.time()),
        "original_character": conversation["character"],
        "selected_character_id": character.character_folder_name,
        "target": payload["target"], "status": "available", "checked_at": int(time.time()),
        "last_available_at": int(time.time()),
    }
    # meta 默认关闭世界书；不把诊断或来源角色配置当作本机运行配置。
    manager.add_chat(chat)
    try:
        manager.save()
    except Exception:
        manager.delete_chat(chat.chat_id)
        raise
    return chat


def source_status_text(source: dict[str, object]) -> str:
    """区分已不可用与检查失败；不因网络故障宣称原记录已清除。"""
    status = source.get("status")
    if status == "unavailable" or int(source.get("expires_at", 0)) <= time.time():
        text = "原始反馈已不可用（撤回、删除或到期），本地对话已保留。"
    elif status == "available":
        text = "原始反馈仍可用。"
    else:
        text = "暂时无法检查原始反馈，本地对话已保留。"
    checked = source.get("checked_at")
    if isinstance(checked, (int, float)):
        text += " 最近检查：" + time.strftime("%Y-%m-%d %H:%M", time.localtime(checked))
    return text
