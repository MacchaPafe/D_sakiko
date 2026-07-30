"""向聊天层提供角色隔离的世界书对话检索深接口。"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from rag.models import CharacterId, ScopeType
from rag.worldbook.models import EntryType
from rag.worldbook.time_coordinates import EPISODE_SLOT_COUNT, StoryTimeCoordinate, encode_story_time

from .catalog import WorldbookCatalogError, WorldbookRootCatalog
from .models import (
    DirectThought,
    LinkedStoryEvent,
    LoreKnowledge,
    RelationHistoryPage,
    RelationHistoryQueryResult,
    RelationKnowledge,
    RelationTargetsQueryResult,
    RetrievalCandidate,
    RetrievalFailure,
    RetrievalTrace,
    ThoughtMemory,
    WorldbookQueryResult,
    WorldbookResolvedContext,
)
from .retrieval import (
    PayloadScanRequest,
    RetrievalConstraints,
    SemanticSearchRequest,
    WorldbookRetrievalRepository,
)


DIRECT_THOUGHT_THRESHOLD = 0.86
"""按当前已审核小样本校准的严格直接观点最低相似度。"""

TOOL_RETRIEVAL_THRESHOLD = 0.45
"""世界书工具检索使用的最低相似度。"""

RELATION_HISTORY_PAGE_SIZE = 5
"""关系历史固定页长。"""


class WorldbookConversationService:
    """隐藏包、索引和可见性细节，暴露四种对话知识查询。"""

    def __init__(
        self,
        catalog: WorldbookRootCatalog,
        retrieval: WorldbookRetrievalRepository,
    ) -> None:
        """注入根包目录和只读检索仓库。"""

        self._catalog = catalog
        self._retrieval = retrieval

    def direct_thoughts(
        self,
        context: WorldbookResolvedContext,
        query: str,
        current_user_text: str,
    ) -> WorldbookQueryResult[DirectThought]:
        """检索最多三条当前角色可直接使用的高相关观点。"""

        batch = self._retrieval.semantic_search(
            SemanticSearchRequest(
                entry_type="character_thought",
                query=query,
                constraints=RetrievalConstraints(
                    context=context,
                    subject_character_id=context.character_id,
                ),
                limit=24,
                candidate_limit=24,
                score_threshold=DIRECT_THOUGHT_THRESHOLD,
                boost_source=current_user_text,
            )
        )
        visible = self._visible_candidates(
            batch.candidates,
            context,
            "character_thought",
            context.current_time,
            active=True,
            character_id=context.character_id,
        )
        current = self._current_states(visible, context, "thought_thread_key")
        selected = current[:3]
        return WorldbookQueryResult[DirectThought](
            items=[self._direct_thought(item) for item in selected],
            failure=batch.failure,
            trace=RetrievalTrace(
                selected_entry_ids=[item.entry_id for item in selected],
                candidates=batch.candidates,
            ),
        )

    def search_lore(
        self,
        context: WorldbookResolvedContext,
        query: str,
    ) -> WorldbookQueryResult[LoreKnowledge]:
        """检索最多三条适用于根包作品上下文的世界观知识。"""

        batch = self._retrieval.semantic_search(
            SemanticSearchRequest(
                entry_type="lore_entry",
                query=query,
                constraints=RetrievalConstraints(context=context),
                limit=24,
                candidate_limit=24,
                score_threshold=TOOL_RETRIEVAL_THRESHOLD,
                boost_source=query,
            )
        )
        visible = self._visible_candidates(
            batch.candidates,
            context,
            "lore_entry",
            context.current_time,
            active=True,
        )
        selected = visible[:3]
        return WorldbookQueryResult[LoreKnowledge](
            items=[self._lore(item) for item in selected],
            failure=batch.failure,
            trace=RetrievalTrace(
                selected_entry_ids=[item.entry_id for item in selected],
                candidates=batch.candidates,
            ),
        )

    def query_relation(
        self,
        context: WorldbookResolvedContext,
        target_character: CharacterId | str,
        focus: str,
        episode: int | None = None,
    ) -> WorldbookQueryResult[RelationKnowledge]:
        """查询当前或指定过去集结束时最多三个关系方面。"""

        target = CharacterId(target_character)
        query_time, failure = self._relation_query_time(context, episode)
        if failure is not None:
            return WorldbookQueryResult[RelationKnowledge](failure=failure)
        query = focus.strip() or f"{context.character_id.common_name}与{target.common_name}的关系"
        batch = self._retrieval.semantic_search(
            SemanticSearchRequest(
                entry_type="character_relation",
                query=query,
                constraints=RetrievalConstraints(
                    context=context,
                    query_time=query_time,
                    subject_character_id=context.character_id,
                    object_character_id=target,
                ),
                limit=24,
                candidate_limit=24,
                score_threshold=TOOL_RETRIEVAL_THRESHOLD,
                boost_source=focus,
            )
        )
        visible = self._visible_candidates(
            batch.candidates,
            context,
            "character_relation",
            query_time,
            active=True,
            character_id=context.character_id,
            target_character_id=target,
        )
        current = self._current_states(visible, context, "relation_type_key")
        selected = current[:3]
        return WorldbookQueryResult[RelationKnowledge](
            items=[self._relation(item, target) for item in selected],
            failure=batch.failure,
            trace=RetrievalTrace(
                selected_entry_ids=[item.entry_id for item in selected],
                candidates=batch.candidates,
            ),
        )

    def relation_history(
        self,
        context: WorldbookResolvedContext,
        target_character: CharacterId | str,
        page: int,
    ) -> RelationHistoryQueryResult:
        """按新到旧返回固定五条一页的显式关系历史。"""

        if page < 1:
            return RelationHistoryQueryResult(
                page=RelationHistoryPage(page=1, items=[], has_more=False),
                failure=RetrievalFailure(
                    code="invalid_request",
                    message="关系历史页码必须从 1 开始",
                ),
            )
        target = CharacterId(target_character)
        batch = self._retrieval.scan_payloads(
            PayloadScanRequest(
                entry_type="character_relation",
                constraints=RetrievalConstraints(
                    context=context,
                    time_mode="started",
                    subject_character_id=context.character_id,
                    object_character_id=target,
                ),
            )
        )
        candidates = [
            RetrievalCandidate(
                entry_id=record.entry_id,
                package_id=record.package_id,
                entry_type=record.entry_type,
                payload=record.payload,
                score=0.0,
                final_score=0.0,
            )
            for record in batch.records
        ]
        visible = self._visible_candidates(
            candidates,
            context,
            "character_relation",
            context.current_time,
            active=False,
            character_id=context.character_id,
            target_character_id=target,
        )
        visible.sort(
            key=lambda item: (
                -self._int_field(item, "visible_from"),
                context.package_depths.get(item.package_id, 999),
                str(item.entry_id),
            )
        )
        start = (page - 1) * RELATION_HISTORY_PAGE_SIZE
        end = start + RELATION_HISTORY_PAGE_SIZE
        selected = visible[start:end]
        has_more = end < len(visible)
        return RelationHistoryQueryResult(
            page=RelationHistoryPage(
                items=[self._relation(item, target) for item in selected],
                page=page,
                has_more=has_more,
                next_page=page + 1 if has_more else None,
            ),
            failure=batch.failure,
            trace=RetrievalTrace(
                selected_entry_ids=[item.entry_id for item in selected],
                candidates=candidates,
            ),
        )

    def search_memory(
        self,
        context: WorldbookResolvedContext,
        query: str,
    ) -> WorldbookQueryResult[ThoughtMemory]:
        """检索最多五条角色观点并展开全部可见的显式关联事件。"""

        batch = self._retrieval.semantic_search(
            SemanticSearchRequest(
                entry_type="character_thought",
                query=query,
                constraints=RetrievalConstraints(
                    context=context,
                    subject_character_id=context.character_id,
                ),
                limit=32,
                candidate_limit=32,
                score_threshold=TOOL_RETRIEVAL_THRESHOLD,
                boost_source=query,
            )
        )
        visible = self._visible_candidates(
            batch.candidates,
            context,
            "character_thought",
            context.current_time,
            active=True,
            character_id=context.character_id,
        )
        current = self._current_states(visible, context, "thought_thread_key")[:5]
        events, event_failure = self._visible_linked_events(context, current)
        return WorldbookQueryResult[ThoughtMemory](
            items=[self._memory(item, events) for item in current],
            failure=batch.failure or event_failure,
            trace=RetrievalTrace(
                selected_entry_ids=[item.entry_id for item in current],
                candidates=batch.candidates,
            ),
        )

    def close(self) -> None:
        """释放运行时常驻的 embedding 资源。"""

        self._retrieval.close()

    def queryable_relation_targets(
        self,
        context: WorldbookResolvedContext,
    ) -> RelationTargetsQueryResult:
        """从当前进度已可见的关系条目派生可查询目标角色。"""

        batch = self._retrieval.scan_payloads(
            PayloadScanRequest(
                entry_type="character_relation",
                constraints=RetrievalConstraints(
                    context=context,
                    time_mode="started",
                    subject_character_id=context.character_id,
                ),
            )
        )
        candidates = [
            RetrievalCandidate(
                entry_id=record.entry_id,
                package_id=record.package_id,
                entry_type=record.entry_type,
                payload=record.payload,
                score=0.0,
                final_score=0.0,
            )
            for record in batch.records
        ]
        visible = self._visible_candidates(
            candidates,
            context,
            "character_relation",
            context.current_time,
            active=False,
            character_id=context.character_id,
        )
        targets: set[CharacterId] = set()
        for item in visible:
            raw_target = item.payload.get("object_character_id")
            if not isinstance(raw_target, str):
                continue
            try:
                target = CharacterId(raw_target)
            except ValueError:
                continue
            if target != context.character_id:
                targets.add(target)
        return RelationTargetsQueryResult(
            items=sorted(targets, key=lambda item: item.value),
            failure=batch.failure,
        )

    def _visible_linked_events(
        self,
        context: WorldbookResolvedContext,
        thoughts: list[RetrievalCandidate],
    ) -> tuple[dict[UUID, LinkedStoryEvent], RetrievalFailure | None]:
        """从权威有效条目按 Thought 显式 UUID 展开事件。"""

        requested_ids: set[UUID] = set()
        for thought in thoughts:
            raw_ids = thought.payload.get("story_event_entry_ids")
            if not isinstance(raw_ids, list):
                continue
            for raw_id in raw_ids:
                try:
                    requested_ids.add(UUID(str(raw_id)))
                except ValueError:
                    continue
        if not requested_ids:
            return {}, None
        try:
            effective = self._catalog.effective_entries(context)
        except WorldbookCatalogError as exc:
            return {}, RetrievalFailure(code="retrieval_failed", message=str(exc))
        events: dict[UUID, LinkedStoryEvent] = {}
        for item in effective:
            if item.entry.entry_type != "story_event" or item.entry.entry_id not in requested_ids:
                continue
            candidate = RetrievalCandidate(
                entry_id=item.entry.entry_id,
                package_id=item.package_id,
                entry_type="story_event",
                payload=item.entry.content,
                score=0.0,
                final_score=0.0,
            )
            if not self._is_visible(
                candidate,
                context,
                context.current_time,
                active=True,
            ):
                continue
            participants = item.entry.content.get("participants")
            raw_participants = participants if isinstance(participants, list) else []
            participant_names = [
                CharacterId(str(value)).common_name
                for value in raw_participants
                if isinstance(value, str)
                and value in {character.value for character in CharacterId}
            ]
            events[item.entry.entry_id] = LinkedStoryEvent(
                title=self._string_field(candidate, "title"),
                summary=self._string_field(candidate, "summary"),
                participant_names=participant_names,
            )
        return events, None

    def _visible_candidates(
        self,
        candidates: list[RetrievalCandidate],
        context: WorldbookResolvedContext,
        entry_type: EntryType,
        query_time: int,
        *,
        active: bool,
        character_id: CharacterId | None = None,
        target_character_id: CharacterId | None = None,
    ) -> list[RetrievalCandidate]:
        """再次执行完整本地硬过滤，防止 fake 或索引异常绕过约束。"""

        visible = [
            item
            for item in candidates
            if item.entry_type == entry_type
            and self._is_visible(item, context, query_time, active=active)
            and self._matches_perspective(item, character_id, target_character_id)
        ]
        visible.sort(key=lambda item: (-item.final_score, str(item.entry_id)))
        return visible

    def _is_visible(
        self,
        candidate: RetrievalCandidate,
        context: WorldbookResolvedContext,
        query_time: int,
        *,
        active: bool,
    ) -> bool:
        """执行包、时间轴、分支、时间、学年和 Lore scope 过滤。"""

        payload = candidate.payload
        if candidate.package_id not in context.package_ids:
            return False
        if payload.get("timeline_id") != context.timeline_id:
            return False
        if payload.get("canon_branch") != context.canon_branch.value:
            return False
        visible_from = payload.get("visible_from")
        visible_to = payload.get("visible_to")
        if isinstance(visible_from, int) and visible_from > query_time:
            return False
        if active and isinstance(visible_to, int) and visible_to < query_time:
            return False
        if candidate.entry_type != "lore_entry":
            return True
        years = payload.get("applicable_story_years")
        if isinstance(years, list) and years:
            if context.story_year is None or context.story_year not in years:
                return False
        scope = payload.get("scope_type")
        if scope == ScopeType.PACKAGE.value:
            return True
        if scope != ScopeType.SERIES.value:
            return False
        series_ids = payload.get("series_ids")
        return isinstance(series_ids, list) and context.series_id.value in series_ids

    def _matches_perspective(
        self,
        candidate: RetrievalCandidate,
        character_id: CharacterId | None,
        target_character_id: CharacterId | None,
    ) -> bool:
        """强制 Thought 与 Relation 使用当前角色主观视角。"""

        payload = candidate.payload
        if character_id is not None:
            field = (
                "character_id"
                if candidate.entry_type == "character_thought"
                else "subject_character_id"
            )
            if payload.get(field) != character_id.value:
                return False
        if (
            target_character_id is not None
            and payload.get("object_character_id") != target_character_id.value
        ):
            return False
        return True

    def _current_states(
        self,
        candidates: list[RetrievalCandidate],
        context: WorldbookResolvedContext,
        key_field: str,
    ) -> list[RetrievalCandidate]:
        """让依赖方向较新的包覆盖共享 key 的开放旧状态。"""

        grouped: dict[str, list[RetrievalCandidate]] = defaultdict(list)
        for candidate in candidates:
            key = candidate.payload.get(key_field)
            grouped[str(key or candidate.entry_id)].append(candidate)
        selected: list[RetrievalCandidate] = []
        for states in grouped.values():
            states.sort(
                key=lambda item: (
                    context.package_depths.get(item.package_id, 999),
                    -self._int_field(item, "visible_from"),
                    -item.final_score,
                    str(item.entry_id),
                )
            )
            selected.append(states[0])
        selected.sort(key=lambda item: (-item.final_score, str(item.entry_id)))
        return selected

    def _relation_query_time(
        self,
        context: WorldbookResolvedContext,
        episode: int | None,
    ) -> tuple[int, RetrievalFailure | None]:
        """解析关系 current/at-episode 的时间且拒绝未来集。"""

        if episode is None:
            return context.current_time, None
        if not 1 <= episode <= context.episode:
            return context.current_time, RetrievalFailure(
                code="invalid_request",
                message="只能查询当前剧情进度及以前的集数",
            )
        return (
            encode_story_time(
                context.series_id,
                StoryTimeCoordinate(
                    episode=episode,
                    episode_offset=EPISODE_SLOT_COUNT - 1,
                ),
            ),
            None,
        )

    def _direct_thought(self, candidate: RetrievalCandidate) -> DirectThought:
        """投影模型安全的直接观点字段。"""

        character = CharacterId(self._string_field(candidate, "character_id"))
        return DirectThought(
            character_name=character.common_name,
            thought_text=self._string_field(candidate, "thought_text"),
            epistemic_status=self._string_field(candidate, "epistemic_status"),
        )

    def _lore(self, candidate: RetrievalCandidate) -> LoreKnowledge:
        """投影模型安全的 Lore 字段。"""

        return LoreKnowledge(
            title=self._string_field(candidate, "title"),
            content=self._string_field(candidate, "content"),
        )

    def _relation(
        self,
        candidate: RetrievalCandidate,
        target: CharacterId,
    ) -> RelationKnowledge:
        """投影模型安全的角色关系字段。"""

        speech_hint = self._optional_string_field(candidate, "speech_hint")
        nickname = self._optional_string_field(candidate, "object_character_nickname")
        return RelationKnowledge(
            target_character_name=target.common_name,
            state_summary=self._string_field(candidate, "state_summary"),
            speech_hint=speech_hint,
            object_character_nickname=nickname,
        )

    def _memory(
        self,
        candidate: RetrievalCandidate,
        events: dict[UUID, LinkedStoryEvent],
    ) -> ThoughtMemory:
        """投影模型安全的观点记忆和关联事件。"""

        raw_ids = candidate.payload.get("story_event_entry_ids")
        event_items: list[LinkedStoryEvent] = []
        if isinstance(raw_ids, list):
            for raw_id in raw_ids:
                try:
                    event = events.get(UUID(str(raw_id)))
                except ValueError:
                    continue
                if event is not None:
                    event_items.append(event)
        character = CharacterId(self._string_field(candidate, "character_id"))
        return ThoughtMemory(
            character_name=character.common_name,
            thought_text=self._string_field(candidate, "thought_text"),
            epistemic_status=self._string_field(candidate, "epistemic_status"),
            events=event_items,
        )

    def _string_field(self, candidate: RetrievalCandidate, field_name: str) -> str:
        """读取候选中的必填字符串字段。"""

        value = candidate.payload.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"检索结果缺少字符串字段 {field_name}")
        return value

    def _optional_string_field(
        self,
        candidate: RetrievalCandidate,
        field_name: str,
    ) -> str | None:
        """读取候选中的可空字符串字段并把空串收敛为空。"""

        value = candidate.payload.get(field_name)
        return value if isinstance(value, str) and value else None

    def _int_field(self, candidate: RetrievalCandidate, field_name: str) -> int:
        """读取候选中的整数排序字段。"""

        value = candidate.payload.get(field_name)
        return value if isinstance(value, int) else -1
