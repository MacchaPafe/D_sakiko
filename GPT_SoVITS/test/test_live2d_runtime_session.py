from __future__ import annotations

import unittest
from types import ModuleType
from unittest.mock import Mock, call, patch

from live2d_support.runtime_adapter import Live2DModelAdapter
from live2d_support.runtime_session import Live2DRuntimeSession
from live2d_support.layout import Live2DLayout
from multi_char_live2d_module import Live2DModule


class RuntimeSessionTest(unittest.TestCase):
    """验证双运行时生命周期和实例之间的故障隔离。"""

    def test_versions_initialize_once_and_failed_model_keeps_runtime(self) -> None:
        """交替加载及模型失败均不释放常驻运行时，关闭时只释放一次。"""
        v2 = ModuleType("v2")
        v3 = ModuleType("v3")
        with (
            patch("live2d_support.runtime_session.glUseProgram"),
            patch("live2d_support.runtime_session.detect_live2d_runtime_version", side_effect=["v2", "v3", "v2", "v3"]),
            patch("live2d_support.runtime_session.load_live2d_runtime", side_effect=[v2, v3]) as load,
            patch("live2d_support.runtime_session.initialize_live2d_runtime") as initialize,
            patch("live2d_support.runtime_session.release_live2d_runtime") as release,
            patch.object(Live2DModelAdapter, "create", side_effect=[Mock(), ValueError("bad model"), Mock(), Mock()]),
        ):
            session = Live2DRuntimeSession()
            session.create_model("v2")
            with self.assertRaises(ValueError):
                session.create_model("broken-v3")
            session.create_model("v2-again")
            session.create_model("v3-again")
            self.assertEqual(load.call_count, 2)
            self.assertEqual(initialize.call_args_list, [call(v2), call(v3)])
            release.assert_not_called()
            session.close()
            session.close()
            self.assertEqual(release.call_args_list, [call(v2), call(v3)])

    def test_draw_failure_restores_program_boundary(self) -> None:
        """原始模型绘制抛错后仍恢复宿主固定管线。"""
        adapter = object.__new__(Live2DModelAdapter)
        adapter.model = Mock()
        adapter.model.Draw.side_effect = ValueError("draw")
        with patch("live2d_support.runtime_adapter.glUseProgram") as program:
            with self.assertRaises(ValueError):
                adapter.Draw()
            self.assertEqual(program.call_args_list, [call(0), call(0)])

    def test_mixed_slots_load_independently(self) -> None:
        """一个混合版本槽位失败时另一槽位仍成功加载。"""
        module = object.__new__(Live2DModule)
        module.active_slots = [{"model_json_path": "bad-v2"}, {"model_json_path": "good-v3"}]
        session = Mock(spec=Live2DRuntimeSession)
        right = Mock(spec=Live2DModelAdapter)
        right.version = "v3"
        session.create_model.side_effect = [ValueError("bad"), right]
        models: list[Live2DModelAdapter | None] = [None, None]
        layouts = [Live2DLayout(1.0, 0.0, 0.0), Live2DLayout(1.0, 0.0, 0.0)]
        with patch("multi_char_live2d_module.get_live2d_layout"), patch.object(module, "_apply_model_common_setup"):
            module._load_models(models, layouts, 400, {0, 1}, session)
        self.assertIsNone(models[0])
        self.assertIs(models[1], right)

    def test_replacing_left_does_not_reload_right(self) -> None:
        """左侧加载失败不释放或重载已经可见的右侧模型。"""
        module = object.__new__(Live2DModule)
        module.active_slots = [{"model_json_path": "bad"}, {"model_json_path": "right"}]
        session = Mock(spec=Live2DRuntimeSession)
        session.create_model.side_effect = ValueError("bad")
        right = Mock(spec=Live2DModelAdapter)
        models: list[Live2DModelAdapter | None] = [None, right]
        layouts = [Live2DLayout(1.0, 0.0, 0.0), Live2DLayout(1.0, 0.0, 0.0)]
        module._load_models(models, layouts, 400, {0}, session)
        session.create_model.assert_called_once_with("bad")
        right.dispose.assert_not_called()
        self.assertIs(models[1], right)

    def test_setup_failure_disposes_candidate(self) -> None:
        """模型已经加载但布局配置失败时释放候选实例并保持空槽位。"""
        module = object.__new__(Live2DModule)
        module.active_slots = [{"model_json_path": "v2"}, {"model_json_path": None}]
        session = Mock(spec=Live2DRuntimeSession)
        candidate = Mock(spec=Live2DModelAdapter)
        candidate.version = "v2"
        session.create_model.return_value = candidate
        models: list[Live2DModelAdapter | None] = [None, None]
        layouts = [Live2DLayout(1.0, 0.0, 0.0), Live2DLayout(1.0, 0.0, 0.0)]
        with patch("multi_char_live2d_module.get_live2d_layout", side_effect=ValueError("layout")):
            module._load_models(models, layouts, 400, {0, 1}, session)
        candidate.dispose.assert_called_once_with()
        self.assertEqual(models, [None, None])

    def test_mixed_facing_uses_each_models_version(self) -> None:
        """混合模型中 V3 仍可使用方向动作，V2 保持默认方向。"""
        self.assertEqual(Live2DModule._motion_position_for_slot(0, ["v3", "v2"], "face_to_face"), "L")
        self.assertEqual(Live2DModule._motion_position_for_slot(1, ["v3", "v2"], "face_to_face"), "C")


if __name__ == "__main__":
    unittest.main()
