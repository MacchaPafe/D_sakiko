"""Stage 3 审核操作与多文件工作区测试。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from rag.pipeline.review_migration import build_source_fingerprint
from rag.pipeline.schemas import Stage3ImportMetadata, StoryEventPayload
from rag.pipeline.stage3_document_models import (
    Stage3DocumentReviewArtifact,
    StoryEventReviewRecord,
)
from rag.pipeline.stage3_review_operations import (
    CompleteItemReviewCommand,
    ReplaceStoryDocumentCommand,
    RestoreGeneratedContentCommand,
    UpdateItemNotesCommand,
    apply_review_command,
)
from rag.pipeline.stage3_review_regeneration import Stage3ReviewRegenerator
from rag.pipeline.stage3_review_workspace import ReviewWorkspace


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


if __name__ == "__main__":
    unittest.main()
