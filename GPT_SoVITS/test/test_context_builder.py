"""RagContextBuilder 的单元测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rag.context_builder import RagContextBuilder
from rag.models import CanonBranch, SeriesId, SeasonId


class TestBuildQueryText(unittest.TestCase):
    """测试 build_query_text 方法。"""

    def setUp(self) -> None:
        self.builder = RagContextBuilder(query_window_size=3)

    def test_basic_message(self) -> None:
        """单条消息生成查询文本。"""
        result = self.builder.build_query_text("你好")
        self.assertEqual(result, "你好")

    def test_with_recent_messages(self) -> None:
        """包含最近消息的查询文本。"""
        result = self.builder.build_query_text(
            current_message="今天的任务",
            recent_messages=["昨天的消息", "前天的消息"],
        )
        self.assertIn("今天的任务", result)
        self.assertIn("昨天的消息", result)
        self.assertIn("前天的消息", result)

    def test_window_size_limit(self) -> None:
        """滑动窗口只取最近 N 条消息。"""
        builder = RagContextBuilder(query_window_size=2)
        result = builder.build_query_text(
            current_message="当前",
            recent_messages=["消息1", "消息2", "消息3", "消息4"],
        )
        self.assertIn("当前", result)
        self.assertIn("消息3", result)
        self.assertIn("消息4", result)
        self.assertNotIn("消息1", result)
        self.assertNotIn("消息2", result)

    def test_with_situation(self) -> None:
        """包含场景描述的查询文本（场景在最前面）。"""
        result = self.builder.build_query_text(
            current_message="对话内容",
            situation="放学后的教室",
        )
        lines = result.split("\n")
        self.assertEqual(lines[0], "放学后的教室")
        self.assertEqual(lines[1], "对话内容")

    def test_max_length_truncation(self) -> None:
        """超长文本被截断。"""
        result = self.builder.build_query_text(
            current_message="a" * 600,
            max_length=100,
        )
        self.assertEqual(len(result), 100)

    def test_empty_messages_filtered(self) -> None:
        """空白消息被过滤。"""
        result = self.builder.build_query_text(
            current_message="内容",
            recent_messages=["", "  ", "有效消息"],
        )
        self.assertIn("有效消息", result)
        self.assertEqual(result.count("\n"), 1)  # 只有"内容"和"有效消息"


class TestBuildRetrievalContext(unittest.TestCase):
    """测试 build_retrieval_context 方法。"""

    def setUp(self) -> None:
        self.builder = RagContextBuilder()

    def test_minimal_context(self) -> None:
        """仅提供必填参数。"""
        ctx = self.builder.build_retrieval_context(current_time=100)
        self.assertEqual(ctx.current_time, 100)
        self.assertIsNone(ctx.current_character_id)
        self.assertIsNone(ctx.current_series_id)
        self.assertIsNone(ctx.current_season_id)
        self.assertEqual(ctx.current_canon_branch, CanonBranch("main"))

    def test_full_context(self) -> None:
        """提供所有参数。"""
        ctx = self.builder.build_retrieval_context(
            current_time=50,
            series_id="its_mygo",
            season_id=1,
            canon_branch="main",
        )
        self.assertEqual(ctx.current_time, 50)
        self.assertEqual(ctx.current_series_id, SeriesId("its_mygo"))
        self.assertEqual(ctx.current_season_id, SeasonId(1))


class TestExtractTagKeywords(unittest.TestCase):
    """测试 extract_tag_keywords 方法。"""

    def setUp(self) -> None:
        self.builder = RagContextBuilder()

    def test_no_known_tags(self) -> None:
        """没有已知标签时返回空列表。"""
        result = self.builder.extract_tag_keywords("测试文本")
        self.assertEqual(result, [])

    def test_matching_tags(self) -> None:
        """匹配已知标签。"""
        tags = {"MyGO", "Ave Mujica", "CRYCHIC"}
        result = self.builder.extract_tag_keywords(
            "今天MyGO和Ave Mujica的表演很精彩", tags
        )
        self.assertIn("MyGO", result)
        self.assertIn("Ave Mujica", result)
        self.assertNotIn("CRYCHIC", result)

    def test_case_insensitive(self) -> None:
        """标签匹配不区分大小写。"""
        tags = {"MyGO"}
        result = self.builder.extract_tag_keywords("mygo太好了", tags)
        self.assertEqual(result, ["MyGO"])

    def test_deduplication(self) -> None:
        """结果去重。"""
        tags = {"MyGO"}
        result = self.builder.extract_tag_keywords("MyGO是MyGO", tags)
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
