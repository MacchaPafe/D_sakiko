from __future__ import annotations

import os.path
import re
import time
import json
import random
import uuid
from pathlib import Path
from typing import Callable, Optional, Sequence, cast

from PyQt5.QtMultimedia import QMediaPlayer, QMediaPlaylist, QMediaContent

import numpy as np
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDesktopWidget,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStyledItemDelegate,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import QTimer, QThread, pyqtSignal, QObject, Qt, QSize, QUrl, QPoint, pyqtSlot
from PyQt5.QtGui import QFontDatabase, QFont, QIcon, QPalette, QColor, QImage, QPixmap, QCursor, QPainter, QShowEvent

import sounddevice as sd
from opencc import OpenCC
import os,sys

from ui_main.threads.get_model_limit_thread import GetModelLimitThread

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)
from ui_constants import dialogWindowDefaultCss,char_info_json,tool_name_chi_mapping
from log import get_logger
from qconfig import THIRD_PARTY_OPENAI_COMPAT_PROVIDER_IDS, d_sakiko_config
from character import CharacterAttributes, GetCharacterAttributes, EMOTION_REFERENCE_KEYS, ref_audio_language_list
from chat.chat import ChatBackupImportResult, ChatManager, Chat, ChatType, DeleteMessagesResult, Message, get_chat_manager
from chat.attachments import (
    add_model_image_upload_force_allow,
    delete_chat_attachment_dir,
    model_can_force_allow_image_upload,
    model_supports_image_upload,
    resolve_attachment_path,
)
from chat.model_token_usage import count_message_tokens
from chat.rolling_summary import (
    build_llm_query_with_rolling_summary,
    invalidate_rolling_summary_from_message_index,
)
from emotion_enum import EmotionEnum
from ui_main.components.chat_display import ChatDisplay
from ui_main.components.chat_sidebar import ChatSidebarMode, ChatSidebarView
from ui_main.components.context_usage_indicator import (
    ContextUsageIndicator,
    ContextUsageSnapshot,
    resolve_context_usage_sizing,
)
from ui_main.components.message_input import MessageInput
from ui_main.components.update_dialog import UpdateDialog
from ui_main.components.repair_dialog import RepairDialog
from ui_main.threads.repair_controller import RepairCheckThread, RepairPrepareThread
from ui_main.threads.update_controller import ReleaseNotesThread, UpdateCheckThread, UpdateDownloadThread
from ui.file_manager import show_file_in_manager
from update.update_checker import get_configured_index_urls, read_current_version
from update.update_launcher import build_restart_command, launch_update_process
from update.update_models import DownloadedPatch, UpdatePlan
from update.update_paths import get_app_root, get_update_result_file, get_version_file
from repair.repair_checker import PreparedRepair, RepairCheckResult, get_configured_repair_base_urls
from repair.repair_launcher import launch_repair_process
from repair.repair_paths import get_repair_result_file
from llm_model_utils import ensure_openai_compatible_model
from input_commands import (
    CommandSpec,
    InputCommandMatcher,
    InputCommandPalette,
    build_default_input_command_specs,
)
from live2d_support.model_importer import Live2DModelImportError, import_live2d_model_to_extra_model
from live2d_support.model_normalizer import normalize_live2d_model_for_project


TOOL_CALL_START_EVENT_PREFIX = "__TOOL_CALL_START__:"
TOOL_CALL_UPDATE_EVENT_PREFIX = "__TOOL_CALL_UPDATE__:"
LOTTERY_UI_EVENT_PREFIX = "__LOTTERY_UI_CMD__:"
logger = get_logger(__name__)


def append_generation_timing_log(message: str) -> None:
    """把语音生成耗时写入本地调试日志。"""
    try:
        log_path = os.path.join(script_dir, "generation_log.txt")
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] {message}\n")
    except Exception:
        logger.exception("写入语音生成耗时日志失败")

SINGLE_CHAT_COMBO_CSS = """
QComboBox {
    background-color: #FFFFFF;
    border: 1px solid #E0E0E0;
    border-bottom: 2px solid #D1D1D1;
    border-radius: 4px;
    color: #5F6368;
    padding: 6px 28px 6px 10px;
    min-height: 24px;
}

QComboBox:hover {
    background-color: #FDFDFD;
    border-bottom: 2px solid #7799CC;
}

QComboBox:focus {
    background-color: #FFFFFF;
    border: 2px solid #7799CC;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 26px;
    border: none;
    background: transparent;
}

QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #6D7B8D;
    margin-right: 8px;
}
"""

SINGLE_CHAT_COMBO_VIEW_CSS = """
QAbstractItemView,
QListView#singleChatCharacterComboView {
    background-color: #FFFFFF;
    border: 1px solid #E0E0E0;
    border-radius: 4px;
    padding: 4px;
    outline: none;
    margin-top: 4px;
    selection-background-color: #E9F1FB;
    selection-color: #2E4A6B;
}

QAbstractItemView::item,
QListView#singleChatCharacterComboView::item {
    height: 32px;
    border-radius: 4px;
    padding-left: 8px;
    color: #5F6368;
    background-color: transparent;
    border: none;
}

QAbstractItemView::item:hover,
QAbstractItemView::item:selected,
QListView#singleChatCharacterComboView::item:hover,
QListView#singleChatCharacterComboView::item:selected {
    background-color: #E9F1FB;
    color: #2E4A6B;
    border: none;
}
"""

SINGLE_CHAT_DIALOG_CSS = (
    dialogWindowDefaultCss
    + SINGLE_CHAT_COMBO_CSS
    + SINGLE_CHAT_COMBO_VIEW_CSS
)

class ThemeManager: #主题颜色设定
    @staticmethod
    def generate_stylesheet(base_hex_color):
        """
        根据主色调生成整套配色方案
        :param base_hex_color: 用户选择的主题色 (例如 "#7FB2EB")
        :return: 生成好的 QSS 字符串
        """
        base = QColor(base_hex_color)

        # 获取 HSL (色相, 饱和度, 亮度)
        h = base.hue()
        s = base.saturation()
        l = base.lightness()

        # 定义辅助函数：生成不同亮度和饱和度的颜色
        def get_color(hue, sat, light):
            # 确保数值在合法范围内
            sat = max(0, min(255, sat))
            light = max(0, min(255, light))
            # 如果原色是黑白灰(s=0)，保持s=0
            final_h = hue if s > 0 else -1
            return QColor.fromHsl(final_h, int(sat), int(light)).name()

        # 辅助函数：限制数值范围
        def clamp(val, min_v, max_v):
            return max(min_v, min(val, max_v))

        # --- 智能自适应调色算法 (活泼且护眼版) ---

        # 1. 确定基准
        # 如果用户选的颜色太亮（比如纯黄），文字会看不清，所以我们要计算一个“深色版”作为文字颜色
        # 如果用户选的颜色太暗（比如深褐），做按钮会沉闷，我们要计算一个“鲜艳版”

        # 这里的 primary 保持用户原色，用于某些高亮强调
        primary = base.name()

        # 计算适合做文字的颜色：保持色相，但亮度不能太高 (限制在 30-150 之间)，饱和度适中
        # 这样即使用户选了荧光绿，文字也会自动变成深绿色，保证可读性
        text_l = clamp(l, 30, 140)
        text_s = clamp(s, 100, 240)
        text_main = get_color(h, text_s, text_l)

        # 2. 按钮/滑块颜色 (Key Color)
        # 活泼的关键：饱和度要高(>140)，但亮度适中(>120 且 <210)，避免荧光感
        btn_s = clamp(s , 140, 230)  # 哪怕选了灰灰的颜色，也会强制提鲜到 160
        btn_l = clamp(l, 120, 190)  # 亮度控制在中间，最有质感
        pushbtn_slider = get_color(h, btn_s, btn_l)

        # 悬停色：比按钮亮一点，饱和度稍微降一点点防止溢出
        pushbtn_slider_hover = get_color(h, btn_s * 0.9, min(btn_l + 20, 240))

        # 3. 窗口背景色 (Background)
        # 活泼又不刺眼的核心：高亮度(240-250)，低-中饱和度(30-60)
        # 之前的 s*0.7 对于高饱和色来说太高了(会变成 170+ 的粉红背景，很累眼)
        # 我们强制把背景饱和度锁定在 30~70 这个区间，这就是“马卡龙色”的秘密
        bg_s = clamp(s * 0.5, 30, 70)
        bg_main = get_color(h, bg_s, 248)  # 248 非常接近白，但有明显的色调

        # 4. 边框色 (Border)
        # 比背景深，比按钮浅。饱和度控制在 60-120
        border_s = clamp(s * 0.8, 60, 120)
        border_l = 215  # 固定一个较高的亮度，显得通透
        border = get_color(h, border_s, border_l)

        # 5. 滚动条背景
        # 介于背景和边框之间
        scrollbar_bg = get_color(h, bg_s + 10, 235)

        # 6. 滑块划过区域 (Sub-page)
        # 类似果冻的效果，高亮，中等饱和度
        sub_s = clamp(s, 100, 180)
        sub_page = get_color(h, sub_s, 225)

        # 7. 纯白控件背景
        bg_widget = "#FFFFFF"
        # --- 套用模板 ---
        qss_template = f"""
        QWidget {{
            background-color: {bg_main};
            color: {text_main};
        }}

        QTextBrowser {{
            text-decoration: none;
            background-color: {bg_widget};
            border: 3px solid {border};
            border-radius: 9px;
            padding: 5px;
        }}

        QLineEdit {{
            background-color: {bg_widget};
            border: 2px solid {border};
            border-radius: 9px;
            padding: 5px;
            color: {text_main};
        }}

        QPushButton {{                
            background-color: {pushbtn_slider};
            color: #ffffff;
            border-radius: 6px;
            padding: 6px;
            border: none;
        }}

        QPushButton:hover {{
            background-color: {pushbtn_slider_hover};
        }}

        QScrollBar:vertical {{
            border: none;
            background: {scrollbar_bg};
            width: 10px;
            margin: 0px 0px 0px 0px;
        }}

        QScrollBar::handle:vertical {{
            background: {border};
            min-height: 20px;
            border-radius: 3px;
        }}

        QScrollBar::handle:vertical:hover {{
            background: {primary};
        }}

        QSlider::groove:horizontal {{
            border: 1px solid {border};
            height: {int(QDesktopWidget().screenGeometry().height() * 0.004)}px;
            background: {scrollbar_bg};
            margin: 2px 0;
            border-radius: {int(QDesktopWidget().screenGeometry().height() * 0.004*0.5)}px;
        }}

        QSlider::handle:horizontal {{
            background: {pushbtn_slider};
            border: 1px solid {pushbtn_slider};
            width: {int(QDesktopWidget().screenGeometry().height() * 0.004*2)}px;
            margin: -4px 0;
            border-radius: {int(QDesktopWidget().screenGeometry().height() * 0.004*0.5)}px;
        }}

        QSlider::handle:horizontal:hover {{
            background: {pushbtn_slider_hover};
        }}

        QSlider::sub-page:horizontal {{
            background: {sub_page};
            border-radius: 4px;
            margin: 2px 0;
        }}

        QToolButton {{
            color: {text_main};
            background-color: transparent;
            border: none;
            border-radius: 4px;
        }}

        QToolButton:hover {{
            background-color: rgba(0, 0, 0, 0.05);
        }}
        """
        return qss_template

    @staticmethod
    def get_QT_style_theme_color(qt_css):
        try:
            match = re.search(r"QWidget\s*\{[^}]*?(?<!-)color\s*:\s*([^;]+);", qt_css, re.DOTALL)

            if match:
                color_value = match.group(1).strip()
                if not color_value.startswith('#'):
                    logger.info("颜色值有误，使用祥子配色")
                    return '#7799CC'
                return color_value
            else:
                logger.info("使用默认祥子配色")
                return '#7799CC'

        except Exception:
            logger.exception("QT_style.json 文件格式有误，使用默认祥子配色。")
            return '#7799CC'

class CommunicateThreadDP2QT(QThread):
    response_signal=pyqtSignal(object)
    def __init__(self,dp2qt_queue,chat_display):
        super().__init__()
        self.this_turn_response=''
        self.dp2qt_queue=dp2qt_queue
        self.chat_display=chat_display

    def run(self):
        while True:
            if not self.dp2qt_queue.empty() and not self.chat_display.is_streaming():     #解决了特定情况下显示不全回答的bug
                self.this_turn_response=self.dp2qt_queue.get()
                self.response_signal.emit(self.this_turn_response)  # noqa
            time.sleep(0.1)

class CommunicateThreadMessages(QThread):
    message_signal=pyqtSignal(str)
    def __init__(self,message_queue):
        super().__init__()
        self.message=''
        self.message_queue=message_queue

    def run(self):
        while True:
            self.message=self.message_queue.get()
            self.message_signal.emit(self.message)  # noqa


class ModelLoaderThread(QThread):   #加载语音识别模型的线程
    model_loaded = pyqtSignal(object)
    model_load_failed = pyqtSignal(str)

    def __init__(self, model_size, device, compute_type):
        super().__init__()
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type

    def run(self):
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            self.model_loaded.emit(model)  # noqa
        except Exception as e:
            self.model_load_failed.emit(str(e))  # noqa


class TranscriptionWorker(QObject):
    finished = pyqtSignal(str)

    def __init__(self, model):
        super().__init__()
        self.model = model

    def transcribe(self, audio_data):
        try:
            segments, _ = self.model.transcribe(audio_data, beam_size=5, language="zh")
            text = "".join([seg.text for seg in segments])
            self.finished.emit(text)  # noqa
        except Exception as e:
            self.finished.emit(f"识别错误 {e}")  # noqa


class MoreFunctionWindow(QDialog):
    def __init__(
        self,
        parent_window_close_fun: Callable[[], None],
        check_update_fun: Callable[[], None] | None = None,
        check_repair_fun: Callable[[], None] | None = None,
        has_update: bool = False,
    ) -> None:
        super().__init__()
        self.setWindowTitle("更多功能...")
        self.screen = QDesktopWidget().screenGeometry()
        self.resize(int(0.2 * self.screen.width()), int(0.2 * self.screen.height()))
        layout = QVBoxLayout()
        self.open_motion_editor_button =QPushButton("运行动作组编辑程序")
        self.open_motion_editor_button.clicked.connect(self.on_click_open_motion_editor_button)  # noqa
        layout.addWidget(self.open_motion_editor_button)

        self.open_persona_editor_button = QPushButton("编辑对话身份和角色信息")
        self.open_persona_editor_button.clicked.connect(self.on_click_open_persona_editor_button)
        layout.addWidget(self.open_persona_editor_button)

        self.open_start_config_button=QPushButton("启动参数配置")
        self.open_start_config_button.clicked.connect(self.on_click_open_start_config_button)  # noqa
        layout.addWidget(self.open_start_config_button)

        self.open_small_theater_btn=QPushButton("小剧场模式")
        self.open_small_theater_btn.clicked.connect(self.on_click_open_small_theater)  # noqa
        layout.addWidget(self.open_small_theater_btn)

        open_live2d_downloader_btn=QPushButton("Live2D模型下载器")
        open_live2d_downloader_btn.clicked.connect(self.on_click_open_live2d_downloader)  # noqa
        layout.addWidget(open_live2d_downloader_btn)

        if check_update_fun is not None:
            check_update_text = "检查更新（有新版本）" if has_update else "检查更新"
            self.check_update_btn = QPushButton(check_update_text)
            self.check_update_btn.clicked.connect(check_update_fun)  # noqa
            self.check_update_btn.clicked.connect(self.close)  # noqa
            layout.addWidget(self.check_update_btn)

        if check_repair_fun is not None:
            self.check_repair_btn = QPushButton("检查并修复程序文件")
            self.check_repair_btn.clicked.connect(check_repair_fun)  # noqa
            self.check_repair_btn.clicked.connect(self.close)  # noqa
            layout.addWidget(self.check_repair_btn)

        try:
            current_version = read_current_version(get_version_file(get_app_root()))
        except Exception:
            current_version = "无法识别"
        self.version_label = QLabel(f"当前版本: {current_version}")
        self.version_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.version_label)

        self.text_label = QLabel("更多小功能还在开发中...")
        self.text_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.text_label)

        self.close_program_button=QPushButton("退出程序")
        self.close_program_button.clicked.connect(parent_window_close_fun)  # noqa
        self.close_program_button.clicked.connect(self.close)  # noqa
        layout.addWidget(self.close_program_button)

        self.setLayout(layout)
        self.setStyleSheet(dialogWindowDefaultCss)

    @staticmethod
    def exec_bat(bat_name):
        import sys
        current_script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))  # noqa
        #  构造 .bat 文件的完整路径
        # run_2.bat 在当前脚本目录的父目录中 (../run_2.bat)
        parent_dir = os.path.dirname(current_script_dir)
        # 完整的 .bat 文件路径
        bat_path = os.path.join(parent_dir, bat_name)
        # 检查文件是否存在
        if not os.path.exists(bat_path):
            logger.warning(".bat 文件未找到：%s", bat_path)
            return
        try:
            # 使用 subprocess 模块启动 .bat 文件
            import subprocess
            # 使用 shell=True 让系统直接执行批处理文件
            # Windows 会使用 cmd.exe 来执行 .bat 文件
            subprocess.Popen([bat_path], shell=True)
        except Exception:
            logger.exception("启动失败，启动程序时发生错误")

    def on_click_open_motion_editor_button(self):
        try:
            # 使用 subprocess 模块启动 .bat 文件
            import subprocess
            import sys
            # 使用 shell=True 让系统直接执行批处理文件
            # Windows 会使用 cmd.exe 来执行 .bat 文件
            subprocess.Popen([sys.executable, "live2d_viewer.py"])
        except Exception:
            logger.exception("启动 Live2D 动作编辑器失败")
        self.close()

    def on_click_open_persona_editor_button(self):
        try:
            import subprocess
            import sys
            subprocess.Popen([sys.executable, "dsakiko_configuration.py", "CharacterArea"])
        except Exception:
            logger.exception("启动配置窗口失败")
        self.close()

    def on_click_open_start_config_button(self):
        try:
            # 使用 subprocess 模块启动 .bat 文件
            import subprocess
            import sys
            # 使用 shell=True 让系统直接执行批处理文件
            # Windows 会使用 cmd.exe 来执行 .bat 文件
            subprocess.Popen([sys.executable, "dsakiko_configuration.py", "DSakikoConfigArea"])
        except Exception:
            logger.exception("启动配置窗口失败")
        self.close()

    def on_click_open_small_theater(self):
        import sys
        try:
            # 使用 subprocess 模块启动 .bat 文件
            import subprocess
            # 使用 shell=True 让系统直接执行批处理文件
            # Windows 会使用 cmd.exe 来执行 .bat 文件
            subprocess.Popen([sys.executable, "multi_char_main.py"])
        except Exception:
            logger.exception("启动小剧场失败")
        self.close()

    @staticmethod
    def open_live2d_downloader():
        import sys
        try:
            # 使用 subprocess 模块启动 .bat 文件
            import subprocess
            # 使用 shell=True 让系统直接执行批处理文件
            # Windows 会使用 cmd.exe 来执行 .bat 文件
            subprocess.Popen([sys.executable, "live2d_downloader_ui.py"])
        except Exception:
            logger.exception("启动 Live2D 服装下载器失败")

    def on_click_open_live2d_downloader(self):
        self.open_live2d_downloader()
        self.close()

class WarningWindow(QDialog):
    def __init__(self,warning_text,css=None,parent_window_fun=None):
        super().__init__()
        self.setWindowTitle("确认操作")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.label=QLabel(warning_text)
        layout=QVBoxLayout()
        layout.addWidget(self.label)
        self.btn_layout=QHBoxLayout()
        self.confirm_btn=QPushButton("确定")
        self.cancel_btn=QPushButton("取消")
        self.btn_layout.addWidget(self.confirm_btn)
        self.btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(self.btn_layout)
        self.setLayout(layout)
        self.setStyleSheet(dialogWindowDefaultCss)
        if parent_window_fun is not None:
            self.confirm_btn.clicked.connect(parent_window_fun)  # noqa
        self.confirm_btn.clicked.connect(self.close)  # noqa
        self.cancel_btn.clicked.connect(self.close)  # noqa


class ToolCallInfoDialog(QDialog):
    def __init__(self, parent, record: dict, win_size: tuple[int, int]):
        super().__init__(parent)
        self.setWindowTitle("工具调用详情")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(*win_size)

        tool_name = str(tool_name_chi_mapping.get(record.get("tool_name"),record.get("tool_name")) or "unknown")
        status = str(record.get("status") or "running")
        duration = record.get("duration_sec")
        result_content = str(record.get("result_content") or "工具运行中...")

        status_text = "工具运行中..." if status != "completed" else f"运行完成 {float(duration or 0.0):.2f} 秒"

        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"工具名称: {tool_name}"))
        layout.addWidget(QLabel(f"状态: {status_text}"))

        result_box = QTextBrowser()
        result_box.setPlainText(result_content)
        layout.addWidget(result_box)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)  # noqa
        layout.addWidget(close_btn)

        self.setLayout(layout)
        self.setStyleSheet(dialogWindowDefaultCss)


class LotteryDialog(QDialog):
    def __init__(self, title: str, options: list[str], on_result, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title or "随机抽签")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.screen = QDesktopWidget().screenGeometry()
        self.resize(int(self.screen.width() * 0.2), int(self.screen.height() * 0.35))
        
        self.options = [str(one).strip() for one in options if str(one).strip()]
        self.on_result = on_result
        self.current_highlight_index = -1
        self.winner_index = -1
        self.animation_step = 0
        self.total_animation_steps = 0
        self.animation_running = False

        font_size = max(12, int(self.screen.height() * 0.014))
        
        layout = QVBoxLayout()
        tip = QLabel("点击“开始抽签”进行随机抽奖吧！")
        tip.setStyleSheet(f"font-size: {font_size}px; font-weight: bold;")
        tip.setAlignment(Qt.AlignCenter)
        layout.addWidget(tip)

        self.options_box = QTextBrowser()
        self.options_box.setOpenLinks(False)
        self.options_box.setStyleSheet(f"font-size: {font_size - 2}px; border: none; background: transparent;")
        layout.addWidget(self.options_box)
        self._render_options(-1)

        self.result_label = QLabel("")
        self.result_label.setAlignment(Qt.AlignCenter)
        self.result_label.setStyleSheet(f"font-size: {font_size}px; font-weight: bold; color: #ff5c5c;")
        layout.addWidget(self.result_label)

        btn_layout = QHBoxLayout()
        self.draw_btn = QPushButton("开始抽签")
        self.draw_btn.setMinimumHeight(int(self.screen.height() * 0.03))
        self.draw_btn.setStyleSheet(f"font-size: {font_size}px;")
        self.draw_btn.clicked.connect(self.draw_once)  # noqa
        
        close_btn = QPushButton("关闭")
        close_btn.setMinimumHeight(int(self.screen.height() * 0.03))
        close_btn.setStyleSheet(f"font-size: {font_size}px;")
        close_btn.clicked.connect(self.close)  # noqa
        
        btn_layout.addWidget(self.draw_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self._on_animation_tick)  # noqa

        self.setLayout(layout)
        
        # 继承父窗口(ChatGUI)的主题色设定
        if parent and hasattr(parent, '_current_theme_color'):
            self.setStyleSheet(ThemeManager.generate_stylesheet(parent._current_theme_color))
        else:
            self.setStyleSheet(dialogWindowDefaultCss)
        self.parent=parent

    def _render_options(self, highlight_index: int):
        lines: list[str] = []
        pad_y = int(self.screen.height() * 0.006)
        pad_x = int(self.screen.width() * 0.005)
        for idx, one in enumerate(self.options):
            safe_text = one.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if idx == highlight_index:
                lines.append(
                    f"<div style='padding: {pad_y-1}px {pad_x-1}px; margin: 8px 0; background-color: {self.parent._current_theme_color}; color: #FFF; border-radius: 8px; font-weight:bold; text-align: center; border: 2px solid #ffa940;'>"
                    + f"—>{safe_text}"
                    + "</div>"
                )
            else:
                lines.append(
                    f"<div style='padding: {pad_y}px {pad_x}px; margin: 8px 0; background-color: rgba(0, 0, 0, 0.05); color: inherit; border-radius: 8px; text-align: center;'>"
                    + f"{safe_text}"
                    + "</div>"
                )
        self.options_box.setHtml("".join(lines))

    def _on_animation_tick(self):
        if not self.options:
            self.animation_timer.stop()
            self.animation_running = False
            return

        if self.animation_step < self.total_animation_steps:
            self.current_highlight_index = (self.current_highlight_index + 1) % len(self.options)
            self._render_options(self.current_highlight_index)
            self.animation_step += 1
            
            # ease-out - gradually slow down the animation timer
            if self.total_animation_steps - self.animation_step < 10:
                slow_down = 100 + (10 - (self.total_animation_steps - self.animation_step)) * 20
                self.animation_timer.setInterval(slow_down)
            
            return

        self.animation_timer.stop()
        self.animation_running = False
        self.current_highlight_index = self.winner_index
        self._render_options(self.current_highlight_index)
        winner = self.options[self.winner_index]
        self.result_label.setText(f"抽签结果：{winner}")
        if callable(self.on_result):
            self.on_result(winner)

    def draw_once(self):
        if not self.options:
            self.result_label.setText("没有可抽取的候选项")
            return

        if self.animation_running:
            return

        self.winner_index = random.randint(0, len(self.options) - 1)
        self.animation_step = 0
        self.total_animation_steps = max(len(self.options) * 4 + random.randint(0, len(self.options)), 25)
        self.animation_running = True
        self.result_label.setText("抽签中...")
        self.draw_btn.setEnabled(False)
        self.animation_timer.start(50)


