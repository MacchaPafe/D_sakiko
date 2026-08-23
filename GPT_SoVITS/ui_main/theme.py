from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from functools import lru_cache

from coloraide import Color


DEFAULT_CHARACTER_THEME_SEED = "#7799CC"

_HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_QT_WIDGET_COLOR_PATTERN = re.compile(
    r"QWidget\s*\{[^}]*?(?<!-)color\s*:\s*([^;]+);",
    re.DOTALL,
)
_MIN_TEXT_CONTRAST = 5.0
_MIN_SECONDARY_TEXT_CONTRAST = 3.75
_MIN_ACCENT_TEXT_CONTRAST = 4.5
_MIN_CONTROL_TEXT_CONTRAST = 4.0
_CONTRAST_SEARCH_MARGIN = 0.03
_SRGB_GAMUT_MAPPING_METHOD = "oklch-chroma"
_DARK_ON_ACCENT_CHARACTER_SEEDS = frozenset({
    "#FFEE88",  # 羽泽鸫
    "#FFEE22",  # 弦卷心
    "#FFEEAA",  # 千圣
    "#FFDD88",  # 素世
    "#FFEE55",  # 阿拉蕾
})

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ThemePalette:
    """保存由角色原色衍生出的浅色界面语义色。"""

    # 角色原色；用于主要按钮、进度和较大的身份色块。
    accent: str
    # 角色原色背景上的文字和图标色。
    on_accent: str
    # 实心主题控件处于鼠标悬停状态时的背景色。
    accent_hover: str
    # 实心主题控件处于按下状态时的背景色。
    accent_pressed: str
    # 正文、输入文字和主要标签使用的高对比文字色。
    text_primary: str
    # 译文、工具状态和辅助说明使用的次要文字色。
    text_secondary: str
    # 角色名、链接和白底主题图标使用的强调文字色。
    text_accent: str
    # 聊天框和输入控件等主要内容表面色。
    surface: str
    # 窗口和横幅使用的轻微角色染色表面色。
    surface_tint: str
    # 当前会话、候选项等选中区域的浅色背景。
    surface_selected: str
    # 普通边框、分隔线和滚动条轨道使用的低强调色。
    border_subtle: str
    # 键盘焦点和活动输入控件使用的清晰轮廓色。
    focus_ring: str


def _normalize_seed(seed: str) -> str:
    """校验角色原色并统一为大写六位十六进制格式。"""
    if not isinstance(seed, str) or _HEX_COLOR_PATTERN.fullmatch(seed) is None:
        raise ValueError("角色主题色必须是 #RRGGBB 格式的不透明 sRGB 颜色")
    return seed.upper()


def _to_hex(color: Color) -> str:
    """把任意 ColorAide 颜色映射到 sRGB 并输出大写十六进制。"""
    fitted = color.clone().fit("srgb", method=_SRGB_GAMUT_MAPPING_METHOD).convert("srgb")
    return fitted.to_string(hex=True).upper()


def _oklch_color(lightness: float, chroma: float, hue: float) -> Color:
    """创建限制在有效通道范围内的 OKLCH 颜色。"""
    return Color(
        "oklch",
        [
            max(0.0, min(1.0, lightness)),
            max(0.0, chroma),
            hue,
        ],
    )


def _contrast(first: str, second: str) -> float:
    """计算两种不透明 sRGB 颜色的 WCAG 2.1 对比度。"""
    return Color(first).contrast(Color(second), method="wcag21")


def _tone_for_contrast(
    hue: float,
    chroma: float,
    background: str,
    minimum_contrast: float,
) -> str:
    """在指定色相和色度下寻找满足对比度的最亮文字色。"""
    low = 0.0
    high = 1.0
    target = minimum_contrast + _CONTRAST_SEARCH_MARGIN
    best = _to_hex(_oklch_color(low, chroma, hue))
    for _ in range(28):
        middle = (low + high) / 2
        candidate = _to_hex(_oklch_color(middle, chroma, hue))
        if _contrast(candidate, background) >= target:
            low = middle
            best = candidate
        else:
            high = middle
    return best


