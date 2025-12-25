import sys, os,json,re,ast

from PyQt5.QtCore import QTimer, QThread, pyqtSignal, QUrl, Qt, QSize
from PyQt5.QtMultimedia import QMediaPlayer, QMediaPlaylist, QMediaContent

script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

import time,copy
import threading
from queue import Queue

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QTextBrowser, QPushButton, QDesktopWidget, QHBoxLayout, \
    QGridLayout, QApplication, QLabel, QGroupBox, QDialog, QMessageBox, QMenu, QFormLayout, QDialogButtonBox, \
    QToolButton, QStyle, QSlider

from PyQt5.QtGui import QFontDatabase, QFont, QIcon, QTextCursor, QPalette

import faulthandler
import character


faulthandler.enable(open("faulthandler_log.txt", "w"), all_threads=True)


# 将你的主题色定义为变量，方便微调
THEME_COLOR = "#7799CC"        # 祥子蓝（主色）
THEME_COLOR_HOVER = "#88AADD"  # 悬停亮色
THEME_COLOR_PRESSED = "#5E7FA8"# 按下深色
BG_COLOR = "#F0F4F9"           # 模拟 Win11 Mica 材质的淡灰蓝背景（比之前的E6F2FF稍微灰一点，更耐看）
TEXT_COLOR = "#333333"         # 主要文字深灰
SUB_TEXT_COLOR = "#5F6368"     # 次要文字

fluent_css = f"""
/* ================= 1. 全局背景与字体 ================= */
QWidget {{
    background-color: {BG_COLOR};
    color: {TEXT_COLOR};
}}

QDialog {{
    background-color: {BG_COLOR};
    color: #7799CC;
}}
/* ================= 2. 卡片化容器 (GroupBox & Dialog) ================= */
/* Fluent 风格的核心：白色悬浮卡片 */
QGroupBox{{
    background-color: #FFFFFF; /* 纯白背景 */
    border: 1px solid #E5E5E5; /* 极细的灰色边框 */
    border-radius: 8px;        /* 较大的圆角 */
}}

QGroupBox {{
    margin-top: 24px; /* 留出标题空间 */
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    top: 5px;
    color: {THEME_COLOR};
    background-color: transparent;
}}

/* ================= 3. 输入框 (ChatDisplay & Messages) ================= */
/* 模拟 WinUI 输入框：底部有一条强调线 */
QTextBrowser, QLineEdit {{
    background-color: #FFFFFF;
    border: 1px solid #E0E0E0;
    border-bottom: 2px solid #D1D1D1; /* 底部稍微深一点 */
    border-radius: 4px;
    padding: 8px;
    color: {TEXT_COLOR};
}}

/* 悬停状态：背景变灰白 */
QTextBrowser:hover, QLineEdit:hover {{
    background-color: #FDFDFD;
    border-bottom: 2px solid {THEME_COLOR}; /* 悬停时底部变蓝 */
}}

/* 聚焦状态：全边框高亮 */
QTextBrowser:focus, QLineEdit:focus {{
    background-color: #FFFFFF;
    border: 2px solid {THEME_COLOR}; /* 激活时全蓝 */
}}

/* ================= 4. 按钮 (普通次要按钮) ================= */
/* 模拟 WinUI Standard Button */
QPushButton {{
    background-color: #FFFFFF;
    border: 1px solid #D0D0D0;
    border-bottom: 1px solid #C0C0C0; /* 底部厚一点模拟立体感 */
    border-radius: 4px;
    color: {THEME_COLOR};
    padding: 5px 15px;
}}

QPushButton:hover {{
    background-color: #F9F9F9;
    border-color: #C0C0C0;
}}

QPushButton:pressed {{
    background-color: #F0F0F0;
    border-top: 1px solid #C0C0C0; /* 按下时阴影反转 */
    border-bottom: 1px solid #D0D0D0;
    color: {SUB_TEXT_COLOR};
}}

/* ================= 5. 强调按钮 (Primary Button) ================= */
QPushButton#PrimaryBtn {{
    background-color: {THEME_COLOR};
    color: #FFFFFF;
    border: 1px solid {THEME_COLOR};
    border-bottom: 1px solid {THEME_COLOR_PRESSED};
}}

QPushButton#PrimaryBtn:hover {{
    background-color: {THEME_COLOR_HOVER};
    border: 1px solid {THEME_COLOR_HOVER};
}}

QPushButton#PrimaryBtn:pressed {{
    background-color: {THEME_COLOR_PRESSED};
    border: 1px solid {THEME_COLOR_PRESSED};
    padding-top: 7px; /* 微妙的位移 */
}}

/* ================= 6. 工具栏按钮 (透明按钮) ================= */
QToolButton {{
    background-color: transparent;
    border: none;
    border-radius: 4px;
}}

QToolButton:hover {{
    background-color: rgba(0, 0, 0, 0.05); /* 极淡的灰色背景 */
}}

QToolButton:pressed {{
    background-color: rgba(0, 0, 0, 0.08);
}}

QPushButton:checked {{
    background-color: #7799CC; /* 变成主题色 */
    color: #FFFFFF;            /* 文字变白 */
    border: 1px solid #7799CC;
}}

/* 3. 选中状态下的悬停 (可选) */
/* 防止鼠标放上去时颜色变回去了，保持浅蓝色 */
QPushButton:checked:hover {{
    background-color: #88AADD; 
    border: 1px solid #88AADD;
}}

/* ================= 7. 滚动条 (模拟 Win11 细条) ================= */
QScrollBar:vertical {{
    border: none;
    background: transparent;
    width: 8px; /* 很细 */
    margin: 0px;
}}

QScrollBar::handle:vertical {{
    background: #C0C0C0;
    min-height: 20px;
    border-radius: 4px; /* 胶囊形状 */
}}

QScrollBar::handle:vertical:hover {{
    background: #A0A0A0;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px; /* 隐藏上下箭头 */
}}

QLabel {{

background-color: transparent; /* 标签背景透明 */

border: none; /* 确保没有边框 */

padding: 5px 0; /* 增加内边距，使标签不显得拥挤 */

color:{THEME_COLOR}

}}

QSlider::groove:horizontal {{
    /* 滑槽背景 */
    border: 1px solid #B3D1F2;  /* 使用边框色作为滑槽边框 */
    height: 8px;
    background: #D0E2F0;       /* 使用浅色背景 */
    margin: 2px 0;
    border-radius: 4px;
}}

QSlider::handle:horizontal {{
    /* 滑块手柄 */
    background: #7FB2EB;       /* 使用按钮的亮蓝色 */
    border: 1px solid #4F80E0;
    width: 16px;
    margin: -4px 0;            /* 垂直方向上的偏移，使手柄在滑槽上居中 */
    border-radius: 8px;        /* 使手柄成为圆形 */
}}

QSlider::handle:horizontal:hover {{
    /* 鼠标悬停时的手柄颜色 */
    background: #3FB2EB;       /* 使用按钮的 hover 亮色 */
    border: 1px solid #3F60D0;
}}

QSlider::sub-page:horizontal {{
    /* 进度条（已滑过部分） */
    background: #AACCFF;       /* 使用一个中间的蓝色，比滑槽背景深，比手柄浅 */
    border-radius: 4px;
    margin: 2px 0;
}}
"""

