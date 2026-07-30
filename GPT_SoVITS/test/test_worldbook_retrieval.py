"""世界书根包目录、只读仓库和对话检索服务测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import UUID, uuid4

from filelock import FileLock
from qdrant_client import QdrantClient, models as qdrant_models

from rag.models import CharacterId
from rag.worldbook.hashing import file_sha256
from rag.worldbook.index_schema import (
    INDEX_SCHEMA_VERSION,
    PAYLOAD_INDEXES,
    e5_passage_text,
    e5_query_text,
)
from rag.worldbook.models import (
    ContentFileRecord,
    EffectiveWorldbookEntry,
    EntryType,
    PackageDependency,
    WorldbookEntry,
    WorldbookManifest,
    WorldbookReadiness,
)
from rag.worldbook.runtime.catalog import WorldbookCatalogError, WorldbookRootCatalog
from rag.worldbook.runtime.models import (
    CharacterMemoryKnowledge,
    DirectWorldbookContext,
    PayloadBatch,
    PayloadRecord,
    RetrievalBatch,
    RetrievalCandidate,
    RetrievalFailure,
    WorldbookResolvedContext,
)
from rag.worldbook.runtime.retrieval import (
    PayloadScanRequest,
    RetrievalConstraints,
    SemanticSearchRequest,
    WorldbookRetrievalRepository,
)
from rag.worldbook.runtime.readiness import WorldbookIndexReadinessState
from rag.worldbook.runtime.service import WorldbookConversationService


class _FakeEmbedding:
    """记录查询前缀并返回固定测试向量。"""

    def __init__(self) -> None:
        """初始化调用记录。"""

        self.encoded: list[str] = []
        self.closed = False

    def encode_text(self, text: str) -> list[float]:
        """记录文本并返回二维单位向量。"""

        self.encoded.append(text)
        return [1.0, 0.0]

    def close(self) -> None:
        """记录模型已释放。"""

        self.closed = True


class _FakeRetrieval:
    """为服务测试返回预置候选并记录重复查询次数。"""

    def __init__(self) -> None:
        """创建空预置结果。"""

        self.semantic: dict[EntryType, list[RetrievalCandidate]] = {}
        self.failures: dict[EntryType, RetrievalFailure] = {}
        self.scanned: list[PayloadRecord] = []
        self.semantic_calls = 0

    def semantic_search(self, request: SemanticSearchRequest) -> RetrievalBatch:
        """返回指定条目类型的候选副本。"""

        self.semantic_calls += 1
        return RetrievalBatch(
            candidates=list(self.semantic.get(request.entry_type, [])),
            failure=self.failures.get(request.entry_type),
        )

    def scan_payloads(self, request: PayloadScanRequest) -> PayloadBatch:
        """返回预置 payload 记录。"""

        return PayloadBatch(records=list(self.scanned))

    def close(self) -> None:
        """测试仓库没有外部资源。"""


class _FakeCatalog:
    """为记忆展开提供预置权威有效条目。"""

    def __init__(self, entries: list[EffectiveWorldbookEntry]) -> None:
        """保存预置有效条目。"""

        self._entries = entries

    def effective_entries(
        self,
        context: WorldbookResolvedContext,
    ) -> list[EffectiveWorldbookEntry]:
        """返回预置有效条目副本。"""

        del context
        return list(self._entries)


def _context() -> WorldbookResolvedContext:
    """创建 MyGO 第 2 集结束时的测试上下文。"""

    return WorldbookResolvedContext(
        root_package_id="root",
        root_package_version="1.0.0",
        package_ids=["root", "base"],
        package_versions={"root": "1.0.0", "base": "1.0.0"},
        package_depths={"root": 0, "base": 1},
        character_id="anon",
        series_id="its_mygo",
        timeline_id="bang_dream_original",
        canon_branch="main",
        current_time=4099,
        story_year=3,
        episode=2,
    )


def _candidate(
    entry_type: EntryType,
    payload: dict[str, object],
    *,
    package_id: str = "root",
    score: float = 0.8,
    entry_id: UUID | None = None,
) -> RetrievalCandidate:
    """创建通过通用时间过滤的内部候选。"""

    complete = {
        "timeline_id": "bang_dream_original",
        "canon_branch": "main",
        "visible_from": 4000,
        "visible_to": 999999,
        **payload,
    }
    return RetrievalCandidate(
        entry_id=entry_id or uuid4(),
        package_id=package_id,
        entry_type=entry_type,
        payload=complete,
        score=score,
        final_score=score,
    )


class WorldbookRuntimeTest(unittest.TestCase):
    """验证运行时检索的关键安全与排序约束。"""

    def test_e5_prefix_contract_is_explicit(self) -> None:
        """索引正文和问题必须使用不同 E5 前缀并提升结构版本。"""

        self.assertEqual(e5_passage_text("正文"), "passage: 正文")
        self.assertEqual(e5_query_text("问题"), "query: 问题")
        self.assertGreaterEqual(INDEX_SCHEMA_VERSION, 4)
        self.assertIn(
            ("known_by_character_ids", "KEYWORD"),
            [
                (item.field_name, item.schema_name)
                for item in PAYLOAD_INDEXES["story_event"]
            ],
        )

    def test_catalog_resolves_fixed_episode_and_dependency_closure(self) -> None:
        """根包应解析依赖闭包与固定第 1～13 集结束坐标。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            official = root / "official"
            state = root / "state"
            self._write_package(official, "base", dependencies=[])
            self._write_package(
                official,
                "root",
                dependencies=[PackageDependency(package_id="base")],
            )
            catalog = WorldbookRootCatalog(official, state)

            context = catalog.resolve("root", 2, CharacterId.ANON)
            with self.assertRaises(WorldbookCatalogError):
                catalog.resolve("root", 14, CharacterId.ANON)

        self.assertEqual(context.package_ids, ["root", "base"])
        self.assertEqual(context.package_depths, {"root": 0, "base": 1})
        self.assertEqual(context.current_time, 4099)

    def test_story_event_filter_uses_known_by_array_membership(self) -> None:
        """Event 查询必须使用独立知情字段而不是参与者或关系主体字段。"""

        repository = WorldbookRetrievalRepository.__new__(
            WorldbookRetrievalRepository
        )
        query_filter = repository._qdrant_filter(
            "story_event",
            RetrievalConstraints(
                context=_context(),
                known_by_character_id=CharacterId.ANON,
            ),
        )
        payload = json.dumps(
            query_filter.model_dump(mode="json"),
            ensure_ascii=False,
        )

        self.assertIn("known_by_character_ids", payload)
        self.assertNotIn("subject_character_id", payload)
        self.assertNotIn('"participants"', payload)

    def test_same_layer_overlapping_state_key_disables_root(self) -> None:
        """同层依赖包复用并重叠同一关系 key 时根包必须禁用。"""

        relation_key = uuid4()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            official = root / "official"
            state = root / "state"
            relation = self._relation_entry(relation_key)
            self._write_package(official, "left", entries=[relation])
            self._write_package(
                official,
                "right",
                entries=[
                    WorldbookEntry(
                        entry_id=uuid4(),
                        entry_type=relation.entry_type,
                        content=dict(relation.content),
                    )
                ],
            )
            self._write_package(
                official,
                "root",
                dependencies=[
                    PackageDependency(package_id="left"),
                    PackageDependency(package_id="right"),
                ],
            )
            catalog = WorldbookRootCatalog(official, state)

            option = next(item for item in catalog.list_roots() if item.package_id == "root")

        self.assertFalse(option.enabled)
        self.assertTrue(any("ambiguous_state_key" in reason for reason in option.unavailable_reasons))

    def test_repository_uses_query_prefix_fails_fast_on_lock_and_closes_client(self) -> None:
        """只读仓库应使用 query 前缀、非阻塞锁并在调用后释放 Qdrant。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index_path = root / "index"
            lock_path = root / "worldbook.lock"
            client = QdrantClient(path=str(index_path))
            client.create_collection(
                "lore_entries",
                vectors_config=qdrant_models.VectorParams(
                    size=2,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
            client.upsert(
                "lore_entries",
                [
                    qdrant_models.PointStruct(
                        id=str(uuid4()),
                        vector=[1.0, 0.0],
                        payload={
                            "package_id": "root",
                            "timeline_id": "bang_dream_original",
                            "canon_branch": "main",
                            "scope_type": "package",
                            "title": "CRYCHIC",
                            "content": "旧乐队",
                            "tags": ["乐队"],
                        },
                    )
                ],
                wait=True,
            )
            client.close()
            embedding = _FakeEmbedding()
            readiness = WorldbookIndexReadinessState(WorldbookReadiness.READY)
            repository = WorldbookRetrievalRepository(
                index_path,
                lock_path,
                root / "unused-model",
                readiness,
                embedding=embedding,
            )
            request = SemanticSearchRequest(
                entry_type="lore_entry",
                query="CRYCHIC",
                constraints=self._constraints(),
                limit=1,
                candidate_limit=2,
                score_threshold=0.4,
                boost_source="CRYCHIC",
            )

            result = repository.semantic_search(request)
            reopened = QdrantClient(path=str(index_path))
            self.assertTrue(reopened.collection_exists("lore_entries"))
            reopened.close()
            held_lock = FileLock(str(lock_path))
            held_lock.acquire(timeout=0)
            try:
                locked = repository.semantic_search(request)
            finally:
                held_lock.release()
            readiness.update(WorldbookReadiness.UNAVAILABLE)
            unavailable = repository.semantic_search(request)
            repository.close()

        self.assertIsNone(result.failure)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(embedding.encoded, ["query: CRYCHIC"])
        self.assertEqual(locked.failure.code if locked.failure else None, "temporarily_unavailable")
        self.assertEqual(
            unavailable.failure.code if unavailable.failure else None,
            "index_unavailable",
        )
        self.assertTrue(embedding.closed)

    def test_service_enforces_perspective_scope_state_precedence_and_limits(self) -> None:
        """服务必须隔离视角、执行 Lore scope，并让后作状态覆盖依赖状态。"""

        retrieval = _FakeRetrieval()
        thoughts = [
            _candidate(
                "character_thought",
                {
                    "character_id": "anon",
                    "thought_thread_key": str(uuid4()),
                    "thought_text": f"观点 {index}",
                    "epistemic_status": "believes",
                },
                score=0.9 - index * 0.01,
            )
            for index in range(4)
        ]
        thoughts.append(
            _candidate(
                "character_thought",
                {
                    "character_id": "soyo",
                    "thought_thread_key": str(uuid4()),
                    "thought_text": "错误视角",
                    "epistemic_status": "believes",
                },
            )
        )
        retrieval.semantic["character_thought"] = thoughts
        retrieval.semantic["lore_entry"] = [
            _candidate(
                "lore_entry",
                {
                    "scope_type": "series",
                    "series_ids": ["its_mygo"],
                    "applicable_story_years": [3],
                    "title": "可见",
                    "content": "可见内容",
                },
            ),
            _candidate(
                "lore_entry",
                {
                    "scope_type": "series",
                    "series_ids": ["ave_mujica"],
                    "applicable_story_years": [3],
                    "title": "错误作品",
                    "content": "不可见",
                },
            ),
            _candidate(
                "lore_entry",
                {
                    "scope_type": "package",
                    "series_ids": None,
                    "applicable_story_years": [2],
                    "title": "错误学年",
                    "content": "不可见",
                },
            ),
        ]
        shared_key = str(uuid4())
        retrieval.semantic["character_relation"] = [
            _candidate(
                "character_relation",
                self._relation_payload(shared_key, "前作旧状态"),
                package_id="base",
                score=0.95,
            ),
            _candidate(
                "character_relation",
                self._relation_payload(shared_key, "后作新状态"),
                package_id="root",
                score=0.8,
            ),
        ]
        service = WorldbookConversationService(_FakeCatalog([]), retrieval)

        direct = service.direct_thoughts(_context(), "乐队", "乐队")
        lore = service.search_lore(_context(), "设定")
        relation = service.query_relation(_context(), CharacterId.SOYO, "")
        service.search_lore(_context(), "重复调用")

        self.assertEqual(len(direct.items), 3)
        self.assertTrue(all(item.character_name == "爱音" for item in direct.items))
        self.assertEqual([item.title for item in lore.items], ["可见"])
        self.assertEqual([item.state_summary for item in relation.items], ["后作新状态"])
        self.assertEqual(
            direct.trace.selected_entry_ids,
            [item.entry_id for item in thoughts[:3]],
        )
        self.assertEqual(
            relation.trace.selected_entry_ids,
            [retrieval.semantic["character_relation"][1].entry_id],
        )
        self.assertEqual(retrieval.semantic_calls, 4)

    def test_relation_targets_only_include_started_current_perspective_entries(self) -> None:
        """关系目标枚举不得泄露未来关系或其他角色视角。"""

        retrieval = _FakeRetrieval()
        retrieval.scanned = [
            PayloadRecord(
                entry_id=uuid4(),
                package_id="root",
                entry_type="character_relation",
                payload={
                    "timeline_id": "bang_dream_original",
                    "canon_branch": "main",
                    "visible_from": 4000,
                    "visible_to": 4010,
                    **self._relation_payload(str(uuid4()), "历史关系"),
                },
            ),
            PayloadRecord(
                entry_id=uuid4(),
                package_id="root",
                entry_type="character_relation",
                payload={
                    "timeline_id": "bang_dream_original",
                    "canon_branch": "main",
                    "visible_from": 4100,
                    "visible_to": 999999,
                    "subject_character_id": "anon",
                    "object_character_id": "taki",
                    "relation_type_key": str(uuid4()),
                    "state_summary": "未来关系",
                },
            ),
            PayloadRecord(
                entry_id=uuid4(),
                package_id="root",
                entry_type="character_relation",
                payload={
                    "timeline_id": "bang_dream_original",
                    "canon_branch": "main",
                    "visible_from": 4000,
                    "visible_to": 999999,
                    "subject_character_id": "soyo",
                    "object_character_id": "taki",
                    "relation_type_key": str(uuid4()),
                    "state_summary": "其他视角",
                },
            ),
        ]
        service = WorldbookConversationService(_FakeCatalog([]), retrieval)

        result = service.queryable_relation_targets(_context())

        self.assertIsNone(result.failure)
        self.assertEqual(result.items, [CharacterId.SOYO])

    def test_relation_history_is_newest_first_fixed_page(self) -> None:
        """关系历史必须倒序、固定五条并返回下一页标记。"""

        retrieval = _FakeRetrieval()
        retrieval.scanned = [
            PayloadRecord(
                entry_id=uuid4(),
                package_id="root",
                entry_type="character_relation",
                payload={
                    "timeline_id": "bang_dream_original",
                    "canon_branch": "main",
                    "visible_from": 4000 + index,
                    "visible_to": 999999,
                    **self._relation_payload(str(uuid4()), f"状态 {index}"),
                },
            )
            for index in range(6)
        ]
        service = WorldbookConversationService(_FakeCatalog([]), retrieval)

        result = service.relation_history(_context(), CharacterId.SOYO, page=1)

        self.assertEqual(len(result.page.items), 5)
        self.assertEqual(result.page.items[0].state_summary, "状态 5")
        self.assertTrue(result.page.has_more)
        self.assertEqual(result.page.next_page, 2)

    def test_memory_expands_only_explicit_visible_events(self) -> None:
        """记忆只补充展开获授权且没有越过当前进度的关联事件。"""

        visible_event_id = uuid4()
        future_event_id = uuid4()
        retrieval = _FakeRetrieval()
        retrieval.semantic["character_thought"] = [
            _candidate(
                "character_thought",
                {
                    "character_id": "anon",
                    "thought_thread_key": str(uuid4()),
                    "thought_text": "记得两件事",
                    "epistemic_status": "knows",
                    "story_event_entry_ids": [
                        str(visible_event_id),
                        str(future_event_id),
                    ],
                },
            )
        ]
        entries = [
            self._effective_event(visible_event_id, 4050, "已发生"),
            self._effective_event(future_event_id, 4100, "未来"),
        ]
        service = WorldbookConversationService(_FakeCatalog(entries), retrieval)

        result = service.search_memory(_context(), "发生了什么")

        self.assertIsInstance(result.knowledge, CharacterMemoryKnowledge)
        if not isinstance(result.knowledge, CharacterMemoryKnowledge):
            self.fail("记忆查询没有返回 CharacterMemoryKnowledge")
        self.assertEqual(len(result.knowledge.thoughts), 1)
        self.assertEqual(
            [item.title for item in result.knowledge.events],
            ["已发生"],
        )

    def test_direct_context_filters_event_permission_and_uses_fixed_quotas(self) -> None:
        """直接上下文只公开授权事件，并固定为三 Event 加两 Thought。"""

        retrieval = _FakeRetrieval()
        retrieval.semantic["character_thought"] = [
            _candidate(
                "character_thought",
                {
                    "character_id": "anon",
                    "thought_thread_key": str(uuid4()),
                    "thought_text": f"观点 {index}",
                    "epistemic_status": "believes",
                },
                score=0.95 - index * 0.01,
            )
            for index in range(4)
        ]
        retrieval.semantic["story_event"] = [
            _candidate(
                "story_event",
                {
                    "title": f"授权事件 {index}",
                    "summary": f"摘要 {index}",
                    "participants": ["tomori"],
                    "known_by_character_ids": ["anon"],
                },
                score=0.95 - index * 0.01,
            )
            for index in range(4)
        ] + [
            _candidate(
                "story_event",
                {
                    "title": "未授权参与事件",
                    "summary": "即使参与也不能公开",
                    "participants": ["anon"],
                    "known_by_character_ids": ["soyo"],
                },
                score=0.99,
            ),
            _candidate(
                "story_event",
                {
                    "title": "旧字段缺失事件",
                    "summary": "参与者也不能因缺字段而获得权限",
                    "participants": ["anon"],
                },
                score=0.985,
            ),
            _candidate(
                "story_event",
                {
                    "title": "未来事件",
                    "summary": "尚未发生",
                    "participants": ["anon"],
                    "known_by_character_ids": ["anon"],
                    "visible_from": 4100,
                },
                score=0.98,
            ),
        ]
        service = WorldbookConversationService(_FakeCatalog([]), retrieval)

        result = service.direct_context(_context(), "认识灯", "怎么认识灯")

        self.assertIsInstance(result.knowledge, DirectWorldbookContext)
        if not isinstance(result.knowledge, DirectWorldbookContext):
            self.fail("直接检索没有返回 DirectWorldbookContext")
        self.assertEqual(len(result.knowledge.events), 3)
        self.assertEqual(len(result.knowledge.thoughts), 2)
        self.assertTrue(
            all(item.title.startswith("授权事件") for item in result.knowledge.events)
        )
        self.assertEqual(result.knowledge.events[0].participant_names, ["灯"])

    def test_thought_link_cannot_grant_event_permission(self) -> None:
        """Thought 链接不得越过事件知情角色权限。"""

        event_id = uuid4()
        retrieval = _FakeRetrieval()
        retrieval.semantic["character_thought"] = [
            _candidate(
                "character_thought",
                {
                    "character_id": "anon",
                    "thought_thread_key": str(uuid4()),
                    "thought_text": "只知道自己的判断",
                    "epistemic_status": "believes",
                    "story_event_entry_ids": [str(event_id)],
                },
            )
        ]
        event = self._effective_event(
            event_id,
            4050,
            "不应展开",
            known_by=[CharacterId.SOYO],
        )
        service = WorldbookConversationService(_FakeCatalog([event]), retrieval)

        result = service.search_memory(_context(), "发生了什么")

        self.assertIsInstance(result.knowledge, CharacterMemoryKnowledge)
        if not isinstance(result.knowledge, CharacterMemoryKnowledge):
            self.fail("记忆查询没有返回 CharacterMemoryKnowledge")
        self.assertEqual(result.knowledge.events, [])
        self.assertEqual(len(result.knowledge.thoughts), 1)
        self.assertEqual(result.unauthorized_linked_event_ids, [event_id])

    def test_memory_uses_independent_five_plus_five_quotas(self) -> None:
        """记忆工具应独立保留最多五条 Event 与五条 Thought。"""

        retrieval = _FakeRetrieval()
        retrieval.semantic["character_thought"] = [
            _candidate(
                "character_thought",
                {
                    "character_id": "anon",
                    "thought_thread_key": str(uuid4()),
                    "thought_text": f"观点 {index}",
                    "epistemic_status": "believes",
                },
                score=0.95 - index * 0.01,
            )
            for index in range(7)
        ]
        retrieval.semantic["story_event"] = [
            _candidate(
                "story_event",
                {
                    "title": f"事件 {index}",
                    "summary": f"摘要 {index}",
                    "participants": ["anon"],
                    "known_by_character_ids": ["anon"],
                },
                score=0.95 - index * 0.01,
            )
            for index in range(7)
        ]
        service = WorldbookConversationService(_FakeCatalog([]), retrieval)

        result = service.search_memory(_context(), "过去发生了什么")

        self.assertIsInstance(result.knowledge, CharacterMemoryKnowledge)
        if not isinstance(result.knowledge, CharacterMemoryKnowledge):
            self.fail("记忆查询没有返回 CharacterMemoryKnowledge")
        self.assertEqual(len(result.knowledge.events), 5)
        self.assertEqual(len(result.knowledge.thoughts), 5)

    def test_independent_and_linked_event_hits_are_deduplicated(self) -> None:
        """同一 Event 被独立命中和 Thought 链接时只返回一次。"""

        event_id = uuid4()
        retrieval = _FakeRetrieval()
        retrieval.semantic["story_event"] = [
            _candidate(
                "story_event",
                {
                    "title": "同一事件",
                    "summary": "同一摘要",
                    "participants": ["anon"],
                    "known_by_character_ids": ["anon"],
                },
                entry_id=event_id,
            )
        ]
        retrieval.semantic["character_thought"] = [
            _candidate(
                "character_thought",
                {
                    "character_id": "anon",
                    "thought_thread_key": str(uuid4()),
                    "thought_text": "关联观点",
                    "epistemic_status": "knows",
                    "story_event_entry_ids": [str(event_id)],
                },
            )
        ]
        service = WorldbookConversationService(
            _FakeCatalog([self._effective_event(event_id, 4050, "同一事件")]),
            retrieval,
        )

        result = service.search_memory(_context(), "同一事件")

        self.assertIsInstance(result.knowledge, CharacterMemoryKnowledge)
        if not isinstance(result.knowledge, CharacterMemoryKnowledge):
            self.fail("记忆查询没有返回 CharacterMemoryKnowledge")
        self.assertEqual(len(result.knowledge.events), 1)
        self.assertEqual(result.deduplicated_event_ids, [event_id])

    def test_event_failure_keeps_thought_results(self) -> None:
        """Event 来源失败时仍应返回成功的 Thought 结果。"""

        retrieval = _FakeRetrieval()
        retrieval.failures["story_event"] = RetrievalFailure(
            code="retrieval_failed",
            message="event unavailable",
        )
        retrieval.semantic["character_thought"] = [
            _candidate(
                "character_thought",
                {
                    "character_id": "anon",
                    "thought_thread_key": str(uuid4()),
                    "thought_text": "仍可使用的观点",
                    "epistemic_status": "knows",
                },
            )
        ]
        service = WorldbookConversationService(_FakeCatalog([]), retrieval)

        result = service.direct_context(_context(), "问题", "问题")

        self.assertIsInstance(result.knowledge, DirectWorldbookContext)
        if not isinstance(result.knowledge, DirectWorldbookContext):
            self.fail("直接检索没有返回 DirectWorldbookContext")
        self.assertEqual(len(result.knowledge.thoughts), 1)
        self.assertEqual(result.knowledge.events, [])
        self.assertEqual(
            [item.source for item in result.source_failures],
            ["event"],
        )

    def test_thought_failure_keeps_event_results(self) -> None:
        """Thought 来源失败时仍应返回成功的授权 Event 结果。"""

        retrieval = _FakeRetrieval()
        retrieval.failures["character_thought"] = RetrievalFailure(
            code="retrieval_failed",
            message="thought unavailable",
        )
        retrieval.semantic["story_event"] = [
            _candidate(
                "story_event",
                {
                    "title": "仍可使用的事件",
                    "summary": "事件来源成功。",
                    "participants": ["anon"],
                    "known_by_character_ids": ["anon"],
                },
            )
        ]
        service = WorldbookConversationService(_FakeCatalog([]), retrieval)

        result = service.direct_context(_context(), "问题", "问题")

        self.assertIsInstance(result.knowledge, DirectWorldbookContext)
        if not isinstance(result.knowledge, DirectWorldbookContext):
            self.fail("直接检索没有返回 DirectWorldbookContext")
        self.assertEqual(len(result.knowledge.events), 1)
        self.assertEqual(result.knowledge.thoughts, [])
        self.assertEqual(
            [item.source for item in result.source_failures],
            ["thought"],
        )

    def _constraints(self) -> RetrievalConstraints:
        """创建仓库测试使用的检索约束。"""

        return RetrievalConstraints(context=_context())

    def _write_package(
        self,
        official_root: Path,
        package_id: str,
        *,
        dependencies: list[PackageDependency] | None = None,
        entries: list[WorldbookEntry] | None = None,
    ) -> None:
        """写入一个合成季度包。"""

        package_dir = official_root / package_id
        package_dir.mkdir(parents=True, exist_ok=True)
        content_files: list[ContentFileRecord] = []
        grouped: dict[EntryType, list[WorldbookEntry]] = {}
        for entry in entries or []:
            grouped.setdefault(entry.entry_type, []).append(entry)
        for entry_type, typed_entries in grouped.items():
            content_dir = package_dir / "content"
            content_dir.mkdir(parents=True, exist_ok=True)
            path = content_dir / f"{entry_type}.json"
            path.write_text(
                json.dumps(
                    {"entries": [item.model_dump(mode="json") for item in typed_entries]}
                ),
                encoding="utf-8",
            )
            content_files.append(
                ContentFileRecord(
                    path=f"content/{entry_type}.json",
                    sha256=file_sha256(path),
                    entry_type=entry_type,
                )
            )
        manifest = WorldbookManifest(
            package_id=package_id,
            package_version="1.0.0",
            display_name=package_id,
            package_type="season",
            timeline_id="bang_dream_original",
            conversation_context={
                "series_id": "its_mygo",
                "canon_branch": "main",
                "story_year": 3,
            },
            dependencies=dependencies or [],
            content_files=content_files,
        )
        (package_dir / "manifest.json").write_text(
            json.dumps(manifest.model_dump(mode="json")),
            encoding="utf-8",
        )

    def _relation_entry(self, relation_key: UUID) -> WorldbookEntry:
        """创建用于跨包冲突测试的关系条目。"""

        return WorldbookEntry(
            entry_id=uuid4(),
            entry_type="character_relation",
            content={
                **self._relation_payload(str(relation_key), "关系"),
                "series_id": "its_mygo",
                "timeline_id": "bang_dream_original",
                "canon_branch": "main",
                "visible_from": 4000,
                "visible_to": 999999,
                "tags": [],
                "retrieval_text": "关系",
            },
        )

    def _relation_payload(self, relation_key: str, summary: str) -> dict[str, object]:
        """创建角色关系候选的核心 payload。"""

        return {
            "subject_character_id": "anon",
            "object_character_id": "soyo",
            "relation_type_key": relation_key,
            "state_summary": summary,
            "speech_hint": "",
            "object_character_nickname": "",
        }

    def _effective_event(
        self,
        entry_id: UUID,
        visible_from: int,
        title: str,
        *,
        known_by: list[CharacterId] | None = None,
    ) -> EffectiveWorldbookEntry:
        """创建权威有效剧情事件。"""

        entry = WorldbookEntry(
            entry_id=entry_id,
            entry_type="story_event",
            content={
                "timeline_id": "bang_dream_original",
                "occurred_story_year": 3,
                "series_id": "its_mygo",
                "episode": 1,
                "time_order": 0,
                "visible_from": visible_from,
                "visible_to": 999999,
                "canon_branch": "main",
                "title": title,
                "summary": f"{title}摘要",
                "participants": ["anon"],
                "known_by_character_ids": [
                    item.value for item in (known_by or [CharacterId.ANON])
                ],
                "importance": 1,
                "tags": [title],
                "retrieval_text": title,
            },
        )
        return EffectiveWorldbookEntry(
            package_id="root",
            entry=entry,
            revision="test",
            source="official",
        )


if __name__ == "__main__":
    unittest.main()
