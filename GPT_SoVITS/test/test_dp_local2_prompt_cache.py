from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import dp_local2


class UsageWithModelDump:
    """模拟 litellm Usage 对象的 model_dump 行为。"""

    def model_dump(self) -> dict[str, object]:
        """返回包含 prompt cache 字段的 usage 字典。"""
        return {
            "completion_tokens": 3,
            "prompt_tokens": 100,
            "total_tokens": 103,
            "prompt_cache_hit_tokens": 80,
            "prompt_cache_miss_tokens": 20,
        }


class ResponseWithUsage:
    """模拟带 usage 属性的 litellm 响应对象。"""

    def __init__(self, usage: object, model: str = "deepseek-chat") -> None:
        """保存测试用 usage 和模型名称。"""
        self.usage = usage
        self.model = model


class ResponseWithModelDump:
    """模拟只通过 model_dump 暴露 usage 的响应对象。"""

    def model_dump(self) -> dict[str, object]:
        """返回包含 usage 的响应字典。"""
        return {
            "model": "deepseek-chat",
            "usage": {
                "completion_tokens": 2,
                "prompt_tokens": 40,
                "total_tokens": 42,
                "prompt_cache_hit_tokens": 10,
                "prompt_cache_miss_tokens": 30,
            },
        }


class PromptCacheUsageTestCase(unittest.TestCase):
    """验证 DeepSeek prompt cache usage 的提取和记录。"""

    def test_extract_from_model_dump_usage(self) -> None:
        """usage 为 litellm 对象时，应能读取 prompt cache 字段。"""
        response = ResponseWithUsage(UsageWithModelDump())

        usage = dp_local2._extract_prompt_cache_usage(response)

        self.assertEqual(
            usage,
            {
                "prompt_cache_hit_tokens": 80,
                "prompt_cache_miss_tokens": 20,
                "prompt_tokens": 100,
                "completion_tokens": 3,
                "total_tokens": 103,
            },
        )

    def test_extract_from_response_model_dump(self) -> None:
        """响应只通过 model_dump 暴露 usage 时，应能回退读取。"""
        usage = dp_local2._extract_prompt_cache_usage(ResponseWithModelDump())

        self.assertEqual(usage["prompt_cache_hit_tokens"], 10)
        self.assertEqual(usage["prompt_cache_miss_tokens"], 30)

    def test_missing_cache_fields_is_ignored(self) -> None:
        """缺少 DeepSeek cache 字段时，应返回 None 而不是报错。"""
        response = ResponseWithUsage({"prompt_tokens": 10, "completion_tokens": 1})

        usage = dp_local2._extract_prompt_cache_usage(response)

        self.assertIsNone(usage)

    def test_log_prompt_cache_usage_writes_info(self) -> None:
        """成功提取 usage 后，应写入带固定标识的日志。"""
        response = ResponseWithUsage(
            {
                "completion_tokens": 4,
                "prompt_tokens": 50,
                "total_tokens": 54,
                "prompt_cache_hit_tokens": 25,
                "prompt_cache_miss_tokens": 25,
            },
            model="deepseek-chat",
        )

        with mock.patch.object(dp_local2.logger, "info") as mocked_info:
            dp_local2._log_prompt_cache_usage(response, {"model": "deepseek/deepseek-chat"})

        mocked_info.assert_called_once()
        log_args = mocked_info.call_args.args
        self.assertIn("deepseek_prompt_cache", log_args[0])
        self.assertEqual(log_args[1], "deepseek/deepseek-chat")
        self.assertEqual(log_args[2], 25)
        self.assertEqual(log_args[3], 25)
        self.assertEqual(log_args[4], "50.00%")

    def test_completion_returns_original_response_and_logs_usage(self) -> None:
        """completion 包装函数应返回原始响应，并在成功后记录 usage。"""
        response = ResponseWithUsage(
            {
                "prompt_cache_hit_tokens": 1,
                "prompt_cache_miss_tokens": 2,
            }
        )

        with (
            mock.patch.object(dp_local2.litellm, "completion", return_value=response) as mocked_completion,
            mock.patch.object(dp_local2, "_log_prompt_cache_usage") as mocked_log,
        ):
            result = dp_local2.completion(
                model="openai/deepseek-chat",
                messages=[{"role": "user", "content": "hi"}],
            )

        self.assertIs(result, response)
        mocked_completion.assert_called_once()
        mocked_log.assert_called_once()
        self.assertIs(mocked_log.call_args.args[0], response)


if __name__ == "__main__":
    unittest.main()
