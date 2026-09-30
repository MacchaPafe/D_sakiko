from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QLockFile
from PyQt5.QtWidgets import QApplication
from qfluentwidgets import EditableComboBox

from chat import attachments
from deepseek_models import DEEPSEEK_DEPRECATED_MODEL_ALIASES
from qconfig import DSakikoConfig, normalize_deepseek_model_config
from ui.components import llm_api_area


class DeepSeekModelMigrationTestCase(unittest.TestCase):
    """验证旧模型配置的持久化迁移和设置页模型预设。"""

    @classmethod
    def setUpClass(cls) -> None:
        """创建供下拉框测试使用的 Qt 应用。"""
        cls.app = QApplication.instance() or QApplication([])

    def test_legacy_names_are_persisted_once_without_changing_other_models(self) -> None:
        """旧名称一步迁移到 Flash，重复迁移无写入且第三方模型保持原值。"""
        config = DSakikoConfig()
        original_models = config.llm_api_model.value
        self.addCleanup(setattr, config.llm_api_model, "value", original_models)
        with tempfile.TemporaryDirectory() as directory:
            config.file = Path(directory) / "config.json"
            config.lock = QLockFile(str(Path(directory) / "config.lock"))
            for legacy_name in DEEPSEEK_DEPRECATED_MODEL_ALIASES:
                with self.subTest(legacy_name=legacy_name):
                    models = {
                        "deepseek": f" {legacy_name} ",
                        "modelscope": legacy_name,
                        "openai": "gpt-5",
                    }
                    config.file.write_text(json.dumps({
                        "llm_setting": {
                            "llm_api_model": models,
                            "custom_llm_api_model": legacy_name,
                        },
                    }), encoding="utf-8")
                    original_custom = config.custom_llm_api_model.value
                    try:
                        with config:
                            normalize_deepseek_model_config(config)
                        saved = json.loads(config.file.read_text(encoding="utf-8"))
                        expected = dict(models, deepseek="deepseek-flash")
                        self.assertEqual(saved["llm_setting"]["llm_api_model"], expected)
                        self.assertEqual(saved["llm_setting"]["custom_llm_api_model"], legacy_name)
                        with mock.patch.object(config, "set") as set_item:
                            normalize_deepseek_model_config(config)
                        set_item.assert_not_called()
                    finally:
                        config.custom_llm_api_model.value = original_custom

    def test_pro_and_unknown_models_remain_unchanged(self) -> None:
        """迁移只修改已知别名，保留 Pro 和用户填写的其他模型。"""
        for model in ("deepseek-flash", "deepseek-v4-pro", "my-custom-model"):
            with self.subTest(model=model):
                config = SimpleNamespace(
                    llm_api_model=SimpleNamespace(value={"deepseek": model}),
                    set=mock.Mock(),
                )
                normalize_deepseek_model_config(config)
                config.set.assert_not_called()

    def test_dropdown_uses_current_presets_and_selects_migrated_model(self) -> None:
        """下拉框只展示 Flash 与 Pro，旧配置在界面上选中 Flash。"""
        combo = EditableComboBox()
        subject = SimpleNamespace(standard_model_combo=combo)
        for model in (*DEEPSEEK_DEPRECATED_MODEL_ALIASES, "deepseek-flash", "deepseek-v4-pro"):
            with self.subTest(model=model):
                config = SimpleNamespace(llm_api_model=SimpleNamespace(value={"deepseek": model}))
                with mock.patch.object(llm_api_area, "d_sakiko_config", config):
                    llm_api_area.LLMAPIArea.update_model_list(subject, "deepseek")
                self.assertEqual([combo.itemText(index) for index in range(combo.count())], [
                    "deepseek-flash", "deepseek-v4-pro",
                ])
                self.assertEqual(combo.currentText(), "deepseek-v4-pro" if model == "deepseek-v4-pro" else "deepseek-flash")
                self.assertFalse(combo.signalsBlocked())
        combo.deleteLater()

    def test_flash_image_support_survives_old_capability_files_and_litellm_miss(self) -> None:
        """旧附件配置和 LiteLLM 未收录新名称时，官方 Flash 仍允许图片。"""
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "capabilities.json"
            config_path.write_text('{"image_upload": {"force_allowlist": []}}', encoding="utf-8")
            with (
                mock.patch.object(attachments, "_MODEL_ATTACHMENT_CAPABILITIES_PATH", config_path),
                mock.patch.object(attachments, "_litellm_supports_vision", return_value=False),
            ):
                for model in ("deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"):
                    self.assertTrue(attachments.model_supports_image_upload(f"deepseek/{model}"))
                    self.assertFalse(attachments.model_supports_image_upload(
                        f"deepseek/{model}", use_default_deepseek_api=True,
                    ))
                self.assertFalse(attachments.model_supports_image_upload("deepseek/deepseek-v4-pro"))
                self.assertFalse(attachments.model_supports_image_upload("openai/deepseek-ai/DeepSeek-V4-Flash"))
