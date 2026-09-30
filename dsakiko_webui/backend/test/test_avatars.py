"""头像实时扫描、持久选择与现有设置接口的回归验证。"""
import base64
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from dsakiko_webui.backend import assets as assets_module
from dsakiko_webui.backend.app import create_app
from dsakiko_webui.backend.assets import AssetRegistry
from dsakiko_webui.backend.auth import AccessController
from dsakiko_webui.backend.runtime import HeadlessRuntime


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZMsAAAAASUVORK5CYII=")


class DiskConfig:
    """隔离全局配置文件，避免测试加载真实密钥或写用户设置。"""
    def __init__(self, path):
        self.path = path
        self.webui_character_avatars = SimpleNamespace(value={})
        self.path.write_text(json.dumps({"webui_setting": {"character_avatars": {"other": "keep.png"}},
                                         "unrelated": "keep"}), encoding="utf-8")
        self.fail = False

    def snapshot(self):
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self.webui_character_avatars.value = self.data["webui_setting"]["character_avatars"]
        return self

    def __enter__(self):
        return self.snapshot()

    def set(self, item, value):
        item.value = value

    def __exit__(self, exc_type, *args):
        if self.fail:
            raise OSError("test disk failure")
        if exc_type is None:
            self.data["webui_setting"]["character_avatars"] = self.webui_character_avatars.value
            self.path.write_text(json.dumps(self.data), encoding="utf-8")


class AvatarTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.live = self.root / "live2d_related"
        self.head = self.root / "GPT_SoVITS/assets/char_headprof"
        self.character_root = self.live / "anon"
        self.download = self.head / "webui_chat_mode_avatars/anon"
        self.character_root.mkdir(parents=True)
        self.download.mkdir(parents=True)
        for name, value in (("PROJECT_ROOT", self.root), ("LIVE2D_ROOT", self.live),
                            ("CHAR_HEADPROF_ROOT", self.head)):
            self.stack.enter_context(patch.object(assets_module, name, value))
        self.config = DiskConfig(self.root / "d_sakiko_config.json")
        self.stack.enter_context(patch.dict(sys.modules, {"qconfig": SimpleNamespace(
            d_sakiko_config=self.config, create_d_sakiko_config_snapshot=self.config.snapshot)}))
        self.character = SimpleNamespace(character_folder_name="anon", character_name="爱音")
        self.registry = AssetRegistry()
        self.runtime = HeadlessRuntime(self.registry)
        self.addCleanup(self.runtime.uploads.close)
        self.runtime.status = "ready"
        self.runtime.character_by_id = {"anon": self.character}

    def write(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG)
        return path.relative_to(self.root).as_posix()

    def snapshot(self):
        return self.runtime.settings_snapshot(avatar_character_id="anon")["avatar"]

    def save(self, avatar_id):
        return self.runtime.update_settings(speech_speed=None, sentence_pause_seconds=None,
                                            llm_choice_id=None,
                                            avatar={"character_id": "anon", "avatar_id": avatar_id})["avatar"]

    def test_catalog_combines_sources_deduplicates_and_rescans(self):
        preferred = self.write(self.character_root / "anon_icon.png")
        self.write(self.character_root / "second.png")
        self.write(self.head / "爱音.png")
        self.write(self.head / "其他角色.png")
        self.write(self.character_root / "live2D_model/texture.png")
        self.write(self.download / "one.png")
        first = self.snapshot()
        self.assertEqual(len(first["options"]), 4)
        self.assertEqual(first["selected_id"], preferred)
        self.write(self.download / "nested/two.png")
        (self.download / "one.png").unlink()
        second = self.snapshot()
        self.assertEqual(len(second["options"]), 4)
        self.assertTrue(any(item["name"] == "two" for item in second["options"]))
        self.assertNotEqual(first["options"][0]["image_url"], second["options"][0]["image_url"])

    def test_saved_choice_survives_new_runtime_and_preserves_other_settings(self):
        self.write(self.head / "爱音.png")
        chosen = self.write(self.download / "choice.png")
        self.runtime.phase = "generating"
        result = self.save(chosen)
        self.assertEqual(result["selected_id"], chosen)
        stored = json.loads(self.config.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["unrelated"], "keep")
        self.assertEqual(stored["webui_setting"]["character_avatars"], {"other": "keep.png", "anon": chosen})
        entity = AssetRegistry().register_character(self.character, self.config.snapshot().webui_character_avatars.value["anon"])
        self.assertEqual(entity["avatar_id"], chosen)
        self.assertEqual(self.runtime.events.get_nowait()["type"], "character_updated")

    def test_deleted_selection_falls_back_and_stale_or_foreign_selection_is_rejected(self):
        fallback = self.write(self.head / "爱音.png")
        chosen = self.write(self.download / "choice.png")
        self.save(chosen)
        (self.download / "choice.png").unlink()
        self.assertEqual(self.snapshot()["selected_id"], fallback)
        foreign = self.write(self.head / "其他角色.png")
        for value in (chosen, foreign, "../../outside.png"):
            with self.assertRaisesRegex(Exception, "头像已不存在"):
                self.save(value)
        self.assertEqual(self.config.snapshot().webui_character_avatars.value["anon"], chosen)

    def test_save_failure_does_not_publish_selection(self):
        chosen = self.write(self.download / "choice.png")
        self.config.fail = True
        with self.assertRaisesRegex(Exception, "头像配置保存失败"):
            self.save(chosen)
        self.assertEqual(self.runtime.character_entities, {})
        self.assertTrue(self.runtime.events.empty())

    def test_empty_and_download_only_catalog_preserves_default_fallback(self):
        self.assertEqual(self.snapshot()["options"], [])
        self.write(self.download / "choice.png")
        self.assertIsNone(self.snapshot()["selected_id"])

    def test_existing_settings_and_media_routes_cover_full_flow_without_cache(self):
        chosen = self.write(self.download / "choice.png")
        app = create_app(self.runtime, AccessController("123456"), initialize_runtime=False)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/settings?avatar_character_id=anon").status_code, 401)
            self.assertEqual(client.patch("/api/v1/settings", json={"avatar": {"character_id": "anon", "avatar_id": chosen}}).status_code, 401)
            client.post("/api/v1/session", json={"access_code": "123456"})
            response = client.get("/api/v1/settings?avatar_character_id=anon")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(client.get("/api/v1/settings?avatar_character_id=missing").status_code, 400)
            response = client.patch("/api/v1/settings", json={"avatar": {"character_id": "anon", "avatar_id": chosen}})
            self.assertEqual(response.status_code, 200)
            url = response.json()["avatar"]["character"]["avatar_url"]
            image = client.get(url)
            self.assertEqual(image.content, PNG)
            self.assertEqual(image.headers["cache-control"], "no-store")
            (self.download / "choice.png").write_bytes(PNG + b"new")
            self.assertEqual(client.get(url).content, PNG + b"new")
            (self.download / "choice.png").unlink()
            self.assertEqual(client.get(url).status_code, 404)
