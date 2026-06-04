from __future__ import annotations

import unittest

from tools.fake_openai_server import (
    Phase,
    Scenario,
    build_chat_completion_response,
    detect_phase,
    scenario_content,
)


class FakeOpenAIServerTest(unittest.TestCase):
    """验证本地假 OpenAI 服务的纯逻辑。"""

    def test_detect_phase_from_prompt_text(self) -> None:
        """应根据格式收口提示判断请求阶段。"""

        self.assertEqual(
            detect_phase([{"role": "user", "content": "请将上一条 assistant 候选回复转换为本轮最终回复。"}]),
            Phase.FINAL_JSON,
        )
        self.assertEqual(
            detect_phase([{"role": "user", "content": "下面是模型输出\n请只纠正 JSON 结构、字段和缺失项"}]),
            Phase.FORMAT_RETRY,
        )
        self.assertEqual(detect_phase([{"role": "user", "content": "你好"}]), Phase.INITIAL)

    def test_lenient_retry_scenario_changes_by_phase(self) -> None:
        """宽松重试场景应先返回自然语言，再返回可宽松解析的 JSON。"""

        self.assertEqual(
            scenario_content(Scenario.LENIENT_RETRY, Phase.INITIAL),
            "这是一条普通候选回复，会触发最终 JSON 收口。",
        )
        self.assertIn("neutral", scenario_content(Scenario.LENIENT_RETRY, Phase.FINAL_JSON))
        self.assertIn("debug", scenario_content(Scenario.LENIENT_RETRY, Phase.FORMAT_RETRY))

    def test_chat_completion_response_shape(self) -> None:
        """chat completions 响应应符合 OpenAI-compatible 基本形状。"""

        response = build_chat_completion_response(
            {
                "model": "openai/fake-model",
                "messages": [{"role": "user", "content": "你好"}],
            },
            Scenario.TOP_LEVEL_OBJECT_NEUTRAL,
        )

        self.assertEqual(response["object"], "chat.completion")
        self.assertEqual(response["model"], "openai/fake-model")
        choices = response["choices"]
        self.assertIsInstance(choices, list)
        self.assertIn("neutral", str(choices[0]))


if __name__ == "__main__":
    unittest.main()
