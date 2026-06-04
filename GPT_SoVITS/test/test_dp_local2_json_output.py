from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import dp_local2


class FakeChat:
    """用于测试 LLM 请求构造的最小对话对象。"""

    chat_id = "chat-1"

    def build_llm_query(self, perspective: str, is_simplify: bool = False) -> list[dict[str, object]]:
        """返回一段包含 system 和 user 的固定请求历史。"""
        return [
            {"role": "system", "content": f"角色设定：{perspective}"},
            {"role": "user", "content": "[User]: 你好呀"},
        ]


class FakeChatManager:
    """用于测试 current_chat 属性的最小 ChatManager。"""

    def __init__(self, chat: FakeChat) -> None:
        """保存测试对话对象。"""
        self.chat = chat

    def get_chat_by_id(self, chat_id: str) -> FakeChat:
        """返回固定测试对话。"""
        return self.chat


class DpLocal2JsonOutputTestCase(unittest.TestCase):
    """验证 dp_local2 的 JSON 输出协议与请求构造。"""

    def _build_subject(self, language: str = "日英混合") -> dp_local2.DSLocalAndVoiceGen:
        """构造跳过 __init__ 的测试对象。"""
        subject = dp_local2.DSLocalAndVoiceGen.__new__(dp_local2.DSLocalAndVoiceGen)
        subject.audio_language_choice = language
        subject.if_sakiko = False
        subject.sakiko_state = True
        subject.restr = "[重要原则]保持角色边界。"
        subject.if_generate_audio = True
        subject.current_chat_id = "chat-1"
        subject.chat_manager = FakeChatManager(FakeChat())
        return subject

    def test_build_messages_appends_runtime_controls_as_separate_message(self) -> None:
        """请求构造应追加独立 runtime controls，而不是替换最后一条 user。"""
        subject = self._build_subject()

        messages = subject._build_llm_messages_for_chat_turn("祥子")

        self.assertEqual(messages[1]["content"], "[User]: 你好呀")
        self.assertEqual(messages[-1]["role"], "user")
        self.assertIn("<runtime_controls>", str(messages[-1]["content"]))
        self.assertIn("reply_language: ja_with_zh_translation", str(messages[-1]["content"]))
        self.assertIn("JSON array", str(messages[0]["content"]))
        self.assertIn("[重要原则]保持角色边界。", str(messages[0]["content"]))

    def test_parse_japanese_segments_requires_translation(self) -> None:
        """日文模式下每个 JSON 段落都必须带 translation。"""
        subject = self._build_subject("日英混合")

        segments = subject._parse_model_segments_payload(
            '[{"text":"こんにちは。","translation":"你好。","emotion":"happiness"}]'
        )

        self.assertEqual(
            segments,
            [{"text": "こんにちは。", "emotion": "happiness", "translation": "你好。"}],
        )

        with self.assertRaises(ValueError):
            subject._parse_model_segments_payload('[{"text":"こんにちは。","emotion":"happiness"}]')

    def test_parse_accepts_top_level_object_as_single_segment(self) -> None:
        """兼容旧格式：顶层单个 JSON object 会被视为单段回复。"""
        subject = self._build_subject("中英混合")

        segments = subject._parse_model_segments_payload('{"text":"你好。","emotion":"happiness"}')

        self.assertEqual(segments, [{"text": "你好。", "emotion": "happiness"}])

    def test_parse_rejects_neutral_emotion(self) -> None:
        """EmotionEnum 没有 neutral，机器协议也不应接受 neutral。"""
        subject = self._build_subject("中英混合")

        with self.assertRaises(ValueError):
            subject._parse_model_segments_payload('[{"text":"你好。","emotion":"neutral"}]')

    def test_parse_lenient_mode_recovers_minor_schema_errors(self) -> None:
        """宽松模式应兜底可恢复字段，但仍返回标准段落结构。"""
        subject = self._build_subject("日英混合")

        segments = subject._parse_model_segments_payload(
            '[{"text":"こんにちは。","emotion":"neutral","extra":"ignored"}]',
            strict_mode=False,
        )

        self.assertEqual(
            segments,
            [{"text": "こんにちは。", "emotion": "happiness", "translation": ""}],
        )

    def test_parse_lenient_mode_still_requires_text(self) -> None:
        """宽松模式仍必须保留可显示的 text。"""
        subject = self._build_subject("中英混合")

        with self.assertRaises(ValueError):
            subject._parse_model_segments_payload(
                '[{"translation":"你好。","emotion":"happiness"}]',
                strict_mode=False,
            )

    def test_parse_chinese_segments_ignores_translation(self) -> None:
        """中文模式下 translation 字段不会进入标准段落。"""
        subject = self._build_subject("中英混合")

        segments = subject._parse_model_segments_payload(
            '[{"text":"你好。","translation":"hello","emotion":"happiness"}]'
        )

        self.assertEqual(segments, [{"text": "你好。", "emotion": "happiness"}])

    def test_empty_json_completion_retries_once(self) -> None:
        """JSON Mode 返回空 content 时应静默重试一次。"""
        subject = self._build_subject()
        calls: list[dict[str, object]] = []

        def fake_completion(**kwargs: object) -> dict[str, object]:
            """第一次返回空内容，第二次返回合法 JSON。"""
            calls.append(dict(kwargs))
            content = "" if len(calls) == 1 else '[{"text":"你好。","translation":"你好。","emotion":"happiness"}]'
            return {"choices": [{"message": {"content": content}}]}

        subject._completion_with_current_config = fake_completion
        subject._supports_json_object_response_format_for_current_config = lambda: True

        content, used_json_mode = subject._run_json_completion_with_empty_retry(
            messages=[{"role": "user", "content": "go"}],
            reasoning_kwargs={},
        )

        self.assertEqual(len(calls), 2)
        self.assertIn("response_format", calls[0])
        self.assertTrue(used_json_mode)
        self.assertEqual(content, '[{"text":"你好。","translation":"你好。","emotion":"happiness"}]')

    def test_json_completion_skips_json_mode_when_model_does_not_support_it(self) -> None:
        """LiteLLM 参数表不支持 response_format 时，应直接走普通补全。"""
        subject = self._build_subject()
        calls: list[dict[str, object]] = []

        def fake_completion(**kwargs: object) -> dict[str, object]:
            """记录请求并返回合法 JSON。"""
            calls.append(dict(kwargs))
            return {
                "choices": [
                    {
                        "message": {
                            "content": '[{"text":"你好。","translation":"你好。","emotion":"happiness"}]'
                        }
                    }
                ]
            }

        subject._completion_with_current_config = fake_completion
        subject._supports_json_object_response_format_for_current_config = lambda: False

        content, used_json_mode = subject._run_json_completion_with_empty_retry(
            messages=[{"role": "user", "content": "go"}],
            reasoning_kwargs={},
        )

        self.assertEqual(len(calls), 1)
        self.assertNotIn("response_format", calls[0])
        self.assertFalse(used_json_mode)
        self.assertEqual(content, '[{"text":"你好。","translation":"你好。","emotion":"happiness"}]')

    def test_final_json_completion_reuses_valid_candidate(self) -> None:
        """候选回复已经符合协议时，应直接复用而不再请求模型收口。"""
        subject = self._build_subject()
        calls: list[dict[str, object]] = []

        def fake_completion(**kwargs: object) -> dict[str, object]:
            """记录意外的最终 JSON 请求。"""
            calls.append(dict(kwargs))
            return {
                "choices": [
                    {
                        "message": {
                            "content": '[{"text":"こんにちは。","translation":"你好。","emotion":"happiness"}]'
                        }
                    }
                ]
            }

        subject._completion_with_current_config = fake_completion
        subject._supports_json_object_response_format_for_current_config = lambda: True

        segments = subject._run_final_json_completion(
            runtime_messages=[{"role": "user", "content": "go"}],
            candidate_content='[{"text":"旧回复。","translation":"旧回复。","emotion":"happiness"}]',
            reasoning_kwargs={"_reasoning_snapshot_locked": True},
        )

        self.assertEqual(calls, [])
        self.assertEqual(
            segments,
            [{"text": "旧回复。", "emotion": "happiness", "translation": "旧回复。"}],
        )

    def test_schema_error_uses_format_retry(self) -> None:
        """非空 JSON 的 schema 错误应触发一次格式纠正 retry。"""
        subject = self._build_subject()
        retry_inputs: list[str] = []

        def fake_retry(
            invalid_content: str,
            reason: str,
            reasoning_kwargs: dict[str, object],
            strict_mode: bool = True,
        ) -> list[dict[str, str]]:
            """记录 retry 输入并返回修正后的段落。"""
            retry_inputs.append(invalid_content)
            self.assertIn("translation", reason)
            self.assertEqual(reasoning_kwargs, {"_reasoning_snapshot_locked": True})
            self.assertFalse(strict_mode)
            return [{"text": "こんにちは。", "emotion": "happiness", "translation": "你好。"}]

        subject._retry_model_segments_format = fake_retry
        subject._run_json_completion_with_empty_retry = (
            lambda messages, reasoning_kwargs: ('[{"text":"こんにちは。","emotion":"happiness"}]', True)
        )

        segments = subject._run_final_json_completion(
            runtime_messages=[{"role": "user", "content": "go"}],
            candidate_content="候选回复",
            reasoning_kwargs={"_reasoning_snapshot_locked": True},
        )

        self.assertEqual(retry_inputs, ['[{"text":"こんにちは。","emotion":"happiness"}]'])
        self.assertEqual(
            segments,
            [{"text": "こんにちは。", "emotion": "happiness", "translation": "你好。"}],
        )


if __name__ == "__main__":
    unittest.main()