def _state_color(
    seed_oklch: Color,
    lightness_delta: float,
    foreground: str,
) -> str:
    """生成保持色相的控件状态色，并确保前景文字仍然清晰。"""
    seed_lightness = float(seed_oklch["l"])
    seed_chroma = float(seed_oklch["c"])
    seed_hue = float(seed_oklch["h"])
    desired_lightness = max(0.0, min(1.0, seed_lightness + lightness_delta))

    def color_at(lightness: float) -> str:
        return _to_hex(_oklch_color(lightness, seed_chroma, seed_hue))

    desired = color_at(desired_lightness)
    target = _MIN_CONTROL_TEXT_CONTRAST + _CONTRAST_SEARCH_MARGIN
    if _contrast(desired, foreground) >= target:
        return desired

    if foreground == "#000000":
        low = desired_lightness
        high = 1.0
        best = color_at(high)
        for _ in range(28):
            middle = (low + high) / 2
            candidate = color_at(middle)
            if _contrast(candidate, foreground) >= target:
                high = middle
                best = candidate
            else:
                low = middle
        return best

    low = 0.0
    high = desired_lightness
    best = color_at(low)
    for _ in range(28):
        middle = (low + high) / 2
        candidate = color_at(middle)
        if _contrast(candidate, foreground) >= target:
            low = middle
            best = candidate
        else:
            high = middle
    return best


@lru_cache(maxsize=256)
def _derive_normalized_theme_palette(normalized_seed: str) -> ThemePalette:
    """根据已规范化的角色原色计算并缓存完整语义色板。"""
    accent = normalized_seed
    seed_oklch = Color(accent).convert("oklch")
    seed_chroma = float(seed_oklch["c"])
    seed_hue = float(seed_oklch["h"])
    if math.isnan(seed_hue) or seed_chroma < 0.01:
        seed_hue = 0.0
        seed_chroma = 0.0

    surface = "#FFFFFF"
    surface_tint = _to_hex(
        _oklch_color(0.975, min(seed_chroma * 0.16, 0.025), seed_hue)
    )
    surface_selected = _to_hex(
        _oklch_color(0.935, min(seed_chroma * 0.34, 0.055), seed_hue)
    )
    border_subtle = _to_hex(
        _oklch_color(0.855, min(seed_chroma * 0.24, 0.04), seed_hue)
    )

    text_primary = _tone_for_contrast(
        seed_hue,
        min(seed_chroma * 0.16, 0.025),
        surface_selected,
        _MIN_TEXT_CONTRAST,
    )
    text_secondary = _tone_for_contrast(
        seed_hue,
        min(seed_chroma * 0.28, 0.045),
        surface_selected,
        _MIN_SECONDARY_TEXT_CONTRAST,
    )
    text_accent = _tone_for_contrast(
        seed_hue,
        min(seed_chroma * 0.78, 0.16),
        surface_selected,
        _MIN_ACCENT_TEXT_CONTRAST,
    )

    black_contrast = _contrast(accent, "#000000")
    white_contrast = _contrast(accent, "#FFFFFF")
    on_accent = "#FFFFFF" if black_contrast >= white_contrast else "#FFFFFF"
    if normalized_seed in _DARK_ON_ACCENT_CHARACTER_SEEDS:
        on_accent = _tone_for_contrast(
            seed_hue,
            min(seed_chroma * 0.18, 0.035),
            accent,
            _MIN_ACCENT_TEXT_CONTRAST,
        )
        hover_lightness_delta = 0.025
        pressed_lightness_delta = 0.05
    else:
        hover_lightness_delta = -0.035
        pressed_lightness_delta = -0.075
    accent_hover = _state_color(seed_oklch, hover_lightness_delta, on_accent)
    accent_pressed = _state_color(seed_oklch, pressed_lightness_delta, on_accent)

    return ThemePalette(
        accent=accent,
        on_accent=on_accent,
        accent_hover=accent_hover,
        accent_pressed=accent_pressed,
        text_primary=text_primary,
        text_secondary=text_secondary,
        text_accent=text_accent,
        surface=surface,
        surface_tint=surface_tint,
        surface_selected=surface_selected,
        border_subtle=border_subtle,
        focus_ring=text_accent,
    )


def derive_theme_palette(seed: str) -> ThemePalette:
    """把角色原色转换为确定、可复用且满足文字对比度的语义色板。"""
    return _derive_normalized_theme_palette(_normalize_seed(seed))


def resolve_character_theme_seed(qt_style_content: str | None) -> str:
    """从旧版 QT_style.json 内容读取角色原色，缺失或损坏时使用默认色。"""
    if qt_style_content is None:
        return DEFAULT_CHARACTER_THEME_SEED
    match = _QT_WIDGET_COLOR_PATTERN.search(qt_style_content)
    if match is None:
        logger.warning("QT_style.json 未包含有效的 QWidget 文字色，使用默认角色配色")
        return DEFAULT_CHARACTER_THEME_SEED
    raw_seed = match.group(1).strip()
    try:
        return _normalize_seed(raw_seed)
    except ValueError:
        logger.warning("QT_style.json 中的角色主题色格式无效，使用默认角色配色")
        return DEFAULT_CHARACTER_THEME_SEED


