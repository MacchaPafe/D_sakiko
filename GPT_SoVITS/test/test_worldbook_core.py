"""世界书包加载、用户状态和有效条目测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from rag.worldbook.effective_entries import entry_revision, merge_effective_entries
from rag.worldbook.hashing import file_sha256
from rag.worldbook.models import (
    ContentFileRecord,
    WorldbookEntry,
    WorldbookManifest,
    WorldbookOverride,
    WorldbookTombstone,
    WorldbookUserState,
)
from rag.worldbook.package_loader import WorldbookPackageLoader
from rag.worldbook.user_state import WorldbookUserStateRepository


def _story_entry() -> WorldbookEntry:
    """创建一条有效的测试剧情事件。"""

    return WorldbookEntry(
        entry_id=uuid4(),
        entry_type="story_event",
        content={
            "timeline_id": "test_timeline",
            "occurred_story_year": None,
            "series_id": "its_mygo",
            "episode": 1,
            "time_order": 1,
            "visible_from": 1,
            "visible_to": 99,
            "canon_branch": "main",
            "title": "测试事件",
            "summary": "测试摘要",
            "participants": ["anon"],
            "importance": 1,
            "tags": ["测试"],
            "retrieval_text": "测试事件摘要",
        },
    )


class WorldbookCoreTest(unittest.TestCase):
    """验证 JSON 权威层的核心不变量。"""

    def test_user_state_roundtrip_and_override_conflict(self) -> None:
        """用户状态应原子往返，并保留官方更新后的 base conflict。"""

        entry = _story_entry()
        override = WorldbookOverride(
            entry_id=entry.entry_id,
            entry_type=entry.entry_type,
            schema_version=0,
            base_revision="old-revision",
            content={**entry.content, "title": "用户标题"},
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.put_override("test.package", override)
            state = repository.load("test.package")

        effective, issues = merge_effective_entries("test.package", [entry], state)

        self.assertFalse(issues)
        self.assertEqual(effective[0].source, "override")
        self.assertTrue(effective[0].base_conflict)

    def test_tombstone_hides_official_entry(self) -> None:
        """tombstone 应从有效结果中移除官方条目。"""

        entry = _story_entry()
        state = WorldbookUserState(
            package_id="test.package",
            tombstones=[WorldbookTombstone(entry_id=entry.entry_id, base_revision=entry_revision(entry))],
        )

        effective, issues = merge_effective_entries("test.package", [entry], state)

        self.assertEqual(effective, [])
        self.assertEqual(issues, [])

    def test_loader_validates_manifest_hash_and_path(self) -> None:
        """加载器应接受摘要正确的包并拒绝路径逃逸。"""

        entry = _story_entry()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_dir = root / "test.package"
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

            result = WorldbookPackageLoader(root).discover()["test.package"]

        self.assertEqual(result.readiness.value, "ready")
        self.assertEqual(result.entries[0].entry_id, entry.entry_id)


if __name__ == "__main__":
    unittest.main()
