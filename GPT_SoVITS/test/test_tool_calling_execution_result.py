"""工具执行结果及用户展示格式化测试。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from chat.tool_calling import (
    SystemHardwareTool,
    ToolCallRequest,
    ToolCallingAgentRuntime,
    ToolRegistry,
    WeatherTool,
)


def _successful_handler(arguments: dict[str, object]) -> dict[str, object]:
    """返回同时适合验证模型文本和展示文本的固定结果。"""

    return {"ok": True, "value": arguments.get("value")}


def _display_value(result: object) -> str:
    """把固定测试结果转换为简短展示文本。"""

    if not isinstance(result, dict):
        return "无结果"
    return f"展示值: {result.get('value')}"


def _raising_formatter(result: object) -> str:
    """模拟发生异常的展示格式化器。"""

    raise ValueError(f"无法展示: {result!r}")


class _ToolThenFinalCompletion:
    """先请求工具调用，再返回最终回答，并保存第二轮消息。"""

    def __init__(self) -> None:
        """初始化调用次数和第二轮消息快照。"""

        self.call_count = 0
        self.second_round_messages: list[object] = []

    def __call__(self, **kwargs: object) -> dict[str, object]:
        """按调用次数返回工具请求或最终回答。"""

        self.call_count += 1
        if self.call_count == 1:
            return {
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "runtime-call",
                                    "type": "function",
                                    "function": {
                                        "name": "visible",
                                        "arguments": '{"value": "模型原文"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
            }

        messages = kwargs.get("messages")
        if isinstance(messages, list):
            self.second_round_messages = list(messages)
        return {
            "model": "test-model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "完成"},
                }
            ],
        }


class ToolExecutionResultTest(unittest.TestCase):
    """验证工具结果的模型表示、展示表示和执行状态相互独立。"""

    def test_visible_tool_returns_separate_model_and_display_content(self) -> None:
        """可见工具应同时返回稳定 JSON 和自定义展示文本。"""

        registry = ToolRegistry()
        registry.register_tool(
            name="visible",
            description="可见测试工具",
            parameters={"type": "object", "properties": {}},
            handler=_successful_handler,
            display_formatter=_display_value,
        )

        result = registry.execute(
            ToolCallRequest("call-1", "visible", {"value": "原始值"})
        )

        self.assertTrue(result.execution_succeeded)
        self.assertEqual(
            result.model_content,
            '{"ok": true, "value": "原始值"}',
        )
        self.assertEqual(result.display_content, "展示值: 原始值")

    def test_runtime_sends_model_content_and_records_display_content(self) -> None:
        """主循环应把模型文本送回 LLM，并把展示文本写入执行记录。"""

        registry = ToolRegistry()
        registry.register_tool(
            name="visible",
            description="可见测试工具",
            parameters={"type": "object", "properties": {}},
            handler=_successful_handler,
            display_formatter=_display_value,
        )
        completion = _ToolThenFinalCompletion()
        runtime = ToolCallingAgentRuntime(completion, tool_registry=registry)

        run_result = runtime.run(
            model="test-model",
            messages=[{"role": "user", "content": "开始"}],
        )

        tool_messages = [
            message
            for message in completion.second_round_messages
            if isinstance(message, dict) and message.get("role") == "tool"
        ]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(
            json.loads(str(tool_messages[0].get("content")))["value"],
            "模型原文",
        )
        self.assertEqual(
            run_result.tool_execution_records[0]["result_content"],
            "展示值: 模型原文",
        )

    def test_hidden_tool_does_not_run_display_formatter(self) -> None:
        """隐藏工具应只生成模型文本，不调用展示格式化器。"""

        formatter_calls: list[object] = []

        def record_formatter(result: object) -> str:
            """记录意外发生的隐藏工具格式化调用。"""

            formatter_calls.append(result)
            return "不应生成"

        registry = ToolRegistry()
        registry.register_tool(
            name="hidden",
            description="隐藏测试工具",
            parameters={"type": "object", "properties": {}},
            handler=_successful_handler,
            channel="worldbook-hidden",
            display_formatter=record_formatter,
        )

        result = registry.execute(
            ToolCallRequest("call-2", "hidden", {"value": "模型可见"})
        )

        self.assertTrue(result.execution_succeeded)
        self.assertIsNone(result.display_content)
        self.assertEqual(formatter_calls, [])

    def test_formatter_failure_falls_back_without_failing_execution(self) -> None:
        """展示格式化失败不得改变工具执行状态或模型文本。"""

        registry = ToolRegistry()
        registry.register_tool(
            name="fallback",
            description="回退测试工具",
            parameters={"type": "object", "properties": {}},
            handler=_successful_handler,
            display_formatter=_raising_formatter,
        )

        with patch("chat.tool_calling.logger.exception") as log_exception:
            result = registry.execute(
                ToolCallRequest("call-3", "fallback", {"value": 7})
            )

        self.assertTrue(result.execution_succeeded)
        self.assertEqual(json.loads(result.model_content)["value"], 7)
        self.assertIn('"value": 7', result.display_content or "")
        log_exception.assert_called_once()

    def test_handler_reported_failure_uses_generic_error_display(self) -> None:
        """handler 主动报告失败时应保留既有执行状态并显示错误字段。"""

        formatter_calls: list[object] = []

        def failing_handler(arguments: dict[str, object]) -> dict[str, object]:
            """返回业务失败结果但不抛出异常。"""

            return {"ok": False, "error": "参数无效"}

        def record_formatter(result: object) -> str:
            """记录不应处理失败结果的专用格式化调用。"""

            formatter_calls.append(result)
            return "错误被隐藏"

        registry = ToolRegistry()
        registry.register_tool(
            name="reported-failure",
            description="业务失败测试工具",
            parameters={"type": "object", "properties": {}},
            handler=failing_handler,
            display_formatter=record_formatter,
        )

        result = registry.execute(
            ToolCallRequest("call-4", "reported-failure", {})
        )

        self.assertTrue(result.execution_succeeded)
        self.assertIn("参数无效", result.model_content)
        self.assertIn("参数无效", result.display_content or "")
        self.assertEqual(formatter_calls, [])

    def test_unserializable_result_becomes_execution_failure(self) -> None:
        """无法序列化的 handler 返回值应保持既有的执行失败语义。"""

        def unserializable_handler(arguments: dict[str, object]) -> object:
            """返回 JSON 无法序列化的对象。"""

            return object()

        registry = ToolRegistry()
        registry.register_tool(
            name="unserializable",
            description="序列化失败测试工具",
            parameters={"type": "object", "properties": {}},
            handler=unserializable_handler,
        )

        result = registry.execute(
            ToolCallRequest("call-5", "unserializable", {})
        )
        payload = json.loads(result.model_content)

        self.assertFalse(result.execution_succeeded)
        self.assertEqual(payload["error"], "tool_execution_failed")
        self.assertIn("tool_execution_failed", result.display_content or "")


class BuiltInToolFormatterTest(unittest.TestCase):
    """验证内置 formatter 使用工具实际返回的嵌套字段。"""

    def test_weather_formatter_reads_nested_weather_fields(self) -> None:
        """天气展示应从 weather 对象读取温度、描述、湿度和风速。"""

        display = WeatherTool.format_for_display(
            {
                "ok": True,
                "city": "上海",
                "weather": {
                    "temperature_celsius": 31,
                    "apparent_temperature_celsius": 34,
                    "humidity_percent": 70,
                    "wind_speed_kmh": 9,
                    "weather_description": "Partly cloudy",
                },
            }
        )

        self.assertEqual(
            display,
            "城市: 上海\n"
            "温度: 31°C\n"
            "体感温度: 34°C\n"
            "天气: Partly cloudy\n"
            "湿度: 70%\n"
            "风速: 9 km/h",
        )

    def test_hardware_formatter_reads_nested_usage_fields(self) -> None:
        """硬件展示应从 cpu、memory 和 gpu 对象读取指标。"""

        display = SystemHardwareTool.format_for_display(
            {
                "ok": True,
                "cpu": {
                    "usage_percent": 12.5,
                    "temperature": {
                        "available": True,
                        "value_celsius": 48,
                    },
                },
                "memory": {"usage_percent": 40.0},
                "gpu": {
                    "available": True,
                    "cards": [
                        {
                            "name": "Test GPU",
                            "utilization_percent": 20,
                            "temperature_celsius": 55,
                        }
                    ],
                },
            }
        )

        self.assertEqual(
            display,
            "CPU占用: 12.5%\n"
            "CPU温度: 48°C\n"
            "内存占用: 40.0%\n"
            "GPU: Test GPU\n"
            "GPU占用: 20%\n"
            "GPU温度: 55°C",
        )


if __name__ == "__main__":
    unittest.main()
