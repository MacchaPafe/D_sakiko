from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

import httpx
import litellm

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import dp_local2
from llm_provider.modelscope import (
    ModelScopeQuotaExceededError,
    ModelScopeRateLimitKind,
    classify_rate_limit,
    extract_error_headers,
    parse_quota_headers,
    parse_retry_after_seconds,
)


class ConfigValue:
    """模拟配置快照中的单个 value 包装对象。"""

    def __init__(self, value: object) -> None:
        """保存配置值。"""

        self.value = value


class FakeModelScopeConfig:
    """提供 ModelScope 请求构造所需的最小配置快照。"""

    use_default_deepseek_api = ConfigValue(False)
    enable_custom_llm_api_provider = ConfigValue(False)
    llm_api_provider = ConfigValue("modelscope")
    llm_api_model = ConfigValue({"modelscope": "deepseek-ai/DeepSeek-V4-Pro"})
    llm_api_key = ConfigValue({"modelscope": "test-key"})
    llm_api_base_url = ConfigValue(
        {"modelscope": "https://api-inference.modelscope.cn/v1"}
    )
    llm_temperature = ConfigValue(1.0)
    llm_top_p = ConfigValue(1.0)


def build_rate_limit_error(headers: dict[str, str]) -> litellm.exceptions.RateLimitError:
    """构造带指定响应头的 LiteLLM 429 异常。"""

    response = httpx.Response(
        status_code=429,
        headers=headers,
        request=httpx.Request("POST", "https://api-inference.modelscope.cn/v1/chat/completions"),
    )
    return litellm.exceptions.RateLimitError(
        message="rate limited",
        llm_provider="openai",
        model="deepseek-ai/DeepSeek-V4-Pro",
        response=response,
    )


