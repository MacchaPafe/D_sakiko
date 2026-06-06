from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PyQt5.QtWidgets import QApplication
from qfluentwidgets import ConfigItem, FluentIcon, RangeConfigItem, RangeValidator, qconfig

from ui.custom_widgets.float_range_setting_card import FloatRangeSettingCard


class FloatRangeSettingCardTestCase(unittest.TestCase):
    """验证浮点范围设置卡的刻度映射与配置同步。"""

    app: QApplication
    original_qconfig_set: object

    @classmethod
    def setUpClass(cls) -> None:
        """创建 Qt 应用并替换配置写入函数。"""
        cls.app = QApplication.instance() or QApplication([])
        cls.original_qconfig_set = qconfig.set

        def set_config_value(
            item: ConfigItem,
            value: object,
            save: bool = True,
            copy: bool = True,
        ) -> None:
            """在测试中直接更新配置项。"""
            del save, copy
            item.value = value

        qconfig.set = set_config_value

    @classmethod
    def tearDownClass(cls) -> None:
        """恢复全局配置写入函数。"""
        qconfig.set = cls.original_qconfig_set

    def test_slider_maps_integer_ticks_to_float_values(self) -> None:
        """滑块整数刻度应按指定步长映射为浮点值。"""
        config_item = RangeConfigItem(
            "test",
            "top_p",
            0.7,
            validator=RangeValidator(0.0, 1.0),
        )
        card = FloatRangeSettingCard(
            config_item,
            FluentIcon.TILES,
            "Top P",
            single_step=0.05,
            decimals=2,
        )

        self.assertEqual(card.slider.value(), 14)
        self.assertEqual(card.value_label.text(), "0.70")

        card.slider.setValue(15)

        self.assertEqual(config_item.value, 0.75)
        self.assertEqual(card.value(), 0.75)
        self.assertEqual(card.value_label.text(), "0.75")
        card.deleteLater()

    def test_config_changes_update_slider_without_losing_precision(self) -> None:
        """外部配置变化应同步滑块位置和显示文本。"""
        config_item = RangeConfigItem(
            "test",
            "temperature",
            1.0,
            validator=RangeValidator(0.0, 2.0),
        )
        card = FloatRangeSettingCard(
            config_item,
            FluentIcon.CALORIES,
            "Temperature",
            single_step=0.05,
            decimals=2,
        )

        config_item.value = 1.35

        self.assertEqual(card.slider.value(), 27)
        self.assertEqual(card.value(), 1.35)
        self.assertEqual(card.value_label.text(), "1.35")
        card.deleteLater()


if __name__ == "__main__":
    unittest.main()