class SettingWindow(QDialog):
    def __init__(self,parent_window,desktop_size,color,audio_gen_module):
        super().__init__()
        self.parent_window:ChatGUI=parent_window
        self.audio_gen_module=audio_gen_module
        self.setWindowTitle('设置')
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(int(desktop_size.width()*0.2),int(desktop_size.height()*0.4))
        self.change_lan_btn=QPushButton("切换语言")
        self.change_lan_btn.clicked.connect(self.change_lan)
        self.switch_voice_btn=QPushButton("开启/关闭语言合成")
        self.switch_voice_btn.clicked.connect(self.switch_voice)
        self.clear_history_btn=QPushButton("清空聊天记录")
        self.clear_history_btn.clicked.connect(self.clear_history_msg)
        self.change_theme_color_btn=QPushButton("更改主题配色")
        self.change_theme_color_btn.clicked.connect(self.open_color_picker)
        self.change_reference_audio_btn=QPushButton("配置参考音频")
        self.change_reference_audio_btn.clicked.connect(self.open_change_ref_audio_window)
        current_chara:CharacterAttributes=self.parent_window.current_character
        if not current_chara.has_valid_voice_model():
            self.change_reference_audio_btn.setEnabled(False)
        self.switch_live2d_text_btn=QPushButton("开启/关闭Live2D界面文本显示")
        self.switch_live2d_text_btn.clicked.connect(self.parent_window.toggle_live2d_text_display)
        self.change_l2d_background_btn=QPushButton("切换Live2D背景")
        self.change_l2d_background_btn.clicked.connect(self.change_l2d_background)
        self.change_l2d_model_btn=QPushButton("更改当前角色Live2D模型")
        self.change_l2d_model_btn.clicked.connect(self.change_live2d_model_1)
        self.edit_l2d_layout_btn=QPushButton("调整Live2D位置/大小")
        self.edit_l2d_layout_btn.clicked.connect(self.edit_live2d_layout)
        self.change_l2d_fps_btn=QPushButton(f"Live2D渲染帧率：{self.parent_window.get_current_l2d_fps()}")
        self.change_l2d_fps_btn.clicked.connect(self.change_l2d_fps)
        self.convert_sakiko_state_btn=QPushButton("黑/白祥")
        self.convert_sakiko_state_btn.clicked.connect(self.convert_sakiko_state)
        self.sakiko_mask_btn=QPushButton("面具")
        self.sakiko_mask_btn.clicked.connect(self.sakiko_mask)
        setting_layout=QGridLayout()
        setting_layout.addWidget(self.change_lan_btn,0,0,1,2)
        setting_layout.addWidget(self.clear_history_btn,1,0,1,2)
        setting_layout.addWidget(self.switch_voice_btn,2,0,1,2)
        #setting_layout.addWidget(self.change_theme_color_btn,3,0,1,2)
        setting_layout.addWidget(self.switch_live2d_text_btn,4,0,1,2)
        setting_layout.addWidget(self.change_l2d_background_btn,5,0,1,2)
        setting_layout.addWidget(self.change_l2d_fps_btn,6,0,1,2)
        setting_group=QGroupBox("常用设置")
        setting_group.setLayout(setting_layout)
        setting_layout_2=QGridLayout()
        setting_layout_2.addWidget(self.change_theme_color_btn,0,0,1,2)
        setting_layout_2.addWidget(self.change_reference_audio_btn,1,0,1,2)
        setting_layout_2.addWidget(self.change_l2d_model_btn,2,0,1,2)
        setting_layout_2.addWidget(self.edit_l2d_layout_btn,3,0,1,2)
        setting_group_2=QGroupBox("角色与外观")
        setting_group_2.setLayout(setting_layout_2)
        layout=QVBoxLayout()
        sakiko_group=QGroupBox("祥子的状态")
        sakiko_layout=QHBoxLayout()
        sakiko_layout.addWidget(self.convert_sakiko_state_btn)
        sakiko_layout.addWidget(self.sakiko_mask_btn)
        sakiko_group.setLayout(sakiko_layout)
        layout.addWidget(setting_group)
        layout.addWidget(setting_group_2)
        layout.addWidget(sakiko_group)
        self.setLayout(layout)
        self.current_color=color
        self.setStyleSheet(dialogWindowDefaultCss)

    def change_lan(self):
        self.parent_window.run_input_command_text('l', 'setting_button')
    def clear_history_msg(self):
        self.parent_window.run_input_command_text('clr', 'setting_button')
    def convert_sakiko_state(self):
        self.parent_window.run_input_command_text('conv', 'setting_button')
    def sakiko_mask(self):
        self.parent_window.run_input_command_text('mask', 'setting_button')
    def switch_voice(self):
        self.parent_window.run_input_command_text('v', 'setting_button')
    def open_color_picker(self):
        color_picker = ColorPicker(self.parent_window_set_theme_color,self.current_color)
        color_picker.exec_()
    def parent_window_set_theme_color(self,color):
        self.parent_window.setStyleSheet(ThemeManager.generate_stylesheet(color))
        self.parent_window.set_btn_color(color)
    def open_change_ref_audio_window(self):
        change_ref_audio_window=ChangeReferenceAudioWindow(self.parent_window,self.audio_gen_module,QDesktopWidget().screenGeometry())
        change_ref_audio_window.exec_()
    def change_l2d_background(self):
        self.parent_window.run_input_command_text('change_l2d_background', 'setting_button')
    def change_l2d_fps(self):
        self.parent_window.run_input_command_text('switch_l2d_fps', 'setting_button')
        self.change_l2d_fps_btn.setText(f"Live2D渲染帧率：{self.parent_window.get_current_l2d_fps()}")
    def edit_live2d_layout(self) -> None:
        """通知主窗口切换 Live2D 布局编辑模式。"""
        self.parent_window.toggle_live2d_layout_edit()


    def change_live2d_model_1(self):
        current_char_folder_name=self.parent_window.current_character.character_folder_name
        change_l2d_model_window=ChangeL2DModelWindow(current_char_folder_name,self.change_live2d_model_2)
        change_l2d_model_window.exec_()
    def change_live2d_model_2(self,new_model_json):
        self.parent_window._send_l2d_model_payload(new_model_json)



class ChangeL2DModelWindow(QDialog):
    def __init__(self,current_char_folder_name,change_l2d_model_func):
        super().__init__()
        self.setWindowTitle('更改当前角色Live2D模型')
        screen=QDesktopWidget().screenGeometry()
        self.resize(int(screen.width()*0.25),int(screen.height()*0.4))
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.current_char_folder_name=current_char_folder_name
        self.change_l2d_model_func=change_l2d_model_func
        self.refresh_ui()
        self.setStyleSheet(dialogWindowDefaultCss)

    def refresh_ui(self):
        # 1. 【修复问题二】先清理旧的布局和控件
        if self.layout() is not None:
            # 这是一个标准的清除布局内所有组件的方法
            old_layout = self.layout()
            while old_layout.count():
                item = old_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()  # 销毁控件
            # 移除旧布局本身（通过将其父对象设为新的临时 Widget 然后销毁）
            QWidget().setLayout(old_layout)

        default_model_dir = os.path.join(f'../live2d_related/{self.current_char_folder_name}', 'live2D_model')
        default_live2d_json = self._find_preferred_model_json(default_model_dir)
        self.current_char_l2d_models = [{"model_name": "默认", "model_json_path": default_live2d_json}]
        self.find_extra_models(self.current_char_folder_name)
        layout = QVBoxLayout()
        title_layout=QHBoxLayout()
        title_label = QLabel("选择一个Live2D模型：")
        refresh_btn=QToolButton()
        refresh_btn.setIcon(QIcon("./icons/refresh.svg"))
        refresh_btn.clicked.connect(self.refresh_ui)  # noqa
        title_layout.addWidget(title_label)
        if self.current_char_folder_name != 'sakiko':
            import_btn = QPushButton("导入模型")
            import_btn.clicked.connect(self.import_l2d_model)  # noqa
            title_layout.addWidget(import_btn)
        title_layout.addWidget(refresh_btn)
        layout.addLayout(title_layout)

        display_group = QGroupBox(f"共有 {len(self.current_char_l2d_models)} 套服装")
        group_layout = QVBoxLayout()
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)  # 【关键！】必须设为True，让内部画布能自适应宽度
        scroll_area.setFrameShape(QFrame.NoFrame)  # 去除边框，让UI看起来更干净融合
        scroll_widget = QWidget()   #创建一个新的画布，所有的模型选项都放在这个画布上，滚动条滚动的就是这个画布
        display_layout = QVBoxLayout(scroll_widget)  #设置这个画布的布局为垂直布局，而这个layout将在下面填充每个模型的选项

        def delete_model_folder(path):
            import shutil
            try:
                reply = QMessageBox.question(self, '确认删除', f"确定要删除该模型吗？\n{path}",
                                             QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    shutil.rmtree(path)
                    self.refresh_ui()
            except Exception as e:
                logger.exception("删除 Live2D 模型文件夹失败")
        if self.current_char_folder_name != 'sakiko':
            for model in self.current_char_l2d_models:
                model_layout = QHBoxLayout()
                name_label = QLabel(model["model_name"])
                select_btn = QToolButton()
                select_btn.setText("选择")
                select_btn.clicked.connect(
                    lambda checked, path=model["model_json_path"]: self.change_l2d_model_func(path))  # noqa
                delete_btn = QToolButton()
                delete_btn.setIcon(QIcon("./icons/delete.svg"))
                delete_target_path = f'../live2d_related/{self.current_char_folder_name}/extra_model/{model["model_name"]}'
                delete_btn.clicked.connect(lambda checked=False, p=delete_target_path: delete_model_folder(p))
                model_layout.addWidget(name_label)
                model_layout.addWidget(select_btn)
                if model["model_name"] != "默认": #默认模型不允许删除
                    model_layout.addWidget(delete_btn)
                display_layout.addLayout(model_layout)
        else:
            label = QLabel("祥子暂时不能更换L2D模型。可手动修改sakiko文件夹内的模型文件。")
            display_layout.addWidget(label)
        scroll_area.setWidget(scroll_widget)    #把填充了模型选项的画布放进滚动区域，scroll_area只能这样设置子widget
        group_layout.addWidget(scroll_area)     #把滚动区域放进groupbox的布局，groupbox显示的就是这个滚动区域，滚动区域里是那个画布，画布上有每个模型的选项
        display_group.setLayout(group_layout)   #最后把groupbox放到主布局上
        layout.addWidget(display_group)
        open_downloader_btn = QPushButton("打开Live2D服装下载器")
        open_downloader_btn.clicked.connect(lambda _:MoreFunctionWindow.open_live2d_downloader())  # noqa
        layout.addWidget(open_downloader_btn)
        self.setLayout(layout)

    def import_l2d_model(self) -> None:
        """从本地选择 Live2D 模型 JSON 并导入当前角色的 extra_model 目录。"""
        selected_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "导入 Live2D 模型",
            "",
            "Live2D Model JSON (*.model3.json *.model.json);;JSON Files (*.json)",
        )
        if not selected_path:
            return

        try:
            result = import_live2d_model_to_extra_model(selected_path, self.current_char_folder_name)
        except Live2DModelImportError as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        except Exception as exc:
            logger.exception("导入 Live2D 模型失败")
            QMessageBox.warning(self, "导入失败", str(exc) or exc.__class__.__name__)
            return

        self.refresh_ui()
        QMessageBox.information(
            self,
            "导入成功",
            f"已导入模型：{result.model_name}。",
        )

    def find_extra_models(self,current_char_folder_name):
        base_path = f"../live2d_related/{current_char_folder_name}/extra_model"
        if not os.path.exists(base_path):
            os.makedirs(base_path)
            # print(f"\n提示：目录 {base_path} 不存在，已自动创建。加入更多live2D模型的方法为：在该文件夹中新建名为新模型名称的文件夹，然后在其中放入新模型的组成材料（.model.json结尾的配置文件、moc模型、.physics.json、mtn动作文件以及贴图文件）。\n")
            return

        model_dirs = [d for d in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, d))]

        for model_dir_name in model_dirs:
            # 构建这个模型文件夹的完整路径，例如 ../live2d_related/miku/extra_model/Miku
            model_dir_path = os.path.join(base_path, model_dir_name)
            full_path = self._find_preferred_model_json(model_dir_path)
            if full_path is not None:
                self.current_char_l2d_models.append({"model_name":model_dir_name,"model_json_path":full_path})

    @staticmethod
    def _find_preferred_model_json(model_dir_path: str) -> str | None:
        """查找目录中的 Live2D 模型 JSON，同目录同时存在 v2/v3 时优先 v3。"""
        if not os.path.isdir(model_dir_path):
            return None
        model2_paths: list[str] = []
        model3_paths: list[str] = []
        for current_dir, _dir_names, file_names in os.walk(model_dir_path):
            current_model2_paths: list[str] = []
            current_model3_paths: list[str] = []
            for file in sorted(file_names):
                full_path = os.path.join(current_dir, file)
                if file.endswith(".model3.json"):
                    current_model3_paths.append(full_path)
                elif file.endswith(".model.json"):
                    current_model2_paths.append(full_path)
            if current_model2_paths and current_model3_paths:
                logger.warning(
                    "Live2D 模型目录同时存在 .model.json 和 .model3.json，将优先使用 v3：%s",
                    current_dir,
                )
            model2_paths.extend(current_model2_paths)
            model3_paths.extend(current_model3_paths)
        if model3_paths:
            return model3_paths[0].replace("\\", "/")
        if model2_paths:
            return model2_paths[0].replace("\\", "/")
        return None

    @staticmethod
    def find_all_models() -> list[str]:
        all_models = []
        for char_folder in os.listdir("../live2d_related"):
            char_folder_path = os.path.join("../live2d_related", char_folder)
            if os.path.isdir(char_folder_path):
                full_path = ChangeL2DModelWindow._find_preferred_model_json(char_folder_path)
                if full_path is not None:
                    all_models.append(full_path)
        return all_models


class LegacyChangeReferenceAudioWindow(QDialog):
    def __init__(self,parent_window,audio_gen_module,desktop_size):
        super().__init__()
        self.parent_window: ChatGUI = parent_window
        self.audio_gen_module=audio_gen_module
        self.current_character: CharacterAttributes = self.parent_window.current_character
        self.setWindowTitle('更改当前角色参考音频')
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(int(desktop_size.width()*0.3),int(desktop_size.height()*0.4))
        layout=QVBoxLayout()
        self.audio_player = QMediaPlayer()
        self.audio_playlist = QMediaPlaylist()

        current_ref_audio_layout=QHBoxLayout()
        current_ref_audio_name=self.current_character.gptsovits_ref_audio
        if self.current_character.character_name == "祥子":
            current_ref_audio_name=self.audio_gen_module.ref_audio_file_black_sakiko if self.parent_window.dp_chat.sakiko_state else self.audio_gen_module.ref_audio_file_white_sakiko
        self.current_ref_audio_label=QLabel(f"当前参考音频:{os.path.basename(current_ref_audio_name or '')}")
        self.play_current_ref_audio_btn=QToolButton()
        self.play_current_ref_audio_btn.setIcon(QIcon("./icons/play.svg"))
        self.play_current_ref_audio_btn.clicked.connect(self.play_current_ref_audio)
        current_ref_audio_layout.addWidget(self.current_ref_audio_label)
        current_ref_audio_layout.addWidget(self.play_current_ref_audio_btn)
        layout.addLayout(current_ref_audio_layout)

        all_ref_audio_files=[]
        try:
            for file in os.listdir(f'../reference_audio/{self.current_character.character_folder_name}'):
                if file.endswith('.wav') or file.endswith('.mp3'):
                    all_ref_audio_files.append(file)
        except Exception:
            logger.exception("参考音频文件夹读取错误")
        select_new_ref_audio_group=QGroupBox("选择新的参考音频:")
        select_new_ref_audio_layout=QVBoxLayout()
        for ref_audio_file in all_ref_audio_files:
            single_ref_audio_layout=QHBoxLayout()
            name_label=QLabel(os.path.basename(ref_audio_file))
            play_btn=QToolButton()
            play_btn.setIcon(QIcon("./icons/play.svg"))
            play_btn.clicked.connect(lambda checked, path=os.path.join(f'../reference_audio/{self.current_character.character_folder_name}',ref_audio_file): self.play_ref_audio(path))
            select_btn=QToolButton()
            select_btn.setText("选择")
            select_btn.clicked.connect(lambda checked, path=os.path.join(f'../reference_audio/{self.current_character.character_folder_name}',ref_audio_file): self.replace_ref_audio(path))
            single_ref_audio_layout.addWidget(name_label)
            single_ref_audio_layout.addWidget(play_btn)
            single_ref_audio_layout.addWidget(select_btn)
            select_new_ref_audio_layout.addLayout(single_ref_audio_layout)
        select_new_ref_audio_group.setLayout(select_new_ref_audio_layout)
        layout.addWidget(select_new_ref_audio_group)

        change_ref_text_group=QGroupBox("不要忘记修改新参考音频对应的文本 ↓")
        new_ref_text_input_layout=QHBoxLayout()
        self.new_ref_text_input=QLineEdit()
        self.new_ref_text_input.setPlaceholderText("在此输入新参考音频对应的文本")
        if self.current_character.character_name == "祥子":
            if self.parent_window.dp_chat.sakiko_state:
                with open(self.audio_gen_module.ref_text_file_black_sakiko,'r',encoding='utf-8') as f:
                    current_ref_text=f.read().strip()
            else:
                with open(self.audio_gen_module.ref_text_file_white_sakiko,'r',encoding='utf-8') as f:
                    current_ref_text=f.read().strip()
        else:
            ref_text_path = (
                self.current_character.gptsovits_ref_audio_text
                or os.path.join(
                    "../reference_audio",
                    self.current_character.character_folder_name,
                    "reference_text.txt",
                )
            )
            if os.path.exists(ref_text_path):
                with open(ref_text_path, 'r', encoding='utf-8') as f:
                    current_ref_text = f.read().strip()
            else:
                current_ref_text = ""
        self.new_ref_text_input.setText(current_ref_text)
        self.new_ref_text_input.returnPressed.connect(self.change_ref_text)
        self.new_ref_text_input_confirm_btn=QPushButton("确认修改")
        self.new_ref_text_input_confirm_btn.clicked.connect(self.change_ref_text)
        self.change_ref_text_success_label=QLabel("")
        new_ref_text_input_layout.addWidget(self.new_ref_text_input)
        new_ref_text_input_layout.addWidget(self.new_ref_text_input_confirm_btn)
        new_ref_text_input_layout.addWidget(self.change_ref_text_success_label)
        change_ref_text_group.setLayout(new_ref_text_input_layout)
        layout.addWidget(change_ref_text_group)

        self.setLayout(layout)
        self.setStyleSheet(dialogWindowDefaultCss)

    def play_current_ref_audio(self):
        current_ref_audio_path=self.current_character.gptsovits_ref_audio
        if self.current_character.character_name == "祥子":
            current_ref_audio_path=self.audio_gen_module.ref_audio_file_black_sakiko if self.parent_window.dp_chat.sakiko_state else self.audio_gen_module.ref_audio_file_white_sakiko
        if current_ref_audio_path:
            self.play_ref_audio(current_ref_audio_path)

    def play_ref_audio(self,ref_audio_path):
        audio_path=os.path.abspath(ref_audio_path)
        url=QUrl.fromLocalFile(audio_path)
        self.audio_playlist.clear()
        self.audio_playlist.addMedia(QMediaContent(url))
        self.audio_playlist.setCurrentIndex(0)
        self.audio_player.setPlaylist(self.audio_playlist)
        self.audio_player.play()

    def replace_ref_audio(self,new_ref_audio_file):
        try:
            if self.current_character.character_name == "祥子":
                if self.parent_window.dp_chat.sakiko_state:
                    self.audio_gen_module.ref_audio_file_black_sakiko=new_ref_audio_file
                    with open('../reference_audio/sakiko/default_ref_audio_black.txt','w',encoding='utf-8') as f:
                        f.write(new_ref_audio_file)
                else:
                    self.audio_gen_module.ref_audio_file_white_sakiko=new_ref_audio_file
                    with open('../reference_audio/sakiko/default_ref_audio_white.txt','w',encoding='utf-8') as f:
                        f.write(new_ref_audio_file)
            else:
                self.current_character.gptsovits_ref_audio=new_ref_audio_file
                with open(f'../reference_audio/{self.current_character.character_folder_name}/default_ref_audio.txt','w',encoding='utf-8') as f:
                    f.write(new_ref_audio_file)
        except Exception:
            logger.exception("更改参考音频出现错误")
        self.current_ref_audio_label.setText(f"当前参考音频:{os.path.basename(new_ref_audio_file)}")

    def change_ref_text(self):
        new_ref_text=self.new_ref_text_input.text().strip()
        if new_ref_text:
            if self.current_character.character_name == "祥子":
                if self.parent_window.dp_chat.sakiko_state:
                    with open(self.audio_gen_module.ref_text_file_black_sakiko,'w',encoding='utf-8') as f:
                        f.write(new_ref_text)
                else:
                    with open(self.audio_gen_module.ref_text_file_white_sakiko,'w',encoding='utf-8') as f:
                        f.write(new_ref_text)
            else:
                ref_text_path = (
                    self.current_character.gptsovits_ref_audio_text
                    or os.path.join(
                        "../reference_audio",
                        self.current_character.character_folder_name,
                        "reference_text.txt",
                    )
                )
                with open(ref_text_path, 'w', encoding='utf-8') as f:
                    f.write(new_ref_text)
                self.current_character.gptsovits_ref_audio_text = ref_text_path
            self.change_ref_text_success_label.setText("修改成功!")
        else:
            self.change_ref_text_success_label.setText("文本不能为空!")



