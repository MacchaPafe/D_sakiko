"""LLMRagIntegration 的单元测试。"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rag.llm_integration import LLMRagIntegration
from rag.models import CharacterId, RetrievalContext


class TestInjectRagIntoMessages(unittest.TestCase):
    """测试 inject_rag_into_messages 静态方法。"""

    def test_system_before_user_mode(self) -> None:
        """system_before_user 模式：在最新 user 消息前插入 system 消息。"""
        messages = [
            {"role": "system", "content": "你是一个AI"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
            {"role": "user", "content": "今天天气？"},
        ]
        rag_prompt = "【背景】今天是晴天"
        result = LLMRagIntegration.inject_rag_into_messages(
            messages, rag_prompt, injection_mode="system_before_user"
        )
        # 应在最后一个 user 消息（index 3）前插入，新 index 3 是 system
        self.assertEqual(len(result), 5)
        self.assertEqual(result[3]["role"], "system")
        self.assertEqual(result[3]["content"], rag_prompt)
        self.assertEqual(result[4]["role"], "user")
        self.assertEqual(result[4]["content"], "今天天气？")

    def test_inline_user_mode(self) -> None:
        """inline_user 模式：将 RAG 内容追加到最新 user 消息末尾。"""
        messages = [
            {"role": "system", "content": "你是一个AI"},
            {"role": "user", "content": "你好"},
        ]
        rag_prompt = "【背景信息】"
        result = LLMRagIntegration.inject_rag_into_messages(
            messages, rag_prompt, injection_mode="inline_user"
        )
        self.assertEqual(len(result), 2)
        self.assertIn("你好", result[1]["content"])
        self.assertIn("【背景信息】", result[1]["content"])

    def test_empty_rag_prompt_returns_copy(self) -> None:
        """空 RAG prompt 返回消息副本。"""
        messages = [{"role": "user", "content": "测试"}]
        result = LLMRagIntegration.inject_rag_into_messages(messages, "")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "测试")

    def test_no_user_message_fallback(self) -> None:
        """无 user 消息时，在末尾追加 system 消息。"""
        messages = [{"role": "system", "content": "系统提示"}]
        rag_prompt = "背景"
        result = LLMRagIntegration.inject_rag_into_messages(
            messages, rag_prompt
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["role"], "system")
        self.assertEqual(result[1]["content"], "背景")

    def test_original_not_modified(self) -> None:
        """原始消息列表不被修改。"""
        messages = [
            {"role": "user", "content": "原始内容"},
        ]
        LLMRagIntegration.inject_rag_into_messages(
            messages, "RAG内容", injection_mode="inline_user"
        )
        self.assertEqual(messages[0]["content"], "原始内容")

    def test_multiple_user_messages(self) -> None:
        """多个 user 消息时只在最后一个前插入。"""
        messages = [
            {"role": "user", "content": "第一句"},
            {"role": "assistant", "content": "回复"},
            {"role": "user", "content": "第二句"},
        ]
        result = LLMRagIntegration.inject_rag_into_messages(
            messages, "RAG", injection_mode="system_before_user"
        )
        # RAG 应插在 index 2（第二个 user 前）
        self.assertEqual(result[2]["role"], "system")
        self.assertEqual(result[2]["content"], "RAG")
        self.assertEqual(result[3]["content"], "第二句")


class TestCacheKey(unittest.TestCase):
    """测试缓存键生成。"""

    def test_same_input_same_key(self) -> None:
        """相同输入生成相同的缓存键。"""
        from rag.context_builder import RagContextBuilder
        from rag.prompt_formatter import RagPromptFormatter

        # 使用 None 作为 rag_service 因为我们不调用 retrieve_and_format
        integration = LLMRagIntegration.__new__(LLMRagIntegration)
        key1 = integration._get_cache_key("query", "char", 100)
        key2 = integration._get_cache_key("query", "char", 100)
        self.assertEqual(key1, key2)

    def test_different_input_different_key(self) -> None:
        """不同输入生成不同的缓存键。"""
        integration = LLMRagIntegration.__new__(LLMRagIntegration)
        key1 = integration._get_cache_key("query1", "char", 100)
        key2 = integration._get_cache_key("query2", "char", 100)
        self.assertNotEqual(key1, key2)


class TestCacheBehavior(unittest.TestCase):
    """测试缓存读写和过期逻辑。"""

    def test_cache_put_and_get(self) -> None:
        """写入缓存后可以读取。"""
        integration = LLMRagIntegration.__new__(LLMRagIntegration)
        integration.cache_enabled = True
        integration.cache_ttl_seconds = 300
        integration._cache = {}

        integration._put_to_cache("key1", "result1")
        result = integration._get_from_cache("key1")
        self.assertEqual(result, "result1")

    def test_cache_miss(self) -> None:
        """缓存不存在时返回 None。"""
        integration = LLMRagIntegration.__new__(LLMRagIntegration)
        integration.cache_enabled = True
        integration.cache_ttl_seconds = 300
        integration._cache = {}

        result = integration._get_from_cache("nonexistent")
        self.assertIsNone(result)

    def test_cache_expired(self) -> None:
        """缓存过期后返回 None。"""
        integration = LLMRagIntegration.__new__(LLMRagIntegration)
        integration.cache_enabled = True
        integration.cache_ttl_seconds = 0  # 立即过期
        integration._cache = {}

        integration._put_to_cache("key1", "result1")
        # 等待很短的时间确保过期
        time.sleep(0.01)
        result = integration._get_from_cache("key1")
        self.assertIsNone(result)

    def test_cache_disabled(self) -> None:
        """缓存禁用时始终返回 None。"""
        integration = LLMRagIntegration.__new__(LLMRagIntegration)
        integration.cache_enabled = False
        integration.cache_ttl_seconds = 300
        integration._cache = {}

        integration._put_to_cache("key1", "result1")
        result = integration._get_from_cache("key1")
        self.assertIsNone(result)


class TestRelationshipPerspectiveFiltering(unittest.TestCase):
    """验证集成层按当前扮演角色选择关系检索策略。"""

    def _build_integration(self) -> tuple[LLMRagIntegration, MagicMock, MagicMock]:
        """构造不依赖真实数据库的 RAG 集成对象。"""

        rag_service = MagicMock()
        rag_service.query_story_events.return_value = []
        rag_service.query_character_relations.return_value = []
        rag_service.query_lore_entries.return_value = []

        context_builder = MagicMock()
        context_builder.build_query_text.return_value = "测试查询"
        context_builder.build_retrieval_context.return_value = RetrievalContext(current_time=50)

        formatter = MagicMock()
        formatter.format_for_single_character.return_value = "格式化结果"

        integration = LLMRagIntegration(
            rag_service=rag_service,
            context_builder=context_builder,
            formatter=formatter,
            cache_enabled=False,
        )
        return integration, rag_service, context_builder

    def test_known_character_enables_subject_filter(self) -> None:
        """已知角色会写入上下文并启用关系主体筛选。"""

        integration, rag_service, context_builder = self._build_integration()

        integration.retrieve_and_format(
            current_message="你好",
            recent_messages=[],
            character_name="祥子",
            current_time=50,
        )

        context_kwargs = context_builder.build_retrieval_context.call_args.kwargs
        self.assertEqual(context_kwargs["current_character_id"], CharacterId.SAKIKO)
        relation_options = rag_service.query_character_relations.call_args.kwargs["options"]
        self.assertTrue(relation_options.require_subject_character_match)

    def test_unknown_character_skips_relation_query(self) -> None:
        """未知角色不会退化成无主体限制的关系查询。"""

        integration, rag_service, context_builder = self._build_integration()

        integration.retrieve_and_format(
            current_message="你好",
            recent_messages=[],
            character_name="自定义角色",
            current_time=50,
        )

        context_kwargs = context_builder.build_retrieval_context.call_args.kwargs
        self.assertIsNone(context_kwargs["current_character_id"])
        rag_service.query_character_relations.assert_not_called()

if __name__ == "__main__":
    unittest.main()
