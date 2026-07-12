"""世界书条目类型模块扩展点。"""

from __future__ import annotations

from .character_relation import CharacterRelationTypeModule
from .lore_entry import LoreEntryTypeModule
from .story_event import StoryEventTypeModule

__all__ = ["CharacterRelationTypeModule", "LoreEntryTypeModule", "StoryEventTypeModule"]
