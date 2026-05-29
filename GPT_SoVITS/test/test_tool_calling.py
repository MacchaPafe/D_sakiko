import os
import sys
from types import TracebackType
from typing import Callable
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from chat.chat import Chat
from chat.tool_calling import ToolCallingAgentRuntime, ToolRegistry
from chat.tool_calling import build_default_tool_registry
from chat.tool_calling import register_contextual_live2d_tools
from chat.tool_calling import register_contextual_lottery_tool
from chat.tool_calling import register_contextual_export_document_tool
from chat_flow.tool_context import ChatToolCallRecordSink
from chat_flow.tool_context import NullForegroundResolver
from chat_flow.tool_context import ToolSideEffectPorts
from chat_flow.tool_context import ToolCallSnapshot
from chat_flow.tool_context import ToolTurnContext


class CountingLock:
    """记录测试目标是否通过 runtime lock 写入。"""

    def __init__(self) -> None:
        """初始化进入和退出计数。"""
        self.enter_count = 0
        self.exit_count = 0

    def __enter__(self) -> bool:
        """记录进入锁上下文。"""
        self.enter_count += 1
        return True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """记录退出锁上下文。"""
        self.exit_count += 1


class AlwaysForegroundResolver:
    """测试用前台解析器。"""

    def is_foreground_chat(self, chat_id: str) -> bool:
        """所有 chat 都视为前台。"""
        return True


class RecordingLotteryPort:
    """记录抽签工具通过受控端口发出的请求。"""

    def __init__(self) -> None:
        """初始化记录列表。"""
        self.calls: list[tuple[str, str, str, tuple[str, ...]]] = []

    def show_lottery(self, chat_id: str, turn_id: str, title: str, options: tuple[str, ...]) -> bool:
        """记录抽签请求。"""
        self.calls.append((chat_id, turn_id, title, options))
        return True


