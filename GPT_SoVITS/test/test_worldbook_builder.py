"""世界书 build spec、ID map 与确定性 staging 测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import UUID, uuid4

from rag.models import CanonBranch, CharacterId, SeriesId
from rag.pipeline.review_migration import build_source_fingerprint
from rag.pipeline.stage3_document_models import Stage3DocumentReviewArtifact, StoryEventReviewRecord
from rag.pipeline.stage3_lore_models import Stage3LoreDecisionsArtifact
from rag.pipeline.stage3_review_editor import replace_artifact_item_content, update_artifact_item_notes
from rag.pipeline.stage3_relation_models import Stage3RelationReviewArtifact
from rag.pipeline.stage3_thought_models import Stage3ThoughtReviewArtifact, ThoughtAggregationMetadata
from rag.pipeline.schemas import Stage3ImportMetadata, Stage3RelationAggregationMetadata, StoryEventPayload
from rag.worldbook.build_audit import audit_staging_packages
from rag.worldbook.build_models import IdentityMapRecord, WorldbookBuildSpec, WorldbookIdentityMap
from rag.worldbook.builder import build_staging_package, load_build_spec
from rag.worldbook.identity_map import IdentityResolver
from rag.worldbook.publisher import publish_worldbook_package, validate_worldbook_build


_CANDIDATE_ID = "story_candidate:11111111-1111-4111-8111-111111111111"


def _write_json(path: Path, value: object) -> None:
    """写入测试所需的 UTF-8 JSON。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _prepare_build_spec(root: Path) -> Path:
    """创建一套单集、含一条 Story Event 的完整构建输入。"""

    stage2 = root / "ep01_stage2.json"
    stage2a = root / "ep01_stage2a.json"
    stage2b = root / "ep01_stage2b.json"
    for path, label in ((stage2, "stage2"), (stage2a, "stage2a"), (stage2b, "stage2b")):
        path.write_text(label, encoding="utf-8")
    metadata = Stage3ImportMetadata(
        subtitle_path="ep01.ass",
        anime_title="It's MyGO!!!!!",
        series_id=SeriesId.ITS_MYGO,
        timeline_id="bang_dream_original",
        story_year=3,
        canon_branch=CanonBranch.MAIN,
        episode=1,
        source_stage2_model="test",
        source_stage2_template_path="test.jinja",
    )
    document_artifact = Stage3DocumentReviewArtifact(
        metadata=metadata,
        direct_sources=[
            build_source_fingerprint("stage2_input", stage2, 1),
            build_source_fingerprint("stage2a_annotation", stage2a, 1),
        ],
        story_events=[
            StoryEventReviewRecord(
                candidate_id=_CANDIDATE_ID,
                source_scene_id="ep01_s001",
                source_local_id="event_01",
                confidence=1.0,
                review_status="completed",
                disposition="publish",
                review_basis_sha256="0" * 64,
                generated_document=StoryEventPayload(
                    timeline_id="bang_dream_original",
                    occurred_story_year=3,
                    series_id=SeriesId.ITS_MYGO,
                    episode=1,
                    time_order=4000,
                    visible_from=4000,
                    visible_to=4999,
                    canon_branch=CanonBranch.MAIN,
                    title="CRYCHIC 解散",
                    summary="CRYCHIC 在祥子离开后解散。",
                    participants=[CharacterId.SAKIKO, CharacterId.TOMORI],
                    importance=3,
                    tags=["CRYCHIC"],
                    retrieval_text="CRYCHIC 解散。",
                ),
            )
        ],
    )
    rag_path = root / "ep01_rag.json"
    _write_json(rag_path, document_artifact.model_dump(mode="json"))
    relation_artifact = Stage3RelationReviewArtifact(
        metadata=Stage3RelationAggregationMetadata(
            anime_title="It's MyGO!!!!!",
            series_id="its_mygo",
            timeline_id="bang_dream_original",
            story_year=3,
            canon_branch="main",
            episodes=[1],
        ),
        direct_sources=[
            build_source_fingerprint("stage2_input", stage2, 1),
            build_source_fingerprint("stage2a_annotation", stage2a, 1),
        ],
        aggregation_model="test",
    )
    relation_path = root / "relation_review.json"
    _write_json(relation_path, relation_artifact.model_dump(mode="json"))
    thought_artifact = Stage3ThoughtReviewArtifact(
        metadata=ThoughtAggregationMetadata(
            anime_title="It's MyGO!!!!!",
            series_id="its_mygo",
            timeline_id="bang_dream_original",
            story_year=3,
            canon_branch=CanonBranch.MAIN,
            episodes=[1],
        ),
        direct_sources=[
            build_source_fingerprint("stage2_input", stage2, 1),
            build_source_fingerprint("stage2b_annotation", stage2b, 1),
            build_source_fingerprint("stage3_rag", rag_path, 1),
        ],
        aggregation_model="test",
    )
    thought_path = root / "thought_review.json"
    _write_json(thought_path, thought_artifact.model_dump(mode="json"))
    lore_artifact = Stage3LoreDecisionsArtifact(
        episodes=[1],
        direct_sources=[build_source_fingerprint("stage3_rag", rag_path, 1)],
    )
    lore_path = root / "lore_decisions.json"
    _write_json(lore_path, lore_artifact.model_dump(mode="json"))
    spec = WorldbookBuildSpec(
        package_id="official.test.mygo",
        package_version="0.1.0",
        display_name="测试 MyGO",
        package_type="season",
        series_id=SeriesId.ITS_MYGO,
        timeline_id="bang_dream_original",
        canon_branch=CanonBranch.MAIN,
        story_year=3,
        episodes=[
            {
                "episode": 1,
                "stage2_input": stage2.name,
                "stage2a_annotation": stage2a.name,
                "stage2b_annotation": stage2b.name,
                "rag_artifact": rag_path.name,
            }
        ],
        relation_review=relation_path.name,
        thought_review=thought_path.name,
        lore_decisions=lore_path.name,
        id_map="entry_ids.json",
        official_root="GPT_SoVITS/rag/worldbooks/official",
    )
    spec_path = root / "worldbook_build.json"
    _write_json(spec_path, spec.model_dump(mode="json"))
    return spec_path


