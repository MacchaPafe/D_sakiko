"""世界书包加载、用户状态和有效条目测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from rag.worldbook.adapters import create_default_registry
from rag.worldbook.effective_entries import entry_revision, merge_effective_entries
from rag.worldbook.hashing import file_sha256
from rag.worldbook.models import (
    ContentFileRecord,
    EffectiveWorldbookEntry,
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

    def test_orphan_override_is_silently_inactive(self) -> None:
        """官方条目消失后 Override 应保留在用户状态但不阻塞合并。"""

        missing_id = uuid4()
        state = WorldbookUserState(
            package_id="test.package",
            overrides=[
                WorldbookOverride(
                    entry_id=missing_id,
                    entry_type="story_event",
                    schema_version=0,
                    base_revision="old-revision",
                    content=_story_entry().content,
                )
            ],
        )

        effective, issues = merge_effective_entries("test.package", [], state)

        self.assertEqual(effective, [])
        self.assertEqual(issues, [])
        self.assertEqual(state.overrides[0].entry_id, missing_id)

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

    def test_formal_character_relation_schema_omits_label(self) -> None:
        """正式 Relation Schema 应使用稳定类型 UUID 而非语义标签。"""

        entry = WorldbookEntry(
            entry_id=uuid4(),
            entry_type="character_relation",
            content={
                "subject_character_id": "anon",
                "object_character_id": "soyo",
                "series_id": "its_mygo",
                "timeline_id": "main",
                "canon_branch": "main",
                "relation_type_key": str(uuid4()),
                "state_summary": "爱音把素世视为重要的乐队伙伴。",
                "speech_hint": "语气亲近。",
                "object_character_nickname": "素世同学",
                "visible_from": 4001,
                "visible_to": 4999,
                "tags": ["伙伴"],
                "retrieval_text": "爱音与素世的伙伴关系。",
            },
        )
        effective = EffectiveWorldbookEntry(
            package_id="test.package",
            entry=entry,
            revision=entry_revision(entry),
            source="official",
        )

        projection = create_default_registry().projection(effective)

        self.assertNotIn("relation_label", projection.payload)
        self.assertEqual(projection.payload["state_summary"], "爱音把素世视为重要的乐队伙伴。")

    def test_character_thought_is_a_first_class_worldbook_entry(self) -> None:
        """Character Thought 应能通过正式 Schema 校验并生成索引投影。"""

        event_id = uuid4()
        thread_id = uuid4()
        entry = WorldbookEntry(
            entry_id=uuid4(),
            entry_type="character_thought",
            content={
                "character_id": "tomori",
                "series_id": "its_mygo",
                "timeline_id": "main",
                "canon_branch": "main",
                "thought_thread_key": str(thread_id),
                "canonical_subject": "组建乐队",
                "thought_aspect": "是否能长久维持",
                "thought_text": "灯希望这支乐队能够持续一辈子。",
                "epistemic_status": "believes",
                "visible_from": 4001,
                "visible_to": 4999,
                "story_event_entry_ids": [str(event_id)],
                "tags": ["乐队"],
                "retrieval_text": "灯希望乐队持续一辈子。",
            },
        )
        effective = EffectiveWorldbookEntry(
            package_id="test.package",
            entry=entry,
            revision=entry_revision(entry),
            source="official",
        )

        projection = create_default_registry().projection(effective)

        self.assertEqual(projection.entry_type, "character_thought")
        self.assertEqual(projection.payload["thought_thread_key"], str(thread_id))
        self.assertEqual(projection.payload["story_event_entry_ids"], [str(event_id)])


if __name__ == "__main__":
    unittest.main()
