"""Stage 3 审核操作与多文件工作区测试。"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from rag.models import CharacterId
from rag.pipeline.review_migration import build_source_fingerprint, safely_write_json_model
from rag.pipeline.schemas import Stage3ImportMetadata, StoryEventPayload
from rag.pipeline.stage3_document_models import (
    Stage3DocumentReviewArtifact,
    StoryEventReviewRecord,
)
from rag.pipeline.stage3_document_review import normalize_stage3_documents_from_files
from rag.pipeline.stage3_review_operations import (
    CompleteItemReviewCommand,
    ReplaceStoryDocumentCommand,
    RestoreGeneratedContentCommand,
    UpdateItemNotesCommand,
    apply_review_command,
)
from rag.pipeline.stage3_review_regeneration import Stage3ReviewRegenerator
from rag.pipeline.stage3_review_workspace import ReviewWorkspace
from rag.pipeline.stage3_source_revalidation import (
    build_source_projection,
    load_source_acceptance_log,
    source_acceptance_path,
    validate_current_references,
)
from rag.pipeline.stage3_thought_models import (
    Stage3ThoughtReviewArtifact,
    ThoughtAggregationMetadata,
    ThoughtStateDraft,
    ThoughtThreadContentDraft,
    ThoughtThreadReviewRecord,
)
from rag.worldbook.builder import load_build_spec


_CANDIDATE_ID = "story_candidate:11111111-1111-4111-8111-111111111111"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _story_payload(summary: str = "原始摘要") -> StoryEventPayload:
    """构造最小合法 Story Event 文档。"""

    return StoryEventPayload(
        timeline_id="bang_dream_original",
        occurred_story_year=3,
        series_id="its_mygo",
        episode=1,
        time_order=100,
        visible_from=100,
        visible_to=999999,
        canon_branch="main",
        title="测试事件",
        summary=summary,
        participants=[],
        importance=3,
        tags=[],
        retrieval_text=summary,
    )


def _document_artifact(stage2_path: Path, annotation_path: Path) -> Stage3DocumentReviewArtifact:
    """构造带真实来源摘要的最小 Story/Lore Review。"""

    record = StoryEventReviewRecord(
        candidate_id=_CANDIDATE_ID,
        source_scene_id="scene_001",
        source_local_id="event_001",
        confidence=0.9,
        generated_document=_story_payload(),
        review_basis_sha256="1" * 64,
    )
    return Stage3DocumentReviewArtifact(
        metadata=Stage3ImportMetadata(
            subtitle_path="ep01.ass",
            anime_title="It's MyGO!!!!!",
            series_id="its_mygo",
            timeline_id="bang_dream_original",
            story_year=3,
            canon_branch="main",
            episode=1,
            source_stage2_model="test-model",
            source_stage2_template_path="test-template",
        ),
        direct_sources=[
            build_source_fingerprint("stage2_input", stage2_path, 1),
            build_source_fingerprint("stage2a_annotation", annotation_path, 1),
        ],
        story_events=[record],
    )


def _write_json(path: Path, payload: object) -> None:
    """为测试写入格式化 JSON。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class TestStage3ReviewOperations(unittest.TestCase):
    """验证共享审核操作的完整快照与状态语义。"""

    def test_edit_reset_review_and_restore_generated_document(self) -> None:
        """编辑应撤销审批，恢复机器版本后仍需重新审核。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage2 = root / "stage2.json"
            annotation = root / "annotation.json"
            _write_json(stage2, {})
            _write_json(annotation, {})
            artifact = _document_artifact(stage2, annotation)
            completed = apply_review_command(
                artifact,
                CompleteItemReviewCommand(_CANDIDATE_ID, "publish"),
            )
            edited = apply_review_command(
                completed,
                ReplaceStoryDocumentCommand(_CANDIDATE_ID, _story_payload("人工摘要")),
            )
            record = edited.story_events[0]
            self.assertEqual(record.review_status, "unreviewed")
            self.assertIsNone(record.disposition)
            self.assertEqual(record.effective_document().summary, "人工摘要")
            restored = apply_review_command(
                edited,
                RestoreGeneratedContentCommand(_CANDIDATE_ID),
            )
            self.assertIsNone(restored.story_events[0].reviewed_document)
            self.assertEqual(restored.story_events[0].effective_document().summary, "原始摘要")


class TestReviewWorkspace(unittest.TestCase):
    """验证工作区草稿、保存、撤销和来源状态。"""

    def setUp(self) -> None:
        """创建一份只生成第 1 集 Review 的临时构建工作区。"""

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.stage2 = self.root / "stage2.json"
        self.stage2a = self.root / "stage2a.json"
        self.stage2b = self.root / "stage2b.json"
        _write_json(self.stage2, {})
        _write_json(self.stage2a, {})
        _write_json(self.stage2b, {})
        artifact = _document_artifact(self.stage2, self.stage2a)
        self.review_path = self.root / "reviews" / "ep01.json"
        _write_json(self.review_path, artifact.model_dump(mode="json"))
        self.build_spec = self.root / "worldbook_build.json"
        _write_json(
            self.build_spec,
            {
                "format_version": 0,
                "package_id": "official.test.mygo",
                "package_version": "0.1.0",
                "display_name": "Test MyGO",
                "package_type": "season",
                "series_id": "its_mygo",
                "timeline_id": "bang_dream_original",
                "canon_branch": "main",
                "story_year": 3,
                "dependencies": [],
                "episodes": [
                    {
                        "episode": 1,
                        "stage2_input": "stage2.json",
                        "stage2a_annotation": "stage2a.json",
                        "stage2b_annotation": "stage2b.json",
                        "rag_artifact": "reviews/ep01.json",
                    }
                ],
                "relation_review": "relation.json",
                "thought_review": "thought.json",
                "lore_decisions": "lore.json",
                "id_map": "ids.json",
                "official_root": "official",
                "build_root": "build",
                "build_report": "build/report.json",
            },
        )

    def tearDown(self) -> None:
        """释放临时工作区。"""

        self.temporary.cleanup()

    def test_missing_artifacts_do_not_block_workspace_start(self) -> None:
        """尚未生成的全量审核文件应显示为缺失占位。"""

        workspace = ReviewWorkspace(self.build_spec)
        self.assertIsNotNone(workspace.slots["document:1"].artifact)
        self.assertTrue(workspace.freshness("document:1").is_fresh)
        self.assertTrue(workspace.freshness("relation").missing)

    def test_apply_undo_redo_and_explicit_save(self) -> None:
        """命令只修改草稿，保存后才写入磁盘。"""

        workspace = ReviewWorkspace(self.build_spec)
        workspace.apply(
            "document:1",
            UpdateItemNotesCommand(_CANDIDATE_ID, "人工核对"),
        )
        self.assertTrue(workspace.slots["document:1"].dirty)
        original_payload = json.loads(self.review_path.read_text(encoding="utf-8"))
        self.assertIsNone(original_payload["story_events"][0]["review_notes"])
        self.assertTrue(workspace.undo("document:1"))
        self.assertTrue(workspace.redo("document:1"))
        workspace.save("document:1")
        saved_payload = json.loads(self.review_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_payload["story_events"][0]["review_notes"], "人工核对")

    def test_external_change_blocks_save(self) -> None:
        """加载后被外部修改的文件不得被草稿静默覆盖。"""

        workspace = ReviewWorkspace(self.build_spec)
        workspace.apply(
            "document:1",
            UpdateItemNotesCommand(_CANDIDATE_ID, "草稿"),
        )
        self.review_path.write_text(
            self.review_path.read_text(encoding="utf-8") + " ",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            workspace.save("document:1")

    def test_upstream_change_marks_document_stale(self) -> None:
        """直接来源变化后应立即报告文档审核产物过期。"""

        workspace = ReviewWorkspace(self.build_spec)
        _write_json(self.stage2a, {"changed": True})
        freshness = workspace.freshness("document:1")
        self.assertFalse(freshness.is_fresh)
        self.assertIn("stage2a_annotation@1", freshness.stale_sources)

    def test_equal_projection_can_refresh_sources_without_llm(self) -> None:
        """纯格式变化应自动验证、保留审核内容并刷新来源指纹。"""

        workspace = self._workspace_with_valid_document_sources()
        sidecar = source_acceptance_path(self.review_path)
        self.assertTrue(sidecar.exists())
        self.stage2a.write_text(
            self.stage2a.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )

        preview = workspace.preview_source_revalidation("document:1")

        self.assertEqual(preview.baseline_status, "ready")
        self.assertTrue(preview.projection_equal)
        self.assertTrue(preview.can_accept_automatically)
        result = workspace.accept_current_sources(preview)
        self.assertTrue(workspace.freshness("document:1").is_fresh)
        self.assertEqual(result.slot_key, "document:1")
        status, log = load_source_acceptance_log(self.review_path)
        self.assertEqual(status, "ready")
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.acceptances[-1].mode, "automatic")
        self.assertEqual(
            workspace.slots["document:1"].artifact.story_events[0].review_notes,
            None,
        )

    def test_missing_baseline_requires_manual_acceptance(self) -> None:
        """旧产物缺少投影基线时必须提供人工确认说明。"""

        workspace = self._workspace_with_valid_document_sources()
        source_acceptance_path(self.review_path).unlink()
        payload = json.loads(self.stage2a.read_text(encoding="utf-8"))
        payload["model"] = "changed-model-label"
        _write_json(self.stage2a, payload)
        preview = workspace.preview_source_revalidation("document:1")

        self.assertEqual(preview.baseline_status, "missing")
        self.assertFalse(preview.can_accept_automatically)
        with self.assertRaisesRegex(ValueError, "必须显式人工确认"):
            workspace.accept_current_sources(preview)
        with self.assertRaisesRegex(ValueError, "必须填写确认说明"):
            workspace.accept_current_sources(preview, force=True)

        workspace.accept_current_sources(
            preview,
            force=True,
            reason="人工确认模型标签变化不影响当前审核",
        )
        self.assertTrue(workspace.freshness("document:1").is_fresh)
        _, log = load_source_acceptance_log(self.review_path)
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.acceptances[-1].mode, "manual")
        self.assertIn("模型标签", log.acceptances[-1].reason or "")

    def test_projection_change_requires_force_and_records_all_differences(self) -> None:
        """消费投影变化应拒绝自动接受并保存完整字段差异。"""

        workspace = self._workspace_with_valid_document_sources()
        payload = json.loads(self.stage2a.read_text(encoding="utf-8"))
        payload["model"] = "changed-model-label"
        _write_json(self.stage2a, payload)

        preview = workspace.preview_source_revalidation("document:1")

        self.assertEqual(preview.baseline_status, "ready")
        self.assertFalse(preview.projection_equal)
        self.assertTrue(
            any(difference.path.endswith(".model") for difference in preview.differences)
        )
        with self.assertRaisesRegex(ValueError, "必须显式人工确认"):
            workspace.accept_current_sources(preview)
        workspace.accept_current_sources(
            preview,
            force=True,
            reason="人工确认来源标签变化不影响审核内容",
        )
        _, log = load_source_acceptance_log(self.review_path)
        assert log is not None
        self.assertEqual(
            len(log.acceptances[-1].differences),
            len(preview.differences),
        )

    def test_source_change_after_preview_blocks_acceptance(self) -> None:
        """确认前来源再次变化时必须要求重新生成差异预览。"""

        workspace = self._workspace_with_valid_document_sources()
        self.stage2a.write_text(
            self.stage2a.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        preview = workspace.preview_source_revalidation("document:1")
        self.stage2a.write_text(
            self.stage2a.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "预览后发生变化"):
            workspace.accept_current_sources(preview)

    def test_missing_source_cannot_be_manually_accepted(self) -> None:
        """缺失的直接来源必须阻断预览和人工覆盖。"""

        workspace = self._workspace_with_valid_document_sources()
        self.stage2a.unlink()

        with self.assertRaisesRegex(ValueError, "直接来源缺失"):
            workspace.preview_source_revalidation("document:1")

    def test_sidecar_failure_rolls_back_artifact_and_log(self) -> None:
        """人工确认记录写入失败时应恢复 artifact 与既有 sidecar。"""

        workspace = self._workspace_with_valid_document_sources()
        sidecar = source_acceptance_path(self.review_path)
        artifact_before = self.review_path.read_bytes()
        sidecar_before = sidecar.read_bytes()
        self.stage2a.write_text(
            self.stage2a.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        preview = workspace.preview_source_revalidation("document:1")
        calls = 0

        def fail_second_write(model: object, path: str | Path) -> None:
            """在 sidecar 写入阶段制造一次可恢复的 I/O 失败。"""

            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated sidecar failure")
            safely_write_json_model(model, path)

        with patch(
            "rag.pipeline.stage3_review_workspace.safely_write_json_model",
            side_effect=fail_second_write,
        ):
            with self.assertRaisesRegex(OSError, "simulated sidecar failure"):
                workspace.accept_current_sources(preview)

        self.assertEqual(self.review_path.read_bytes(), artifact_before)
        self.assertEqual(sidecar.read_bytes(), sidecar_before)

    def test_thought_projection_ignores_story_known_by_permissions(self) -> None:
        """只修改 Event 知情权限时 Thought 消费投影应保持一致。"""

        stage2_root = (
            _PROJECT_ROOT
            / "GPT_SoVITS"
            / "rag"
            / "pipeline"
            / "data"
            / "annotations_stage2"
        )
        shutil.copyfile(stage2_root / "ep01_stage2_input.json", self.stage2)
        shutil.copyfile(stage2_root / "ep01_pass2_raw.json", self.stage2a)
        shutil.copyfile(stage2_root / "ep01_pass2b_raw.json", self.stage2b)
        document, _ = normalize_stage3_documents_from_files(
            self.stage2,
            self.stage2a,
            self.review_path,
            fresh=True,
        )
        thought_path = self.root / "thought.json"
        thought = Stage3ThoughtReviewArtifact(
            metadata=ThoughtAggregationMetadata(
                anime_title="It's MyGO!!!!!",
                series_id="its_mygo",
                timeline_id="bang_dream_original",
                story_year=3,
                canon_branch="main",
                episodes=[1],
            ),
            direct_sources=[
                build_source_fingerprint("stage2_input", self.stage2, 1),
                build_source_fingerprint("stage2b_annotation", self.stage2b, 1),
                build_source_fingerprint("stage3_rag", self.review_path, 1),
            ],
            aggregation_model="test",
        )
        safely_write_json_model(thought, thought_path)
        build_payload = json.loads(self.build_spec.read_text(encoding="utf-8"))
        build_payload["thought_review"] = "thought.json"
        _write_json(self.build_spec, build_payload)
        workspace = ReviewWorkspace(self.build_spec)
        self.assertTrue(workspace.freshness("thought").is_fresh)
        document.story_events[0].generated_document.known_by_character_ids = [
            CharacterId.ANON
        ]
        safely_write_json_model(document, self.review_path)

        preview = workspace.preview_source_revalidation("thought")

        self.assertIn("stage3_rag@1", preview.stale_sources)
        self.assertTrue(preview.projection_equal)
        self.assertTrue(preview.can_accept_automatically)
        workspace.accept_current_sources(preview)
        self.assertTrue(workspace.freshness("thought").is_fresh)

    def test_dangling_published_thought_reference_cannot_be_accepted(self) -> None:
        """人工来源确认不得放行指向未发布 Event 的 Thought。"""

        state = ThoughtStateDraft(
            thought_state_id="thought_state:00000000-0000-4000-8000-000000000001",
            transition="acquired",
            thought_text="角色记得这件事。",
            epistemic_status="knows",
            visible_from=100,
            visible_to=999999,
            story_event_candidate_ids=[_CANDIDATE_ID],
            retrieval_text="角色对事件的记忆。",
        )
        thread = ThoughtThreadReviewRecord(
            thought_thread_id="thought_thread:00000000-0000-4000-8000-000000000001",
            character_id=CharacterId.ANON,
            series_id="its_mygo",
            timeline_id="bang_dream_original",
            canon_branch="main",
            generated_content=ThoughtThreadContentDraft(
                canonical_subject="测试事件",
                thought_aspect="记忆",
                states=[state],
            ),
            review_status="completed",
            disposition="publish",
            review_basis_sha256="2" * 64,
        )
        artifact = Stage3ThoughtReviewArtifact(
            metadata=ThoughtAggregationMetadata(
                anime_title="It's MyGO!!!!!",
                series_id="its_mygo",
                timeline_id="bang_dream_original",
                story_year=3,
                canon_branch="main",
                episodes=[1],
            ),
            aggregation_model="test",
            threads=[thread],
        )

        with self.assertRaisesRegex(ValueError, "未发布的 Story Event"):
            validate_current_references(
                load_build_spec(self.build_spec),
                "thought",
                artifact,
            )

    def test_lore_projection_ignores_story_event_changes(self) -> None:
        """Lore decisions 消费投影不应因 Story Event 文本变化而改变。"""

        resolved = load_build_spec(self.build_spec)
        fingerprints = [
            build_source_fingerprint("stage3_rag", self.review_path, 1)
        ]
        before = build_source_projection(
            resolved,
            "lore_decisions",
            fingerprints,
        )
        artifact = _document_artifact(self.stage2, self.stage2a)
        artifact.story_events[0].generated_document.summary = "修改后的事件摘要"
        safely_write_json_model(artifact, self.review_path)
        after = build_source_projection(
            resolved,
            "lore_decisions",
            [build_source_fingerprint("stage3_rag", self.review_path, 1)],
        )

        self.assertEqual(before.projection_sha256, after.projection_sha256)

    def test_document_regenerator_uses_build_spec_paths(self) -> None:
        """确定性重生成应直接读取 build spec 路径并写回对应 Review。"""

        stage2_root = (
            _PROJECT_ROOT
            / "GPT_SoVITS"
            / "rag"
            / "pipeline"
            / "data"
            / "annotations_stage2"
        )
        payload = json.loads(self.build_spec.read_text(encoding="utf-8"))
        episode = payload["episodes"][0]
        episode["stage2_input"] = str(stage2_root / "ep01_stage2_input.json")
        episode["stage2a_annotation"] = str(stage2_root / "ep01_pass2_raw.json")
        episode["rag_artifact"] = "reviews/regenerated.json"
        _write_json(self.build_spec, payload)
        result = Stage3ReviewRegenerator(self.build_spec).regenerate_document(1)
        self.assertEqual(result.slot_key, "document:1")
        self.assertTrue(result.output_path.exists())
        self.assertIsNotNone(result.migration_report)
        evidence = ReviewWorkspace(self.build_spec).evidence_context(
            {"ep01_s001"},
            {"ep01_u0005"},
            set(),
        )
        self.assertTrue(any("ep01_u0005" in line for line in evidence))
        self.assertTrue(any(line.startswith("★") for line in evidence))

    def _workspace_with_valid_document_sources(self) -> ReviewWorkspace:
        """用仓库内真实 Stage 2 样本替换最小测试来源并建立 fresh 基线。"""

        stage2_root = (
            _PROJECT_ROOT
            / "GPT_SoVITS"
            / "rag"
            / "pipeline"
            / "data"
            / "annotations_stage2"
        )
        shutil.copyfile(stage2_root / "ep01_stage2_input.json", self.stage2)
        shutil.copyfile(stage2_root / "ep01_pass2_raw.json", self.stage2a)
        artifact = _document_artifact(self.stage2, self.stage2a)
        _write_json(self.review_path, artifact.model_dump(mode="json"))
        return ReviewWorkspace(self.build_spec)


if __name__ == "__main__":
    unittest.main()
