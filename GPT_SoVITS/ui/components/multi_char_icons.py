"""小剧场界面的 SVG 图标加载和按字号缩放。"""
from functools import lru_cache
from pathlib import Path

from PyQt5.QtCore import QSize
from PyQt5.QtGui import QIcon


@lru_cache(maxsize=None)
def multi_char_icon(name):
    """缓存小剧场 SVG 图标，播放时切换图标不反复解析文件。"""
    path = Path(__file__).parents[2] / "icons" / f"multi_char_{name}.svg"
    return QIcon(str(path))


def set_button_icon(button, name, font_source):
    """为按钮或复选框挂载 SVG，并跟随所在窗口的字号设置图标尺寸。"""
    size = round(font_source.fontMetrics().height() * 0.85)
    button.setIcon(multi_char_icon(name))
    button.setIconSize(QSize(size, size))
