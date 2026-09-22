from __future__ import annotations

import unittest

from live2d_support.layout import (
    Live2DLayout,
    default_live2d_layout,
)
from qconfig import migrate_live2d_layouts


class Live2DLayoutTest(unittest.TestCase):
    """验证 Live2D 平台布局默认值和配置迁移。"""

    def test_single_v3_defaults_are_platform_specific(self) -> None:
        """桌面端与 WebUI 的单角色 V3 默认偏移应独立。"""
        desktop = default_live2d_layout("v3", "single", "desktop")
        web = default_live2d_layout("v3", "single", "web")

        self.assertEqual(desktop, Live2DLayout(scale=2.3, offset_x=0.0, offset_y=-0.75))
        self.assertEqual(web, Live2DLayout(scale=1.5, offset_x=0.0, offset_y=-0.53))

    def test_legacy_layouts_migrate_to_desktop_only(self) -> None:
        """旧版扁平布局应迁移为桌面端布局。"""
        legacy = {
            "model.json": {
                "single": {"scale": 1.2, "offset_x": 0.1, "offset_y": -0.3},
                "theater": {"scale": 2.0, "offset_x": 0.0, "offset_y": -0.77},
            }
        }

        migrated = migrate_live2d_layouts(legacy)

        self.assertEqual(migrated["model.json"]["single"]["desktop"]["scale"], 1.2)
        self.assertEqual(migrated["model.json"]["theater"]["desktop"]["offset_y"], -0.77)
        self.assertNotIn("web", migrated["model.json"]["single"])

    def test_migration_is_idempotent_and_preserves_platform_layouts(self) -> None:
        """新格式重复迁移不应改变数据或覆盖平台值。"""
        current = {
            "model.json": {
                "single": {
                    "desktop": {"scale": 1.2},
                    "web": {"scale": 1.5},
                }
            }
        }

        migrated = migrate_live2d_layouts(current)

        self.assertEqual(migrated, current)


if __name__ == "__main__":
    unittest.main()
