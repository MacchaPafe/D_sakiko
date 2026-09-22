"""音频生成逐请求读音覆盖接口的单元测试。"""

from __future__ import annotations

import inspect
import unittest

from audio_generator import AudioGenerate
from character import CharacterAttributes


class AudioPronunciationOverridesTest(unittest.TestCase):
    """验证动态读音覆盖的替换顺序与兼容接口。"""

    def test_longer_source_text_is_replaced_first(self) -> None:
        """较长称呼应优先替换，避免被短角色名提前破坏。"""

        result = AudioGenerate._apply_pronunciation_overrides(
            "祥ちゃん和祥",
            {
                "祥": "さき",
                "祥ちゃん": "さきちゃん",
            },
        )
        self.assertEqual(result, "さきちゃん和さき")

    def test_missing_overrides_preserves_text(self) -> None:
        """没有传入覆盖时应保持原文本不变。"""

        self.assertEqual(AudioGenerate._apply_pronunciation_overrides("立希", None), "立希")

    def test_dynamic_override_precedes_global_japanese_replacements(self) -> None:
        """动态长称呼读音应在全局角色名替换之前生效。"""

        audio_generator = AudioGenerate.__new__(AudioGenerate)
        audio_generator.replacements_jap = {"祥": "さきこ"}
        audio_generator.replacements_chi = {}
        character = CharacterAttributes()
        character.character_name = "爱音"
        normalized, is_valid = audio_generator._normalize_text_for_generation(
            "祥ちゃん",
            "日英混合",
            character,
            pronunciation_overrides={"祥ちゃん": "さきちゃん"},
        )
        self.assertTrue(is_valid)
        self.assertEqual(normalized, "さきちゃん")

    def test_sync_entry_points_expose_optional_mapping(self) -> None:
        """两个同步高层入口都应预留相同的可选覆盖参数。"""

        character_signature = inspect.signature(AudioGenerate.generate_audio_for_character_sync)
        regenerate_signature = inspect.signature(AudioGenerate.generate_audio_sync)
        self.assertIn("pronunciation_overrides", character_signature.parameters)
        self.assertIn("pronunciation_overrides", regenerate_signature.parameters)
        self.assertIsNone(character_signature.parameters["pronunciation_overrides"].default)
        self.assertIsNone(regenerate_signature.parameters["pronunciation_overrides"].default)


if __name__ == "__main__":
    unittest.main()
