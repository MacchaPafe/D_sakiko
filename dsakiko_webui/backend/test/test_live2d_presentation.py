from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dsakiko_webui.backend.assets as assets_module
from dsakiko_webui.backend.assets import AssetRegistry
from dsakiko_webui.backend.live2d_presentation import Live2DPresentationResolver
from dsakiko_webui.backend.runtime import HeadlessRuntime


class Live2DPresentationResolverTest(unittest.TestCase):
    """验证 WebUI 对话级 Live2D 呈现解析规则。"""

    def setUp(self) -> None:
        """为每个测试建立独立的角色资源目录。"""
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name).resolve()
        self.live2d_root = self.project_root / "live2d_related"
        self.character_root = self.live2d_root / "anon"
        self.character_root.mkdir(parents=True)
        self.assets_patch = patch.object(assets_module, "LIVE2D_ROOT", self.live2d_root)
        self.assets_patch.start()
        self.assets = AssetRegistry()
        self.resolver = Live2DPresentationResolver(
            self.assets,
            self.project_root,
            self.live2d_root,
            Path(__file__).resolve().parents[3] / "GPT_SoVITS",
        )
        self.character = SimpleNamespace(
            character_name="爱音",
            character_folder_name="anon",
            live2d_json=None,
        )

    def tearDown(self) -> None:
        """恢复全局资源目录并删除临时文件。"""
        self.assets_patch.stop()
        self.temporary_directory.cleanup()

    def _chat(self, explicit_target: str | None = None) -> SimpleNamespace:
        """创建只包含 Live2D 对话元数据的测试对话。"""
        models = {"爱音": explicit_target} if explicit_target is not None else {}
        return SimpleNamespace(meta=SimpleNamespace(live2d_models=models))

    def _write_v2(self, relative_path: str, motion_name: str = "smile01.mtn") -> Path:
        """写入最小可识别的 v2 模型配置。"""
        model_path = self.character_root / relative_path
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_text(json.dumps({
            "model": "model.moc",
            "textures": ["texture.png"],
            "motions": {"happiness": [{"file": motion_name}]},
            "expressions": [{"name": "exp_smile01", "file": "smile.exp.json"}],
        }), encoding="utf-8")
        return model_path.resolve()

    def _write_v3(self, relative_path: str) -> Path:
        """写入最小可识别的 v3 模型配置。"""
        model_path = self.character_root / relative_path
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_text(json.dumps({
            "Version": 3,
            "FileReferences": {
                "Moc": "model.moc3",
                "Textures": ["texture.png"],
                "Motions": {"happiness": [{"File": "mtn_smile01.motion3.json"}]},
                "Expressions": [{"Name": "exp_smile01", "File": "smile.exp3.json"}],
            },
        }), encoding="utf-8")
        return model_path.resolve()

    def test_explicit_dialogue_target_has_priority_over_character_default(self) -> None:
        """对话显式模型应优先于角色默认模型。"""
        default_model = self._write_v2("live2D_model/default.model.json")
        explicit_model = self._write_v3("extra_model/costume/costume.model3.json")
        self.character.live2d_json = str(default_model)

        presentation = self.resolver.resolve(self._chat(str(explicit_model)), self.character)

        self.assertEqual(presentation.resolution, "resolved")
        self.assertEqual(presentation.version, "v3")
        self.assertTrue(str(presentation.model_url).endswith("/costume.model3.json"))
        self.assertEqual(presentation.layout.scale, 2.3)
        self.assertEqual(presentation.layout.offset_x, 0.0)
        self.assertEqual(presentation.layout.offset_y, -0.77)

    def test_character_default_is_used_without_explicit_target(self) -> None:
        """对话无显式目标时应使用角色默认模型。"""
        default_model = self._write_v2("live2D_model/default.model.json")
        self.character.live2d_json = str(default_model)

        presentation = self.resolver.resolve(self._chat(), self.character)

        self.assertEqual(presentation.resolution, "resolved")
        self.assertEqual(presentation.version, "v2")
        self.assertIn("happiness", presentation.capabilities.motion_files_by_group)

    def test_explicit_missing_target_does_not_fallback_to_default(self) -> None:
        """显式目标失效时应保留配置错误而不回退。"""
        self.character.live2d_json = str(self._write_v2("live2D_model/default.model.json"))
        missing = self.character_root / "extra_model" / "missing.model3.json"

        presentation = self.resolver.resolve(self._chat(str(missing)), self.character)

        self.assertEqual(presentation.resolution, "configured_error")
        self.assertEqual(presentation.error.code, "LIVE2D_TARGET_NOT_FOUND")
        self.assertIsNone(presentation.model_url)

    def test_target_outside_character_directory_is_rejected(self) -> None:
        """其他角色或项目外部的模型应被拒绝。"""
        other_model = self.live2d_root / "tomori" / "live2D_model" / "other.model.json"
        other_model.parent.mkdir(parents=True)
        other_model.write_text('{"model": "other.moc"}', encoding="utf-8")

        presentation = self.resolver.resolve(self._chat(str(other_model)), self.character)

        self.assertEqual(presentation.resolution, "configured_error")
        self.assertEqual(presentation.error.code, "LIVE2D_TARGET_OUTSIDE_CHARACTER")

    def test_absent_target_is_a_normal_presentation_state(self) -> None:
        """角色和对话都未配置模型时应返回正常无模型状态。"""
        presentation = self.resolver.resolve(self._chat(), self.character)

        self.assertEqual(presentation.resolution, "absent")
        self.assertIsNone(presentation.error)

    def test_target_identity_is_path_based_and_revision_tracks_file_change(self) -> None:
        """目标 ID 应区分同角色模型，revision 应跟踪文件修订。"""
        first_model = self._write_v2("extra_model/first/model.model.json")
        second_model = self._write_v2("extra_model/second/model.model.json")
        first = self.resolver.resolve(self._chat(str(first_model)), self.character)
        second = self.resolver.resolve(self._chat(str(second_model)), self.character)
        time.sleep(0.002)
        first_model.write_text(first_model.read_text(encoding="utf-8") + " ", encoding="utf-8")
        changed = self.resolver.resolve(self._chat(str(first_model)), self.character)

        self.assertNotEqual(first.target_id, second.target_id)
        self.assertEqual(first.target_id, changed.target_id)
        self.assertNotEqual(first.revision, changed.revision)

    def test_presentation_does_not_expose_host_absolute_path(self) -> None:
        """序列化呈现描述不应泄露主机绝对路径。"""
        model = self._write_v3("extra_model/costume/model.model3.json")

        serialized = json.dumps(
            self.resolver.resolve(self._chat(str(model)), self.character).to_dict(),
            ensure_ascii=False,
        )

        self.assertNotIn(str(self.project_root), serialized)
        self.assertIn("/api/v1/live2d/", serialized)

    def test_runtime_forwards_attributed_switch_as_presentation_event(self) -> None:
        """WebUI 应将归属正确的换装命令转为对话级呈现事件。"""
        model = self._write_v3("extra_model/costume/model.model3.json")
        chat = SimpleNamespace(
            chat_id="chat_anon",
            meta=SimpleNamespace(
                live2d_models={"爱音": str(model)},
                extra={"webui_messages": [{"turn_id": "turn_switch"}]},
            ),
            message_list=[],
            get_character_name=lambda: "爱音",
        )
        runtime = HeadlessRuntime(self.assets)
        runtime.live2d_presentations = self.resolver
        runtime.dp_chat = SimpleNamespace(current_chat_id=chat.chat_id)
        runtime.chat_manager = SimpleNamespace(
            get_chat_by_id=lambda chat_id: chat if chat_id == chat.chat_id else None,
        )
        runtime.character_by_name = {"爱音": self.character}

        try:
            runtime._handle_live2d_command({
                "type": "switch_live2d",
                "chat_id": chat.chat_id,
                "turn_id": "turn_switch",
                "model_json": str(model),
            })
            event = runtime.events.get_nowait()
        finally:
            runtime.uploads.close()

        self.assertEqual(event["type"], "live2d_presentation_changed")
        self.assertEqual(event["chat_id"], chat.chat_id)
        self.assertEqual(event["data"]["presentation"]["version"], "v3")

    def test_runtime_snapshot_contains_conversation_level_presentation(self) -> None:
        """状态快照应在角色静态实体之外携带对话级呈现描述。"""
        model = self._write_v3("extra_model/costume/model.model3.json")
        chat = SimpleNamespace(
            chat_id="chat_anon",
            name="换装对话",
            meta=SimpleNamespace(live2d_models={"爱音": str(model)}, extra={}),
            message_list=[],
            prompt_generator=SimpleNamespace(user_persona=None),
            get_character_name=lambda: "爱音",
        )
        runtime = HeadlessRuntime(self.assets)
        runtime.live2d_presentations = self.resolver
        runtime.dp_chat = SimpleNamespace(current_chat=chat)
        runtime.character_by_name = {"爱音": self.character}
        runtime.character_entities = {"anon": {"id": "anon", "name": "爱音"}}

        try:
            snapshot = runtime.state_snapshot()
        finally:
            runtime.uploads.close()

        self.assertEqual(snapshot["live2d"]["resolution"], "resolved")
        self.assertEqual(snapshot["live2d"]["version"], "v3")
        self.assertNotIn("model_url", snapshot["character"])

    def test_runtime_ignores_switch_for_unknown_turn(self) -> None:
        """WebUI 不应应用未知轮次的延迟或伪造换装事件。"""
        chat = SimpleNamespace(
            chat_id="chat_anon",
            meta=SimpleNamespace(live2d_models={}, extra={"webui_messages": []}),
            message_list=[],
            get_character_name=lambda: "爱音",
        )
        runtime = HeadlessRuntime(self.assets)
        runtime.live2d_presentations = self.resolver
        runtime.dp_chat = SimpleNamespace(current_chat_id=chat.chat_id)
        runtime.chat_manager = SimpleNamespace(get_chat_by_id=lambda chat_id: chat)
        runtime.character_by_name = {"爱音": self.character}

        try:
            runtime._handle_live2d_command({
                "type": "switch_live2d",
                "chat_id": chat.chat_id,
                "turn_id": "turn_unknown",
            })
            self.assertTrue(runtime.events.empty())
        finally:
            runtime.uploads.close()

    def test_manual_retry_re_resolves_current_dialogue_without_mutating_target(self) -> None:
        """手动重试应重新解析已保存目标，而不改写对话元数据。"""
        model = self.character_root / "extra_model" / "fixed" / "model.model3.json"
        chat = SimpleNamespace(
            chat_id="chat_anon",
            meta=SimpleNamespace(live2d_models={"爱音": str(model)}, extra={}),
            message_list=[],
            get_character_name=lambda: "爱音",
        )
        runtime = HeadlessRuntime(self.assets)
        runtime.live2d_presentations = self.resolver
        runtime.dp_chat = SimpleNamespace(current_chat_id=chat.chat_id, current_chat=chat)
        runtime.character_by_name = {"爱音": self.character}

        try:
            _, failed_events = runtime._retry_live2d({"chat_id": chat.chat_id})
            self._write_v3("extra_model/fixed/model.model3.json")
            _, recovered_events = runtime._retry_live2d({"chat_id": chat.chat_id})
        finally:
            runtime.uploads.close()

        self.assertEqual(
            failed_events[0]["data"]["presentation"]["resolution"],
            "configured_error",
        )
        self.assertEqual(
            recovered_events[0]["data"]["presentation"]["resolution"],
            "resolved",
        )
        self.assertEqual(chat.meta.live2d_models["爱音"], str(model))


if __name__ == "__main__":
    unittest.main()