class ChangeReferenceAudioWindow(QDialog):
    EMOTION_LABELS = {
        "happiness": "happiness（开心）",
        "sadness": "sadness（难过）",
        "anger": "anger（生气）",
        "disgust": "disgust（反感）",
        "like": "like（喜欢）",
        "surprise": "surprise（惊讶）",
        "fear": "fear（害怕）",
    }

    def __init__(self, parent_window, audio_gen_module, desktop_size):
        super().__init__()
        self.parent_window: ChatGUI = parent_window
        self.audio_gen_module = audio_gen_module
        self.current_character: CharacterAttributes = self.parent_window.current_character
        self._initializing = True
        self.audio_player = QMediaPlayer()
        self.audio_playlist = QMediaPlaylist()
        self.audio_refs: dict[str, str] = {}
        self.text_refs: dict[str, str] = {}
        self.sakiko_refs: dict[str, dict[str, str]] = {}
        self.current_key: str = "black" if self.current_character.is_sakiko else "happiness"
        self.audio_files: list[str] = []

        self.setWindowTitle("配置当前角色参考音频")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(int(desktop_size.width() * 0.62), int(desktop_size.height() * 0.62))
        self._load_config_from_disk()
        self._build_ui()
        self._refresh_mode_list(self.current_key)
        self._refresh_library()
        self._load_current_mode_to_editor()
        self._initializing = False

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.setObjectName("referenceAudioDialog")

        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel(f"角色：{self.current_character.character_name}", self))
        top_layout.addStretch(1)
        top_layout.addWidget(QLabel("参考音频语言", self))
        self.language_combo = QComboBox(self)
        self.language_combo.setObjectName("referenceLanguageCombo")
        self.language_combo.addItems(ref_audio_language_list)
        if self.current_character.gptsovits_ref_audio_lan in ref_audio_language_list:
            self.language_combo.setCurrentText(self.current_character.gptsovits_ref_audio_lan)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        top_layout.addWidget(self.language_combo)
        layout.addLayout(top_layout)

        body_layout = QHBoxLayout()
        mode_group = QGroupBox("所有emotion标签" if not self.current_character.is_sakiko else "所有状态", self)
        mode_layout = QVBoxLayout(mode_group)
        self.mode_list = QListWidget(self)
        self.mode_list.setObjectName("referenceModeList")
        self.mode_list.setMinimumWidth(170)
        self.mode_list.setMaximumWidth(220)
        self.mode_list.currentItemChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self.mode_list)
        body_layout.addWidget(mode_group)

        center_group = QGroupBox("当前emotion的配置" if not self.current_character.is_sakiko else "当前状态的配置", self)
        center_group.setObjectName("referenceCenterGroup")
        center_layout = QVBoxLayout(center_group)
        self.current_mode_label = QLabel("", self)
        self.current_audio_label = QLabel("", self)
        self.current_audio_label.setWordWrap(True)
        play_current_btn = QPushButton("播放当前音频", self)
        play_current_btn.clicked.connect(self.play_current_ref_audio)
        self.ref_text_edit = QPlainTextEdit(self)
        self.ref_text_edit.setObjectName("referenceTextEdit")
        self.ref_text_edit.setMinimumHeight(160)
        confirm_text_btn = QPushButton("确认修改", self)
        confirm_text_btn.clicked.connect(self.confirm_text_change)
        apply_layout = QHBoxLayout()
        apply_all_btn = QPushButton("将本配置应用到全部emotion", self)
        apply_all_btn.clicked.connect(self.apply_current_to_all)
        help_label = QLabel("?", self)
        help_label.setObjectName("referenceHelpLabel")
        help_label.setToolTip("把当前情绪/状态的参考音频和参考文本复制到全部项。适合你只想使用同一套参考音频的情况。")
        apply_layout.addWidget(apply_all_btn)
        apply_layout.addWidget(help_label)
        apply_layout.addStretch(1)
        center_layout.addWidget(self.current_mode_label)
        center_layout.addWidget(self.current_audio_label)
        center_layout.addWidget(play_current_btn)
        center_layout.addWidget(QLabel("参考文本", self))
        center_layout.addWidget(self.ref_text_edit)
        center_layout.addWidget(confirm_text_btn)
        center_layout.addLayout(apply_layout)
        body_layout.addWidget(center_group, 2)

        library_group = QGroupBox("候选音频素材库", self)
        library_group.setObjectName("referenceLibraryGroup")
        library_layout = QVBoxLayout(library_group)
        self.search_input = QLineEdit(self)
        self.search_input.setObjectName("referenceSearchInput")
        self.search_input.setPlaceholderText("按文件名搜索")
        self.search_input.textChanged.connect(self._refresh_library)
        self.library_container = QWidget(self)
        self.library_layout = QVBoxLayout(self.library_container)
        self.library_layout.setContentsMargins(0, 0, 0, 0)
        self.library_layout.addStretch(1)
        scroll_area = QScrollArea(self)
        scroll_area.setObjectName("referenceLibraryScroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.library_container)
        library_layout.addWidget(self.search_input)
        library_layout.addWidget(scroll_area)
        body_layout.addWidget(library_group, 2)
        layout.addLayout(body_layout)

        self.toast_label = QLabel("", self)
        self.toast_label.setStyleSheet("color: #2E7D32;")
        layout.addWidget(self.toast_label)
        self._apply_dialog_styles()

    def _mode_entries(self) -> list[tuple[str, str]]:
        if self.current_character.is_sakiko:
            return [("black", "黑祥"), ("white", "白祥")]
        return [(key, self.EMOTION_LABELS.get(key, key)) for key in EMOTION_REFERENCE_KEYS]

    def _load_config_from_disk(self) -> None:
        if self.current_character.is_sakiko:
            self.sakiko_refs = {
                "black": {
                    "audio": self._read_sakiko_audio_path("black"),
                    "text": self._read_text_file(self.audio_gen_module.ref_text_file_black_sakiko),
                },
                "white": {
                    "audio": self._read_sakiko_audio_path("white"),
                    "text": self._read_text_file(self.audio_gen_module.ref_text_file_white_sakiko),
                },
            }
        else:
            self.current_character.reload_emotion_reference_config_from_disk(migrate_if_missing=True)
            self.audio_refs = self.current_character._emotion_dict(self.current_character.gptsovits_ref_audio)
            self.text_refs = self.current_character._emotion_dict(self.current_character.gptsovits_ref_audio_text)
            for key in EMOTION_REFERENCE_KEYS:
                self.audio_refs.setdefault(key, "")
                self.text_refs.setdefault(key, "")
        self._scan_audio_files()

    def _scan_audio_files(self) -> None:
        ref_dir = os.path.join("../reference_audio", self.current_character.character_folder_name)
        self.audio_files = []
        try:
            for file in sorted(os.listdir(ref_dir)):
                if file.lower().endswith((".wav", ".mp3")):
                    self.audio_files.append(os.path.join(ref_dir, file))
        except Exception:
            logger.exception("参考音频文件夹读取错误")

    def _apply_dialog_styles(self) -> None:
        self.setStyleSheet(
            dialogWindowDefaultCss
            + """
            QDialog#referenceAudioDialog {
                background: #F4F7FB;
            }
            QGroupBox {
                border: 1px solid #D6E0EE;
                border-radius: 6px;
                margin-top: 14px;
                padding: 9px 7px 7px 7px;
                color: #5C82BD;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QListWidget#referenceModeList {
                background: #FFFFFF;
                border: 1px solid #D6E0EE;
                border-radius: 5px;
                padding: 4px;
                outline: none;
            }
            QListWidget#referenceModeList::item {
                min-height: 24px;
                padding: 3px 6px;
                border-radius: 4px;
            }
            QListWidget#referenceModeList::item:selected {
                background: #DCE8F8;
                color: #315F9D;
            }
            QComboBox#referenceLanguageCombo,
            QLineEdit#referenceSearchInput,
            QPlainTextEdit#referenceTextEdit {
                background: #FFFFFF;
                border: 1px solid #C8D5E6;
                border-radius: 5px;
                padding: 5px 7px;
                color: #3F6EA8;
                selection-background-color: #BFD4F2;
            }
            QComboBox#referenceLanguageCombo {
                min-width: 130px;
                padding-right: 24px;
            }
            QComboBox#referenceLanguageCombo::drop-down {
                width: 24px;
                border-left: 1px solid #C8D5E6;
                border-top-right-radius: 5px;
                border-bottom-right-radius: 5px;
                background: #EEF4FB;
            }
            QComboBox#referenceLanguageCombo QAbstractItemView {
                background: #FFFFFF;
                border: 1px solid #C8D5E6;
                selection-background-color: #DCE8F8;
                color: #3F6EA8;
                outline: none;
            }
            QScrollArea#referenceLibraryScroll {
                background: #FFFFFF;
                border: 1px solid #D6E0EE;
                border-radius: 5px;
            }
            QWidget#referenceAudioCard {
                background: #FFFFFF;
                border: none;
            }
            QLabel#referenceAudioFileName {
                color: #4F7DB7;
            }
            QFrame#referenceAudioSeparator {
                color: #E2EAF4;
                background: #E2EAF4;
                max-height: 1px;
            }
            QPushButton, QToolButton {
                border: 1px solid #C8D5E6;
                border-radius: 5px;
                padding: 5px 10px;
                background: #FFFFFF;
                color: #4D79B2;
            }
            QPushButton:hover, QToolButton:hover {
                background: #EEF5FF;
                border-color: #9EBBE0;
            }
            QLabel#referenceHelpLabel {
                color: #5C82BD;
                padding: 0 6px;
                font-weight: 600;
            }
            QScrollBar:vertical,
            QScrollBar:horizontal {
                background: #EEF4FB;
                border: 1px solid #D6E0EE;
                border-radius: 4px;
                margin: 0;
            }
            QScrollBar:vertical {
                width: 11px;
            }
            QScrollBar:horizontal {
                height: 11px;
            }
            QScrollBar::handle:vertical,
            QScrollBar::handle:horizontal {
                background: #B8CCE8;
                border-radius: 4px;
                min-height: 24px;
                min-width: 24px;
            }
            QScrollBar::handle:vertical:hover,
            QScrollBar::handle:horizontal:hover {
                background: #95B5DD;
            }
            QScrollBar::add-line,
            QScrollBar::sub-line {
                width: 0;
                height: 0;
                border: none;
                background: transparent;
            }
            """
        )

    def _read_sakiko_audio_path(self, key: str) -> str:
        file_name = "default_ref_audio_black.txt" if key == "black" else "default_ref_audio_white.txt"
        fallback = (
            self.audio_gen_module.ref_audio_file_black_sakiko
            if key == "black"
            else self.audio_gen_module.ref_audio_file_white_sakiko
        )
        path_file = os.path.join("../reference_audio", "sakiko", file_name)
        if os.path.exists(path_file):
            try:
                with open(path_file, "r", encoding="utf-8") as file:
                    candidate = file.read().strip()
                if candidate:
                    return candidate
            except Exception:
                logger.exception("读取祥子参考音频路径失败：%s", path_file)
        return fallback

    @staticmethod
    def _read_text_file(path: str | None) -> str:
        if not path or not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as file:
                return file.read().strip()
        except Exception:
            logger.exception("读取参考文本失败：%s", path)
            return ""

    @staticmethod
    def _write_text_file(path: str, text: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as file:
                file.write(text)
            return True
        except Exception:
            logger.exception("写入参考文本失败：%s", path)
            return False

    def _current_audio(self) -> str:
        if self.current_character.is_sakiko:
            return self.sakiko_refs.get(self.current_key, {}).get("audio", "")
        return self.audio_refs.get(self.current_key, "")

    def _current_text(self) -> str:
        if self.current_character.is_sakiko:
            return self.sakiko_refs.get(self.current_key, {}).get("text", "")
        return self.text_refs.get(self.current_key, "")

    def _set_current_audio(self, path: str) -> bool:
        if self.current_character.is_sakiko:
            self.sakiko_refs.setdefault(self.current_key, {})["audio"] = path
            return self._save_sakiko_key(self.current_key)
        self.audio_refs[self.current_key] = path
        return self._save_non_sakiko()

    def _set_current_text(self, text: str) -> bool:
        if self.current_character.is_sakiko:
            self.sakiko_refs.setdefault(self.current_key, {})["text"] = text
            return self._save_sakiko_key(self.current_key)
        self.text_refs[self.current_key] = text
        return self._save_non_sakiko()

    def _save_non_sakiko(self) -> bool:
        ok = self.current_character.save_emotion_reference_config(self.audio_refs, self.text_refs)
        if ok:
            self.audio_refs = self.current_character._emotion_dict(self.current_character.gptsovits_ref_audio)
            self.text_refs = self.current_character._emotion_dict(self.current_character.gptsovits_ref_audio_text)
        return ok

    def _save_sakiko_key(self, key: str) -> bool:
        refs = self.sakiko_refs.get(key, {})
        audio = refs.get("audio", "")
        text = refs.get("text", "")
        if key == "black":
            audio_path_file = "../reference_audio/sakiko/default_ref_audio_black.txt"
            text_path = self.audio_gen_module.ref_text_file_black_sakiko
            self.audio_gen_module.ref_audio_file_black_sakiko = audio
        else:
            audio_path_file = "../reference_audio/sakiko/default_ref_audio_white.txt"
            text_path = self.audio_gen_module.ref_text_file_white_sakiko
            self.audio_gen_module.ref_audio_file_white_sakiko = audio
        audio_ok = self._write_text_file(audio_path_file, audio)
        text_ok = self._write_text_file(text_path, text)
        return audio_ok and text_ok

    def _refresh_mode_list(self, select_key: str | None = None) -> None:
        select_key = select_key or self.current_key
        self.mode_list.blockSignals(True)
        self.mode_list.clear()
        selected_row = 0
        for row, (key, label) in enumerate(self._mode_entries()):
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            self.mode_list.addItem(item)
            if key == select_key:
                selected_row = row
        self.mode_list.setCurrentRow(selected_row)
        self.mode_list.blockSignals(False)

    def _is_mode_valid(self, key: str) -> bool:
        if self.current_character.is_sakiko:
            refs = self.sakiko_refs.get(key, {})
            return bool(refs.get("text", "").strip()) and os.path.exists(refs.get("audio", ""))
        return bool(self.text_refs.get(key, "").strip()) and os.path.exists(self.audio_refs.get(key, ""))

    def _on_mode_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        if current is None:
            return
        key = current.data(Qt.UserRole)
        if isinstance(key, str):
            self.current_key = key
            self._load_current_mode_to_editor()

    def _load_current_mode_to_editor(self) -> None:
        label = dict(self._mode_entries()).get(self.current_key, self.current_key)
        audio_path = self._current_audio()
        self.current_mode_label.setText(f"当前项：{label}")
        self.current_audio_label.setText(f"当前音频：{os.path.basename(audio_path) if audio_path else '无'}")
        self.current_audio_label.setToolTip(audio_path)
        self.ref_text_edit.setPlainText(self._current_text())

    def _refresh_library(self, *_args) -> None:
        while self.library_layout.count():
            item = self.library_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        keyword = self.search_input.text().strip().lower() if hasattr(self, "search_input") else ""
        for path in self.audio_files:
            if keyword and keyword not in os.path.basename(path).lower():
                continue
            card = QWidget(self)
            card.setObjectName("referenceAudioCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 7, 8, 7)
            card_layout.setSpacing(6)

            name_label = QLabel(os.path.basename(path), card)
            name_label.setObjectName("referenceAudioFileName")
            name_label.setToolTip(path)
            name_label.setWordWrap(True)

            action_layout = QHBoxLayout()
            play_btn = QToolButton(card)
            play_btn.setIcon(QIcon("./icons/play.svg"))
            play_btn.clicked.connect(lambda _checked=False, p=path: self.play_ref_audio(p))
            use_btn = QPushButton("使用", card)
            use_btn.clicked.connect(lambda _checked=False, p=path: self.use_audio_for_current(p))
            action_layout.addWidget(play_btn)
            action_layout.addWidget(use_btn)
            action_layout.addStretch(1)

            card_layout.addWidget(name_label)
            card_layout.addLayout(action_layout)
            self.library_layout.addWidget(card)

            separator = QFrame(self)
            separator.setFrameShape(QFrame.HLine)
            separator.setObjectName("referenceAudioSeparator")
            self.library_layout.addWidget(separator)
        self.library_layout.addStretch(1)

    def _on_language_changed(self, *_args) -> None:
        if self._initializing:
            return
        language = self.language_combo.currentText()
        if self.current_character.write_reference_audio_language(language):
            self._show_toast("参考音频语言已更新")
        else:
            self._show_toast("参考音频语言保存失败", success=False)

    def play_current_ref_audio(self) -> None:
        audio_path = self._current_audio()
        if audio_path:
            self.play_ref_audio(audio_path)

    def play_ref_audio(self, ref_audio_path: str) -> None:
        audio_path = os.path.abspath(ref_audio_path)
        url = QUrl.fromLocalFile(audio_path)
        self.audio_playlist.clear()
        self.audio_playlist.addMedia(QMediaContent(url))
        self.audio_playlist.setCurrentIndex(0)
        self.audio_player.setPlaylist(self.audio_playlist)
        self.audio_player.play()

    def use_audio_for_current(self, path: str) -> None:
        if self._set_current_audio(path):
            self._refresh_mode_list(self.current_key)
            self._load_current_mode_to_editor()
            self._refresh_library()
            self._show_toast(f"已更新 {dict(self._mode_entries()).get(self.current_key, self.current_key)} 参考音频")
        else:
            self._show_toast("参考音频保存失败", success=False)

    def confirm_text_change(self) -> None:
        text = self.ref_text_edit.toPlainText().strip()
        if not text:
            self._show_toast("参考文本不能为空", success=False)
            return
        if self._set_current_text(text):
            self._refresh_mode_list(self.current_key)
            self._refresh_library()
            self._show_toast("参考文本已保存")
        else:
            self._show_toast("参考文本保存失败", success=False)

    def apply_current_to_all(self) -> None:
        audio = self._current_audio()
        text = self.ref_text_edit.toPlainText().strip()
        if not audio or not text:
            self._show_toast("当前音频或参考文本为空，无法应用到全部", success=False)
            return
        if self.current_character.is_sakiko:
            for key in ("black", "white"):
                self.sakiko_refs.setdefault(key, {})["audio"] = audio
                self.sakiko_refs.setdefault(key, {})["text"] = text
            ok = self._save_sakiko_key("black") and self._save_sakiko_key("white")
        else:
            for key in EMOTION_REFERENCE_KEYS:
                self.audio_refs[key] = audio
                self.text_refs[key] = text
            ok = self._save_non_sakiko()
        if ok:
            self._refresh_mode_list(self.current_key)
            self._load_current_mode_to_editor()
            self._refresh_library()
            self._show_toast("已应用到全部")
        else:
            self._show_toast("应用到全部失败", success=False)

    def _show_toast(self, message: str, success: bool = True) -> None:
        self.toast_label.setStyleSheet("color: #2E7D32;" if success else "color: #B00020;")
        self.toast_label.setText(message)
        QTimer.singleShot(2200, self.toast_label.clear)


class ColorPicker(QDialog):
    def __init__(self,parent_window_set_color_fun,current_color="#7799CC"):
        super().__init__()
        self.setStyleSheet(dialogWindowDefaultCss)
        self.current_color:str=current_color
        self.screen = QDesktopWidget().screenGeometry()
        self.parent_window_set_theme_color=parent_window_set_color_fun
        self.initUI()

    def initUI(self):
        self.setWindowTitle('主题色选择')

        layout = QVBoxLayout()
        # 显示当前颜色的标签
        preset_group=QGroupBox("预设BangDream主题色")
        preset_layout=QGridLayout()
        preset_layout.setHorizontalSpacing(int(self.screen.height() * 0.04 * 0.25))
        preset_layout.setVerticalSpacing(int(self.screen.height() * 0.05 * 0.25))
        for i,(char_name,info) in enumerate(char_info_json.items()):
            btn=QToolButton()
            btn.setText(char_name)
            btn.setFixedSize(int(self.screen.height() * 0.06), int(self.screen.height() * 0.075))
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.clicked.connect(lambda checked, col=info["theme_color"]: self.set_theme_color(col))
            if os.path.exists(f'./char_headprof/{char_name}.png'):
                btn.setIcon(QIcon(f'./char_headprof/{char_name}.png'))
                btn.setIconSize(
                    QSize(int(self.screen.height() * 0.06 * 0.7), int(self.screen.height() * 0.075 * 0.7)))
            btn.setStyleSheet(f"""
                    QToolButton {{
                        background-color: {info["theme_color"]}; 
                        color: #FFFFFF;  /* 强制文字白色，防止和背景混色 */
                        font-weight: bold;
                        border-radius: 6px; /* 加点圆角更好看 */
                    }}
                    QToolButton:hover {{
                        border: 2px solid white; /* 悬停加个边框 */
                    }}
                """)
            preset_layout.addWidget(btn,i//10,i%10)
        preset_group.setLayout(preset_layout)
        layout.addWidget(preset_group)

        self.lbl_display = QLabel(f"当前主题色: {self.current_color}")
        self.lbl_display.setStyleSheet(f"color: {self.current_color}")
        layout.addWidget(self.lbl_display)
        # 触发按钮
        self.btn_pick = QPushButton('打开调色盘')
        self.btn_pick.setStyleSheet("padding: 10px;")
        self.btn_pick.clicked.connect(self.show_color_dialog)
        layout.addWidget(self.btn_pick)

        self.setLayout(layout)

    def show_color_dialog(self):
        col = QColorDialog.getColor(parent=self, title="选择颜色",initial=QColor(self.current_color),options=QColorDialog.DontUseNativeDialog)
        if col.isValid():
            hex_code = col.name()
            self.set_theme_color(hex_code)

    def set_theme_color(self,color):
        self.current_color=color
        self.lbl_display.setText(f"当前主题色: {self.current_color}")
        self.lbl_display.setStyleSheet(f"color: {self.current_color}")
        self.parent_window_set_theme_color(self.current_color)

class AudioRegenThread(QThread):
    finished_signal = pyqtSignal(str, int)  # (新音频路径, msg_index)

    def __init__(self, audio_gen, text, character, sakiko_state, audio_language_choice, msg_index, emotion=None):
        super().__init__()
        self.audio_gen = audio_gen
        self.text = text
        self.character = character
        self.sakiko_state = sakiko_state
        self.audio_language_choice = audio_language_choice
        self.msg_index = msg_index
        self.emotion = emotion

    def run(self):
        new_audio_path = self.audio_gen.generate_audio_sync(
            self.text,
            self.character,
            self.sakiko_state,
            self.audio_language_choice,
            emotion=self.emotion,
        )
        self.finished_signal.emit(new_audio_path, self.msg_index)

class DragDropListWidget(QListWidget):
    """
    支持拖拽排序并在顺序变化后发出信号的对话列表。
    """
    order_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QListView.DragDropMode.InternalMove)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.order_changed.emit()


class MessageEditDialog(QDialog):
    """编辑普通聊天单条消息正文和翻译的对话框。"""

    def __init__(
            self,
            message: Message,
            parent: QWidget | None = None,
    ) -> None:
        """根据消息类型创建对应的编辑字段。"""
        super().__init__(parent)
        self.message = message
        self.is_user_message = message.character_name == "User"
        self.setWindowTitle("编辑用户消息" if self.is_user_message else "编辑角色回复")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        text_label = QLabel("正文", self)
        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setPlainText(message.text)
        self.text_edit.setMinimumHeight(120)
        layout.addWidget(text_label)
        layout.addWidget(self.text_edit)

        self.translation_edit: QPlainTextEdit | None = None
        if not self.is_user_message:
            translation_label = QLabel("翻译", self)
            self.translation_edit = QPlainTextEdit(self)
            self.translation_edit.setPlainText(message.translation)
            self.translation_edit.setMinimumHeight(90)
            layout.addWidget(translation_label)
            layout.addWidget(self.translation_edit)

            if message.audio_path and message.audio_path != "NO_AUDIO":
                audio_notice = QLabel("修改正文不会自动重新生成音频，原语音可能与新文本不一致。", self)
                audio_notice.setWordWrap(True)
                audio_notice.setStyleSheet("color: #8A6D3B;")
                layout.addWidget(audio_notice)

        self.validation_label = QLabel("", self)
        self.validation_label.setWordWrap(True)
        self.validation_label.setStyleSheet("color: #B00020;")
        layout.addWidget(self.validation_label)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        cancel_button = QPushButton("取消", self)
        save_button = QPushButton("保存", self)
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self._accept_if_valid)
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(save_button)
        layout.addLayout(button_layout)

    def edited_text(self) -> str:
        """返回编辑后的正文。"""
        return self.text_edit.toPlainText().strip()

    def edited_translation(self) -> str:
        """返回编辑后的翻译。"""
        if self.translation_edit is None:
            return self.message.translation
        return self.translation_edit.toPlainText().strip()

    def _accept_if_valid(self) -> None:
        """校验正文非空后接受对话框。"""
        if not self.edited_text():
            self.validation_label.setText("正文不能为空。")
            return
        self.accept()


class NewSingleChatDialog(QDialog):
    """
    创建普通单角色对话的弹窗。
    """

    DEFAULT_PERSONA_NOTICE = "选择“无身份”时，AI 不会知道任何关于你的信息。"

    def __init__(
            self,
            character_list: list[CharacterAttributes],
            parent: QWidget | None = None,
    ) -> None:
        """创建包含 AI 角色与用户人设选择的新对话弹窗。"""
        super().__init__(parent)
        self.setWindowTitle("新建对话")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.character_list = character_list
        self.user_personas = self._reload_user_personas()
        screen = QDesktopWidget().screenGeometry()
        self.setMinimumSize(
            int(screen.width() * 0.28),
            int(screen.height() * 0.18),
        )

        layout = QVBoxLayout(self)
        form_layout = QGridLayout()
        self.chat_name_edit = QLineEdit()
        self.chat_name_edit.setPlaceholderText("可选，例如：练习后的闲聊")
        self.character_combo = QComboBox()
        combo_view = QListView()
        combo_view.setObjectName("singleChatCharacterComboView")
        combo_view.setFrameShape(QFrame.NoFrame)
        self.character_combo.setView(combo_view)
        self.character_combo.setItemDelegate(QStyledItemDelegate())
        for one_character in self.character_list:
            self.character_combo.addItem(one_character.character_name)

        form_layout.addWidget(QLabel("对话名称"), 0, 0)
        form_layout.addWidget(self.chat_name_edit, 0, 1)
        form_layout.addWidget(QLabel("角色"), 1, 0)
        form_layout.addWidget(self.character_combo, 1, 1)
        layout.addLayout(form_layout)

        self.persona_toggle_button = QToolButton(self)
        self.persona_toggle_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.persona_toggle_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.persona_toggle_button.clicked.connect(self._toggle_persona_section)
        layout.addWidget(self.persona_toggle_button)

        self.persona_panel = QWidget(self)
        persona_layout = QVBoxLayout(self.persona_panel)
        persona_layout.setContentsMargins(0, 0, 0, 0)

        self.persona_combo = QComboBox(self.persona_panel)
        persona_combo_view = QListView()
        persona_combo_view.setObjectName("singleChatCharacterComboView")
        persona_combo_view.setFrameShape(QFrame.NoFrame)
        self.persona_combo.setView(persona_combo_view)
        self.persona_combo.setItemDelegate(QStyledItemDelegate())
        self._populate_persona_combo()
        self.persona_combo.currentIndexChanged.connect(self._refresh_persona_preview)
        self.character_combo.currentIndexChanged.connect(self._refresh_persona_preview)

        self.persona_open_layout = QHBoxLayout()
        self.persona_view_hint = QLabel("创建对话后无法再次更改对话身份", self.persona_panel)
        self.persona_edit_button = QPushButton("编辑身份信息", self.persona_panel)
        self.persona_edit_button.clicked.connect(self._open_persona_editor)
        self.persona_open_layout.addWidget(self.persona_view_hint)
        self.persona_open_layout.addWidget(self.persona_edit_button)

        self.persona_preview_label = QLabel("身份描述预览", self.persona_panel)
        self.persona_preview = QTextBrowser(self.persona_panel)
        self.persona_preview.setFixedHeight(100)
        self.persona_preview.setOpenExternalLinks(False)
        self.persona_preview.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )

        self.persona_notice = QLabel(self.persona_panel)
        self.persona_notice.setWordWrap(True)
        self.persona_notice.setStyleSheet("color: #8A6D3B;")

        persona_layout.addWidget(self.persona_combo)
        persona_layout.addLayout(self.persona_open_layout)
        persona_layout.addWidget(self.persona_preview_label)
        persona_layout.addWidget(self.persona_preview)
        persona_layout.addWidget(self.persona_notice)
        layout.addWidget(self.persona_panel)

        button_layout = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        ok_btn = QPushButton("创建")
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self._accept_if_valid)
        button_layout.addStretch(1)
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(ok_btn)
        layout.addLayout(button_layout)
        self.setStyleSheet(SINGLE_CHAT_DIALOG_CSS)
        for combo in (self.character_combo, self.persona_combo):
            combo.setStyleSheet(SINGLE_CHAT_COMBO_CSS)
            combo.view().setStyleSheet(SINGLE_CHAT_COMBO_VIEW_CSS)

        expanded = bool(d_sakiko_config.user_persona_section_expanded.value)
        self.persona_section_expanded = True
        self._set_persona_section_expanded(expanded, save=False)
        self._refresh_persona_preview()

    def showEvent(self, event: QShowEvent) -> None:
        """显示弹窗时根据用户人设区域的当前状态更新布局尺寸。"""
        super().showEvent(event)
        if self.persona_section_expanded:
            self._ensure_expanded_persona_layout()
        else:
            self.adjustSize()

    @staticmethod
    def _reload_user_personas() -> list[CharacterAttributes]:
        """从磁盘读取最新配置，并刷新角色管理器中的用户人设。"""
        d_sakiko_config.reload_from_disk()
        character_manager = GetCharacterAttributes()
        character_manager.reload_user_characters()
        return list(character_manager.user_characters)

    @pyqtSlot()
    def _open_persona_editor(self) -> None:
        # 使用 subprocess 模块启动文件
        import subprocess
        import sys
        subprocess.Popen([sys.executable, "dsakiko_configuration.py", "CharacterArea"])

    def _populate_persona_combo(self) -> None:
        """填充用户人设下拉框，并仅在界面中区分重复名称。"""
        name_counts: dict[str, int] = {}
        for persona in self.user_personas:
            if persona.is_default_user:
                base_name = "无身份"
            else:
                effective_name = persona.effective_character_name.strip()
                base_name = effective_name or "未命名身份"
            name_counts[base_name] = name_counts.get(base_name, 0) + 1
            occurrence = name_counts[base_name]
            display_name = base_name if occurrence == 1 else f"{base_name} ({occurrence})"
            self.persona_combo.addItem(display_name, persona)
        self.persona_combo.setCurrentIndex(0)

    def _toggle_persona_section(self) -> None:
        """切换用户人设区域的展开状态并立即写入配置。"""
        self._set_persona_section_expanded(
            not self.persona_section_expanded,
            save=True,
        )

    def _set_persona_section_expanded(self, expanded: bool, save: bool) -> None:
        """设置用户人设区域状态；折叠时强制选择无用户人设。"""
        if expanded:
            self.persona_section_expanded = True
            self.persona_panel.setVisible(True)
            self._ensure_expanded_persona_layout()
        else:
            self.persona_section_expanded = False
            if self.persona_combo.count() > 0:
                self.persona_combo.setCurrentIndex(0)
            self.persona_panel.setMinimumHeight(0)
            self.persona_panel.setVisible(False)
            self.layout().invalidate()
            self.adjustSize()

        self.persona_toggle_button.setText(
            "▼ 你的身份" if expanded else "▶ 你的身份"
        )
        if save:
            d_sakiko_config.set(
                d_sakiko_config.user_persona_section_expanded,
                expanded,
            )

    def _ensure_expanded_persona_layout(self) -> None:
        """重新计算展开的人设面板高度，避免预览框与提示标签重叠。"""
        if not self.persona_section_expanded:
            return

        self.persona_notice.updateGeometry()
        persona_layout = self.persona_panel.layout()
        persona_layout.invalidate()
        persona_layout.activate()
        self.persona_panel.setMinimumHeight(persona_layout.sizeHint().height())
        self.persona_panel.updateGeometry()
        self.layout().invalidate()
        self.layout().activate()
        self.adjustSize()

    def _selected_persona_item(self) -> CharacterAttributes | None:
        """返回下拉框当前对应的人设对象。"""
        if not self.persona_section_expanded:
            return None
        persona = self.persona_combo.currentData()
        if not isinstance(persona, CharacterAttributes):
            return None
        return persona

    def _refresh_persona_preview(self) -> None:
        """刷新人设描述预览与创建风险提示。"""
        persona = self._selected_persona_item()
        if persona is None or persona.is_default_user:
            self.persona_preview.setPlainText(self.DEFAULT_PERSONA_NOTICE)
            self.persona_notice.clear()
            self._ensure_expanded_persona_layout()
            return

        name = persona.effective_character_name.strip()
        description = persona.effective_character_description
        self.persona_preview.setPlainText(
            description if description.strip() else "（未填写身份描述）"
        )

        notices: list[str] = []
        if not name:
            notices.append("此身份没有名称，无法创建对话。")
        elif not description.strip():
            notices.append("该身份只有名称，AI 不会获得身份描述信息。")

        selected_character = self.selected_character()
        if name and name == selected_character.character_name.strip():
            notices.append("你的身份与对话角色名称相同，AI 可能会混淆双方身份，导致对话体验下降。")
        self.persona_notice.setText("\n".join(notices))
        self._ensure_expanded_persona_layout()

    def _accept_if_valid(self) -> None:
        """校验所选用户人设后接受创建请求。"""
        persona = self._selected_persona_item()
        if (
                persona is not None
                and not persona.is_default_user
                and not persona.effective_character_name.strip()
        ):
            QMessageBox.warning(self, "无法创建对话", "所选对话身份没有名称，请先在设置中填写名称。")
            return
        self.accept()

    def selected_character(self) -> CharacterAttributes:
        """
        获取用户选择的角色对象。
        """
        return self.character_list[self.character_combo.currentIndex()]

    def selected_user_persona(self) -> CharacterAttributes | None:
        """获取用户选择的人设；无用户人设或折叠状态返回 None。"""
        persona = self._selected_persona_item()
        if persona is None or persona.is_default_user:
            return None
        return persona

    def chat_name(self) -> str:
        """
        获取用户填写的对话名称。
        """
        return self.chat_name_edit.text().strip()


