# 封装 qfluentwidget.ScrollArea，通过 qss 要求其背景默认透明，且附带一个 self.widget 组件
import contextlib

from PyQt5.QtWidgets import QWidget

# 去除牛皮藓广告
with contextlib.redirect_stdout(None):
    from qfluentwidgets import ScrollArea


class TransparentScrollArea(ScrollArea):
    """
    一个背景透明的 ScrollArea，附带一个已被设为滚动对象，且大小可以自动变化的 widget 组件
    """
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName("TransparentScrollArea")

        self.view = QWidget(self)
        self.view.setObjectName("view")

        # 设置背景为透明
        self.setStyleSheet("""
TransparentScrollArea, #view{
    background-color: transparent;
}

QScrollArea {
    border: none;
    background-color: transparent;
}
        """)

        self.setWidget(self.view)
        self.setWidgetResizable(True)
