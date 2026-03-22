import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from qdrant_client import models as qdrant_models

from rag.models import (
    CanonBranch,
    CharacterId,
    CharacterRelationDocument,
    CharacterRelationQuery,
    CollectionName,
    LoreEntryDocument,
    LoreQuery,
    RetrievalContext,
    ScopeType,
    SeasonId,
    SeriesId,
    StoryEventDocument,
    StoryEventQuery,
)
from rag.services import (
    PointRecord,
    QdrantRagService,
    RagDatasetBundle,
    RagServiceConfig,
    RetrievalMode,
)


class FakeEmbeddingProvider:
    """用于测试的假 embedding provider。"""

    def __init__(self):
        """初始化假 provider 的内部状态。"""

        self.loaded = False

    def ensure_loaded(self):
        """模拟加载 embedding 模型。"""

        self.loaded = True

    def is_loaded(self):
        """返回当前 provider 是否已加载。"""

        return self.loaded

    def get_dimension(self):
        """返回测试用的固定向量维度。"""

        self.ensure_loaded()
        return 2

    def encode_text(self, text):
        """对单条文本生成稳定的测试向量。"""

        return self.encode_texts([text])[0]

    def encode_texts(self, texts, batch_size=None):
        """对多条文本生成稳定的测试向量。"""

        self.ensure_loaded()
        vectors = []
        for text in texts:
            if "退出" in text or "冲突" in text or "关系" in text or "小灯" in text:
                vectors.append([1.0, 0.0])
            elif "世界" in text or "术语" in text or "学校" in text:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.5, 0.5])
        return vectors

    def close(self):
        """模拟关闭 provider。"""

        self.loaded = False


