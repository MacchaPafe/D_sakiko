"""世界书版本化检索案例与 fake embedding runner 测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from qdrant_client import QdrantClient, models as qdrant_models

from rag.evaluation.models import (
    WorldbookEvaluationCase,
    WorldbookEvaluationCaseFile,
)
from rag.evaluation.runner import WorldbookEvaluationRunner, load_evaluation_cases
from rag.worldbook.index_schema import COLLECTION_NAMES
from rag.worldbook.models import WorldbookReadiness
from rag.worldbook.runtime.catalog import WorldbookRootCatalog
from rag.worldbook.runtime.readiness import WorldbookIndexReadinessState
from rag.worldbook.runtime.retrieval import WorldbookRetrievalRepository
from rag.worldbook.runtime.service import WorldbookConversationService


PACKAGE_ID = "official.bang_dream.its_mygo"


class _FakeEmbedding:
    """为所有测试查询返回同一个确定性单位向量。"""

    def encode_text(self, text: str) -> list[float]:
        """忽略查询内容并返回二维单位向量。"""

        del text
        return [1.0, 0.0]

    def close(self) -> None:
        """fake embedding 不持有资源。"""


class WorldbookEvaluationTest(unittest.TestCase):
    """验证案例 schema、模型可见结果映射和硬过滤。"""

    def test_official_case_file_is_strict_and_has_current_small_sample(self) -> None:
        """正式 MyGO 案例文件应可严格加载并包含约 10～15 条案例。"""

        case_path = (
            Path(__file__).resolve().parents[1]
            / "rag"
            / "evaluation"
            / "cases"
            / "its_mygo.json"
        )

        case_file = load_evaluation_cases(case_path)

        self.assertEqual(case_file.format_version, 1)
        self.assertGreaterEqual(len(case_file.cases), 10)
        self.assertLessEqual(len(case_file.cases), 15)

    def test_fake_embedding_runner_checks_hits_future_and_perspective(self) -> None:
        """fake embedding 验收应命中正例，并隔离未来与其他角色观点。"""

        lore_id = UUID("10000000-0000-0000-0000-000000000001")
        thought_id = UUID("10000000-0000-0000-0000-000000000002")
        future_id = UUID("10000000-0000-0000-0000-000000000003")
        other_character_id = UUID("10000000-0000-0000-0000-000000000004")
        selected_duplicate_id = UUID("10000000-0000-0000-0000-000000000005")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index_path = root / "index"
            client = QdrantClient(path=str(index_path))
            self._create_collection(client, "lore_entry")
            self._create_collection(client, "character_thought")
            client.upsert(
                COLLECTION_NAMES["lore_entry"],
                [
                    qdrant_models.PointStruct(
                        id=str(lore_id),
                        vector=[1.0, 0.0],
                        payload={
                            **self._base_payload(),
                            "scope_type": "series",
                            "series_ids": ["its_mygo"],
                            "applicable_story_years": [3],
                            "title": "CRYCHIC",
                            "content": "旧乐队",
                            "tags": ["CRYCHIC"],
                        },
                    )
                ],
                wait=True,
            )
            client.upsert(
                COLLECTION_NAMES["character_thought"],
                [
                    self._thought_point(
                        thought_id,
                        "anon",
                        4000,
                        "爱音想举办 Live",
                        thread_key=thought_id,
                    ),
                    self._thought_point(
                        selected_duplicate_id,
                        "anon",
                        4001,
                        "爱音想举办 Live",
                        thread_key=thought_id,
                    ),
                    self._thought_point(future_id, "anon", 4050, "未来观点"),
                    self._thought_point(
                        other_character_id,
                        "soyo",
                        4000,
                        "其他角色观点",
                    ),
                ],
                wait=True,
            )
            client.close()
            app_root = Path(__file__).resolve().parents[2]
            catalog = WorldbookRootCatalog(
                app_root / "GPT_SoVITS" / "rag" / "worldbooks" / "official",
                root / "state",
            )
            repository = WorldbookRetrievalRepository(
                index_path,
                root / "worldbook.lock",
                root / "unused-model",
                WorldbookIndexReadinessState(WorldbookReadiness.READY),
                embedding=_FakeEmbedding(),
            )
            service = WorldbookConversationService(catalog, repository)
            runner = WorldbookEvaluationRunner(catalog, service)
            report = runner.run(
                WorldbookEvaluationCaseFile(
                    dataset_id="fake",
                    cases=[
                        self._case(
                            "lore",
                            "lore",
                            expected=[lore_id],
                        ),
                        self._case(
                            "thought",
                            "direct_thought",
                            expected=[selected_duplicate_id],
                            forbidden=[thought_id],
                        ),
                        self._case(
                            "future",
                            "direct_thought",
                            expected=[selected_duplicate_id],
                            forbidden=[thought_id, future_id],
                        ),
                        self._case(
                            "perspective",
                            "direct_thought",
                            expected=[selected_duplicate_id],
                            forbidden=[thought_id, other_character_id],
                        ),
                    ],
                ),
                backend_label="fake-embedding",
            )
            service.close()

        self.assertEqual(report.passed, report.total)
        self.assertEqual(report.pass_rate, 1.0)

    @staticmethod
    def _create_collection(client: QdrantClient, entry_type: str) -> None:
        """创建一个二维余弦测试 collection。"""

        client.create_collection(
            COLLECTION_NAMES[entry_type],
            vectors_config=qdrant_models.VectorParams(
                size=2,
                distance=qdrant_models.Distance.COSINE,
            ),
        )

    @staticmethod
    def _base_payload() -> dict[str, object]:
        """创建所有测试点共享的世界书过滤字段。"""

        return {
            "package_id": PACKAGE_ID,
            "timeline_id": "bang_dream_original",
            "canon_branch": "main",
            "visible_from": None,
            "visible_to": None,
        }

    def _thought_point(
        self,
        entry_id: UUID,
        character_id: str,
        visible_from: int,
        text: str,
        *,
        thread_key: UUID | None = None,
    ) -> qdrant_models.PointStruct:
        """创建一个可由服务投影的 Character Thought 测试点。"""

        return qdrant_models.PointStruct(
            id=str(entry_id),
            vector=[1.0, 0.0],
            payload={
                **self._base_payload(),
                "visible_from": visible_from,
                "visible_to": 999999,
                "character_id": character_id,
                "thought_thread_key": str(thread_key or entry_id),
                "thought_text": text,
                "epistemic_status": "believes",
                "story_event_entry_ids": [],
            },
        )

    @staticmethod
    def _case(
        case_id: str,
        query_type: str,
        *,
        expected: list[UUID],
        forbidden: list[UUID] | None = None,
    ) -> WorldbookEvaluationCase:
        """创建 MyGO 第 1 集爱音视角的最小验收案例。"""

        return WorldbookEvaluationCase.model_validate(
            {
                "case_id": case_id,
                "description": case_id,
                "query_type": query_type,
                "root_package_id": PACKAGE_ID,
                "episode": 1,
                "character_id": "anon",
                "query": "测试查询",
                "expected_entry_ids": expected,
                "forbidden_entry_ids": forbidden or [],
            }
        )


if __name__ == "__main__":
    unittest.main()
