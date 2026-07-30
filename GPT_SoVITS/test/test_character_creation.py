from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from character_creation import (
    CharacterCreationError,
    create_character_resources,
    discover_complete_character_records,
)


class CharacterCreationTestCase(unittest.TestCase):
    """测试角色资源创建和待启用角色发现。"""

    def test_create_character_writes_required_files_and_empty_voice_structure(self) -> None:
        """验证角色目录原子落盘，且空语音目录不会伪造模型配置。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live2d_root = root / "live2d_related"
            voice_root = root / "reference_audio"
            avatar_path = root / "avatar.png"
            avatar_path.write_bytes(b"avatar")

            record = create_character_resources(
                character_name="三角初华",
                character_folder_name="uika",
                character_description="擅长隐藏真实想法的吉他手。",
                avatar_source_path=str(avatar_path),
                live2d_related_dir=str(live2d_root),
                reference_audio_dir=str(voice_root),
            )

            character_dir = live2d_root / "uika"
            voice_model_dir = voice_root / "uika" / "GPT-SoVITS_models"
            self.assertEqual(record.character_folder_name, "uika")
            self.assertEqual((character_dir / "name.txt").read_text(encoding="utf-8"), "三角初华")
            self.assertTrue((character_dir / "character_description.txt").is_file())
            self.assertTrue((character_dir / "avatar.png").is_file())
            self.assertTrue((voice_model_dir / "README.txt").is_file())
            self.assertEqual(list(voice_model_dir.glob("*.ckpt")), [])
            self.assertEqual(list(voice_model_dir.glob("*.pth")), [])
            self.assertEqual(
                [item.character_folder_name for item in discover_complete_character_records(str(live2d_root))],
                ["uika"],
            )

            create_character_resources(
                character_name="若叶睦",
                character_folder_name="mutsumi",
                character_description="沉默寡言。",
                live2d_related_dir=str(live2d_root),
                reference_audio_dir=str(voice_root),
            )
            (character_dir / "live2D_model").mkdir()
            self.assertEqual(
                [
                    item.character_folder_name
                    for item in discover_complete_character_records(str(live2d_root))
                ],
                ["uika", "mutsumi"],
            )

    def test_create_character_rejects_empty_description_and_duplicate_name(self) -> None:
        """验证角色描述必须由用户填写，显示名称也必须唯一。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live2d_root = root / "live2d_related"
            voice_root = root / "reference_audio"

            with self.assertRaises(CharacterCreationError):
                create_character_resources(
                    character_name="若叶睦",
                    character_folder_name="mutsumi",
                    character_description="   ",
                    live2d_related_dir=str(live2d_root),
                    reference_audio_dir=str(voice_root),
                )

            create_character_resources(
                character_name="若叶睦",
                character_folder_name="mutsumi",
                character_description="沉默寡言。",
                live2d_related_dir=str(live2d_root),
                reference_audio_dir=str(voice_root),
            )
            with self.assertRaises(CharacterCreationError):
                create_character_resources(
                    character_name="若叶睦",
                    character_folder_name="mutsumi_2",
                    character_description="另一份描述。",
                    live2d_related_dir=str(live2d_root),
                    reference_audio_dir=str(voice_root),
                )


if __name__ == "__main__":
    unittest.main()
