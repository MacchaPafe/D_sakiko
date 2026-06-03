from __future__ import annotations

import os
import sys
import unittest
from unittest import mock


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from chat import model_token_usage


class ModelTokenUsageTestCase(unittest.TestCase):
    """验证模型 token 用量工具的 LiteLLM 与本地 fallback 行为。"""

    def setUp(self) -> None:
        """清理带缓存的模型上限查询，避免测试互相影响。"""
        model_token_usage.get_model_input_token_limit.cache_clear()

    def test_litellm_limit_takes_precedence(self) -> None:
        """LiteLLM 能返回上限时，应优先使用 LiteLLM 的结果。"""
        with mock.patch.object(
            model_token_usage,
            "_get_litellm_model_input_token_limit",
            return_value=131072,
        ):
            limit = model_token_usage.get_model_input_token_limit("deepseek/deepseek-v4-flash")

        self.assertEqual(limit, 131072)

    def test_deepseek_v4_flash_uses_local_override_when_litellm_missing(self) -> None:
        """DeepSeek V4 Flash 未被 LiteLLM 收录时，应使用本地上下文补充表。"""
        with mock.patch.object(
            model_token_usage,
            "_get_litellm_model_input_token_limit",
            return_value=None,
        ):
            limit = model_token_usage.get_model_input_token_limit("deepseek/deepseek-v4-flash")

        self.assertEqual(limit, 1000000)

    def test_deepseek_v4_pro_openai_compatible_alias_matches_override(self) -> None:
        """OpenAI-compatible 路由前缀下的 DeepSeek V4 Pro 别名也应命中补充表。"""
        with mock.patch.object(
            model_token_usage,
            "_get_litellm_model_input_token_limit",
            return_value=None,
        ):
            limit = model_token_usage.get_model_input_token_limit("openai/deepseek-ai/DeepSeek-V4-Pro")

        self.assertEqual(limit, 1000000)


if __name__ == "__main__":
    unittest.main()
