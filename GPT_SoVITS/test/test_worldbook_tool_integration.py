"""隐藏世界书工具 overlay、展示通道和七次预算测试。"""

from __future__ import annotations

import json
import unittest
from chat.tool_calling import (
    ToolCallRequest,
    ToolCallingAgentRuntime,
    ToolRegistry,
)
from rag.models import CharacterId
from rag.worldbook.runtime.models import (
    CharacterMemoryKnowledge,
    KnownStoryEvent,
    LoreKnowledge,
    RelationHistoryPage,
    RelationHistoryQueryResult,
    RelationKnowledge,
    RelationTargetsQueryResult,
    ThoughtMemory,
    WorldbookKnowledgeResult,
    WorldbookQueryResult,
    WorldbookResolvedContext,
)
from rag.worldbook.runtime.tools import WorldbookToolSession


def _context() -> WorldbookResolvedContext:
    """创建隐藏工具测试上下文。"""

    return WorldbookResolvedContext(
        root_package_id="root",
        root_package_version="1.0.0",
        package_ids=["root"],
        package_versions={"root": "1.0.0"},
        package_depths={"root": 0},
        character_id="anon",
        series_id="its_mygo",
        timeline_id="bang_dream_original",
        canon_branch="main",
        current_time=4099,
        story_year=3,
        episode=2,
    )


def _tool_response(name: str, arguments: dict[str, object], call_id: str) -> dict[str, object]:
    """构造一条 OpenAI 风格工具调用响应。"""

    return {
        "model": "test-model",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            },
                        }
                    ],
                },
            }
        ],
    }


def _final_response(content: str = "最终回答") -> dict[str, object]:
    """构造一条无工具的最终响应。"""

    return {
        "model": "test-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": content},
            }
        ],
    }


class _SequenceCompletion:
    """按顺序返回预置响应并记录每次 schema。"""

    def __init__(self, responses: list[dict[str, object]]) -> None:
        """保存响应队列。"""

        self._responses = list(responses)
        self.tool_names: list[list[str]] = []
        self.tool_choices: list[str] = []

    def __call__(self, **kwargs: object) -> dict[str, object]:
        """记录本次工具 schema 并返回下一条响应。"""

        tools = kwargs.get("tools")
        names: list[str] = []
        if isinstance(tools, list):
            for raw_tool in tools:
                if not isinstance(raw_tool, dict):
                    continue
                function = raw_tool.get("function")
                if isinstance(function, dict):
                    name = function.get("name")
                    if isinstance(name, str):
                        names.append(name)
        self.tool_names.append(names)
        self.tool_choices.append(str(kwargs.get("tool_choice") or ""))
        return self._responses.pop(0)


class _FakeWorldbookService:
    """为工具 session 返回固定模型安全结果。"""

    def __init__(self) -> None:
        """初始化调用次数。"""

        self.lore_calls = 0
        self.target_calls = 0

    def queryable_relation_targets(
        self,
        context: WorldbookResolvedContext,
    ) -> RelationTargetsQueryResult:
        """返回当前视角与一个关系目标。"""

        del context
        self.target_calls += 1
        return RelationTargetsQueryResult(items=[CharacterId.SOYO])

    def search_lore(
        self,
        context: WorldbookResolvedContext,
        query: str,
    ) -> WorldbookQueryResult[LoreKnowledge]:
        """返回一条固定 Lore。"""

        del context, query
        self.lore_calls += 1
        return WorldbookQueryResult[LoreKnowledge](
            items=[LoreKnowledge(title="CRYCHIC", content="一支旧乐队。")]
        )

    def query_relation(
        self,
        context: WorldbookResolvedContext,
        target_character: CharacterId | str,
        focus: str,
        episode: int | None = None,
    ) -> WorldbookQueryResult[RelationKnowledge]:
        """返回一条固定关系。"""

        del context, target_character, focus, episode
        return WorldbookQueryResult[RelationKnowledge](
            items=[
                RelationKnowledge(
                    target_character_name="素世",
                    state_summary="爱音把素世视为同伴。",
                )
            ]
        )

    def relation_history(
        self,
        context: WorldbookResolvedContext,
        target_character: CharacterId | str,
        page: int,
    ) -> RelationHistoryQueryResult:
        """返回一页固定关系历史。"""

        del context, target_character
        return RelationHistoryQueryResult(
            page=RelationHistoryPage(
                items=[],
                page=page,
                has_more=False,
            )
        )

    def search_memory(
        self,
        context: WorldbookResolvedContext,
        query: str,
    ) -> WorldbookKnowledgeResult:
        """返回固定的顶层事件与观点记忆结果。"""

        del context, query
        return WorldbookKnowledgeResult(
            knowledge=CharacterMemoryKnowledge(
                events=[
                    KnownStoryEvent(
                        title="初次相遇",
                        summary="爱音摔倒后得到灯递来的创可贴。",
                        participant_names=["爱音", "灯"],
                    )
                ],
                thoughts=[
                    ThoughtMemory(
                        character_name="爱音",
                        thought_text="灯很特别。",
                        epistemic_status="believes",
                    )
                ],
            )
        )


