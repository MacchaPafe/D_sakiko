from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from character import CharacterAttributes, EMOTION_REFERENCE_KEYS, GetCharacterAttributes
from qconfig import d_sakiko_config


class CharacterVoiceConfigTestCase(unittest.TestCase):
    """验证语音辅助配置缺失时的角色加载与语音能力判断。"""

    def test_missing_reference_metadata_does_not_skip_character(self) -> None:
        """缺少参考文本和语言文件时仍应加载角色，但禁用语音生成。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            working_dir = project_root / "GPT_SoVITS"
            character_dir = project_root / "live2d_related" / "test_character"
            live2d_dir = character_dir / "live2D_model"
            voice_dir = project_root / "reference_audio" / "test_character"
            model_dir = voice_dir / "GPT-SoVITS_models"

            working_dir.mkdir()
            live2d_dir.mkdir(parents=True)
            model_dir.mkdir(parents=True)
            (character_dir / "name.txt").write_text("测试角色", encoding="utf-8")
            (character_dir / "character_description.txt").write_text(
                "用于测试的角色描述",
                encoding="utf-8",
            )
            (live2d_dir / "model.model.json").write_text(
                json.dumps({"motions": {}}),
                encoding="utf-8",
            )
            (model_dir / "model.ckpt").touch()
            (model_dir / "model.pth").touch()
            (voice_dir / "reference.mp3").touch()

            loader = object.__new__(GetCharacterAttributes)
            loader.character_num = 0
            loader.character_class_list = []
            loader.user_characters = []

            original_cwd = os.getcwd()
            original_order = d_sakiko_config.character_order.value
            original_users = d_sakiko_config.user_characters.value
            original_live2d_paths = d_sakiko_config.l2d_json_paths_dict.value
            try:
                os.chdir(working_dir)
                d_sakiko_config.character_order.value = {
                    "character_num": 0,
                    "character_names": [],
                }
                d_sakiko_config.user_characters.value = []
                d_sakiko_config.l2d_json_paths_dict.value = {}
                with mock.patch.object(d_sakiko_config, "set"):
                    loader.load_data()
            finally:
                os.chdir(original_cwd)
                d_sakiko_config.character_order.value = original_order
                d_sakiko_config.user_characters.value = original_users
                d_sakiko_config.l2d_json_paths_dict.value = original_live2d_paths

            self.assertEqual(len(loader.character_class_list), 1)
            character = loader.character_class_list[0]
            self.assertEqual(character.character_name, "测试角色")
            self.assertIsInstance(character.gptsovits_ref_audio_text, dict)
            self.assertFalse(any(character.gptsovits_ref_audio_text.values()))
            self.assertIsNone(character.gptsovits_ref_audio_lan)
            self.assertFalse(character.has_valid_voice_model())

    def test_reference_text_and_language_are_required_for_voice(self) -> None:
        """参考文本或语言缺失时语音能力判定应为不可用。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            character = CharacterAttributes()
            character.character_name = "测试角色"
            character.GPT_model_path = str(root / "model.ckpt")
            character.sovits_model_path = str(root / "model.pth")
            character.gptsovits_ref_audio = str(root / "reference.wav")
            character.gptsovits_ref_audio_text = str(root / "reference_text.txt")
            character.gptsovits_ref_audio_lan = "日文"

            for path in (
                character.GPT_model_path,
                character.sovits_model_path,
                character.gptsovits_ref_audio,
                character.gptsovits_ref_audio_text,
            ):
                Path(path).touch()
            Path(character.gptsovits_ref_audio_text).write_text("参考文本", encoding="utf-8")

            self.assertTrue(character.has_valid_voice_model())

            Path(character.gptsovits_ref_audio_text).unlink()
            self.assertFalse(character.has_valid_voice_model())

            Path(character.gptsovits_ref_audio_text).write_text("参考文本", encoding="utf-8")
            character.gptsovits_ref_audio_lan = None
            self.assertFalse(character.has_valid_voice_model())

            character.character_name = "祥子"
            self.assertFalse(character.has_valid_voice_model())

    def test_legacy_reference_config_migrates_to_emotion_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            working_dir = root / "GPT_SoVITS"
            voice_dir = root / "reference_audio" / "test_character"
            working_dir.mkdir()
            voice_dir.mkdir(parents=True)

            audio_path = voice_dir / "reference.wav"
            audio_path.touch()
            default_file = voice_dir / "default_ref_audio.txt"
            text_file = voice_dir / "reference_text.txt"
            default_file.write_text(str(audio_path), encoding="utf-8")
            text_file.write_text("参考文本", encoding="utf-8")

            character = CharacterAttributes()
            character.character_folder_name = "test_character"
            character.character_name = "测试角色"

            original_cwd = os.getcwd()
            try:
                os.chdir(working_dir)
                character.reload_emotion_reference_config_from_disk()
            finally:
                os.chdir(original_cwd)

            config_path = voice_dir / "reference_audio_and_text.json"
            self.assertTrue(config_path.exists())
            self.assertFalse(default_file.exists())
            self.assertFalse(text_file.exists())
            self.assertIsInstance(character.gptsovits_ref_audio, dict)
            self.assertIsInstance(character.gptsovits_ref_audio_text, dict)
            self.assertEqual(set(character.gptsovits_ref_audio), set(EMOTION_REFERENCE_KEYS))
            self.assertEqual(character.gptsovits_ref_audio["anger"], str(audio_path))
            self.assertEqual(character.gptsovits_ref_audio_text["anger"], "参考文本")


if __name__ == "__main__":
    unittest.main()
