"""小剧场界面的 SVG 图标加载和按字号缩放。"""
from functools import lru_cache
from pathlib import Path

from PyQt5.QtCore import QRectF, QSize, Qt
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
    return _tinted_icon("delete.svg", DELETE_COLOR)


@lru_cache(maxsize=16)
def speaker_switch_icon(color):
    """使用与角色名同色的现有换角图标。"""
    return _tinted_icon("change_char.svg", color)


@lru_cache(maxsize=1)
def reference_audio_settings_icon():
    """复用项目设置图标，与小剧场操作按钮使用同一文字色。"""
    return _tinted_icon("setting.svg", "#7799CC")


def _tinted_icon(filename, color):
    renderer = QSvgRenderer(str(Path(__file__).parents[1] / "icons" / filename))
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(color))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def set_button_icon(button, name, font_source):
    """为按钮或复选框挂载 SVG，并跟随所在窗口的字号设置图标尺寸。"""
    size = round(font_source.fontMetrics().height() * 0.85)
    button.setIcon(multi_char_icon(name))
    button.setIconSize(QSize(size, size))


@lru_cache(maxsize=64)
def color_dot_icon(color_hex: str, diameter: int = 8) -> QIcon:
    """绘制高 DPI 抗锯齿的角色主题色圆点图标，供未点亮胶囊和角色菜单共用。"""
    scale = 2
    canvas = (diameter + 2) * scale
    pixmap = QPixmap(canvas, canvas)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color_hex))
    margin = scale
    painter.drawEllipse(QRectF(margin, margin, diameter * scale, diameter * scale))
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)
