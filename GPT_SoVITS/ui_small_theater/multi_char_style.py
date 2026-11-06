"""编排窗口与添加台词弹窗共用的主题模板。"""
from pathlib import Path

from PyQt5.QtGui import QColor
from ui_constants import char_info_json


def editor_stylesheet(font_pixels, names):
    """按统一逻辑字号展开编排 QSS，不绑定父窗口的尺寸或控件样式。"""
    stylesheet = Path(__file__).parent / "styles" / "multi_char_editor.qss"
    style = stylesheet.read_text(encoding="utf-8-sig").replace("@FONT_PX@", str(font_pixels))
    style = style.replace("@SMALL_PX@", str(round(font_pixels * 0.85)))
    style = style.replace("@PAD_Y@", str(round(font_pixels * 0.25)))
    style = style.replace("@PAD_X@", str(round(font_pixels * 0.6)))
    for index, name in enumerate(names):
        color = QColor(char_info_json.get(name, {}).get("theme_color", "#5A7FE7"))
        if not color.isValid():
            color = QColor("#5A7FE7")
        brightness = color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114
        style = style.replace(f"@ROLE_{index}_COLOR@", color.name())
        style = style.replace(f"@ROLE_{index}_TEXT@", "#334155" if brightness >= 145 else "#FFFFFF")
    check_icon = Path(__file__).parents[1] / "icons" / "multi_char_check.svg"
    return style.replace("@CHECK_ICON@", check_icon.as_posix())
