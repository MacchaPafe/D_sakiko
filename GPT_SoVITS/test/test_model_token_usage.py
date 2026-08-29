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

    def test_count_message_tokens_adds_file_cost_without_mutating_messages(self) -> None:
        """统计 file 内容块时应移除副本中的块并按出现次数补回成本。"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    {"type": "file", "file_id": "file-1"},
                    {
                        "type": "tool_result",
                        "content": [{"type": "file", "file_id": "file-1"}],
                    },
                ],
            },
            {
                "role": "user",
                "content": [{"type": "file", "file_id": "file-2"}],
            },
        ]
        original_messages = repr(messages)

        with mock.patch("litellm.token_counter", return_value=17) as token_counter:
            token_count = model_token_usage.count_message_tokens(
                "test/model",
                messages,
                file_token_cost=384,
            )

        self.assertEqual(token_count, 17 + 3 * 384)
        self.assertEqual(repr(messages), original_messages)
        sanitized_messages = token_counter.call_args.kwargs["messages"]
        self.assertNotIn('"type": "file"', repr(sanitized_messages))
        self.assertEqual(sanitized_messages[1]["content"], [])

    def test_count_message_tokens_keeps_legacy_file_behavior_without_policy(self) -> None:
        """未传入文件策略时应原样交给 LiteLLM。"""
        messages = [{
            "role": "user",
            "content": [{"type": "file", "file_id": "file-1"}],
        }]

        with mock.patch("litellm.token_counter", return_value=9) as token_counter:
            token_count = model_token_usage.count_message_tokens("test/model", messages)

        self.assertEqual(token_count, 9)
        token_counter.assert_called_once_with(model="test/model", messages=messages)

    def test_count_message_tokens_rejects_invalid_file_cost(self) -> None:
        """文件 token 成本必须是正整数。"""
        messages = [{"role": "user", "content": "hello"}]
        for invalid_cost in (True, 0, -1, 1.5):
            with self.subTest(invalid_cost=invalid_cost):
                with self.assertRaises(ValueError):
                    model_token_usage.count_message_tokens(
                        "test/model",
                        messages,
                        file_token_cost=invalid_cost,
                    )


if __name__ == "__main__":
    unittest.main()