class ChatBackupExportOptionsDialog(QDialog):
    """选择单个对话备份包导出路径和导出选项的弹窗。"""

    def __init__(
            self,
            title: str,
            default_filename: str,
            file_filter: str,
            parent: QWidget | None = None,
    ) -> None:
        """创建单个对话导出设置弹窗。"""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.default_filename = default_filename
        self.file_filter = file_filter
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit(self)
        self.path_edit.setPlaceholderText("请选择导出文件路径")
        browse_button = QPushButton("浏览...", self)
        browse_button.clicked.connect(self._browse_output_path)
        path_layout.addWidget(self.path_edit, 1)
        path_layout.addWidget(browse_button)
        layout.addLayout(path_layout)

        self.include_audio_checkbox = QCheckBox("包含历史语音（导出文件会更大）", self)
        self.include_audio_checkbox.setChecked(True)
        layout.addWidget(self.include_audio_checkbox)

        self.validation_label = QLabel("", self)
        self.validation_label.setStyleSheet("color: #B00020;")
        layout.addWidget(self.validation_label)

        button_box = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok, self)
        button_box.button(QDialogButtonBox.Ok).setText("导出")
        button_box.button(QDialogButtonBox.Cancel).setText("取消")
        button_box.accepted.connect(self._accept_if_valid)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def output_path(self) -> str:
        """返回用户填写的绝对导出路径。"""
        raw_path = self.path_edit.text().strip()
        if not raw_path:
            return ""
        return os.path.abspath(raw_path)

    def include_audio(self) -> bool:
        """返回是否在备份包中包含历史语音。"""
        return self.include_audio_checkbox.isChecked()

    def _browse_output_path(self) -> None:
        """打开保存文件对话框，并把选择结果写入路径输入框。"""
        start_path = self.path_edit.text().strip() or self.default_filename
        output_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出对话备份",
            start_path,
            self.file_filter,
        )
        if output_path:
            self.path_edit.setText(os.path.abspath(output_path))
            self.validation_label.clear()

    def _accept_if_valid(self) -> None:
        """确认导出路径非空后接受弹窗。"""
        if not self.output_path():
            self.validation_label.setText("请选择导出文件路径。")
            return
        self.accept()


class ChatBackupMultiExportDialog(QDialog):
    """选择需要导出的多条普通对话及导出选项。"""

    def __init__(self, chats: Sequence[Chat], parent: QWidget | None = None) -> None:
        """用当前普通对话列表初始化多选导出弹窗。"""
        super().__init__(parent)
        self.setWindowTitle("导出多个对话")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        self.chat_list_widget = QListWidget(self)
        self.chat_list_widget.setMinimumHeight(260)
        for chat in chats:
            item = QListWidgetItem(chat.name.strip() or "未命名对话")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, chat.chat_id)
            self.chat_list_widget.addItem(item)
        layout.addWidget(self.chat_list_widget)

        quick_select_layout = QHBoxLayout()
        select_all_button = QPushButton("全选", self)
        select_none_button = QPushButton("全不选", self)
        select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        select_none_button.clicked.connect(lambda: self._set_all_checked(False))
        quick_select_layout.addWidget(select_all_button)
        quick_select_layout.addWidget(select_none_button)
        quick_select_layout.addStretch(1)
        layout.addLayout(quick_select_layout)

        self.include_audio_checkbox = QCheckBox("包含历史语音（导出文件会更大）", self)
        self.include_audio_checkbox.setChecked(True)
        layout.addWidget(self.include_audio_checkbox)

        button_box = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok, self)
        button_box.button(QDialogButtonBox.Ok).setText("导出")
        button_box.button(QDialogButtonBox.Cancel).setText("取消")
        button_box.accepted.connect(self._accept_if_valid)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def selected_chat_ids(self) -> list[str]:
        """返回已勾选的对话 ID 列表。"""
        selected_ids: list[str] = []
        for row in range(self.chat_list_widget.count()):
            item = self.chat_list_widget.item(row)
            if item.checkState() != Qt.Checked:
                continue
            chat_id = item.data(Qt.UserRole)
            if isinstance(chat_id, str):
                selected_ids.append(chat_id)
        return selected_ids

    def include_audio(self) -> bool:
        """返回是否在备份包中包含历史语音。"""
        return self.include_audio_checkbox.isChecked()

    def _set_all_checked(self, checked: bool) -> None:
        """批量设置所有对话条目的勾选状态。"""
        check_state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.chat_list_widget.count()):
            self.chat_list_widget.item(row).setCheckState(check_state)

    def _accept_if_valid(self) -> None:
        """确保至少选择一条对话后接受弹窗。"""
        if not self.selected_chat_ids():
            QMessageBox.information(self, "请选择对话", "请至少选择一条要导出的对话。")
            return
        self.accept()


