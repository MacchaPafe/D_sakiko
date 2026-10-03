"""验证应用只依赖 1.0.0 统一接口，并保留原有渲染策略。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from live2d_support.runtime_adapter import Live2DModelAdapter, Live2DVersion, release_live2d_runtime
from live2d_support.runtime_session import Live2DRuntimeSession


class UnifiedApiTest(unittest.TestCase):
    """使用禁止旧接口的替身检测参数、呼吸、眨眼和资源生命周期。"""

    def model(self) -> Mock:
        """创建仅允许上游公开接口的严格原生替身。"""
        model = Mock(spec=["LoadModelJson", "GetParamIds", "GetParamCount",
            "GetParamValueById", "GetParamValueByIndex", "GetParamMinByIndex",
            "GetParamMaxByIndex", "GetParamDefaultByIndex", "SetParamById",
            "SetAutoBreath", "SetAutoBreathParameterOnly", "SetAutoBlink",
            "Update", "UpdateBlink", "IsMotionFinished", "LoadExtraMotion", "StartMotion"])
        model.GetParamIds.return_value = ["ParamMouthOpenY", "ParamAngleX"]
        model.GetParamCount.return_value = 2
        model.GetParamValueById.return_value = 0.7
        model.GetParamValueByIndex.return_value = 0.7
        model.GetParamMinByIndex.return_value = 0.0
        model.GetParamMaxByIndex.return_value = 1.0
        model.GetParamDefaultByIndex.return_value = 0.0
        model.IsMotionFinished.return_value = True
        model.LoadExtraMotion.return_value = 0
        return model

    def adapter(self, model: Mock, version: Live2DVersion = "v3") -> Live2DModelAdapter:
        """构造不需要文件和 OpenGL 的应用适配器。"""
        return Live2DModelAdapter(
            model_json_path="model.model3.json", version=version,
            runtime=ModuleType("live2d"), model=model, motion_groups=frozenset(),
            motion_files_by_group={}, expression_ids=frozenset(),
            parameter_ids=frozenset(), preview_motion_indices_by_path={})

    def test_create_and_parameter_editor_roundtrip(self) -> None:
        """加载后的参数编辑器保留元数据，口型按当前帧值读写。"""
        native = self.model()
        runtime = ModuleType("live2d")
        runtime.Model = Mock(return_value=native)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.model3.json"
            path.write_text(json.dumps({"Version": 3, "FileReferences": {"Moc": "model.moc3"}}))
            with patch("live2d_support.runtime_adapter.load_live2d_runtime", return_value=runtime), patch("live2d_support.runtime_adapter.glUseProgram"):
                adapter = Live2DModelAdapter.create(str(path))
        self.assertEqual(adapter.GetParameterCount(), 2)
        parameter = adapter.GetParameter(0)
        self.assertEqual((parameter.id, parameter.min, parameter.max, parameter.default),
                         ("ParamMouthOpenY", 0.0, 1.0, 0.0))
        self.assertTrue(adapter.set_parameter_value("mouth_open_y", 0.8))
        native.SetParamById.assert_called_once_with("ParamMouthOpenY", 0.8)
        self.assertEqual(adapter.get_parameter_value("mouth_open_y"), 0.7)

    def test_idle_blink_and_explicit_delta(self) -> None:
        """空闲时额外眨眼，活动动作时不覆盖动作中的眼睛曲线。"""
        native = self.model()
        adapter = self.adapter(native)
        adapter.SetAutoBlinkEnable(True)
        adapter._last_update_time = 10.0
        with patch("live2d_support.runtime_adapter.time.monotonic", return_value=10.02):
            adapter.Update()
        self.assertAlmostEqual(native.Update.call_args.args[0], 0.02)
        self.assertEqual(native.UpdateBlink.call_count, 1)
        native.IsMotionFinished.return_value = False
        with patch("live2d_support.runtime_adapter.time.monotonic", return_value=10.04):
            adapter.Update()
        self.assertEqual(native.UpdateBlink.call_count, 1)

    def test_breath_only_and_disable(self) -> None:
        """V3 自动呼吸只动呼吸参数，关闭时停止全部自动呼吸。"""
        native = self.model()
        adapter = self.adapter(native)
        adapter.SetAutoBreathEnable(True)
        native.SetAutoBreathParameterOnly.assert_called_once_with(True)
        adapter.SetAutoBreathEnable(False)
        native.SetAutoBreath.assert_called_once_with(False)

    def test_v2_external_motion_keeps_callbacks_and_cache(self) -> None:
        """V2 外部动作使用统一加载方式，缓存索引并传递两类回调。"""
        native = self.model()
        adapter = self.adapter(native, "v2")
        start, finish = Mock(), Mock()
        for _ in range(2):
            self.assertTrue(adapter.StartMotionFile("probe.mtn", 3, start, finish))
        self.assertEqual(native.LoadExtraMotion.call_count, 1)
        self.assertEqual(native.StartMotion.call_count, 2)
        self.assertEqual(native.StartMotion.call_args.args[-2:], (start, finish))

    def test_mixed_models_share_one_runtime_lifecycle(self) -> None:
        """同一窗口混合 V2/V3 只初始化和释放一次统一运行时。"""
        runtime = ModuleType("live2d")
        with patch("live2d_support.runtime_session.glUseProgram"), patch("live2d_support.runtime_session.detect_live2d_runtime_version", side_effect=["v2", "v3"]), patch("live2d_support.runtime_session.load_live2d_runtime", return_value=runtime), patch("live2d_support.runtime_session.initialize_live2d_runtime") as initialize, patch("live2d_support.runtime_session.release_live2d_runtime") as release, patch.object(Live2DModelAdapter, "create", return_value=Mock()):
            session = Live2DRuntimeSession()
            session.create_model("v2")
            session.create_model("v3")
            session.close()
        initialize.assert_called_once_with(runtime)
        release.assert_called_once_with(runtime)

    def test_shader_release_precedes_framework_dispose(self) -> None:
        """框架释放前销毁共享着色器，避免访问已销毁的分配器。"""
        events: list[str] = []
        runtime = ModuleType("live2d")
        runtime.glRelease = Mock(side_effect=lambda: events.append("shaders"))
        runtime.dispose = Mock(side_effect=lambda: events.append("framework"))
        release_live2d_runtime(runtime)
        self.assertEqual(events, ["shaders", "framework"])


if __name__ == "__main__":
    unittest.main()
