"""真实 multilingual-e5-small、Qdrant 与检索质量门测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from rag.evaluation.runner import WorldbookEvaluationRunner, load_evaluation_cases
from rag.worldbook.adapters import create_default_registry
from rag.worldbook.models import IndexProjection, WorldbookReadiness
from rag.worldbook.package_loader import WorldbookPackageLoader
from rag.worldbook.paths import WorldbookPaths
from rag.worldbook.qdrant_index import QdrantWorldbookIndex
from rag.worldbook.runtime import (
    WorldbookIndexReadinessState,
    create_worldbook_conversation_service,
)
from rag.worldbook.runtime.catalog import WorldbookRootCatalog
from rag.worldbook.sync import WorldbookSyncCoordinator
from rag.worldbook.user_state import WorldbookUserStateRepository


MODEL_PATH = Path(__file__).resolve().parents[1] / "pretrained_models" / "multilingual-e5-small"
OFFICIAL_PATH = Path(__file__).resolve().parents[1] / "rag" / "worldbooks" / "official"


@unittest.skipUnless(MODEL_PATH.is_dir(), "本地 release embedding 模型不存在")
class WorldbookRealEmbeddingTest(unittest.TestCase):
    """验证真实模型的索引生命周期和小样本检索质量。"""

    def test_real_embedding_roundtrip(self) -> None:
        """真实向量写入后应可扫描 revision，并可精确删除。"""

        entry_id = uuid4()
        with tempfile.TemporaryDirectory() as directory:
            index = QdrantWorldbookIndex(Path(directory) / "qdrant", MODEL_PATH)
            try:
                fingerprint = index.index_fingerprint()
                projection = IndexProjection(
                    entry_id=entry_id,
                    package_id="test.package",
                    entry_type="story_event",
                    entry_revision="revision-1",
                    embedding_text="祥子退出乐队",
                    payload={"title": "祥子退出乐队"},
                )
                self.assertEqual(index.upsert([projection], fingerprint), 1)
                metadata = index.scan_metadata()
                self.assertEqual(metadata[entry_id].entry_revision, "revision-1")
                self.assertEqual(index.delete([entry_id]), 1)
                self.assertNotIn(entry_id, index.scan_metadata())
            finally:
                index.close()

    def test_official_package_full_reconcile(self) -> None:
        """正式 MyGO 开发包应能用真实模型完整建立四类索引。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = QdrantWorldbookIndex(root / "qdrant", MODEL_PATH)
            try:
                fingerprint = index.index_fingerprint()
                report = WorldbookSyncCoordinator(
                    WorldbookPackageLoader(OFFICIAL_PATH),
                    WorldbookUserStateRepository(root / "state"),
                    create_default_registry(),
                    index,
                    fingerprint,
                ).reconcile_all()

                self.assertTrue(report.success)
                self.assertEqual(report.indexed_count, 72)
                self.assertEqual(len(index.scan_metadata()), 72)
            finally:
                index.close()

    @unittest.skipUnless(
        os.environ.get("RUN_WORLDBOOK_REAL_EMBEDDING") == "1",
        "仅在发布前显式运行真实 embedding 质量门",
    )
    def test_reviewed_mygo_cases_meet_release_gate(self) -> None:
        """真实索引应达到 85% 通过率，且全部 safety/negative 案例通过。"""

        app_root = Path(__file__).resolve().parents[2]
        paths = WorldbookPaths(app_root)
        self.assertTrue(paths.index.is_dir(), "缺少已同步的世界书索引")
        cases = load_evaluation_cases(
            app_root
            / "GPT_SoVITS"
            / "rag"
            / "evaluation"
            / "cases"
            / "its_mygo.json"
        )
        catalog = WorldbookRootCatalog(paths.official_packages, paths.user_state)
        service = create_worldbook_conversation_service(
            app_root,
            WorldbookIndexReadinessState(WorldbookReadiness.READY),
        )
        try:
            report = WorldbookEvaluationRunner(catalog, service).run(
                cases,
                backend_label="multilingual-e5-small",
            )
        finally:
            service.close()

        safety_results = [
            result
            for result in report.results
            if result.case_id.startswith(("safety-", "negative-"))
        ]
        self.assertGreaterEqual(report.pass_rate, 0.85, report.model_dump_json(indent=2))
        self.assertTrue(
            all(result.passed for result in safety_results),
            report.model_dump_json(indent=2),
        )


if __name__ == "__main__":
    unittest.main()
