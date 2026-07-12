"""Character Relation 当前类型模块。"""

from __future__ import annotations

from dataclasses import dataclass

from rag.models import CharacterRelationDocument

from ..models import EntryType, WorldbookEntry
from .common import display_fields


@dataclass(frozen=True, slots=True)
class CharacterRelationTypeModule:
    """集中解释当前 Character Relation Schema。"""

    entry_type: EntryType = "character_relation"
    projection_version: int = 0

    def _document(self, entry: WorldbookEntry) -> CharacterRelationDocument:
        """解析强类型角色关系。"""

        return CharacterRelationDocument.from_payload(entry.content)

    def validate(self, entry: WorldbookEntry) -> None:
        """验证当前内容。"""

        self._document(entry)

    def display_fields(self, entry: WorldbookEntry) -> list[tuple[str, str]]:
        """生成只读字段。"""

        return display_fields(self.payload(entry))

    def embedding_text(self, entry: WorldbookEntry) -> str:
        """生成角色关系 embedding 文本。"""

        return self._document(entry).retrieval_text

    def payload(self, entry: WorldbookEntry) -> dict[str, object]:
        """生成角色关系索引投影。"""

        return self._document(entry).to_payload()
