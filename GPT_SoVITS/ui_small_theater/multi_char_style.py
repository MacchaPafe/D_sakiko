"""小剧场窗口共用的字体和主题模板。"""
from pathlib import Path

from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import QApplication, QWidget
from ui_constants import char_info_json


def editor_font(window):
    """按主控制台所在屏幕计算编排与设置面板共用的逻辑字号。"""
    screen = QWidget.screen(window) or QApplication.primaryScreen()
    font = QFont(window.font())
    font.setPixelSize(max(14, min(36, round(screen.availableGeometry().height() * 0.016))))
    return font


def _scale_padding(style, font_pixels):
    return (style.replace("@PAD_Y@", str(round(font_pixels * 0.25)))
            .replace("@PAD_X@", str(round(font_pixels * 0.6))))


def mode_bar_stylesheet(font_pixels, *, text_color=None):
    """共用场景和字幕分段样式，字幕可覆盖各状态的文字色。"""
    stylesheet = Path(__file__).parent / "styles" / "multi_char_mode_bar.qss"
    style = stylesheet.read_text(encoding="utf-8").replace("@FONT_PX@", str(font_pixels))
    style = style.replace("@TEXT@", text_color or "#566B8A")
    style = style.replace("@HOVER_TEXT@", text_color or "#2C3A52")
    style = style.replace("@CHECKED_TEXT@", text_color or "#254EDB")
    return _scale_padding(style, font_pixels)


def editor_stylesheet(font_pixels, names):
    """按统一逻辑字号展开编排 QSS，不绑定父窗口的尺寸或控件样式。"""
    stylesheet = Path(__file__).parent / "styles" / "multi_char_editor.qss"
    style = stylesheet.read_text(encoding="utf-8-sig").replace("@FONT_PX@", str(font_pixels))
    style = style.replace("@SMALL_PX@", str(round(font_pixels * 0.85)))
    style = _scale_padding(style, font_pixels)
    for index, name in enumerate(names):
        color = QColor(char_info_json.get(name, {}).get("theme_color", "#5A7FE7"))
        if not color.isValid():
            color = QColor("#5A7FE7")
        brightness = color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114
        style = style.replace(f"@ROLE_{index}_COLOR@", color.name())
        style = style.replace(f"@ROLE_{index}_TEXT@", "#334155" if brightness >= 145 else "#FFFFFF")
    check_icon = Path(__file__).parents[1] / "icons" / "multi_char_check.svg"
    return style.replace("@CHECK_ICON@", check_icon.as_posix()) + "\n" + mode_bar_stylesheet(font_pixels)