class WorldbookBuilderTest(unittest.TestCase):
    """验证纯构建、正式审计和身份生命周期。"""

    def test_provisional_build_is_byte_deterministic_and_auditable(self) -> None:
        """相同输入与空 ID map 应产生字节一致且可审计的 staging。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = _prepare_build_spec(root)
            resolved = load_build_spec(spec_path)
            first_dir = root / "first" / resolved.spec.package_id
            second_dir = root / "second" / resolved.spec.package_id
            first_resolver = IdentityResolver(WorldbookIdentityMap(package_id=resolved.spec.package_id), allocate=False)
            second_resolver = IdentityResolver(WorldbookIdentityMap(package_id=resolved.spec.package_id), allocate=False)

            first = build_staging_package(resolved, first_resolver, first_dir)
            second = build_staging_package(resolved, second_resolver, second_dir)
            issues = audit_staging_packages(resolved.official_root, {resolved.spec.package_id: first_dir})

            self.assertEqual((first_dir / "manifest.json").read_bytes(), (second_dir / "manifest.json").read_bytes())
            self.assertEqual(
                (first_dir / "content" / "story_events.json").read_bytes(),
                (second_dir / "content" / "story_events.json").read_bytes(),
            )
            self.assertEqual(first.manifest.package_id, "official.test.mygo")
            self.assertEqual(issues, [])

    def test_inactive_identity_requires_explicit_reactivation(self) -> None:
        """同 identity key 恢复旧 UUID 时必须提供一次性许可。"""

        entry_id = uuid4()
        identity_map = WorldbookIdentityMap(
            package_id="test",
            identities={_CANDIDATE_ID: IdentityMapRecord(entry_id=entry_id, status="inactive")},
        )
        with self.assertRaises(ValueError):
            IdentityResolver(identity_map.model_copy(deep=True), allocate=True).resolve(_CANDIDATE_ID)

        resolver = IdentityResolver(
            identity_map.model_copy(deep=True),
            allocate=True,
            reactivate_ids={_CANDIDATE_ID},
        )

        self.assertEqual(resolver.resolve(_CANDIDATE_ID), entry_id)
        self.assertEqual(resolver.identity_map.identities[_CANDIDATE_ID].status, "active")

    def test_removed_active_identity_requires_confirmation(self) -> None:
        """当前构建未使用的 active 身份应在确认后转为 inactive。"""

        identity_key = "lore_candidate:22222222-2222-4222-8222-222222222222"
        identity_map = WorldbookIdentityMap(
            package_id="test",
            identities={identity_key: IdentityMapRecord(entry_id=UUID("22222222-2222-4222-8222-222222222222"))},
        )
        resolver = IdentityResolver(identity_map, allocate=True)
        with self.assertRaises(ValueError):
            resolver.finalize_removed(set(), allow_all_removed=False)

        resolver.finalize_removed(set(), allow_all_removed=True)

        self.assertEqual(identity_map.identities[identity_key].status, "inactive")

    def test_content_edit_resets_review_but_notes_do_not(self) -> None:
        """人工内容快照应撤销审批，纯备注修改则应保留审批。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _prepare_build_spec(root)
            artifact_path = root / "ep01_rag.json"
            update_artifact_item_notes(artifact_path, _CANDIDATE_ID, "已核对字幕")
            noted = Stage3DocumentReviewArtifact.model_validate_json(
                artifact_path.read_text(encoding="utf-8")
            )
            replacement = noted.story_events[0].effective_document().model_copy(
                update={"title": "CRYCHIC 正式解散"}
            )
            replacement_path = root / "replacement.json"
            _write_json(replacement_path, replacement.model_dump(mode="json"))

            replace_artifact_item_content(artifact_path, _CANDIDATE_ID, replacement_path)
            edited = Stage3DocumentReviewArtifact.model_validate_json(
                artifact_path.read_text(encoding="utf-8")
            )

        self.assertEqual(noted.story_events[0].review_status, "completed")
        self.assertEqual(noted.story_events[0].review_notes, "已核对字幕")
        self.assertEqual(edited.story_events[0].review_status, "unreviewed")
        self.assertIsNone(edited.story_events[0].disposition)
        self.assertEqual(edited.story_events[0].effective_document().title, "CRYCHIC 正式解散")

    def test_validate_is_read_only_and_publish_rebuilds_once(self) -> None:
        """纯验证不得创建 ID map，正式发布应替换包并调用一次全量重建。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = _prepare_build_spec(root)
            id_map_path = root / "entry_ids.json"
            rebuild_roots: list[Path] = []

            validation_report = validate_worldbook_build(spec_path)

            self.assertTrue(validation_report.succeeded)
            self.assertFalse(id_map_path.exists())

            def rebuild(app_root: Path) -> tuple[bool, str | None]:
                """记录测试发布传入的应用根目录。"""

                rebuild_roots.append(app_root)
                return True, "ready"

            publish_report = publish_worldbook_package(spec_path, rebuild_callback=rebuild)
            target = root / "GPT_SoVITS" / "rag" / "worldbooks" / "official" / "official.test.mygo"
            manifest_exists = (target / "manifest.json").exists()

        self.assertTrue(publish_report.succeeded)
        self.assertTrue(publish_report.package_published)
        self.assertTrue(publish_report.index_rebuilt)
        self.assertEqual(len(rebuild_roots), 1)
        self.assertTrue(manifest_exists)


if __name__ == "__main__":
    unittest.main()
