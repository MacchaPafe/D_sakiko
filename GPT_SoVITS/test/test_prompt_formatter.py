"""RagPromptFormatter 的单元测试。"""

import os
import sys
import unittest
from typing import List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rag.prompt_formatter import RagPromptFormatter
from rag.models import (
    CharacterId,
    CharacterRelationDocument,
    LoreEntryDocument,
    StoryEventDocument,
    CanonBranch,
    ScopeType,
    SeasonId,
    SeriesId,
)
from rag.services import QueryHit, RetrievalMode


def _make_event(
    title: str = "测试事件",
    summary: str = "这是一个测试事件",
    participants: Optional[List[str]] = None,
) -> QueryHit[StoryEventDocument]:
    """构造测试用的 StoryEventDocument QueryHit。"""
    if participants is None:
        participants = ["kasumi", "tae"]
    doc = StoryEventDocument(
        title=title,
        summary=summary,
        participants=[CharacterId(p) for p in participants],
        season_id=SeasonId(1),
        series_id=SeriesId("its_mygo"),
        episode=1,
        time_order=1,
        visible_from=0,
        visible_to=100,
        canon_branch=CanonBranch("main"),
        importance=1,
        tags=["test"],
        retrieval_text=summary,
    )
    return QueryHit(document=doc, score=0.9, point_id="test-1", source=RetrievalMode.VECTOR)


def _make_relation(
    subject: str = "kasumi",
    obj: str = "tae",
    label: str = "关心",
    summary: str = "kasumi关心tae",
    nickname: str = "小tae",
    speech_hint: str = "温柔",
) -> QueryHit[CharacterRelationDocument]:
    """构造测试用的 CharacterRelationDocument QueryHit。"""
    doc = CharacterRelationDocument(
        subject_character_id=CharacterId(subject),
        object_character_id=CharacterId(obj),
        relation_label=label,
        state_summary=summary,
        object_character_nickname=nickname,
        speech_hint=speech_hint,
        season_id=SeasonId(1),
        series_id=SeriesId("its_mygo"),
        visible_from=0,
        visible_to=100,
        canon_branch=CanonBranch("main"),
        tags=["test"],
        retrieval_text=summary,
    )
    return QueryHit(document=doc, score=0.85, point_id="test-2", source=RetrievalMode.VECTOR)


def _make_lore(
    title: str = "CRYCHIC",
    content: str = "一支过去的乐队",
) -> QueryHit[LoreEntryDocument]:
    """构造测试用的 LoreEntryDocument QueryHit。"""
    doc = LoreEntryDocument(
        title=title,
        content=content,
        scope_type=ScopeType("global"),
        series_ids=None,
        season_ids=None,
        visible_from=None,
        visible_to=None,
        canon_branch=CanonBranch("main"),
        tags=["test"],
        retrieval_text=content,
    )
    return QueryHit(document=doc, score=0.8, point_id="test-3", source=RetrievalMode.VECTOR)


class TestFormatForSingleCharacter(unittest.TestCase):
    """测试单角色模式格式化。"""

    def setUp(self) -> None:
        self.formatter = RagPromptFormatter()

    def test_all_results(self) -> None:
        """三种结果同时存在。"""
        result = self.formatter.format_for_single_character(
            story_events=[_make_event()],
            character_relations=[_make_relation()],
            lore_entries=[_make_lore()],
            perspective_character="kasumi",
        )
        self.assertIn("以下是与当前对话相关的背景信息", result)
        self.assertIn("【近期剧情】", result)
        self.assertIn("【角色关系参考】", result)
        self.assertIn("【相关设定】", result)

    def test_empty_results(self) -> None:
        """没有任何检索结果时返回空字符串。"""
        result = self.formatter.format_for_single_character(
            story_events=[],
            character_relations=[],
            lore_entries=[],
            perspective_character="kasumi",
        )
        self.assertEqual(result, "")

    def test_only_events(self) -> None:
        """仅有剧情事件。"""
        result = self.formatter.format_for_single_character(
            story_events=[_make_event()],
            character_relations=[],
            lore_entries=[],
            perspective_character="kasumi",
        )
        self.assertIn("【近期剧情】", result)
        self.assertNotIn("【角色关系参考】", result)
        self.assertNotIn("【相关设定】", result)

    def test_event_content(self) -> None:
        """剧情事件包含标题、摘要和参与者。"""
        event = _make_event(title="春日影事件", summary="春日影演出失败")
        result = self.formatter.format_single_event(event.document)
        self.assertIn("春日影事件", result)
        self.assertIn("春日影演出失败", result)
        self.assertIn("参与者", result)

    def test_relation_content(self) -> None:
        """角色关系包含态度和称呼。"""
        relation = _make_relation(nickname="小tae", speech_hint="温柔")
        result = self.formatter.format_single_relation(
            relation.document, "kasumi"
        )
        self.assertIn("态度", result)
        self.assertIn("小tae", result)
        self.assertIn("温柔", result)

    def test_lore_content(self) -> None:
        """世界观条目包含标题和内容。"""
        lore = _make_lore(title="RAS", content="乐队名称")
        result = self.formatter.format_single_lore(lore.document)
        self.assertIn("RAS", result)
        self.assertIn("乐队名称", result)


class TestFormatForSmallTheater(unittest.TestCase):
    """测试小剧场模式格式化。"""

    def setUp(self) -> None:
        self.formatter = RagPromptFormatter()

    def test_all_results(self) -> None:
        """小剧场模式包含关系和剧情。"""
        result = self.formatter.format_for_small_theater(
            story_events=[_make_event()],
            character_relations=[_make_relation()],
            lore_entries=[_make_lore()],
            character_pair=("kasumi", "tae"),
        )
        self.assertIn("请严格参照", result)
        self.assertIn("【角色关系】", result)
        self.assertIn("【相关剧情】", result)
        self.assertIn("【世界观】", result)

    def test_empty_results(self) -> None:
        """无结果返回空字符串。"""
        result = self.formatter.format_for_small_theater(
            story_events=[],
            character_relations=[],
            lore_entries=[],
            character_pair=("kasumi", "tae"),
        )
        self.assertEqual(result, "")

    def test_relation_includes_nickname(self) -> None:
        """小剧场关系包含称呼信息。"""
        result = self.formatter.format_for_small_theater(
            story_events=[],
            character_relations=[_make_relation(nickname="小tae")],
            lore_entries=[],
            character_pair=("kasumi", "tae"),
        )
        self.assertIn("称呼：小tae", result)


if __name__ == "__main__":
    unittest.main()
