"""解析角色映射并在消息入队时冻结世界书上下文。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from rag.models import CharacterId

from .catalog import WorldbookCatalogError, WorldbookRootCatalog
from .models import WorldbookTurnSnapshot


class WorldbookSnapshotResolution(BaseModel):
    """描述本轮是否成功冻结世界书快照及安全关闭原因。"""

    model_config = ConfigDict(extra="forbid")

    snapshot: WorldbookTurnSnapshot | None = None
    disabled_reason: str | None = None


def normalize_character_knowledge_mappings(
    raw_value: object,
) -> dict[str, CharacterId]:
    """清理配置中的角色文件夹到规范 CharacterId 映射。"""

    if not isinstance(raw_value, dict):
        return {}
    mappings: dict[str, CharacterId] = {}
    for raw_folder, raw_character in raw_value.items():
        if not isinstance(raw_folder, str) or not raw_folder.strip():
            continue
        if not isinstance(raw_character, str):
            continue
        try:
            mappings[raw_folder.strip()] = CharacterId(raw_character)
        except ValueError:
            continue
    return mappings


def freeze_worldbook_snapshot(
    catalog: WorldbookRootCatalog,
    *,
    enabled: bool,
    root_package_id: str,
    episode: int | None,
    character_folder_name: str,
    mappings: dict[str, CharacterId],
) -> WorldbookSnapshotResolution:
    """根据对话设置和全局角色映射生成一份不可变回合快照。"""

    if not enabled:
        return WorldbookSnapshotResolution(disabled_reason="worldbook_disabled")
    if not root_package_id or episode is None:
        return WorldbookSnapshotResolution(disabled_reason="incomplete_worldbook_settings")
    character_id = mappings.get(character_folder_name)
    if character_id is None:
        try:
            character_id = CharacterId(character_folder_name)
        except ValueError:
            character_id = None
    if character_id is None:
        return WorldbookSnapshotResolution(disabled_reason="unmapped_character")
    try:
        context = catalog.resolve(root_package_id, episode, character_id)
    except (TypeError, ValueError, WorldbookCatalogError) as exc:
        return WorldbookSnapshotResolution(disabled_reason=str(exc))
    return WorldbookSnapshotResolution(
        snapshot=WorldbookTurnSnapshot.model_validate(context.model_dump(mode="json"))
    )
