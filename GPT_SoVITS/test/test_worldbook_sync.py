"""世界书 revision 对账测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rag.worldbook.adapters import create_default_registry
from rag.worldbook.hashing import file_sha256
from rag.worldbook.index import InMemoryWorldbookIndex
from rag.worldbook.models import ContentFileRecord, WorldbookEntry, WorldbookManifest
from rag.worldbook.package_loader import WorldbookPackageLoader
from rag.worldbook.sync import WorldbookSyncCoordinator
from rag.worldbook.user_state import WorldbookUserStateRepository

from test.test_worldbook_core import _story_entry


class WorldbookSyncTest(unittest.TestCase):
    """验证增量同步与指纹重建行为。"""

    def test_reconcile_is_incremental_and_fingerprint_can_rebuild(self) -> None:
        """第二次相同对账不应 upsert，指纹变化后应重建写入。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            official_root = root / "official"
            state_root = root / "state"
            self._write_package(official_root, _story_entry())
            index = InMemoryWorldbookIndex()
            first = WorldbookSyncCoordinator(WorldbookPackageLoader(official_root), WorldbookUserStateRepository(state_root), create_default_registry(), index, "fingerprint-a").reconcile_all()
            second = WorldbookSyncCoordinator(WorldbookPackageLoader(official_root), WorldbookUserStateRepository(state_root), create_default_registry(), index, "fingerprint-a").reconcile_all()
            third = WorldbookSyncCoordinator(WorldbookPackageLoader(official_root), WorldbookUserStateRepository(state_root), create_default_registry(), index, "fingerprint-b").reconcile_all()

        self.assertEqual(first.indexed_count, 1)
        self.assertEqual(second.indexed_count, 0)
        self.assertEqual(third.indexed_count, 1)

    def _write_package(self, official_root: Path, entry: WorldbookEntry) -> None:
        """写入同步测试使用的最小合法包。"""

        package_dir = official_root / "test.package"
        content_dir = package_dir / "content"
        content_dir.mkdir(parents=True)
        content_path = content_dir / "story_events.json"
        content_path.write_text(json.dumps({"entries": [entry.model_dump(mode="json")]}), encoding="utf-8")
        manifest = WorldbookManifest(
            package_id="test.package",
            package_version="1.0.0",
            display_name="测试包",
            package_type="season",
            timeline_id="test_timeline",
            content_files=[ContentFileRecord(path="content/story_events.json", sha256=file_sha256(content_path), entry_type="story_event")],
        )
        (package_dir / "manifest.json").write_text(json.dumps(manifest.model_dump(mode="json")), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