class CommunicateThreadMessages(QThread):
    message_signal=pyqtSignal(str)
    def __init__(self,message_queue):
        super().__init__()
        self.message=''
        self.message_queue=message_queue

    def run(self):
        while True:
            try:
                self.message=self.message_queue.get()
            except (ValueError, EOFError):
                # Queue closed
                break
            if self.message=='EXIT':
                break
            self.message_signal.emit(self.message)


class CommunicateThreadDP2QT(QThread):
    response_signal = pyqtSignal(str)

    def __init__(self, dp2qt_queue):
        super().__init__()
        self.dp2qt_queue = dp2qt_queue

    def run(self):
        while True:
            data = self.dp2qt_queue.get()
            if data=='EXIT':
                break
            self.response_signal.emit(data)

class WaitUntilAudioGenModuleChangeComplete(QThread):
    change_complete_signal = pyqtSignal()
    def __init__(self,audio_gen_module,char_index):
        super().__init__()
        self.audio_gen_module=audio_gen_module
        self.char_index=char_index

    def run(self):
        self.audio_gen_module.change_character_multi_char_ver(self.char_index)
        self.change_complete_signal.emit()

class DisplayTalkText(QThread):
    talk_text_signal=pyqtSignal(dict)
    def __init__(self,tell_qt_this_turn_finish_queue):
        super().__init__()
        self.tell_qt_this_turn_finish_queue=tell_qt_this_turn_finish_queue

    def run(self):
        while True:
            x=self.tell_qt_this_turn_finish_queue.get()
            if x=='EXIT':
                break
            self.talk_text_signal.emit(x)


class WaitUntilAudioGenModuleSynthesisComplete(QThread):
    synthesis_complete_signal = pyqtSignal(list)
    def __init__(self,audio_gen_module,char_texts_list,text_queue):
        super().__init__()
        self.audio_gen_module=audio_gen_module
        self.char_texts_list=char_texts_list
        self.text_queue=text_queue

    def run(self):
        char_text_audio_path_list=[]
        for i,text in enumerate(self.char_texts_list):
            self.text_queue.put(text)
            while True:

                if self.audio_gen_module.is_completed:
                    self.audio_gen_module.is_completed=False
                    char_text_audio_path_list.append(self.audio_gen_module.audio_file_path)
                    break
                time.sleep(0.2)
        self.synthesis_complete_signal.emit(char_text_audio_path_list)


class MoreInfoDialog(QWidget):
    def __init__(self, char_name_0, char_name_1,parent_user_input):
        super().__init__()
        self.setWindowTitle("写一些细节信息吧（也可以都不填）")
        self.screen = QDesktopWidget().screenGeometry()
        self.resize(int(0.35 * self.screen.width()), int(0.5 * self.screen.height()))

        # 主布局，增加更大的内边距
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(25, 20, 25, 25)
        main_layout.setSpacing(15)

        # --- 角色 0 配置块 ---
        group_0 = QGroupBox(f" 关于角色：{char_name_0} ")
        layout_0 = QVBoxLayout(group_0)
        layout_0.setContentsMargins(20, 30, 20, 20)

        # 说话风格
        h_layout_0_1 = QHBoxLayout()
        h_layout_0_1.addWidget(QLabel("说话风格:"), 1)
        self.char_0_talk_style = QLineEdit()
        self.char_0_talk_style.setPlaceholderText(f"如：素世面对爱音时带点不耐烦，但很关心")
        h_layout_0_1.addWidget(self.char_0_talk_style, 4)
        layout_0.addLayout(h_layout_0_1)

        # 互动细节
        h_layout_0_2 = QHBoxLayout()
        h_layout_0_2.addWidget(QLabel("互动细节:"), 1)
        self.char_0_inter_details = QLineEdit()
        self.char_0_inter_details.setPlaceholderText(f"如：称爱音方为'あのんちゃん'（小爱音）")
        h_layout_0_2.addWidget(self.char_0_inter_details, 4)
        layout_0.addLayout(h_layout_0_2)

        main_layout.addWidget(group_0)

        # --- 角色 1 配置块 ---
        group_1 = QGroupBox(f" 关于角色：{char_name_1} ")
        layout_1 = QVBoxLayout(group_1)
        layout_1.setContentsMargins(20, 30, 20, 20)

        h_layout_1_1 = QHBoxLayout()
        h_layout_1_1.addWidget(QLabel("说话风格:"), 1)
        self.char_1_talk_style = QLineEdit()
        self.char_1_talk_style.setPlaceholderText("如：活泼开朗，有元气")
        h_layout_1_1.addWidget(self.char_1_talk_style, 4)
        layout_1.addLayout(h_layout_1_1)

        h_layout_1_2 = QHBoxLayout()
        h_layout_1_2.addWidget(QLabel("互动细节:"), 1)
        self.char_1_inter_details = QLineEdit()
        self.char_1_inter_details.setPlaceholderText(f"如：叫素世为'そよりん'（soyorin）")
        h_layout_1_2.addWidget(self.char_1_inter_details, 4)
        layout_1.addLayout(h_layout_1_2)

        main_layout.addWidget(group_1)

        # --- 情境配置 ---
        group_sit = QGroupBox(" 对话发生的情境/话题 ")
        layout_sit = QVBoxLayout(group_sit)
        self.situation_input = QLineEdit()
        self.situation_input.setPlaceholderText("比如：傍晚乐队练习结束，两人一起走在回家的路上...")
        layout_sit.addWidget(self.situation_input)
        main_layout.addWidget(group_sit)

        # 完成按钮
        self.commit_btn = QPushButton("完成")
        self.commit_btn.clicked.connect(self.commit_info)
        self.commit_btn.setObjectName("PrimaryBtn")
        main_layout.addWidget(self.commit_btn)

        self.setLayout(main_layout)

        # 针对性的 QSS 美化
        self.setStyleSheet(fluent_css)
        #-------------------------------------------------
        self.user_input=parent_user_input
        if self.user_input:
            self.char_0_talk_style.setText(self.user_input['character_0']['talk_style'])
            self.char_0_inter_details.setText(self.user_input['character_0']['interaction_details'])
            self.char_1_talk_style.setText(self.user_input['character_1']['talk_style'])
            self.char_1_inter_details.setText(self.user_input['character_1']['interaction_details'])
            self.situation_input.setText(self.user_input['situation'])

    def commit_info(self):
        self.user_input.update({
            'character_0': {'talk_style': f'{self.char_0_talk_style.text()}',
                            'interaction_details': f'{self.char_0_inter_details.text()}'},
            'character_1': {'talk_style': f'{self.char_1_talk_style.text()}',
                            'interaction_details': f'{self.char_1_inter_details.text()}'},
            'situation': f'{self.situation_input.text()}'})


        self.close()


