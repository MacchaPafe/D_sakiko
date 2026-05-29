from __future__ import annotations

import json
import os
import sys
import unittest
from collections.abc import Callable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from chat.chat import Chat, Message, StaticPromptGenerator
from chat.tool_calling import ToolCallingAgentRuntime
from chat.tool_calling import ToolRegistry
from chat_flow.audio_scheduler import PENDING_AUDIO
from chat_flow.tool_context import ToolSideEffectPorts
from chat_flow.tool_context import ToolTurnContext
from chat_flow.turn_runner import ChatAssistantSegmentCommitted
from chat_flow.turn_runner import ChatGenerationSession
from chat_flow.turn_runner import ChatToolCallRecordsUpdated
from chat_flow.turn_runner import ChatTurnCancelled
from chat_flow.turn_runner import ChatTurnEvent
from chat_flow.turn_runner import ChatTurnFailed
from chat_flow.turn_runner import ChatTurnCompleted
from chat_flow.turn_runner import ChatTurnSettings
from chat_flow.turn_runner import ChatUserMessageCommitted
from chat_flow.turn_runner import CancellationToken
from emotion_enum import EmotionEnum


class AlwaysForegroundResolver:
    """测试用前台解析器。"""

    def is_foreground_chat(self, chat_id: str) -> bool:
        """所有 chat 都视为前台。"""
        return True


class FakeCompletion:
    """记录请求并返回固定模型内容。"""

    def __init__(self, content: str) -> None:
        """保存测试返回内容。"""
        self.content = content
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> dict[str, object]:
        """模拟 OpenAI/LiteLLM 风格补全响应。"""
        self.calls.append(dict(kwargs))
        return {"choices": [{"message": {"content": self.content}}]}


class SequencedCompletion:
    """按顺序返回多次模型内容。"""

    def __init__(self, contents: list[str]) -> None:
        """保存测试返回内容序列。"""
        self._contents = list(contents)
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> dict[str, object]:
        """模拟多次补全响应。"""
        self.calls.append(dict(kwargs))
        index = len(self.calls) - 1
        if index >= len(self._contents):
            raise RuntimeError("unexpected completion call")
        return {"choices": [{"message": {"content": self._contents[index]}}]}


class CancellingCompletion:
    """返回内容前取消当前 token。"""

    def __init__(self, token: CancellationToken) -> None:
        """保存待取消 token。"""
        self._token = token

    def __call__(self, **kwargs: object) -> dict[str, object]:
        """模拟阻塞 LLM 返回前收到了取消请求。"""
        self._token.cancel()
        return {"choices": [{"message": {"content": '[{"text":"不应写入。","emotion":"happiness"}]'}}]}


class FailingCompletion:
    """模拟补全调用失败。"""

    def __call__(self, **kwargs: object) -> dict[str, object]:
        """抛出测试异常。"""
        raise RuntimeError("LLM failed")


