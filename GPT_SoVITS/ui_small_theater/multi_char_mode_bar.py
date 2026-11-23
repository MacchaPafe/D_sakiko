"""小剧场分段选择器共用的滑动选中背景。"""
from PyQt5.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QSizePolicy


class SceneModeBar(QFrame):
    """为小剧场分段按钮绘制滑动的选中背景。"""

    def __init__(self, group):
        super().__init__()
        self.group = group
        # 宽度随各段内容，避免相邻控件显隐时分段条被拉宽。
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self.highlight = QFrame(self)
        self.highlight.setObjectName("multiCharSceneSelection")
        self.highlight.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.highlight.hide()
        self.animation = QPropertyAnimation(self.highlight, b"geometry", self)
        self.animation.setDuration(200)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        group.buttonToggled.connect(lambda button, checked: self.update_selection() if checked else None)

    def update_selection(self, animated=True):
        button = self.group.checkedButton()
        if button is None:
            return
        target = button.geometry()
        if animated and self.animation.endValue() == target:
            return
        self.animation.stop()
        start = self.highlight.geometry()
        if animated and self.highlight.isVisible():
            self.animation.setStartValue(start)
            self.animation.setEndValue(target)
            self.animation.start()
        else:
            self.animation.setEndValue(target)
            self.highlight.setGeometry(target)
        self.highlight.show()

    def showEvent(self, event):
        super().showEvent(event)
        self.layout().activate()
        self.update_selection(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.layout().activate()
        self.update_selection(False)
