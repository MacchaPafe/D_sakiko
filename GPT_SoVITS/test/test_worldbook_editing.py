"""世界书 Override 编辑服务与时间坐标测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from rag.worldbook.adapters import create_default_registry
from rag.worldbook.editing import (
    PersistentEntryRecord,
    RetrievalSummaryReviewRequired,
    WorldbookEditService,
    WorldbookExtensionReferencedError,
    WorldbookReferenceError,
    WorldbookSequenceConflict,
    build_package_entry_records,
    build_persistent_entry_catalog,
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
    WorldbookUserState,
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

    def test_extension_draft_is_validated_saved_and_updated(self) -> None:
        """用户扩展应自动获得内部身份、基础检索文本并支持原位更新。"""

        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            service = WorldbookEditService(repository, create_default_registry())
            draft = service.create_extension_draft(
                "story_event",
                "bang_dream_original",
                "its_mygo",
                "main",
            )
            content = {
                **draft.content,
                "title": "用户事件",
                "summary": "用户补充的事件摘要",
                "participants": ["anon"],
                "tags": ["用户条目"],
            }
            candidate = draft.model_copy(update={"content": content})
            saved = service.save_extension("test.package", candidate, [])
            catalog = [
                PersistentEntryRecord(
                    owner_package_id="test.package",
                    entry=saved,
                    source="extension",
                )
            ]
            updated = service.save_extension(
                "test.package",
                saved.model_copy(
                    update={"content": {**saved.content, "title": "更新后的用户事件"}}
                ),
                catalog,
            )
            state = repository.load("test.package")

        self.assertEqual(saved.content["retrieval_text"], "用户补充的事件摘要")
        self.assertEqual(saved.content["importance"], 3)
        self.assertEqual(updated.entry_id, draft.entry_id)
        self.assertEqual(len(state.extensions), 1)
        self.assertEqual(state.extensions[0].content["title"], "更新后的用户事件")

    def test_all_non_story_extension_types_generate_transparent_retrieval_text(self) -> None:
        """想法、关系和名词解释草稿应补齐内部身份与确定性检索文本。"""

        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            service = WorldbookEditService(repository, create_default_registry())
            thought = service.create_extension_draft(
                "character_thought", "bang_dream_original", "its_mygo", "main"
            )
            thought.content.update(
                {
                    "character_id": "tomori",
                    "canonical_subject": "继续乐队",
                    "thought_aspect": "愿望",
                    "thought_text": "灯希望大家继续组一辈子乐队。",
                }
            )
            saved_thought = service.save_extension("test.package", thought, [])
            thought_catalog = [
                PersistentEntryRecord(
                    "test.package", saved_thought, "extension"
                )
            ]

            relation = service.create_extension_draft(
                "character_relation", "bang_dream_original", "its_mygo", "main"
            )
            relation.content.update(
                {
                    "subject_character_id": "anon",
                    "object_character_id": "tomori",
                    "state_summary": "爱音把灯视作重要的乐队伙伴。",
                    "speech_hint": "语气亲近。",
                }
            )
            saved_relation = service.save_extension(
                "test.package", relation, thought_catalog
            )
            relation_catalog = [
                *thought_catalog,
                PersistentEntryRecord(
                    "test.package", saved_relation, "extension"
                ),
            ]

            lore = service.create_extension_draft(
                "lore_entry", "bang_dream_original", "its_mygo", "main"
            )
            lore.content.update(
                {
                    "title": "RiNG",
                    "content": "MyGO!!!!! 经常活动的 Live House。",
                    "tags": ["地点"],
                }
            )
            saved_lore = service.save_extension(
                "test.package", lore, relation_catalog
            )

        self.assertEqual(
            saved_thought.content["retrieval_text"],
            "灯希望大家继续组一辈子乐队。",
        )
        UUID(str(saved_thought.content["thought_thread_key"]))
        self.assertEqual(
            saved_relation.content["retrieval_text"],
            "爱音把灯视作重要的乐队伙伴。 说话方式：语气亲近。",
        )
        UUID(str(saved_relation.content["relation_type_key"]))
        self.assertEqual(
            saved_lore.content["retrieval_text"],
            "RiNG：MyGO!!!!! 经常活动的 Live House。",
        )

    def test_story_deletion_cascades_all_safe_same_package_thought_states(self) -> None:
        """同包官方、Override、扩展和孤立修改应在一次保存中按规则处理。"""

        story = _story_entry()
        official_plain = _thought_entry(uuid4())
        official_plain.content["story_event_entry_ids"] = [str(story.entry_id)]
        official_overridden = _thought_entry(uuid4())
        override_entry = official_overridden.model_copy(
            update={
                "content": {
                    **official_overridden.content,
                    "thought_text": "用户保留的想法正文。",
                    "retrieval_text": "用户保留的想法正文。",
                    "story_event_entry_ids": [str(story.entry_id)],
                }
            }
        )
        extension = _thought_entry(uuid4())
        extension.content["story_event_entry_ids"] = [str(story.entry_id)]
        orphan = _thought_entry(uuid4()).model_copy(update={"entry_id": uuid4()})
        orphan.content["story_event_entry_ids"] = [str(story.entry_id)]
        package = _package_result(
            "test.package",
            [official_plain, official_overridden],
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.save(
                WorldbookUserState(
                    package_id="test.package",
                    extensions=[story, extension],
                    overrides=[
                        WorldbookOverride(
                            entry_id=official_overridden.entry_id,
                            entry_type="character_thought",
                            schema_version=0,
                            base_revision="仍需复核的旧基准",
                            content=override_entry.content,
                        ),
                        WorldbookOverride(
                            entry_id=orphan.entry_id,
                            entry_type="character_thought",
                            schema_version=0,
                            base_revision="已不存在的基准",
                            content=orphan.content,
                        ),
                    ],
                )
            )
            service = WorldbookEditService(repository, create_default_registry())
            catalog = build_persistent_entry_catalog(
                {"test.package": package},
                repository,
            )
            plan = service.plan_story_event_deletion(
                "test.package",
                story.entry_id,
                catalog,
            )
            with patch.object(repository, "save", wraps=repository.save) as save:
                service.apply_story_event_deletion(plan, catalog)
            state = repository.load("test.package")

        self.assertTrue(plan.can_apply)
        self.assertEqual(
            {impact.action for impact in plan.impacts},
            {
                "detach_reference",
                "create_official_override",
                "delete_orphan_override",
            },
        )
        self.assertEqual(save.call_count, 1)
        self.assertNotIn(story.entry_id, {item.entry_id for item in state.extensions})
        saved_extension = next(
            item for item in state.extensions if item.entry_id == extension.entry_id
        )
        self.assertEqual(saved_extension.content["story_event_entry_ids"], [])
        self.assertEqual(saved_extension.content["thought_text"], extension.content["thought_text"])
        override_map = {item.entry_id: item for item in state.overrides}
        self.assertNotIn(orphan.entry_id, override_map)
        self.assertEqual(
            override_map[official_overridden.entry_id].base_revision,
            "仍需复核的旧基准",
        )
        self.assertEqual(
            override_map[official_overridden.entry_id].content["story_event_entry_ids"],
            [],
        )
        self.assertEqual(
            override_map[official_plain.entry_id].base_revision,
            entry_revision(official_plain),
        )

    def test_incompatible_thoughts_are_deleted_or_restore_official_safely(self) -> None:
        """不兼容扩展应删除，不兼容 Override 仅在官方引用完整时恢复。"""

        story = _story_entry()
        official = _thought_entry(uuid4())
        broken_extension = _thought_entry(uuid4())
        broken_extension.content["story_event_entry_ids"] = [str(story.entry_id)]
        broken_extension.content.pop("thought_text")
        broken_override = _thought_entry(uuid4()).model_copy(
            update={"entry_id": official.entry_id}
        )
        broken_override.content["story_event_entry_ids"] = [str(story.entry_id)]
        broken_override.content.pop("thought_text")
        package = _package_result("test.package", [official])
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.save(
                WorldbookUserState(
                    package_id="test.package",
                    extensions=[story, broken_extension],
                    overrides=[
                        WorldbookOverride(
                            entry_id=official.entry_id,
                            entry_type="character_thought",
                            schema_version=0,
                            base_revision=entry_revision(official),
                            content=broken_override.content,
                        )
                    ],
                )
            )
            service = WorldbookEditService(repository, create_default_registry())
            catalog = build_persistent_entry_catalog(
                {"test.package": package},
                repository,
            )
            plan = service.plan_story_event_deletion(
                "test.package",
                story.entry_id,
                catalog,
            )
            service.apply_story_event_deletion(plan, catalog)
            state = repository.load("test.package")

        self.assertEqual(
            {impact.action for impact in plan.impacts},
            {"delete_incompatible_extension", "restore_official"},
        )
        self.assertEqual(state.extensions, [])
        self.assertEqual(state.overrides, [])

    def test_cross_package_reference_blocks_plan_without_writing(self) -> None:
        """其他包的角色想法引用应阻断删除，且应用器不得写入任何状态。"""

        story = _story_entry()
        thought = _thought_entry(uuid4())
        thought.content["story_event_entry_ids"] = [str(story.entry_id)]
        packages = {
            "story.package": _package_result("story.package", []),
            "thought.package": _package_result("thought.package", [thought]),
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.put_extension("story.package", story)
            service = WorldbookEditService(repository, create_default_registry())
            catalog = build_persistent_entry_catalog(packages, repository)
            plan = service.plan_story_event_deletion(
                "story.package",
                story.entry_id,
                catalog,
            )
            with patch.object(repository, "save", wraps=repository.save) as save:
                with self.assertRaises(WorldbookExtensionReferencedError):
                    service.apply_story_event_deletion(plan, catalog)
            remaining = repository.load("story.package").extensions

        self.assertFalse(plan.can_apply)
        self.assertEqual(plan.blockers[0].owner_package_id, "thought.package")
        self.assertEqual(save.call_count, 0)
        self.assertEqual(remaining, [story])

    def test_plan_rejects_cross_package_changes_made_during_confirmation(self) -> None:
        """确认期间其他包状态变化后，旧计划不得继续写入当前包。"""

        story = _story_entry()
        packages = {
            "story.package": _package_result("story.package", []),
            "other.package": _package_result("other.package", []),
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.put_extension("story.package", story)
            service = WorldbookEditService(repository, create_default_registry())
            initial_catalog = build_persistent_entry_catalog(packages, repository)
            plan = service.plan_story_event_deletion(
                "story.package",
                story.entry_id,
                initial_catalog,
            )
            repository.put_extension("other.package", _story_entry())
            changed_catalog = build_persistent_entry_catalog(packages, repository)
            with patch.object(repository, "save", wraps=repository.save) as save:
                with self.assertRaisesRegex(ValueError, "其他世界书内容"):
                    service.apply_story_event_deletion(plan, changed_catalog)
            remaining = repository.load("story.package").extensions

        self.assertEqual(save.call_count, 0)
        self.assertEqual(remaining, [story])

    def test_unrelated_incompatible_extension_does_not_block_event_deletion(self) -> None:
        """包中既有但不引用目标事件的隔离内容不应误阻断本次删除。"""

        story = _story_entry()
        unrelated = WorldbookEntry(
            entry_id=uuid4(),
            entry_type="lore_entry",
            content={"title": "缺少其他必填字段"},
        )
        package = _package_result("test.package", [])
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.save(
                WorldbookUserState(
                    package_id="test.package",
                    extensions=[story, unrelated],
                )
            )
            service = WorldbookEditService(repository, create_default_registry())
            catalog = build_persistent_entry_catalog(
                {"test.package": package},
                repository,
            )
            plan = service.plan_story_event_deletion(
                "test.package",
                story.entry_id,
                catalog,
            )
            service.apply_story_event_deletion(plan, catalog)
            state = repository.load("test.package")

        self.assertTrue(plan.can_apply)
        self.assertEqual(state.extensions, [unrelated])

    def test_incompatible_override_cannot_restore_dangling_official_thought(self) -> None:
        """删除不兼容 Override 会暴露悬空官方引用时应阻断全部级联写入。"""

        story = _story_entry()
        official = _thought_entry(uuid4())
        official.content["story_event_entry_ids"] = [str(story.entry_id)]
        broken = official.model_copy(deep=True)
        broken.content.pop("thought_text")
        package = _package_result("test.package", [official])
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.save(
                WorldbookUserState(
                    package_id="test.package",
                    extensions=[story],
                    overrides=[
                        WorldbookOverride(
                            entry_id=official.entry_id,
                            entry_type="character_thought",
                            schema_version=0,
                            base_revision=entry_revision(official),
                            content=broken.content,
                        )
                    ],
                )
            )
            service = WorldbookEditService(repository, create_default_registry())
            plan = service.plan_story_event_deletion(
                "test.package",
                story.entry_id,
                build_persistent_entry_catalog({"test.package": package}, repository),
            )

        self.assertFalse(plan.can_apply)
        self.assertIn("官方角色想法仍有无效引用", plan.blockers[0].detail)

    def test_restore_operations_keep_state_when_official_thought_reference_is_missing(self) -> None:
        """恢复官方内容或隐藏条目会暴露悬空引用时必须保留原用户状态。"""

        missing_story_id = uuid4()
        official = _thought_entry(uuid4())
        official.content["story_event_entry_ids"] = [str(missing_story_id)]
        override = WorldbookOverride(
            entry_id=official.entry_id,
            entry_type="character_thought",
            schema_version=0,
            base_revision=entry_revision(official),
            content={**official.content, "story_event_entry_ids": []},
        )
        package = _package_result("test.package", [official])
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.save(
                WorldbookUserState(
                    package_id="test.package",
                    overrides=[override],
                    tombstones=[
                        WorldbookTombstone(
                            entry_id=official.entry_id,
                            base_revision=entry_revision(official),
                        )
                    ],
                )
            )
            service = WorldbookEditService(repository, create_default_registry())
            catalog = build_persistent_entry_catalog(
                {"test.package": package},
                repository,
            )
            with self.assertRaises(WorldbookReferenceError):
                service.restore_official_content(
                    "test.package",
                    official,
                    catalog,
                )
            with self.assertRaises(WorldbookReferenceError):
                service.restore_hidden_entry(
                    "test.package",
                    official,
                    catalog,
                )
            state = repository.load("test.package")

        self.assertEqual(state.overrides, [override])
        self.assertEqual(len(state.tombstones), 1)

    def test_unreferenced_extension_is_permanently_deleted(self) -> None:
        """无反向引用的用户扩展应从包级用户状态中删除。"""

        story = _story_entry()
        catalog = [PersistentEntryRecord("test.package", story, "extension")]
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.put_extension("test.package", story)
            service = WorldbookEditService(repository, create_default_registry())
            service.delete_extension("test.package", story.entry_id, catalog)
            remaining = repository.load("test.package").extensions

        self.assertEqual(remaining, [])

    def test_global_catalog_blocks_reference_from_hidden_other_package(self) -> None:
        """其他包中已隐藏的官方想法仍应阻止删除其引用的用户事件。"""

        story = _story_entry()
        thought = _thought_entry(uuid4())
        thought.content["story_event_entry_ids"] = [str(story.entry_id)]
        packages = {
            "story.package": _package_result("story.package", []),
            "thought.package": _package_result("thought.package", [thought]),
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.put_extension("story.package", story)
            repository.hide(
                "thought.package",
                WorldbookTombstone(
                    entry_id=thought.entry_id,
                    base_revision=entry_revision(thought),
                ),
            )
            catalog = build_persistent_entry_catalog(packages, repository)
            service = WorldbookEditService(repository, create_default_registry())
            with self.assertRaises(WorldbookExtensionReferencedError):
                service.delete_extension("story.package", story.entry_id, catalog)

        hidden_thought = next(record for record in catalog if record.entry.entry_id == thought.entry_id)
        self.assertTrue(hidden_thought.hidden)

    def test_incompatible_extension_keeps_raw_navigation_record(self) -> None:
        """格式失效的用户扩展应保留原始内容和问题供 UI 查看与删除。"""

        broken = _story_entry()
        broken.content.pop("summary")
        package = _package_result("test.package", [])
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.put_extension("test.package", broken)
            records, issues = build_package_entry_records(
                {"test.package": package},
                repository,
                "test.package",
                create_default_registry(),
            )

        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0].effective_entry)
        self.assertEqual(records[0].raw_entry, broken)
        self.assertIsNotNone(records[0].issue)
        self.assertTrue(any(issue.code == "incompatible_user_entry" for issue in issues))

    def test_orphan_override_keeps_separate_catalog_layer_and_navigation_record(self) -> None:
        """孤立 Override 应保留原始层，并只作为不生效的用户内容导航。"""

        official = _story_entry()
        orphan = _story_entry().model_copy(update={"entry_id": uuid4()})
        package = _package_result("test.package", [official])
        with tempfile.TemporaryDirectory() as directory:
            repository = WorldbookUserStateRepository(Path(directory))
            repository.put_override(
                "test.package",
                WorldbookOverride(
                    entry_id=orphan.entry_id,
                    entry_type="story_event",
                    schema_version=0,
                    base_revision="missing-base",
                    content=orphan.content,
                ),
            )
            catalog = build_persistent_entry_catalog(
                {"test.package": package},
                repository,
            )
            records, issues = build_package_entry_records(
                {"test.package": package},
                repository,
                "test.package",
                create_default_registry(),
            )

        self.assertEqual(
            [(record.source, record.entry.entry_id) for record in catalog],
            [("official", official.entry_id), ("override", orphan.entry_id)],
        )
        orphan_record = next(record for record in records if record.orphaned_override)
        self.assertIsNone(orphan_record.official_entry)
        self.assertIsNone(orphan_record.effective_entry)
        self.assertEqual(orphan_record.raw_entry, orphan)
        self.assertTrue(orphan_record.has_override)
        self.assertEqual(issues, [])

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


def _package_result(package_id: str, entries: list[WorldbookEntry]) -> PackageLoadResult:
    """创建编辑服务测试使用的可用世界书包结果。"""

    return PackageLoadResult(
        manifest=WorldbookManifest(
            package_id=package_id,
            package_version="1.0.0",
            display_name=package_id,
            package_type="season",
            timeline_id="bang_dream_original",
        ),
        entries=entries,
        readiness=PackageReadiness.READY,
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
