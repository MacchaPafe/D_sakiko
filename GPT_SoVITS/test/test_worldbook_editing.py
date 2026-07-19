"""世界书 Override 编辑服务与时间坐标测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import UUID, uuid4

from rag.worldbook.adapters import create_default_registry
from rag.worldbook.editing import (
    RetrievalSummaryReviewRequired,
    WorldbookEditService,
    WorldbookReferenceError,
    WorldbookSequenceConflict,
    build_package_entry_records,
    dependency_closure,
)
from rag.worldbook.effective_entries import entry_revision
from rag.worldbook.models import (
    PackageDependency,
    PackageLoadResult,
    PackageReadiness,
    WorldbookEntry,
    WorldbookManifest,
    WorldbookOverride,
    WorldbookTombstone,
)
from rag.worldbook.time_coordinates import (
    OPEN_ENDED_TIME,
    StoryTimeCoordinate,
    decode_story_time,
    encode_story_time,
    is_open_ended_time,
)
from rag.worldbook.user_state import WorldbookUserStateRepository


class WorldbookEditingTest(unittest.TestCase):
    """验证保存前校验、检索复核和时间序列约束。"""

    def test_story_time_coordinate_roundtrip_and_open_end(self) -> None:
        """时间坐标应稳定往返且开放上界不能伪装成具体时间点。"""

        coordinate = StoryTimeCoordinate(episode=2, episode_offset=16)
        encoded = encode_story_time("its_mygo", coordinate)

        self.assertEqual(encoded, 4066)
        self.assertEqual(decode_story_time("its_mygo", encoded), coordinate)
        self.assertTrue(is_open_ended_time(OPEN_ENDED_TIME))
        with self.assertRaises(ValueError):
            decode_story_time("its_mygo", OPEN_ENDED_TIME)

    def test_semantic_change_requires_retrieval_review_and_preserves_importance(self) -> None:
        """语义字段变化必须复核检索摘要，保存后保留隐藏元数据。"""

        official = _story_entry()
        content = {**official.content, "title": "修改后的事件"}
        with tempfile.TemporaryDirectory() as directory:
            service = WorldbookEditService(
                WorldbookUserStateRepository(Path(directory)), create_default_registry()
            )
            with self.assertRaises(RetrievalSummaryReviewRequired):
                service.save_override(
                    "test.package", official, official, content, [official], False
                )
            saved = service.save_override(
                "test.package", official, official, content, [official], True
            )

        self.assertEqual(saved.content["title"], "修改后的事件")
        self.assertEqual(saved.content["importance"], 7)

    def test_thought_reference_must_resolve_to_compatible_story(self) -> None:
        """角色想法不得引用时间线不相容或不存在的剧情事件。"""

        thought = _thought_entry(uuid4())
        content = dict(thought.content)
        content["story_event_entry_ids"] = [str(uuid4())]
        with tempfile.TemporaryDirectory() as directory:
            service = WorldbookEditService(
                WorldbookUserStateRepository(Path(directory)), create_default_registry()
            )
            with self.assertRaises(WorldbookReferenceError):
                service.save_override(
                    "test.package", thought, thought, content, [_story_entry(), thought], True
                )

    def test_overlapping_thought_state_is_rejected(self) -> None:
        """同一 Thought Thread 的闭区间状态不得重叠。"""

        thread_id = uuid4()
        first = _thought_entry(thread_id)
        second = _thought_entry(thread_id)
        second.content["visible_from"] = 4050
        second.content["visible_to"] = 999999
        with tempfile.TemporaryDirectory() as directory:
            service = WorldbookEditService(
                WorldbookUserStateRepository(Path(directory)), create_default_registry()
            )
            with self.assertRaises(WorldbookSequenceConflict):
                service.save_override(
                    "test.package", first, first, first.content, [first, second], True
                )

    def test_dependency_records_keep_real_owner_package(self) -> None:
        """根包视图应包含依赖条目并保留真实所属包。"""

        root_entry = _story_entry()
        common_entry = _story_entry()
        packages = {
            "root.package": PackageLoadResult(
                manifest=WorldbookManifest(
                    package_id="root.package",
                    package_version="1.0.0",
                    display_name="根包",
                    package_type="season",
                    timeline_id="bang_dream_original",
                    dependencies=[PackageDependency(package_id="common.package")],
                ),
                entries=[root_entry],
                readiness=PackageReadiness.READY,
            ),
            "common.package": PackageLoadResult(
                manifest=WorldbookManifest(
                    package_id="common.package",
                    package_version="1.0.0",
                    display_name="通用包",
                    package_type="common",
                    timeline_id="bang_dream_original",
                ),
                entries=[common_entry],
                readiness=PackageReadiness.READY,
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            records, issues = build_package_entry_records(
                packages, repository, "root.package", create_default_registry()
            )

        self.assertEqual(dependency_closure(packages, "root.package"), ["root.package", "common.package"])
        self.assertEqual(issues, [])
        self.assertEqual(
            {record.entry.entry_id: record.owner_package_id for record in records},
            {root_entry.entry_id: "root.package", common_entry.entry_id: "common.package"},
        )

    def test_incompatible_override_remains_navigable_with_issue(self) -> None:
        """不兼容 Override 应保留官方导航位置并携带修复问题。"""

        official = _story_entry()
        package = PackageLoadResult(
            manifest=WorldbookManifest(
                package_id="test.package",
                package_version="1.0.0",
                display_name="测试包",
                package_type="season",
                timeline_id="bang_dream_original",
            ),
            entries=[official],
            readiness=PackageReadiness.READY,
        )
        broken_content = dict(official.content)
        broken_content.pop("summary")
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.put_override(
                "test.package",
                WorldbookOverride(
                    entry_id=official.entry_id,
                    entry_type="story_event",
                    schema_version=0,
                    base_revision=entry_revision(official),
                    content=broken_content,
                ),
            )
            records, issues = build_package_entry_records(
                {"test.package": package},
                repository,
                "test.package",
                create_default_registry(),
            )

        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0].effective_entry)
        self.assertIsNotNone(records[0].issue)
        self.assertTrue(any(issue.code == "incompatible_user_entry" for issue in issues))

    def test_hide_restore_and_confirm_base_keep_override_content(self) -> None:
        """隐藏、恢复和确认新基准都不得丢失用户完整替换。"""

        official = _story_entry()
        override = WorldbookOverride(
            entry_id=official.entry_id,
            entry_type="story_event",
            schema_version=0,
            base_revision="old-revision",
            content={**official.content, "title": "用户标题"},
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.put_override("test.package", override)
            service = WorldbookEditService(repository, create_default_registry())
            service.hide_entry("test.package", official)
            hidden_state = repository.load("test.package")
            self.assertEqual(hidden_state.overrides[0].content["title"], "用户标题")
            self.assertEqual(len(hidden_state.tombstones), 1)

            service.restore_entry("test.package", official.entry_id)
            confirmed = service.confirm_current_base("test.package", official)
            restored_state = repository.load("test.package")

        self.assertEqual(restored_state.tombstones, [])
        self.assertEqual(restored_state.overrides[0].content["title"], "用户标题")
        self.assertEqual(confirmed.base_revision, entry_revision(official))

    def test_hidden_official_update_is_reported(self) -> None:
        """隐藏期间官方 revision 变化应在导航记录中提示复核。"""

        official = _story_entry()
        package = PackageLoadResult(
            manifest=WorldbookManifest(
                package_id="test.package",
                package_version="1.0.0",
                display_name="测试包",
                package_type="season",
                timeline_id="bang_dream_original",
            ),
            entries=[official],
            readiness=PackageReadiness.READY,
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.hide(
                "test.package",
                WorldbookTombstone(entry_id=official.entry_id, base_revision="old-revision"),
            )
            records, _issues = build_package_entry_records(
                {"test.package": package},
                repository,
                "test.package",
                create_default_registry(),
            )

        self.assertTrue(records[0].hidden)
        self.assertTrue(records[0].hidden_base_conflict)


def _story_entry() -> WorldbookEntry:
    """创建编辑服务测试使用的剧情事件。"""

    return WorldbookEntry(
        entry_id=uuid4(),
        entry_type="story_event",
        content={
            "timeline_id": "bang_dream_original",
            "occurred_story_year": None,
            "series_id": "its_mygo",
            "episode": 1,
            "time_order": 4000,
            "visible_from": 4000,
            "visible_to": 999999,
            "canon_branch": "main",
            "title": "测试事件",
            "summary": "测试摘要",
            "participants": ["anon"],
            "importance": 7,
            "tags": ["测试"],
            "retrieval_text": "测试事件的检索摘要",
        },
    )


def _thought_entry(thread_id: UUID) -> WorldbookEntry:
    """创建指定 Thread 身份的角色想法。"""

    return WorldbookEntry(
        entry_id=uuid4(),
        entry_type="character_thought",
        content={
            "character_id": "tomori",
            "series_id": "its_mygo",
            "timeline_id": "bang_dream_original",
            "canon_branch": "main",
            "thought_thread_key": str(thread_id),
            "canonical_subject": "组建乐队",
            "thought_aspect": "是否长久",
            "thought_text": "灯希望乐队长久维持。",
            "epistemic_status": "believes",
            "visible_from": 4000,
            "visible_to": 999999,
            "story_event_entry_ids": [],
            "tags": ["乐队"],
            "retrieval_text": "灯希望乐队长久维持。",
        },
    )


if __name__ == "__main__":
    unittest.main()
