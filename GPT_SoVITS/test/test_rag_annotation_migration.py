"""旧标注 artifact 时间字段迁移测试。"""

from __future__ import annotations

import unittest

from rag.pipeline.temporal_migration import migrate_temporal_payload


class TemporalMigrationTest(unittest.TestCase):
    """验证一次性迁移只产生新时间语义。"""

    def test_migrates_stage3_documents(self) -> None:
        """Stage 3 三类 document 应按各自语义迁移。"""

        source: dict[str, object] = {
            "metadata": {"season_id": 3},
            "story_events": [{"point_id": "story_event:its_mygo:s3:ep01:event01", "document": {"season_id": 3, "title": "事件"}}],
            "character_relations": [{"document": {"season_id": 3}}],
            "lore_entries": [{"document": {"season_ids": [3]}}],
        }

        migrated = migrate_temporal_payload(source, "bang_dream_original")
        serialized = str(migrated)

        self.assertNotIn("season_id", serialized)
        self.assertIn("occurred_story_year", serialized)
        self.assertIn("applicable_story_years", serialized)
        self.assertEqual(migrated["metadata"], {"timeline_id": "bang_dream_original", "story_year": 3})
        self.assertIn("story_event:bang_dream_original:y3", serialized)
        self.assertEqual(migrate_temporal_payload(migrated, "bang_dream_original"), migrated)


if __name__ == "__main__":
    unittest.main()
