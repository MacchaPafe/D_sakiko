import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from chat.tool_calling import ToolCallingAgentRuntime, ToolRegistry


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

if __name__ == '__main__':
    unittest.main()