class RagServiceTest(unittest.TestCase):
    """验证 Qdrant RAG service 的核心行为。"""

    def setUp(self):
        """为每个测试准备一个新的内存数据库 service。"""

        self.service = QdrantRagService(RagServiceConfig(qdrant_location=":memory:"))
        self.service._embedding_provider = FakeEmbeddingProvider()

        self.story_event = StoryEventDocument(
            season_id=SeasonId.THREE,
            series_id=SeriesId.ITS_MYGO,
            episode=1,
            time_order=10,
            visible_from=1,
            visible_to=100,
            canon_branch=CanonBranch.MAIN,
            title="祥子退出 CRYCHIC",
            summary="祥子宣布退出乐队，引发成员冲突。",
            participants=[CharacterId.SAKIKO, CharacterId.SOYO, CharacterId.TAKI, CharacterId.TOMORI],
            importance=1,
            tags=["CRYCHIC", "退出", "冲突"],
            retrieval_text="祥子宣布退出 CRYCHIC，引发素世和立希的冲突。",
        )
        self.relation_forward = CharacterRelationDocument(
            subject_character_id=CharacterId.ANON,
            object_character_id=CharacterId.TOMORI,
            season_id=SeasonId.THREE,
            series_id=SeriesId.ITS_MYGO,
            visible_from=1,
            visible_to=100,
            canon_branch=CanonBranch.MAIN,
            relation_label="关心鼓励",
            state_summary="爱音关心灯，并主动鼓励她组乐队。",
            speech_hint="轻松而主动",
            object_character_nickname="小灯",
            tags=["关心", "鼓励"],
            retrieval_text="爱音对灯的关系：会主动鼓励她，并称呼她为小灯。",
        )
        self.relation_reverse = CharacterRelationDocument(
            subject_character_id=CharacterId.TOMORI,
            object_character_id=CharacterId.ANON,
            season_id=SeasonId.THREE,
            series_id=SeriesId.ITS_MYGO,
            visible_from=1,
            visible_to=100,
            canon_branch=CanonBranch.MAIN,
            relation_label="信任依赖",
            state_summary="灯逐渐信任爱音，并愿意向她袒露心声。",
            speech_hint="小心但真诚",
            object_character_nickname="爱音",
            tags=["信任", "依赖"],
            retrieval_text="灯对爱音的关系：逐渐产生信任和依赖。",
        )
        self.lore_entry = LoreEntryDocument(
            scope_type=ScopeType.SERIES,
            series_ids=[SeriesId.ITS_MYGO],
            season_ids=[SeasonId.THREE],
            visible_from=None,
            visible_to=None,
            canon_branch=CanonBranch.MAIN,
            title="CRYCHIC",
            content="CRYCHIC 是 MyGO 时间线中的旧乐队。",
            retrieval_text="术语：CRYCHIC。它是 MyGO 时间线中与祥子等人有关的旧乐队。",
            tags=["CRYCHIC", "旧乐队", "术语"],
        )

    def tearDown(self):
        """在测试结束后释放 service。"""

        self.service.close()

    def _build_bundle(self):
        """构造一个用于初始化数据库的测试数据集。"""

        return RagDatasetBundle(
            story_events=[PointRecord(point_id=None, document=self.story_event)],
            character_relations=[
                PointRecord(point_id=None, document=self.relation_forward),
                PointRecord(point_id=None, document=self.relation_reverse),
            ],
            lore_entries=[PointRecord(point_id=None, document=self.lore_entry)],
        )

    def test_create_database_creates_collections_and_generates_point_ids(self):
        """验证 create_database 会创建 collection、写入数据并自动生成 point id。"""

        report = self.service.create_database(self._build_bundle(), drop_existing=True)

        self.assertEqual(report.success_count, 4)
        self.assertEqual(len(report.generated_point_ids), 4)
        self.assertIn(CollectionName.STORY_EVENTS.value, self.service.list_collections())
        self.assertEqual(self.service.count_points(CollectionName.STORY_EVENTS), 1)
        self.assertEqual(self.service.count_points(CollectionName.CHARACTER_RELATIONS), 2)
        self.assertEqual(self.service.count_points(CollectionName.LORE_ENTRIES), 1)

    def test_query_story_events_keyword_mode(self):
        """验证 story_events 支持纯关键词查询。"""

        self.service.create_database(self._build_bundle(), drop_existing=True)
        context = RetrievalContext(
            current_time=50,
            current_character_id=CharacterId.SAKIKO,
            current_series_id=SeriesId.ITS_MYGO,
            current_season_id=SeasonId.THREE,
            current_canon_branch=CanonBranch.MAIN,
        )
        results = self.service.query_story_events(
            query_text=None,
            context=context,
            options=StoryEventQuery(),
            query_mode=RetrievalMode.KEYWORD,
            tag_keywords=["退出"],
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source, RetrievalMode.KEYWORD.value)
        self.assertEqual(results[0].document.title, "祥子退出 CRYCHIC")

    def test_query_story_events_vector_mode(self):
        """验证 story_events 支持向量检索。"""

        self.service.create_database(self._build_bundle(), drop_existing=True)
        context = RetrievalContext(
            current_time=50,
            current_character_id=CharacterId.SAKIKO,
            current_series_id=SeriesId.ITS_MYGO,
            current_season_id=SeasonId.THREE,
            current_canon_branch=CanonBranch.MAIN,
        )
        results = self.service.query_story_events(
            query_text="祥子退出后大家发生冲突",
            context=context,
            options=StoryEventQuery(),
            query_mode=RetrievalMode.VECTOR,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source, RetrievalMode.VECTOR.value)
        self.assertEqual(results[0].document.summary, "祥子宣布退出乐队，引发成员冲突。")

    def test_query_character_relations_direct_mode(self):
        """验证 character_relations 支持小剧场 direct 模式。"""

        self.service.create_database(self._build_bundle(), drop_existing=True)
        context = RetrievalContext(
            current_time=50,
            current_series_id=SeriesId.ITS_MYGO,
            current_season_id=SeasonId.THREE,
            current_canon_branch=CanonBranch.MAIN,
        )
        options = CharacterRelationQuery(
            use_direct_pair_insert=True,
            direct_insert_pair=(CharacterId.ANON, CharacterId.TOMORI),
        )
        results = self.service.query_character_relations(
            query_text=None,
            context=context,
            options=options,
            query_mode=RetrievalMode.DIRECT,
        )

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.source == RetrievalMode.DIRECT.value for result in results))

    def test_query_lore_entries_keyword_mode(self):
        """验证 lore_entries 支持纯关键词查询。"""

        self.service.create_database(self._build_bundle(), drop_existing=True)
        context = RetrievalContext(
            current_time=50,
            current_series_id=SeriesId.ITS_MYGO,
            current_season_id=SeasonId.THREE,
            current_canon_branch=CanonBranch.MAIN,
        )
        results = self.service.query_lore_entries(
            query_text=None,
            context=context,
            options=LoreQuery(),
            query_mode=RetrievalMode.KEYWORD,
            tag_keywords=["CRYCHIC"],
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].document.title, "CRYCHIC")

    def test_delete_by_point_ids_removes_record(self):
        """验证可以通过 point id 精确删除记录。"""

        report = self.service.upsert_story_events([PointRecord(point_id="event_1", document=self.story_event)])
        self.assertEqual(report.success_count, 1)

        delete_report = self.service.delete_by_point_ids(CollectionName.STORY_EVENTS, ["event_1"])
        self.assertEqual(delete_report.deleted_count, 1)
        self.assertEqual(self.service.count_points(CollectionName.STORY_EVENTS), 0)

    def test_delete_by_filter_removes_matching_records(self):
        """验证可以通过 Qdrant filter 批量删除记录。"""

        self.service.create_database(self._build_bundle(), drop_existing=True)
        delete_filter = qdrant_models.Filter(
            must=[
                qdrant_models.FieldCondition(
                    key="series_id",
                    match=qdrant_models.MatchValue(value=SeriesId.ITS_MYGO.value),
                )
            ]
        )

        delete_report = self.service.delete_by_filter(CollectionName.STORY_EVENTS, delete_filter)

        self.assertEqual(delete_report.deleted_count, 1)
        self.assertEqual(self.service.count_points(CollectionName.STORY_EVENTS), 0)

    def test_query_all_returns_grouped_results(self):
        """验证 query_all 会按 collection 分组返回结果。"""

        self.service.create_database(self._build_bundle(), drop_existing=True)
        context = RetrievalContext(
            current_time=50,
            current_character_id=CharacterId.SAKIKO,
            current_series_id=SeriesId.ITS_MYGO,
            current_season_id=SeasonId.THREE,
            current_canon_branch=CanonBranch.MAIN,
        )
        results = self.service.query_all(
            query_text="祥子退出后的冲突",
            context=context,
            tag_keywords=["CRYCHIC"],
            query_modes={
                CollectionName.STORY_EVENTS: RetrievalMode.VECTOR,
                CollectionName.CHARACTER_RELATIONS: RetrievalMode.KEYWORD,
                CollectionName.LORE_ENTRIES: RetrievalMode.KEYWORD,
            },
            relation_options=CharacterRelationQuery(),
        )

        self.assertIn(CollectionName.STORY_EVENTS, results)
        self.assertIn(CollectionName.CHARACTER_RELATIONS, results)
        self.assertIn(CollectionName.LORE_ENTRIES, results)
        self.assertEqual(len(results[CollectionName.STORY_EVENTS]), 1)
        self.assertEqual(len(results[CollectionName.LORE_ENTRIES]), 1)


if __name__ == "__main__":
    unittest.main()
