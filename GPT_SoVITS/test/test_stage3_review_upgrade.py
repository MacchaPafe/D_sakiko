"""一次性 Story/Lore 审核 Schema 升级测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from rag.pipeline.stage3_rag_import import load_stage3_normalized_import_artifact
from rag.pipeline.stage3_review_upgrade import upgrade_stage3_review_schema


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "GPT_SoVITS" / "rag" / "pipeline" / "data"


class Stage3ReviewUpgradeTest(unittest.TestCase):
    """验证 dry-run、旧 UUID 继承和重复 apply 幂等性。"""

    def test_upgrade_is_dry_by_default_and_idempotent_after_apply(self) -> None:
        """默认不改文件，应用后再次运行应保留 candidate ID 与正式 UUID。"""

        source_artifact = DATA_ROOT / "annotations_stage3" / "ep01_rag_ready.json"
        stage2_input = DATA_ROOT / "annotations_stage2" / "ep01_stage2_input.json"
        annotation = DATA_ROOT / "annotations_stage2" / "ep01_pass2_raw.json"
        legacy = load_stage3_normalized_import_artifact(source_artifact)
        legacy_id = legacy.story_events[0].point_id
        entry_id = uuid4()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_path = root / "ep01_rag.json"
            artifact_path.write_bytes(source_artifact.read_bytes())
            id_map_path = root / "entry_ids.json"
            id_map_path.write_text(
                json.dumps({legacy_id: str(entry_id)}),
                encoding="utf-8",
            )
            original_artifact = artifact_path.read_bytes()

            dry_artifacts, dry_map = upgrade_stage3_review_schema(
                package_id="official.test",
                artifact_paths=[artifact_path],
                stage2_input_paths=[stage2_input],
                annotation_paths=[annotation],
                old_id_map_path=id_map_path,
                new_id_map_path=id_map_path,
                apply=False,
            )

            self.assertEqual(artifact_path.read_bytes(), original_artifact)
            candidate_id = dry_artifacts[0].story_events[0].candidate_id
            self.assertEqual(dry_map.identities[candidate_id].entry_id, entry_id)

            first_artifacts, first_map = upgrade_stage3_review_schema(
                package_id="official.test",
                artifact_paths=[artifact_path],
                stage2_input_paths=[stage2_input],
                annotation_paths=[annotation],
                old_id_map_path=id_map_path,
                new_id_map_path=id_map_path,
                apply=True,
            )
            second_artifacts, second_map = upgrade_stage3_review_schema(
                package_id="official.test",
                artifact_paths=[artifact_path],
                stage2_input_paths=[stage2_input],
                annotation_paths=[annotation],
                old_id_map_path=id_map_path,
                new_id_map_path=id_map_path,
                apply=True,
            )

        applied_candidate_id = first_artifacts[0].story_events[0].candidate_id
        self.assertEqual(second_artifacts[0].story_events[0].candidate_id, applied_candidate_id)
        self.assertEqual(first_map.identities[applied_candidate_id].entry_id, entry_id)
        self.assertEqual(second_map.identities[applied_candidate_id].entry_id, entry_id)


if __name__ == "__main__":
    unittest.main()
