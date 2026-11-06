"""小剧场界面的 SVG 图标加载和按字号缩放。"""
from functools import lru_cache
from pathlib import Path

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer


DELETE_COLOR = "#D9534F"


@lru_cache(maxsize=None)
def multi_char_icon(name):
    """缓存小剧场 SVG 图标，播放时切换图标不反复解析文件。"""
    path = Path(__file__).parents[1] / "icons" / f"multi_char_{name}.svg"
    return QIcon(str(path))


@lru_cache(maxsize=1)
def delete_message_icon():
    """复用原删除 SVG 的透明轮廓，动态染红并缓存常用 DPI 尺寸。"""
    renderer = QSvgRenderer(str(Path(__file__).parents[1] / "icons" / "delete.svg"))
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(DELETE_COLOR))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def set_button_icon(button, name, font_source):
    """为按钮或复选框挂载 SVG，并跟随所在窗口的字号设置图标尺寸。"""
    size = round(font_source.fontMetrics().height() * 0.85)
    button.setIcon(multi_char_icon(name))
    button.setIconSize(QSize(size, size))