def build_character_theme_stylesheet(palette: ThemePalette) -> str:
    """把角色语义色板转换为主聊天窗口使用的 Qt 样式表。"""
    return f"""
        QWidget {{
            background-color: {palette.surface_tint};
            color: {palette.text_primary};
        }}

        QTextBrowser {{
            text-decoration: none;
            background-color: {palette.surface};
            color: {palette.text_primary};
            border: 3px solid {palette.border_subtle};
            border-radius: 9px;
            padding: 5px;
        }}

        QLineEdit, QPlainTextEdit {{
            background-color: {palette.surface};
            border: 2px solid {palette.border_subtle};
            border-radius: 9px;
            padding: 5px;
            color: {palette.text_primary};
            selection-background-color: {palette.surface_selected};
            selection-color: {palette.text_primary};
        }}

        QLineEdit:focus, QPlainTextEdit:focus {{
            border: 2px solid {palette.focus_ring};
        }}

        QPushButton {{
            background-color: {palette.accent};
            color: {palette.on_accent};
            border-radius: 6px;
            padding: 6px;
            border: none;
        }}

        QPushButton:hover {{
            background-color: {palette.accent_hover};
            color: {palette.on_accent};
        }}

        QPushButton:pressed {{
            background-color: {palette.accent_pressed};
            color: {palette.on_accent};
        }}

        QScrollBar:vertical {{
            border: none;
            background: {palette.surface_selected};
            width: 10px;
            margin: 0px;
        }}

        QScrollBar::handle:vertical {{
            background: {palette.border_subtle};
            min-height: 20px;
            border-radius: 3px;
        }}

        QScrollBar::handle:vertical:hover {{
            background: {palette.accent};
        }}

        QSlider::groove:horizontal {{
            border: 1px solid {palette.border_subtle};
            height: 4px;
            background: {palette.surface_selected};
            margin: 2px 0;
            border-radius: 2px;
        }}

        QSlider::handle:horizontal {{
            background: {palette.accent};
            border: 1px solid {palette.accent};
            width: 12px;
            margin: -4px 0;
            border-radius: 6px;
        }}

        QSlider::handle:horizontal:hover {{
            background: {palette.accent_hover};
            border-color: {palette.accent_hover};
        }}

        QSlider::sub-page:horizontal {{
            background: {palette.accent};
            border-radius: 2px;
            margin: 2px 0;
        }}

        QToolButton {{
            color: {palette.text_accent};
            background-color: transparent;
            border: none;
            border-radius: 4px;
        }}

        QToolButton:hover {{
            background-color: {palette.surface_selected};
        }}

        QToolButton:focus {{
            border: 1px solid {palette.focus_ring};
        }}
    """


def build_dialog_theme_stylesheet(palette: ThemePalette) -> str:
    """把角色语义色板转换为设置类弹窗共用的 Qt 样式表。"""
    return f"""
        QDialog {{
            background-color: {palette.surface_tint};
            color: {palette.text_primary};
        }}

        QGroupBox {{
            background-color: {palette.surface};
            color: {palette.text_primary};
            border: 1px solid {palette.border_subtle};
            border-radius: 8px;
            margin-top: 18px;
            padding: 12px 8px 8px 8px;
        }}

        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            color: {palette.text_accent};
            background-color: {palette.surface};
        }}

        QLabel {{
            color: {palette.text_primary};
            background-color: transparent;
            padding: 4px 0;
        }}

        QLabel[dialogRole="secondary"] {{
            color: {palette.text_secondary};
        }}

        QPushButton {{
            background-color: {palette.surface};
            color: {palette.text_accent};
            border: 1px solid {palette.border_subtle};
            border-radius: 6px;
            padding: 7px 12px;
        }}

        QPushButton:hover {{
            background-color: {palette.surface_selected};
            border-color: {palette.focus_ring};
        }}

        QPushButton:pressed {{
            background-color: {palette.surface_selected};
            color: {palette.text_primary};
            border-color: {palette.accent_pressed};
        }}

        QPushButton:focus {{
            border: 2px solid {palette.focus_ring};
        }}

        QPushButton:disabled {{
            background-color: {palette.surface_tint};
            color: {palette.text_secondary};
            border-color: {palette.border_subtle};
        }}
    """