class WorldbookHiddenToolTest(unittest.TestCase):
    """验证世界书工具不会污染普通工具 UI，同时保持完整 LLM 闭环。"""

    def test_hidden_only_call_has_no_interim_or_visible_record(self) -> None:
        """仅隐藏调用不得触发过渡回调或生成普通工具记录。"""

        overlay = ToolRegistry()
        overlay.register_tool(
            "hidden",
            "隐藏测试工具",
            {"type": "object", "properties": {}},
            lambda arguments: {"ok": True, "value": arguments},
            channel="worldbook-hidden",
        )
        completion = _SequenceCompletion(
            [_tool_response("hidden", {}, "hidden-1"), _final_response()]
        )
        interim_calls: list[str] = []
        runtime = ToolCallingAgentRuntime(completion, tool_registry=ToolRegistry())

        result = runtime.run(
            "test",
            [{"role": "user", "content": "你好"}],
            tool_overlay=overlay,
            include_base_tools=False,
            on_interim_message=lambda text: interim_calls.append(str(text)),
        )

        self.assertEqual(interim_calls, [])
        self.assertEqual(result.tool_execution_records, [])
        self.assertEqual(result.visible_tool_rounds, 0)
        self.assertTrue(any(message.get("role") == "tool" for message in result.messages))

    def test_mixed_call_only_exposes_general_tool(self) -> None:
        """同一响应混合调用时只把普通工具交给回调和 UI 记录。"""

        base = ToolRegistry()
        base.register_tool(
            "visible",
            "普通测试工具",
            {"type": "object", "properties": {}},
            lambda arguments: {"ok": True, "value": arguments},
        )
        overlay = ToolRegistry()
        overlay.register_tool(
            "hidden",
            "隐藏测试工具",
            {"type": "object", "properties": {}},
            lambda arguments: {"ok": True, "value": arguments},
            channel="worldbook-hidden",
        )
        mixed_response = _tool_response("visible", {}, "visible-1")
        message = mixed_response["choices"][0]["message"]
        message["tool_calls"].append(
            {
                "id": "hidden-1",
                "type": "function",
                "function": {"name": "hidden", "arguments": "{}"},
            }
        )
        completion = _SequenceCompletion([mixed_response, _final_response()])
        callback_names: list[list[str]] = []
        runtime = ToolCallingAgentRuntime(completion, tool_registry=base)

        result = runtime.run(
            "test",
            [{"role": "user", "content": "你好"}],
            tool_overlay=overlay,
            include_base_tools=True,
            on_interim_message=lambda text, calls: callback_names.append(
                [call.name for call in calls]
            ),
        )

        self.assertEqual(callback_names, [["visible"]])
        self.assertEqual(
            [record["tool_name"] for record in result.tool_execution_records],
            ["visible"],
        )
        self.assertEqual(result.visible_tool_rounds, 1)

    def test_budget_notices_and_schema_removal_after_seventh_query(self) -> None:
        """第四次起应提示剩余次数，第七次后下一轮不得再发送世界书 schema。"""

        service = _FakeWorldbookService()
        session = WorldbookToolSession(service, _context(), "最近对话")
        responses = [
            _tool_response(
                "search_worldbook_lore",
                {"query": f"问题 {index}"},
                f"call-{index}",
            )
            for index in range(1, 8)
        ]
        responses.append(_final_response())
        completion = _SequenceCompletion(responses)
        runtime = ToolCallingAgentRuntime(
            completion,
            tool_registry=ToolRegistry(),
            max_tool_rounds=10,
        )

        result = runtime.run(
            "test",
            [{"role": "user", "content": "连续查询"}],
            tool_overlay=session.build_registry(),
            include_base_tools=False,
        )

        notices = [
            diagnostic.result.get("query_budget_notice")
            for diagnostic in session.diagnostics
        ]
        self.assertEqual(service.lore_calls, 7)
        self.assertEqual(
            notices,
            [None, None, None, "还能再查询 3 次", "还能再查询 2 次", "还能再查询 1 次", "世界书查询次数已达到上限"],
        )
        self.assertEqual(completion.tool_names[-1], [])
        self.assertEqual(completion.tool_choices[-1], "none")
        self.assertEqual(result.tool_execution_records, [])

    def test_relation_schema_uses_dynamic_character_enum(self) -> None:
        """关系工具 schema 应使用本轮冻结的安全目标枚举。"""

        service = _FakeWorldbookService()
        session = WorldbookToolSession(service, _context(), "最近对话")
        schemas = session.build_registry().build_tools_schema()
        session.build_registry()
        relation = next(
            item["function"]
            for item in schemas
            if item["function"]["name"] == "query_character_relation"
        )

        enum_values = relation["parameters"]["properties"]["target_character"]["enum"]
        self.assertEqual(enum_values, ["soyo"])
        self.assertEqual(service.target_calls, 1)

    def test_hidden_tool_validation_error_omits_traceback(self) -> None:
        """隐藏工具参数错误不得把本地 traceback 发送给模型。"""

        registry = WorldbookToolSession(
            _FakeWorldbookService(),
            _context(),
            "最近对话",
        ).build_registry()

        ok, output = registry.execute(
            ToolCallRequest(
                tool_call_id="bad",
                name="search_worldbook_lore",
                arguments={},
            )
        )

        self.assertFalse(ok)
        self.assertNotIn("traceback", output)

    def test_memory_tool_returns_top_level_events_and_thoughts(self) -> None:
        """记忆工具应按顶层来源返回事实与观点，不再把 Event 嵌套进 Thought。"""

        registry = WorldbookToolSession(
            _FakeWorldbookService(),
            _context(),
            "最近对话",
        ).build_registry()

        ok, output = registry.execute(
            ToolCallRequest(
                tool_call_id="memory",
                name="search_character_memory",
                arguments={"query": "最初怎么认识灯"},
            )
        )
        payload = json.loads(output)

        self.assertTrue(ok)
        self.assertEqual(payload["events"][0]["title"], "初次相遇")
        self.assertEqual(payload["thoughts"][0]["thought_text"], "灯很特别。")
        self.assertNotIn("results", payload)


if __name__ == "__main__":
    unittest.main()
