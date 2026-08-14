from __future__ import annotations

import os
import re
import sys
import unittest
from dataclasses import fields
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from coloraide import Color
from PyQt5.QtCore import QObject, QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QContextMenuEvent, QImage, QPainter
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMenu, QStyle, QStyleOptionViewItem

from chat.chat import Message
from emotion_enum import EmotionEnum
from ui_constants import char_info_json
from ui_main.components.chat_display import ChatDisplay
from ui_main.components.message_input import MessageInput
from ui_main.components.chat_sidebar import ChatSidebarDelegate, ChatSidebarRow
from ui_main.theme import (
    DEFAULT_CHARACTER_THEME_SEED,
    ThemePalette,
    build_character_theme_stylesheet,
    build_dialog_theme_stylesheet,
    derive_theme_palette,
    resolve_character_theme_seed,
)


class _PaletteEmitter(QObject):
    """为主题色槽函数测试提供最小 Qt 信号发送器。"""

    paletteChanged = pyqtSignal(object)


class ThemePaletteAlgorithmTestCase(unittest.TestCase):
    """验证所有预置角色原色都能生成稳定且清晰的语义色板。"""

    _HEX_PATTERN = re.compile(r"^#[0-9A-F]{6}$")

    def test_all_preset_colors_meet_semantic_contrast_thresholds(self) -> None:
        """全部预置色应满足格式、原色保留和关键文字对比度约束。"""
        for character_name, character_info in char_info_json.items():
            seed = str(character_info["theme_color"])
            with self.subTest(character=character_name, seed=seed):
                palette = derive_theme_palette(seed)
                self.assertEqual(palette.accent, seed.upper())
                for field in fields(ThemePalette):
                    value = getattr(palette, field.name)
                    self.assertRegex(value, self._HEX_PATTERN)
                    self.assertTrue(Color(value).in_gamut("srgb"))
                self.assertGreaterEqual(self._contrast(palette.text_primary, palette.surface), 9.0)
                self.assertGreaterEqual(self._contrast(palette.text_secondary, palette.surface), 5.5)
                self.assertGreaterEqual(self._contrast(palette.text_accent, palette.surface), 4.75)
                self.assertGreaterEqual(
                    self._contrast(palette.text_primary, palette.surface_selected),
                    9.0,
                )
                self.assertGreaterEqual(
                    self._contrast(palette.text_secondary, palette.surface_selected),
                    5.5,
                )
                self.assertGreaterEqual(
                    self._contrast(palette.text_accent, palette.surface_selected),
                    4.75,
                )
                self.assertGreaterEqual(self._contrast(palette.on_accent, palette.accent), 4.5)
                self.assertGreaterEqual(self._contrast(palette.on_accent, palette.accent_hover), 4.5)
                self.assertGreaterEqual(self._contrast(palette.on_accent, palette.accent_pressed), 4.5)

    def test_representative_extremes_are_deterministic(self) -> None:
        """高松灯、阿拉蕾和极端中性色应稳定生成同一结果。"""
        for seed in ("#77BBDD", "#FFEE55", "#000000", "#FFFFFF", "#777777"):
            with self.subTest(seed=seed):
                first = derive_theme_palette(seed.lower())
                second = derive_theme_palette(seed)
                self.assertIs(first, second)

    def test_invalid_seed_formats_raise_value_error(self) -> None:
        """衍生入口只接受六位不透明十六进制 sRGB 原色。"""
        for seed in ("7799CC", "#79C", "#7799CCFF", "blue", "oklch(0.5 0.1 20)", ""):
            with self.subTest(seed=seed):
                with self.assertRaises(ValueError):
                    derive_theme_palette(seed)

    def test_legacy_qt_style_resolver_normalizes_and_falls_back(self) -> None:
        """旧 QSS 内容应正确解析，缺失或损坏内容应回退默认原色。"""
        self.assertEqual(
            resolve_character_theme_seed("QWidget { color: #77bbdd; }"),
            "#77BBDD",
        )
        self.assertEqual(resolve_character_theme_seed(None), DEFAULT_CHARACTER_THEME_SEED)
        self.assertEqual(
            resolve_character_theme_seed("QWidget { background-color: #FFFFFF; }"),
            DEFAULT_CHARACTER_THEME_SEED,
        )

    def test_qss_uses_semantic_palette_without_desktop_queries(self) -> None:
        """主窗口 QSS 应直接消费语义字段，不依赖屏幕尺寸或再次算色。"""
        palette = derive_theme_palette("#FFEE55")
        stylesheet = build_character_theme_stylesheet(palette)

        self.assertIn(f"color: {palette.text_primary};", stylesheet)
        self.assertIn(f"background-color: {palette.accent};", stylesheet)
        self.assertIn(f"color: {palette.on_accent};", stylesheet)
        self.assertIn(f"border: 2px solid {palette.focus_ring};", stylesheet)
        self.assertNotIn("QDesktopWidget", stylesheet)

    def test_dialog_qss_uses_shared_card_and_secondary_action_styles(self) -> None:
        """设置类弹窗应共享角色色板、卡片分组和非实心普通按钮。"""
        palette = derive_theme_palette("#FFEE55")
        stylesheet = build_dialog_theme_stylesheet(palette)

        self.assertIn("QGroupBox {", stylesheet)
        self.assertIn(f"background-color: {palette.surface};", stylesheet)
        self.assertIn(f"color: {palette.text_accent};", stylesheet)
        self.assertIn('QLabel[dialogRole="secondary"]', stylesheet)
        self.assertNotIn("QDesktopWidget", stylesheet)

    @staticmethod
    def _contrast(foreground: str, background: str) -> float:
        """返回测试断言使用的 WCAG 2.1 对比度。"""
        return Color(foreground).contrast(Color(background), method="wcag21")


