from __future__ import annotations

import sys, os,json,re,ast
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

if __name__ == "__main__":
    from maintenance.bootstrap import recover_before_startup
    recover_before_startup(Path(__file__).resolve().parents[1])

from PyQt5.QtCore import QTimer, QThread, pyqtSignal, QUrl, Qt, QSize
from PyQt5.QtMultimedia import QMediaPlayer, QMediaPlaylist, QMediaContent

from qconfig import DEFAULT_MULTI_CHAR_BGM_PATH, d_sakiko_config
from ui_main.components.custom_bgm_dialog import (
    BGM_DIRECTORY,
    SUPPORTED_BGM_SUFFIXES,
    CustomBGMDialog,
    validate_bgm_media_file,
)

import time,copy
import threading

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QTextBrowser, QPushButton, QDesktopWidget, QHBoxLayout, \
    QGridLayout, QApplication, QLabel, QGroupBox, QDialog, QMessageBox, QMenu, QFormLayout, QDialogButtonBox, \
    QToolButton, QStyle, QSlider, QListWidget, QListWidgetItem, QInputDialog, QComboBox, QTextEdit, QListView, QStyledItemDelegate

from PyQt5.QtGui import QCloseEvent, QFontDatabase, QFont, QIcon, QTextCursor, QPalette

import faulthandler
import character
from chat.chat import ChatManager, Chat, ChatType, Message, SmallTheaterPromptGenerator, get_chat_manager
from chat.chat_meta import TheaterMeta
from log import get_logger, setup_logging, get_log_queue, shutdown_logging
from qtUI import ChangeL2DModelWindow
from live2d_support.model_catalog import Live2DModelOption
from live2d_support.motion_capabilities import get_live2d_motion_capabilities
from live2d_support.model_normalizer import normalize_live2d_model_for_project
from live2d_support.runtime_adapter import detect_live2d_runtime_version
from ui.components.multi_char_icons import set_button_icon


logger = get_logger(__name__)
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
QTextBrowser, QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: #FFFFFF;
    border: 1px solid #E0E0E0;
    border-bottom: 2px solid #D1D1D1; /* 底部稍微深一点 */
    border-radius: 4px;
    padding: 8px;
    color: {TEXT_COLOR};
}}

/* 悬停状态：背景变灰白 */
QTextBrowser:hover, QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {{
    background-color: #FDFDFD;
    border-bottom: 2px solid {THEME_COLOR}; /* 悬停时底部变蓝 */
}}

/* 聚焦状态：全边框高亮 */
QTextBrowser:focus, QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    background-color: #FFFFFF;
    border: 2px solid {THEME_COLOR}; /* 激活时全蓝 */
}}

QComboBox {{
    background-color: #FFFFFF;
    border: 1px solid #E0E0E0;
    border-bottom: 2px solid #D1D1D1;
    border-radius: 4px;
    color: {TEXT_COLOR};
    padding: 6px 28px 6px 10px;
    min-height: 24px;
}}

QComboBox:hover {{
    background-color: #FDFDFD;
    border-bottom: 2px solid {THEME_COLOR};
}}

QComboBox:focus {{
    background-color: #FFFFFF;
    border: 2px solid {THEME_COLOR};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 26px;
    border: none;
    background: transparent;
}}

QComboBox::down-arrow {{
    image: none;
    width: 0;
    height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #6D7B8D;
    margin-right: 8px;
}}

/* 下拉列表容器 */
QComboBox QAbstractItemView {{
    background-color: #FFFFFF;
    border: 1px solid #E0E0E0;
    border-radius: 4px;
    padding: 4px;
    outline: none; /* 去掉虚线框 */
    margin-top: 4px; /* 与输入框拉开一点距离 */
}}

/* 下拉列表项 */
QComboBox QAbstractItemView::item {{
    height: 32px; /* 增加高度 */
    border-radius: 4px; /* 圆角项 */
    padding-left: 8px;
    color: {TEXT_COLOR};
    background-color: transparent;
}}