class TurnRunnerTestCase(unittest.TestCase):
    """验证 one-shot 对话轮次运行器。"""

    def test_one_shot_foreground_turn_commits_messages_and_events(self) -> None:
        """一轮前台文本生成应写入指定 chat，并返回稳定消息索引事件。"""
        completion = FakeCompletion(
            '[{"text":"こんにちは。","translation":"你好。","emotion":"happiness"}]'
        )
        session = ChatGenerationSession(completion=completion, model_name="fake-model")
        chat = Chat(
            prompt_generator=StaticPromptGenerator("系统设定"),
            message_list=[
                Message("User", "旧消息", "", EmotionEnum.HAPPINESS, ""),
            ],
        )

        events = session.run_foreground_turn(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-1",
            user_text="今天怎么样？",
            character_name="祥子",
            settings=ChatTurnSettings(audio_enabled=False, audio_language_choice="日英混合"),
        )

        self.assertEqual([message.character_name for message in chat.message_list], ["User", "User", "祥子"])
        self.assertEqual(chat.message_list[1].text, "今天怎么样？")
        self.assertEqual(chat.message_list[2].text, "こんにちは。")
        self.assertEqual(chat.message_list[2].translation, "你好。")
        self.assertEqual(chat.message_list[2].audio_path, "NO_AUDIO")
        user_events = self._events_of_type(events, ChatUserMessageCommitted)
        assistant_events = self._events_of_type(events, ChatAssistantSegmentCommitted)
        self.assertEqual(user_events[0].message_index, 1)
        self.assertEqual(assistant_events[0].message_index, 2)
        self.assertIsInstance(events[-1], ChatTurnCompleted)
        self.assertEqual(completion.calls[0]["model"], "fake-model")
        request_messages = completion.calls[0]["messages"]
        self.assertIsInstance(request_messages, list)
        self.assertIn("Machine Output Contract", str(request_messages[0]["content"]))
        self.assertIn("不是 OpenAI messages", str(request_messages[0]["content"]))
        self.assertIn("<runtime_controls>", str(request_messages[-1]["content"]))
        self.assertIn("reply_language: ja_with_zh_translation", str(request_messages[-1]["content"]))

    def test_audio_enabled_assistant_segment_is_committed_with_pending_audio(self) -> None:
        """需要语音的 assistant 片段应先写入 PENDING_AUDIO 哨兵。"""
        completion = FakeCompletion(
            '[{"text":"こんにちは。","translation":"你好。","emotion":"happiness"}]'
        )
        session = ChatGenerationSession(completion=completion, model_name="fake-model")
        chat = Chat(prompt_generator=StaticPromptGenerator("系统设定"))

        events = session.run_foreground_turn(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-1",
            user_text="打个招呼",
            character_name="祥子",
            settings=ChatTurnSettings(audio_enabled=True, audio_language_choice="日英混合"),
        )

        self.assertEqual(chat.message_list[1].audio_path, PENDING_AUDIO)
        assistant_events = self._events_of_type(events, ChatAssistantSegmentCommitted)
        self.assertEqual(assistant_events[0].audio_path, PENDING_AUDIO)

    def test_one_shot_foreground_turn_normalizes_invalid_final_json_once(self) -> None:
        """普通模型回复不是最终 JSON 时，应重试收口为合法段落后提交。"""
        completion = SequencedCompletion([
            "こんにちは，今天先这样说。",
            '[{"text":"こんにちは。","translation":"你好。","emotion":"happiness"}]',
        ])
        session = ChatGenerationSession(completion=completion, model_name="fake-model")
        chat = Chat(prompt_generator=StaticPromptGenerator("系统设定"))

        events = session.run_foreground_turn(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-1",
            user_text="打个招呼",
            character_name="祥子",
            settings=ChatTurnSettings(
                audio_enabled=False,
                audio_language_choice="日英混合",
                reasoning_kwargs={"temperature": 0.2},
            ),
        )

        self.assertEqual(len(completion.calls), 2)
        self.assertEqual(chat.message_list[-1].character_name, "祥子")
        self.assertEqual(chat.message_list[-1].text, "こんにちは。")
        self.assertEqual(chat.message_list[-1].translation, "你好。")
        self.assertIsInstance(events[-1], ChatTurnCompleted)
        retry_messages = completion.calls[1]["messages"]
        self.assertIsInstance(retry_messages, list)
        self.assertIn("候选草稿", str(retry_messages[-1]))
        self.assertIn("不是 OpenAI messages", str(retry_messages[-1]))
        self.assertIn("role/content", str(retry_messages[-1]))
        self.assertIn("こんにちは，今天先这样说。", str(retry_messages[-2]))

    def test_one_shot_foreground_turn_repairs_openai_message_array_retry(self) -> None:
        """收口误产出 OpenAI messages 数组时，应再纠正为段落数组。"""
        final_text = "（听到有人打招呼，轻轻抬起头）\n\n啊...你好。"
        completion = SequencedCompletion([
            final_text,
            json.dumps([{"role": "assistant", "content": final_text}], ensure_ascii=False),
            json.dumps([{"text": final_text, "emotion": "happiness"}], ensure_ascii=False),
        ])
        session = ChatGenerationSession(completion=completion, model_name="fake-model")
        chat = Chat(prompt_generator=StaticPromptGenerator("系统设定"))

        events = session.run_foreground_turn(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-1",
            user_text="你好",
            character_name="祥子",
            settings=ChatTurnSettings(audio_enabled=False, audio_language_choice="中英混合"),
        )

        self.assertEqual(len(completion.calls), 3)
        self.assertEqual(chat.message_list[-1].text, final_text)
        self.assertEqual(chat.message_list[-1].emotion, EmotionEnum.HAPPINESS)
        self.assertIsInstance(events[-1], ChatTurnCompleted)
        repair_messages = completion.calls[2]["messages"]
        self.assertIsInstance(repair_messages, list)
        self.assertIn("校验错误", str(repair_messages[-1]))
        self.assertIn("role/content", str(repair_messages[-1]))

    def test_cancel_after_user_commit_discards_uncommitted_assistant_output(self) -> None:
        """取消发生在 LLM 返回前后时，应保留用户消息但不写入未提交 assistant。"""
        token = CancellationToken()
        session = ChatGenerationSession(completion=CancellingCompletion(token), model_name="fake-model")
        chat = Chat()

        events = session.run_foreground_turn(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-1",
            user_text="停下",
            character_name="祥子",
            settings=ChatTurnSettings(
                audio_enabled=False,
                audio_language_choice="中英混合",
                cancellation_token=token,
            ),
        )

        self.assertEqual([message.character_name for message in chat.message_list], ["User"])
        self.assertEqual(chat.message_list[0].text, "停下")
        self.assertIsInstance(events[-1], ChatTurnCancelled)

    def test_turn_failure_is_returned_as_event_without_direct_ui_mutation(self) -> None:
        """补全失败应转换为失败事件，而不是从 runner 直接改 UI。"""
        session = ChatGenerationSession(completion=FailingCompletion(), model_name="fake-model")
        chat = Chat()

        events = session.run_foreground_turn(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-1",
            user_text="你好",
            character_name="祥子",
            settings=ChatTurnSettings(audio_enabled=False, audio_language_choice="中英混合"),
        )

        self.assertEqual([message.character_name for message in chat.message_list], ["User"])
        self.assertIsInstance(events[-1], ChatTurnFailed)
        self.assertIn("LLM failed", events[-1].message)

    def test_contextual_tool_runtime_persists_records_and_final_response(self) -> None:
        """新 turn runner 路径应绑定工具上下文并持久化工具调用记录。"""
        registry = ToolRegistry()

        def create_handler(context: ToolTurnContext) -> Callable[[dict[str, object]], dict[str, object]]:
            """创建带显式上下文的测试工具处理器。"""

            def handler(arguments: dict[str, object]) -> dict[str, object]:
                """返回工具执行时看到的上下文。"""
                return {
                    "ok": True,
                    "chat_id": context.chat_id,
                    "turn_id": context.turn_id,
                    "query": arguments.get("query"),
                }

            return handler

        registry.register_contextual_tool(
            name="context_lookup",
            description="Lookup with explicit context.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            execution_scope="foreground_and_background",
            handler_factory=create_handler,
        )
        responses = [
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "我查一下。",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "context_lookup",
                                        "arguments": "{\"query\":\"needle\"}",
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "[{\"text\":\"找到了。\",\"emotion\":\"happiness\"}]",
                        },
                    }
                ],
            },
        ]

        def completion(**kwargs: object) -> dict[str, object]:
            return responses.pop(0)

        runtime = ToolCallingAgentRuntime(
            llm_completion=completion,
            tool_registry=registry,
            debug=False,
        )
        session = ChatGenerationSession(
            completion=completion,
            model_name="fake-model",
            tool_runtime=runtime,
            foreground_resolver=AlwaysForegroundResolver(),
            side_effect_ports=ToolSideEffectPorts(),
        )
        chat = Chat()

        events = session.run_foreground_turn(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-42",
            user_text="查一下",
            character_name="祥子",
            settings=ChatTurnSettings(audio_enabled=False, audio_language_choice="中英混合"),
        )

        self.assertEqual([message.text for message in chat.message_list], ["查一下", "我查一下。", "找到了。"])
        self.assertEqual(chat.message_list[1].audio_path, "NO_AUDIO")
        record = chat.get_tool_call_records()[0]
        self.assertEqual(record.tool_call_id, "call-1")
        self.assertEqual(record.message_index, 1)
        self.assertEqual(record.status, "completed")
        self.assertTrue(any(isinstance(event, ChatToolCallRecordsUpdated) for event in events))

    @staticmethod
    def _events_of_type(
        events: list[ChatTurnEvent],
        event_type: type[ChatUserMessageCommitted] | type[ChatAssistantSegmentCommitted],
    ) -> list[ChatUserMessageCommitted] | list[ChatAssistantSegmentCommitted]:
        """按事件类型筛选事件。"""
        return [event for event in events if isinstance(event, event_type)]


if __name__ == "__main__":
    unittest.main()
