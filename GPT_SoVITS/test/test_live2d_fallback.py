from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from character import CharacterAttributes
from live2d_module import Live2DModule as SingleLive2DModule
from live2d_support.runtime_adapter import NullLive2DModel
from multi_char_live2d_module import Live2DModule as TheaterLive2DModule


class NullLive2DModelTestCase(unittest.TestCase):
    """测试普通对话渲染器使用的空模型。"""

    def test_null_model_absorbs_render_calls_and_rejects_actions(self) -> None:
        """验证空模型不绘制内容，动作与能力查询均返回空结果。"""
        model = NullLive2DModel()

        model.Resize(800, 800)
        model.Update()
        model.Draw()
        model.SetAutoBlinkEnable(True)
        model.SetAutoBreathEnable(True)
        model.dispose()
        model.dispose()

        self.assertFalse(model.StartRandomMotion("IDLE"))
        self.assertFalse(model.StartMotion("IDLE", 0, 3))
        self.assertFalse(model.SetSemanticExpression("idle"))
        self.assertFalse(model.set_parameter_value("mouth_open_y", 1.0))
        self.assertEqual(model.motion_capabilities().supported_positions_by_group, {})


class SingleLive2DTargetTestCase(unittest.TestCase):
    """测试普通对话渲染器的可空模型目标语义。"""

    def setUp(self) -> None:
        """创建一个没有默认 Live2D 模型的角色。"""
        self.character = CharacterAttributes()
        self.character.character_folder_name = "uika"
        self.character.character_name = "三角初华"
        self.module = SingleLive2DModule()
        self.module.character_list = [self.character]
        self.module.character_by_name = {self.character.character_name: self.character}
        self.module.character_by_folder = {
            self.character.character_folder_name: self.character,
        }

    def test_explicit_missing_path_is_preserved_but_none_stays_unconfigured(self) -> None:
        """验证显式失效路径不回退默认模型，空目标保持正常未配置。"""
        missing_path = "/missing/uika.model3.json"

        self.assertEqual(
            self.module.switch_live2d_target(
                self.character.character_name,
                missing_path,
                character_folder_name="uika",
                use_default=False,
            ),
            missing_path,
        )
        self.assertIsNone(
            self.module.switch_live2d_target(
                self.character.character_name,
                None,
                character_folder_name="uika",
                use_default=False,
            )
        )


class TheaterLive2DFallbackPolicyTestCase(unittest.TestCase):
    """测试小剧场可空模型槽位的纯策略。"""

    def setUp(self) -> None:
        """创建不初始化 pygame 的小剧场策略对象。"""
        self.module = object.__new__(TheaterLive2DModule)

    def test_normalize_slots_accepts_zero_one_or_two_models(self) -> None:
        """验证两个角色槽位可以分别为空或配置模型。"""
        payload = [
            {"slot": 0, "character_name": "初华", "model_json_path": None},
            {"slot": 1, "character_name": "睦", "model_json_path": "mutsumi.model3.json"},
        ]
        with patch(
            "multi_char_live2d_module.detect_live2d_runtime_version",
            return_value="v3",
        ):
            normalized = self.module._normalize_slots_payload(payload)

        self.assertIsNone(normalized[0]["model_json_path"])
        self.assertIsNone(normalized[0]["model_version"])
        self.assertEqual(normalized[1]["model_version"], "v3")
        self.assertEqual(self.module._select_runtime_version([None, None], None), None)
        self.assertEqual(self.module._select_runtime_version([None, "v3"], None), "v3")

    def test_broken_slot_does_not_reject_other_slot(self) -> None:
        """验证一个槽位模型损坏时，另一槽位仍保留可加载版本。"""
        payload = [
            {"slot": 0, "character_name": "初华", "model_json_path": "broken.model3.json"},
            {"slot": 1, "character_name": "睦", "model_json_path": "valid.model3.json"},
        ]

        def detect(path: str) -> str:
            """模拟一个损坏模型和一个有效 V3 模型。"""
            if path.startswith("broken"):
                raise ValueError("broken")
            return "v3"

        with patch("multi_char_live2d_module.detect_live2d_runtime_version", side_effect=detect):
            normalized = self.module._normalize_slots_payload(payload)

        self.assertIsNone(normalized[0]["model_version"])
        self.assertEqual(normalized[1]["model_version"], "v3")


if __name__ == "__main__":
    unittest.main()