class ModelScopeRateLimitTestCase(unittest.TestCase):
    """验证 ModelScope 额度解析和条件重试策略。"""

    def test_user_quota_exhaustion_takes_precedence(self) -> None:
        """用户总额度和模型额度同时为零时应优先报告用户额度。"""

        quota = parse_quota_headers(
            {
                "ModelScope-RateLimit-Requests-Limit": "2000",
                "ModelScope-RateLimit-Requests-Remaining": "0",
                "ModelScope-RateLimit-Model-Requests-Limit": "50",
                "ModelScope-RateLimit-Model-Requests-Remaining": "0",
            }
        )

        self.assertEqual(quota.user_limit, 2000)
        self.assertEqual(quota.model_limit, 50)
        self.assertEqual(
            classify_rate_limit(quota),
            ModelScopeRateLimitKind.USER_QUOTA_EXHAUSTED,
        )

    def test_model_quota_exhaustion_is_detected(self) -> None:
        """用户仍有额度但当前模型为零时应报告单模型额度。"""

        quota = parse_quota_headers(
            {
                "modelscope-ratelimit-requests-remaining": "1999",
                "modelscope-ratelimit-model-requests-limit": "50",
                "modelscope-ratelimit-model-requests-remaining": "0",
            }
        )

        self.assertEqual(
            classify_rate_limit(quota),
            ModelScopeRateLimitKind.MODEL_QUOTA_EXHAUSTED,
        )

    def test_missing_or_invalid_quota_headers_are_temporary(self) -> None:
        """缺失或非法额度头不应被误判为每日额度耗尽。"""

        quota = parse_quota_headers(
            {
                "modelscope-ratelimit-requests-remaining": "unknown",
                "modelscope-ratelimit-model-requests-remaining": "-1",
            }
        )

        self.assertIsNone(quota.user_remaining)
        self.assertIsNone(quota.model_remaining)
        self.assertEqual(
            classify_rate_limit(quota),
            ModelScopeRateLimitKind.TEMPORARY_RATE_LIMIT,
        )

    def test_retry_after_supports_seconds_and_http_date(self) -> None:
        """Retry-After 应同时支持秒数和 HTTP 日期。"""

        now = datetime(2026, 6, 13, 8, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(parse_retry_after_seconds({"Retry-After": "1.5"}), 1.5)
        self.assertEqual(
            parse_retry_after_seconds(
                {"Retry-After": "Sat, 13 Jun 2026 08:00:05 GMT"},
                now=now,
            ),
            5.0,
        )

    def test_temporary_rate_limit_retries_without_litellm_retries(self) -> None:
        """临时 429 应关闭 LiteLLM 重试并由策略层退避后重试。"""

        rate_limit_error = build_rate_limit_error(
            {
                "modelscope-ratelimit-requests-remaining": "10",
                "modelscope-ratelimit-model-requests-remaining": "5",
                "retry-after": "0",
            }
        )
        response = {"choices": [{"message": {"content": "ok"}}]}

        with (
            mock.patch.object(
                dp_local2,
                "completion",
                side_effect=[rate_limit_error, response],
            ) as mocked_completion,
            mock.patch.object(dp_local2.time, "sleep") as mocked_sleep,
        ):
            result = dp_local2.DSLocalAndVoiceGen._completion_with_modelscope_rate_limit_policy(
                {
                    "model": "openai/deepseek-ai/DeepSeek-V4-Pro",
                    "messages": [{"role": "user", "content": "hi"}],
                }
            )

        self.assertIs(result, response)
        self.assertEqual(mocked_completion.call_count, 2)
        self.assertEqual(mocked_completion.call_args_list[0].kwargs["max_retries"], 0)
        self.assertEqual(mocked_completion.call_args_list[1].kwargs["max_retries"], 0)
        mocked_sleep.assert_called_once_with(0.0)

    def test_quota_exhaustion_does_not_retry(self) -> None:
        """每日额度耗尽时应立即抛出结构化异常而不重试。"""

        rate_limit_error = build_rate_limit_error(
            {
                "modelscope-ratelimit-requests-limit": "2000",
                "modelscope-ratelimit-requests-remaining": "120",
                "modelscope-ratelimit-model-requests-limit": "50",
                "modelscope-ratelimit-model-requests-remaining": "0",
            }
        )

        with (
            mock.patch.object(
                dp_local2,
                "completion",
                side_effect=rate_limit_error,
            ) as mocked_completion,
            mock.patch.object(dp_local2.time, "sleep") as mocked_sleep,
            self.assertRaises(ModelScopeQuotaExceededError) as raised,
        ):
            dp_local2.DSLocalAndVoiceGen._completion_with_modelscope_rate_limit_policy(
                {
                    "model": "openai/deepseek-ai/DeepSeek-V4-Pro",
                    "messages": [{"role": "user", "content": "hi"}],
                }
            )

        self.assertEqual(mocked_completion.call_count, 1)
        mocked_sleep.assert_not_called()
        self.assertEqual(
            raised.exception.kind,
            ModelScopeRateLimitKind.MODEL_QUOTA_EXHAUSTED,
        )
        self.assertEqual(
            extract_error_headers(raised.exception.original_error)[
                "modelscope-ratelimit-model-requests-limit"
            ],
            "50",
        )
        self.assertIn(
            "今日限额 50 次",
            dp_local2.DSLocalAndVoiceGen._modelscope_quota_exhausted_message(
                raised.exception
            ),
        )

    def test_temporary_rate_limit_stops_after_two_retries(self) -> None:
        """连续临时 429 最多尝试三次并抛出最后一个异常。"""

        rate_limit_errors = [
            build_rate_limit_error(
                {
                    "modelscope-ratelimit-requests-remaining": "10",
                    "modelscope-ratelimit-model-requests-remaining": "5",
                }
            )
            for _ in range(3)
        ]

        with (
            mock.patch.object(
                dp_local2,
                "completion",
                side_effect=rate_limit_errors,
            ) as mocked_completion,
            mock.patch.object(dp_local2.time, "sleep") as mocked_sleep,
            self.assertRaises(litellm.exceptions.RateLimitError) as raised,
        ):
            dp_local2.DSLocalAndVoiceGen._completion_with_modelscope_rate_limit_policy(
                {
                    "model": "openai/deepseek-ai/DeepSeek-V4-Pro",
                    "messages": [{"role": "user", "content": "hi"}],
                }
            )

        self.assertIs(raised.exception, rate_limit_errors[-1])
        self.assertEqual(mocked_completion.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in mocked_sleep.call_args_list],
            [1.0, 2.0],
        )

    def test_non_modelscope_provider_keeps_existing_completion_behavior(self) -> None:
        """非 ModelScope provider 不应被强制写入 max_retries。"""

        response = {"choices": [{"message": {"content": "ok"}}]}
        completion_kwargs: dict[str, object] = {
            "model": "deepseek/deepseek-v4-flash",
            "messages": [{"role": "user", "content": "hi"}],
        }

        with mock.patch.object(
            dp_local2,
            "completion",
            return_value=response,
        ) as mocked_completion:
            result = dp_local2.DSLocalAndVoiceGen._execute_completion_with_provider_policy(
                "deepseek",
                completion_kwargs,
            )

        self.assertIs(result, response)
        self.assertNotIn("max_retries", mocked_completion.call_args.kwargs)

    def test_current_config_routes_modelscope_to_provider_policy(self) -> None:
        """预定义 ModelScope 配置应把明确 provider id 交给策略分发器。"""

        subject = dp_local2.DSLocalAndVoiceGen.__new__(dp_local2.DSLocalAndVoiceGen)
        subject.d_sakiko_config = FakeModelScopeConfig()
        response = {"choices": [{"message": {"content": "ok"}}]}

        with mock.patch.object(
            subject,
            "_execute_completion_with_provider_policy",
            return_value=response,
        ) as mocked_execute:
            result = subject._completion_with_current_config(
                model="tool-runtime",
                messages=[{"role": "user", "content": "hi"}],
                _reasoning_snapshot_locked=True,
            )

        self.assertIs(result, response)
        self.assertEqual(mocked_execute.call_args.args[0], "modelscope")
        request_kwargs = mocked_execute.call_args.args[1]
        self.assertEqual(
            request_kwargs["model"],
            "openai/deepseek-ai/DeepSeek-V4-Pro",
        )
        self.assertEqual(
            request_kwargs["base_url"],
            "https://api-inference.modelscope.cn/v1",
        )


if __name__ == "__main__":
    unittest.main()
