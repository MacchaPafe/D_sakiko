from __future__ import annotations

import hashlib
from functools import lru_cache

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap


AVATAR_COLORS: tuple[str, ...] = (
    "#7799CC",
    "#FF8899",
    "#FFB36B",
    "#77BBDD",
    "#77AA88",
    "#AA88CC",
    "#D68A9A",
    "#8BA6D6",
    "#C6A15B",
    "#7EA6A6",
)


def first_visible_character(character_name: str) -> str:
    """
    获取角色名称中用于头像展示的第一个可见字符。
    """
    for one_character in character_name.strip():
        if not one_character.isspace():
            return one_character.upper()
    return "?"


def stable_color_for_name(character_name: str) -> QColor:
    """
    根据角色名称生成稳定的头像背景色。
    """
    name_for_hash = character_name.strip() or "?"
    digest = hashlib.sha1(name_for_hash.encode("utf-8")).digest()
    color_index = digest[0] % len(AVATAR_COLORS)
    return QColor(AVATAR_COLORS[color_index])


def softened_avatar_color(color_hex: str, character_name: str) -> QColor:
    """
    根据角色主题色生成低饱和度头像背景色。
    """
    color = QColor(color_hex)
    if not color.isValid():
        color = stable_color_for_name(character_name)

    hue = color.hue()
    saturation = color.saturation()
    lightness = color.lightness()
    if saturation == 0 or hue < 0:
        return QColor.fromHsl(-1, 0, max(150, min(210, lightness)))

    softened_saturation = max(55, min(120, int(saturation * 0.55)))
    softened_lightness = max(135, min(190, int(lightness * 0.75 + 70)))
    return QColor.fromHsl(hue, softened_saturation, softened_lightness)


def readable_text_color(background: QColor) -> QColor:
    """
    根据背景色亮度选择可读的头像文字颜色。
    """
    luminance = (
        0.299 * background.red()
        + 0.587 * background.green()
        + 0.114 * background.blue()
    )
    if luminance > 170:
        return QColor("#334155")
    return QColor("#FFFFFF")


@lru_cache(maxsize=256)
def build_initial_avatar(
    character_name: str,
    size: int,
    device_pixel_ratio: float = 1.0,
    color_hex: str = "",
) -> QPixmap:
    """
    使用角色名称首字符绘制聊天侧栏头像。
    """
    safe_size = max(12, int(size))
    safe_ratio = max(1.0, float(device_pixel_ratio))
    pixmap_size = int(safe_size * safe_ratio)
    pixmap = QPixmap(pixmap_size, pixmap_size)
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(safe_ratio)

    if color_hex:
        background_color = softened_avatar_color(color_hex, character_name)
    else:
        background_color = stable_color_for_name(character_name)
    text_color = readable_text_color(background_color)
    avatar_text = first_visible_character(character_name)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(background_color)
    radius = safe_size * 0.24
    painter.drawRoundedRect(QRectF(0, 0, safe_size, safe_size), radius, radius)

    font = QFont()
    font.setBold(True)
    font.setPixelSize(max(10, int(safe_size * 0.45)))
    painter.setFont(font)
    painter.setPen(text_color)
    painter.drawText(QRectF(0, 0, safe_size, safe_size), Qt.AlignCenter, avatar_text)
    painter.end()

    return pixmap


def clear_avatar_cache() -> None:
    """
    清空首字符头像缓存。
    """
    build_initial_avatar.cache_clear()
