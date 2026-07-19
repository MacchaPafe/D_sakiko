"""Story Event 当前类型模块。"""

from __future__ import annotations

from dataclasses import dataclass

from rag.models import StoryEventDocument

from ..models import EntryType, WorldbookEntry
from .common import display_fields


@dataclass(frozen=True, slots=True)
class StoryEventTypeModule:
    """集中解释当前 Story Event Schema。"""

    entry_type: EntryType = "story_event"
    projection_version: int = 0
    semantic_fields: frozenset[str] = frozenset({"title", "summary", "participants"})

    def _document(self, entry: WorldbookEntry) -> StoryEventDocument:
        """解析强类型剧情事件。"""

        return StoryEventDocument.from_payload(entry.content)

    def validate(self, entry: WorldbookEntry) -> None:
        """验证当前内容。"""

        self._document(entry)

    def display_fields(self, entry: WorldbookEntry) -> list[tuple[str, str]]:
        """生成只读字段。"""

        return display_fields(self.payload(entry))

    def embedding_text(self, entry: WorldbookEntry) -> str:
        """生成剧情事件 embedding 文本。"""

        return self._document(entry).retrieval_text

    def basic_retrieval_text(self, entry: WorldbookEntry) -> str:
        """使用事件摘要生成不冒充智能改写的基础检索文本。"""

        return self._document(entry).summary

    def payload(self, entry: WorldbookEntry) -> dict[str, object]:
        """生成剧情事件索引投影。"""

        return self._document(entry).to_payload()
