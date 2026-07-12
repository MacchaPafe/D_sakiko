"""剧情时间线与可空剧情学年语义测试。"""

from __future__ import annotations

import unittest

from rag.models import (
    CanonBranch,
    CharacterId,
    LoreEntryDocument,
    LoreQuery,
    RetrievalContext,
    ScopeType,
    SeriesId,
    StoryEventDocument,
    StoryEventQuery,
)
from rag.services import QdrantRagService, RagServiceConfig


class TemporalModelTest(unittest.TestCase):
    """验证时间线隔离与非严格学年过滤。"""

    def test_story_event_keeps_occurrence_year_without_same_year_option(self) -> None:
        """事件发生学年应保留，但查询选项不再表达同年限制。"""

        event = StoryEventDocument(
            timeline_id="bang_dream_original",
            occurred_story_year=1,
            series_id=SeriesId.BANG_DREAM_1,
            episode=1,
            time_order=1,
            visible_from=1,
            visible_to=999,
            canon_branch=CanonBranch.MAIN,
            title="相遇",
            summary="角色相遇。",
            participants=[CharacterId.KASUMI],
            importance=1,
            tags=["相遇"],
            retrieval_text="香澄与同伴相遇。",
        )
        context = RetrievalContext(current_time=100, current_timeline_id="bang_dream_original", current_story_year=3)
        service = QdrantRagService(RagServiceConfig(qdrant_location=":memory:"))

        self.assertTrue(service._story_event_matches(event, context, StoryEventQuery(require_character_match=False)))
        self.assertFalse(hasattr(StoryEventQuery(), "limit_to_season"))

    def test_lore_with_applicable_year_is_unavailable_when_current_year_unknown(self) -> None:
        """受学年限制的 Lore 在当前学年未知时默认不可用。"""

        lore = LoreEntryDocument(
            scope_type=ScopeType.GLOBAL,
            series_ids=None,
            timeline_id="bang_dream_original",
            applicable_story_years=[3],
            visible_from=None,
            visible_to=None,
            canon_branch=CanonBranch.MAIN,
            title="学校",
            content="第三学年的学校设定。",
            retrieval_text="学校设定",
            tags=["学校"],
        )
        context = RetrievalContext(current_time=100, current_timeline_id="bang_dream_original")
        service = QdrantRagService(RagServiceConfig(qdrant_location=":memory:"))

        self.assertFalse(service._lore_entry_matches(lore, context, LoreQuery()))


if __name__ == "__main__":
    unittest.main()
