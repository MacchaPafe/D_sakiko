"""Lore Entry 当前类型模块。"""

from __future__ import annotations

from dataclasses import dataclass

from rag.models import LoreEntryDocument

from ..models import EntryType, WorldbookEntry
from .common import display_fields


@dataclass(frozen=True, slots=True)
class LoreEntryTypeModule:
    """集中解释当前 Lore Entry Schema。"""

    entry_type: EntryType = "lore_entry"
    projection_version: int = 0
    semantic_fields: frozenset[str] = frozenset({"title", "content"})

    def _document(self, entry: WorldbookEntry) -> LoreEntryDocument:
        """解析强类型世界观条目。"""

        return LoreEntryDocument.from_payload(entry.content)

    def validate(self, entry: WorldbookEntry) -> None:
        """验证当前内容。"""

        self._document(entry)

    def display_fields(self, entry: WorldbookEntry) -> list[tuple[str, str]]:
        """生成只读字段。"""

        return display_fields(self.payload(entry))

    def embedding_text(self, entry: WorldbookEntry) -> str:
        """生成 Lore embedding 文本。"""

        return self._document(entry).retrieval_text

    def basic_retrieval_text(self, entry: WorldbookEntry) -> str:
        """用标题和正文生成透明的基础检索文本。"""

        document = self._document(entry)
        return f"{document.title}：{document.content}"

    def payload(self, entry: WorldbookEntry) -> dict[str, object]:
        """生成 Lore 索引投影。"""

        return self._document(entry).to_payload()
