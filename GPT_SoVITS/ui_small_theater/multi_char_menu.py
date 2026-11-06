"""按实际字体为小剧场弹出菜单预留文字、图标和箭头空间。"""
from PyQt5.QtWidgets import QStyle


def fit_menu_to_contents(menu):
    """QMenu 的样式尺寸可能低估中文宽度，主菜单与抽屉分别按内容撑开。"""
    menu.ensurePolished()
    metrics = menu.fontMetrics()
    icon_width = menu.style().pixelMetric(QStyle.PM_SmallIconSize, None, menu)
    text_width = 0
    has_icon = False
    has_submenu = False
    for action in menu.actions():
        if action.isSeparator() or not action.isVisible():
            continue
        text_width = max(text_width, metrics.horizontalAdvance(action.text()))
        has_icon = has_icon or not action.icon().isNull()
        if action.menu() is not None:
            has_submenu = True
            fit_menu_to_contents(action.menu())
    # 两侧 item padding 各 16px，另加菜单边框、外边距和列间空隙。
    width = text_width + 42
    if has_icon:
        width += icon_width + 8
    if has_submenu:
        width += icon_width + 8
    menu.setMinimumWidth(max(menu.sizeHint().width(), width))