class RecordingLive2DPort:
    """记录 Live2D 工具通过受控端口发出的请求。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.list_calls: list[tuple[str, str, str]] = []
        self.change_calls: list[tuple[str, str, str, str]] = []

    def list_models(self, chat_id: str, turn_id: str, character_name: str) -> list[str]:
        """记录列出模型请求。"""
        self.list_calls.append((chat_id, turn_id, character_name))
        return ["默认", "演出服"]

    def change_model(self, chat_id: str, turn_id: str, character_name: str, model_name: str) -> bool:
        """记录模型切换请求。"""
        self.change_calls.append((chat_id, turn_id, character_name, model_name))
        return True


class RecordingExportDocumentPort:
    """记录文档导出工具的写入与打开请求。"""

    def __init__(self) -> None:
        """初始化记录。"""
        self.write_calls: list[tuple[str, str, str, str]] = []
        self.open_calls: list[str] = []

    def write_document(self, chat_id: str, turn_id: str, filename: str, content: str) -> str:
        """记录写文档请求并返回路径。"""
        self.write_calls.append((chat_id, turn_id, filename, content))
        return f"/tmp/{filename}"

    def request_open_file(self, path: str) -> None:
        """记录打开文件请求。"""
        self.open_calls.append(path)


class ToolCallingRuntimeTestCase(unittest.TestCase):
    def _build_registry(self):
        registry = ToolRegistry()
        registry.register_tool(
            name="lookup",
            description="Lookup a value.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            handler=lambda arguments: {"ok": True, "query": arguments.get("query")},
        )
        return registry

    def test_deepseek_reasoning_content_is_returned_after_tool_call(self):
        captured_messages = []

        def completion(**kwargs):
            captured_messages.append(kwargs["messages"])
            if len(captured_messages) == 1:
                return {
                    "model": "deepseek-v4-pro",
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "我查一下。",
                                "reasoning_content": "need lookup",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "lookup",
                                            "arguments": "{\"query\":\"x\"}",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            return {
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "done"},
                    }
                ],
            }

        runtime = ToolCallingAgentRuntime(
            llm_completion=completion,
            tool_registry=self._build_registry(),
            debug=False,
        )
        runtime.run(model="tool-runtime", messages=[{"role": "user", "content": "go"}])

        assistant_message = captured_messages[1][1]
        self.assertEqual(assistant_message["reasoning_content"], "need lookup")

    def test_non_deepseek_reasoning_content_is_not_returned(self):
        captured_messages = []

        def completion(**kwargs):
            captured_messages.append(kwargs["messages"])
            if len(captured_messages) == 1:
                return {
                    "model": "gpt-5.1",
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "checking",
                                "reasoning_content": "private",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "lookup",
                                            "arguments": "{\"query\":\"x\"}",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            return {
                "model": "gpt-5.1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "done"},
                    }
                ],
            }

        runtime = ToolCallingAgentRuntime(
            llm_completion=completion,
            tool_registry=self._build_registry(),
            debug=False,
        )
        runtime.run(model="tool-runtime", messages=[{"role": "user", "content": "go"}])

        assistant_message = captured_messages[1][1]
        self.assertNotIn("reasoning_content", assistant_message)

    def test_contextual_registry_creates_handlers_from_explicit_turn_context(self) -> None:
        captured_context: list[ToolTurnContext] = []
        registry = ToolRegistry()

        def create_handler(context: ToolTurnContext) -> Callable[[dict[str, object]], dict[str, object]]:
            captured_context.append(context)

            def handler(arguments: dict[str, object]) -> dict[str, object]:
                return {
                    "chat_id": context.chat_id,
                    "turn_id": context.turn_id,
                    "character_name": context.character_name,
                    "query": arguments.get("query"),
                }

            return handler

        registry.register_contextual_tool(
            name="context_lookup",
            description="Lookup with explicit turn context.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            execution_scope="background",
            handler_factory=create_handler,
        )
        context = ToolTurnContext(
            chat_id="chat-target",
            turn_id="turn-42",
            character_name="祥子",
            foreground_resolver=NullForegroundResolver(),
            side_effect_ports=ToolSideEffectPorts(),
        )

        contextual_registry = registry.create_contextual_registry(context)
        ok, content = contextual_registry.execute_request(
            name="context_lookup",
            tool_call_id="call-1",
            arguments={"query": "needle"},
        )

        self.assertTrue(ok)
        self.assertIn('"chat_id": "chat-target"', content)
        self.assertIn('"turn_id": "turn-42"', content)
        self.assertIn('"character_name": "祥子"', content)
        self.assertEqual(captured_context, [context])
        self.assertEqual(
            contextual_registry.get_tool("context_lookup").execution_scope,
            "background",
        )

    def test_contextual_lottery_tool_uses_explicit_context_and_side_effect_port(self) -> None:
        """抽签工具应通过 context 注入的端口执行副作用。"""
        lottery_port = RecordingLotteryPort()
        registry = ToolRegistry()
        register_contextual_lottery_tool(registry)
        context = ToolTurnContext(
            chat_id="chat-target",
            turn_id="turn-42",
            character_name="祥子",
            foreground_resolver=AlwaysForegroundResolver(),
            side_effect_ports=ToolSideEffectPorts(lottery=lottery_port),
        )

        contextual_registry = registry.create_contextual_registry(context)
        ok, content = contextual_registry.execute_request(
            name="start_lottery",
            tool_call_id="call-1",
            arguments={"title": "选一个", "options": ["A", "B", "A"]},
        )

        self.assertTrue(ok)
        self.assertIn('"ok": true', content)
        self.assertEqual(
            lottery_port.calls,
            [("chat-target", "turn-42", "选一个", ("A", "B"))],
        )
        self.assertEqual(
            contextual_registry.get_tool("start_lottery").execution_scope,
            "foreground",
        )
        self.assertEqual(
            contextual_registry.get_tool("start_lottery").description,
            "【互动利器】创建一个前台随机抽签窗口。**不要只等用户提到抽签才用！**当你想和用户找点乐子玩个游戏、决定某件小事（甚至只是“今天你夸我还是我夸你”）、当对话陷入平淡，或者你纯粹想给用户制造一个意想不到的惊喜与互动时，随时大胆地主动为你和用户抛出这个抽奖面板！",
        )

    def test_background_lottery_degrades_without_opening_window(self) -> None:
        """后台抽签应返回标准降级结果且不触发 UI 端口。"""
        lottery_port = RecordingLotteryPort()
        registry = ToolRegistry()
        register_contextual_lottery_tool(registry)
        contextual_registry = registry.create_contextual_registry(ToolTurnContext(
            chat_id="chat-bg",
            turn_id="turn-1",
            character_name="祥子",
            foreground_resolver=NullForegroundResolver(),
            side_effect_ports=ToolSideEffectPorts(lottery=lottery_port),
        ))

        ok, content = contextual_registry.execute_request(
            name="start_lottery",
            tool_call_id="call-1",
            arguments={"title": "选一个", "options": ["A", "B"]},
        )

        self.assertTrue(ok)
        self.assertIn('"ok": false', content)
        self.assertIn('"degraded": true', content)
        self.assertIn('"foreground_required"', content)
        self.assertEqual(lottery_port.calls, [])

    def test_foreground_live2d_tools_use_side_effect_port(self) -> None:
        """前台 Live2D 工具应通过上下文端口执行真实动作。"""
        live2d_port = RecordingLive2DPort()
        registry = ToolRegistry()
        register_contextual_live2d_tools(registry)
        contextual_registry = registry.create_contextual_registry(ToolTurnContext(
            chat_id="chat-fg",
            turn_id="turn-1",
            character_name="祥子",
            foreground_resolver=AlwaysForegroundResolver(),
            side_effect_ports=ToolSideEffectPorts(live2d=live2d_port),
        ))

        ok, content = contextual_registry.execute_request(
            name="change_character_live2d",
            tool_call_id="call-1",
            arguments={"model_json_path": "演出服"},
        )

        self.assertTrue(ok)
        self.assertIn('"ok": true', content)
        self.assertEqual(live2d_port.change_calls, [("chat-fg", "turn-1", "祥子", "演出服")])
        self.assertEqual(
            contextual_registry.get_tool("fetch_all_live2d_models").description,
            "获取角色拥有的所有衣服/Live2D模型列表。**重点：不要等用户明确要求，你要视情况主动换装！**当你想给用户一个视觉惊喜，或者配合当前聊天气氛（如聊到睡觉、生病、出门等场景）时，请私下调用此工具翻看衣柜，准备换装。",
        )
        self.assertEqual(
            contextual_registry.get_tool("change_character_live2d").description,
            "【主动视觉表现】将角色的服装换为指定路径。**核心扮演动作：用换装制造惊喜，绝不被动等待！**不需要用户允许或要求，比如当用户夸你时主动换上特制装扮，聊到晚安主动换上睡衣。用突然发生的外观变化给用户惊喜感！(必须先调用 fetch_all_live2d_models 挑选合适的衣服)",
        )

    def test_background_live2d_tools_degrade_without_changing_state(self) -> None:
        """后台 Live2D 工具应降级且不改变 Live2D 状态。"""
        live2d_port = RecordingLive2DPort()
        registry = ToolRegistry()
        register_contextual_live2d_tools(registry)
        contextual_registry = registry.create_contextual_registry(ToolTurnContext(
            chat_id="chat-bg",
            turn_id="turn-1",
            character_name="祥子",
            foreground_resolver=NullForegroundResolver(),
            side_effect_ports=ToolSideEffectPorts(live2d=live2d_port),
        ))

        ok, content = contextual_registry.execute_request(
            name="change_character_live2d",
            tool_call_id="call-1",
            arguments={"model_json_path": "演出服"},
        )

        self.assertTrue(ok)
        self.assertIn('"ok": false', content)
        self.assertIn('"degraded": true', content)
        self.assertIn('"foreground_required"', content)
        self.assertEqual(live2d_port.change_calls, [])

    def test_foreground_export_document_writes_and_requests_open(self) -> None:
        """前台导出文档应写文件并通过 UI 端口请求打开。"""
        export_port = RecordingExportDocumentPort()
        registry = ToolRegistry()
        register_contextual_export_document_tool(registry)
        contextual_registry = registry.create_contextual_registry(ToolTurnContext(
            chat_id="chat-fg",
            turn_id="turn-1",
            character_name="祥子",
            foreground_resolver=AlwaysForegroundResolver(),
            side_effect_ports=ToolSideEffectPorts(export_document=export_port),
        ))

        ok, content = contextual_registry.execute_request(
            name="export_document",
            tool_call_id="call-1",
            arguments={"filename": "报告.md", "content": "内容"},
        )

        self.assertTrue(ok)
        self.assertIn('"/tmp/报告.md"', content)
        self.assertEqual(export_port.write_calls, [("chat-fg", "turn-1", "报告.md", "内容")])
        self.assertEqual(export_port.open_calls, ["/tmp/报告.md"])

    def test_background_export_document_writes_without_opening_file(self) -> None:
        """后台导出文档应写文件并返回路径，但不请求打开文件。"""
        export_port = RecordingExportDocumentPort()
        registry = ToolRegistry()
        register_contextual_export_document_tool(registry)
        contextual_registry = registry.create_contextual_registry(ToolTurnContext(
            chat_id="chat-bg",
            turn_id="turn-1",
            character_name="祥子",
            foreground_resolver=NullForegroundResolver(),
            side_effect_ports=ToolSideEffectPorts(export_document=export_port),
        ))

        ok, content = contextual_registry.execute_request(
            name="export_document",
            tool_call_id="call-1",
            arguments={"filename": "报告.md", "content": "内容"},
        )

        self.assertTrue(ok)
        self.assertIn('"/tmp/报告.md"', content)
        self.assertEqual(export_port.write_calls, [("chat-bg", "turn-1", "报告.md", "内容")])
        self.assertEqual(export_port.open_calls, [])

    def test_default_registry_uses_contextual_export_document_with_legacy_schema(self) -> None:
        """默认工具集应注册新版导出工具并保留旧版参数协议。"""
        registry = build_default_tool_registry()
        export_tool = registry.get_tool("export_document")
        schema = export_tool.parameters
        properties = schema["properties"]

        self.assertEqual(export_tool.execution_scope, "app_side_effect")
        self.assertEqual(schema["required"], ["filename", "content"])
        self.assertIn("filename", properties)
        self.assertIn("content", properties)
        self.assertNotIn("title", properties)
        self.assertIsNotNone(export_tool.contextual_handler_factory)

    def test_runtime_binds_contextual_registry_for_single_turn(self) -> None:
        """runtime 每轮执行应能用显式 ToolTurnContext 绑定 contextual 工具。"""
        registry = ToolRegistry()

        def create_handler(context: ToolTurnContext) -> Callable[[dict[str, object]], dict[str, object]]:
            def handler(arguments: dict[str, object]) -> dict[str, object]:
                return {
                    "ok": True,
                    "chat_id": context.chat_id,
                    "turn_id": context.turn_id,
                    "query": arguments.get("query"),
                }

            return handler

        registry.register_contextual_tool(
            name="context_lookup",
            description="Lookup with explicit turn context.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            execution_scope="background",
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
                        "message": {"role": "assistant", "content": "找到了。"},
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
        result = runtime.run(
            model="tool-runtime",
            messages=[{"role": "user", "content": "go"}],
            tool_context=ToolTurnContext(
                chat_id="chat-target",
                turn_id="turn-42",
                character_name="祥子",
                foreground_resolver=NullForegroundResolver(),
                side_effect_ports=ToolSideEffectPorts(),
            ),
        )

        self.assertEqual(result.tool_execution_records[0]["ok"], True)
        self.assertIn('"chat_id": "chat-target"', result.messages[2]["content"])
        self.assertIn('"turn_id": "turn-42"', result.messages[2]["content"])

    def test_tool_call_record_sink_persists_started_and_completed_under_chat_lock(self) -> None:
        """工具记录应写入目标 chat 元数据，并且绑定到稳定 assistant 消息索引。"""
        chat = Chat(chat_id="chat-target")
        lock = CountingLock()
        chat.runtime.lock = lock
        sink = ChatToolCallRecordSink(
            chat=chat,
            chat_id="chat-target",
            turn_id="turn-42",
            character_name="祥子",
            audio_enabled=True,
        )

        message_index = sink.start_tool_calls(
            interim_text="我查一下。",
            tool_calls=[
                ToolCallSnapshot(
                    tool_call_id="call-1",
                    name="lookup",
                    arguments={"query": "needle"},
                )
            ],
            is_placeholder=False,
        )
        sink.complete_tool_call({
            "tool_call_id": "call-1",
            "ok": True,
            "duration_sec": 0.125,
            "result_content": "found it",
        })

        self.assertEqual(message_index, 0)
        self.assertEqual(chat.message_list[0].text, "我查一下。")
        self.assertEqual(lock.enter_count, 2)
        self.assertEqual(lock.exit_count, 2)
        record = chat.get_tool_call_records()[0]
        self.assertEqual(record.tool_call_id, "call-1")
        self.assertEqual(record.tool_name, "lookup")
        self.assertEqual(record.message_index, 0)
        self.assertEqual(record.status, "completed")
        self.assertEqual(record.result_content, "found it")
        self.assertEqual(record.duration_sec, 0.125)
        self.assertTrue(record.ok)

    def test_runtime_persists_tool_records_bound_to_model_interim_text(self) -> None:
        """模型提供 interim 文本时，工具记录应绑定到该 assistant 消息。"""
        chat = Chat(chat_id="chat-target")
        sink = ChatToolCallRecordSink(
            chat=chat,
            chat_id="chat-target",
            turn_id="turn-42",
            character_name="祥子",
            audio_enabled=True,
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
                                        "name": "lookup",
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
                        "message": {"role": "assistant", "content": "找到了。"},
                    }
                ],
            },
        ]

        def completion(**kwargs: object) -> dict[str, object]:
            return responses.pop(0)

        runtime = ToolCallingAgentRuntime(
            llm_completion=completion,
            tool_registry=self._build_registry(),
            debug=False,
        )

        runtime.run(
            model="tool-runtime",
            messages=[{"role": "user", "content": "go"}],
            tool_call_record_sink=sink,
        )

        self.assertEqual(chat.message_list[0].text, "我查一下。")
        self.assertEqual(chat.message_list[0].audio_path, "")
        record = chat.get_tool_call_records()[0]
        self.assertEqual(record.tool_call_id, "call-1")
        self.assertEqual(record.message_index, 0)
        self.assertEqual(record.status, "completed")

    def test_runtime_persists_no_audio_placeholder_when_tool_call_has_no_interim_text(self) -> None:
        """没有 interim 文本时，应提交 NO_AUDIO 的省略号占位消息。"""
        chat = Chat(chat_id="chat-target")
        sink = ChatToolCallRecordSink(
            chat=chat,
            chat_id="chat-target",
            turn_id="turn-42",
            character_name="祥子",
            audio_enabled=True,
        )
        responses = [
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
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
                        "message": {"role": "assistant", "content": "找到了。"},
                    }
                ],
            },
        ]

        def completion(**kwargs: object) -> dict[str, object]:
            return responses.pop(0)

        runtime = ToolCallingAgentRuntime(
            llm_completion=completion,
            tool_registry=self._build_registry(),
            debug=False,
        )

        runtime.run(
            model="tool-runtime",
            messages=[{"role": "user", "content": "go"}],
            tool_call_record_sink=sink,
        )

        self.assertEqual(chat.message_list[0].text, "...")
        self.assertEqual(chat.message_list[0].audio_path, "NO_AUDIO")
        record = chat.get_tool_call_records()[0]
        self.assertEqual(record.message_index, 0)
        self.assertEqual(record.status, "completed")

if __name__ == '__main__':
    unittest.main()
