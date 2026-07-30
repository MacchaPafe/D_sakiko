"""向聊天层暴露世界书运行时的稳定小接口。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rag.worldbook.paths import WorldbookPaths

from .catalog import WorldbookCatalogError, WorldbookRootCatalog
from .models import (
    DirectThought,
    LinkedStoryEvent,
    LoreKnowledge,
    RelationHistoryPage,
    RelationKnowledge,
    ThoughtMemory,
    WorldbookResolvedContext,
    WorldbookRootOption,
    WorldbookTurnSnapshot,
)
from .readiness import WorldbookIndexReadinessState

if TYPE_CHECKING:
    from .service import WorldbookConversationService


def create_worldbook_conversation_service(
    app_root: Path,
    readiness: WorldbookIndexReadinessState,
) -> "WorldbookConversationService":
    """从应用根目录和共享就绪态创建世界书服务。"""

    from .retrieval import WorldbookRetrievalRepository
    from .service import WorldbookConversationService

    paths = WorldbookPaths(app_root.resolve())
    catalog = WorldbookRootCatalog(paths.official_packages, paths.user_state)
    retrieval = WorldbookRetrievalRepository(
        paths.index,
        paths.lock,
        paths.embedding_model,
        readiness,
    )
    return WorldbookConversationService(catalog, retrieval)

__all__ = [
    "DirectThought",
    "LinkedStoryEvent",
    "LoreKnowledge",
    "RelationHistoryPage",
    "RelationKnowledge",
    "ThoughtMemory",
    "WorldbookCatalogError",
    "WorldbookResolvedContext",
    "WorldbookRootCatalog",
    "WorldbookRootOption",
    "WorldbookTurnSnapshot",
    "WorldbookIndexReadinessState",
    "create_worldbook_conversation_service",
]
