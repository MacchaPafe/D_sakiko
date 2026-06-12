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
        ) as mocked_get_litellm_limit:
            limit = model_token_usage.get_model_input_token_limit("deepseek/deepseek-v4-flash")

        self.assertEqual(limit, 131072)
        mocked_get_litellm_limit.assert_called_once_with(
            "deepseek/deepseek-v4-flash",
            suppress_unmapped_debug=True,
        )

    def test_deepseek_v4_flash_uses_local_override_when_litellm_missing(self) -> None:
        """DeepSeek V4 Flash 未被 LiteLLM 收录时，应使用本地上下文补充表。"""
        with mock.patch.object(
            model_token_usage,
            "_get_litellm_model_input_token_limit",
            return_value=None,
        ) as mocked_get_litellm_limit:
            limit = model_token_usage.get_model_input_token_limit("deepseek/deepseek-v4-flash")

        self.assertEqual(limit, 1000000)
        mocked_get_litellm_limit.assert_called_once_with(
            "deepseek/deepseek-v4-flash",
            suppress_unmapped_debug=True,
        )

    def test_deepseek_v4_pro_openai_compatible_alias_matches_override(self) -> None:
        """OpenAI-compatible 路由前缀下的 DeepSeek V4 Pro 别名也应命中补充表。"""
        with mock.patch.object(
            model_token_usage,
            "_get_litellm_model_input_token_limit",
            return_value=None,
        ):
            limit = model_token_usage.get_model_input_token_limit("openai/deepseek-ai/DeepSeek-V4-Pro")

        self.assertEqual(limit, 1000000)

    def test_local_override_suppresses_litellm_unmapped_debug(self) -> None:
        """本地已覆盖的模型未被 LiteLLM 收录时，不应记录预期 miss。"""
        unmapped_error = Exception("This model isn't mapped yet. model=deepseek/deepseek-v4-pro")
        with (
            mock.patch("litellm.get_model_info", side_effect=unmapped_error),
            mock.patch.object(model_token_usage.logger, "debug") as mocked_debug,
        ):
            limit = model_token_usage.get_model_input_token_limit("deepseek/deepseek-v4-pro")

        self.assertEqual(limit, 1000000)
        mocked_debug.assert_not_called()

    def test_unknown_model_litellm_miss_still_logs_debug(self) -> None:
        """没有本地覆盖的未知模型仍应记录 LiteLLM lookup miss。"""
        unmapped_error = Exception("This model isn't mapped yet. model=unknown/model")
        with (
            mock.patch("litellm.get_model_info", side_effect=unmapped_error),
            mock.patch.object(model_token_usage.logger, "debug") as mocked_debug,
        ):
            limit = model_token_usage.get_model_input_token_limit("unknown/model")

        self.assertIsNone(limit)
        mocked_debug.assert_called_once_with(
            "查询 LiteLLM 模型上下文上限失败：%s",
            unmapped_error,
        )

    def test_local_override_does_not_suppress_other_litellm_errors(self) -> None:
        """本地有覆盖时，LiteLLM 的非收录类异常仍应记录。"""
        runtime_error = RuntimeError("LiteLLM registry unavailable")
        with (
            mock.patch("litellm.get_model_info", side_effect=runtime_error),
            mock.patch.object(model_token_usage.logger, "debug") as mocked_debug,
        ):
            limit = model_token_usage.get_model_input_token_limit("deepseek/deepseek-v4-flash")

        self.assertEqual(limit, 1000000)
        mocked_debug.assert_called_once_with(
            "查询 LiteLLM 模型上下文上限失败：%s",
            runtime_error,
        )


if __name__ == "__main__":
    unittest.main()