/* 下拉列表项悬停/选中 */
QComboBox QAbstractItemView::item:hover, 
QComboBox QAbstractItemView::item:selected {{
    background-color: #E9F1FB; /* 浅蓝色背景 */
    color: {THEME_COLOR_PRESSED};
    border: none;
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

QScrollBar:horizontal {{
    border: none;
    background: transparent;
    height: 8px;
    margin: 0px;
}}

QScrollBar::handle:horizontal {{
    background: #C0C0C0;
    min-width: 20px;
    border-radius: 4px;
}}

QScrollBar::handle:horizontal:hover {{
    background: #A0A0A0;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

QListWidget {{
    background-color: #FFFFFF;
    border: 1px solid #E0E0E0;
    border-radius: 6px;
    padding: 4px;
}}

QListWidget::item {{
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 8px;
    margin: 2px 0px;
}}

QListWidget::item:hover {{
    background: #F3F7FC;
}}

QListWidget::item:selected {{
    background: #DDE9F8;
    border: 1px solid #B3D1F2;
    color: #2E4A6B;
}}

QMenu {{
    background: #FFFFFF;
    border: 1px solid #E0E0E0;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 16px;
    border-radius: 4px;
}}

QMenu::item:selected {{
    background: #E9F1FB;
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

/* ================= 8.工具提示 ================= */
QToolTip {{
    background-color: #FFFFFF;
    color: {TEXT_COLOR};
    border: 1px solid #E0E0E0;
    border-radius: 4px;
    padding: 4px;
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
            try:
                data = self.dp2qt_queue.get()
            except EOFError:
                break
            if data=='EXIT':
                break
            self.response_signal.emit(data)

class DisplayTalkText(QThread):
    talk_text_signal=pyqtSignal(dict)
    stage_closed = pyqtSignal()
    def __init__(self,tell_qt_this_turn_finish_queue):
        """建立唯一舞台回执消费者，区分正常消息和前台退出。"""
        super().__init__()
        self.tell_qt_this_turn_finish_queue=tell_qt_this_turn_finish_queue

    def run(self):
        """持续传回舞台事件，进程退出通知编辑器保留草稿。"""
        while True:
            x=self.tell_qt_this_turn_finish_queue.get()
            if x=='EXIT':
                self.stage_closed.emit()
                break
            self.talk_text_signal.emit(x)


class WaitUntilAudioGenModuleSynthesisComplete(QThread):
    synthesis_complete_signal = pyqtSignal(list)
    synthesis_failed_signal = pyqtSignal(str)
    def __init__(self, audio_gen_module, char_texts_list, character, sakiko_state, audio_language_choice,
                 char_emotions_list=None):
        super().__init__()
        self.audio_gen_module = audio_gen_module
        self.char_texts_list = char_texts_list
        self.character = character
        self.sakiko_state = sakiko_state
        self.audio_language_choice = audio_language_choice
        self.char_emotions_list = char_emotions_list or []

    def run(self):
        """合成角色台词并返回音频，异常时报告失败以保留旧剧本。"""
        try:
            self.synthesize()
        except Exception:
            logger.exception("小剧场语音合成失败")
            self.synthesis_failed_signal.emit("语音合成失败，原剧本已保留。")

    def synthesize(self):
        """依次合成角色台词，全部完成后发送结果。"""
        char_text_audio_path_list = []
        for index, text in enumerate(self.char_texts_list):
            emotion = self.char_emotions_list[index] if index < len(self.char_emotions_list) else None
            char_text_audio_path_list.append(
                self.audio_gen_module.generate_audio_for_character_sync(
                    text,
                    self.character,
                    self.sakiko_state,
                    self.audio_language_choice,
                    emotion=emotion,
                )
            )
        self.synthesis_complete_signal.emit(char_text_audio_path_list)


class DragDropListWidget(QListWidget):
    """
    一个启用拖拽排序，且在拖拽后发出信号的 QListWidget 子类。
    """
    order_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QListView.DragDropMode.InternalMove)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.order_changed.emit()  # 在拖拽完成后发出信号


class MoreInfoDialog(QDialog):
    """编辑小剧场场景和角色细节配置的对话框。"""

    info_committed = pyqtSignal(dict)

    def __init__(self, char_name_0: str, char_name_1: str, initial_meta: Dict[str, Any], parent=None, replacing=False):
        """展示设定副本，只有开始生成才提交；取消不改变设定或剧本。"""
        super().__init__(parent)
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

        if replacing:
            warning = QLabel("生成成功后将替换当前剧本，包括音频与演出编排。取消或生成失败会保留原剧本。")
            warning.setWordWrap(True)
            main_layout.addWidget(warning)
        buttons = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        self.commit_btn = QPushButton("开始生成！")
        self.commit_btn.clicked.connect(self.commit_info)
        self.commit_btn.setObjectName("PrimaryBtn")
        buttons.addWidget(self.commit_btn)
        main_layout.addLayout(buttons)

        self.setLayout(main_layout)

        # 针对性的 QSS 美化
        self.setStyleSheet(fluent_css)
        self.initial_meta = copy.deepcopy(initial_meta) if isinstance(initial_meta, dict) else {}
        character_0 = self.initial_meta.get("character_0", {})
        character_1 = self.initial_meta.get("character_1", {})

        self.char_0_talk_style.setText(character_0.get("talk_style", ""))
        self.char_0_inter_details.setText(character_0.get("interaction_details", ""))
        self.char_1_talk_style.setText(character_1.get("talk_style", ""))
        self.char_1_inter_details.setText(character_1.get("interaction_details", ""))
        self.situation_input.setText(self.initial_meta.get("situation", ""))

    def commit_info(self) -> None:
        """仅提交草稿；主界面保存并成功投递请求后才关闭窗口。"""
        if not self.commit_btn.isEnabled():
            return
        self.commit_btn.setEnabled(False)
        payload = {
            "character_0": {
                "talk_style": self.char_0_talk_style.text(),
                "interaction_details": self.char_0_inter_details.text(),
            },
            "character_1": {
                "talk_style": self.char_1_talk_style.text(),
                "interaction_details": self.char_1_inter_details.text(),
            },
            "situation": self.situation_input.text(),
        }
        self.info_committed.emit(payload)


class ElementEditorDialog(QDialog):
    def __init__(self, data_dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("修改对话数据")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.data = data_dict  # 引用传递，直接改这个字典

        layout = QFormLayout(self)

        # === 创建输入框 ===
        self.char_name_edit = QLineEdit(self.data.get('character_name', ''))
        self.char_name_edit.setEnabled(False)
        self.text_edit = QLineEdit(self.data.get('text', ''))
        self.translation_edit = QLineEdit(self.data.get('translation', ''))
        self.emotion_edit=QLineEdit(self.data.get('emotion', ''))
        self.emotion_edit.setEnabled(False)


        layout.addRow("角色名:", self.char_name_edit)
        layout.addRow("文本内容:", self.text_edit)
        layout.addRow("翻译:", self.translation_edit)
        layout.addRow("情感标签:", self.emotion_edit)

        # === 确认/取消按钮 ===
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_new_data(self):
        """返回修改后的字典"""
        return {
            **self.data,
            "character_name": self.data.get('character_name', ''),
            "text": self.text_edit.text(),
            "emotion": self.emotion_edit.text(),
            "translation": self.translation_edit.text(),
            "audio_path": self.data.get("audio_path", "")
        }


class InteractiveChatBrowser(QTextBrowser):
    # 定义一个信号：当用户点击"编辑"时，发送该链接对应的索引号
    edit_signal = pyqtSignal(int)
    regenerate_signal=pyqtSignal(int)
    performance_signal = pyqtSignal(int)
    def contextMenuEvent(self, event):
        """为台词链接提供语音重生成及演出编排入口。"""
        # 1. 获取鼠标位置下的链接地址
        # anchorAt 是 Qt 5.10+ 引入的，非常方便
        link = self.anchorAt(event.pos())

        # 2. 如果鼠标底下有链接，且是我们定义的 "replay:" 格式
        if link and link.startswith("replay:"):
            # 创建右键菜单
            menu = QMenu(self)

            performance_action = menu.addAction("演出编排")

            # 添加 "重新播放" 动作 (可选)
            regen_action = menu.addAction("重新生成这条音频")

            # 显示菜单并等待用户选择
            action = menu.exec_(self.mapToGlobal(event.pos()))

            # 解析索引 ID
            try:
                index = int(link.split(":")[1])

                if action == regen_action:
                    self.regenerate_signal.emit(index)
                elif action == performance_action:
                    self.performance_signal.emit(index)

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



class NewTheaterChatDialog(QDialog):
    def __init__(self, character_list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建小剧场对话")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.character_list = character_list

        screen = QDesktopWidget().screenGeometry()
        self.resize(int(screen.width() * 0.28), int(screen.height() * 0.34))

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.chat_name_edit = QLineEdit()
        self.chat_name_edit.setPlaceholderText("可选，例如：雨夜车站偶遇")

        # 一个角色的下拉选择框，列出所有角色供选择
        self.char_0_combo = QComboBox()
        self.char_1_combo = QComboBox()

        #修复 macOS 下原生弹窗样式问题（Windows 下不存在此问题，但该修复也不会有负面作用）
        # 设置 QListView 作为视图，强制使用 Qt 自绘而非 macOS 原生弹窗
        self.char_0_combo.setView(QListView())
        self.char_1_combo.setView(QListView())

        # 设置 ItemDelegate 可以让项高度等样式生效更稳定（可选，但推荐）
        self.char_0_combo.setItemDelegate(QStyledItemDelegate())
        self.char_1_combo.setItemDelegate(QStyledItemDelegate())

        for char_obj in self.character_list:
            self.char_0_combo.addItem(char_obj.character_name)
            self.char_1_combo.addItem(char_obj.character_name)
        if self.char_1_combo.count() > 1:
            self.char_1_combo.setCurrentIndex(1)

        self.scene_edit = QTextEdit()
        self.scene_edit.setPlaceholderText("请输入本次小剧场的场景设定（可选）")
        self.scene_edit.setMinimumHeight(int(screen.height() * 0.11))

        form.addRow("对话名称", self.chat_name_edit)
        form.addRow("角色 A", self.char_0_combo)
        form.addRow("角色 B", self.char_1_combo)
        form.addRow("场景设定", self.scene_edit)
        layout.addLayout(form)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.button(QDialogButtonBox.Ok).setText("创建")
        button_box.button(QDialogButtonBox.Cancel).setText("取消")
        button_box.accepted.connect(self._try_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setStyleSheet(fluent_css)

    def _try_accept(self):
        if self.char_0_combo.currentText() == self.char_1_combo.currentText():
            QMessageBox.warning(self, "提示", "请为小剧场选择两名不同角色")
            return
        self.accept()

    def get_payload(self):
        char_0 = self.char_0_combo.currentText()
        char_1 = self.char_1_combo.currentText()
        scene = self.scene_edit.toPlainText().strip()
        name = self.chat_name_edit.text().strip() or f"{char_0}与{char_1}的对话"
        return {
            "name": name,
            "characters": [char_0, char_1],
            "scene": scene,
        }


class SettingsDialog(QDialog):
    def __init__(self, parent, character_names: List[str], screen_geo, audio_gen_module):
        """构建小剧场角色、渲染及音频设置，并连接主控制台操作。"""
        super().__init__(parent)
        self.setWindowTitle("小剧场控制台")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.audio_gen_module=audio_gen_module
        # 当前小剧场使用的角色列表（字符串列表）
        self.character_names = character_names
        # 所有的角色信息
        self.all_characters = character.GetCharacterAttributes().character_class_list
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

        char_layout.addWidget(self.btn_change_char, 0, 0, 1, 2)
        char_group.setLayout(char_layout)

        # === 分组 2: 祥子状态控制 ===
        sakiko_group = QGroupBox("祥子的状态")
        sakiko_layout = QGridLayout()

        self.btn_sakiko_state = QPushButton("切换黑/白祥")
        self.btn_sakiko_state.setMinimumHeight(btn_h)

        self.btn_sakiko_model = QPushButton("面具")
        self.btn_sakiko_model.setMinimumHeight(btn_h)

        sakiko_layout.addWidget(self.btn_sakiko_state, 0, 0)
        sakiko_layout.addWidget(self.btn_sakiko_model, 0, 1)
        sakiko_group.setLayout(sakiko_layout)

        layout.addWidget(char_group)
        layout.addWidget(sakiko_group)

        # === 分组 3:角色 live2d 模型切换
        live2d_group = QGroupBox("Live2D 模型切换")
        live2d_layout = QHBoxLayout()

        self.btn_character_0_model = QPushButton(character_names[0] if len(character_names) >= 2 else "角色 A 模型")
        self.btn_character_0_model.setMinimumHeight(btn_h)
        self.btn_character_1_model = QPushButton(character_names[1] if len(character_names) >= 2 else "角色 B 模型")
        self.btn_character_1_model.setMinimumHeight(btn_h)
        self.btn_character_0_model.clicked.connect(lambda: self.change_live2d_model(0))
        self.btn_character_1_model.clicked.connect(lambda: self.change_live2d_model(1))
        live2d_layout.addWidget(self.btn_character_0_model)
        live2d_layout.addWidget(self.btn_character_1_model)
        live2d_group.setLayout(live2d_layout)

        layout.addWidget(live2d_group)

        live2d_render_group = QGroupBox("Live2D 渲染设置")
        live2d_render_layout = QHBoxLayout()
        current_fps = self.parent_gui.get_current_l2d_fps() if self.parent_gui else 60
        self.btn_live2d_fps = QPushButton(f"Live2D渲染帧率：{current_fps}")
        self.btn_live2d_fps.setMinimumHeight(btn_h)
        self.btn_set_bgm = QPushButton("设置背景音乐")
        self.btn_set_bgm.setMinimumHeight(btn_h)
        self.btn_motion_facing = QPushButton(self.parent_gui.motion_facing_mode_button_text() if self.parent_gui else "对话朝向：正常")
        self.btn_motion_facing.setMinimumHeight(btn_h)
        live2d_render_layout.addWidget(self.btn_live2d_fps)
        live2d_render_layout.addWidget(self.btn_motion_facing)
        live2d_render_group.setLayout(live2d_render_layout)
        layout.addWidget(live2d_render_group)

        if self.parent_gui is None or not self.parent_gui.is_motion_facing_mode_available():
            self.btn_motion_facing.hide()

        # 如果当前没有角色，隐藏 live2d 模型切换按钮
        if len(character_names) < 2:
            live2d_group.hide()
            self.btn_character_0_model.hide()
            self.btn_character_1_model.hide()
        # 根据当前对话角色动态显示祥子状态控制按钮
        if "祥子" in self.character_names:
            sakiko_group.show()
            self.btn_sakiko_state.show()
            self.btn_sakiko_model.show()
        else:
            sakiko_group.hide()
            self.btn_sakiko_state.hide()
            self.btn_sakiko_model.hide()

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
        bgm_volume_layout.addWidget(self.btn_set_bgm)
        audio_talk_layout.addLayout(talk_speed_layout)
        audio_talk_layout.addLayout(pause_second_layout)
        audio_talk_speed_group.setLayout(audio_talk_layout)
        layout.addWidget(audio_talk_speed_group)
        layout.addLayout(bgm_volume_layout)

        self.setLayout(layout)

        # === 绑定信号 ===
        if self.parent_gui:
            self.btn_change_char.clicked.connect(self.parent_gui._change_character)
            self.btn_sakiko_state.clicked.connect(self.parent_gui.convert_sakiko_state)
            self.btn_sakiko_model.setMenu(self.parent_gui.create_mask_menu())
            self.btn_live2d_fps.clicked.connect(self.change_l2d_fps)
            self.btn_set_bgm.clicked.connect(self.parent_gui.set_bgm)
            self.btn_motion_facing.clicked.connect(self.change_motion_facing_mode)

    def refresh_motion_facing_button(self) -> None:
        """根据当前模型组合刷新动作朝向按钮的显隐和文案。"""
        if not self.parent_gui:
            self.btn_motion_facing.hide()
            return
        self.btn_motion_facing.setText(self.parent_gui.motion_facing_mode_button_text())
        self.btn_motion_facing.setVisible(self.parent_gui.is_motion_facing_mode_available())

    def change_live2d_model(self, char_index):
        if len(self.character_names) <= char_index:
            return  # 安全检查，避免索引越界

        folder_path = ""
        for one in self.all_characters:
            if one.character_name == self.character_names[char_index]:
                folder_path = one.character_folder_name
                break

        if folder_path != "":
            dialog = ChangeL2DModelWindow(
                folder_path,
                lambda option, chat_id=self.parent_gui.current_chat.chat_id, form=self.parent_gui.current_chat.get_character_form(folder_path): self.parent_gui._on_live2d_model_changed(
                    char_index, self.character_names[char_index], option, chat_id=chat_id, form=form),
                form=self.parent_gui.current_chat.get_character_form(folder_path),
            )
            dialog.exec()

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

    def change_l2d_fps(self):
        if not self.parent_gui:
            return
        self.parent_gui.switch_l2d_fps()
        self.btn_live2d_fps.setText(f"Live2D渲染帧率：{self.parent_gui.get_current_l2d_fps()}")

    def change_motion_facing_mode(self) -> None:
        """切换小剧场 Live2D 动作朝向模式。"""
        if not self.parent_gui:
            return
        self.parent_gui.toggle_motion_facing_mode()
        self.btn_motion_facing.setText(self.parent_gui.motion_facing_mode_button_text())


class ViewerGUI(QWidget):
    def __init__(self,
                 characters,

                 qt2dp_queue,
                 message_queue,
                 dp2qt_queue,
                 audio_gen_module,
                 to_live2d_module_queue,
                 to_live2d_change_character_queue,
                 tell_qt_this_turn_finish_queue,
                 playback_idle_value=None,
                 ):
        """构建小剧场控制台、消息通道及共享舞台编排入口。"""
        super().__init__()
        self.playback_idle_value = playback_idle_value

        self.setWindowTitle("数字小祥 小剧场")
        self.setWindowIcon(QIcon("../live2d_related/sakiko/sakiko_icon.png"))
        self.screen = QDesktopWidget().screenGeometry()
        self.scr_w = self.screen.width()
        self.scr_h = self.screen.height()
        self.resize(int(0.4 * self.screen.width()), int(0.7 * self.screen.height()))
        self.l2d_fps_dict = {
            "current_fps": 1,
            "all_fps": [30, 60, 120],
        }

        self.chat_display = InteractiveChatBrowser()
        self.chat_display.setPlaceholderText("这里显示对话记录...")
        self.chat_display.setOpenExternalLinks(False)  # 禁止自动打开浏览器
        self.chat_display.setOpenLinks(False)
        self.chat_display.anchorClicked.connect(self.on_link_clicked)  # 绑定点击信号
        self.chat_display.edit_signal.connect(self.open_editor) #右键
        self.chat_display.regenerate_signal.connect(self.regenerate_audio)
        self.chat_display.performance_signal.connect(self.open_performance_editor)
        self.performance_editor = None
        self._audio_regeneration = None
        self._generation_context = None

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
            """创建沿用顶部工具栏尺寸和悬停样式的图标入口。"""
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
        self.play_bgm_btn.setIcon(QIcon('./icons/music.png'))
        self.btn_save = create_tool_btn( "保存对话记录", self.save_history_data)
        self.btn_save.setIcon(QIcon('./icons/save.png'))
        self.btn_settings = create_tool_btn( "设置", self.open_settings_dialog)
        self.btn_settings.setIcon(QIcon('./icons/setting.png'))
        self.btn_toggle_chat_panel = create_tool_btn("收起对话列表", self.toggle_chat_manage_panel)
        self.btn_toggle_chat_panel.setIcon(QIcon('./icons/chat_list.png'))
        self.btn_close = create_tool_btn( "退出小剧场", self.close_program)
        self.btn_close.setIcon(QIcon('./icons/exit.png'))

        # 布局：左边是消息框，中间弹簧，右边是三个按钮
        self.top_layout.addWidget(self.messages_box,1)
        self.top_layout.addWidget(self.play_bgm_btn,0)
        self.top_layout.addWidget(self.btn_save,0)
        self.top_layout.addWidget(self.btn_settings,0)
        self.top_layout.addWidget(self.btn_toggle_chat_panel,0)
        self.top_layout.addWidget(self.btn_close,0)

        # === 3. 底部大按钮 (Generate Button) ===
        self.replay_full_btn = QPushButton("回放当前对话")
        self.replay_full_btn.setCursor(Qt.PointingHandCursor)
        self.replay_full_btn.clicked.connect(self.replay_current_chat)

        self.edit_script_btn = QPushButton("进入剧本编排")
        set_button_icon(self.edit_script_btn, "clapperboard", self)
        self.edit_script_btn.setCursor(Qt.PointingHandCursor)
        self.edit_script_btn.clicked.connect(lambda: self.open_performance_editor(0))

        self.generate_btn = QPushButton("▶ 生成新对话")
        self.generate_btn.setCursor(Qt.PointingHandCursor)
        self.generate_btn.clicked.connect(self.start_generation)

        # 【关键步骤】设置 ObjectName，对应 CSS 中的 #PrimaryBtn
        self.generate_btn.setObjectName("PrimaryBtn")

        # 动态计算大按钮高度
        gen_btn_height = int(self.scr_h * 0.03)
        self.replay_full_btn.setMinimumHeight(gen_btn_height)
        self.edit_script_btn.setMinimumHeight(gen_btn_height)
        self.generate_btn.setMinimumHeight(gen_btn_height)

        bottom_action_layout = QHBoxLayout()
        bottom_action_layout.setSpacing(int(self.scr_w * 0.01))
        bottom_action_layout.addWidget(self.replay_full_btn, 1)
        bottom_action_layout.addWidget(self.edit_script_btn, 1)
        bottom_action_layout.addWidget(self.generate_btn, 1)

        # 不需要再手动 setStyleSheet 了！
        # 因为上面的 CSS 里的 #PrimaryBtn 已经接管了一切
        # 字体大小可以通过 setFont 设置，或者依然保留一小段 font-size 的 style

        # === 4. 整体布局（右侧对话管理） ===
        content_layout = QVBoxLayout()
        content_layout.addLayout(self.top_layout)
        content_layout.addWidget(self.chat_display)
        content_layout.addSpacing(int(self.scr_h * 0.01))
        content_layout.addLayout(bottom_action_layout)

        self.btn_new_chat = QPushButton("+ 新建对话")
        self.btn_new_chat.setObjectName("PrimaryBtn")
        self.btn_new_chat.clicked.connect(self.create_new_chat)

        self.btn_delete_chat = QPushButton("删除对话")
        self.btn_delete_chat.clicked.connect(self.delete_current_chat)

        self.chat_list = DragDropListWidget()
        self.chat_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self.show_chat_list_menu)
        self.chat_list.itemClicked.connect(self.on_chat_item_clicked)
        self.chat_list.order_changed.connect(self.on_chat_list_order_changed)

        self.chat_manage_panel = QGroupBox("对话列表")
        chat_manage_layout = QVBoxLayout()
        chat_manage_layout.addWidget(self.btn_new_chat)
        chat_manage_layout.addWidget(self.btn_delete_chat)
        chat_manage_layout.addWidget(self.chat_list, 1)
        self.chat_manage_panel.setLayout(chat_manage_layout)
        self.chat_manage_panel.setMinimumWidth(int(self.scr_w * 0.18))
        self.chat_manage_panel.setMaximumWidth(int(self.scr_w * 0.24))

        root_layout = QHBoxLayout()
        root_layout.addLayout(content_layout, 4)
        root_layout.addWidget(self.chat_manage_panel, 2)

        margin = int(self.scr_w * 0.015)
        root_layout.setContentsMargins(margin, margin, margin, margin)

        self.setLayout(root_layout)

        self.setStyleSheet(fluent_css)

        self.qt2dp_queue = qt2dp_queue
        self.message_queue = message_queue
        self.dp2qt_queue = dp2qt_queue
        self.audio_gen_module = audio_gen_module    #这个没办法，只能把整个实例传进来
        self.audio_gen_module.speed=0.94
        self.audio_gen_module.pause_second=0.6
        self.audio_gen_module.if_small_theater_mode=True
        self.to_live2d_module_queue=to_live2d_module_queue
        self.to_live2d_change_character_queue=to_live2d_change_character_queue
        self.tell_qt_this_turn_finish_queue=tell_qt_this_turn_finish_queue
        self.display_text_thread = DisplayTalkText(self.tell_qt_this_turn_finish_queue)
        self.display_text_thread.talk_text_signal.connect(self.display_this_turn_text)
        self.display_text_thread.stage_closed.connect(self.on_stage_closed)
        self.display_text_thread.start()
        self.mes_thread=CommunicateThreadMessages(self.message_queue)
        self.mes_thread.message_signal.connect(self.handle_messages)
        self.mes_thread.start()
        self.response_thread=CommunicateThreadDP2QT(self.dp2qt_queue)
        self.response_thread.response_signal.connect(self.handle_response)
        self.response_thread.start()

        self.character_list = characters
        self.current_char_index = [0, 1]
        self.original_response = []
        self.char_0_talk_texts = []
        self.char_0_audio_path_list = []
        self.char_talk_texts_match_original_response_indices = []
        self.char_1_talk_texts = []
        self.char_1_audio_path_list = []
        self.chat_manager: ChatManager = get_chat_manager(write_scope=ChatType.SMALL_THEATER)
        self.chat_manager.storage_notice = self.message_queue.put
        for notice in self.chat_manager.storage_notices:
            self.message_queue.put(notice)
        self.current_chat: Optional[Chat] = None
        self.sakiko_state=True  #黑祥
        # 当前对话是否可以生成音频（即当前对话中，两个角色是否都有语音模型）
        self.can_generate_audio_for_current_chat = True
        self.pending_turn_uids: set[str] = set()
        self._generation_serial = 0
        self.settings_dialog: Optional[SettingsDialog] = None
        self.ensure_theater_chat_exists()
        if self.current_chat is not None:
            self.sakiko_state = self.current_chat.get_character_form() == "black"
        self.refresh_chat_list()

        self.two_char_names=[]
        self.set_two_char_names()
        self.messages_box.setText(f"当前角色：{self.two_char_names[0]} 和 {self.two_char_names[1]} ")
        self.display_timer = QTimer()
        self.display_timer.timeout.connect(self.display_timer_timeout)

        # === BGM 播放模块 ===
        self.bgm_player = QMediaPlayer()
        self.bgm_playlist = QMediaPlaylist()
        self.bgm_playlist.setPlaybackMode(QMediaPlaylist.CurrentItemInLoop)
        self.bgm_player.setPlaylist(self.bgm_playlist)
        self.bgm_player.setVolume(30)  # 音量 0-100 (Pygame是0.0-1.0，注意区别)
        self.bgm_player.error.connect(self._handle_bgm_error)
        self.bgm_player.mediaStatusChanged.connect(self._handle_bgm_media_status)
        self._current_bgm_path: Optional[str] = None
        self._recovering_bgm_error = False
        initial_bgm = d_sakiko_config.multi_char_background_music_path.value
        if not self._load_bgm(initial_bgm):
            self._recover_bgm_after_failure(initial_bgm, "文件不存在或无法载入")

        self.update_chat_panel_toggle_button()
        self.update_replay_button_visibility()

    def update_chat_panel_toggle_button(self) -> None:
        """同步更新右侧面板显隐按钮的图标与提示文案。"""
        if self.chat_manage_panel.isVisible():
            self.btn_toggle_chat_panel.setToolTip("收起对话列表")
        else:
            self.btn_toggle_chat_panel.setToolTip("展开对话列表")

    def toggle_chat_manage_panel(self) -> None:
        """切换右侧对话列表面板的显示状态。"""
        self.chat_manage_panel.setVisible(not self.chat_manage_panel.isVisible())
        self.update_chat_panel_toggle_button()

    def update_replay_button_visibility(self) -> None:
        """当前对话有剧本时显示回放与编排入口，空剧本时同时隐藏。"""
        has_messages = self.current_chat is not None and len(self.current_chat.message_list) > 0
        self.replay_full_btn.setVisible(has_messages)
        self.edit_script_btn.setVisible(has_messages)

    def replay_current_chat(self) -> None:
        """回放当前对话的全部消息。"""
        if self.current_chat is None or not self.current_chat.message_list:
            return

        replay_payload = [self._turn_dict_from_message(msg) for msg in self.current_chat.message_list]
        self.sync_live2d_active_slots()
        self.display_live2d_message(replay_payload, preserve_playback=False)

    def theater_chats(self) -> List[Chat]:
        """返回全部小剧场模式对话。"""
        return [chat for chat in self.chat_manager.chat_list if chat.type == ChatType.SMALL_THEATER]

    def ensure_theater_chat_exists(self) -> None:
        """确保至少存在一个小剧场对话，没有则创建默认对话。"""
        chats = self.theater_chats()
        if chats:
            self.switch_chat(chats[0])
            return

        char_0 = self.character_list[self.current_char_index[0]].character_name
        char_1 = self.character_list[self.current_char_index[1]].character_name
        new_chat = Chat(
            name=f"{char_0}与{char_1}的对话",
            prompt_generator=SmallTheaterPromptGenerator([char_0, char_1]),
            type_=ChatType.SMALL_THEATER,
            start_message="",
            message_list=[]
        )
        self.chat_manager.chat_list.append(new_chat)
        self.switch_chat(new_chat)

    def _preview_text(self, chat: Chat) -> str:
        """
        生成一个对话的预览内容，截取最后一条消息的文本，限制在24字符以内，并去掉换行符。
        如果没有消息，则显示"(暂无消息)"。
        """
        if not chat.message_list:
            return "(暂无消息)"
        last_message = chat.message_list[-1]
        if last_message.translation:
            text = last_message.translation.strip().replace("\n", " ")
        else:
            text = last_message.text.strip().replace("\n", " ")
        return text[:24] + ("..." if len(text) > 24 else "")

    def refresh_chat_list(self) -> None:
        """
        刷新对话列表显示，保持当前对话高亮。每个列表项显示对话名称和最后一条消息的预览。
        """
        chats = self.theater_chats()
        # 在生成列表项时暂时阻止信号，以免触发 on_chat_item_clicked 导致不必要的切换
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        active_row = 0

        for row, chat in enumerate(chats):
            title = chat.name
            preview = self._preview_text(chat)
            item = QListWidgetItem(f"{title}\n{preview}")
            # 存储 chat 对象在 item 的 UserRole 中，方便点击时获取
            item.setData(Qt.UserRole, chat)
            item.setSizeHint(QSize(220, 52))
            self.chat_list.addItem(item)
            # 如果当前对话是这个 chat，就记录它的行号，稍后设置为当前行
            if self.current_chat is chat:
                active_row = row

        if chats:
            self.chat_list.setCurrentRow(active_row)

        self.chat_list.blockSignals(False)

    def on_chat_item_clicked(self, item: QListWidgetItem) -> None:
        """
        在对话列表中点击一个对话时，切换到那个对话。
        """
        # 从 item 的 UserRole 中获取对应的 chat 对象
        chat = item.data(Qt.UserRole)
        if chat is None:
            return
        self.switch_chat(chat)

    def on_chat_list_order_changed(self) -> None:
        """
        当用户拖拽对话列表改变顺序时，更新 ChatManager 中 chat_list 的顺序以保持一致。
        """
        ordered_chat_ids = []
        for i in range(self.chat_list.count()):
            item = self.chat_list.item(i)
            if isinstance(item, QListWidgetItem):
                chat = item.data(Qt.UserRole)
                if chat is not None:
                    ordered_chat_ids.append(chat.chat_id)

        self.chat_manager.reorder_chats_by_type(ChatType.SMALL_THEATER, ordered_chat_ids)
        if not self._save_chat():
            return

    def show_chat_list_menu(self, pos) -> None:
        """
        在对话列表项上右键时显示菜单，提供重命名和删除选项。
        """
        item = self.chat_list.itemAt(pos)
        if item is None:
            return

        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        delete_action = menu.addAction("删除")
        selected_action = menu.exec_(self.chat_list.mapToGlobal(pos))

        if selected_action == rename_action:
            self.rename_chat(item)
        elif selected_action == delete_action:
            self.chat_list.setCurrentItem(item)
            self.delete_current_chat()

    def rename_chat(self, item: QListWidgetItem) -> None:
        chat = item.data(Qt.UserRole)
        if chat is None:
            return

        dialog = QInputDialog(self)
        dialog.setWindowTitle("重命名对话")
        dialog.setLabelText("请输入新的对话名称：")
        dialog.setTextValue(chat.name)
        dialog.setOkButtonText("确定")
        dialog.setCancelButtonText("取消")

        if dialog.exec_() != QDialog.Accepted:
            return
        new_name = dialog.textValue().strip()
        if not new_name:
            QMessageBox.warning(self, "提示", "对话名称不能为空")
            return
        chat.name = new_name
        self.refresh_chat_list()
        self.messages_box.setText("已重命名当前对话")

    def create_new_chat(self) -> None:
        dialog = NewTheaterChatDialog(self.character_list, self)
        if dialog.exec_() != QDialog.Accepted:
            return

        payload = dialog.get_payload()
        new_chat = Chat(
            name=payload["name"],
            prompt_generator=SmallTheaterPromptGenerator(payload["characters"]),
            type_=ChatType.SMALL_THEATER,
            start_message=payload["scene"],
            message_list=[]
        )
        new_chat.update_theater_meta({"situation": payload["scene"]})
        self.chat_manager.chat_list.append(new_chat)
        self.switch_chat(new_chat)
        self.refresh_chat_list()
        self.messages_box.setText(f"已创建新对话：{payload['name']}")
        # 在创建/删除/改变对话顺序后，都自动保存一次
        if not self._save_chat():
            return

    def delete_current_chat(self) -> None:
        if self.current_chat is None:
            return

        box = QMessageBox(QMessageBox.Question, "删除确认", f"确定删除对话“{self.current_chat.name}”吗？", QMessageBox.No | QMessageBox.Yes, self)
        box.setEscapeButton(QMessageBox.No)
        box.button(QMessageBox.Yes).setText("确定")
        box.button(QMessageBox.No).setText("取消")
        reply = box.exec_()
        if reply != QMessageBox.Yes:
            return

        try:
            self.chat_manager.chat_list.remove(self.current_chat)
        except ValueError:
            return

        chats = self.theater_chats()
        if not chats:
            self.current_chat = None
            self.ensure_theater_chat_exists()
        else:
            self.switch_chat(chats[0])

        self.refresh_chat_list()
        self.messages_box.setText("已删除对话")
        if not self._save_chat():
            return

    @staticmethod
    def _turn_dict_from_message(msg: Message) -> Dict[str, Any]:
        """将 Message 转换为通信字典格式（与 Message.as_dict 一致）。"""
        return msg.as_dict()

    @staticmethod
    def _message_from_turn_dict(turn_data: Dict[str, Any]) -> Message:
        """恢复完整台词数据，运行时场景上下文不进入消息存档。"""
        return Message.from_dict({
            "character_name": turn_data.get("character_name", ""),
            "text": turn_data.get("text", ""),
            "translation": turn_data.get("translation", ""),
            "emotion": turn_data.get("emotion", "normal"),
            "audio_path": turn_data.get("audio_path", ""),
            "performance": turn_data.get("performance"),
            "performance_timeline": turn_data.get("performance_timeline"),
            "scene_before": turn_data.get("scene_before"),
            "muted": turn_data.get("muted", False),
            "attachments": turn_data.get("attachments", []),
            "worldbook_snapshot": turn_data.get("worldbook_snapshot"),
        })

    def _character_indices_from_names(self, character_names: List[str]) -> List[int]:
        """按角色名匹配角色索引，若不足两名则使用当前索引兜底。"""
        indices = []
        for name in character_names:
            for idx, char in enumerate(self.character_list):
                if char.character_name == name:
                    indices.append(idx)
                    break
            if len(indices) == 2:
                break

        if len(indices) < 2:
            for fallback in self.current_char_index:
                if fallback not in indices:
                    indices.append(fallback)
                if len(indices) == 2:
                    break
        return indices[:2]

    def _chat_character_names(self, chat: Chat) -> List[str]:
        """优先从 Prompt 配置读取对话角色名，若缺失则回退到消息中提取。"""
        if isinstance(chat.prompt_generator, SmallTheaterPromptGenerator):
            names = [name for name in chat.prompt_generator.character_names if name and name != "User"]
            if len(names) >= 2:
                return names[:2]

        names = [name for name in chat.involved_characters if name and name != "User"]
        return names[:2]

    def _ensure_current_chat_theater_meta(self) -> TheaterMeta:
        """确保当前对话包含可用的小剧场配置结构。"""
        if self.current_chat is None:
            return TheaterMeta()
        return self.current_chat.get_theater_meta()

    def _build_dp_user_input(self) -> Dict[str, Any]:
        """从当前 Chat 的 theater meta 组装给 dp 模块的输入数据。"""
        return copy.deepcopy(self._ensure_current_chat_theater_meta().to_dict())

    def _character_name_from_index(self, char_index: int) -> str:
        """根据角色索引获取角色名，越界时返回兜底值。"""
        if 0 <= char_index < len(self.character_list):
            return self.character_list[char_index].character_name
        return "未知角色"

    def _find_character_by_name(self, character_name: str):
        """按非空角色名精确匹配角色；无角色消息不绑定占位声线。"""
        if not character_name:
            return None
        for char in self.character_list:
            if char.character_name == character_name:
                return char
        return None

    def _can_generate_audio_for_chat_characters(self, character_names: List[str]) -> bool:
        """判断当前对话角色是否具备完整可用的语音模型。"""
        for name in character_names:
            char = self._find_character_by_name(name)
            if char is None:
                return False
            if not char.has_valid_voice_model():
                return False
        return True

    def _default_model_path_from_index(self, char_index: int) -> Optional[str]:
        """根据角色索引获取可选默认 Live2D 模型路径。"""
        if 0 <= char_index < len(self.character_list):
            return self.character_list[char_index].live2d_json
        return None

    def _prepare_live2d_model_for_switch(self, model_path: str, action_label: str) -> bool:
        """在小剧场切换或加载 Live2D 模型前完成项目规范化。"""
        result = normalize_live2d_model_for_project(model_path)
        if not result.ok:
            self.message_queue.put(f"{action_label}失败，检查 Live2D 配置文件时出错。")
            logger.error(
                "%s失败，检查 Live2D 配置文件时出错：%s；%s",
                action_label,
                model_path,
                result.error_message,
            )
            return False
        if result.converted_old_model:
            logger.info("成功转换旧版 Live2D 配置文件：%s", model_path)
        if result.normalized_model3:
            self.message_queue.put("已自动修复 Live2D V3 模型动作组配置")
            logger.info("已自动修复 Live2D V3 模型动作组配置：%s", model_path)
        return True

    def _next_turn_uid(self, turn_index: int) -> str:
        """为本轮生成的每一句对话分配唯一序号，用于去重。"""
        return f"{self._generation_serial}:{turn_index}"

    @staticmethod
    def _message_signature_from_dict(turn_data: Dict[str, Any]) -> Tuple[Any, ...]:
        """用于回放场景去重的稳定签名。"""
        return (
            turn_data.get("character_name", ""),
            turn_data.get("text", ""),
            turn_data.get("translation", ""),
            turn_data.get("emotion", ""),
            turn_data.get("audio_path", ""),
        )

    def _safe_get_audio_path(self, audio_list: List[str], pointer: int) -> Optional[str]:
        """安全读取语音路径，越界时返回 None。"""
        if 0 <= pointer < len(audio_list):
            return audio_list[pointer]
        return None

    def _build_playlist_queue(self, indices=None) -> List[Dict[str, Any]]:
        """将模型返回的原始对话与音频结果组装为播放列表。"""
        indices = self.current_char_index if indices is None else indices
        char_0_text_pointer = 0
        char_1_text_pointer = 0
        playlist_queue: List[Dict[str, Any]] = []

        for i, turn in enumerate(self.original_response):
            emotion = turn.get("emotion", "normal")
            translation = turn.get("translation", "")
            turn_uid = self._next_turn_uid(i)

            if self.char_talk_texts_match_original_response_indices[i] == 'char_0':
                text = self.char_0_talk_texts[char_0_text_pointer]
                audio_path = self._safe_get_audio_path(self.char_0_audio_path_list, char_0_text_pointer)
                char_0_text_pointer += 1
                char_name = self.character_list[indices[0]].character_name
            elif self.char_talk_texts_match_original_response_indices[i] == 'char_1':
                text = self.char_1_talk_texts[char_1_text_pointer]
                audio_path = self._safe_get_audio_path(self.char_1_audio_path_list, char_1_text_pointer)
                char_1_text_pointer += 1
                char_name = self.character_list[indices[1]].character_name
            else:
                text = turn.get('text', '')
                audio_path = None
                char_name = turn.get('speaker', '未知角色')

            one_turn = Message.from_dict({
                "character_name": char_name,
                "text": text,
                "translation": translation,
                "emotion": emotion,
                "audio_path": audio_path,
                "performance": turn.get("performance"),
            }).as_dict()
            one_turn["turn_uid"] = turn_uid
            playlist_queue.append(one_turn)

        return playlist_queue

    def _build_active_slots_payload(
            self,
            indices: list[int] | None = None,
            override_paths: dict[int, str] | None = None,
            preserve_playback: bool = False,
            changed_slot: int | None = None,
    ) -> dict[str, object]:
        """构建发送给 Live2D 子进程的 `set_active_slots` payload。

        模型路径优先级：
        1) `override_paths`（本次显式指定）
        2) 当前对话里保存的自定义模型路径
        3) 角色默认模型路径
        """
        active_indices = indices if indices is not None else self.current_char_index
        if len(active_indices) < 2:
            raise ValueError("至少需要两个角色索引来构造 active_slots")

        override_paths = override_paths or {}
        slots: list[dict[str, object]] = []
        model_paths: list[str] = []

        for slot in (0, 1):
            char_index = active_indices[slot]
            char_name = self._character_name_from_index(char_index)

            # 允许仅覆盖某一个槽位，未覆盖槽位按“对话自定义 -> 默认模型”回退到已有的值
            model_path: str | None = override_paths.get(slot)
            if slot not in override_paths and self.current_chat is not None:
                model_path = self.current_chat.get_custom_live2d_model_meta(char_name)
            if slot not in override_paths and self.current_chat is None:
                if self.character_list[char_index].character_folder_name == "sakiko":
                    from live2d_support.character_forms import default_form_model
                    model_path = default_form_model("black" if self.sakiko_state else "white")
                else:
                    model_path = self._default_model_path_from_index(char_index)

            slots.append({
                "slot": slot,
                "character_name": char_name,
                "character_folder_name": self.character_list[char_index].character_folder_name,
                "model_json_path": model_path,
                "sakiko_state": self.sakiko_state,
            })
            if model_path is not None:
                model_paths.append(model_path)

        motion_facing_mode = str(d_sakiko_config.multi_char_motion_facing_mode.value)
        if motion_facing_mode == "face_to_face" and not self._model_paths_support_motion_facing(model_paths):
            motion_facing_mode = "screen"
        payload: dict[str, object] = {
            "type": "set_active_slots",
            "slots": slots,
            "motion_facing_mode": motion_facing_mode,
            # True: 仅切模型，不打断当前对话播放；False: 切模型时可中断当前播放
            "preserve_playback": preserve_playback,
        }
        if changed_slot in (0, 1):
            payload["changed_slot"] = changed_slot
        return payload

    def _refresh_open_settings_dialog(self) -> None:
        """刷新当前打开的小剧场设置面板中依赖模型能力的控件。"""
        if self.settings_dialog is None:
            return
        self.settings_dialog.refresh_motion_facing_button()

    def sync_live2d_active_slots(
            self,
            indices: Optional[List[int]] = None,
            override_paths: Optional[Dict[int, str]] = None,
            preserve_playback: bool = False,
            changed_slot: Optional[int] = None,
    ) -> None:
        """将双模型状态同步到 Live2D 子进程。"""
        payload = self._build_active_slots_payload(
            indices=indices,
            override_paths=override_paths,
            preserve_playback=preserve_playback,
            changed_slot=changed_slot,
        )
        for slot in payload["slots"]:
            path = slot.get("model_json_path")
            if path:
                self._prepare_live2d_model_for_switch(path, "加载小剧场 Live2D 模型")
        self.to_live2d_change_character_queue.put(payload)
        self._refresh_open_settings_dialog()

    def display_live2d_message(self, msg: list[dict], preserve_playback: bool = False) -> None:
        """
        将数条消息发送 Live2D 子进程播放。

        :param msg: 每条消息包含角色名称、文本内容、表情等信息的字典列表。
        :param preserve_playback: 设置为 True 时，传入的内容会进入队列排队，在前面已有内容播放完后再播放；
        设置为 False 时，当前正在播放的内容播放完一整句后会被立刻打断，立即播放传入的内容。（此时队列缓存的待播放内容也会被清空）
        """
        if self.playback_idle_value is not None and msg:
            self.playback_idle_value.value = False
        payload = {
            "playlist": msg,
            "preserve_playback": preserve_playback,
        }
        self.to_live2d_module_queue.put(payload)

    def _on_live2d_model_changed(
        self,
        char_index: int,
        character_name: str,
        option: Live2DModelOption,
        *, chat_id=None, form=None,
    ) -> None:
        """
        接收 SettingsDialog 中角色模型更换的回调，并通知 Live2D 模块更新指定角色的模型。
        随后，将新的模型路径保存到对话信息中。
        """
        folder = self.character_list[self.current_char_index[char_index]].character_folder_name
        if self.current_chat is None or character_name != self.character_list[self.current_char_index[char_index]].character_name or (chat_id is not None and (chat_id != self.current_chat.chat_id or form != self.current_chat.get_character_form(folder))):
            self.message_queue.put("对话或形态已变化，请重新打开模型窗口。")
            return
        from live2d_support.character_forms import explicit_model, set_model_override
        previous = explicit_model(self.current_chat.meta, character_name, folder, form)
        model_path = str(option.model_json_path)
        if not self._prepare_live2d_model_for_switch(model_path, "切换小剧场 Live2D 模型"):
            return
        if self.current_chat is not None:
            if option.is_default:
                self.current_chat.clear_custom_live2d_model_meta(character_name)
            else:
                self.current_chat.update_custom_live2d_model_meta(character_name, model_path)
            if not self._save_chat():
                set_model_override(self.current_chat.meta, character_name, folder, previous, form)
                return
        # 设置面板中的“切同角色不同模型”不应中断正在播放的句子
        self.sync_live2d_active_slots(
            override_paths={char_index: model_path},
            preserve_playback=True,
            changed_slot=char_index,
        )

    @staticmethod
    def render_message_html(index: int, msg: Message) -> Tuple[str, str]:
        """渲染可回放的原文与翻译；无角色台词直接显示文本。"""
        name_prefix = f"★{escape(msg.character_name)}：" if msg.character_name else ""
        original = escape(msg.text).replace("\n", "<br>")
        text_html = (
            f'<a href="replay:{index}" style="text-decoration: none; color: #7799CC;">'
            f'{name_prefix}{original}'
            "</a>"
        )
        translation = escape(msg.translation).replace("\n", "<br>")
        translation_html = (
            f'<span style="color: #B3D1F2; font-style: italic;">{translation}</span><br>'
        ) if translation else ""
        return text_html, translation_html

    def switch_chat(self, chat: Chat) -> None:
        """切换当前对话，并同步文本渲染与 Live2D 角色。"""
        self.current_chat = chat
        self.sakiko_state = chat.get_character_form() == "black"
        self._ensure_current_chat_theater_meta()

        char_names = self._chat_character_names(chat)

        self.current_char_index = self._character_indices_from_names(char_names)
        self.refresh_chat_display()
        self.refresh_chat_list()

        self.can_generate_audio_for_current_chat = self._can_generate_audio_for_chat_characters(char_names)
        if 'dp_module' in globals():
            setattr(dp_module, "if_generate_audio", self.can_generate_audio_for_current_chat)

        if len(self.current_char_index) == 2:
            self.sync_live2d_active_slots(indices=self.current_char_index)
        self.set_two_char_names()
        self.messages_box.setText(f"当前对话：{chat.name}")

        self.update_replay_button_visibility()

    def display_timer_timeout(self):
        self.set_two_char_names()
        self.messages_box.setText(f"当前角色：{self.two_char_names[0]} 和 {self.two_char_names[1]}")

    def open_settings_dialog(self):
        """打开设置面板"""
        if self.settings_dialog is not None and self.settings_dialog.isVisible():
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            self.settings_dialog.refresh_motion_facing_button()
            return

        if self.current_chat is None:
            character_names = []
        else:
            character_names = self._chat_character_names(self.current_chat)
        self.settings_dialog = SettingsDialog(self, character_names, self.screen, self.audio_gen_module)
        self.settings_dialog.finished.connect(self._on_settings_dialog_finished)
        self.settings_dialog.show()

    def _on_settings_dialog_finished(self, _result: int) -> None:
        """在设置面板关闭后清理窗口引用。"""
        self.settings_dialog = None

    def toggle_bgm(self) -> None:
        """切换背景音乐的播放和暂停状态。"""
        if self.bgm_player.state()==QMediaPlayer.PlayingState:
            self.bgm_player.pause()
        else:
            self.bgm_player.play()


    def handle_messages(self,msg):
        self.messages_box.setText(msg)
        if (msg not in ['调用大模型生成文本中...']) and ('整理语言' not in msg):
            try:
                self.display_timer.setSingleShot(True)
                self.display_timer.start(2500)
            except Exception as e:
                self.display_timer=QTimer()
                self.display_timer.setSingleShot(True)
                self.display_timer.start(2500)


    def _handle_bgm_error(self, error: QMediaPlayer.Error) -> None:
        """处理 Qt 媒体后端报告的背景音乐播放错误。"""
        if error == QMediaPlayer.NoError:
            return

        message = self.bgm_player.errorString() or f"错误代码 {error}"
        logger.error(
            "背景音乐播放失败：error=%s, message=%s",
            error,
            message,
        )
        failed_path = (
            self._current_bgm_path
            or d_sakiko_config.multi_char_background_music_path.value
        )
        self._recover_bgm_after_failure(failed_path, message)

    def _handle_bgm_media_status(
        self, status: QMediaPlayer.MediaStatus
    ) -> None:
        """处理 Qt 异步报告的无效背景音乐状态。"""
        if status != QMediaPlayer.InvalidMedia:
            return

        message = self.bgm_player.errorString() or "媒体无效或无法解码"
        logger.error("背景音乐媒体无效：%s", message)
        failed_path = (
            self._current_bgm_path
            or d_sakiko_config.multi_char_background_music_path.value
        )
        self._recover_bgm_after_failure(failed_path, message)

    def _load_bgm(self, bgm_path: str) -> bool:
        """将背景音乐载入现有播放列表，并显式选中新增条目。"""
        absolute_path = os.path.abspath(bgm_path)
        if not os.path.isfile(absolute_path):
            logger.error("背景音乐文件不存在：%s", absolute_path)
            return False

        self.bgm_player.stop()
        self.bgm_playlist.clear()
        url = QUrl.fromLocalFile(absolute_path)
        if not self.bgm_playlist.addMedia(QMediaContent(url)):
            logger.error(
                "背景音乐无法加入播放列表：path=%s, message=%s",
                absolute_path,
                self.bgm_playlist.errorString(),
            )
            self._current_bgm_path = None
            return False

        # clear() 会把 currentIndex 重置为 -1，addMedia() 不会自动恢复。
        self.bgm_playlist.setCurrentIndex(0)
        self._current_bgm_path = absolute_path
        return True

    def _candidate_bgm_paths(
        self,
        failed_path: str,
        preferred_path: Optional[str] = None,
    ) -> List[str]:
        """按优先级列出可用于回退的背景音乐候选路径。"""
        failed_absolute_path = os.path.abspath(failed_path)
        candidates: List[str] = []

        for candidate in (preferred_path, DEFAULT_MULTI_CHAR_BGM_PATH):
            if candidate is None:
                continue
            candidate_path = os.path.abspath(candidate)
            if (
                candidate_path != failed_absolute_path
                and candidate_path not in candidates
            ):
                candidates.append(candidate_path)

        try:
            bgm_files = sorted(
                (
                    path.resolve()
                    for path in BGM_DIRECTORY.iterdir()
                    if path.is_file()
                    and path.suffix.lower() in SUPPORTED_BGM_SUFFIXES
                ),
                key=lambda path: path.name.casefold(),
            )
        except OSError:
            logger.exception("背景音乐文件夹读取失败：%s", BGM_DIRECTORY)
            bgm_files = []

        for bgm_file in bgm_files:
            candidate_path = os.path.abspath(str(bgm_file))
            if (
                candidate_path != failed_absolute_path
                and candidate_path not in candidates
            ):
                candidates.append(candidate_path)

        return candidates

    def _show_bgm_failure_message(self, message: str) -> None:
        """在界面上提示背景音乐失败原因和处理结果。"""
        QTimer.singleShot(
            0,
            lambda: QMessageBox.warning(self, "背景音乐播放失败", message),
        )

    def _recover_bgm_after_failure(
        self,
        failed_path: str,
        reason: str,
        preferred_fallback: Optional[str] = None,
    ) -> bool:
        """当前背景音乐失效时自动切换到可播放的备用音乐。"""
        if self._recovering_bgm_error:
            return False

        self._recovering_bgm_error = True
        was_playing = self.bgm_player.state() == QMediaPlayer.PlayingState
        self.bgm_player.stop()

        try:
            for candidate_path in self._candidate_bgm_paths(
                failed_path,
                preferred_fallback,
            ):
                validation_error = validate_bgm_media_file(Path(candidate_path))
                if validation_error is not None:
                    logger.error(
                        "备用背景音乐不可用：path=%s, reason=%s",
                        candidate_path,
                        validation_error,
                    )
                    continue

                if not self._load_bgm(candidate_path):
                    continue

                d_sakiko_config.set(
                    d_sakiko_config.multi_char_background_music_path,
                    candidate_path,
                )
                if was_playing:
                    self.bgm_player.play()
                self._show_bgm_failure_message(
                    "当前背景音乐播放失败："
                    f"{reason}\n已自动切换到：{os.path.basename(candidate_path)}"
                )
                return True

            self.bgm_playlist.clear()
            self._current_bgm_path = None
            self._show_bgm_failure_message(
                f"当前背景音乐播放失败：{reason}\n没有找到可用的备用背景音乐。"
            )
            return False
        finally:
            self._recovering_bgm_error = False

    def set_bgm(self) -> None:
        """打开背景音乐选择窗口，并在关闭后应用新的音乐。"""
        old_bgm = d_sakiko_config.multi_char_background_music_path.value
        was_playing = self.bgm_player.state() == QMediaPlayer.PlayingState
        if was_playing:
            self.bgm_player.pause()

        dialog = CustomBGMDialog(parent=self)
        dialog.exec_()
        new_bgm = d_sakiko_config.multi_char_background_music_path.value

        if new_bgm != old_bgm:
            loaded = self._load_bgm(new_bgm)
            recovered_from_failure = False
            if not loaded:
                logger.error("新背景音乐载入失败，尝试恢复原背景音乐：%s", old_bgm)
                loaded = self._recover_bgm_after_failure(
                    new_bgm,
                    "文件不存在或无法载入",
                    old_bgm,
                )
                recovered_from_failure = loaded
            if loaded and was_playing and not recovered_from_failure:
                self.bgm_player.play()
        elif was_playing:
            self.bgm_player.play()

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
                if self.current_chat is not None and isinstance(self.current_chat.prompt_generator, SmallTheaterPromptGenerator):
                    self.current_chat.prompt_generator.character_names = [
                        self.character_list[self.current_char_index[0]].character_name,
                        self.character_list[self.current_char_index[1]].character_name,
                    ]
                self.sync_live2d_active_slots(indices=self.current_char_index)
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
        if self.current_chat is None or not any(self.character_list[index].character_folder_name == "sakiko" for index in self.current_char_index):
            self.message_queue.put("祥子好像不在…")
            return
        if not self.generate_btn.isEnabled() or self.pending_turn_uids or (self.playback_idle_value is not None and not self.playback_idle_value.value):
            self.message_queue.put("请等待生成和播放完成后切换形态。")
            return
        previous = self.current_chat.get_character_form()
        stored_form = self.current_chat.meta.character_forms.get("sakiko")
        self.current_chat.set_character_form("white" if previous == "black" else "black")
        if not self._save_chat():
            if stored_form is None:
                self.current_chat.meta.character_forms.pop("sakiko", None)
            else:
                self.current_chat.set_character_form(stored_form)
            return
        self.sakiko_state = self.current_chat.get_character_form() == "black"
        self.set_two_char_names()
        self.audio_gen_module.sakiko_which_state = self.sakiko_state
        self.sync_live2d_active_slots()
        self.message_queue.put(f"当前角色：{self.two_char_names[0]} 和 {self.two_char_names[1]}")

    def create_mask_menu(self):
        from live2d_support.mask_actions import mask_actions
        menu = QMenu(self)
        for key, label in (("on", "戴上面具"), ("off", "摘下面具")):
            action = menu.addAction(label)
            action.setData(key)
            action.triggered.connect(lambda checked=False, value=key: self.to_live2d_change_character_queue.put({"type": "mask_action", "action": value}))
        def refresh():
            present = any(self.character_list[index].character_folder_name == "sakiko" for index in self.current_char_index)
            bindings = mask_actions(self.current_chat.get_custom_live2d_model_meta("祥子")) if self.current_chat and present and self.sakiko_state else {}
            for action in menu.actions():
                action.setEnabled(action.data() in bindings and self.generate_btn.isEnabled() and not self.pending_turn_uids and (self.playback_idle_value is None or self.playback_idle_value.value))
        menu.aboutToShow.connect(refresh)
        return menu

    def switch_l2d_fps(self):
        self.l2d_fps_dict["current_fps"] = (
            self.l2d_fps_dict["current_fps"] + 1
        ) % len(self.l2d_fps_dict["all_fps"])
        self.to_live2d_change_character_queue.put({
            "type": "switch_l2d_fps",
            "fps": self.get_current_l2d_fps(),
        })

    def get_current_l2d_fps(self):
        return self.l2d_fps_dict["all_fps"][self.l2d_fps_dict["current_fps"]]

    def current_live2d_model_paths(self) -> list[str]:
        """返回当前小剧场两个槽位使用的 Live2D 模型路径。"""
        payload = self._build_active_slots_payload(preserve_playback=True)
        slots = payload.get("slots", [])
        if not isinstance(slots, list):
            return []
        model_paths: list[str] = []
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            model_path = slot.get("model_json_path", "")
            if isinstance(model_path, str) and model_path:
                model_paths.append(model_path)
        return model_paths

    def is_motion_facing_mode_available(self) -> bool:
        """判断当前小剧场模型组合是否支持动作朝向模式。"""
        model_paths = self.current_live2d_model_paths()
        return self._model_paths_support_motion_facing(model_paths)

    @staticmethod
    def _model_paths_support_motion_facing(model_paths: Sequence[str]) -> bool:
        """判断两个模型路径是否共同支持小剧场面对面动作朝向。"""
        if len(model_paths) != 2:
            return False
        try:
            if not all(detect_live2d_runtime_version(model_path) == "v3" for model_path in model_paths):
                return False
            left_capabilities = get_live2d_motion_capabilities(model_paths[0])
            right_capabilities = get_live2d_motion_capabilities(model_paths[1])
            return left_capabilities.supports_position("L") and right_capabilities.supports_position("R")
        except Exception:
            logger.debug("检测小剧场 Live2D 动作朝向能力失败，隐藏动作朝向设置", exc_info=True)
            return False

    def motion_facing_mode_button_text(self) -> str:
        """返回小剧场动作朝向按钮文案。"""
        mode = str(d_sakiko_config.multi_char_motion_facing_mode.value)
        label = "面对面" if mode == "face_to_face" else "正常"
        return f"对话朝向：{label}"

    def toggle_motion_facing_mode(self) -> None:
        """切换并保存小剧场动作朝向模式。"""
        current_mode = str(d_sakiko_config.multi_char_motion_facing_mode.value)
        next_mode = "screen" if current_mode == "face_to_face" else "face_to_face"
        d_sakiko_config.set(d_sakiko_config.multi_char_motion_facing_mode, next_mode)
        self.sync_live2d_active_slots(preserve_playback=True)


    def start_generation(self) -> None:
        """主页生成入口只打开设定副本，不清空剧本或发送请求。"""
        if self.current_chat is None or self._generation_context is not None:
            return
        chat = self.current_chat
        indices = list(self.current_char_index)
        meta = copy.deepcopy(chat.meta.theater.to_dict())
        if not meta.get("situation"):
            meta["situation"] = chat.start_message or ""
        dialog = MoreInfoDialog(
            self.character_list[indices[0]].character_name,
            self.character_list[indices[1]].character_name,
            meta, self, replacing=bool(chat.message_list),
        )
        dialog.info_committed.connect(lambda data: self.submit_generation(dialog, chat, indices, data))
        dialog.exec_()
        dialog.deleteLater()

    def submit_generation(self, dialog, chat, indices, data) -> None:
        """设定窗口唯一提交入口，保存设定后绑定原对话发送 LLM 请求。"""
        if self._generation_context is not None:
            dialog.commit_btn.setEnabled(True)
            return
        old_meta = copy.deepcopy(chat.meta.theater)
        old_start = chat.start_message
        chat.update_theater_meta(data)
        if not self._save_chat():
            chat.meta.theater = old_meta
            chat.start_message = old_start
            dialog.commit_btn.setEnabled(True)
            return
        self._generation_context = {
            "chat": chat, "indices": list(indices), "sakiko_state": self.sakiko_state,
            "audio": self.can_generate_audio_for_current_chat,
        }
        self.generate_btn.setEnabled(False)
        temporary_input = copy.deepcopy(data)
        # 这里必须用原始角色名判断，不能用 two_char_names（可能带“黑祥/白祥”后缀）。
        char_0_name = self.character_list[indices[0]].character_name
        char_1_name = self.character_list[indices[1]].character_name
        sakiko_suffix = "（此时祥子为黑祥）" if self.sakiko_state else "（此时祥子为白祥）"

        if char_0_name == "祥子":
            temporary_input["character_0"]["talk_style"] = (
                temporary_input["character_0"]["talk_style"] + sakiko_suffix
            )
            self.audio_gen_module.sakiko_which_state = self.sakiko_state
        elif char_1_name == "祥子":
            temporary_input["character_1"]["talk_style"] = (
                temporary_input["character_1"]["talk_style"] + sakiko_suffix
            )
            self.audio_gen_module.sakiko_which_state = self.sakiko_state

        request = {
            "char_index": indices,
            "user_input": temporary_input,
            "performance_models": {
                str(slot.get("character_name")): slot.get("model_json_path")
                for slot in self._build_active_slots_payload().get("slots", [])
                if isinstance(slot, dict)
            },
        }
        try:
            self.qt2dp_queue.put(request)
        except (OSError, ValueError) as error:
            self.generation_failed(f"请求未能发送，原剧本已保留：{error}")
            dialog.commit_btn.setEnabled(True)
            return
        dialog.accept()

    def generation_failed(self, message) -> None:
        """生成或解析失败时释放入口，保留当前与原目标对话的全部消息。"""
        self._generation_context = None
        self.generate_btn.setEnabled(True)
        self.messages_box.setText(message)

    def handle_response(self,response):
        """验证整份生成结果后进入合成，失败时保留旧剧本并释放生成入口。"""
        if self._generation_context is None:
            return
        if response=='error':
            self.generation_failed("生成失败，原剧本已保留。")
            return

        self.original_response = self.robust_json_parse(response) if isinstance(response, str) else []
        # self.display_timer.setSingleShot(True)
        # self.display_timer.start(1000)
        if (not isinstance(self.original_response, list) or not self.original_response
                or any(not isinstance(turn, dict) or not isinstance(turn.get("speaker"), str)
                       or not isinstance(turn.get("text"), str) or not turn["text"].strip()
                       for turn in self.original_response)):
            self.generation_failed("生成结果格式无效，原剧本已保留。")
            return
        self.char_0_talk_texts=[]
        self.char_0_talk_emotions=[]
        self.char_0_audio_path_list=[]
        self.char_talk_texts_match_original_response_indices=[]
        self.char_1_talk_texts=[]
        self.char_1_talk_emotions=[]
        self.char_1_audio_path_list=[]
        idx_0, idx_1 = self._generation_context["indices"]
        for i,turn in enumerate(self.original_response):
            speaker_name_from_llm = turn.get('speaker', '').strip()  # 去除首尾空格
            text = turn.get('text', '')
            emotion = str(turn.get('emotion') or "happiness")

            # === 【关键修改】使用智能匹配 ===
            matched_idx = self.match_speaker_index(speaker_name_from_llm, [idx_0, idx_1])

            if matched_idx == idx_0:
                self.char_0_talk_texts.append(text)
                self.char_0_talk_emotions.append(emotion)
                self.char_talk_texts_match_original_response_indices.append('char_0')
            elif matched_idx == idx_1:
                self.char_1_talk_texts.append(text)
                self.char_1_talk_emotions.append(emotion)
                self.char_talk_texts_match_original_response_indices.append('char_1')
            else:
                # 两个都没匹配上（比如大模型突然输出了“旁白”或者幻觉出了第三个人）
                self.char_talk_texts_match_original_response_indices.append('skipped')
                logger.warning("无法识别说话人 '%s'，已跳过该句，内容：%s", speaker_name_from_llm, text)
                self.generation_failed("生成结果含未知角色，原剧本已保留。")
                return

        self._generation_serial += 1
        self.audio_gen_module.audio_language_choice="日英混合"

        if not self._generation_context["audio"]:
            self.char_0_audio_path_list = []
            self.char_1_audio_path_list = []
            self.messages_box.setText("缺少语音模型，跳过语音生成，直接展示文本")
            self.final_handle_response([])
            return

        self.handle_response_continue()

    def handle_response_continue(self):
        """使用提交时绑定的第一名角色合成，避免切换对话后角色串位。"""
        context = self._generation_context
        self.messages_box.setText(f"生成 {self.character_list[context['indices'][0]].character_name} 的语音..")
        self.audio_gen_generate_audio_thread_0 = WaitUntilAudioGenModuleSynthesisComplete(
            self.audio_gen_module,
            self.char_0_talk_texts,
            self.character_list[context["indices"][0]],
            context["sakiko_state"],
            self.audio_gen_module.audio_language_choice,
            self.char_0_talk_emotions,
        )
        self.audio_gen_generate_audio_thread_0.synthesis_complete_signal.connect(self.handle_response_continue_1)
        self.audio_gen_generate_audio_thread_0.synthesis_failed_signal.connect(self.generation_failed)
        self.audio_gen_generate_audio_thread_0.start()

    def handle_response_continue_1(self,char_0_audio_path_list):
        """接收第一名角色音频并继续合成第二名角色。"""
        self.char_0_audio_path_list=char_0_audio_path_list
        self.handle_response_continue_2()

    def handle_response_continue_2(self):
        """使用提交时绑定的第二名角色完成语音合成。"""
        context = self._generation_context
        self.messages_box.setText(f"生成 {self.character_list[context['indices'][1]].character_name} 的语音..")
        self.audio_gen_generate_audio_thread_1 = WaitUntilAudioGenModuleSynthesisComplete(
            self.audio_gen_module,
            self.char_1_talk_texts,
            self.character_list[context["indices"][1]],
            context["sakiko_state"],
            self.audio_gen_module.audio_language_choice,
            self.char_1_talk_emotions,
        )
        self.audio_gen_generate_audio_thread_1.synthesis_complete_signal.connect(self.final_handle_response)
        self.audio_gen_generate_audio_thread_1.synthesis_failed_signal.connect(self.generation_failed)
        self.audio_gen_generate_audio_thread_1.start()
    def final_handle_response(self,char_1_audio_path_list=None):
        """整份结果可用后一次替换原目标剧本；保存失败回滚，不污染其他对话。"""
        context = self._generation_context
        if context is None:
            return
        target = context["chat"]
        if not any(chat is target for chat in self.chat_manager.chat_list):
            self.generation_failed("原对话已删除，生成结果未写入其他对话。")
            return
        self.char_1_audio_path_list = char_1_audio_path_list or []
        try:
            playlist = self._build_playlist_queue(context["indices"])
            messages = [self._message_from_turn_dict(turn) for turn in playlist]
        except (ValueError, TypeError, KeyError) as error:
            logger.warning("生成结果转换失败：%s", error)
            self.generation_failed("生成结果无效，原剧本已保留。")
            return
        old_messages = target.message_list
        old_meta = copy.deepcopy(target.meta)
        target.message_list = []
        target.clear_message_list()
        target.message_list = messages
        if not self._save_chat():
            target.message_list = old_messages
            target.meta = old_meta
            self.generation_failed("新剧本保存失败，原剧本已保留。")
            return
        self._generation_context = None
        self.messages_box.setText(f"对话生成完毕")
        self.display_timer.setSingleShot(True)
        self.display_timer.start(1000)
        self.generate_btn.setEnabled(True)

        # 已整份保存，不再通过舞台逐句回执追加消息。
        self.pending_turn_uids.clear()
        self.refresh_chat_list()
        if self.current_chat is target:
            self.playlist_queue = playlist
            self.refresh_chat_display()
            self.display_live2d_message(playlist, preserve_playback=False)

    def display_this_turn_text(self,this_turn_elements):
        """分发预览回执；只有正式播放的新消息进入聊天记录。"""
        if this_turn_elements.get("type") == "multi_char_scene_error":
            self.message_queue.put(this_turn_elements.get("error", "场景加载失败。"))
            return
        if this_turn_elements.get("type") in (
                "multi_char_preview", "multi_char_transition", "multi_char_window", "expression_editor"):
            if self.performance_editor is not None:
                self.performance_editor.receive(this_turn_elements)
            return
        if self.current_chat is None:
            return

        turn_uid = this_turn_elements.get("turn_uid")
        if turn_uid:
            if turn_uid not in self.pending_turn_uids:
                return
            self.pending_turn_uids.discard(turn_uid)

        message = self._message_from_turn_dict(this_turn_elements)

        # 回放旧消息时（旧数据可能没有 turn_uid），按内容签名去重。
        if not turn_uid:
            incoming_signature = self._message_signature_from_dict(this_turn_elements)
            existing_signatures = {
                self._message_signature_from_dict(self._turn_dict_from_message(msg))
                for msg in self.current_chat.message_list
            }
            if incoming_signature in existing_signatures:
                return

        self.current_chat.add_message(message)
        self.refresh_chat_display()
        self.refresh_chat_list()

    def on_link_clicked(self, url: QUrl) -> None:
        """处理聊天区链接点击：支持单条消息回放。"""
        if self.current_chat is None:
            return

        url_str = url.toString()
        if not url_str.startswith("replay:"):
            return

        try:
            index = int(url_str.split(":", 1)[1])
        except ValueError:
            return

        if not (0 <= index < len(self.current_chat.message_list)):
            return

        replay_payload = self._single_replay_payload(index)

        # 回放前按当前对话角色同步 Live2D 双模型，避免角色错位。
        self.sync_live2d_active_slots()
        self.display_live2d_message([replay_payload], preserve_playback=False)

    def _single_replay_payload(self, index: int) -> Dict[str, Any]:
        """携带整段场景配置，单句回放可从默认舞台解析到目标句。"""
        messages = [message.as_dict() for message in self.current_chat.message_list]
        # 当前句与上下文使用独立字典，避免形成递归引用或写入消息存档。
        turn = self.current_chat.message_list[index].as_dict()
        turn["scene_context"] = {"messages": messages, "index": index}
        return turn

    def on_stage_closed(self) -> None:
        """Live2D 退出时保留编排草稿，让用户仍可保存或放弃。"""
        if self.performance_editor is not None:
            self.performance_editor.stage_closed()

    def open_performance_editor(self, index: int) -> None:
        """空闲时进入双窗口编排，初始化失败不留下半开的编辑会话。"""
        if self.performance_editor is not None:
            self.performance_editor.resource_window.raise_()
            return
        if self.current_chat is None or not 0 <= index < len(self.current_chat.message_list):
            return
        worker = getattr(self, "generate_thread", None)
        busy = not self.generate_btn.isEnabled() or bool(self.pending_turn_uids)
        busy = busy or (worker is not None and worker.isRunning())
        busy = busy or (self.playback_idle_value is not None and not self.playback_idle_value.value)
        if busy:
            QMessageBox.information(self, "演出编排", "请等待生成、合成或当前播放结束后再进入编排。")
            return
        from ui.components.multi_char_editor import MultiCharEditor
        payload = self._build_active_slots_payload(preserve_playback=True)
        self.sync_live2d_active_slots(preserve_playback=True)
        try:
            self.performance_editor = MultiCharEditor(
                self, self.current_chat, self.current_chat.message_list[index],
                payload["slots"], payload["motion_facing_mode"],
            )
        except (OSError, ValueError, RuntimeError) as error:
            self.setEnabled(True)
            QMessageBox.warning(self, "无法进入编排", str(error))

    def open_editor(self, index: int) -> None:
        """打开编辑器，修改当前对话中的指定消息。"""
        if self.current_chat is None or not (0 <= index < len(self.current_chat.message_list)):
            return

        current_data = self._turn_dict_from_message(self.current_chat.message_list[index])
        dialog = ElementEditorDialog(current_data, self)
        if dialog.exec_() != QDialog.Accepted:
            return

        new_data = dialog.get_new_data()
        # 台词编辑保留原编排；正常无声只在演出编辑器保存时清理越界。
        self.current_chat.message_list[index] = self._message_from_turn_dict(new_data)
        self.refresh_chat_display()
        self.refresh_chat_list()

    def refresh_chat_display(self) -> None:
        """基于当前 Chat 对象重新渲染 QTextBrowser，保留滚动位置。"""
        scroll_bar = self.chat_display.verticalScrollBar()
        saved_position = scroll_bar.value()
        was_at_bottom = saved_position == scroll_bar.maximum()

        self.chat_display.clear()

        if self.current_chat is not None:
            for index, msg in enumerate(self.current_chat.message_list):
                # 必须分两次添加，先添加文本再添加翻译，才能正确显示换行
                # 一次添加时，即使文本-翻译之间有换行，文本和翻译也会被挤在一起显示，无法区分
                text_html, translation_html = self.render_message_html(index, msg)
                self.chat_display.append(text_html)
                if translation_html.strip():
                    self.chat_display.append(translation_html)

        QApplication.processEvents()

        if was_at_bottom:
            scroll_bar.setValue(scroll_bar.maximum())
        else:
            scroll_bar.setValue(saved_position)

        self.update_replay_button_visibility()

    def regenerate_audio(self, index: int, on_complete=None) -> bool:
        """捕获原聊天、消息及输入快照，合成回填不使用当前聊天下标。"""
        worker = getattr(self, "generate_thread", None)
        if worker is not None and worker.isRunning():
            QMessageBox.information(self, "正在合成", "请等待当前音频生成完成。")
            return False
        self.audio_gen_module.audio_language_choice = "日英混合"
        if self.current_chat is None or not (0 <= index < len(self.current_chat.message_list)):
            return False

        message = self.current_chat.message_list[index]
        if not message.character_name:
            QMessageBox.information(self, "无法合成", "这句台词未选择语音角色，无法生成音频。")
            return False
        voice_character = self._find_character_by_name(message.character_name)
        if voice_character is None or not voice_character.has_valid_voice_model():
            QMessageBox.information(self, "无法合成", "当前角色未配置可用的语音模型。")
            return False
        self._audio_regeneration_callback = on_complete
        self.regenerate_text = message.text
        self.regenerate_emotion = (
            message.emotion.as_string()
            if hasattr(message.emotion, "as_string")
            else str(message.emotion or "happiness")
        )
        self._audio_regeneration = (self.current_chat, message, message.text, message.audio_path, object())
        self.regenerate_character = voice_character
        self.regenerate_audio_continue()
        return True

    def regenerate_audio_continue(self):
        """启动已捕获消息的语音合成，并固定本次回填上下文。"""
        self.generate_thread = WaitUntilAudioGenModuleSynthesisComplete(
            self.audio_gen_module,
            [self.regenerate_text],
            self.regenerate_character,
            self.sakiko_state,
            self.audio_gen_module.audio_language_choice,
            [self.regenerate_emotion],
        )
        context = self._audio_regeneration
        self.generate_thread.synthesis_complete_signal.connect(
            lambda paths, target=context: self.replace_audio_path(paths, target)
        )
        self.generate_thread.synthesis_failed_signal.connect(
            lambda reason, target=context: self.finish_audio_regeneration(target, False, reason)
        )
        self.generate_thread.start()

    def finish_audio_regeneration(self, context, success, reason):
        """统一结束重生成任务，通知编排器解锁并保留失败前的数据。"""
        if context is not self._audio_regeneration:
            return
        callback = getattr(self, "_audio_regeneration_callback", None)
        self._audio_regeneration = None
        self._audio_regeneration_callback = None
        self.message_queue.put(reason)
        if callback is not None:
            callback(success, reason)

    def replace_audio_path(self, audio_path, context=None):
        """只替换有效原消息，持久保存新音频并校验全部角色轨道。"""
        if context is None or context is not self._audio_regeneration:
            return
        chat, message, text, previous_path, token = context
        if (not any(item is message for item in chat.message_list)
                or message.text != text or message.audio_path != previous_path):
            self.finish_audio_regeneration(context, False, "原消息已变化，未替换音频。")
            return
        from live2d_support.multi_char_resources import audio_span
        from performance_types import trim_timeline
        span = audio_span(str(audio_path[0] or "") if audio_path else "", text)
        if not span.path:
            self.finish_audio_regeneration(context, False, "音频生成失败，原音频与编排已保留")
            return
        old_timeline = message.performance_timeline
        new_timeline, removed = trim_timeline(old_timeline, span.duration_ms)
        message.audio_path = span.path
        message.performance_timeline = new_timeline
        if not self._save_chat():
            message.audio_path = previous_path
            message.performance_timeline = old_timeline
            self.finish_audio_regeneration(context, False, "音频保存失败，原音频与编排已保留")
            return
        self.finish_audio_regeneration(context, True,
                                       f"生成完毕；已移除 {removed} 个越界编排事件" if removed else "生成完毕")
        if self.current_chat is chat and self.performance_editor is None:
            index = next(index for index, item in enumerate(chat.message_list) if item is message)
            self.sync_live2d_active_slots()
            self.display_live2d_message([self._single_replay_payload(index)], preserve_playback=False)

    def _save_chat(self) -> bool:
        """保存失败时提示用户，并让调用者停止后续成功流程。"""
        try:
            self.chat_manager.save()
            return True
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", f"未保存内容仍在内存中，请重试。\n{exc}")
            return False

    def save_history_data(self):
        if not self._save_chat():
            return
        self.message_queue.put("已保存最新记录")


    def close_program(self) -> None:
        """退出按钮统一走窗口关闭时的保存确认。"""
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭前保存，失败时允许重试、放弃或继续留在程序中。"""
        if self.performance_editor is not None:
            self.performance_editor.request_close()
            if self.performance_editor is not None:
                event.ignore()
                return
        from runtime.storage_ui import save_before_close
        if not save_before_close(self.chat_manager, self):
            event.ignore()
            return
        self.to_live2d_change_character_queue.put('EXIT')
        self.audio_gen_module.shutdown_worker()
        self.dp2qt_queue.put('EXIT')
        self.message_queue.put('EXIT')
        self.qt2dp_queue.put('EXIT')
        event.accept()

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
        except json.JSONDecodeError:
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
            logger.warning("未在输出中找到 JSON 结构，重新生成一遍。")
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

        logger.debug("解析彻底失败，原始内容：\n%s", llm_output)
        return []  # 返回空列表作为兜底




if __name__ == "__main__":
    import multiprocessing as mp
    from dp_local_multi_char import DSLocalAndVoiceGen
    from audio_generator import AudioGenerate
    from multi_char_live2d_module import run_live2d_process

    # Windows/macOS 下默认是 spawn；显式声明有助于一致性
    mp.freeze_support()
    ctx = mp.get_context("spawn")

    setup_logging()

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    from runtime.runtime_lock import RuntimeLockBusy, acquire_runtime_lock
    try:
        runtime_lease = acquire_runtime_lock(project_root, "theater")
        get_chat_manager(write_scope=ChatType.SMALL_THEATER)
    except (RuntimeLockBusy, RuntimeError, OSError) as exc:
        print(str(exc))
        raise SystemExit(1)

    get_char_attr = character.GetCharacterAttributes()

    dp_module = DSLocalAndVoiceGen(get_char_attr.character_class_list)
    audio_gen = AudioGenerate()
    audio_gen.if_small_theater_mode = True

    # === 进程/线程通信：统一用 multiprocessing 的 Queue（spawn 语义下可用）===
    # 重要：不要在启动 Live2D 子进程时传入 get_char_attr.character_class_list 这种大对象，
    # 以避免 Windows 进程启动参数大小限制（约 8-16KB）导致的失败。
    change_char_queue = ctx.Queue()
    qt2dp_queue = ctx.Queue()
    message_queue = ctx.Queue()
    dp2qt_queue = ctx.Queue()
    to_live2d_module_queue = ctx.Queue()
    tell_qt_this_turn_finish_queue = ctx.Queue()
    playback_idle_value = ctx.Value("b", True)

    audio_gen.initialize(get_char_attr.character_class_list, message_queue)

    # Live2D：必须在子进程的主线程里创建窗口（macOS NSWindow 限制）
    live2d_process = ctx.Process(
        target=run_live2d_process,
        args=(change_char_queue, to_live2d_module_queue, tell_qt_this_turn_finish_queue, 
              get_log_queue(), playback_idle_value),
        name="Live2DProcess",
    )

    dp_thread = threading.Thread(
        target=dp_module.text_generator,
        args=(dp2qt_queue, qt2dp_queue, message_queue),
        daemon=True,
    )
    app = QApplication(sys.argv)

    window = ViewerGUI(
        get_char_attr.character_class_list,
        qt2dp_queue,
        message_queue,
        dp2qt_queue,
        audio_gen,
        to_live2d_module_queue,
        change_char_queue,
        tell_qt_this_turn_finish_queue,
        playback_idle_value,
    )
    font_path = os.path.join(project_root, "font", "msyh.ttc")
    font_id = QFontDatabase.addApplicationFont(os.path.abspath(font_path))  # 设置字体
    # 在成功加载后才使用该字体
    if font_id != -1:
        font_family = QFontDatabase.applicationFontFamilies(font_id)
        font = QFont(font_family[0], 12)
        app.setFont(font)

    from PyQt5.QtWidgets import QDesktopWidget  # 设置qt窗口位置，与live2d对齐

    screen_w_mid = int(0.55 * QDesktopWidget().screenGeometry().width())
    screen_h_mid = int(0.5 * QDesktopWidget().screenGeometry().height())
    window.move(screen_w_mid,
                int(screen_h_mid - 0.35 * QDesktopWidget().screenGeometry().height()))  # 因为窗口高度设置的是0.7倍桌面宽

    live2d_process.start()
    dp_thread.start()
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
        qt2dp_queue.put("EXIT", block=False)
    except Exception:
        pass

    dp_thread.join()
    try:
        audio_gen.shutdown_worker()
    except Exception:
        pass
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
    
    shutdown_logging()

'''
按钮改为图标式
'''
