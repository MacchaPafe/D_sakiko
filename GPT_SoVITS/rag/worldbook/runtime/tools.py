"""构造绑定单回合快照、隐藏展示和七次预算的世界书工具。"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from uuid import UUID

from chat.tool_calling import ToolRegistry
from rag.models import CharacterId

from .models import RetrievalCandidate, WorldbookResolvedContext
from .service import WorldbookConversationService


WORLD_BOOK_TOOL_LIMIT = 7
"""单个真实用户回合最多执行的世界书工具次数。"""


@dataclass(slots=True)
class WorldbookToolDiagnostic:
    """保存一次隐藏工具调用的参数、候选、结果和耗时。"""

    tool_name: str
    arguments: dict[str, object]
    selected_entry_ids: list[UUID]
    candidates: list[RetrievalCandidate]
    result: dict[str, object]
    duration_sec: float


@dataclass(slots=True)
class WorldbookToolBudget:
    """跟踪单回合世界书查询次数并生成临近上限提示。"""

    used: int = 0
    limit: int = WORLD_BOOK_TOOL_LIMIT

    def can_query(self) -> bool:
        """判断下一次世界书查询是否仍可执行。"""

        return self.used < self.limit

    def consume(self) -> int:
        """消耗一次查询机会并返回已使用次数。"""

        if not self.can_query():
            return self.used
        self.used += 1
        return self.used

    def notice(self) -> str | None:
        """从第四次起提示模型剩余查询次数，第七次说明已达上限。"""

        if self.used < 4:
            return None
        remaining = self.limit - self.used
        if remaining <= 0:
            return "世界书查询次数已达到上限"
        return f"还能再查询 {remaining} 次"


@dataclass(slots=True)
class WorldbookToolSession:
    """持有一轮对话的动态工具 registry、预算和隐藏诊断。"""

    service: WorldbookConversationService
    context: WorldbookResolvedContext
    fallback_query: str
    budget: WorldbookToolBudget = field(default_factory=WorldbookToolBudget)
    diagnostics: list[WorldbookToolDiagnostic] = field(default_factory=list)
    _relation_targets: tuple[CharacterId, ...] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def build_registry(self) -> ToolRegistry:
        """创建只属于本轮的最多三个隐藏世界书工具。"""

        registry = ToolRegistry()
        unavailable_message = "世界书查询次数已达到上限"
        registry.register_tool(
            name="search_worldbook_lore",
            description=(
                "查询作品世界观中的人物、地点、乐队、设施和术语。"
                "例如用户提到 CRYCHIC 而你需要确认含义时，调用 "
                'search_worldbook_lore({"query":"CRYCHIC 是什么乐队"})。'
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "希望查询的世界观问题或关键词。",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=self._search_lore,
            channel="worldbook-hidden",
            availability=self.budget.can_query,
            unavailable_message=unavailable_message,
        )
        target_characters = self._relation_targets_for_turn()
        if target_characters:
            self._register_relation_tool(
                registry,
                target_characters,
                unavailable_message,
            )
        registry.register_tool(
            name="search_character_memory",
            description=(
                "查询当前角色在当前剧情进度已经持有的记忆、判断、信念或怀疑。"
                "只在需要角色主观经历或想法时调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "希望回忆的主题或问题。",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=self._search_memory,
            channel="worldbook-hidden",
            availability=self.budget.can_query,
            unavailable_message=unavailable_message,
        )
        return registry

    def _relation_targets_for_turn(self) -> tuple[CharacterId, ...]:
        """首次构建工具时查询并冻结本轮可公开的关系目标。"""

        if self._relation_targets is None:
            result = self.service.queryable_relation_targets(self.context)
            if result.failure is not None:
                self._relation_targets = ()
                return self._relation_targets
            targets = {
                item
                for item in result.items
                if item != self.context.character_id
            }
            self._relation_targets = tuple(
                sorted(targets, key=lambda item: item.value)
            )
        return self._relation_targets

    def _register_relation_tool(
        self,
        registry: ToolRegistry,
        target_characters: tuple[CharacterId, ...],
        unavailable_message: str,
    ) -> None:
        """使用冻结角色枚举注册本轮关系工具。"""

        target_values = [item.value for item in target_characters]
        display_mapping = "、".join(
            f"{item.value}={item.common_name}" for item in target_characters
        )
        registry.register_tool(
            name="query_character_relation",
            description=(
                "从当前角色的主观视角查询她与目标角色的关系。"
                "current 查询当前状态，at_episode 查询不晚于当前进度的指定集，"
                "history 按新到旧分页查询变化历史。"
                f"目标角色映射：{display_mapping}。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_character": {
                        "type": "string",
                        "enum": target_values,
                        "description": "规范角色 ID。",
                    },
                    "focus": {
                        "type": "string",
                        "description": "可选的关系方面或当前话题。",
                    },
                    "view": {
                        "type": "string",
                        "enum": ["current", "at_episode", "history"],
                    },
                    "episode": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": self.context.episode,
                    },
                    "page": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "history 模式页码，固定每页五条。",
                    },
                },
                "required": ["target_character", "view"],
                "additionalProperties": False,
            },
            handler=self._query_relation,
            channel="worldbook-hidden",
            availability=self.budget.can_query,
            unavailable_message=unavailable_message,
        )

    def _search_lore(self, arguments: dict[str, object]) -> dict[str, object]:
        """执行 Lore 查询并只返回模型安全字段。"""

        query = self._required_string(arguments, "query")
        started = time.perf_counter()
        self.budget.consume()
        result = self.service.search_lore(self.context, query)
        payload = self._result_payload(
            [item.model_dump(mode="json") for item in result.items],
            result.failure.model_dump(mode="json") if result.failure is not None else None,
        )
        self._record(
            "search_worldbook_lore",
            arguments,
            result.trace.selected_entry_ids,
            result.trace.candidates,
            payload,
            started,
        )
        return payload

    def _query_relation(self, arguments: dict[str, object]) -> dict[str, object]:
        """执行关系 current、at_episode 或 history 查询。"""

        target_text = self._required_string(arguments, "target_character")
        try:
            target = CharacterId(target_text)
        except ValueError as exc:
            raise ValueError("target_character 不在当前世界书可查询范围内") from exc
        available = set(self._relation_targets_for_turn())
        if target not in available or target == self.context.character_id:
            raise ValueError("target_character 不在当前世界书可查询范围内")
        view = self._required_string(arguments, "view")
        focus_value = arguments.get("focus")
        focus = focus_value.strip() if isinstance(focus_value, str) else ""
        query_focus = focus or self.fallback_query
        started = time.perf_counter()
        self.budget.consume()
        if view == "history":
            page_value = arguments.get("page", 1)
            if isinstance(page_value, bool) or not isinstance(page_value, int):
                raise ValueError("history 模式的 page 必须是整数")
            result = self.service.relation_history(self.context, target, page_value)
            payload = {
                "ok": result.failure is None,
                **result.page.model_dump(mode="json"),
            }
            if result.failure is not None:
                payload["error"] = result.failure.model_dump(mode="json")
            selected_entry_ids = result.trace.selected_entry_ids
            candidates = result.trace.candidates
        elif view in {"current", "at_episode"}:
            episode: int | None = None
            if view == "at_episode":
                episode_value = arguments.get("episode")
                if isinstance(episode_value, bool) or not isinstance(episode_value, int):
                    raise ValueError("at_episode 模式必须提供整数 episode")
                episode = episode_value
            result = self.service.query_relation(
                self.context,
                target,
                query_focus,
                episode,
            )
            payload = self._result_payload(
                [item.model_dump(mode="json") for item in result.items],
                result.failure.model_dump(mode="json") if result.failure is not None else None,
            )
            selected_entry_ids = result.trace.selected_entry_ids
            candidates = result.trace.candidates
        else:
            raise ValueError("view 必须是 current、at_episode 或 history")
        self._append_budget_notice(payload)
        self._record(
            "query_character_relation",
            arguments,
            selected_entry_ids,
            candidates,
            payload,
            started,
        )
        return payload

    def _search_memory(self, arguments: dict[str, object]) -> dict[str, object]:
        """执行角色记忆查询并展开显式关联事件。"""

        query = self._required_string(arguments, "query")
        started = time.perf_counter()
        self.budget.consume()
        result = self.service.search_memory(self.context, query)
        payload = self._result_payload(
            [item.model_dump(mode="json") for item in result.items],
            result.failure.model_dump(mode="json") if result.failure is not None else None,
        )
        self._record(
            "search_character_memory",
            arguments,
            result.trace.selected_entry_ids,
            result.trace.candidates,
            payload,
            started,
        )
        return payload

    def _result_payload(
        self,
        items: list[dict[str, object]],
        failure: dict[str, object] | None,
    ) -> dict[str, object]:
        """构造统一工具结果并附加预算提示。"""

        payload: dict[str, object] = {
            "ok": failure is None,
            "results": items,
        }
        if failure is not None:
            payload["error"] = failure
        self._append_budget_notice(payload)
        return payload

    def _append_budget_notice(self, payload: dict[str, object]) -> None:
        """在第四至第七次实际查询结果中加入剩余次数提示。"""

        notice = self.budget.notice()
        if notice is not None:
            payload["query_budget_notice"] = notice

    def _record(
        self,
        tool_name: str,
        arguments: dict[str, object],
        selected_entry_ids: list[UUID],
        candidates: list[RetrievalCandidate],
        result: dict[str, object],
        started: float,
    ) -> None:
        """记录一条仅供世界书诊断使用的隐藏调用。"""

        self.diagnostics.append(
            WorldbookToolDiagnostic(
                tool_name=tool_name,
                arguments=dict(arguments),
                selected_entry_ids=list(selected_entry_ids),
                candidates=list(candidates),
                result=dict(result),
                duration_sec=max(0.0, time.perf_counter() - started),
            )
        )

    def _required_string(
        self,
        arguments: dict[str, object],
        field_name: str,
    ) -> str:
        """读取并校验工具参数中的必填非空字符串。"""

        value = arguments.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} 必须是非空字符串")
        return value.strip()
