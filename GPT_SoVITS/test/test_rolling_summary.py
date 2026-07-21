from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import dp_local2
from chat.chat import Chat, ChatManager, Message, MessageAttachment, StaticPromptGenerator
from chat.chat_meta import ToolCallRecordMeta
from chat.rolling_summary import (
    ROLLING_SUMMARY_META_KEY,
    build_llm_query_with_rolling_summary,
    build_rolling_summary_update,
    clear_rolling_summary,
    context_reaches_summary_threshold,
    find_recent_window_start,
    get_rolling_summary,
    invalidate_rolling_summary_from_message_index,
    rolling_summary_token_budget,
    rolling_summary_validation_error,
    set_rolling_summary,
    trim_messages_for_emergency,
)
from emotion_enum import EmotionEnum
from qconfig import DSakikoConfig


class RollingSummaryTestCase(unittest.TestCase):
    @staticmethod
    def _message(character_name: str, text: str) -> Message:
        return Message(
            character_name=character_name,
            text=text,
            translation="",
            emotion=EmotionEnum.HAPPINESS,
            audio_path="",
        )

    def _chat_with_turns(self, turn_count: int) -> Chat:
        messages: list[Message] = []
        for index in range(turn_count):
            messages.append(self._message("User", f"user-{index}"))
            messages.append(self._message("角色", f"assistant-{index}"))
        return Chat(
            prompt_generator=StaticPromptGenerator("system-prompt"),
            message_list=messages,
        )

    def test_threshold_uses_configurable_ratio_with_eighty_percent_default(self) -> None:
        self.assertFalse(context_reaches_summary_threshold(799, 1000))
        self.assertTrue(context_reaches_summary_threshold(800, 1000))
        self.assertFalse(context_reaches_summary_threshold(800, None))
        self.assertFalse(context_reaches_summary_threshold(749, 1000, 0.75))
        self.assertTrue(context_reaches_summary_threshold(750, 1000, 0.75))

    def test_threshold_config_is_limited_to_seventy_through_ninety_percent(self) -> None:
        item = DSakikoConfig.rolling_summary_trigger_ratio

        self.assertEqual(item.defaultValue, 0.80)
        self.assertEqual(item.range, (0.70, 0.90))
        self.assertEqual(item.validator.correct(0.20), 0.70)
        self.assertEqual(item.validator.correct(0.95), 0.90)

    def test_summary_budget_is_ten_percent_without_a_fixed_upper_cap(self) -> None:
        self.assertEqual(rolling_summary_token_budget(1000), 100)
        self.assertEqual(rolling_summary_token_budget(128_000), 12_800)
        self.assertEqual(rolling_summary_token_budget(1_000_000), 100_000)
        with self.assertRaises(ValueError):
            rolling_summary_token_budget(0)

    def test_dp_updates_summary_at_threshold_and_saves_it(self) -> None:
        chat = self._chat_with_turns(12)
        subject = dp_local2.DSLocalAndVoiceGen.__new__(dp_local2.DSLocalAndVoiceGen)
        subject.chat_manager = mock.Mock()
        completion_response = {
            "choices": [{"message": {"content": "模型生成的累计摘要"}}],
        }
        event_queue = mock.Mock()

        with (
            mock.patch.object(subject, "_current_litellm_model_name", return_value="test/model"),
            mock.patch.object(
                subject,
                "_completion_with_current_config",
                return_value=completion_response,
            ) as completion_mock,
            mock.patch.object(dp_local2, "get_model_input_token_limit", return_value=1000),
            mock.patch.object(dp_local2, "count_message_tokens", return_value=800),
        ):
            updated = subject._maybe_update_rolling_summary(
                chat,
                "角色",
                [{"role": "user", "content": "current"}],
                dp2qt_queue=event_queue,
                turn_id="turn-1",
            )

        self.assertTrue(updated)
        state = get_rolling_summary(chat)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.text, "模型生成的累计摘要")
        self.assertEqual(state.summary_until_message_index, 3)
        subject.chat_manager.save.assert_called_once_with()
        completion_mock.assert_called_once()
        self.assertEqual(
            completion_mock.call_args.kwargs["max_tokens"],
            100,
        )
        event_queue.put.assert_called_once_with({
            "type": "context_compaction_started",
            "chat_id": chat.chat_id,
            "turn_id": "turn-1",
            "message": "正在整理过往思绪...",
        })

    def test_dp_skips_summary_below_threshold(self) -> None:
        chat = self._chat_with_turns(12)
        subject = dp_local2.DSLocalAndVoiceGen.__new__(dp_local2.DSLocalAndVoiceGen)
        subject.chat_manager = mock.Mock()

        with (
            mock.patch.object(subject, "_current_litellm_model_name", return_value="test/model"),
            mock.patch.object(subject, "_completion_with_current_config") as completion_mock,
            mock.patch.object(dp_local2, "get_model_input_token_limit", return_value=1000),
            mock.patch.object(dp_local2, "count_message_tokens", return_value=799),
        ):
            updated = subject._maybe_update_rolling_summary(
                chat,
                "角色",
                [{"role": "user", "content": "current"}],
            )

        self.assertFalse(updated)
        self.assertIsNone(get_rolling_summary(chat))
        completion_mock.assert_not_called()

    def test_dp_uses_cooldown_after_summary_failure(self) -> None:
        chat = self._chat_with_turns(12)
        subject = dp_local2.DSLocalAndVoiceGen.__new__(dp_local2.DSLocalAndVoiceGen)
        subject.chat_manager = mock.Mock()

        with (
            mock.patch.object(subject, "_current_litellm_model_name", return_value="test/model"),
            mock.patch.object(
                subject,
                "_completion_with_current_config",
                side_effect=TimeoutError("summary timeout"),
            ) as completion_mock,
            mock.patch.object(dp_local2, "get_model_input_token_limit", return_value=1000),
            mock.patch.object(dp_local2, "count_message_tokens", return_value=800),
        ):
            first = subject._maybe_update_rolling_summary(
                chat,
                "角色",
                [{"role": "user", "content": "current"}],
            )
            second = subject._maybe_update_rolling_summary(
                chat,
                "角色",
                [{"role": "user", "content": "current"}],
            )

        self.assertFalse(first)
        self.assertFalse(second)
        completion_mock.assert_called_once()

    def test_dp_reads_summary_threshold_from_turn_config_snapshot(self) -> None:
        chat = self._chat_with_turns(12)
        subject = dp_local2.DSLocalAndVoiceGen.__new__(dp_local2.DSLocalAndVoiceGen)
        subject.chat_manager = mock.Mock()
        subject.d_sakiko_config = SimpleNamespace(
            rolling_summary_trigger_ratio=SimpleNamespace(value=0.75),
        )

        with (
            mock.patch.object(subject, "_current_litellm_model_name", return_value="test/model"),
            mock.patch.object(subject, "_completion_with_current_config") as completion_mock,
            mock.patch.object(dp_local2, "get_model_input_token_limit", return_value=1000),
            mock.patch.object(dp_local2, "count_message_tokens", return_value=749),
        ):
            updated = subject._maybe_update_rolling_summary(
                chat,
                "角色",
                [{"role": "user", "content": "current"}],
            )

        self.assertFalse(updated)
        completion_mock.assert_not_called()

    def test_summary_validation_rejects_short_and_error_results_without_length_cap(self) -> None:
        self.assertIsNotNone(rolling_summary_validation_error("无"))
        self.assertIsNotNone(rolling_summary_validation_error("Internal Server Error"))
        self.assertIsNone(rolling_summary_validation_error("摘" * 12001))
        self.assertIsNone(rolling_summary_validation_error("用户希望以后继续讨论 Live2D 性能优化。"))

    def test_dp_rejects_invalid_summary_without_advancing_boundary(self) -> None:
        chat = self._chat_with_turns(12)
        subject = dp_local2.DSLocalAndVoiceGen.__new__(dp_local2.DSLocalAndVoiceGen)
        subject.chat_manager = mock.Mock()
        response = {"choices": [{"message": {"content": "Internal Server Error"}}]}

        with (
            mock.patch.object(subject, "_current_litellm_model_name", return_value="test/model"),
            mock.patch.object(subject, "_completion_with_current_config", return_value=response),
            mock.patch.object(dp_local2, "get_model_input_token_limit", return_value=1000),
            mock.patch.object(dp_local2, "count_message_tokens", return_value=800),
        ):
            updated = subject._maybe_update_rolling_summary(
                chat,
                "角色",
                [{"role": "user", "content": "current"}],
            )

        self.assertFalse(updated)
        self.assertIsNone(get_rolling_summary(chat))
        subject.chat_manager.save.assert_not_called()

    def test_recent_window_counts_real_user_turns(self) -> None:
        chat = self._chat_with_turns(10)
        chat.message_list.insert(
            2,
            self._message("User", "【系统内部事件触发：提醒】"),
        )
        self.assertIsNone(find_recent_window_start(chat))

        chat.message_list.extend([
            self._message("User", "user-10"),
            self._message("角色", "assistant-10"),
        ])
        self.assertEqual(find_recent_window_start(chat), 3)

    def test_summary_update_keeps_latest_ten_real_user_turns(self) -> None:
        chat = self._chat_with_turns(12)

        update = build_rolling_summary_update(chat, perspective="角色")

        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual(update.summary_until_message_index, 3)
        request_text = repr(update.messages)
        self.assertIn("user-0", request_text)
        self.assertIn("assistant-1", request_text)
        self.assertNotIn("user-2", request_text)

    def test_summary_update_only_sends_messages_after_old_boundary(self) -> None:
        chat = self._chat_with_turns(12)
        set_rolling_summary(
            chat,
            text="旧摘要",
            summary_until_message_index=1,
            model="test/model",
        )

        update = build_rolling_summary_update(chat, perspective="角色")

        self.assertIsNotNone(update)
        assert update is not None
        request_text = repr(update.messages)
        self.assertIn("旧摘要", request_text)
        self.assertNotIn("user-0", request_text)
        self.assertIn("user-1", request_text)
        self.assertNotIn("user-2", request_text)

    def test_summary_update_includes_persisted_tool_trace(self) -> None:
        chat = self._chat_with_turns(12)
        chat.meta.tool_call_records.append(
            ToolCallRecordMeta(
                tool_call_id="call-1",
                tool_name="get_weather",
                arguments={"city": "上海"},
                message_index=1,
                status="completed",
                result_content="城市: 上海\n温度: 31",
                raw_result_content='{"city":"上海","temperature":31,"humidity":70}',
                ok=True,
            )
        )

        update = build_rolling_summary_update(chat, perspective="角色")

        self.assertIsNotNone(update)
        assert update is not None
        request_text = repr(update.messages)
        self.assertIn("get_weather", request_text)
        self.assertIn("上海", request_text)
        self.assertIn("humidity", request_text)

    def test_summary_replaces_images_with_text_placeholder(self) -> None:
        chat = self._chat_with_turns(12)
        chat.message_list[0].attachments.append(
            MessageAttachment(
                type="image",
                path="chat_attachments/test/image.png",
                mime_type="image/png",
                original_name="image.png",
            )
        )

        update = build_rolling_summary_update(chat, perspective="角色")

        self.assertIsNotNone(update)
        assert update is not None
        request_text = repr(update.messages)
        self.assertIn("用户发送了一张图片", request_text)
        self.assertIn("压缩过程未读取图片内容", request_text)
        self.assertNotIn("image_url", request_text)
        self.assertNotIn("data:image", request_text)

    def test_tool_arguments_survive_serialization(self) -> None:
        chat = self._chat_with_turns(1)
        chat.meta.tool_call_records.append(
            ToolCallRecordMeta(
                tool_call_id="call-1",
                tool_name="web_search",
                arguments={"query": "Live2D"},
                message_index=1,
                raw_result_content='{"items":[]}',
            )
        )

        restored = Chat.from_dict(chat.to_dict())

        self.assertEqual(
            restored.meta.tool_call_records[0].arguments,
            {"query": "Live2D"},
        )
        self.assertEqual(
            restored.meta.tool_call_records[0].raw_result_content,
            '{"items":[]}',
        )

    def test_emergency_trim_preserves_system_summary_and_latest_user(self) -> None:
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "<current_session_summary>summary</current_session_summary>"},
            {"role": "user", "content": "old-" + "x" * 2000},
            {"role": "assistant", "content": "reply-" + "y" * 2000},
            {"role": "user", "content": "latest"},
            {"role": "user", "content": "<runtime_controls>zh_only</runtime_controls>"},
        ]

        with mock.patch(
            "chat.model_token_usage.count_message_tokens",
            side_effect=lambda _model, candidate: sum(
                len(str(message.get("content") or "")) for message in candidate
            ),
        ):
            trimmed = trim_messages_for_emergency(
                messages,
                model="test/model",
                token_limit=1000,
                reserved_recent_user_messages=1,
            )

        request_text = repr(trimmed)
        self.assertIn("system", request_text)
        self.assertIn("current_session_summary", request_text)
        self.assertIn("latest", request_text)
        self.assertIn("runtime_controls", request_text)
        self.assertNotIn("old-", request_text)
        self.assertNotIn("reply-", request_text)

    def test_request_view_keeps_all_uncovered_messages_between_refreshes(self) -> None:
        chat = self._chat_with_turns(12)
        set_rolling_summary(
            chat,
            text="已覆盖第零轮",
            summary_until_message_index=1,
            model="test/model",
        )

        messages = build_llm_query_with_rolling_summary(
            chat,
            perspective="角色",
        )
        request_text = repr(messages)

        self.assertIn("已覆盖第零轮", request_text)
        self.assertNotIn("user-0", request_text)
        self.assertNotIn("assistant-0", request_text)
        self.assertIn("user-1", request_text)
        self.assertIn("assistant-11", request_text)

    def test_summary_state_survives_serialization(self) -> None:
        chat = self._chat_with_turns(12)
        set_rolling_summary(
            chat,
            text="持久化摘要",
            summary_until_message_index=3,
            model="test/model",
        )

        restored = Chat.from_dict(chat.to_dict())
        state = get_rolling_summary(restored)

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.text, "持久化摘要")
        self.assertEqual(state.summary_until_message_index, 3)

    def test_history_changes_only_clear_covered_summary(self) -> None:
        chat = self._chat_with_turns(12)
        set_rolling_summary(
            chat,
            text="摘要",
            summary_until_message_index=3,
            model="test/model",
        )

        self.assertFalse(invalidate_rolling_summary_from_message_index(chat, 4))
        self.assertIsNotNone(get_rolling_summary(chat))
        self.assertTrue(invalidate_rolling_summary_from_message_index(chat, 3))
        self.assertNotIn(ROLLING_SUMMARY_META_KEY, chat.meta.extra)

    def test_delete_and_clear_use_summary_invalidation_rule(self) -> None:
        chat = self._chat_with_turns(12)
        set_rolling_summary(
            chat,
            text="摘要",
            summary_until_message_index=3,
            model="test/model",
        )

        chat.delete_message_range(10, 12)
        self.assertIsNotNone(get_rolling_summary(chat))
        chat.delete_message_range(3, 4)
        self.assertIsNone(get_rolling_summary(chat))

        set_rolling_summary(
            chat,
            text="新摘要",
            summary_until_message_index=1,
            model="test/model",
        )
        chat.clear_message_list()
        self.assertFalse(clear_rolling_summary(chat))

    def test_clone_and_fork_keep_or_clear_summary_by_boundary(self) -> None:
        source = self._chat_with_turns(12)
        set_rolling_summary(
            source,
            text="摘要",
            summary_until_message_index=3,
            model="test/model",
        )
        manager = ChatManager([source])

        clone = manager.clone_chat(source.chat_id, copy_attachments=False)
        fork_at_boundary = manager.clone_chat(
            source.chat_id,
            fork_after_message_index=3,
            copy_attachments=False,
        )
        fork_inside_summary = manager.clone_chat(
            source.chat_id,
            fork_after_message_index=2,
            copy_attachments=False,
        )

        self.assertIsNotNone(get_rolling_summary(clone))
        self.assertIsNotNone(get_rolling_summary(fork_at_boundary))
        self.assertIsNone(get_rolling_summary(fork_inside_summary))


if __name__ == "__main__":
    unittest.main()
