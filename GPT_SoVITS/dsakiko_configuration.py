import sys
import contextlib
import os

from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QApplication,
                             QVBoxLayout, QLabel,
                             QStackedWidget, QMainWindow)
from PyQt5.QtCore import Qt, QTimer

from ui.components.custom_setting_area import CustomSettingArea
from ui.components.gpt_sovits_area import GPTSoVITSArea
from ui.components.llm_api_area import LLMAPIArea
from ui.custom_widgets.transparent_scroll_area import TransparentScrollArea

# 去广告
with contextlib.redirect_stdout(None):
    from qfluentwidgets import Pivot, PrimaryPushButton, PushButton, InfoBar, InfoBarPosition, InfoBarIcon

# 将当前文件夹加入 sys.path，强制搜索当前目录的模块（即使 os.getcwd() 不是当前目录）
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)


from qconfig import d_sakiko_config


class DSakikoConfigArea(TransparentScrollArea):
    def __init__(self):
        super().__init__()

        self.v_box_layout = QVBoxLayout(self.view)

        # 分栏组件
        self.pivot = Pivot(self)
        # 存放各个分栏下的内容
        self.stacked_widget = QStackedWidget(self)

        # 对应的分栏内容
        self.llm_api_area = LLMAPIArea(self)
        self.gpt_sovits_area = GPTSoVITSArea(self)
        self.custom_setting_area = CustomSettingArea(self)

        # 添加到分栏中
        self.add_sub_interface(self.llm_api_area, "LLMApiArea", self.tr("大模型 API 配置"))
        self.add_sub_interface(self.gpt_sovits_area, "GPTSoVITSArea", self.tr("语音合成模型配置"))
        self.add_sub_interface(self.custom_setting_area, "customSettingArea", self.tr("个性化"))

        # 连接信号并初始化当前标签页
        self.stacked_widget.currentChanged.connect(self.on_current_index_changed)
        self.stacked_widget.setCurrentWidget(self.llm_api_area)
        self.pivot.setCurrentItem(self.llm_api_area.objectName())

        # 添加标题和分栏组件到主布局
        self.v_box_layout.setContentsMargins(30, 0, 30, 30)
        self.v_box_layout.addWidget(self.pivot, 0, Qt.AlignmentFlag.AlignHCenter)
        self.v_box_layout.addWidget(self.stacked_widget, 1, Qt.AlignmentFlag.AlignHCenter)

        self.save_button = PrimaryPushButton(self.tr("保存配置"), self)
        self.save_button.clicked.connect(self.save_config)
        self.v_box_layout.addWidget(self.save_button)

        self.exit_button = PushButton(self.tr("关闭窗口"), self)
        self.exit_button.clicked.connect(self.close)
        self.v_box_layout.addWidget(self.exit_button)

        self.custom_setting_area.status_signal.connect(self.show_status)

        d_sakiko_config.themeColorChanged.connect(self.on_theme_change)

        self.resize(500, 600)

    def on_theme_change(self, color: QColor):
        # 不知道为啥“保存配置”这个按钮的主题颜色不会自动更新，所以手动更新一下
        QTimer.singleShot(500, lambda: self.save_button.update())

    def add_sub_interface(self, widget: QLabel, object_name: str, text: str):
        """
        添加一个 Qt 组件为一个分栏项目。

        :param widget: 要添加的 Qt 组件实例。   
        :param object_name: 该组件的标识符，需要是唯一的，取什么都行
        :param text: 分栏标题上显示的文字
        """
        widget.setObjectName(object_name)
        widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stacked_widget.addWidget(widget)

        # 使用全局唯一的 objectName 作为路由键
        self.pivot.addItem(
            routeKey=object_name,
            text=text,
            onClick=lambda: self.stacked_widget.setCurrentWidget(widget)
        )

    def on_current_index_changed(self, index):
        """
        在当前 stackedWidget 中选中的 index 变化时，更新 pivot 的当前选中项。
        这样可以保证两者同步。
        """
        widget = self.stacked_widget.widget(index)
        self.pivot.setCurrentItem(widget.objectName())
    
    def show_status(self, icon: InfoBarIcon, message: str):
        """
        在点击保存按键时，显示保存状态信息，并在3秒后自动清除。
        """
        InfoBar.new(icon=icon, title="", content=message, isClosable=True, position=InfoBarPosition.BOTTOM, duration=3000,
                    parent=self)

    def load_config_to_ui(self):
        """
        要求三个子组件将 d_sakiko_config 中的配置加载到 UI 界面中。
        """
        self.llm_api_area.load_config_to_ui()
        self.gpt_sovits_area.load_config_to_ui()
        self.custom_setting_area.load_config_to_ui()

    def save_ui_to_config(self) -> bool:
        """
        保存当前的 ui 到配置变量中。

        如果某一页的保存失败了，就切换到那一页，并返回 False。
        """
        result = self.llm_api_area.save_ui_to_config()
        if not result:
            self.stacked_widget.setCurrentWidget(self.llm_api_area)
            return False
        result = self.gpt_sovits_area.save_ui_to_config()
        if not result:
            self.stacked_widget.setCurrentWidget(self.gpt_sovits_area)
            return False
        result = self.custom_setting_area.save_ui_to_config()
        if not result:
            self.stacked_widget.setCurrentWidget(self.custom_setting_area)
            return False

        return True

    def save_config(self):
        """
        Save the current UI state to the config, and then save the config to disk.
        """
        if self.save_ui_to_config():
            d_sakiko_config.save()

            self.show_status(InfoBarIcon.SUCCESS, self.tr("保存成功！大模型相关配置立刻生效，音频推理与角色顺序等配置在下次启动时应用"))


if __name__ == '__main__':
    import os

    app = QApplication(sys.argv)
    area = DSakikoConfigArea()
    w = QMainWindow()
    w.setMinimumSize(600, 800)
    w.setCentralWidget(area)

    w.show()
    sys.exit(app.exec_())
