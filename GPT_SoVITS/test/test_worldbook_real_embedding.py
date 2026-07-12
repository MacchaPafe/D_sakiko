"""真实 multilingual-e5-small 与本地 Qdrant 集成测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from rag.worldbook.models import IndexProjection
from rag.worldbook.adapters import create_default_registry
from rag.worldbook.package_loader import WorldbookPackageLoader
from rag.worldbook.qdrant_index import QdrantWorldbookIndex
from rag.worldbook.sync import WorldbookSyncCoordinator
from rag.worldbook.user_state import WorldbookUserStateRepository


MODEL_PATH = Path(__file__).resolve().parents[1] / "pretrained_models" / "multilingual-e5-small"
OFFICIAL_PATH = Path(__file__).resolve().parents[1] / "rag" / "worldbooks" / "official"


@unittest.skipUnless(MODEL_PATH.is_dir(), "本地 release embedding 模型不存在")
class WorldbookRealEmbeddingTest(unittest.TestCase):
    """验证真实模型能够完成世界书 point 写入、更新和删除。"""

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
                    embedding_text="query: 祥子退出乐队",
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
        """正式 MyGO 开发包应能用真实模型完整建立三类索引。"""

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
                self.assertEqual(report.indexed_count, 39)
                self.assertEqual(len(index.scan_metadata()), 39)
            finally:
                index.close()


if __name__ == "__main__":
    unittest.main()
