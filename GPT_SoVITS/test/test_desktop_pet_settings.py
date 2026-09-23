"""验证桌宠设置页的排列、文案和独立配置文件中的持久化。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent, QLockFile, pyqtSignal
from PyQt5.QtWidgets import QApplication
from qfluentwidgets import InfoBarIcon

from dsakiko_configuration import DSakikoConfigArea
from qconfig import DSakikoConfig
from ui.custom_widgets.transparent_scroll_area import TransparentScrollArea


class PlaceholderSettings(TransparentScrollArea):
    """代替不相关设置页，避免测试启动硬件检测或外部服务。"""

    status_signal = pyqtSignal(InfoBarIcon, str)

    def load_config_to_ui(self) -> None:
        """占位页没有需要加载的数据。"""

    def save_ui_to_config(self) -> bool:
        """允许设置容器完成统一保存流程。"""
        return True


class DesktopPetSettingsTests(TestCase):
    """使用真实设置容器和桌宠卡片验证页面接入。"""

    @classmethod
    def setUpClass(cls) -> None:
        """初始化离屏应用用于布局与卡片交互。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """将卡片读写重定向到临时文件，保留用户实际配置。"""
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = DSakikoConfig()
        self.config.file = Path(self.directory.name) / "config.json"
        self.config.lock = QLockFile(str(Path(self.directory.name) / "config.lock"))
        self.original_start = self.config.start_in_pet_mode.value
        self.original_idle = self.config.unload_voice_models_when_pet_idle.value
        self.config.start_in_pet_mode.value = False
        self.config.unload_voice_models_when_pet_idle.value = True
        for target, replacement in (
            ("ui.components.desktop_pet_setting_area.d_sakiko_config", self.config),
            (
                "ui.custom_widgets.custom_switch_setting_card.d_sakiko_config",
                self.config,
            ),
            ("dsakiko_configuration.LLMAPIArea", PlaceholderSettings),
            ("dsakiko_configuration.GPTSoVITSArea", PlaceholderSettings),
            ("dsakiko_configuration.CustomSettingArea", PlaceholderSettings),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.area = DSakikoConfigArea()

    def tearDown(self) -> None:
        """断开卡片连接并恢复共享配置项，避免污染后续测试。"""
        self.area.close()
        self.area.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.config.start_in_pet_mode.value = self.original_start
        self.config.unload_voice_models_when_pet_idle.value = self.original_idle

    def test_page_order_and_short_copy(self) -> None:
        """桌宠页在语音和个性化之间，并保留用户修改后的简短文案。"""
        self.assertEqual(
            [self.area.stacked_widget.widget(i).objectName() for i in range(4)],
            [
                "LLMApiArea",
                "GPTSoVITSArea",
                "DesktopPetSettingArea",
                "customSettingArea",
            ],
        )
        page = self.area.desktop_pet_setting_area
        self.assertEqual(
            page.idle_voice_unload_card.titleLabel.text(), "空闲时释放语音模型"
        )
        self.assertEqual(
            page.idle_voice_unload_card.contentLabel.text(),
            "长时间未对话时，释放语音模型以节约资源占用",
        )
        self.assertEqual(
            page.start_in_pet_mode_card.titleLabel.text(), "启动时进入桌宠形态"
        )
        self.assertEqual(
            page.start_in_pet_mode_card.contentLabel.text(), "下次启动时直接显示桌宠"
        )
        self.assertFalse(page.start_in_pet_mode_card.isChecked())
        self.assertTrue(page.idle_voice_unload_card.isChecked())
        self.assertFalse(self.config.start_in_pet_mode.defaultValue)

    def test_switches_persist_and_idle_option_keeps_existing_key(self) -> None:
        """真实开关写入临时配置，移动空闲开关不改变原存储位置。"""
        page = self.area.desktop_pet_setting_area
        page.start_in_pet_mode_card.switchButton.setChecked(True)
        page.idle_voice_unload_card.switchButton.setChecked(False)
        self.assertTrue(self.area.save_ui_to_config())
        saved = json.loads(self.config.file.read_text(encoding="utf-8"))
        self.assertTrue(saved["desktop_pet_setting"]["start_in_pet_mode"])
        self.assertFalse(saved["audio_setting"]["unload_voice_models_when_pet_idle"])
        restored = DSakikoConfig()
        restored.load(self.config.file)
        self.assertTrue(restored.start_in_pet_mode.value)
        self.assertFalse(restored.unload_voice_models_when_pet_idle.value)

    def test_pivot_navigation_and_layout(self) -> None:
        """导航与堆叠页同步，新卡片在常用窗口宽度内完整布局。"""
        self.area.resize(640, 700)
        self.area.show()
        self.area.pivot.items["DesktopPetSettingArea"].click()
        self.app.processEvents()
        page = self.area.desktop_pet_setting_area
        self.assertIs(self.area.stacked_widget.currentWidget(), page)
        self.assertEqual(self.area.pivot.currentRouteKey(), "DesktopPetSettingArea")
        for card in (page.start_in_pet_mode_card, page.idle_voice_unload_card):
            self.assertTrue(card.isVisible())
            self.assertGreater(card.width(), 400)
            self.assertLessEqual(card.geometry().right(), page.view.width())