class ChatGUI(QWidget):
    def __init__(self,
                 dp2qt_queue,
                 qt2dp_queue,
                 QT_message_queue,
                 characters,
                 dp_chat,
                 audio_gen,live2d_text_queue,is_display_text_value,motion_complete_value,emotion_queue,audio_file_path_queue,
                 change_char_queue=None,
                 is_motion_complete=None):
        super().__init__()
        self.is_motion_complete = is_motion_complete
        self.audio_gen = audio_gen  # 为了获得音频文件路径，以及修改语速
        self.character_list:list[CharacterAttributes] = characters
        self.character_by_name: dict[str, CharacterAttributes] = {
            one_character.character_name: one_character for one_character in self.character_list
        }
        self.dp_chat=dp_chat    # dp_local2 模块引用
        self.change_char_queue = change_char_queue
        # 使用 ChatManager 管理所有聊天记录（与 dp_local2 共享同一个实例）
        self.chat_manager: ChatManager = self.dp_chat.chat_manager
        self.current_chat_id = self.dp_chat.current_chat_id
        # 当前显示的对话的 id（保存于对话中）
        self.active_chat_id: str | None = None
        # 当前轮次对话的 id（在用户发送一条消息时生成，作为内部状态，不保存）
        self.active_turn_id: str | None = None
        # 一轮对话的几个阶段，包含 'llm', 'tts', 'rendering' 三个阶段
        # 即 AI 生成对话，角色语音合成、界面慢速渲染三个部分。
        self.active_turn_phase: str | None = None
        self.active_turn_message_indices: set[int] = set()
        # 用户取消的对话轮次的 id
        self.cancelled_turn_ids: set[str] = set()
        self.tool_call_records_cache: dict[str, dict] = {}
        self.reasoning_enabled_labels: dict[str, str] = {
            "auto": "自动",
            "on": "思考",
            "off": "快速",
        }
        self.reasoning_effort_labels: dict[str, str] = {
            "default": "默认",
            "minimal": "极低",
            "low": "低",
            "medium": "中",
            "high": "高",
            "xhigh": "极高",
        }
        self.setWindowTitle("数字小祥")
        self.setWindowIcon(QIcon("../live2d_related/sakiko/sakiko_icon.png"))
        self.screen = QDesktopWidget().screenGeometry()
        self.input_tool_button_height = int(self.screen.height()*0.027)
        self.resize(int(0.37 * self.screen.width()), int(0.7 * self.screen.height()))
        self.chat_display = ChatDisplay(self)
        self.chat_display.audioLinkClicked.connect(self.play_history_audio)  # noqa
        self.chat_display.toolCallClicked.connect(self._open_tool_call_dialog)  # noqa
        self.chat_display.deleteMessageRequested.connect(self.delete_message)  # noqa
        self.chat_display.deleteTurnRequested.connect(self.delete_turn)  # noqa
        self.chat_display.editMessageRequested.connect(self.edit_message)  # noqa
        self.chat_display.editAndResendRequested.connect(self.edit_and_resend_user_message)  # noqa
        self.chat_display.rollbackRequested.connect(self.rollback_to_message)  # noqa
        self.chat_display.regenerateTurnReplyRequested.connect(self.regenerate_turn_reply)  # noqa
        self.chat_display.regenerateAudioRequested.connect(self.regenerate_audio)  # noqa
        self.chat_display.forkChatRequested.connect(self.fork_chat_from_message)  # noqa
        self.chat_display.streamFinished.connect(self._refresh_send_button_state)  # noqa

        self.messages_box = QTextBrowser()
        self.messages_box.setStyleSheet("""
                                    QTextBrowser {
                                        background: transparent; 
                                        border: none; 
                                        
                                        padding-left: 0px;
                                        font-weight: bold;
                                    }
                                """)
        self.messages_box.setPlaceholderText("这里是各种消息提示框...")
        self.messages_box.setMaximumHeight(int(0.05*self.screen.height()))
        self._set_message_box_idle()
        self.update_banner = self._create_update_banner()
        self.user_input = MessageInput()
        self.user_input.setObjectName("messageTextInput")
        self.user_input.setPlaceholderText("在这里输入内容")
        self.user_input.set_vision_support_checker(self._current_model_supports_vision)
        self.user_input.set_image_upload_override_handlers(
            self._current_litellm_model_name,
            self._current_model_can_force_image_upload,
            self._force_allow_current_model_image_upload,
        )

        self.voice_button = QPushButton()
        self.voice_button.setObjectName("voiceInputButton")
        mic_icon=QIcon("./icons/microphone.png")
        self.voice_button.setIcon(mic_icon)
        self.voice_button.setFixedSize(int(self.screen.height()*0.035), self.input_tool_button_height)
        self.voice_button.setIconSize(QSize(int(self.input_tool_button_height*0.58), int(self.input_tool_button_height*0.58)))
        self.voice_button.pressed.connect(self.voice_dectect)  # noqa
        self.voice_button.released.connect(self.voice_decect_end)  # noqa

        self.send_button = QToolButton()
        self.send_button.setObjectName("sendMessageButton")
        self.send_button.setText("↑")
        self.send_button.setToolTip("发送")
        self.send_button.setFixedSize(self.input_tool_button_height, self.input_tool_button_height)
        self.send_button.setIconSize(QSize(int(self.input_tool_button_height*0.42), int(self.input_tool_button_height*0.42)))
        self._send_stop_icon = self._create_stop_button_icon()
        self.send_button.clicked.connect(self.handle_send_button_clicked)  # noqa

        self.save_dialog_btn=QToolButton()
        self.save_dialog_btn.setIcon(QIcon("./icons/save.svg"))
        self.save_dialog_btn.setFixedSize(int(self.screen.height()*0.04),int(self.screen.height()*0.04))
        self.save_dialog_btn.setIconSize(QSize(int(self.screen.height()*0.04*0.6),int(self.screen.height()*0.04*0.6)))
        self.save_dialog_btn.setToolTip("保存聊天记录")

        context_usage_sizing = resolve_context_usage_sizing(self.screen.height(), sys.platform)
        self.context_usage_indicator = ContextUsageIndicator(
            self,
            size=context_usage_sizing.indicator_size,
            popup_width=context_usage_sizing.popup_width,
            popup_font_size=context_usage_sizing.popup_font_size,
        )
        self.context_usage_indicator.set_summary_threshold_ratio(
            float(d_sakiko_config.rolling_summary_trigger_ratio.value)
        )
        self.context_usage_indicator.summaryThresholdChanged.connect(
            self._set_summary_compression_threshold
        )  # noqa
        self._context_usage_refresh_timer = QTimer(self)
        self._context_usage_refresh_timer.setSingleShot(True)
        self._context_usage_refresh_timer.timeout.connect(self.refresh_context_usage_indicator)  # noqa

        layout = QVBoxLayout()

        input_panel = self._create_input_panel()
        self._setup_input_commands(input_panel)

        #处理模型的回答
        self.dp2qt_queue=dp2qt_queue
        self.get_response_thread=CommunicateThreadDP2QT(self.dp2qt_queue,self.chat_display)
        self.get_response_thread.response_signal.connect(self.handle_response)  # noqa
        self.get_response_thread.start()
        #处理用户输入
        self.qt2dp_queue=qt2dp_queue
        self.save_dialog_btn.clicked.connect(self.save_data)  # noqa

        self.user_input.sendRequested.connect(self.handle_user_input)  # noqa
        #处理各种消息
        self.QT_message_queue=QT_message_queue
        self.get_message_thread=CommunicateThreadMessages(self.QT_message_queue)
        self.get_message_thread.message_signal.connect(self.handle_messages)  # noqa
        self.get_message_thread.start()

        #---------------------消息命令处理机制 从引入抽奖工具开始构建---------------------
        self._message_command_handlers: dict[str, Callable] = {}
        self._lottery_dialog_ref = None
        self._register_message_command_handler(LOTTERY_UI_EVENT_PREFIX, self._handle_lottery_ui_command)
        self.update_check_thread: UpdateCheckThread | None = None
        self.update_download_thread: UpdateDownloadThread | None = None
        self.update_notes_thread: ReleaseNotesThread | None = None
        self.update_dialog: UpdateDialog | None = None
        self.pending_update_plan: UpdatePlan | None = None
        self.downloaded_update_patches: list[DownloadedPatch] = []
        self.repair_check_thread: RepairCheckThread | None = None
        self.repair_prepare_thread: RepairPrepareThread | None = None
        self.repair_check_progress: QProgressDialog | None = None
        self.repair_dialog: RepairDialog | None = None
        self.pending_repair_result: RepairCheckResult | None = None
        self.repair_workflow_active = False
        #----------------------------------------------------------
        # 获取当前主题色
        self._current_theme_color = self._get_theme_color()

        self._load_tool_call_records_cache()

        if self.current_character.icon_path is not None:  # noqa
            self.setWindowIcon(QIcon(self.current_character.icon_path))  # noqa

        if self.current_character.qt_css is not None:  # noqa
            self.setStyleSheet(ThemeManager.generate_stylesheet(
                ThemeManager.get_QT_style_theme_color(self.current_character.qt_css)
                )
            )  # noqa
        else:
            self.setStyleSheet(ThemeManager.generate_stylesheet("#7799CC"))
        if not self.current_character.has_valid_voice_model():
            self.dp_chat.if_generate_audio=False


        self.is_ch = False
        self.saved_talk_speed_and_pause_second: dict[str, dict[str, float]] = {}
        for one_character in self.character_list:
            self.saved_talk_speed_and_pause_second[one_character.character_name] = {
                "talk_speed": 0.9 if one_character.character_name == "祥子" else 0.88,
                "pause_second": 0.5,
            }

        self.talk_speed_label = QLabel(f"语速调节：{self.audio_gen.speed}")  # 此时的数值不是真实的，audio_gen还没有初始化完成
        self.talk_speed_label.setToolTip("调整生成语音的语速，数值越大语速越快。如果觉得生成质量不佳，适当调整一下。")
        self.talk_speed_slider = QSlider(Qt.Horizontal)
        self.talk_speed_slider.setRange(60, 140)
        self.talk_speed_slider.valueChanged.connect(self.set_talk_speed)  # noqa



        self.setting_btn=QToolButton()
        self.setting_btn.setIcon(QIcon('./icons/setting.svg'))
        self.setting_btn.setFixedSize(int(self.screen.height()*0.04),int(self.screen.height()*0.04))
        self.setting_btn.setIconSize(QSize(int(self.screen.height()*0.04*0.6),int(self.screen.height()*0.04*0.6)))
        self.setting_btn.setToolTip('设置')
        self.setting_btn.clicked.connect(self.open_setting_window)
        self.more_function_button =QToolButton()
        self.more_function_button.setIcon(QIcon('./icons/more.svg'))
        self.more_function_button.setFixedSize(int(self.screen.height()*0.04),int(self.screen.height()*0.04))
        self.more_function_button.setIconSize(QSize(int(self.screen.height()*0.04*0.6),int(self.screen.height()*0.04*0.6)))
        self.more_function_button.setToolTip('更多功能')
        self.more_function_button.clicked.connect(self.open_more_function_window)  # noqa
        self.change_character_button = QToolButton()
        self.change_character_button.setIcon(QIcon('./icons/chat_list.svg'))
        self.change_character_button.setFixedSize(int(self.screen.height()*0.04),int(self.screen.height()*0.04))
        self.change_character_button.setIconSize(QSize(int(self.screen.height()*0.04*0.6),int(self.screen.height()*0.04*0.6)))
        self.change_character_button.setToolTip("展开/收起对话列表")
        self.change_character_button.clicked.connect(self.toggle_chat_panel)  # noqa

        self.pause_second_label=QLabel(f"句间停顿时间(s)：{self.audio_gen.pause_second}")
        self.pause_second_label.setToolTip("调整句子之间的停顿时间，数值越大停顿越久。如果觉得生成质量不佳，适当调整一下。")
        self.pause_second_slider=QSlider(Qt.Horizontal)
        self.pause_second_slider.valueChanged.connect(self.set_pause_second)  # noqa
        self.pause_second_slider.setRange(10,80)
        self.pause_second_slider.setValue(50)

        slider_layout = QHBoxLayout()
        slider_layout.addWidget(self.talk_speed_label)
        slider_layout.addWidget(self.talk_speed_slider)
        slider_layout.addWidget(self.pause_second_label)
        slider_layout.addWidget(self.pause_second_slider)


        top_layout=QHBoxLayout()
        top_layout.addWidget(self.messages_box,1)
        top_layout.addWidget(self.save_dialog_btn,0)
        top_layout.addWidget(self.change_character_button,0)
        top_layout.addWidget(self.setting_btn,0)
        top_layout.addWidget(self.more_function_button,0)

        layout.addLayout(top_layout)
        layout.addWidget(self.update_banner)
        layout.addWidget(self.chat_display)
        layout.addLayout(slider_layout)
        layout.addWidget(input_panel)
        if self.current_character.qt_css is not None:
            self.set_btn_color(ThemeManager.get_QT_style_theme_color(self.current_character.qt_css))
        else:
            self.set_btn_color('#7799CC')

        self.talk_speed_reset()

        self.chat_manage_panel = self._create_chat_manage_panel()
        root_layout = QHBoxLayout()
        root_layout.addLayout(layout, 1)
        root_layout.addWidget(self.chat_manage_panel, 0)
        self.setLayout(root_layout)  #因为需要character_list等参数，所以放在最后初始化
        self.refresh_chat_list()
        self.sync_current_chat_to_backends()
        self.audio_gen.request_preload_character(self.current_character)    #预加载当前角色的语音模型，减少首次生成语音的等待时间
        self.schedule_context_usage_refresh()

        # 保存 Live2D 跨进程通信的共享变量和队列
        self.live2d_text_queue=live2d_text_queue
        self.is_display_text_value=is_display_text_value
        self.motion_complete_value=motion_complete_value
        self.emotion_queue=emotion_queue
        self.audio_file_path_queue=audio_file_path_queue    #为了播放历史记录
        #-------------------------------------------------------------------------------以下为语音识别部分
        self.voice_button.setCheckable(True)
        self.voice_button.setEnabled(False)
        self.record_timer = QTimer()
        self.record_timer.timeout.connect(self.check_valid)  # noqa
        self.is_recording=False
        self.record_data=[]
        self.voice_is_valid=False
        self.load_whisper_model()
        QTimer.singleShot(0, self.show_last_update_failure_if_needed)
        QTimer.singleShot(0, self.show_last_repair_failure_if_needed)
        QTimer.singleShot(30000, self.start_auto_update_check)

    def show_last_update_failure_if_needed(self) -> None:
        """在主窗口启动后提示上次 detached 更新器失败结果。"""

        result_file = get_update_result_file(get_app_root())
        if not result_file.exists():
            return
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("读取更新结果记录失败：%s", result_file)
            return
        if not isinstance(result, dict):
            return
        result_data = cast(dict[str, object], result)
        if result_data.get("status") != "failed" or result_data.get("notified") is not False:
            return

        log_path = self._resolve_update_log_path(result_data.get("log_file"))
        self._show_last_update_failure_dialog(result_data, log_path)
        self._mark_update_result_notified(result_file, result_data)

    def _resolve_update_log_path(self, log_file_value: object) -> Path | None:
        """把更新结果记录中的日志路径解析为本机绝对路径。"""

        log_file = str(log_file_value or "").strip()
        if not log_file:
            return None
        path = Path(log_file).expanduser()
        if path.is_absolute():
            return path.resolve(strict=False)
        return (get_app_root() / path).resolve(strict=False)

    def _show_last_update_failure_dialog(self, result: dict[str, object], log_path: Path | None) -> None:
        """显示上次更新失败并可定位日志文件的一次性提示。"""

        rollback_performed = result.get("rollback_performed") is True
        rollback_succeeded = result.get("rollback_succeeded")
        if not rollback_performed:
            detail = "上次更新没有完成，程序仍停留在旧版本。"
        elif rollback_succeeded is False:
            detail = "上次更新没有完成，且自动回滚可能失败。"
        else:
            detail = "上次更新没有完成，程序已自动回滚到旧版本。"

        message = (
            f"{detail}\n\n"
            "如果你愿意反馈这个问题，可以点击“显示日志文件”，把该更新日志文件发给开发者。\n\n"
            "问题提示只会显示这一次。"
        )
        if log_path is not None:
            message += f"\n\n日志文件：\n{log_path}"

        box = QMessageBox(QMessageBox.Warning, "上次更新未完成", message, QMessageBox.Ok, self)
        box.button(QMessageBox.Ok).setText("确定")
        show_log_button = None
        if log_path is not None:
            show_log_button = box.addButton("显示日志文件", QMessageBox.ActionRole)
        box.exec_()
        if show_log_button is not None and box.clickedButton() == show_log_button:
            show_file_in_manager(log_path)

    def _mark_update_result_notified(self, result_file: Path, result: dict[str, object]) -> None:
        """在用户看过提示后把更新结果记录标记为已提示。"""

        try:
            updated_result = dict(result)
            updated_result["notified"] = True
            temp_file = result_file.with_name(f".{result_file.name}.tmp")
            temp_file.write_text(json.dumps(updated_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp_file.replace(result_file)
        except Exception:
            logger.exception("更新结果记录已提示状态写回失败：%s", result_file)

    def show_last_repair_failure_if_needed(self) -> None:
        """在主窗口启动后提示上次 detached 修复器失败结果。"""

        result_file = get_repair_result_file(get_app_root())
        if not result_file.exists():
            return
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("读取修复结果记录失败：%s", result_file)
            return
        if not isinstance(result, dict):
            return
        result_data = cast(dict[str, object], result)
        if result_data.get("status") != "failed" or result_data.get("notified") is not False:
            return
        log_path = self._resolve_update_log_path(result_data.get("log_file"))
        rollback_performed = result_data.get("rollback_performed") is True
        rollback_succeeded = result_data.get("rollback_succeeded")
        if rollback_performed and rollback_succeeded is False:
            detail = "上次程序文件修复失败，且自动回滚可能没有完整完成。"
        elif rollback_performed:
            detail = "上次程序文件修复失败，已自动恢复本次修改。"
        else:
            detail = "上次程序文件修复没有完成，未修改或未完成替换程序文件。"
        message = f"{detail}\n\n问题提示只会显示这一次。"
        if log_path is not None:
            message += f"\n\n日志文件：\n{log_path}"
        box = QMessageBox(QMessageBox.Warning, "上次程序文件修复未完成", message, QMessageBox.Ok, self)
        box.button(QMessageBox.Ok).setText("确定")
        show_log_button = None
        if log_path is not None:
            show_log_button = box.addButton("显示日志文件", QMessageBox.ActionRole)
        box.exec_()
        if show_log_button is not None and box.clickedButton() == show_log_button:
            show_file_in_manager(log_path)
        self._mark_update_result_notified(result_file, result_data)

    def _update_operation_in_progress(self) -> bool:
        """判断主程序内是否正在检查或下载更新。"""

        return bool(
            (self.update_check_thread is not None and self.update_check_thread.isRunning())
            or (self.update_download_thread is not None and self.update_download_thread.isRunning())
        )

    def check_repair_manual(self) -> None:
        """响应用户手动检查并修复程序文件。"""

        if self.repair_workflow_active:
            QMessageBox.information(self, "程序文件修复", "正在检查或准备修复，请稍候。")
            return
        if self._update_operation_in_progress():
            QMessageBox.information(self, "程序文件修复", "当前正在检查或下载更新，请稍后再试。")
            return
        base_urls = get_configured_repair_base_urls()
        if not base_urls:
            QMessageBox.information(self, "程序文件修复", "尚未配置程序文件修复资源地址。")
            return
        self.repair_workflow_active = True
        self.pending_repair_result = None
        progress = QProgressDialog("正在获取当前版本修复清单...", "取消", 0, 0, self)
        progress.setWindowTitle("检查程序文件")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        self.repair_check_progress = progress
        self.repair_check_thread = RepairCheckThread(base_urls, self)
        progress.canceled.connect(self.repair_check_thread.cancel)  # noqa
        self.repair_check_thread.progressChanged.connect(self._on_repair_check_progress)  # noqa
        self.repair_check_thread.checkFinished.connect(self._on_repair_check_finished)  # noqa
        self.repair_check_thread.checkFailed.connect(self._on_repair_check_failed)  # noqa
        self.repair_check_thread.start()
        progress.show()

    def _on_repair_check_progress(self, completed: int, total: int, path: str) -> None:
        """刷新本地文件扫描进度。"""
        # 这里有一个比较严重的并发问题：repair_check_progress 这个条甚至可能在三个语句执行期间变为 None。
        # 因此我们在检查后，设置进度条时同样要忽略一切 AttributeError，避免报错出现。
        if self.repair_check_progress is None:
            return
        try:
            self.repair_check_progress.setRange(0, max(total, 1))
            self.repair_check_progress.setValue(min(completed, max(total, 1)))
            self.repair_check_progress.setLabelText(f"正在检查程序文件 {completed}/{total}\n{path}")
        except AttributeError:
            pass

    def _close_repair_check_progress(self) -> None:
        """关闭并释放检查进度弹窗。"""

        if self.repair_check_progress is not None:
            self.repair_check_progress.close()
            self.repair_check_progress.deleteLater()
            self.repair_check_progress = None

    def _on_repair_check_finished(self, result: RepairCheckResult) -> None:
        """处理完整性扫描完成结果。"""

        self._close_repair_check_progress()
        if not result.candidates:
            self.repair_workflow_active = False
            QMessageBox.information(self, "程序文件修复", "程序文件完整。")
            return
        self.pending_repair_result = result
        dialog = RepairDialog(result, self)
        self.repair_dialog = dialog
        dialog.prepareRequested.connect(self.start_repair_prepare)  # noqa
        dialog.cancelRequested.connect(self.cancel_repair_prepare)  # noqa
        dialog.exec_()
        if self.repair_prepare_thread is None or not self.repair_prepare_thread.isRunning():
            self.repair_workflow_active = False

    def _on_repair_check_failed(self, reason: str, message: str) -> None:
        """处理检查取消、不支持或服务错误。"""

        self._close_repair_check_progress()
        self.repair_workflow_active = False
        if reason == "cancelled":
            return
        title = "当前版本暂不支持修复" if reason == "unsupported" else "程序文件检查失败"
        QMessageBox.warning(self, title, message)

    def start_repair_prepare(self) -> None:
        """下载并校验用户已经确认的全部修复文件。"""

        if self.pending_repair_result is None:
            return
        if self.repair_prepare_thread is not None and self.repair_prepare_thread.isRunning():
            return
        if self.repair_dialog is not None:
            self.repair_dialog.set_preparing()
        self.repair_prepare_thread = RepairPrepareThread(self.pending_repair_result, self)
        self.repair_prepare_thread.progressChanged.connect(self._on_repair_prepare_progress)  # noqa
        self.repair_prepare_thread.prepareFinished.connect(self._on_repair_prepare_finished)  # noqa
        self.repair_prepare_thread.prepareFailed.connect(self._on_repair_prepare_failed)  # noqa
        self.repair_prepare_thread.start()

    def cancel_repair_prepare(self) -> None:
        """取消尚未进入独立替换阶段的修复准备。"""

        if self.repair_prepare_thread is not None and self.repair_prepare_thread.isRunning():
            self.repair_prepare_thread.cancel()
            if self.repair_dialog is not None:
                self.repair_dialog.set_status("正在取消修复资源下载...")
            return
        if self.repair_dialog is not None:
            self.repair_dialog.reject()

    def _on_repair_prepare_progress(self, completed: int, total: int, path: str) -> None:
        """刷新修复资源准备进度。"""

        if self.repair_dialog is not None:
            self.repair_dialog.set_progress(completed, total, path)

    def _on_repair_prepare_failed(self, reason: str, message: str) -> None:
        """处理下载取消或修复准备失败。"""

        if self.repair_dialog is None:
            self.repair_workflow_active = False
            return
        if reason == "cancelled":
            self.repair_dialog.set_cancelled()
        else:
            logger.warning("准备程序文件修复失败：%s", message)
            self.repair_dialog.set_error(message)

    def _on_repair_prepare_finished(self, prepared: PreparedRepair) -> None:
        """启动独立修复器并退出当前主程序。"""

        if self.repair_dialog is not None:
            self.repair_dialog.set_launching()
        app_root = get_app_root()
        try:
            self.save_data()
            launch_repair_process(
                app_root=app_root,
                plan_file=prepared.plan_file,
                main_pid=os.getpid(),
                restart_command=build_restart_command(app_root),
            )
        except Exception as exc:
            logger.exception("启动独立修复器失败")
            if self.repair_dialog is not None:
                self.repair_dialog.set_error(f"启动独立修复器失败：{exc}")
            return
        if self.repair_dialog is not None:
            self.repair_dialog.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()
        self.close()

    def start_auto_update_check(self) -> None:
        """启动一次静默自动更新检查。"""

        self._start_update_check(silent=True)

    def check_update_manual(self) -> None:
        """响应用户手动检查更新。"""

        if self.repair_workflow_active:
            QMessageBox.information(self, "检查更新", "当前正在检查或准备修复程序文件，请稍后再试。")
            return
        if self.pending_update_plan is not None:
            self.open_update_dialog(self.pending_update_plan)
            return
        self._start_update_check(silent=False)

    def _start_update_check(self, silent: bool) -> None:
        """创建后台线程检查更新。"""

        if self.repair_workflow_active:
            if not silent:
                QMessageBox.information(self, "检查更新", "当前正在检查或准备修复程序文件，请稍后再试。")
            return
        if self.update_check_thread is not None and self.update_check_thread.isRunning():
            if not silent:
                QMessageBox.information(self, "检查更新", "正在检查更新，请稍候。")
            return
        index_urls = get_configured_index_urls()
        if not index_urls and not silent:
            QMessageBox.information(self, "检查更新", "尚未配置更新索引 URL。")
            return
        self.update_check_thread = UpdateCheckThread(index_urls, self)
        self.update_check_thread.updateAvailable.connect(lambda plan: self._on_update_available(plan, silent))  # noqa
        self.update_check_thread.noUpdate.connect(lambda: self._on_no_update(silent))  # noqa
        self.update_check_thread.checkFailed.connect(lambda message: self._on_update_check_failed(message, silent))  # noqa
        self.update_check_thread.start()
        if not silent:
            self.setWindowTitle("正在检查更新...")

    def _on_update_available(self, update_plan: UpdatePlan, silent: bool) -> None:
        """处理发现新版本的结果。"""

        self.pending_update_plan = update_plan
        if silent and not update_plan.critical:
            self._show_update_banner(update_plan)
            return
        self.open_update_dialog(update_plan)

    def _on_no_update(self, silent: bool) -> None:
        """处理没有新版本的结果。"""

        self._hide_update_banner()
        if not silent:
            self.setWindowTitle("数字小祥")
            QMessageBox.information(self, "检查更新", "当前已是最新版本。")

    def _on_update_check_failed(self, message: str, silent: bool) -> None:
        """处理更新检查失败。"""

        logger.warning("检查更新失败：%s", message)
        if not silent:
            self.setWindowTitle("数字小祥")
            QMessageBox.warning(self, "检查更新失败", message)

    def open_update_dialog(self, update_plan: UpdatePlan | None = None) -> None:
        """打开更新弹窗并启动更新公告加载。"""

        if self.repair_workflow_active:
            QMessageBox.information(self, "检查更新", "当前正在检查或准备修复程序文件，请稍后再试。")
            return
        plan = update_plan or self.pending_update_plan
        if plan is None:
            self.check_update_manual()
            return
        dialog = UpdateDialog(plan, self)
        self.update_dialog = dialog
        dialog.downloadAndInstallRequested.connect(self.start_update_download_and_install)  # noqa
        dialog.cancelRequested.connect(self.cancel_update_download)  # noqa
        self._start_release_notes_loading(plan)
        dialog.exec_()

    def _start_release_notes_loading(self, update_plan: UpdatePlan) -> None:
        """后台拉取完整更新公告。"""

        notes_urls = tuple(url for release in update_plan.releases for url in release.notes_urls)
        if not notes_urls:
            return
        self.update_notes_thread = ReleaseNotesThread(notes_urls, self)
        self.update_notes_thread.notesLoaded.connect(self._on_release_notes_loaded)  # noqa
        self.update_notes_thread.notesFailed.connect(lambda message: logger.warning("更新公告拉取失败：%s", message))  # noqa
        self.update_notes_thread.start()

    def _on_release_notes_loaded(self, markdown: str, source_name: str) -> None:
        """把完整更新公告写入弹窗。"""

        if self.update_dialog is not None:
            self.update_dialog.set_release_notes(markdown, source_name)

    def _create_update_banner(self) -> QFrame:
        """创建发现新版本时显示的顶部横幅。"""

        banner = QFrame(self)
        banner.setObjectName("updateBanner")
        banner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(12, 6, 8, 6)
        banner_layout.setSpacing(8)

        self.update_banner_label = QLabel("发现新版本", banner)
        self.update_banner_label.setObjectName("updateBannerLabel")
        self.update_banner_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        banner_layout.addWidget(self.update_banner_label, 1)

        self.update_banner_view_button = QPushButton("查看更新", banner)
        self.update_banner_view_button.setObjectName("updateBannerViewButton")
        self.update_banner_view_button.clicked.connect(lambda: self.open_update_dialog())  # noqa
        banner_layout.addWidget(self.update_banner_view_button, 0)

        self.update_banner_dismiss_button = QPushButton("稍后", banner)
        self.update_banner_dismiss_button.setObjectName("updateBannerDismissButton")
        self.update_banner_dismiss_button.clicked.connect(self._hide_update_banner)  # noqa
        banner_layout.addWidget(self.update_banner_dismiss_button, 0)

        banner.setVisible(False)
        return banner

    def _show_update_banner(self, update_plan: UpdatePlan) -> None:
        """显示普通更新的持久入口横幅。"""

        self.update_banner_label.setText(f"发现新版本 {update_plan.target_version}")
        self.update_banner.setVisible(True)

    def _hide_update_banner(self) -> None:
        """隐藏普通更新入口横幅。"""

        if hasattr(self, "update_banner"):
            self.update_banner.setVisible(False)

    def _apply_update_banner_style(self, color: str) -> None:
        """根据当前主题色刷新更新横幅样式。"""

        if not hasattr(self, "update_banner"):
            return
        theme_color = QColor(color)
        if not theme_color.isValid():
            theme_color = QColor("#7799CC")
        hover_color = theme_color.lighter(112).name()
        pressed_color = theme_color.darker(108).name()
        background_color = f"rgba({theme_color.red()}, {theme_color.green()}, {theme_color.blue()}, 0.10)"
        self.update_banner.setStyleSheet(f"""
            QFrame#updateBanner {{
                background-color: {background_color};
                border: 1px solid {theme_color.name()};
                border-radius: 9px;
            }}
            QLabel#updateBannerLabel {{
                color: #272636;
                background: transparent;
                font-weight: bold;
            }}
            QPushButton#updateBannerViewButton {{
                color: #FFFFFF;
                background-color: {theme_color.name()};
                border: 1px solid {theme_color.name()};
                border-radius: 7px;
                padding: 4px 10px;
                font-weight: bold;
            }}
            QPushButton#updateBannerViewButton:hover {{
                background-color: {hover_color};
                border: 1px solid {hover_color};
            }}
            QPushButton#updateBannerViewButton:pressed {{
                background-color: {pressed_color};
                border: 1px solid {pressed_color};
            }}
            QPushButton#updateBannerDismissButton {{
                color: {theme_color.name()};
                background-color: transparent;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 7px;
                padding: 4px 10px;
            }}
            QPushButton#updateBannerDismissButton:hover {{
                background-color: rgba(255, 255, 255, 0.50);
            }}
            QPushButton#updateBannerDismissButton:pressed {{
                background-color: rgba(0, 0, 0, 0.06);
            }}
        """)

    def start_update_download_and_install(self) -> None:
        """确认后下载当前更新计划中的补丁链，并在下载完成后自动安装。"""

        if self.pending_update_plan is None:
            return
        if self.repair_workflow_active:
            QMessageBox.information(self, "更新", "当前正在检查或准备修复程序文件，请稍后再试。")
            return
        if self.update_download_thread is not None and self.update_download_thread.isRunning():
            return
        reply = QMessageBox.question(
            self,
            "下载并安装更新",
            "下载完成后将关闭当前程序并应用更新，完成后会自动重启。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.downloaded_update_patches = []
        if self.update_dialog is not None:
            self.update_dialog.set_downloading()
        self.update_download_thread = UpdateDownloadThread(self.pending_update_plan, self)
        self.update_download_thread.progressChanged.connect(self._on_update_download_progress)  # noqa
        self.update_download_thread.statusChanged.connect(self._on_update_download_status)  # noqa
        self.update_download_thread.downloadFinished.connect(self._on_update_download_finished)  # noqa
        self.update_download_thread.downloadFailed.connect(self._on_update_download_failed)  # noqa
        self.update_download_thread.start()

    def cancel_update_download(self) -> None:
        """取消正在进行的更新下载。"""

        if self.update_download_thread is not None and self.update_download_thread.isRunning():
            self.update_download_thread.cancel()
            if self.update_dialog is not None:
                self.update_dialog.set_status("正在取消下载...")
            return
        if self.update_dialog is not None:
            self.update_dialog.reject()

    def _on_update_download_progress(self, downloaded: int, total: int, speed: float) -> None:
        """刷新更新下载进度。"""

        if self.update_dialog is not None:
            self.update_dialog.set_download_progress(downloaded, total, speed)

    def _on_update_download_status(self, text: str) -> None:
        """刷新更新下载状态。"""

        if self.update_dialog is not None:
            self.update_dialog.set_status(text)

    def _on_update_download_finished(self, downloaded_patches: list[DownloadedPatch]) -> None:
        """处理补丁链下载完成。"""

        self.downloaded_update_patches = downloaded_patches
        if self.update_dialog is not None:
            self.update_dialog.set_installing()
        self.install_prepared_update()

    def _on_update_download_failed(self, message: str) -> None:
        """处理补丁链下载失败。"""

        logger.warning("下载更新失败：%s", message)
        if self.update_dialog is not None:
            self.update_dialog.set_error(message)

    def install_prepared_update(self) -> None:
        """启动独立更新器并退出当前主程序。"""

        if not self.downloaded_update_patches:
            QMessageBox.warning(self, "更新", "补丁尚未下载完成。")
            return
        self.save_data()
        app_root = get_app_root()
        launch_update_process(
            downloaded_patches=self.downloaded_update_patches,
            app_root=app_root,
            main_pid=os.getpid(),
            restart_command=build_restart_command(app_root),
        )
        if self.update_dialog is not None:
            self.update_dialog.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()
        self.close()

    def toggle_live2d_text_display(self):
        # 切换共享变量的值
        self.is_display_text_value.value = not self.is_display_text_value.value

    @property
    def current_chat(self) -> Chat:
        """获取当前对话对象。"""
        chat = self.chat_manager.get_chat_by_id(self.current_chat_id)
        if chat is None:
            chat = self.chat_manager.ensure_default_single_character_chat(self.character_list)
            self.current_chat_id = chat.chat_id
        return chat

    @property
    def current_character(self) -> CharacterAttributes:
        """
        获取当前对话绑定的角色对象。
        """
        character_name = self.current_chat.get_character_name()
        if character_name is None:
            raise ValueError("当前对话无法确定角色。")
        character = self.character_by_name.get(character_name)
        if character is None:
            raise ValueError(f"找不到当前对话绑定的角色：{character_name}")
        return character

    def single_character_chats(self) -> list[Chat]:
        """
        获取普通对话列表。
        """
        return self.chat_manager.single_character_chats()

    def _create_chat_manage_panel(self) -> QWidget:
        """
        创建右侧普通对话管理侧栏。
        """
        panel = QWidget()
        panel.setFixedWidth(max(210, int(self.screen.width() * 0.13)))
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(6)

        action_layout = QHBoxLayout()
        self.btn_new_chat = QPushButton("新建")
        self.btn_delete_chat = QPushButton("删除")
        self.btn_chat_sidebar_mode = QPushButton()
        self.btn_chat_backup_menu = QToolButton()
        self.btn_chat_backup_menu.setIcon(QIcon("./icons/more.svg"))
        self.btn_chat_backup_menu.setToolTip("对话备份")
        self.chat_backup_menu = QMenu(self)
        import_backup_action = self.chat_backup_menu.addAction("导入对话备份...")
        export_many_action = self.chat_backup_menu.addAction("导出多个对话...")
        import_backup_action.triggered.connect(self.import_chat_backup)
        export_many_action.triggered.connect(self.export_multiple_chats)
        self.btn_chat_backup_menu.clicked.connect(self.show_chat_backup_menu)  # noqa
        self.btn_chat_sidebar_mode.clicked.connect(self.toggle_chat_sidebar_mode)  # noqa
        self.btn_new_chat.clicked.connect(self.create_new_chat)
        self.btn_delete_chat.clicked.connect(self.delete_current_chat)
        action_layout.addWidget(self.btn_new_chat)
        action_layout.addWidget(self.btn_delete_chat)
        action_layout.addWidget(self.btn_chat_sidebar_mode)
        action_layout.addWidget(self.btn_chat_backup_menu)

        self.chat_sidebar = ChatSidebarView()
        self.chat_sidebar.set_mode(self._chat_sidebar_mode())
        self.chat_sidebar.set_expanded_character_names(self._chat_sidebar_expanded_characters())
        self.chat_sidebar.set_character_theme_colors(self._chat_sidebar_character_theme_colors())
        self.chat_sidebar.chat_selected.connect(self.on_chat_item_clicked)  # noqa
        self.chat_sidebar.chat_menu_requested.connect(self.show_chat_list_menu)  # noqa
        self.chat_sidebar.chat_order_changed.connect(self.on_chat_list_order_changed)  # noqa
        self.chat_sidebar.expanded_characters_changed.connect(self.on_chat_sidebar_expanded_changed)  # noqa
        self._refresh_chat_sidebar_mode_button()
        # 查询模型 input token 上限的线程；因为这个查询比较慢所以需要开新线程
        self._model_limit_query_thread = GetModelLimitThread(model="", parent=self)
        self._model_limit_query_thread.model_input_token_limit.connect(self._on_model_limit_queryed)

        layout.addLayout(action_layout)
        layout.addWidget(self.chat_sidebar, 1)
        return panel

    def refresh_chat_list(self) -> None:
        """
        刷新右侧普通对话列表并保持当前对话高亮。
        """
        if not hasattr(self, "chat_sidebar"):
            return
        chats = self.single_character_chats()
        self.chat_sidebar.set_character_theme_colors(self._chat_sidebar_character_theme_colors())
        self.chat_sidebar.set_chats(chats, self.current_chat_id)

    def _set_message_box_text(self, message: str) -> None:
        """
        用指定文本替换状态栏提示。
        """
        self.messages_box.clear()
        self.messages_box.append(message)

    def _set_message_box_idle(self) -> None:
        """
        将状态栏恢复为固定空闲提示。
        """
        self._set_message_box_text("就绪")

    def toggle_chat_panel(self) -> None:
        """
        展开或收起右侧普通对话列表。
        """
        self.chat_manage_panel.setVisible(not self.chat_manage_panel.isVisible())

    def toggle_chat_sidebar_mode(self) -> None:
        """
        在平铺和折叠两种模式间切换聊天侧栏。
        """
        current_mode = self._chat_sidebar_mode()
        new_mode = "folded" if current_mode == "flat" else "flat"
        self.set_chat_sidebar_mode(new_mode)

    def on_chat_item_clicked(self, chat_id: str) -> None:
        """
        点击侧栏对话时切换当前对话。
        """
        self.switch_chat_by_id(chat_id)

    def on_chat_list_order_changed(self, ordered_chat_ids: list[str]) -> None:
        """
        拖拽侧栏后保存普通对话类型内顺序。
        """
        self.chat_manager.reorder_chats_by_type(ChatType.SINGLE_CHARACTER, ordered_chat_ids)
        self.chat_manager.save()

    def show_chat_backup_menu(self) -> None:
        """在备份工具按钮下方弹出对话备份菜单。"""
        if not hasattr(self, "chat_backup_menu"):
            return
        button = self.btn_chat_backup_menu
        menu_pos = button.mapToGlobal(QPoint(0, button.height()))
        self.chat_backup_menu.exec_(menu_pos)

    def show_chat_list_menu(self, chat_id: str, global_pos: QPoint) -> None:
        """
        显示对话项右键菜单。
        """
        menu = QMenu(self)
        clone_action = menu.addAction("复制对话")
        export_action = menu.addAction("导出此对话...")
        menu.addSeparator()
        rename_action = menu.addAction("重命名...")
        delete_action = menu.addAction("删除")
        selected_action = menu.exec_(global_pos)
        if selected_action == clone_action:
            self.clone_chat_from_sidebar(chat_id)
        elif selected_action == export_action:
            self.export_single_chat(chat_id)
        elif selected_action == rename_action:
            self.rename_chat(chat_id)
        elif selected_action == delete_action:
            self.delete_chat_by_id(chat_id)

    def clone_chat_from_sidebar(self, chat_id: str) -> None:
        """从侧栏右键入口复制一条普通对话并切换到副本。"""
        if not self._ensure_chat_history_operation_allowed("复制对话"):
            return
        source_chat = self.chat_manager.get_chat_by_id(chat_id)
        if source_chat is None:
            return
        try:
            cloned_chat = self.chat_manager.clone_chat(chat_id)
            self.chat_manager.save()
        except Exception:
            logger.exception("复制对话失败：chat_id=%s", chat_id)
            QMessageBox.warning(self, "复制失败", "复制对话失败，请稍后重试。")
            return
        self.refresh_chat_list()
        self.switch_chat_by_id(cloned_chat.chat_id)
        self._set_message_box_text(f"已复制对话“{source_chat.name}”。")

    def fork_chat_from_message(self, msg_index: int) -> None:
        """从当前普通对话的指定消息处分叉出一条新对话。"""
        if not (0 <= msg_index < len(self.current_chat.message_list)):
            self.refresh_current_chat_display()
            return
        if not self._ensure_chat_history_operation_allowed("分叉对话"):
            return
        try:
            forked_chat = self.chat_manager.clone_chat(
                self.current_chat_id,
                fork_after_message_index=msg_index,
            )
            self.chat_manager.save()
        except Exception:
            logger.exception("分叉对话失败：chat_id=%s, msg_index=%s", self.current_chat_id, msg_index)
            QMessageBox.warning(self, "分叉失败", "分叉对话失败，请稍后重试。")
            return
        self.refresh_chat_list()
        self.switch_chat_by_id(forked_chat.chat_id)
        self._set_message_box_text("已从所选消息分叉出新对话。")

    def export_single_chat(self, chat_id: str) -> None:
        """导出侧栏右键选中的单条对话备份包。"""
        if not self._ensure_chat_history_operation_allowed("导出对话"):
            return
        chat = self.chat_manager.get_chat_by_id(chat_id)
        if chat is None:
            return

        options_dialog = ChatBackupExportOptionsDialog(
            "导出此对话",
            self._default_single_chat_backup_filename(chat),
            self._chat_backup_save_file_filter(),
            self,
        )
        if options_dialog.exec_() != QDialog.Accepted:
            return

        output_path = self._ensure_chat_backup_extension(options_dialog.output_path())
        self._export_chat_ids_to_backup(
            [chat_id],
            output_path,
            include_audio=options_dialog.include_audio(),
        )

    def export_multiple_chats(self) -> None:
        """导出当前普通聊天侧栏中的多条对话。"""
        if not self._ensure_chat_history_operation_allowed("导出对话"):
            return
        chats = self.single_character_chats()
        if not chats:
            QMessageBox.information(self, "没有可导出的对话", "当前没有可导出的普通对话。")
            return

        dialog = ChatBackupMultiExportDialog(chats, self)
        if dialog.exec_() != QDialog.Accepted:
            return

        output_path = self._select_chat_backup_output_path(self._default_multi_chat_backup_filename())
        if not output_path:
            return
        self._export_chat_ids_to_backup(
            dialog.selected_chat_ids(),
            output_path,
            include_audio=dialog.include_audio(),
        )

    def import_chat_backup(self) -> None:
        """从用户选择的 zip 对话备份包导入所有对话。"""
        if not self._ensure_chat_history_operation_allowed("导入对话备份"):
            return
        input_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "导入对话备份",
            "",
            self._chat_backup_open_file_filter(),
        )
        if not input_path:
            return
        try:
            result = self.chat_manager.import_chats_from_backup(input_path)
            self.chat_manager.save()
        except Exception:
            logger.exception("导入对话备份失败：%s", input_path)
            QMessageBox.warning(self, "导入失败", "这不是有效的 D_sakiko 对话备份文件。")
            return

        self.refresh_chat_list()
        self._show_chat_backup_import_result(result)

    def _export_chat_ids_to_backup(
            self,
            chat_ids: Sequence[str],
            output_path: str,
            *,
            include_audio: bool,
    ) -> None:
        """调用模型层导出接口并展示导出结果。"""
        try:
            result = self.chat_manager.export_chats_to_backup(
                chat_ids,
                output_path,
                include_audio=include_audio,
            )
        except Exception:
            logger.exception("导出对话备份失败：%s", output_path)
            QMessageBox.warning(self, "导出失败", "导出对话备份失败，请稍后重试。")
            return

        success_line = (
            f"已导出 {result.exported_chat_count} 条对话，"
            f"包含 {result.exported_attachment_count} 个附件、{result.exported_audio_count} 段历史语音。"
        )
        if result.warnings:
            QMessageBox.warning(
                self,
                "导出完成，但有内容未包含",
                "\n".join([success_line, *self._summarize_chat_backup_warnings(result.warnings)]),
            )
        else:
            self._set_message_box_text(success_line)

    def _show_chat_backup_import_result(self, result: ChatBackupImportResult) -> None:
        """展示对话备份包导入结果。"""
        single_count = sum(1 for chat in result.imported_chats if chat.type == ChatType.SINGLE_CHARACTER)
        theater_count = sum(1 for chat in result.imported_chats if chat.type == ChatType.SMALL_THEATER)
        missing_role_count = self._count_imported_chats_with_missing_roles(result.imported_chats)
        lines = [
            f"已导入 {result.imported_chat_count} 条对话。",
            f"普通聊天：{single_count} 条，小剧场：{theater_count} 条。",
        ]
        if result.restored_audio_count or result.reused_audio_count or result.restored_attachment_count:
            lines.append(
                f"恢复语音 {max(result.restored_audio_count, result.reused_audio_count)} 段，"
                f"恢复附件 {result.restored_attachment_count} 个。"
            )
        warning_lines = self._summarize_chat_backup_warnings(result.warnings)
        if missing_role_count:
            warning_lines.append(f"角色缺失：{missing_role_count} 条对话暂时无法打开。")
        if warning_lines:
            QMessageBox.warning(self, "导入完成，但有内容未恢复", "\n".join([*lines, *warning_lines]))
        else:
            QMessageBox.information(self, "导入完成", "\n".join(lines))

    def _ensure_chat_history_operation_allowed(self, action_text: str) -> bool:
        """检查当前是否允许执行对话历史管理操作。"""
        return self._ensure_delete_operation_allowed(action_text)

    def _select_chat_backup_output_path(self, default_filename: str) -> str:
        """弹出保存文件对话框并返回带备份扩展名的路径。"""
        output_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出对话备份",
            default_filename,
            self._chat_backup_save_file_filter(),
        )
        if not output_path:
            return ""
        return self._ensure_chat_backup_extension(output_path)

    @staticmethod
    def _chat_backup_save_file_filter() -> str:
        """返回对话备份包保存文件筛选器。"""
        return "D_sakiko 对话备份 (*.dsakiko-chat.zip);;Zip 文件 (*.zip)"

    @staticmethod
    def _chat_backup_open_file_filter() -> str:
        """返回对话备份包打开文件筛选器。"""
        return "D_sakiko 对话备份 (*.dsakiko-chat.zip *.zip);;Zip 文件 (*.zip)"

    @staticmethod
    def _ensure_chat_backup_extension(output_path: str) -> str:
        """确保导出路径带有对话备份包扩展名。"""
        if output_path.endswith(".dsakiko-chat.zip") or output_path.endswith(".zip"):
            return output_path
        return f"{output_path}.dsakiko-chat.zip"

    @classmethod
    def _default_single_chat_backup_filename(cls, chat: Chat) -> str:
        """生成单条对话导出的默认文件名。"""
        safe_name = cls._safe_backup_filename_part(chat.name.strip() or "未命名对话")
        return f"{safe_name}_{time.strftime('%Y%m%d-%H%M%S')}.dsakiko-chat.zip"

    @staticmethod
    def _default_multi_chat_backup_filename() -> str:
        """生成多条对话导出的默认文件名。"""
        return f"D_sakiko_对话备份_{time.strftime('%Y%m%d-%H%M%S')}.dsakiko-chat.zip"

    @staticmethod
    def _safe_backup_filename_part(text: str) -> str:
        """清理文件名中不适合作为路径组成部分的字符。"""
        cleaned = re.sub(r'[\\/:*?"<>|]+', "_", text).strip(" .")
        return cleaned or "未命名对话"

    @staticmethod
    def _summarize_chat_backup_warnings(warnings_list: Sequence[str]) -> list[str]:
        """把模型层 warning 聚合成适合弹窗展示的摘要。"""
        audio_count = 0
        attachment_count = 0
        skipped_chat_count = 0
        other_count = 0
        for warning in warnings_list:
            lowered_warning = warning.lower()
            if "音频" in warning or "语音" in warning or "audio" in lowered_warning:
                audio_count += 1
            elif "附件" in warning or "图片" in warning or "attachment" in lowered_warning:
                attachment_count += 1
            elif "对话" in warning:
                skipped_chat_count += 1
            else:
                other_count += 1

        summary: list[str] = []
        if audio_count:
            summary.append(f"缺失语音：{audio_count} 项。")
        if attachment_count:
            summary.append(f"缺失附件：{attachment_count} 项。")
        if skipped_chat_count:
            summary.append(f"跳过对话：{skipped_chat_count} 条。")
        return summary

    def _count_imported_chats_with_missing_roles(self, imported_chats: Sequence[Chat]) -> int:
        """统计导入后因角色缺失而无法在普通聊天窗口打开的对话数量。"""
        missing_count = 0
        for chat in imported_chats:
            character_name = chat.get_character_name()
            if character_name and character_name not in self.character_by_name:
                missing_count += 1
        return missing_count

    def rename_chat(self, chat_id: str) -> None:
        """
        重命名侧栏中的普通对话。
        """
        chat = self.chat_manager.get_chat_by_id(chat_id)
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
        self.chat_manager.save()
        self.refresh_chat_list()

    def _chat_sidebar_mode(self) -> ChatSidebarMode:
        """
        从配置中读取聊天侧栏展示模式。
        """
        mode = str(d_sakiko_config.chat_sidebar_mode.value)
        if mode == "folded":
            return "folded"
        return "flat"

    def _chat_sidebar_expanded_characters(self) -> list[str]:
        """
        从配置中读取聊天侧栏展开角色列表。
        """
        configured_value = d_sakiko_config.chat_sidebar_expanded_characters.value
        if not isinstance(configured_value, list):
            return []
        return [one for one in configured_value if isinstance(one, str)]

    def _chat_sidebar_character_theme_colors(self) -> dict[str, str]:
        """
        获取侧栏中每个角色的主题色映射。
        """
        character_theme_colors: dict[str, str] = {}
        for one_character in self.character_list:
            if one_character.qt_css is None:
                continue
            character_theme_colors[one_character.character_name] = ThemeManager.get_QT_style_theme_color(one_character.qt_css)
        return character_theme_colors

    def set_chat_sidebar_mode(self, mode: ChatSidebarMode) -> None:
        """
        设置并保存聊天侧栏展示模式。
        """
        if not hasattr(self, "chat_sidebar"):
            return
        self.chat_sidebar.set_mode(mode)
        d_sakiko_config.set(d_sakiko_config.chat_sidebar_mode, mode)

        self._refresh_chat_sidebar_mode_button()
        self.refresh_chat_list()

    def _refresh_chat_sidebar_mode_button(self) -> None:
        """
        根据当前模式刷新聊天侧栏模式按钮。
        """
        if not hasattr(self, "btn_chat_sidebar_mode"):
            return
        mode = self._chat_sidebar_mode()
        # 这边我感觉显示“点击后侧边栏切换到什么状态”比“目前侧边栏是什么状态“更好一些
        # 所以这里的显示文字是反过来的
        self.btn_chat_sidebar_mode.setText("折叠" if mode == "flat" else "展开")

    def on_chat_sidebar_expanded_changed(self, character_names: list[str]) -> None:
        """
        保存聊天侧栏折叠模式下展开的角色。
        """
        d_sakiko_config.set(d_sakiko_config.chat_sidebar_expanded_characters, character_names)

    def create_new_chat(self) -> None:
        """
        创建一条新的普通单角色对话并立即切换过去。
        """
        if self.is_chat_busy():
            QMessageBox.information(self, "请稍等", "请等待当前回复完成后再新建对话。")
            return
        dialog = NewSingleChatDialog(self.character_list, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        new_chat = self.chat_manager.create_single_character_chat(
            dialog.selected_character(),
            dialog.chat_name(),
            user_character=dialog.selected_user_persona(),
        )
        self.chat_manager.save()
        self.refresh_chat_list()
        self.switch_chat_by_id(new_chat.chat_id)

    def delete_current_chat(self) -> None:
        """
        删除当前普通对话。
        """
        self.delete_chat_by_id(self.current_chat_id)

    def delete_chat_by_id(self, chat_id: str) -> None:
        """
        删除指定普通对话。
        """
        if not self._ensure_delete_operation_allowed("删除"):
            return
        chat = self.chat_manager.get_chat_by_id(chat_id)
        if chat is None:
            return
        single_chats_before_delete = self.single_character_chats()
        deleted_chat_index = next(
            (index for index, one_chat in enumerate(single_chats_before_delete) if one_chat.chat_id == chat_id),
            -1,
        )
        deleted_messages = list(chat.message_list)
        accepted, should_delete_audio = self._confirm_delete_operation(
            f"确定删除对话“{chat.name}”吗？",
            self._build_delete_preview_from_messages(0, len(deleted_messages), deleted_messages),
        )
        if not accepted:
            return
        deleted_chat = self.chat_manager.delete_chat(chat_id)
        if deleted_chat is None:
            return
        if chat_id == self.current_chat_id:
            remaining_chats = self.single_character_chats()
            if remaining_chats:
                next_index = min(max(deleted_chat_index, 0), len(remaining_chats) - 1)
                new_current = remaining_chats[next_index]
            else:
                new_current = self.chat_manager.ensure_default_single_character_chat(self.character_list)
            self.current_chat_id = new_current.chat_id
            self.sync_current_chat_to_backends()
            self.apply_current_chat_ui_state()
        audio_failed = self._delete_audio_for_messages_if_requested(deleted_chat.message_list, should_delete_audio)
        try:
            delete_chat_attachment_dir(deleted_chat.chat_id)
        except Exception:
            logger.exception("删除对话附件目录失败：chat_id=%s", deleted_chat.chat_id)
        self.chat_manager.save()
        self.refresh_chat_list()
        self._show_delete_success_message("已删除对话", audio_failed)

    def _ensure_delete_operation_allowed(self, action_text: str = "重试") -> bool:
        """检查当前是否允许执行消息操作。"""
        if self.is_chat_busy():
            self.messages_box.clear()
            self.messages_box.append(f"请等待当前回复完成后再{action_text}。")
            return False
        if hasattr(self, "regen_thread") and self.regen_thread is not None and self.regen_thread.isRunning():
            self.messages_box.clear()
            self.messages_box.append(f"请等待当前音频重新生成完成后再{action_text}。")
            return False
        if hasattr(self, "motion_complete_value") and not self.motion_complete_value.value:
            self.messages_box.clear()
            self.messages_box.append(f"请等待当前播放完成后再{action_text}。")
            return False
        return True

    @staticmethod
    def _build_delete_preview_from_messages(
        start: int,
        end: int,
        messages: list[Message],
    ) -> DeleteMessagesResult:
        """根据待删除消息构造不修改数据的删除预览。"""
        deleted_user_count = sum(1 for message in messages if message.character_name == "User")
        return DeleteMessagesResult(
            start=start,
            end=end,
            deleted_messages=messages,
            deleted_user_count=deleted_user_count,
            deleted_character_count=len(messages) - deleted_user_count,
        )

    def _confirm_delete_operation(
        self,
        prompt: str,
        preview: DeleteMessagesResult,
        extra_detail_lines: list[str] | None = None,
        title: str = "删除确认",
        confirm_button_text: str = "删除",
    ) -> tuple[bool, bool]:
        """显示统一删除确认框，并返回是否确认及是否清理音频。"""
        related_audio_paths = self.chat_manager.collect_audio_paths_from_messages(
            preview.deleted_messages
        )
        deletable_audio_paths = self.chat_manager.collect_deletable_audio_paths_from_messages(
            preview.deleted_messages
        )
        detail_lines = [prompt]
        if extra_detail_lines:
            detail_lines.extend(extra_detail_lines)

        if preview.deleted_user_count > 0:
            delete_count_line = f"将删除你的 {preview.deleted_user_count} 条消息"
            if preview.deleted_character_count > 0:
                delete_count_line += f"和角色的 {preview.deleted_character_count} 条回复。"
        else:
            delete_count_line = f"将删除角色的 {preview.deleted_character_count} 条回复。"
        detail_lines.append(delete_count_line)

        detail = "\n".join(detail_lines)
        box = QMessageBox(QMessageBox.Question, title, detail, QMessageBox.No | QMessageBox.Yes, self)
        delete_audio_checkbox = QCheckBox("同时删除相关的语音")
        if deletable_audio_paths:
            delete_audio_checkbox.setChecked(True)
        else:
            delete_audio_checkbox.setChecked(False)
            delete_audio_checkbox.setEnabled(False)
            delete_audio_checkbox.setToolTip(
                self._disabled_delete_audio_tooltip(has_related_audio=bool(related_audio_paths))
            )
        box.setCheckBox(delete_audio_checkbox)
        box.setEscapeButton(QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.button(QMessageBox.Yes).setText(confirm_button_text)
        box.button(QMessageBox.No).setText("取消")
        accepted = box.exec_() == QMessageBox.Yes
        should_delete_audio = accepted and delete_audio_checkbox.isEnabled() and delete_audio_checkbox.isChecked()
        return accepted, should_delete_audio

    @staticmethod
    def _disabled_delete_audio_tooltip(has_related_audio: bool) -> str:
        """返回删除语音勾选框不可用时的提示文本。"""
        if has_related_audio:
            return "相关语音仍被其他对话引用，删除当前内容时会保留这些语音。"
        return "没有相关语音可删除。"

    def _delete_audio_for_messages_if_requested(
        self,
        messages: list[Message],
        should_delete_audio: bool,
    ) -> bool:
        """按需删除给定消息中不再被引用的音频，并返回是否存在删除失败。"""
        if not should_delete_audio:
            return False
        result = self.chat_manager.delete_unreferenced_audio_files_for_messages(messages)
        return bool(result.failed_paths)

    def _show_delete_success_message(self, success_message: str, audio_failed: bool) -> None:
        """在消息栏展示删除结果。"""
        self.messages_box.clear()
        if audio_failed:
            self.messages_box.append("已删除对话内容，但部分音频文件删除失败。")
        else:
            self.messages_box.append(success_message)

    def switch_to_next_chat(self) -> None:
        """
        切换到侧栏中的下一条普通对话。
        """
        chats = self.single_character_chats()
        if not chats:
            return
        current_index = next((index for index, chat in enumerate(chats) if chat.chat_id == self.current_chat_id), 0)
        next_chat = chats[(current_index + 1) % len(chats)]
        self.switch_chat_by_id(next_chat.chat_id)

    def switch_chat_by_id(self, chat_id: str) -> None:
        """
        切换当前普通对话。
        """
        if chat_id == self.current_chat_id:
            return
        if self.is_chat_busy():
            QMessageBox.information(self, "请稍等", "请等待当前回复完成后再切换对话。")
            self.refresh_chat_list()
            return
        chat = self.chat_manager.get_chat_by_id(chat_id)
        if chat is None:
            return
        if self.character_by_name.get(chat.get_character_name() or "") is None:
            QMessageBox.information(self, "角色缺失", "对话对应的角色已经被删除，无法加载。")
            return

        self.current_chat_id = chat.chat_id
        self.sync_current_chat_to_backends()
        self.apply_current_chat_ui_state()
        self.refresh_chat_list()

    def sync_current_chat_to_backends(self) -> None:
        """
        将当前对话同步给后端与 Live2D 进程。
        """
        self.qt2dp_queue.put({"type": "switch_chat", "chat_id": self.current_chat_id})
        character_name = self.current_character.character_name
        model_json = self.current_chat.get_custom_live2d_model_meta(character_name)
        if not self._prepare_live2d_model_for_switch(model_json, "加载对话 Live2D 模型"):
            model_json = None
        self._send_live2d_switch(character_name, model_json)

    def apply_current_chat_ui_state(self) -> None:
        """
        根据当前对话角色刷新主题、图标、聊天显示与语音设置。
        """
        self.chat_display.clear_chat()
        self._current_theme_color = self._get_theme_color()
        if self.current_character.qt_css is not None:
            color = ThemeManager.get_QT_style_theme_color(self.current_character.qt_css)
            self.setStyleSheet(ThemeManager.generate_stylesheet(color))
            self.set_btn_color(color)
        else:
            self.setStyleSheet(ThemeManager.generate_stylesheet("#7799CC"))
            self.set_btn_color("#7799CC")
        self.refresh_current_chat_display()
        self._load_tool_call_records_cache()
        self._refresh_input_option_buttons()
        if self.current_character.icon_path is not None:
            self.setWindowIcon(QIcon(self.current_character.icon_path))
        self.talk_speed_reset()
        self.pause_second_reset()
        self.dp_chat.if_generate_audio = self.current_character.has_valid_voice_model()
        self.audio_gen.request_preload_character(self.current_character)

    def _setup_input_commands(self, input_panel: QFrame) -> None:
        """初始化输入框命令注册表、匹配器与命令栏控件。"""
        self.input_command_specs: tuple[CommandSpec, ...] = build_default_input_command_specs()
        self.input_command_matcher = InputCommandMatcher(self.input_command_specs)
        self.input_command_palette = InputCommandPalette(self.input_command_matcher, self)
        self.input_command_palette.set_screen_height(self.screen.height())
        self.input_command_palette.attach_to_input(self.user_input.text_edit, input_panel)
        self.input_command_palette.commandSelected.connect(self._on_input_command_selected)  # noqa
        self.input_command_palette.set_theme_color(self._current_theme_color if hasattr(self, "_current_theme_color") else "#7799CC")

    def _create_input_panel(self) -> QFrame:
        """创建带底部工具行的 Codex 风格输入面板。"""
        self.input_panel = QFrame()
        self.input_panel.setObjectName("messageInputPanel")
        self.input_panel.setMinimumHeight(int(self.screen.height()*0.087))

        panel_layout = QVBoxLayout()
        panel_layout.setContentsMargins(10, 7, 10, 8)
        panel_layout.setSpacing(4)

        panel_layout.addWidget(self.user_input)

        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(6)

        self.tool_calling_toggle_button = QToolButton()
        self.tool_calling_toggle_button.setObjectName("toolCallingToggleButton")
        self.tool_calling_toggle_button.setCheckable(True)
        self.tool_calling_toggle_button.setFixedHeight(self.input_tool_button_height)
        self.tool_calling_toggle_button.setMinimumWidth(max(46, int(self.input_tool_button_height * 1.8)))
        self.tool_calling_toggle_button.clicked.connect(self._set_tool_calling_enabled)  # noqa

        self.reasoning_menu_button = QToolButton()
        self.reasoning_menu_button.setObjectName("reasoningMenuButton")
        self.reasoning_menu_button.setPopupMode(QToolButton.InstantPopup)
        self.reasoning_menu_button.setToolTip("设置当前对话的推理与推理强度")
        self.reasoning_menu_button.setMenu(self._build_reasoning_menu())
        self.reasoning_menu_button.setFixedHeight(self.input_tool_button_height)
        self._refresh_input_option_buttons()

        bottom_layout.addWidget(self.tool_calling_toggle_button, 0)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.context_usage_indicator, 0)
        bottom_layout.addWidget(self.reasoning_menu_button, 0)
        bottom_layout.addWidget(self.voice_button, 0)
        bottom_layout.addWidget(self.send_button, 0)
        panel_layout.addLayout(bottom_layout)

        self.input_panel.setLayout(panel_layout)
        self._apply_input_panel_style("#7799CC")
        return self.input_panel

    def _build_reasoning_menu(self) -> QMenu:
        """创建推理设置菜单。"""
        menu = QMenu(self)

        enabled_menu = menu.addMenu("开启推理")
        for enabled_value, label in self.reasoning_enabled_labels.items():
            action = QAction(label, self)
            action.setCheckable(True)
            action.setData(f"enabled:{enabled_value}")
            action.triggered.connect(
                lambda checked=False, value=enabled_value: self._set_reasoning_enabled(value)
            )  # noqa
            enabled_menu.addAction(action)

        effort_menu = menu.addMenu("推理强度")
        for effort_value, label in self.reasoning_effort_labels.items():
            action = QAction(label, self)
            action.setCheckable(True)
            action.setData(f"effort:{effort_value}")
            action.triggered.connect(
                lambda checked=False, value=effort_value: self._set_reasoning_effort(value)
            )  # noqa
            effort_menu.addAction(action)

        return menu

    def _set_tool_calling_enabled(self, enabled: bool) -> None:
        """修改当前对话的工具调用开关配置。"""
        self.current_chat.meta.tool_calling_enabled = bool(enabled)
        self._refresh_tool_calling_button()
        self._save_tool_calling_config()

    def _refresh_input_option_buttons(self) -> None:
        """刷新输入栏中的对话级选项按钮。"""
        self._refresh_tool_calling_button()
        self._refresh_reasoning_button()

    def _refresh_tool_calling_button(self) -> None:
        """根据当前对话配置刷新工具调用按钮文本和选中状态。"""
        if not hasattr(self, "tool_calling_toggle_button"):
            return
        enabled = bool(self.current_chat.meta.tool_calling_enabled)
        self.tool_calling_toggle_button.setChecked(enabled)
        self.tool_calling_toggle_button.setText("工具 开" if enabled else "工具 关")
        self.tool_calling_toggle_button.setToolTip(
            "当前对话允许模型调用工具"
            if enabled
            else "当前对话不会调用工具"
        )

    def _save_tool_calling_config(self) -> None:
        """保存当前对话的工具调用配置。"""
        try:
            self.chat_manager.save()
            self.setWindowTitle("已更新工具调用设置")
        except Exception:
            logger.exception("保存工具调用设置失败")
            self.setWindowTitle("工具调用设置保存失败！")

    def _set_reasoning_enabled(self, enabled: str) -> None:
        """修改当前对话的推理开关配置。"""
        if enabled not in self.reasoning_enabled_labels:
            enabled = "auto"
        self.current_chat.meta.llm_reasoning.enabled = enabled
        self._refresh_reasoning_button()
        self._save_reasoning_config()

    def _set_reasoning_effort(self, effort: str) -> None:
        """修改当前对话的推理强度配置。"""
        if effort not in self.reasoning_effort_labels:
            effort = "default"
        self.current_chat.meta.llm_reasoning.effort = effort
        self._refresh_reasoning_button()
        self._save_reasoning_config()

    def _refresh_reasoning_button(self) -> None:
        """根据当前对话配置刷新推理按钮文本和菜单选中状态。"""
        reasoning_meta = self.current_chat.meta.llm_reasoning
        if reasoning_meta.enabled not in self.reasoning_enabled_labels:
            reasoning_meta.enabled = "auto"
        if reasoning_meta.effort not in self.reasoning_effort_labels:
            reasoning_meta.effort = "default"

        enabled_label = self.reasoning_enabled_labels[reasoning_meta.enabled]
        effort_label = self.reasoning_effort_labels[reasoning_meta.effort]

        if reasoning_meta.enabled == "off":
            self.reasoning_menu_button.setText(f"{enabled_label}")
        else:
            self.reasoning_menu_button.setText(f"{enabled_label} {effort_label}")

        menu = self.reasoning_menu_button.menu()
        if menu is None:
            return

        for action in menu.actions():
            if action.text() == "推理强度":
                action.setEnabled(reasoning_meta.enabled != "off")  #当推理关闭时，强度设置不可用

            sub_menu = action.menu()
            if sub_menu is None:
                continue
            for sub_action in sub_menu.actions():
                data = sub_action.data()
                sub_action.setChecked(
                    data == f"enabled:{reasoning_meta.enabled}"
                    or data == f"effort:{reasoning_meta.effort}"
                )

    def _save_reasoning_config(self) -> None:
        """保存当前对话的推理配置。"""
        try:
            self.chat_manager.save()
            self.setWindowTitle("已更新推理设置")
        except Exception:
            logger.exception("保存推理设置失败")
            self.setWindowTitle("推理设置保存失败！")

    def _apply_input_panel_style(self, color: str) -> None:
        """根据主题色刷新输入面板局部样式。"""
        self._set_voice_button_icon_color(color)
        if hasattr(self, "input_command_palette"):
            self.input_command_palette.set_theme_color(color)
        send_color = QColor(color)
        if not send_color.isValid():
            send_color = QColor("#7799CC")
        if hasattr(self, "context_usage_indicator"):
            self.context_usage_indicator.set_theme_color(send_color.name())
        self._apply_update_banner_style(send_color.name())
        send_hover_color = send_color.lighter(112).name()
        send_pressed_color = send_color.darker(108).name()
        if hasattr(self, "tool_calling_toggle_button"):
            self.tool_calling_toggle_button.setStyleSheet(f"""
                QToolButton#toolCallingToggleButton {{
                    color: {color};
                    background-color: rgba(0, 0, 0, 0.035);
                    border: 1px solid rgba(0, 0, 0, 0.08);
                    border-radius: 9px;
                    padding: 0px 9px;
                }}
                QToolButton#toolCallingToggleButton:hover {{
                    background-color: rgba(0, 0, 0, 0.07);
                }}
                QToolButton#toolCallingToggleButton:checked {{
                    color: #FFFFFF;
                    background-color: {send_color.name()};
                    border: 1px solid {send_color.name()};
                }}
                QToolButton#toolCallingToggleButton:checked:hover {{
                    background-color: {send_hover_color};
                    border: 1px solid {send_hover_color};
                }}
                QToolButton#toolCallingToggleButton:checked:pressed {{
                    background-color: {send_pressed_color};
                    border: 1px solid {send_pressed_color};
                }}
            """)
        self.input_panel.setStyleSheet(f"""
            QFrame#messageInputPanel {{
                background-color: #FFFFFF;
                border: 2px solid {color};
                border-radius: 13px;
            }}
        """)
        self.user_input.set_theme_color(color)
        self.user_input.refresh_height()
        self.reasoning_menu_button.setStyleSheet(f"""
            QToolButton#reasoningMenuButton {{
                color: {color};
                background-color: rgba(0, 0, 0, 0.035);
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 9px;
                padding: 0px 9px;
            }}
            QToolButton#reasoningMenuButton:hover {{
                background-color: rgba(0, 0, 0, 0.07);
            }}
            QToolButton#reasoningMenuButton::menu-indicator {{
                image: none;
                width: 0px;
            }}
        """)
        self.voice_button.setStyleSheet(f"""
            QPushButton#voiceInputButton {{
                color: {color};
                background-color: rgba(0, 0, 0, 0.035);
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 9px;
                padding: 0px;
            }}
            QPushButton#voiceInputButton:hover {{
                background-color: rgba(0, 0, 0, 0.07);
            }}
            QPushButton#voiceInputButton:pressed {{
                background-color: rgba(0, 0, 0, 0.11);
            }}
            QPushButton#voiceInputButton:disabled {{
                background-color: rgba(0, 0, 0, 0.025);
                border: 1px solid rgba(0, 0, 0, 0.055);
            }}
        """)
        self.send_button.setStyleSheet(f"""
            QToolButton#sendMessageButton {{
                color: #FFFFFF;
                background-color: {send_color.name()};
                border: none;
                border-radius: {int(self.input_tool_button_height*0.5)}px;
                padding: 0px;
                font-size: {int(self.input_tool_button_height*0.68)}px;
                font-weight: bold;
            }}
            QToolButton#sendMessageButton:hover {{
                background-color: {send_hover_color};
            }}
            QToolButton#sendMessageButton:pressed {{
                background-color: {send_pressed_color};
            }}
        """)

    def _set_voice_button_icon_color(self, color: str) -> None:
        """将语音输入图标重绘为当前主题色。"""
        image = QImage("./icons/microphone.png")
        if image.isNull():
            return

        themed_image = image.convertToFormat(QImage.Format_ARGB32)
        theme_color = QColor(color)
        for y in range(themed_image.height()):
            for x in range(themed_image.width()):
                pixel_color = themed_image.pixelColor(x, y)
                alpha = pixel_color.alpha()
                if alpha > 0:
                    pixel_color.setRed(theme_color.red())
                    pixel_color.setGreen(theme_color.green())
                    pixel_color.setBlue(theme_color.blue())
                    pixel_color.setAlpha(alpha)
                    themed_image.setPixelColor(x, y, pixel_color)

        pixmap = QPixmap.fromImage(themed_image)
        icon = QIcon()
        icon.addPixmap(pixmap, QIcon.Normal, QIcon.Off)
        icon.addPixmap(pixmap, QIcon.Disabled, QIcon.Off)
        self.voice_button.setIcon(icon)

    def _load_tool_call_records_cache(self):
        self.tool_call_records_cache = {}
        records = self.current_chat.get_tool_call_record_dicts()
        for one in records:
            tool_call_id = str(one.get("tool_call_id") or "")
            if tool_call_id:
                self.tool_call_records_cache[tool_call_id] = one

    def _append_tool_status_line(self, tool_call_id: str, text: str, status: str = "running"):
        self.chat_display.append_tool_status_line(tool_call_id, text, status=status)

    def _handle_tool_call_start_event(self, payload: dict):
        tool_call_id = str(payload.get("tool_call_id") or "")
        if not tool_call_id:
            return
        self.tool_call_records_cache[tool_call_id] = payload
        tool_name = str(payload.get("tool_name") or "unknown")
        self._append_tool_status_line(tool_call_id, f"<正在调用工具...>", status="running")

    def _handle_tool_call_update_event(self, payload: dict):
        tool_call_id = str(payload.get("tool_call_id") or "")
        if not tool_call_id:
            return
        self.tool_call_records_cache[tool_call_id] = payload
        tool_name = str(payload.get("tool_name") or "unknown")
        duration_sec = float(payload.get("duration_sec") or 0.0)
        self._append_tool_status_line(tool_call_id, f"<工具调用完成 {duration_sec:.2f}秒>", status="completed")

    def _open_tool_call_dialog(self, tool_call_id: str):
        record = self.tool_call_records_cache.get(tool_call_id)
        if record is None:
            record = {
                "tool_call_id": tool_call_id,
                "tool_name": "unknown",
                "status": "running",
                "result_content": "未找到该工具调用记录。",
            }
        dialog = ToolCallInfoDialog(self, record,win_size=(int(self.screen.width()*0.2), int(self.screen.height()*0.3)))
        dialog.exec_()

    def _get_theme_color(self) -> str:
        """获取当前角色的主题色"""
        if self.current_character.qt_css is not None:
            return ThemeManager.get_QT_style_theme_color(self.current_character.qt_css)
        return '#7799CC'

    def schedule_context_usage_refresh(self, delay_ms: int = 100) -> None:
        """合并并延迟刷新上下文 token 用量组件。"""
        if not hasattr(self, "_context_usage_refresh_timer"):
            return
        self._context_usage_refresh_timer.start(max(0, delay_ms))

    @pyqtSlot(float)
    def _set_summary_compression_threshold(self, ratio: float) -> None:
        """保存用户在上下文用量浮窗中设置的压缩阈值。"""
        normalized_ratio = max(0.20, min(0.80, round(float(ratio) * 20) / 20))
        d_sakiko_config.set(
            d_sakiko_config.rolling_summary_trigger_ratio,
            normalized_ratio,
        )
        self.context_usage_indicator.set_summary_threshold_ratio(normalized_ratio)

    def refresh_context_usage_indicator(self) -> None:
        """重新计算当前对话上下文 token 用量并刷新圆环组件。"""
        if not hasattr(self, "context_usage_indicator"):
            return
        model = self._current_litellm_model_name()
        self._model_limit_query_thread.model = model
        self._model_limit_query_thread.start()

    @pyqtSlot(object)
    def _on_model_limit_queryed(self, token_limit: int | None) -> None:
        self.context_usage_indicator.set_snapshot(self._build_context_usage_snapshot(token_limit))

    def _build_context_usage_snapshot(self, token_limit: int | None) -> ContextUsageSnapshot:
        """构造当前普通聊天下一次请求会携带的上下文 token 快照。"""
        model = self._current_litellm_model_name()
        try:
            messages = self._build_context_usage_messages()
            used_tokens = count_message_tokens(model=model, messages=messages)
        except Exception as exc:
            logger.warning("上下文 token 统计失败：%s", exc)
            return ContextUsageSnapshot(
                used_tokens=None,
                token_limit=token_limit,
                error_message=str(exc),
            )

        return ContextUsageSnapshot(
            used_tokens=max(0, used_tokens),
            token_limit=token_limit,
        )

    def _build_context_usage_messages(self) -> list[dict[str, object]]:
        """生成用于 token 统计的消息列表，不包含输入框中尚未发送的文本。"""
        character_name = self.current_character.character_name
        messages = self._normalize_llm_messages(
            build_llm_query_with_rolling_summary(
                self.current_chat,
                perspective=character_name,
                is_simplify=True,
                include_translation=getattr(self.dp_chat, "audio_language_choice", "") == "日英混合",
            )
        )
        runtime_system_instruction = self._context_runtime_system_instruction()
        if runtime_system_instruction:
            self._append_context_runtime_system_instruction(messages, runtime_system_instruction)
        messages.append({"role": "user", "content": self._context_runtime_controls()})
        return messages

    def _context_runtime_system_instruction(self) -> str:
        """获取 token 统计用的运行期系统提示词。"""
        builder = getattr(self.dp_chat, "_build_runtime_system_instruction", None)
        if not callable(builder):
            return ""
        try:
            return str(builder() or "")
        except Exception:
            logger.exception("生成 token 统计用运行期系统提示词失败。")
            return ""

    @staticmethod
    def _append_context_runtime_system_instruction(messages: list[dict[str, object]], instruction: str) -> None:
        """把运行期系统提示词追加到第一条 system 消息，或插入新的 system 消息。"""
        for message in messages:
            if message.get("role") == "system":
                content = str(message.get("content") or "")
                message["content"] = content + "\n" + instruction
                return
        messages.insert(0, {"role": "system", "content": instruction})

    def _context_runtime_controls(self) -> str:
        """根据 UI 当前对话状态构造 token 统计用运行时控制消息。"""
        reply_language = (
            "ja_with_zh_translation"
            if getattr(self.dp_chat, "audio_language_choice", "") == "日英混合"
            else "zh_only"
        )
        sakiko_tone = "none"
        if self.current_character.character_name == "祥子":
            sakiko_tone = "dark" if getattr(self.dp_chat, "sakiko_state", True) else "light"
        return (
            "<runtime_controls>\n"
            f"reply_language: {reply_language}\n"
            f"sakiko_tone: {sakiko_tone}\n"
            "</runtime_controls>"
        )

    @staticmethod
    def _normalize_llm_messages(raw_messages: object) -> list[dict[str, object]]:
        """将未知来源的 messages 数据整理为 token_counter 可接收的字典列表。"""
        if not isinstance(raw_messages, list):
            return []
        messages: list[dict[str, object]] = []
        for raw_message in raw_messages:
            if not isinstance(raw_message, dict):
                continue
            message: dict[str, object] = {}
            for key, value in raw_message.items():
                if isinstance(key, str):
                    message[key] = value
            if "role" in message and "content" in message:
                messages.append(message)
        return messages

    def _current_litellm_model_name(self) -> str:
        """按照当前配置解析 LiteLLM 实际使用的模型名称。"""
        if d_sakiko_config.use_default_deepseek_api.value:
            return "deepseek/deepseek-v4-flash"
        if d_sakiko_config.enable_custom_llm_api_provider.value:
            custom_model = str(d_sakiko_config.custom_llm_api_model.value or "")
            return ensure_openai_compatible_model(custom_model)

        provider = str(d_sakiko_config.llm_api_provider.value or "")
        configured_models = d_sakiko_config.llm_api_model.value
        model_name = str(configured_models.get(provider, "") if isinstance(configured_models, dict) else "")
        if provider in THIRD_PARTY_OPENAI_COMPAT_PROVIDER_IDS:
            return ensure_openai_compatible_model(model_name)
        return self._concat_provider_and_model(provider, model_name)

    @staticmethod
    def _concat_provider_and_model(provider: str, model: str) -> str:
        """将 provider 和 model 合并为 LiteLLM 常用的 provider/model 形式。"""
        model = model.strip()
        provider = provider.strip()
        if not model:
            return provider
        if "/" in model or not provider:
            return model
        return f"{provider}/{model}"

    def _current_model_supports_vision(self) -> bool:
        """判断当前选择的大模型是否支持视觉输入。"""
        model = self._current_litellm_model_name()
        return model_supports_image_upload(
            model,
            use_default_deepseek_api=bool(d_sakiko_config.use_default_deepseek_api.value),
        )

    def _current_model_can_force_image_upload(self) -> bool:
        """判断当前模型是否允许用户手动强制开启图片上传。"""
        model = self._current_litellm_model_name()
        return model_can_force_allow_image_upload(
            model,
            use_default_deepseek_api=bool(d_sakiko_config.use_default_deepseek_api.value),
        )

    def _force_allow_current_model_image_upload(self, model: str) -> bool:
        """将用户确认过的模型写入图片上传白名单。"""
        try:
            add_model_image_upload_force_allow(model)
            return True
        except Exception:
            logger.exception("写入图片上传白名单失败：model=%s", model)
            return False

    def refresh_current_chat_display(self):
        """从当前 Chat 数据重新渲染聊天显示。"""
        self.chat_display.render_chat(self.current_chat)
        self.schedule_context_usage_refresh()

    def set_btn_color(self,color):
        #更改按钮图标颜色-----------------------
        icon_map = {
            self.setting_btn: './icons/setting.svg',
            self.change_character_button: './icons/chat_list.svg',
            self.save_dialog_btn: './icons/save.svg',
            self.more_function_button: './icons/more.svg'
        }
        for btn,file in icon_map.items():
            try:
                with open(file,'r',encoding='utf-8') as f:
                    orig_data=f.read()
                pattern = r'fill=(["\'])(.*?)\1'
                replacement = f'fill="{color}"'
                new_data = re.sub(pattern, replacement, orig_data)
                svg_bytes = new_data.encode('utf-8')

                image = QImage.fromData(svg_bytes)
                pixmap = QPixmap.fromImage(image)
                btn.setIcon(QIcon(pixmap))
            except:
                pass
        # 更新主题色并从 Chat 数据重新生成 HTML（不再使用正则替换旧 HTML 中的颜色）
        self._current_theme_color = color
        self.chat_display.set_theme_color(color)
        self._apply_input_panel_style(color)
        self._refresh_input_option_buttons()
        self.refresh_current_chat_display()
        # 保存新颜色到本地以及修改内存中的颜色
        try:
            with open(f"../reference_audio/{self.current_character.character_folder_name}/QT_style.json",'w',encoding='utf-8') as f:
                f.write(f'''QWidget {{
                color: {color};
                }}''')
            self.current_character.qt_css=f'''
                                                        QWidget {{
                                                                color: {color};
                                                                }}'''
        except Exception:
            logger.exception("保存主题色失败")



    def set_pause_second(self):
        pause_second_value=self.pause_second_slider.value()
        self.audio_gen.pause_second=pause_second_value/100
        self.pause_second_label.setText(f"句间停顿时间(s)：{self.audio_gen.pause_second:.2f}")
        self.saved_talk_speed_and_pause_second[self.current_character.character_name]['pause_second']=self.audio_gen.pause_second  # noqa


    def open_setting_window(self):
        color =ThemeManager.get_QT_style_theme_color(self.current_character.qt_css) if self.current_character.qt_css is not None else "#7799CC"

        setting_window=SettingWindow(self,self.screen,color,self.audio_gen)
        setting_window.exec_()
        self.schedule_context_usage_refresh()

    def open_more_function_window(self):
        more_function_win=MoreFunctionWindow(
            self.close_program,
            self.check_update_manual,
            self.check_repair_manual,
            has_update=self.pending_update_plan is not None,
        )
        more_function_win.exec_()

    def close_program(self):
        self.run_input_command_text('bye', 'more_function_button')

    def talk_speed_reset(self):
        saved_speed=self.saved_talk_speed_and_pause_second[self.current_character.character_name]['talk_speed']  # noqa
        self.talk_speed_slider.setValue(int(saved_speed*100))
        self.audio_gen.speed=saved_speed
        self.talk_speed_label.setText(f"语速调节：{self.audio_gen.speed:.2f}")

    def pause_second_reset(self):
        saved_value=self.saved_talk_speed_and_pause_second[self.current_character.character_name]['pause_second']  # noqa
        self.pause_second_slider.setValue(int(saved_value*100))
        self.audio_gen.pause_second = saved_value
        self.pause_second_label.setText(f"句间停顿时间(s)：{self.audio_gen.pause_second:.2f}")


    def set_talk_speed(self):
        speed_value=self.talk_speed_slider.value()
        self.audio_gen.speed=speed_value/100
        self.talk_speed_label.setText(f"语速调节：{self.audio_gen.speed:.2f}")
        self.saved_talk_speed_and_pause_second[self.current_character.character_name]['talk_speed']=self.audio_gen.speed  # noqa

    def load_whisper_model(self):
        self.model_loader= ModelLoaderThread("./pretrained_models/faster_whisper_small", device="cpu", compute_type="int8")
        self.model_loader.model_loaded.connect(self.on_model_loaded)  # noqa
        self.model_loader.model_load_failed.connect(self.on_model_load_failed)  # noqa
        self.model_loader.start()

    def on_model_load_failed(self,error_message):
        self.setWindowTitle(f"语音模型加载失败: {error_message}")

    def on_model_loaded(self,model):
        self.whisper_model=model
        self.setWindowTitle("数字小祥")
        self.voice_button.setEnabled(True)

    def start_input_stream(self):
        try:
            self.stream = sd.InputStream(
                samplerate=16000,
                channels=1,
                callback=self.audio_callback,
                dtype='int16'  # 使用 int16 格式
            )
            # 啟動串流，它將在背景執行緒中持續呼叫 audio_callback
            self.stream.start()

        except Exception:
            logger.warning("无法启动麦克风串流，本次运行无法使用语音输入，请检查麦克风是否连接或被其他程序占用。", exc_info=True)
            self.voice_button.setEnabled(False)  # 保持按鈕禁用

    def stop_input_stream(self):
        if hasattr(self, "stream") and self.stream:
            try:
                self.stream.stop()
                self.stream.close()
                self.stream = None
            except Exception:
                logger.exception("关闭串流时出错")

    def audio_callback(self, indata, frames, time, status):
        if self.is_recording:
            self.record_data.append(indata.copy())


    def check_valid(self):
        self.voice_is_valid=True

    def voice_dectect(self):
        self.setWindowTitle("正在准备录音...")
        self.start_input_stream()
        if not hasattr(self, 'stream') or self.stream is None:
            return

        self.voice_is_valid=False
        self.is_recording=True
        self.record_data=[]
        self.record_timer.start(300)
        self.run_input_command_text('start_talking', 'voice_button')
        QTimer.singleShot(1000, lambda: self.setWindowTitle("正在录音...松开结束"))

    def voice_decect_end(self):
        if not self.is_recording:
            return # 防止重复触发

        self.setWindowTitle("数字小祥")
        self.is_recording=False
        self.record_timer.stop()
        self.run_input_command_text('stop_talking', 'voice_button')

        self.stop_input_stream()

        if not self.voice_is_valid:
            self.setWindowTitle("录音时间过短，请重试...")
            return
        else:
            try:
                if not self.record_data:
                    self.setWindowTitle("录音失败，没有捕获到音频数据。")
                    return

                audio_data = np.concatenate(self.record_data, axis=0).flatten()  # noqa
                audio_data = audio_data.astype(np.float32) / 32768.0

            except ValueError:
                self.setWindowTitle("录音数据处理失败，请重试。")
                return
            self.setWindowTitle("正在识别语音...")
            self.run_transcription_thread(audio_data)

    def run_transcription_thread(self, audio_data):
        self.voice_button.setEnabled(False)
        self.transcription_worker = TranscriptionWorker(self.whisper_model)
        self.transcription_thread = QThread()
        self.transcription_worker.moveToThread(self.transcription_thread)
        self.transcription_thread.started.connect(lambda: self.transcription_worker.transcribe(audio_data))  # noqa
        self.transcription_worker.finished.connect(self.on_transcription_finished)  # noqa

        self.transcription_thread.finished.connect(self.transcription_thread.deleteLater)  # noqa
        self.transcription_worker.finished.connect(self.transcription_thread.quit)  # noqa
        self.transcription_worker.finished.connect(self.transcription_worker.deleteLater)  # noqa

        self.transcription_thread.start()

    def on_transcription_finished(self, text: str) -> None:
        """将语音识别结果转换为简体中文后插入当前光标位置。"""
        cc = OpenCC('tw2s.json')
        text=cc.convert(text)
        if text=='切换角色':
            self.run_input_command_text('s', 'speech_recognition')
        elif text=='切换语言':
            self.run_input_command_text('l', 'speech_recognition')
        else:
            self.user_input.insert_text_at_cursor(text)

        if self.whisper_model and not self.is_recording:
            self.voice_button.setEnabled(True)
            self.setWindowTitle("数字小祥")

    def closeEvent(self, a0):
        try:
            self.chat_manager.save()
            self.stop_input_stream()
        finally:
            a0.accept()

    def play_history_audio(self,audio_path_and_emotion):
        if self.motion_complete_value.value:
            self.setWindowTitle("数字小祥")
            audio_path_and_emotion=audio_path_and_emotion.toString()
            if "silence.wav" in audio_path_and_emotion:
                return
            msg_index = None
            # 去除新增的 ?msg= 锚点参数
            if '?msg=' in audio_path_and_emotion:
                audio_path_and_emotion, msg_index_text = audio_path_and_emotion.split('?msg=', 1)
                try:
                    msg_index = int(msg_index_text)
                except ValueError:
                    msg_index = None
            if audio_path_and_emotion in ("user:", "no_audio:"):
                logger.info("点击到用户消息或无音频的消息，无法播放")
                return

            match=re.match(r"(.+?)\[(.+?)\]$", audio_path_and_emotion)
            if match:
                audio_path = match.group(1)  # 路径
                emotion = match.group(2)  #emotion标签

                if not audio_path or audio_path == "NO_AUDIO" or os.path.isdir(audio_path):
                    logger.info("点击到无效音频路径，无法播放：%s", audio_path)
                    return

                if os.path.isfile(audio_path):
                    #----------------------------设置live2d文本框内容逻辑
                    target_msg = None
                    # 按照 msg_index 属性寻找对应的消息条目
                    if msg_index is not None and 0 <= msg_index < len(self.current_chat.message_list):
                        target_msg = self.current_chat.message_list[msg_index]
                    else:
                        filename=os.path.basename(audio_path)
                        for msg in self.current_chat.message_list:
                            if os.path.basename(msg.audio_path) == filename:
                                target_msg = msg
                                break
                    # 如果能找到对应的消息，那么添加翻译后一同发送给 live2d 模块，从而显示翻译。
                    if target_msg is not None:
                        self.live2d_text_queue.put(
                            self._format_live2d_display_text(target_msg.text, target_msg.translation)
                        )

                    # ----------------------------
                    self.audio_file_path_queue.put(audio_path)
                    self.emotion_queue.put(emotion)
                    logger.info("音频文件路径：%s", audio_path)
                    #print("注意：若你已经设置了if_delete_audio_cache.txt中的数字不为0，并且觉得这句生成的还不错，请复制该音频文件到别处，因为设置数字不为0的情况下关闭程序会自动删除该文件，以释放空间。设置数字不为0的情况下如果希望下次打开程序还能听到，再把这个文件复制回这个路径即可。\n")
                else:
                    self.setWindowTitle('所选文本对应的音频文件已经删除...')
                    logger.info("所选文本对应的音频文件已经删除。")
        else:
            self.setWindowTitle('请等待当前过程完成后重试...')


    def delete_message(self, msg_index: int) -> None:
        """删除当前普通聊天中的单条消息。"""
        if not (0 <= msg_index < len(self.current_chat.message_list)):
            self.refresh_current_chat_display()    #如果索引无效，强制刷新显示以纠正可能的错误状态
            return

        self._delete_current_chat_message_range(
            start=msg_index,
            end=msg_index + 1,
            prompt="确定删除这条消息吗？",
            success_message="已删除消息",
        )

    def delete_turn(self, msg_index: int) -> None:
        """删除当前普通聊天中指定消息所属的完整轮次。"""
        if not (0 <= msg_index < len(self.current_chat.message_list)):
            self.refresh_current_chat_display()
            return

        message_range = self.current_chat.find_turn_range(msg_index)
        selected_message = self.current_chat.message_list[msg_index]
        preview_messages = self.current_chat.message_list[message_range.start:message_range.end]
        preview = self._build_delete_preview_from_messages(
            message_range.start,
            message_range.end,
            list(preview_messages),
        )

        if preview.deleted_user_count == 0:
            prompt = "确定删除这组开场消息吗？"
        elif selected_message.character_name == "User":
            prompt = "确定删除这一轮对话吗？"
        else:
            prompt = "确定删除这条角色消息所属的整轮对话吗？\n这会连带删除之前你发送的消息。"

        self._delete_current_chat_message_range(
            start=message_range.start,
            end=message_range.end,
            prompt=prompt,
            success_message="已删除这一轮对话",
        )

    def edit_message(self, msg_index: int) -> None:
        """编辑当前普通聊天中的单条消息。"""
        if not (0 <= msg_index < len(self.current_chat.message_list)):
            self.refresh_current_chat_display()
            return
        if not self._ensure_delete_operation_allowed():
            return

        message = self.current_chat.message_list[msg_index]
        if not Chat.can_edit_message(message):
            self.messages_box.clear()
            self.messages_box.append("这条消息不能编辑。")
            return

        dialog = MessageEditDialog(message, self)
        if dialog.exec_() != QDialog.Accepted:
            return

        message.text = dialog.edited_text()
        if message.character_name != "User":
            message.translation = dialog.edited_translation()
        self._save_after_message_content_edit(msg_index)
        self.messages_box.clear()
        self.messages_box.append("已保存消息修改。")

    def edit_and_resend_user_message(self, msg_index: int) -> None:
        """将历史用户消息移回输入框，并删除该消息及后续消息。"""
        if not (0 <= msg_index < len(self.current_chat.message_list)):
            self.refresh_current_chat_display()
            return
        if not self._ensure_delete_operation_allowed():
            return

        message = self.current_chat.message_list[msg_index]
        if not Chat.can_edit_and_resend_user_message(message):
            self.messages_box.clear()
            self.messages_box.append("这条消息不能编辑并重发。")
            return

        original_text = message.text
        image_paths_to_restore = [
            str(resolve_attachment_path(attachment.path))
            for attachment in message.attachments
            if attachment.is_image()
        ]
        end = len(self.current_chat.message_list)
        has_following_messages = end - msg_index > 1
        had_input_draft = bool(self.user_input.toPlainText() or self.user_input.pending_image_source_paths())
        should_delete_audio = False

        if has_following_messages:
            preview = self._build_delete_preview_from_messages(
                msg_index,
                end,
                list(self.current_chat.message_list[msg_index:end]),
            )
            extra_detail_lines: list[str] = []
            if had_input_draft:
                extra_detail_lines.append("当前输入框内容将被覆盖。")
            accepted, should_delete_audio = self._confirm_delete_operation(
                "确定编辑并重发你的这条消息吗？\n这会删除角色后续的回复。",
                preview,
                extra_detail_lines=extra_detail_lines,
            )
            if not accepted:
                return

        audio_failed = self._apply_current_chat_message_range_delete(
            msg_index,
            end,
            should_delete_audio,
        )
        failed_image_paths = self.user_input.replace_draft(original_text, image_paths=image_paths_to_restore)

        if had_input_draft:
            success_message = "已将消息移回输入框，原输入框内容已被替换。"
        elif has_following_messages:
            success_message = "已清除后续消息，可编辑后重新发送。"
        else:
            success_message = "已将消息移回输入框，可编辑后重新发送。"
        if failed_image_paths:
            success_message += " 部分图片附件已丢失，无法恢复。"
        self._show_delete_success_message(success_message, audio_failed)

    def rollback_to_message(self, msg_index: int) -> None:
        """回溯到指定消息，删除该消息之后的所有后续消息。"""
        if not (0 <= msg_index < len(self.current_chat.message_list)):
            self.refresh_current_chat_display()
            return
        if not self._ensure_delete_operation_allowed():
            return

        message = self.current_chat.message_list[msg_index]
        if msg_index >= len(self.current_chat.message_list) - 1 or not Chat.can_rollback_to_message(message):
            self.messages_box.clear()
            self.messages_box.append("这条消息不能作为回溯位置。")
            return

        start = msg_index + 1
        end = len(self.current_chat.message_list)
        preview = self._build_delete_preview_from_messages(
            start,
            end,
            list(self.current_chat.message_list[start:end]),
        )
        accepted, should_delete_audio = self._confirm_delete_operation(
            "确定回溯到这条消息吗？\n这会删除此消息之后的所有后续消息。",
            preview,
            title="回溯确认",
            confirm_button_text="回溯",
        )
        if not accepted:
            return

        audio_failed = self._apply_current_chat_message_range_delete(
            start,
            end,
            should_delete_audio,
        )
        self._show_delete_success_message("已回溯到所选消息。", audio_failed)

    def regenerate_turn_reply(self, msg_index: int) -> None:
        """重新生成最后一条真实用户消息对应的本轮回复。"""
        if not (0 <= msg_index < len(self.current_chat.message_list)):
            self.refresh_current_chat_display()
            return
        if not self._ensure_delete_operation_allowed():
            return

        last_user_index = self.current_chat.find_last_real_user_message_index()
        if msg_index != last_user_index:
            self.messages_box.clear()
            self.messages_box.append("只能重新生成最后一条用户消息的回复。")
            return

        message = self.current_chat.message_list[msg_index]
        user_text = message.text
        start = msg_index + 1
        end = len(self.current_chat.message_list)
        preview = self._build_delete_preview_from_messages(
            start,
            end,
            list(self.current_chat.message_list[start:end]),
        )
        accepted, should_delete_audio = self._confirm_delete_operation(
            "确定重新生成本轮回复吗？\n这会保留你的这条消息，并让角色重新回答。",
            preview,
            title="重新生成确认",
            confirm_button_text="重新生成",
        )
        if not accepted:
            return

        self._apply_current_chat_message_range_delete(
            start,
            end,
            should_delete_audio,
        )
        self._send_user_message_payload(
            user_text,
            append_user_message=False,
            context_usage_delay_ms=1300,
        )
        self.messages_box.clear()
        self.messages_box.append("正在重新生成本轮回复...")

    def _delete_current_chat_message_range(
        self,
        start: int,
        end: int,
        prompt: str,
        success_message: str,
    ) -> None:
        """删除当前对话中的消息范围，并统一处理音频清理和界面刷新。"""
        if not self._ensure_delete_operation_allowed("删除"):
            return
        if not (0 <= start <= end <= len(self.current_chat.message_list)):
            self.refresh_current_chat_display()
            return

        preview = self._build_delete_preview_from_messages(
            start,
            end,
            list(self.current_chat.message_list[start:end]),
        )
        accepted, should_delete_audio = self._confirm_delete_operation(prompt, preview)
        if not accepted:
            return

        audio_failed = self._apply_current_chat_message_range_delete(
            start,
            end,
            should_delete_audio,
        )
        self._show_delete_success_message(success_message, audio_failed)

    def _apply_current_chat_message_range_delete(
        self,
        start: int,
        end: int,
        should_delete_audio: bool,
    ) -> bool:
        """执行当前对话的消息范围删除，并返回是否存在音频删除失败。"""
        result = self.current_chat.delete_message_range(start, end)
        audio_failed = self._delete_audio_for_messages_if_requested(
            result.deleted_messages,
            should_delete_audio,
        )
        self.chat_manager.save()
        self._load_tool_call_records_cache()
        self.refresh_current_chat_display()
        self.refresh_chat_list()
        self.schedule_context_usage_refresh()
        return audio_failed

    def _save_after_message_content_edit(self, msg_index: int) -> None:
        """保存消息内容编辑结果，并刷新相关界面状态。"""
        invalidate_rolling_summary_from_message_index(self.current_chat, msg_index)
        self.chat_manager.save()
        self._load_tool_call_records_cache()
        self.refresh_current_chat_display()
        self.refresh_chat_list()
        self.schedule_context_usage_refresh()

    def regenerate_audio(self, msg_index):
        if hasattr(self, 'regen_thread') and self.regen_thread is not None and self.regen_thread.isRunning():
            WarningWindow("当前正在重新生成音频中，请稍后再试").exec_()
            return
        if not (0 <= msg_index < len(self.current_chat.message_list)):
            self.refresh_current_chat_display()    #如果索引无效，强制刷新显示以纠正可能的错误状态
            return
        current_character= self.current_character
        if not current_character.has_valid_voice_model():
            WarningWindow("当前角色未配置完整的音频生成条件，无法重新生成音频！").exec_()
            return
        # 防止重新生成时重复操作
        # if not self.audio_gen.is_completed or not self.live2d_mod.live2d_this_turn_motion_complete:
        #     QMessageBox.warning(self, "稍后再试", "当前正在播放音频或合成新音频中，稍后再试！")
        #     return

        msg = self.current_chat.message_list[msg_index]
        if msg.text in ("...","我要调用工具"):
            WarningWindow("无法重新生成音频").exec_()
            return
        # UI反聩
        self.setWindowTitle("正在重新生成音频...")
        #self.chat_display.setEnabled(False) # 暂时禁用右键等交互
        # self._regen_audio_timing = {
        #     "started_at": time.perf_counter(),
        #     "msg_index": msg_index,
        #     "text": msg.text,
        # }
        # append_generation_timing_log(
        #     f"重新生成音频开始 | msg_index={msg_index} | text={msg.text[:80]!r}"
        # )

        # 启动后台合成线程
        self.regen_thread = AudioRegenThread(
            self.audio_gen,
            msg.text,
            current_character,
            self.dp_chat.sakiko_state,
            self.dp_chat.audio_language_choice,
            msg_index,
            msg.emotion.as_label(),
        )
        self.regen_thread.finished_signal.connect(self.handle_regenerate_audio_finished)
        self.regen_thread.start()

    def handle_regenerate_audio_finished(self, new_audio_path, msg_index):
        self.setWindowTitle("数字小祥")
        #self.chat_display.setEnabled(True)
        self.regen_thread=None

        if 0 <= msg_index < len(self.current_chat.message_list):
            msg = self.current_chat.message_list[msg_index]
            msg.audio_path = os.path.abspath(new_audio_path).replace('\\', '/')
            self.refresh_current_chat_display()
            logger.info("重新生成音频成功，新路径为：%s", msg.audio_path)
            self.play_history_audio(QUrl(f"{msg.audio_path}[{msg.emotion.as_label()}]?msg={msg_index}"))
            # timing = getattr(self, "_regen_audio_timing", None)
            # if isinstance(timing, dict) and timing.get("msg_index") == msg_index:
            #     elapsed = time.perf_counter() - float(timing.get("started_at", time.perf_counter()))
            #     try:
            #         audio_size = os.path.getsize(msg.audio_path)
            #     except OSError:
            #         audio_size = -1
            #     append_generation_timing_log(
            #         "重新生成音频完成并送入播放队列 | "
            #         f"msg_index={msg_index} | elapsed={elapsed:.3f}s | "
            #         f"audio_size={audio_size} | path={msg.audio_path} | text={msg.text[:80]!r}"
            #     )
            #     self._regen_audio_timing = None

        self._set_message_box_idle()


    def handle_response(self,response_text):
        if isinstance(response_text, dict):
            self._handle_structured_response(response_text)
            return

        if response_text.startswith(TOOL_CALL_START_EVENT_PREFIX):
            payload_str = response_text[len(TOOL_CALL_START_EVENT_PREFIX):]
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                payload = {}
            self._handle_tool_call_start_event(payload)
            return

        if response_text.startswith(TOOL_CALL_UPDATE_EVENT_PREFIX):
            payload_str = response_text[len(TOOL_CALL_UPDATE_EVENT_PREFIX):]
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                payload = {}
            self._handle_tool_call_update_event(payload)
            return

        if response_text=='changechange':
            self.apply_current_chat_ui_state()
            return
        response_text=response_text.replace("\n\n",'')
        response_text=response_text.replace("\n", '')
        response_text = response_text.replace("。。", '。')
        pattern = r'(.*?)(?:\[翻译\](.+?)\[翻译结束\])'
        response_tuple_list=re.findall(pattern,response_text,flags=re.DOTALL)
        if not response_tuple_list: #中文
            display_text = response_text
            translation = ''
        else:   #日文
            display_text=response_tuple_list[0][0]  # noqa
            translation=response_tuple_list[0][1]  # noqa

        character_name = self.current_character.character_name

        # 找到下一条未处理的 AI 消息（可能有多段）
        # 从尾部向前搜索尚未设置 audio_path 的 AI 消息中最早的那条
        target_msg = None
        msg_index = None
        for i, msg in enumerate(self.current_chat.message_list):
            if msg.character_name == character_name and msg.audio_path == "" and msg.audio_path != "NO_AUDIO":
                target_msg = msg
                msg_index = i
                break  # 找到第一条未处理的

        force_no_audio = (target_msg is not None and target_msg.audio_path == "NO_AUDIO")

        if self.dp_chat.if_generate_audio and response_text!='（再见）' and not force_no_audio:
            emotion = 'LABEL_0'
            if target_msg is not None:
                emotion = target_msg.emotion.as_label()

            abs_path = os.path.abspath(self.audio_gen.audio_file_path).replace('\\', '/')
            self.live2d_text_queue.put(
                self._format_live2d_display_text(display_text, translation)
            )    #更新live2d文本框的内容显示

            # 更新该条 AI 消息的 audio_path 和 translation
            if target_msg is not None:
                target_msg.audio_path = abs_path
                target_msg.translation = translation
        else:
            abs_path = "NO_AUDIO"
            emotion = target_msg.emotion.as_label() if target_msg is not None else "LABEL_0"
            if target_msg is not None:
                target_msg.audio_path = "NO_AUDIO"
                target_msg.translation = translation

        display_message = target_msg or Message(
            character_name=character_name,
            text=display_text,
            translation=translation,
            emotion=EmotionEnum.from_string(emotion),
            audio_path=abs_path,
        )
        display_msg_index = msg_index if msg_index is not None else len(self.current_chat.message_list)
        self.chat_display.append_message(display_message, display_msg_index, stream=True, interval_ms=30)
        self.schedule_context_usage_refresh()

    def _handle_structured_response(self, payload: dict[str, object]) -> None:
        """
        处理来自 main2/dp_local2 的结构化 UI 事件。
        """
        event_type = str(payload.get("type") or "")
        if self._is_cancelled_turn_payload(payload):
            if event_type == "assistant_turn_complete":
                self.cancelled_turn_ids.discard(str(payload.get("turn_id") or ""))
            return
        if event_type == "context_compaction_started":
            chat_id = str(payload.get("chat_id") or "")
            if self._is_active_turn_payload(payload) and chat_id == self.current_chat_id:
                self.chat_display.append_context_compaction_notice(
                    str(payload.get("message") or "正在整理过往思绪...")
                )
            return
        if event_type in {"tool_call_started", "tool_call_updated"}:
            chat_id = str(payload.get("chat_id") or self.current_chat_id)
            if chat_id != self.current_chat_id:
                return
            if event_type == "tool_call_started":
                self._handle_tool_call_start_event(payload)
            else:
                self._handle_tool_call_update_event(payload)
            return
        # 事件：切换对话
        if event_type == "chat_switched":
            return
        # 事件：设置当前的处理阶段
        if event_type == "assistant_turn_phase":
            if self._is_active_turn_payload(payload):
                self.active_turn_phase = str(payload.get("phase") or self.active_turn_phase or "")
                raw_message_indices = payload.get("message_indices")
                if isinstance(raw_message_indices, list):
                    self.active_turn_message_indices = {
                        one for one in raw_message_indices if isinstance(one, int)
                    }
                status_message = str(payload.get("message") or "")
                if status_message:
                    self._set_message_box_text(status_message)
                self._refresh_send_button_state()
            return
        # 事件：当前对话轮次发生用户可见错误
        if event_type == "assistant_turn_error":
            if self._is_active_turn_payload(payload):
                error_message = str(payload.get("message") or "出现了未知错误。")
                self._set_message_box_text(error_message)
            return
        # 事件：完成一轮对话的生成
        if event_type == "assistant_turn_complete":
            if self._is_active_turn_payload(payload):
                status = str(payload.get("status") or "ok")
                self._clear_active_turn()
                if status == "ok":
                    self._set_message_box_idle()
                else:
                    self.refresh_current_chat_display()
                self.refresh_chat_list()
                self.schedule_context_usage_refresh()
                self.chat_manager.save()
            return
        if event_type != "assistant_segment_ready":
            return

        chat_id = str(payload.get("chat_id") or "")
        # 仅处理当前正在进行对话的事件，且必须是当前界面的对话
        if self.active_turn_id is not None and not self._is_active_turn_payload(payload):
            return
        chat = self.chat_manager.get_chat_by_id(chat_id)
        if chat is None:
            return
        raw_message_index = payload.get("message_index")
        message_index = raw_message_index if isinstance(raw_message_index, int) else -1
        if 0 <= message_index < len(chat.message_list):
            msg = chat.message_list[message_index]
            msg.audio_path = str(payload.get("audio_path") or "NO_AUDIO")
            msg.translation = str(payload.get("translation") or msg.translation)
        if chat_id != self.current_chat_id:
            self.refresh_chat_list()
            return

        self.active_turn_phase = "rendering"
        display_text = str(payload.get("text") or "")
        translation = str(payload.get("translation") or "")
        character_name = str(payload.get("character_name") or self.current_character.character_name)
        emotion = str(payload.get("emotion") or "LABEL_0")
        audio_path = str(payload.get("audio_path") or "NO_AUDIO")

        if audio_path != "NO_AUDIO":
            abs_path = os.path.abspath(audio_path).replace('\\', '/')
            self.live2d_text_queue.put(
                self._format_live2d_display_text(display_text, translation)
            )
        else:
            abs_path = "NO_AUDIO"
        display_message = msg if 0 <= message_index < len(chat.message_list) else Message(
            character_name=character_name,
            text=display_text,
            translation=translation,
            emotion=EmotionEnum.from_string(emotion),
            audio_path=abs_path,
        )
        display_msg_index = message_index if message_index >= 0 else len(chat.message_list)
        self.chat_display.append_message(display_message, display_msg_index, stream=True, interval_ms=30)
        self.refresh_chat_list()
        self.schedule_context_usage_refresh()

    def _start_active_turn(self, chat_id: str, turn_id: str, phase: str = "llm") -> None:
        """
        记录当前正在处理的一轮普通对话。
        """
        self.active_chat_id = chat_id
        self.active_turn_id = turn_id
        self.active_turn_phase = phase
        # 一轮对话中可能涉及多条消息，active_turn_message_indices 用于记录这些消息的索引，以便在需要时进行统一处理（如标记无语音等）
        self.active_turn_message_indices = set()
        self._refresh_send_button_state()

    def _clear_active_turn(self) -> None:
        """
        清除当前正在处理的对话轮次。
        """
        self.active_chat_id = None
        self.active_turn_id = None
        self.active_turn_phase = None
        self.active_turn_message_indices = set()
        self._refresh_send_button_state()

    def _is_active_turn_payload(self, payload: dict[str, object]) -> bool:
        """
        判断结构化事件是否属于当前正在处理的一轮对话。
        判断标准为：当前存在正在进行的对话和轮次，且事件的对话 id、轮次 id 和当前均相同。
        """
        return (
            self.active_chat_id is not None
            and self.active_turn_id is not None
            and str(payload.get("chat_id") or "") == self.active_chat_id
            and str(payload.get("turn_id") or "") == self.active_turn_id
        )

    def _is_cancelled_turn_payload(self, payload: dict[str, object]) -> bool:
        """
        判断结构化事件是否属于已经被前端取消的一轮对话。
        """
        turn_id = str(payload.get("turn_id") or "")
        return bool(turn_id and turn_id in self.cancelled_turn_ids)

    def _create_stop_button_icon(self) -> QIcon:
        """
        生成停止按钮图标，避免使用文本方块时受字体基线影响而视觉偏低。
        """
        icon_size = int(self.input_tool_button_height * 0.42)
        pixmap = QPixmap(icon_size, icon_size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        square_size = int(icon_size * 0.78)
        offset = int((icon_size - square_size) * 0.5)
        painter.drawRoundedRect(offset, offset, square_size, square_size, 1.5, 1.5)
        painter.end()
        return QIcon(pixmap)

    def _refresh_send_button_state(self) -> None:
        """
        根据当前是否有 active turn 切换发送/终止按钮状态。
        """
        if not hasattr(self, "send_button"):
            return
        if self.is_response_active():
            self.send_button.setText("")
            self.send_button.setIcon(self._send_stop_icon)
            self.send_button.setToolTip("终止对话")
        else:
            self.send_button.setIcon(QIcon())
            self.send_button.setText("↑")
            self.send_button.setToolTip("发送")

    def _finish_current_stream_immediately(self) -> None:
        """
        停止逐字渲染，并把当前缓冲区中尚未显示的文字立即补到聊天框。
        """
        self.chat_display.finish_stream_now()

    def _mark_active_turn_pending_messages_no_audio(self) -> None:
        """
        将当前轮次中尚未完成语音回填的 assistant 消息标记为无语音。
        """
        if not self.active_turn_message_indices:
            return
        chat = self.chat_manager.get_chat_by_id(self.active_chat_id or "")
        if chat is None:
            return
        for message_index in self.active_turn_message_indices:
            if 0 <= message_index < len(chat.message_list):
                msg = chat.message_list[message_index]
                if msg.character_name != "User" and not msg.audio_path:
                    msg.audio_path = "NO_AUDIO"

    def handle_send_button_clicked(self) -> None:
        """
        发送按钮的统一入口：空闲时发送，回复中时终止。
        """
        if self.is_response_active():
            self.cancel_active_turn()
        else:
            self.handle_user_input()

    def cancel_active_turn(self) -> None:
        """
        终止当前正在进行的一轮回复。后端可能无法立即停止阻塞调用，但后续事件会被 turn_id 丢弃。
        """
        if self.active_chat_id is None or self.active_turn_id is None:
            return
        chat_id = self.active_chat_id
        turn_id = self.active_turn_id
        phase = self.active_turn_phase or ""
        self.cancelled_turn_ids.add(turn_id)

        if hasattr(self.dp_chat, "request_cancel_turn"):
            # 要求 dp_local2 终止对话生成
            try:
                self.dp_chat.request_cancel_turn(chat_id, turn_id)
            except Exception:
                logger.exception("登记对话终止请求失败。")

        if self.change_char_queue is not None:
            # 要求 live2d 终止对话播放
            self.change_char_queue.put({
                "type": "cancel_turn",
                "chat_id": chat_id,
                "turn_id": turn_id,
            })

        if phase == "tts":
            # 设置尚未生成的语音为无语音
            self._mark_active_turn_pending_messages_no_audio()
            if chat_id == self.current_chat_id:
                self.refresh_current_chat_display()
        elif phase == "rendering":
            self.chat_display.finish_stream_now()
            if chat_id == self.current_chat_id:
                self.refresh_current_chat_display()
        else:
            self._finish_current_stream_immediately()

        self._clear_active_turn()
        self.messages_box.clear()
        self.messages_box.append("已终止当前回复")
        self.refresh_chat_list()
        self.schedule_context_usage_refresh()
        self.chat_manager.save()

    @staticmethod
    def _format_live2d_display_text(text: str, translation: str = "") -> str:
        text_content = re.sub(r"（.*?）", '', text or '').strip()
        translation_content = (translation or '').strip()
        if translation_content:
            return f"{text_content}\n{translation_content}" if text_content else translation_content
        return text_content

    def is_response_active(self) -> bool:
        """
        是否存在一轮仍在 LLM、语音合成或 UI 渲染阶段的对话。
        """
        return self.active_turn_id is not None

    def is_chat_busy(self) -> bool:
        """
        当前界面是否仍有对话轮次或流式渲染未结束。
        """
        return self.is_response_active() or self.chat_display.is_streaming()

    def is_chat_idle(self) -> bool:
        """
        当前界面是否允许修改当前对话对象。
        """
        return not self.is_chat_busy()

    def _on_input_command_selected(self, spec: CommandSpec) -> None:
        """处理命令栏中被用户选中的命令。"""
        self._execute_input_command(spec, "slash_menu")

    def _handle_command_before_send(self, text: str) -> bool:
        """在普通发送流程前识别并处理 slash 命令或裸命令。"""
        if "\n" in text:
            return False
        if not self.run_input_command_text(text, "send_button"):
            if text.strip().startswith("/"):
                self.messages_box.clear()
                self.messages_box.append(f"未知命令：{text.strip()}")
                return True
            return False
        return True

    def run_input_command_text(self, text: str, source: str) -> bool:
        """按文本查找并执行输入框命令，命中时返回 True。"""
        command_match = self.input_command_matcher.find_by_text(text)
        if command_match is None:
            return False
        self._execute_input_command(command_match.spec, source, command_match.payload)
        return True

    def _execute_input_command(self, spec: CommandSpec, source: str, payload: Optional[str] = None) -> None:
        """统一执行输入框命令。"""
        command_payload = payload if payload is not None else spec.command

        if spec.execution_policy == "confirm" and not self._should_skip_command_confirm(spec, source):
            self._confirm_dangerous_command(
                spec,
                lambda: self._execute_confirmed_input_command(spec, source, command_payload),
            )
            return

        self._execute_confirmed_input_command(spec, source, command_payload)

    def _should_skip_command_confirm(self, spec: CommandSpec, source: str) -> bool:
        """判断某个来源触发的命令是否可以跳过二次确认。"""
        return spec.command == "bye" and source == "more_function_button"

    def _execute_confirmed_input_command(self, spec: CommandSpec, source: str, payload: str) -> None:
        """执行已经通过确认的输入框命令。"""
        self.setWindowTitle("数字小祥")

        if spec.command == "save":
            self.save_data()
            self.user_input.clear_after_send()
            return

        if spec.command == "clr":
            if not self.is_chat_idle():
                QMessageBox.information(self, "请稍等", "请等待当前回复完成后再清空对话。")
                return
            self.current_chat.clear_message_list()
            self.tool_call_records_cache.clear()
            self.chat_display.clear_chat()
            self._send_internal_command_payload({"type": "clear_chat", "chat_id": self.current_chat_id}, force=True)
            self.schedule_context_usage_refresh()
            self.user_input.clear_after_send()
            return

        if spec.command == "s":
            self.switch_to_next_chat()
            self.user_input.clear_after_send()
            return

        if spec.command == "change_l2d_model":
            self._open_l2d_model_command_window()
            self.user_input.clear_after_send()
            return

        if spec.command == "switch_l2d_fps":
            self.switch_l2d_fps()
            self.user_input.clear_after_send()
            return

        self._send_internal_command_payload(payload, force=spec.visibility == "hidden")
        self.user_input.clear_after_send()

        if spec.command == "l":
            self.is_ch = not self.is_ch
            self.talk_speed_reset()
            self.pause_second_reset()

    def _send_internal_command_payload(self, payload: str | dict[str, object], force: bool = False) -> None:
        """向后端队列发送内部命令 payload。"""
        if force or (not self.is_chat_busy() and self.qt2dp_queue.empty()):
            if isinstance(payload, str):
                command_map = {
                    "l": {"type": "toggle_language", "chat_id": self.current_chat_id},
                    "v": {"type": "toggle_voice", "chat_id": self.current_chat_id},
                    "conv": {"type": "legacy_command", "command": "conv", "chat_id": self.current_chat_id},
                    "mask": {"type": "legacy_command", "command": "mask", "chat_id": self.current_chat_id},
                    "start_talking": {"type": "legacy_command", "command": "start_talking", "chat_id": self.current_chat_id},
                    "stop_talking": {"type": "legacy_command", "command": "stop_talking", "chat_id": self.current_chat_id},
                    "change_l2d_background": {"type": "legacy_command", "command": "change_l2d_background", "chat_id": self.current_chat_id},
                    "bye": {"type": "exit"},
                }
                payload = command_map.get(payload, {"type": "legacy_command", "command": payload, "chat_id": self.current_chat_id})
            self.qt2dp_queue.put(payload)

    def _confirm_dangerous_command(self, spec: CommandSpec, callback: Callable[[], None]) -> None:
        """对危险命令弹出二次确认窗口。"""
        css = ThemeManager.generate_stylesheet(
            ThemeManager.get_QT_style_theme_color(self.current_character.qt_css)
        ) if self.current_character.qt_css is not None else ThemeManager.generate_stylesheet(
            '#7799CC')
        danger_text = spec.danger_text or f"确定要执行 {spec.display_command} 吗？"
        WarningWindow(danger_text, css, callback).exec_()

    def _open_l2d_model_command_window(self) -> None:
        """打开 Live2D 模型选择窗口。"""
        current_char_folder_name = self.current_character.character_folder_name
        change_l2d_model_window = ChangeL2DModelWindow(current_char_folder_name, self._send_l2d_model_payload)
        change_l2d_model_window.exec_()
    
    def switch_l2d_fps(self):
        if not hasattr(self, 'l2d_fps_dict'):
            self.l2d_fps_dict = {"current_fps":1,
                                 "all_fps":[30, 60, 120]}
        self.l2d_fps_dict["current_fps"]=(self.l2d_fps_dict["current_fps"]+1) % len(self.l2d_fps_dict["all_fps"])
        current_fps = self.l2d_fps_dict["all_fps"][self.l2d_fps_dict["current_fps"]]
        self.change_char_queue.put({
            "type": "switch_l2d_fps",
            "fps": current_fps
        })
        self.QT_message_queue.put(f"已切换 Live2D 渲染帧率为 {current_fps} fps")

    def toggle_live2d_layout_edit(self) -> None:
        """通知 Live2D 窗口切换模型布局编辑模式。"""
        if self.change_char_queue is None:
            return
        self.change_char_queue.put({
            "type": "toggle_l2d_layout_edit",
        })
        self.QT_message_queue.put("已切换 Live2D 布局编辑模式")
    
    def get_current_l2d_fps(self):
        if hasattr(self, 'l2d_fps_dict'):
            return self.l2d_fps_dict["all_fps"][self.l2d_fps_dict["current_fps"]]
        return 60

    def _send_l2d_model_payload(self, new_model_json: str) -> None:
        """校验并发送 Live2D 模型切换 payload。"""
        if not self._prepare_live2d_model_for_switch(new_model_json, "切换模型"):
            return
        character_name = self.current_character.character_name
        try:
            self.current_chat.update_custom_live2d_model_meta(character_name, new_model_json)
            self.chat_manager.save()
        except Exception:
            self.QT_message_queue.put("切换模型失败，保存对话模型配置时出错。")
            logger.exception("保存对话级 Live2D 模型配置失败。")
            return
        self._send_live2d_switch(character_name, new_model_json)

    def _prepare_live2d_model_for_switch(self, model_json: Optional[str], action_label: str) -> bool:
        """在发送 Live2D 切换命令前转换旧模型并规范化 V3 模型。"""
        result = normalize_live2d_model_for_project(model_json)
        if not result.ok:
            self.QT_message_queue.put(f"{action_label}失败，检查 Live2D 配置文件时出错。")
            logger.error(
                "%s失败，检查 Live2D 配置文件时出错：%s；%s",
                action_label,
                model_json,
                result.error_message,
            )
            return False
        if result.converted_old_model:
            logger.info("成功转换旧版 Live2D 配置文件：%s", model_json)
        if result.normalized_model3:
            self.QT_message_queue.put("已自动修复 Live2D V3 模型动作组配置")
            logger.info("已自动修复 Live2D V3 模型动作组配置：%s", model_json)
        return True
    
    def _send_live2d_switch(self, character_name: str, model_json: Optional[str]) -> None:
        """
        发送结构化 Live2D 切换命令。
        """
        if self.change_char_queue is None:
            return
        self.change_char_queue.put({
            "type": "switch_live2d",
            "character_name": character_name,
            "model_json": model_json or "",
        })

    def handle_user_input(self) -> None:
        """校验并发送输入框中的消息，同时保留失败发送的草稿。"""
        self.setWindowTitle("数字小祥")
        raw_user_input = self.user_input.toPlainText()
        user_this_turn_input = self.user_input.prepared_message()
        image_source_paths = self.user_input.pending_image_source_paths()
        if not user_this_turn_input.strip() and not image_source_paths:
            return
        # 不可以在 AI 回复时补充内容
        if self.is_chat_busy():
            QMessageBox.information(self, "请稍等", "请等待当前回复完成后再发送消息。")
            return
        if image_source_paths and not self._current_model_supports_vision():
            self.user_input.show_error("当前模型不支持图片输入，请切换支持视觉的模型或删除图片附件。")
            return
        failed_image_paths = self.user_input.validate_pending_images()
        if failed_image_paths:
            self.user_input.show_error(f"图片不存在或无法读取：{os.path.basename(failed_image_paths[0])}")
            return
        # 存在换行符时，不处理输入框中的命令
        if "\n" not in raw_user_input and self._handle_command_before_send(user_this_turn_input):
            return
        optimistic_attachments = self.user_input.optimistic_image_attachments()
        self._send_user_message_payload(user_this_turn_input, image_source_paths=image_source_paths)
        self.user_input.clear_after_send()

        # 简单预测用户的 msg_index，使其能支持右键删除功能
        if self.current_chat.message_list and self.current_chat.message_list[-1].text == user_this_turn_input:
            predicted_msg_index = len(self.current_chat.message_list)-1
        else:
            predicted_msg_index = len(self.current_chat.message_list)
        user_message = Message(
            character_name="User",
            text=user_this_turn_input,
            translation="",
            emotion=EmotionEnum.HAPPINESS,
            audio_path="",
            attachments=optimistic_attachments,
        )
        self.chat_display.append_message(user_message, predicted_msg_index, stream=True, interval_ms=8)

    def _send_user_message_payload(
        self,
        text: str,
        *,
        append_user_message: bool = True,
        context_usage_delay_ms: int = 1300,
        image_source_paths: Sequence[str] = (),
    ) -> None:
        """向后端发送用户消息请求，并启动当前 active turn。"""
        turn_id = uuid.uuid4().hex
        self._start_active_turn(self.current_chat_id, turn_id, "llm")
        self.qt2dp_queue.put({
            "type": "send_message",
            "chat_id": self.current_chat_id,
            "turn_id": turn_id,
            "text": text,
            "append_user_message": append_user_message,
            "image_source_paths": list(image_source_paths),
        })
        self.schedule_context_usage_refresh(delay_ms=context_usage_delay_ms)

    def save_data(self):
        # 使用 ChatManager 统一保存到 all_conversation.json
        try:
            self.chat_manager.save()
            self.setWindowTitle("已保存最新的聊天记录！")
        except Exception:
            logger.exception("保存聊天记录失败")
            self.setWindowTitle("保存聊天记录失败！")

    def _register_message_command_handler(self, command_prefix: str, handler):
        """注册来自消息队列的特殊命令处理器。"""
        if command_prefix and callable(handler):
            self._message_command_handlers[command_prefix] = handler

    def _dispatch_message_command(self, message: str) -> bool:
        """尝试把消息作为特殊命令分发处理；命中则返回 True。"""
        for prefix, handler in self._message_command_handlers.items():
            if message.startswith(prefix):
                payload = message[len(prefix):]
                handler(payload)
                return True
        return False

    def _handle_lottery_ui_command(self, payload_text: str):
        """处理抽签窗口打开命令，命令负载为 JSON。"""
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            self.messages_box.clear()
            self.messages_box.append("抽签命令解析失败：JSON 格式错误")
            return

        title = str(payload.get("title") or "随机抽签")
        options_raw = payload.get("options") or []
        if not isinstance(options_raw, list):
            self.messages_box.clear()
            self.messages_box.append("抽签命令解析失败：options 不是数组")
            return

        options = [str(one).strip() for one in options_raw if str(one).strip()]
        if len(options) < 2:
            self.messages_box.clear()
            self.messages_box.append("抽签命令解析失败：候选项少于 2 个")
            return

        def _on_lottery_result(winner: str):
            internal_event = (
                f"【系统内部事件触发：抽签结束】抽签主题：{title}。"
                f"用户已点击抽签，结果是：{winner}。"
                "请你自然地继续对话并结合这个结果给出建议。"
            )
            self.qt2dp_queue.put(internal_event)
            self.messages_box.clear()
            self.messages_box.append(f"抽签结果：{winner}")

        dialog = LotteryDialog(title=title, options=options, on_result=_on_lottery_result, parent=self)
        self._lottery_dialog_ref = dialog
        dialog.show()

    def handle_messages(self,message):
        if message=='bye':
            self.save_data()
            self.close()
            return

        if self._dispatch_message_command(message):
            return

        self.messages_box.clear()
        self.messages_box.append(message)

if __name__=='__main__':
    from PyQt5.QtWidgets import QApplication
    import sys,threading
    from queue import Queue
    dp2qt_queue = Queue()
    qt2dp_queue = Queue()
    QT_message_queue = Queue()


    def dp_thread(dp2qt_queue, qt2dp_queue, QT_message_queue):      #测试用
        while True:
            time.sleep(0.5)
            if not qt2dp_queue.empty():
                user_input = qt2dp_queue.get()
                if user_input == 'bye':
                    break
                if user_input == '思考中' or user_input == '有错误发生':
                    QT_message_queue.put(user_input)

                response = user_input + "祥子选择入学有特待生制度的羽丘女子学园，在校期间几乎不和任何人交流，\n期间她多次和睦在羽泽咖啡店会面，要求她不要向前队友透露自己的动向，但素世还是从爱音口中得知了祥子就在羽丘的消息，多次堵校门，这让祥子很厌烦。于是祥子决定与长崎爽世正式谈话，并顺便观看其所在乐队的演出。"
                time.sleep(2)
                dp2qt_queue.put(response)
        dp2qt_queue.put("结束了")


    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)  # noqa
    import character
    get_all = character.GetCharacterAttributes()
    characters = get_all.character_class_list

    app = QApplication(sys.argv)
    class AudioGenMock:
        def __init__(self):
            self.speed=1.0
            self.pause_second=0.3
            self.audio_file_path="../reference_audio/audio_cache/temp_output.wav"
    audio_gen_mock = AudioGenMock()

    # 创建 dp_chat mock，包含 ChatManager 以满足 ChatGUI 初始化需求
    chat_mgr = get_chat_manager()
    class DpChatMock:
        def __init__(self):
            self.chat_manager = chat_mgr
            self.current_chat_id = chat_mgr.ensure_default_single_character_chat(characters).chat_id
            self.if_generate_audio = False
            self.audio_language_choice = "日英混合"
            self.sakiko_state = True
    dp_chat_mock = DpChatMock()

    class _SharedBool:
        def __init__(self, value: bool):
            self.value = value

    live2d_text_queue = Queue()
    is_display_text_value = _SharedBool(True)
    motion_complete_value = _SharedBool(True)
    win = ChatGUI(
        dp2qt_queue,
        qt2dp_queue,
        QT_message_queue,
        characters,
        dp_chat_mock,
        audio_gen_mock,
        live2d_text_queue,
        is_display_text_value,
        motion_complete_value,
        None,
        None,
        None,
    )

    # 如果出现字体加载问题，则暂时不加载字体
    font_path = os.path.join(project_root, "font", "ft.ttf")
    font_id = QFontDatabase.addApplicationFont(os.path.abspath(font_path))
    if font_id != -1:
        font_family = QFontDatabase.applicationFontFamilies(font_id)
        font = QFont(font_family[0], 12)
        app.setFont(font)

    t1 = threading.Thread(target=dp_thread, args=(dp2qt_queue, qt2dp_queue, QT_message_queue))
    t1.start()

    win.show()
    sys.exit(app.exec_())