class ElementEditorDialog(QDialog):
    def __init__(self, data_dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("修改对话数据")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.data = data_dict  # 引用传递，直接改这个字典

        layout = QFormLayout(self)

        # === 创建输入框 ===
        self.char_name_edit = QLineEdit(self.data.get('char_name', ''))
        self.char_name_edit.setEnabled(False)
        self.text_edit = QLineEdit(self.data.get('text', ''))
        self.live2d_motion_num_edit = QLineEdit(str(self.data.get('live2d_motion_num', '')))
        self.translation_edit = QLineEdit(self.data.get('translation', ''))
        self.emotion_edit=QLineEdit(self.data.get('emotion', ''))
        self.emotion_edit.setEnabled(False)


        layout.addRow("角色名:", self.char_name_edit)
        layout.addRow("文本内容:", self.text_edit)
        layout.addRow("翻译:", self.translation_edit)
        layout.addRow("情感标签:", self.emotion_edit)
        layout.addRow("Live2d角色动作：",self.live2d_motion_num_edit)

        # === 确认/取消按钮 ===
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_new_data(self):
        """返回修改后的字典"""
        return {
            "char_name": self.data.get('char_name', ''),
            "text": self.text_edit.text(),
            "emotion": self.emotion_edit.text(),
            "translation": self.translation_edit.text(),
            "audio_path": self.data.get("audio_path", ""),
            "live2d_motion_num": self.live2d_motion_num_edit.text()
        }


class InteractiveChatBrowser(QTextBrowser):
    # 定义一个信号：当用户点击"编辑"时，发送该链接对应的索引号
    edit_signal = pyqtSignal(int)
    regenerate_signal=pyqtSignal(int)
    def contextMenuEvent(self, event):
        # 1. 获取鼠标位置下的链接地址
        # anchorAt 是 Qt 5.10+ 引入的，非常方便
        link = self.anchorAt(event.pos())

        # 2. 如果鼠标底下有链接，且是我们定义的 "replay:" 格式
        if link and link.startswith("replay:"):
            # 创建右键菜单
            menu = QMenu(self)

            # 添加 "编辑此条数据" 动作
            edit_action = menu.addAction("编辑此条数据")

            # 添加 "重新播放" 动作 (可选)
            regen_action = menu.addAction("重新生成这条音频")

            # 显示菜单并等待用户选择
            action = menu.exec_(self.mapToGlobal(event.pos()))

            # 解析索引 ID
            try:
                index = int(link.split(":")[1])

                if action == edit_action:
                    self.edit_signal.emit(index)  # 发送信号给主窗口
                elif action == regen_action:
                    self.regenerate_signal.emit(index)

            except ValueError:
                pass
        else:
            # 如果点的不是链接，就显示默认的菜单（复制、全选等）
            super().contextMenuEvent(event)


class CharacterSelectDialog(QDialog):
    def __init__(self, character_list, current_indices, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择两名角色")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        screen = QDesktopWidget().screenGeometry()
        scr_w = screen.width()
        scr_h = screen.height()

        # 保存传入的数据
        self.character_list = character_list

        # 复制一份当前索引，避免直接修改原数据
        # self.selected_indices 用来维护当前选中的角色编号（列表）
        self.selected_indices = current_indices.copy()

        self.buttons = {}  # 存储按钮对象：{index: button}

        # --- 布局初始化 ---
        layout = QVBoxLayout()

        # 提示标签
        label = QLabel("点击选择两名角色：")
        label.setStyleSheet(f"font-size: {int(scr_h * 0.015)}px; font-weight: bold; margin-bottom: 10px;")

        layout.addWidget(label)

        # 角色按钮网格区域
        grid_layout = QGridLayout()
        # 设定每行显示几个按钮（比如3个）
        columns = 3

        for i, char_obj in enumerate(self.character_list):
            # 获取角色名
            char_name = char_obj.character_name

            btn = QPushButton(char_name)
            btn.setCheckable(True)  # 设置为可选中模式
            btn.setFixedWidth(int(scr_w * 0.04))
            btn.setFixedHeight(int(scr_h * 0.03))

            # 如果该角色在当前的 selected_indices 里，设为选中状态
            if i in self.selected_indices:
                btn.setChecked(True)

            # 连接点击信号 (注意：用 clicked 而不是 toggled，方便处理逻辑)
            # 使用 lambda 传参 i
            btn.clicked.connect(lambda checked, idx=i: self.on_btn_clicked(idx, checked))

            self.buttons[i] = btn

            # 计算网格坐标
            row = i // columns
            col = i % columns
            grid_layout.addWidget(btn, row, col)

        layout.addLayout(grid_layout)
        layout.addStretch()  # 弹簧，把按钮顶上去

        # 底部确定按钮
        self.btn_confirm = QPushButton("确定切换")

        self.btn_confirm.setFixedHeight(int(scr_h * 0.03))
        self.btn_confirm.clicked.connect(self.check_and_accept)
        layout.addWidget(self.btn_confirm)

        self.setLayout(layout)

        # 应用样式 (沿用你之前的蓝白风格)
        self.setStyleSheet(fluent_css)

    def on_btn_clicked(self, index, is_checked):
        """核心逻辑：处理按钮点击"""
        if is_checked:
            # === 情况A：用户想选中这个角色 ===

            # 如果当前已经选了2个（或更多），需要“踢掉”一个
            if len(self.selected_indices) >= 2:
                # 踢掉列表里第0个（也就是最早选进来的那个）
                removed_index = self.selected_indices.pop(0)
                # 把对应的按钮视觉状态取消
                if removed_index in self.buttons:
                    self.buttons[removed_index].setChecked(False)

            # 把新的加进去
            self.selected_indices.append(index)

        else:
            # === 情况B：用户取消选中 ===
            if index in self.selected_indices:
                self.selected_indices.remove(index)

    def check_and_accept(self):
        """点击确定时的校验"""
        if len(self.selected_indices) != 2:
            QMessageBox.warning(self, "提示", "需要选择两名角色哦！")
            return

        # 排序一下，如果你希望索引总是从小到大（可选）
        # self.selected_indices.sort()

        self.accept()  # 关闭窗口并返回 QDialog.Accepted

    def get_result(self):
        """外部获取结果的方法"""
        return self.selected_indices



class SettingsDialog(QDialog):
    def __init__(self, parent=None, screen_geo=None,audio_gen_module=None):
        super().__init__(parent)
        self.setWindowTitle("小剧场控制台")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.audio_gen_module=audio_gen_module
        # 1. 动态设置窗口大小 (宽20%，高25%)
        if screen_geo:
            self.resize(int(screen_geo.width() * 0.2), int(screen_geo.height() * 0.35))
            # 动态计算字体大小
            base_font_size = max(12, int(screen_geo.height() * 0.01))
            self.setStyleSheet(f"font-size: {base_font_size}px;")
        else:
            self.resize(400, 300)

        # 引用父窗口
        self.parent_gui = parent

        # 应用全局样式
        self.setStyleSheet(fluent_css)

        layout = QVBoxLayout()

        # === 分组 1: 角色管理 ===
        char_group = QGroupBox("角色管理")
        char_layout = QGridLayout()

        # 按钮高度动态化 (屏幕高度的 4%)
        btn_h = int(screen_geo.height() * 0.04) if screen_geo else 35

        self.btn_change_char = QPushButton("更换角色组合")
        self.btn_change_char.setMinimumHeight(btn_h)

        self.btn_detail_info = QPushButton("填写详细设定")
        self.btn_detail_info.setMinimumHeight(btn_h)

        char_layout.addWidget(self.btn_change_char, 0, 0, 1, 2)
        char_layout.addWidget(self.btn_detail_info, 1, 0, 1, 2)
        char_group.setLayout(char_layout)

        # === 分组 2: 祥子状态控制 ===
        sakiko_group = QGroupBox("祥子的状态")
        sakiko_layout = QGridLayout()

        self.btn_sakiko_state = QPushButton("切换黑/白祥")
        self.btn_sakiko_state.setMinimumHeight(btn_h)

        self.btn_sakiko_model = QPushButton("切换外观")
        self.btn_sakiko_model.setMinimumHeight(btn_h)

        sakiko_layout.addWidget(self.btn_sakiko_state, 0, 0)
        sakiko_layout.addWidget(self.btn_sakiko_model, 0, 1)
        sakiko_group.setLayout(sakiko_layout)

        layout.addWidget(char_group)
        layout.addWidget(sakiko_group)

        audio_talk_speed_group=QGroupBox('调节语速和间隔时间')
        audio_talk_layout=QVBoxLayout()
        talk_speed_layout=QHBoxLayout()
        self.talk_speed_label = QLabel(f"语速调节：{self.audio_gen_module.speed}")  # 此时的数值不是真实的，audio_gen还没有初始化完成
        self.talk_speed_label.setToolTip("调整生成语音的语速，数值越大语速越快。如果觉得生成质量不佳，适当调整一下。")
        self.talk_speed_slider=QSlider(Qt.Horizontal)
        self.talk_speed_slider.setRange(60, 140)
        self.talk_speed_slider.setValue(88)
        self.talk_speed_slider.valueChanged.connect(self.set_talk_speed)
        talk_speed_layout.addWidget(self.talk_speed_label)
        talk_speed_layout.addWidget(self.talk_speed_slider)
        pause_second_layout=QHBoxLayout()
        self.pause_second_label = QLabel(f"句间停顿时间(s)：{self.audio_gen_module.pause_second}")
        self.pause_second_label.setToolTip("调整句子之间的停顿时间，数值越大停顿越久。如果觉得生成质量不佳，适当调整一下。")
        self.pause_second_slider = QSlider(Qt.Horizontal)
        self.pause_second_slider.valueChanged.connect(self.set_pause_second)
        self.pause_second_slider.setRange(10, 80)
        self.pause_second_slider.setValue(50)
        pause_second_layout.addWidget(self.pause_second_label)
        pause_second_layout.addWidget(self.pause_second_slider)
        bgm_volume_layout=QHBoxLayout()
        self.bgm_volume_label=QLabel(f"BGM音量调节：30")
        self.bgm_volume_slider=QSlider(Qt.Horizontal)
        self.bgm_volume_slider.valueChanged.connect(self.change_bgm_volume)
        self.bgm_volume_slider.setRange(0,100)
        self.bgm_volume_slider.setValue(30)
        bgm_volume_layout.addWidget(self.bgm_volume_label)
        bgm_volume_layout.addWidget(self.bgm_volume_slider)
        audio_talk_layout.addLayout(talk_speed_layout)
        audio_talk_layout.addLayout(pause_second_layout)
        audio_talk_speed_group.setLayout(audio_talk_layout)
        layout.addWidget(audio_talk_speed_group)
        layout.addLayout(bgm_volume_layout)

        self.setLayout(layout)

        # === 绑定信号 ===
        if self.parent_gui:
            self.btn_change_char.clicked.connect(self.parent_gui._change_character)
            self.btn_detail_info.clicked.connect(self.parent_gui.config_more_info)
            self.btn_sakiko_state.clicked.connect(self.parent_gui.convert_sakiko_state)
            self.btn_sakiko_model.clicked.connect(self.parent_gui.convert_sakiko_model)

    def set_talk_speed(self):
        speed_value = self.talk_speed_slider.value()
        self.audio_gen_module.speed = speed_value / 100
        self.talk_speed_label.setText(f"语速调节：{self.audio_gen_module.speed:.2f}")

    def set_pause_second(self):
        pause_second_value = self.pause_second_slider.value()
        self.audio_gen_module.pause_second = pause_second_value / 100
        self.pause_second_label.setText(f"句间停顿时间(s)：{self.audio_gen_module.pause_second:.2f}")

    def change_bgm_volume(self):
        value=self.bgm_volume_slider.value()
        self.parent_gui.bgm_player.setVolume(value)
        self.bgm_volume_label.setText(f"BGM音量调节：{value}")


class ViewerGUI(QWidget):
    def __init__(self,
                 characters,

                 qt2dp_queue,
                 message_queue,
                 dp2qt_queue,
                 to_audio_gen_queue,
                 audio_gen_module,
                 to_live2d_module_queue,
                 to_live2d_change_character_queue,
                 tell_qt_this_turn_finish_queue
                 ):
        super().__init__()

        self.setWindowTitle("数字小祥 小剧场")
        self.setWindowIcon(QIcon("../live2d_related/sakiko/sakiko_icon.png"))
        self.screen = QDesktopWidget().screenGeometry()
        self.scr_w = self.screen.width()
        self.scr_h = self.screen.height()
        self.resize(int(0.4 * self.screen.width()), int(0.7 * self.screen.height()))

        self.chat_display = InteractiveChatBrowser()
        self.chat_display.setPlaceholderText("这里显示对话记录...")
        self.chat_display.setOpenExternalLinks(False)  # 禁止自动打开浏览器
        self.chat_display.setOpenLinks(False)
        self.chat_display.anchorClicked.connect(self.on_link_clicked)  # 绑定点击信号
        self.chat_display.edit_signal.connect(self.open_editor) #右键
        self.chat_display.regenerate_signal.connect(self.regenerate_audio)

        # 消息框高度自适应 (屏幕高度的 6%)
        self.messages_box = QTextBrowser()
        self.messages_box.setPlaceholderText("这里是消息提示框...")
        self.messages_box.setMaximumHeight(int(0.05 * self.scr_h))
        # 消息框样式微调：去掉边框，融入背景
        self.messages_box.setStyleSheet("""
                    QTextBrowser {
                        background: transparent; 
                        border: none; 
                        color: #7799CC; 
                        padding-left: 0px;
                        font-weight: bold;
                    }
                """)

        # === 2. 顶部工具栏 (Tool Bar) ===
        self.top_layout = QHBoxLayout()

        # 动态计算图标按钮大小 (屏幕高度的 4%)
        icon_btn_size = int(self.scr_h * 0.04)

        # 创建工具按钮的辅助函数
        def create_tool_btn(tooltip, callback):
            btn = QToolButton()

            btn.setToolTip(tooltip)
            btn.clicked.connect(callback)
            btn.setFixedSize(icon_btn_size, icon_btn_size)
            btn.setIconSize(QSize(int(icon_btn_size * 0.6), int(icon_btn_size * 0.6)))  # 图标本身的大小
            # 工具栏按钮样式：平时透明，鼠标悬停变浅蓝
            btn.setStyleSheet("""
                        QToolButton { 
                            border: none; 
                            background: transparent; 
                            border-radius: 4px;
                        } 
                        QToolButton:hover { 
                            background-color: #D0E2F0; 
                        }
                        QToolButton:pressed {
                            background-color: #B3D1F2;
                        }
                    """)
            return btn

        self.play_bgm_btn=create_tool_btn("播放/暂停bgm",self.toggle_bgm)
        self.play_bgm_btn.setIcon(QIcon('./music.png'))
        self.btn_save = create_tool_btn( "保存对话记录", self.save_history_data)
        self.btn_save.setIcon(QIcon('./save.png'))
        self.btn_settings = create_tool_btn( "设置", self.open_settings_dialog)
        self.btn_settings.setIcon(QIcon('./setting.png'))
        self.btn_close = create_tool_btn( "退出小剧场", self.close_program)
        self.btn_close.setIcon(QIcon('./exit.png'))

        # 布局：左边是消息框，中间弹簧，右边是三个按钮
        self.top_layout.addWidget(self.messages_box,1)
        self.top_layout.addWidget(self.play_bgm_btn,0)
        self.top_layout.addWidget(self.btn_save,0)
        self.top_layout.addWidget(self.btn_settings,0)
        self.top_layout.addWidget(self.btn_close,0)

        # === 3. 底部大按钮 (Generate Button) ===
        self.generate_btn = QPushButton("▶ 生成对话")
        self.generate_btn.setCursor(Qt.PointingHandCursor)
        self.generate_btn.clicked.connect(self.start_generation)

        # 【关键步骤】设置 ObjectName，对应 CSS 中的 #PrimaryBtn
        self.generate_btn.setObjectName("PrimaryBtn")

        # 动态计算大按钮高度
        gen_btn_height = int(self.scr_h * 0.03)
        self.generate_btn.setMinimumHeight(gen_btn_height)

        # 不需要再手动 setStyleSheet 了！
        # 因为上面的 CSS 里的 #PrimaryBtn 已经接管了一切
        # 字体大小可以通过 setFont 设置，或者依然保留一小段 font-size 的 style

        # === 4. 整体布局 ===
        layout = QVBoxLayout()
        layout.addLayout(self.top_layout)  # 顶部
        layout.addWidget(self.chat_display)  # 中间
        layout.addSpacing(int(self.scr_h * 0.01))  # 动态间距
        layout.addWidget(self.generate_btn)  # 底部

        # 动态边距 (屏幕宽度的 1.5%)
        margin = int(self.scr_w * 0.015)
        layout.setContentsMargins(margin, margin, margin, margin)

        self.setLayout(layout)

        # 应用全局样式
        self.setStyleSheet(fluent_css)

        self.qt2dp_queue = qt2dp_queue
        self.message_queue = message_queue
        self.dp2qt_queue = dp2qt_queue
        self.to_audio_gen_queue = to_audio_gen_queue
        self.audio_gen_module = audio_gen_module    #这个没办法，只能把整个实例传进来
        self.audio_gen_module.speed=0.94
        self.audio_gen_module.pause_second=0.6
        self.audio_gen_module.if_small_theater_mode=True
        self.to_live2d_module_queue=to_live2d_module_queue
        self.to_live2d_change_character_queue=to_live2d_change_character_queue
        self.tell_qt_this_turn_finish_queue=tell_qt_this_turn_finish_queue
        self.mes_thread=CommunicateThreadMessages(self.message_queue)
        self.mes_thread.message_signal.connect(self.handle_messages)
        self.mes_thread.start()
        self.response_thread=CommunicateThreadDP2QT(self.dp2qt_queue)
        self.response_thread.response_signal.connect(self.handle_response)
        self.response_thread.start()


        self.user_input = {}
        self.character_list = characters
        self.current_char_index = [0, 1]
        self.original_response = []
        self.char_0_talk_texts = []
        self.char_0_audio_path_list = []
        self.char_talk_texts_match_original_response_indices = []
        self.char_1_talk_texts = []
        self.char_1_audio_path_list = []
        self.history_data_list=[]
        self.dialogs_matching=[]
        self.sakiko_state=True  #黑祥
        if os.path.exists('../reference_audio/small_theater_history.json'):
            with open('../reference_audio/small_theater_history.json', 'r', encoding='utf-8') as f:
                data=json.load(f)
            self.history_data_list,self.dialogs_matching=data
            self.refresh_chat_display()

        self.two_char_names=[]
        self.set_two_char_names()
        self.messages_box.setText(f"当前角色：{self.two_char_names[0]} 和 {self.two_char_names[1]} ")
        self.display_timer = QTimer()
        self.display_timer.timeout.connect(self.display_timer_timeout)

        # === BGM 播放模块 ===
        self.bgm_player = QMediaPlayer()
        self.bgm_playlist = QMediaPlaylist()

        # 1. 设置 BGM 文件路径 (建议用绝对路径，防止 QUrl 找不到文件)
        # 假设你的 BGM 在项目根目录下的 assets 文件夹里
        bgm_path = os.path.abspath("../reference_audio/small_theater_bgm/Normal.mp3")
        url = QUrl.fromLocalFile(bgm_path)

        # 2. 添加到播放列表
        self.bgm_playlist.addMedia(QMediaContent(url))

        # 3. 设置循环模式 (CurrentItemInLoop = 单曲循环)
        self.bgm_playlist.setPlaybackMode(QMediaPlaylist.CurrentItemInLoop)

        # 4. 载入并播放
        self.bgm_player.setPlaylist(self.bgm_playlist)
        self.bgm_player.setVolume(30)  # 音量 0-100 (Pygame是0.0-1.0，注意区别)
        self.bgm_player.play()
        self.bgm_player.pause()

        self.settings_dialog = SettingsDialog(self, self.screen, self.audio_gen_module)

    def display_timer_timeout(self):
        self.set_two_char_names()
        self.messages_box.setText(f"当前角色：{self.two_char_names[0]} 和 {self.two_char_names[1]}")

    def open_settings_dialog(self):
        """打开设置面板"""
        # 可以在打开前更新一下里面的状态显示
        self.settings_dialog.show()

    def toggle_bgm(self):
        if self.bgm_player.state()==QMediaPlayer.PlayingState:
            self.bgm_player.pause()
        elif self.bgm_player.state()==QMediaPlayer.PausedState:
            self.bgm_player.play()


    def handle_messages(self,msg):
        self.messages_box.setText(msg)
        if (msg not in ['生成文本中...']) and ('整理语言' not in msg):
            try:
                self.display_timer.setSingleShot(True)
                self.display_timer.start(2500)
            except Exception as e:
                self.display_timer=QTimer()
                self.display_timer.setSingleShot(True)
                self.display_timer.start(2500)


    def config_more_info(self):
        self.conf_win=MoreInfoDialog(self.character_list[self.current_char_index[0]].character_name,self.character_list[self.current_char_index[1]].character_name,self.user_input)
        self.conf_win.show()

    def _change_character(self):
        dialog = CharacterSelectDialog(self.character_list, self.current_char_index, self)
        # 显示窗口并等待结果 (exec_ 会阻塞直到窗口关闭)
        if dialog.exec_() == QDialog.Accepted:
            #  获取新索引列表
            new_indices = dialog.get_result()
            if new_indices != self.current_char_index:
                self.current_char_index = new_indices
                self.set_two_char_names()
                self.messages_box.setText(f"切换角色为：{self.two_char_names[0]} 和 {self.two_char_names[1]} ")
                self.to_live2d_change_character_queue.put(self.current_char_index)
                self.display_timer.setSingleShot(True)
                self.display_timer.start(2500)

    def set_two_char_names(self):
        self.two_char_names = [self.character_list[self.current_char_index[0]].character_name,
                               self.character_list[self.current_char_index[1]].character_name]
        if self.character_list[self.current_char_index[0]].character_name == '祥子':
            self.two_char_names[0] = '祥子（黑祥）' if self.sakiko_state else '祥子（白祥）'
        if self.character_list[self.current_char_index[1]].character_name == '祥子':
            self.two_char_names[1] = '祥子（黑祥）' if self.sakiko_state else '祥子（白祥）'

    def convert_sakiko_state(self):
        sakiko_exists=False
        for index in self.current_char_index:
            if self.character_list[index].character_name=='祥子':
                sakiko_exists=True
                break
        if not sakiko_exists:
            self.message_queue.put("祥子好像不在...")
            return
        self.sakiko_state=not self.sakiko_state
        self.set_two_char_names()
        self.audio_gen_module.sakiko_which_state=self.sakiko_state
        self.message_queue.put(f"当前角色：{self.two_char_names[0]} 和 {self.two_char_names[1]} ")

    def convert_sakiko_model(self):
        sakiko_exists = False
        for index in self.current_char_index:
            if self.character_list[index].character_name == '祥子':
                sakiko_exists = True
                break
        if not sakiko_exists:
            self.message_queue.put("祥子好像不在...")
            return
        self.to_live2d_change_character_queue.put('change_sakiko_model')


    def start_generation(self):
        self.generate_btn.setEnabled(False)
        temporary_input=copy.deepcopy(self.user_input)
        if self.two_char_names[0]=='祥子':
            if self.sakiko_state:
                temporary_input['character_0']['talk_style']=self.user_input['character_0']['talk_style']+'（此时祥子为黑祥）'
            else:
                temporary_input['character_0']['talk_style']=self.user_input['character_0']['talk_style']+'（此时祥子为白祥）'
            self.audio_gen_module.sakiko_which_state=self.sakiko_state
        elif self.two_char_names[1]=='祥子':
            if self.sakiko_state:
                temporary_input['character_0']['talk_style']=self.user_input['character_0']['talk_style']+'（此时祥子为黑祥）'
            else:
                temporary_input['character_0']['talk_style']=self.user_input['character_0']['talk_style']+'（此时祥子为白祥）'
            self.audio_gen_module.sakiko_which_state = self.sakiko_state
        self.qt2dp_queue.put({'char_index':self.current_char_index,'user_input':temporary_input})



    def handle_response(self,response):
        if response=='error':
            self.generate_btn.setEnabled(True)
            self.display_timer.setSingleShot(True)
            self.display_timer.start(2500)
            return

        self.original_response=self.robust_json_parse(response)
        # self.display_timer.setSingleShot(True)
        # self.display_timer.start(1000)
        if not self.original_response:
            return
        self.char_0_talk_texts=[]
        self.char_0_audio_path_list=[]
        self.char_talk_texts_match_original_response_indices=[]
        self.char_1_talk_texts=[]
        self.char_1_audio_path_list=[]
        idx_0 = self.current_char_index[0]
        idx_1 = self.current_char_index[1]
        for i,turn in enumerate(self.original_response):
            speaker_name_from_llm = turn.get('speaker', '').strip()  # 去除首尾空格
            text = turn.get('text', '')

            # === 【关键修改】使用智能匹配 ===
            matched_idx = self.match_speaker_index(speaker_name_from_llm, [idx_0, idx_1])

            if matched_idx == idx_0:
                self.char_0_talk_texts.append(text)
                self.char_talk_texts_match_original_response_indices.append('char_0')
            elif matched_idx == idx_1:
                self.char_1_talk_texts.append(text)
                self.char_talk_texts_match_original_response_indices.append('char_1')
            else:
                # 两个都没匹配上（比如大模型突然输出了“旁白”或者幻觉出了第三个人）
                self.char_talk_texts_match_original_response_indices.append('skipped')
                print(f"警告: 无法识别说话人 '{speaker_name_from_llm}'，已跳过该句，内容：{text}")
                return
        self.audio_gen_module.audio_language_choice="日英混合"
        self.messages_box.setText(f"切换 {self.character_list[idx_0].character_name} 的GSV模型，..")
        self.audio_gen_change_char_thread_0=WaitUntilAudioGenModuleChangeComplete(self.audio_gen_module,idx_0)
        self.audio_gen_change_char_thread_0.change_complete_signal.connect(self.handle_response_continue)
        self.audio_gen_change_char_thread_0.start()

    def handle_response_continue(self):
        self.messages_box.setText(f"生成 {self.character_list[self.current_char_index[0]].character_name} 的语音..")
        self.audio_gen_generate_audio_thread_0=WaitUntilAudioGenModuleSynthesisComplete(
                                                                                        self.audio_gen_module,
                                                                                        self.char_0_talk_texts,
                                                                                        self.to_audio_gen_queue)
        self.audio_gen_generate_audio_thread_0.synthesis_complete_signal.connect(self.handle_response_continue_1)
        self.audio_gen_generate_audio_thread_0.start()
    def handle_response_continue_1(self,char_0_audio_path_list):
        self.char_0_audio_path_list=char_0_audio_path_list
        self.messages_box.setText(f"切换 {self.character_list[self.current_char_index[1]].character_name} 的GSV模型，..")
        self.audio_gen_change_char_thread_0 = WaitUntilAudioGenModuleChangeComplete(self.audio_gen_module, self.current_char_index[1])
        self.audio_gen_change_char_thread_0.change_complete_signal.connect(self.handle_response_continue_2)
        self.audio_gen_change_char_thread_0.start()
    def handle_response_continue_2(self):
        self.messages_box.setText(f"生成 {self.character_list[self.current_char_index[1]].character_name} 的语音..")
        self.audio_gen_generate_audio_thread_1 = WaitUntilAudioGenModuleSynthesisComplete(
            self.audio_gen_module,
            self.char_1_talk_texts,
            self.to_audio_gen_queue)
        self.audio_gen_generate_audio_thread_1.synthesis_complete_signal.connect(self.final_handle_response)
        self.audio_gen_generate_audio_thread_1.start()
    def final_handle_response(self,char_1_audio_path_list):
        self.char_1_audio_path_list = char_1_audio_path_list
        self.messages_box.setText(f"对话生成完毕")
        self.display_timer.setSingleShot(True)
        self.display_timer.start(1000)
        self.generate_btn.setEnabled(True)

        char_0_text_pointer=0
        char_1_text_pointer=0
        self.playlist_queue = []

        for i,turn in enumerate(self.original_response):
            emotion = self.original_response[i]["emotion"]
            translation=self.original_response[i]["translation"]
            if self.char_talk_texts_match_original_response_indices[i]=='char_0':
                text=self.char_0_talk_texts[char_0_text_pointer]
                audio_path=self.char_0_audio_path_list[char_0_text_pointer]
                char_0_text_pointer+=1
                char_name=self.character_list[self.current_char_index[0]].character_name
            elif self.char_talk_texts_match_original_response_indices[i]=='char_1':
                text = self.char_1_talk_texts[char_1_text_pointer]
                audio_path = self.char_1_audio_path_list[char_1_text_pointer]
                char_1_text_pointer += 1
                char_name = self.character_list[self.current_char_index[1]].character_name
            else:
                text=turn.get('text','')
                audio_path=None
                char_name=turn.get('speaker','未知角色')

            self.playlist_queue.append({
                "text": text,
                "audio_path": audio_path,
                "char_name": char_name,
                "emotion":emotion,
                "translation":translation,
                "live2d_motion_num":''
            })
        self.to_live2d_module_queue.put(self.playlist_queue)
        self.dialogs_matching.append({f"新的对话{len(self.dialogs_matching)}":[len(self.history_data_list),len(self.playlist_queue)]})
        self.display_text_thread=DisplayTalkText(self.tell_qt_this_turn_finish_queue)
        self.display_text_thread.talk_text_signal.connect(self.display_this_turn_text)
        self.display_text_thread.start()

    def display_this_turn_text(self,this_turn_elements):
        for turn in self.history_data_list:
            if turn['audio_path']==this_turn_elements['audio_path']:
                return  #说明用户在回放，不可显示
        current_index = len(self.history_data_list)
        self.history_data_list.append(this_turn_elements)
        # 生成链接，href 格式设为 "replay:索引号"
        # 例如: href="replay:5"
        #text_color = self.chat_display.palette().color(QPalette.Text).name()
        # 注意这里 href 的写法
        html_content = (
            f'<a href="replay:{current_index}" style="text-decoration: none; color: #7799CC;">'
            f'★{this_turn_elements["char_name"]}：{this_turn_elements["text"]}'
            f'</a>'
        )
        html_translation_content=f'<span style="color: #B3D1F2; font-style: italic;">{this_turn_elements["translation"]}</span><br>'
        for dialog in self.dialogs_matching:
            key, val = next(iter(dialog.items()))
            if val[0] == current_index:
                self.chat_display.append(f'<a href="replaydialog:{val[0]},{val[1]}" style="color: #ED784A">{key}\n</a>')
                break

        self.chat_display.append(html_content)
        self.chat_display.append(html_translation_content)

    def on_link_clicked(self, url):
        # url 是一个 QUrl 对象
        url_str = url.toString()
        # 检查是否是我们自定义的 replay 协议
        if url_str.startswith("replay:"):
            try:
                # 提取冒号后面的数字
                index_str = url_str.split(":")[1]
                index = int(index_str)

                # 从列表里找回原来的数据
                if 0 <= index < len(self.history_data_list):
                    original_data = self.history_data_list[index]

                    which_dialog=0
                    for i,dialog in enumerate(self.dialogs_matching):
                        key, val = next(iter(dialog.items()))
                        if val[0]<=index<val[1]+val[0]:
                            which_dialog=i  #第i次对话
                    which_characters=[]     #下面找出第which_dialog次对话是哪两个角色
                    _,turn_num=next(iter(self.dialogs_matching[which_dialog].items()))

                    for turn in self.history_data_list[turn_num[0]:(turn_num[0]+turn_num[1])]:
                        if turn['char_name'] not in which_characters:
                            which_characters.append(turn['char_name'])
                        if len(which_characters)==2:
                            break

                    self.to_live2d_change_character_queue.put(which_characters)
                    self.to_live2d_module_queue.put([original_data])
            except Exception as e:
                print(f"回放解析错误: {e}")
        elif url_str.startswith("replaydialog:"):
            try:
                # 3. 去掉前缀，提取内容部分
                # split(":", 1) 意思是以冒号切分，只切第一个冒号，防止内容里也有冒号
                content = url_str.split(":", 1)[1]

                # 4. 解包：按逗号分割
                # val_str_0 和 val_str_1 此时都是字符串类型
                val_str_0, val_str_1 = content.split(",")

                # 5. 【重要】类型还原
                # 假设 val[0] 原本是列表索引(int)，val[1] 是字符串
                restored_val_0 = int(val_str_0)
                restored_val_1 = int(val_str_1)
                replay_turns=self.history_data_list[restored_val_0:restored_val_0+restored_val_1]

                which_characters = []
                for turn in replay_turns:
                    if turn['char_name'] not in which_characters:
                        which_characters.append(turn['char_name'])
                    if len(which_characters) == 2:
                        break
                self.to_live2d_change_character_queue.put(which_characters)
                self.to_live2d_module_queue.put(replay_turns)
            except Exception as e:
                print(f"解析链接出错: {e}")

    def open_editor(self, index):
        """打开编辑器"""
        if index < 0 or index >= len(self.history_data_list):
            return

        # 获取当前数据
        current_data = self.history_data_list[index]

        # 弹出对话框
        dialog = ElementEditorDialog(current_data, self)
        if dialog.exec_() == QDialog.Accepted:
            # 1. 获取修改后的数据
            new_data = dialog.get_new_data()

            # 2. 更新内存列表 (这是用于回放的实际数据)
            self.history_data_list[index] = new_data
            #print(f"数据已更新: {new_data}")

            # 3. 【难点】更新 UI 显示
            # 因为 HTML 已经渲染在屏幕上了，单独修改某一行比较麻烦。
            # 最简单的办法是：重新渲染整个聊天记录（如果记录不是特别多）
            self.refresh_chat_display()

    def refresh_chat_display(self):
        """清空屏幕并根据 history_data_list 重新显示"""
        # === 1. 【保存】当前滚动条位置 ===
        scroll_bar = self.chat_display.verticalScrollBar()
        saved_position = scroll_bar.value()

        # 获取滚动条最大值，判断用户是否本来就在最底下
        # 如果用户本来就在最底下，刷新后让他继续保持在最底下，体验更好
        was_at_bottom = (saved_position == scroll_bar.maximum())

        self.chat_display.clear()

        # 这里的逻辑其实就是把 display_this_turn_text 的逻辑抽离出来
        # 注意：这里重新生成 HTML 时，href 里的索引要对应正确
        is_print_title=[]
        title_pointer=0
        for dialog in self.dialogs_matching:
            key, val = next(iter(dialog.items()))
            is_print_title.append(val[0])
        for i, elements in enumerate(self.history_data_list):
            if i in is_print_title:
                key,val=next(iter(self.dialogs_matching[title_pointer].items()))
                self.chat_display.append(f'<a href="replaydialog:{val[0]},{val[1]}" style="color: #ED784A">{key}\n</a>')
                title_pointer+=1
            #text_color = self.chat_display.palette().color(QPalette.Text).name()
            html_content = (
                f'<a href="replay:{i}" style="text-decoration: none; color: #7799CC;">'
                f'★{elements["char_name"]}：{elements["text"]}'
                f'</a>'
            )
            html_translation_content = f'<span style="color: #B3D1F2; font-style: italic;">{elements["translation"]}</span><br>'
            self.chat_display.append(html_content)
            self.chat_display.append(html_translation_content)

        QApplication.processEvents()

        # === 3. 【恢复】滚动条位置 ===
        if was_at_bottom:
            # 如果原来就在最底下，就让它保持在新的最底下（因为内容长度可能变了）
            scroll_bar.setValue(scroll_bar.maximum())
        else:
            # 否则，恢复到之前保存的像素位置
            scroll_bar.setValue(saved_position)

    def regenerate_audio(self,index):
        self.audio_gen_module.audio_language_choice = "日英混合"
        self.regenerate_text=self.history_data_list[index]['text']
        this_char_name=self.history_data_list[index]['char_name']
        char_index=0
        self.replace_audio_index = index
        for i,char in enumerate(self.character_list):
            if this_char_name==char.character_name:
                char_index=i
                break
        if this_char_name!=self.audio_gen_module.character_list[self.audio_gen_module.current_character_index].character_name:
            self.messages_box.setText(f"正在切换 {this_char_name} 的GSV模型，..")
            self.change_audio_module_character_thread=WaitUntilAudioGenModuleChangeComplete(self.audio_gen_module,char_index)
            self.change_audio_module_character_thread.change_complete_signal.connect(self.regenerate_audio_continue)
            self.change_audio_module_character_thread.start()
        else:
            self.regenerate_audio_continue()

    def regenerate_audio_continue(self):
        self.generate_thread = WaitUntilAudioGenModuleSynthesisComplete(self.audio_gen_module, [self.regenerate_text],self.to_audio_gen_queue)
        self.generate_thread.synthesis_complete_signal.connect(self.replace_audio_path)
        self.generate_thread.start()


    def replace_audio_path(self,audio_path):
        self.message_queue.put("生成完毕")
        self.to_live2d_module_queue.put([self.history_data_list[self.replace_audio_index]])
        self.history_data_list[self.replace_audio_index]['audio_path']=audio_path[0]

    def save_history_data(self):
        data=[self.history_data_list,self.dialogs_matching]
        with open('../reference_audio/small_theater_history.json','w',encoding='utf-8') as f:
            json.dump(data,f, ensure_ascii=False, indent=4)
        self.message_queue.put("已保存最新记录")


    def close_program(self):
        self.save_history_data()
        self.to_live2d_change_character_queue.put('EXIT')
        self.to_audio_gen_queue.put('bye')
        self.dp2qt_queue.put('EXIT')
        self.message_queue.put('EXIT')
        self.qt2dp_queue.put('EXIT')
        self.close()



    def match_speaker_index(self, llm_name, candidate_indices):
        """
        智能匹配说话人。
        :param llm_name: 大模型输出的名字 (如 "愛音", "Role: 素世")
        :param candidate_indices: 当前场上的角色索引列表 [0, 1]
        :return: 匹配到的角色索引，如果都没匹配上返回 None
        """
        if not llm_name:
            return None

        best_match_idx = None
        max_score = 0

        # 转换为集合，方便做交集运算
        llm_name_set = set(llm_name)

        for idx in candidate_indices:
            local_name = self.character_list[idx].character_name
            local_name_set = set(local_name)

            # --- 策略 A: 完全相等 (得分最高) ---
            if llm_name == local_name:
                return idx

            # --- 策略 B: 包含关系 (得分次高) ---
            # 应对 "Role: 爱音" 或 "爱音(高兴)" 这种情况
            if local_name in llm_name:
                return idx

            # --- 策略 C: 字符交集 (解决繁体/错别字的关键) ---
            # 计算两个名字里有多少个字是相同的
            common_chars = llm_name_set & local_name_set
            score = len(common_chars)

            # 如果有重合的字，并且比之前的匹配度更高
            if score > 0 and score > max_score:
                max_score = score
                best_match_idx = idx

        # 这里可以加个阈值，比如至少重合1个字
        return best_match_idx

    def robust_json_parse(self,llm_output):
        """
        提高鲁棒性，防止大模型回传的 JSON 胡言乱语
        """
        #去掉可能会导致解析失败的特殊控制字符
        llm_output = llm_output.strip()
        #print(llm_output)

        # === 策略 A：直接尝试解析 ===
        try:
            return json.loads(llm_output)
        except:

            pass

        # === 策略 B：正则提取 (解决"外部多了字"的问题) ===
        # 寻找最外层的 [] 或 {}
        # re.DOTALL 让 . 也能匹配换行符
        match = re.search(r'(\[.*\]|\{.*\})', llm_output, re.DOTALL)

        json_str = ""
        if match:
            json_str = match.group(1)
        else:
            # 如果连括号都找不到，那真的没救了
            print("未在输出中找到 JSON 结构，重新生成一遍吧")
            return []

        # === 策略 C：再次尝试标准解析 (针对提取后的字符串) ===
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass  # 还是失败？可能是用了单引号

        # === 策略 D：AST 评估 (解决"单引号"或"Python风格字典"问题) ===
        # 大模型经常把 JSON 写成 Python 字典（比如用 ' 而不是 "）
        # ast.literal_eval 可以安全地把 Python 格式的字符串转成对象
        try:
            return ast.literal_eval(json_str)
        except (ValueError, SyntaxError):
            pass

        # === 策略 E：手动修补常见错误 (简单的漏逗号) ===
        # 这是一个比较暴力的修补：如果在 } 和 { 之间少了逗号，强行加一个
        try:
            # 把 "} {" 替换成 "}, {"
            fixed_str = re.sub(r'\}\s*\{', '}, {', json_str)
            return json.loads(fixed_str)
        except:
            pass

        print(f"解析彻底失败，原始内容:\n{llm_output}")
        return []  # 返回空列表作为兜底




if __name__ == "__main__":
    import multiprocessing as mp
    from dp_local_multi_char import DSLocalAndVoiceGen
    from audio_generator import AudioGenerate
    from multi_char_live2d_module import run_live2d_process

    # Windows/macOS 下默认是 spawn；显式声明有助于一致性
    mp.freeze_support()
    ctx = mp.get_context("spawn")

    import os
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    get_char_attr = character.GetCharacterAttributes()

    dp_module = DSLocalAndVoiceGen(get_char_attr.character_class_list)
    audio_gen = AudioGenerate()

    # === 进程/线程通信：统一用 multiprocessing 的 Queue（spawn 语义下可用）===
    # 重要：不要在启动 Live2D 子进程时传入 get_char_attr.character_class_list 这种大对象，
    # 以避免 Windows 进程启动参数大小限制（约 8-16KB）导致的失败。
    change_char_queue = ctx.Queue()
    qt2dp_queue = ctx.Queue()
    message_queue = ctx.Queue()
    dp2qt_queue = ctx.Queue()
    to_audio_gen_queue = ctx.Queue()
    to_live2d_module_queue = ctx.Queue()
    tell_qt_this_turn_finish_queue = ctx.Queue()

    audio_gen.initialize(get_char_attr.character_class_list, message_queue)

    # Live2D：必须在子进程的主线程里创建窗口（macOS NSWindow 限制）
    live2d_process = ctx.Process(
        target=run_live2d_process,
        args=(change_char_queue, to_live2d_module_queue, tell_qt_this_turn_finish_queue),
        name="Live2DProcess",
    )

    dp_thread = threading.Thread(
        target=dp_module.text_generator,
        args=(dp2qt_queue, qt2dp_queue, message_queue),
        daemon=True,
    )
    audio_generate_thread = threading.Thread(
        target=audio_gen.audio_generator,
        args=(dp_module, to_audio_gen_queue),
        daemon=True,
    )

    app = QApplication(sys.argv)

    window = ViewerGUI(get_char_attr.character_class_list, qt2dp_queue, message_queue, dp2qt_queue,to_audio_gen_queue, audio_gen,to_live2d_module_queue,change_char_queue,tell_qt_this_turn_finish_queue)
    font_id = QFontDatabase.addApplicationFont("../font/msyh.ttc")  # 设置字体
    font_family = QFontDatabase.applicationFontFamilies(font_id)
    # 在成功加载后才使用该字体
    if font_family:
        font = QFont(font_family[0], 12)
        app.setFont(font)

    from PyQt5.QtWidgets import QDesktopWidget  # 设置qt窗口位置，与live2d对齐

    screen_w_mid = int(0.55 * QDesktopWidget().screenGeometry().width())
    screen_h_mid = int(0.5 * QDesktopWidget().screenGeometry().height())
    window.move(screen_w_mid,
                int(screen_h_mid - 0.35 * QDesktopWidget().screenGeometry().height()))  # 因为窗口高度设置的是0.7倍桌面宽

    live2d_process.start()
    dp_thread.start()
    audio_generate_thread.start()
    window.show()
    app.exec_()

    # 开始清理
    try:
        change_char_queue.put("EXIT", block=False)
    except Exception:
        pass
    try:
        tell_qt_this_turn_finish_queue.put("EXIT", block=False)
    except Exception:
        pass
    try:
        to_audio_gen_queue.put("bye", block=False)
    except Exception:
        pass
    try:
        qt2dp_queue.put("EXIT", block=False)
    except Exception:
        pass

    dp_thread.join()
    audio_generate_thread.join()
    # 给子进程一点时间自我退出；否则强制 terminate 避免僵尸进程
    try:
        live2d_process.join(timeout=3)
    except Exception:
        pass
    if live2d_process.is_alive():
        try:
            live2d_process.terminate()
            live2d_process.join(timeout=3)
        except Exception:
            pass

'''
按钮改为图标式
'''