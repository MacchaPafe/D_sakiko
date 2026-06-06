from __future__ import annotations

import math

from PyQt5.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QLabel, QWidget
from qfluentwidgets import FluentIconBase, RangeConfigItem, SettingCard, Slider, qconfig


class FloatRangeSettingCard(SettingCard):
    """使用整数滑块映射浮点配置值的设置卡。"""

    valueChanged = pyqtSignal(float)

    def __init__(
        self,
        config_item: RangeConfigItem,
        icon: str | QIcon | FluentIconBase,
        title: str,
        content: str | None = None,
        parent: QWidget | None = None,
        *,
        single_step: float = 0.05,
        decimals: int = 2,
    ) -> None:
        """初始化浮点范围设置卡并绑定配置项。"""
        super().__init__(icon, title, content, parent)
        if single_step <= 0:
            raise ValueError("single_step 必须大于 0。")
        if decimals < 0:
            raise ValueError("decimals 不能小于 0。")

        self.config_item = config_item
        self.minimum = float(config_item.range[0])
        self.maximum = float(config_item.range[1])
        self.single_step = float(single_step)
        self.decimals = decimals
        self.step_count = round((self.maximum - self.minimum) / self.single_step)

        mapped_maximum = self.minimum + self.step_count * self.single_step
        if not math.isclose(mapped_maximum, self.maximum, abs_tol=10 ** (-(decimals + 2))):
            raise ValueError("配置范围必须能被 single_step 整除。")

        self.slider = Slider(Qt.Horizontal, self)
        self.value_label = QLabel(self)
        self.slider.setMinimumWidth(268)
        self.slider.setRange(0, self.step_count)
        self.slider.setSingleStep(1)

        self.hBoxLayout.addStretch(1)
        self.hBoxLayout.addWidget(self.value_label, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(6)
        self.hBoxLayout.addWidget(self.slider, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

        self.value_label.setObjectName("valueLabel")
        config_item.valueChanged.connect(self._sync_from_config)
        self.slider.valueChanged.connect(self._on_slider_value_changed)
        self.setValue(float(config_item.value))

    def _value_to_slider_index(self, value: float) -> int:
        """将浮点配置值转换为滑块的整数刻度。"""
        clamped_value = min(max(value, self.minimum), self.maximum)
        return round((clamped_value - self.minimum) / self.single_step)

    def _slider_index_to_value(self, index: int) -> float:
        """将滑块整数刻度转换为浮点配置值。"""
        return round(self.minimum + index * self.single_step, self.decimals)

    def _sync_from_config(self, value: object) -> None:
        """将配置值同步到滑块和数值标签，且不触发配置写回。"""
        slider_index = self._value_to_slider_index(float(value))
        normalized_value = self._slider_index_to_value(slider_index)
        blocker = QSignalBlocker(self.slider)
        self.slider.setValue(slider_index)
        del blocker
        self.value_label.setText(f"{normalized_value:.{self.decimals}f}")
        self.value_label.adjustSize()

    def _on_slider_value_changed(self, index: int) -> None:
        """处理用户滑动并保存映射后的浮点配置值。"""
        value = self._slider_index_to_value(index)
        qconfig.set(self.config_item, value)
        self._sync_from_config(value)
        self.valueChanged.emit(value)

    def setValue(self, value: float) -> None:
        """将值对齐到最近刻度并保存到配置。"""
        normalized_value = self._slider_index_to_value(self._value_to_slider_index(value))
        qconfig.set(self.config_item, normalized_value)
        self._sync_from_config(normalized_value)

    def value(self) -> float:
        """返回当前滑块对应的浮点值。"""
        return self._slider_index_to_value(self.slider.value())
