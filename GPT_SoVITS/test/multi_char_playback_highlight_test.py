"""用真实 Qt 文档验证定位、选区和滚动，不连接舞台或存档。"""
import os
import sys
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import QApplication, QAbstractSlider
from chat.chat import Chat, Message
from multi_char_main import InteractiveChatBrowser, ViewerGUI
from ui_small_theater.multi_char_playback_highlight import PlaybackHighlight


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(app):
    browser = InteractiveChatBrowser()
    browser.resize(380, 180)
    browser.show()
    chat = Chat(message_list=[Message.from_dict({"character_name": "A", "text": f"第{i}句：相同内容😀\n第二行",
                                               "translation": f"译文{i}"}) for i in range(50)])
    host = SimpleNamespace(current_chat=chat, chat_display=browser,
                           _playback_highlight=PlaybackHighlight(browser),
                           render_message_html=ViewerGUI.render_message_html,
                           update_script_button_visibility=Mock())
    host.refresh_chat_display = MethodType(ViewerGUI.refresh_chat_display, host)
    host.refresh_chat_display()
    yield host
    browser.close()
    browser.deleteLater()


def state(index, phase="playing", auto=True, single=False):
    return {"phase": phase, "current_index": index, "auto": auto, "single": single}


def test_highlight_selects_whole_sentence_with_translation_and_multiline(host):
    highlighter = host._playback_highlight
    highlighter.update_state(state(2))
    selections = host.chat_display.extraSelections()
    assert len(selections) == 1
    text = selections[0].cursor.selectedText()
    assert "第2句" in text and "😀" in text and "译文2" in text
    assert "第1句" not in text and "第3句" not in text
    assert "第二行" in text


def test_state_updates_do_not_rebuild_document_or_change_user_selection(host):
    browser = host.chat_display
    cursor = QTextCursor(browser.document())
    cursor.setPosition(2)
    cursor.setPosition(8, QTextCursor.KeepAnchor)
    browser.setTextCursor(cursor)
    revision = browser.document().revision()
    for index in (3, 8, 10):
        host._playback_highlight.update_state(state(index))
    assert browser.document().revision() == revision
    assert browser.textCursor().selectedText() == cursor.selectedText()


def test_manual_scroll_suspends_follow_until_next_playback_start(host):
    browser, highlight = host.chat_display, host._playback_highlight
    bar = browser.verticalScrollBar()
    bar.setValue(0)
    highlight.update_state(state(0))
    highlight.update_state(state(30))
    assert bar.value() > 0
    bar.triggerAction(QAbstractSlider.SliderToMinimum)
    assert not highlight.follow
    highlight.update_state(state(40))
    assert bar.value() == 0
    assert "第40句" in browser.extraSelections()[0].cursor.selectedText()
    highlight.update_state(state(40, phase="ready", auto=False))
    highlight.update_state(state(20, auto=False))
    assert highlight.follow and bar.value() > 0


def test_single_replay_returns_highlight_to_formal_progress(host):
    highlight = host._playback_highlight
    highlight.update_state(state(3, phase="ready", auto=False))
    highlight.update_state(state(35, auto=False, single=True))
    assert "第35句" in host.chat_display.extraSelections()[0].cursor.selectedText()
    highlight.update_state(state(3, phase="ready", auto=False))
    assert "第3句" in host.chat_display.extraSelections()[0].cursor.selectedText()


def test_refresh_remaps_ranges_without_resuming_manual_scroll(host):
    highlight = host._playback_highlight
    highlight.update_state(state(15))
    bar = host.chat_display.verticalScrollBar()
    bar.triggerAction(QAbstractSlider.SliderToMinimum)
    host.current_chat.message_list[0].text = "长度改变" * 50
    host.refresh_chat_display()
    assert not highlight.follow and bar.value() == 0
    text = host.chat_display.extraSelections()[0].cursor.selectedText()
    assert "第15句" in text and "译文15" in text


def test_new_chat_and_reset_clear_old_highlight(host):
    highlight = host._playback_highlight
    highlight.update_state(state(2))
    host.current_chat = Chat(message_list=[Message.from_dict({"text": "新剧本"})])
    host.refresh_chat_display()
    assert host.chat_display.extraSelections() == []
    highlight.update_state(state(0))
    assert len(host.chat_display.extraSelections()) == 1
    highlight.reset()
    assert host.chat_display.extraSelections() == []


@pytest.mark.parametrize("index", [None, -1, 100, True])
def test_empty_or_invalid_position_clears_highlight(host, index):
    host._playback_highlight.update_state(state(index))
    assert host.chat_display.extraSelections() == []