class ThemePaletteQtIntegrationTestCase(unittest.TestCase):
    """验证主题色板的 Qt 信号槽和聊天 HTML 语义接线。"""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        """创建测试使用的无界面 Qt 应用实例。"""
        existing_application = QApplication.instance()
        cls.app = (
            existing_application
            if isinstance(existing_application, QApplication)
            else QApplication([])
        )

    def test_signal_updates_synchronous_palette_consumers(self) -> None:
        """同一信号应把同一不可变色板同步给聊天区和输入区槽函数。"""
        original = derive_theme_palette("#77BBDD")
        replacement = derive_theme_palette("#FFEE55")
        emitter = _PaletteEmitter()
        display = ChatDisplay(original)
        message_input = MessageInput(original)
        emitter.paletteChanged.connect(display.set_theme_palette)
        emitter.paletteChanged.connect(message_input.set_theme_palette)

        emitter.paletteChanged.emit(replacement)

        self.assertIs(display._theme_palette, replacement)
        self.assertIs(message_input._theme_palette, replacement)
        display.deleteLater()
        message_input.deleteLater()

    def test_chat_html_separates_link_body_and_translation_colors(self) -> None:
        """消息标题、正文和译文应保持独立颜色并共享交互锚点。"""
        palette = derive_theme_palette("#FFEE55")
        display = ChatDisplay(palette)
        message = Message(
            character_name="阿拉蕾",
            text="角色正文",
            translation="角色译文",
            emotion=EmotionEnum.HAPPINESS,
            audio_path="NO_AUDIO",
        )

        rendered = display._render_message_html(message, 0)

        self.assertLess(rendered.index("</a>"), rendered.index("角色正文"))
        self.assertEqual(rendered.count('href="no_audio:?msg=0"'), 2)
        self.assertIn(f"color: {palette.text_accent}", rendered)
        self.assertIn(f"color: {palette.text_primary}", rendered)
        self.assertIn(f"color: {palette.text_secondary}", rendered)
        self.assertNotIn("#B3D1F2", rendered)
        display.deleteLater()

    def test_left_click_on_message_body_emits_audio_link(self) -> None:
        """左键点击角色正文应像点击角色名一样触发历史语音链接。"""
        display = ChatDisplay(derive_theme_palette("#77BBDD"))
        display.resize(500, 240)
        display.show()
        emitted_urls: list[str] = []
        display.audioLinkClicked.connect(lambda url: emitted_urls.append(url.toString()))
        message = Message(
            character_name="高松灯",
            text="可以点击的角色正文",
            translation="可以点击的角色译文",
            emotion=EmotionEnum.HAPPINESS,
            audio_path="/tmp/theme-click-test.wav",
        )
        display.append_message(message, 4)
        self.app.processEvents()

        body_point = self._text_point(display, "可以点击的角色正文")
        QTest.mouseClick(display.viewport(), Qt.LeftButton, Qt.NoModifier, body_point)
        self.app.processEvents()

        self.assertEqual(len(emitted_urls), 1)
        self.assertIn("theme-click-test.wav", emitted_urls[0])
        self.assertIn("?msg=4", emitted_urls[0])
        display.close()
        display.deleteLater()

    def test_right_click_on_message_body_builds_message_actions(self) -> None:
        """右键点击用户正文应能解析消息索引并创建消息操作菜单。"""
        display = ChatDisplay(derive_theme_palette("#FFEE55"))
        display.resize(500, 240)
        display.show()
        message = Message(
            character_name="User",
            text="可以右键操作的用户正文",
            translation="",
            emotion=EmotionEnum.HAPPINESS,
            audio_path="",
        )
        display.append_message(message, 2)
        self.app.processEvents()
        body_point = self._text_point(display, "可以右键操作的用户正文")
        menu = QMenu(display)
        context_event = QContextMenuEvent(
            QContextMenuEvent.Mouse,
            body_point,
            display.viewport().mapToGlobal(body_point),
        )

        with (
            mock.patch.object(display, "createStandardContextMenu", return_value=menu),
            mock.patch.object(menu, "exec_", return_value=None),
        ):
            display.contextMenuEvent(context_event)

        action_texts = [action.text() for action in menu.actions()]
        self.assertIn("编辑消息", action_texts)
        self.assertIn("删除此消息", action_texts)
        self.assertIn("删除此轮对话", action_texts)
        display.close()
        display.deleteLater()

    def test_streamed_message_body_keeps_interaction_anchor(self) -> None:
        """流式正文补完后仍应保留播放与右键操作所需的消息锚点。"""
        display = ChatDisplay(derive_theme_palette("#77BBDD"))
        display.resize(500, 240)
        display.show()
        message = Message(
            character_name="高松灯",
            text="流式角色正文",
            translation="流式角色译文",
            emotion=EmotionEnum.HAPPINESS,
            audio_path="/tmp/theme-stream-test.wav",
        )
        display.append_message(message, 7, stream=True, interval_ms=10_000)
        display.finish_stream_now()
        self.app.processEvents()

        for text in ("流式角色正文", "流式角色译文"):
            point = self._text_point(display, text)
            anchor = display.anchorAt(point)
            self.assertIn("theme-stream-test.wav", anchor)
            self.assertIn("?msg=7", anchor)
        display.close()
        display.deleteLater()

    def test_folded_child_rows_use_unified_neutral_colors(self) -> None:
        """不同角色的折叠子对话在普通状态下应渲染为同一套中性色。"""
        tomori_image = self._render_folded_child_row(
            derive_theme_palette("#77BBDD")
        )
        arale_image = self._render_folded_child_row(
            derive_theme_palette("#FFEE55")
        )

        for point in (QPoint(340, 26), QPoint(180, 5), QPoint(340, 44)):
            with self.subTest(point=(point.x(), point.y())):
                self.assertEqual(
                    tomori_image.pixelColor(point),
                    arale_image.pixelColor(point),
                )

    @staticmethod
    def _render_folded_child_row(palette: ThemePalette) -> QImage:
        """使用指定角色色板离屏绘制一条普通折叠子对话。"""
        image = QImage(360, 52, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        delegate = ChatSidebarDelegate()
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, image.width(), image.height())
        option.state = QStyle.State_None
        row = ChatSidebarRow(
            row_type="chat_child",
            chat_id="test-chat",
            character_name="测试角色",
            chat_title="测试对话",
            preview_text="统一颜色的折叠子对话",
            theme_palette=palette,
            expanded=False,
            active=False,
        )
        delegate._paint_child_chat_row(painter, option, row)
        painter.end()
        return image

    @staticmethod
    def _text_point(display: ChatDisplay, text: str) -> QPoint:
        """返回聊天控件中指定文字首字符所在的视口坐标。"""
        cursor = display.document().find(text)
        if cursor.isNull():
            raise AssertionError(f"聊天文档中未找到测试文字：{text}")
        cursor.setPosition(cursor.selectionStart() + 1)
        return display.cursorRect(cursor).center()


if __name__ == "__main__":
    unittest.main()
