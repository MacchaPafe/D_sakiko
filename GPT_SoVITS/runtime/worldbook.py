"""无窗口依赖的普通对话世界书快照。"""

from chat.chat import ChatType
from rag.worldbook.runtime.conversation import (
    freeze_worldbook_snapshot,
    normalize_character_knowledge_mappings,
)
from log import get_logger

logger = get_logger(__name__)


def freeze_turn_snapshot(
    chat, character, catalog, config, chat_manager, *, append_user_message=True
):
    if chat is None or character is None or chat.type != ChatType.SINGLE_CHARACTER:
        return None
    if not append_user_message:
        message_index = chat.find_last_real_user_message_index()
        if message_index is not None:
            existing = chat.message_list[message_index].worldbook_snapshot
            if existing is not None:
                return existing.model_dump(mode="json")

    settings = chat.meta.worldbook
    mappings = normalize_character_knowledge_mappings(
        config.worldbook_character_mappings.value
    )
    resolution = freeze_worldbook_snapshot(
        catalog,
        enabled=settings.enabled,
        root_package_id=settings.root_package_id,
        episode=settings.episode,
        character_folder_name=character.character_folder_name,
        mappings=mappings,
    )
    snapshot = resolution.snapshot
    if snapshot is None:
        return None
    if not append_user_message:
        message_index = chat.find_last_real_user_message_index()
        if message_index is not None:
            chat.message_list[message_index].worldbook_snapshot = snapshot
            try:
                chat_manager.save()
            except Exception:
                logger.exception("补写旧用户消息的世界书快照失败")
    return snapshot.model_dump(mode="json")
