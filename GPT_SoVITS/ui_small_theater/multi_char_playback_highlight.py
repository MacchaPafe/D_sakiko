"""用文本附加选区定位正式播放句，不重建剧本或改变用户的文字选择。"""
from PyQt5.QtCore import QEvent, QObject, Qt
from PyQt5.QtGui import QColor, QTextCursor, QTextFormat
from PyQt5.QtWidgets import QTextEdit


class PlaybackHighlight(QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.chat_id = None
        self.ranges = []
        self.state = {}
        self.follow = True
        self.index = None
        browser.installEventFilter(self)
        browser.viewport().installEventFilter(self)
        bar = browser.verticalScrollBar()
        bar.sliderPressed.connect(self.suspend_follow)
        bar.actionTriggered.connect(self.suspend_follow)

    def suspend_follow(self, *_args):
        self.follow = False

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel or (
            event.type() == QEvent.KeyPress and event.key() in (
                Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown,
                Qt.Key_Home, Qt.Key_End, Qt.Key_Space,
            )
        ):
            self.suspend_follow()
        return super().eventFilter(watched, event)

    def reset(self):
        self.state = {}
        self.index = None
        self.follow = True
        self.browser.setExtraSelections([])

    def set_ranges(self, ranges, chat_id):
        """范围使用 Qt 文档位置，兼容换行、译文、同文台词及非 BMP 字符。"""
        if chat_id != self.chat_id:
            self.reset()
        self.chat_id = chat_id
        self.ranges = ranges
        self.apply(scroll=self.follow and self.state.get("phase") in ("playing", "transition"))

    def update_state(self, state):
        previous = self.state
        active = state.get("phase") in ("playing", "transition")
        starting = active and (
            previous.get("phase") not in ("playing", "transition")
            or (state.get("auto") and not previous.get("auto"))
            or (state.get("single") and not previous.get("single"))
        )
        if starting:
            self.follow = True
        self.state = dict(state)
        index = state.get("current_index")
        self.index = index if isinstance(index, int) and not isinstance(index, bool) else None
        changed = self.index != previous.get("current_index")
        self.apply(scroll=self.follow and (starting or changed))

    def apply(self, scroll=False):
        if self.index is None or not 0 <= self.index < len(self.ranges):
            self.browser.setExtraSelections([])
            return
        start, end = self.ranges[self.index]
        if end >= self.browser.document().characterCount():
            self.browser.setExtraSelections([])
            return
        cursor = QTextCursor(self.browser.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setBackground(QColor("#E7F0FF"))
        selection.format.setProperty(QTextFormat.FullWidthSelection, True)
        self.browser.setExtraSelections([selection])
        if scroll:
            cursor.setPosition(start)
            top = self.browser.cursorRect(cursor).top()
            cursor.setPosition(end)
            bottom = self.browser.cursorRect(cursor).bottom()
            height = self.browser.viewport().height()
            if top < 0 or bottom >= height:
                offset = max(0, (height - (bottom - top)) // 3)
                bar = self.browser.verticalScrollBar()
                bar.setValue(bar.value() + top - offset)
