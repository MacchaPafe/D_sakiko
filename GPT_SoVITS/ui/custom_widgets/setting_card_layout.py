"""为带右侧控件的 Fluent 设置卡提供可换行的文字布局。"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QSizePolicy, QWIDGETSIZE_MAX
from qfluentwidgets import SettingCard


def configure_wrapping_setting_card(card: SettingCard) -> None:
    """在右侧控件添加完毕后，让文字填满剩余宽度并按换行自然增高。"""
    # SettingCard 的整体垂直居中会把文字布局限制在单行高度；
    # 让外层填满卡片，仅在文字列内部居中，交由 Qt 计算换行高度。
    card.hBoxLayout.setAlignment(Qt.Alignment(0))
    card.hBoxLayout.setContentsMargins(16, 12, 0, 12)
    for index in range(card.hBoxLayout.count()):
        card.hBoxLayout.setStretch(index, 1 if index == 2 else 0)
    for label in (card.titleLabel, card.contentLabel):
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        card.vBoxLayout.setAlignment(label, Qt.Alignment(0))
    card.setMinimumHeight(90)
    card.setMaximumHeight(QWIDGETSIZE_MAX)
    card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
