import os.path
import re
import time
import json

from PyQt5.QtMultimedia import QMediaPlayer, QMediaPlaylist, QMediaContent

import numpy as np
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QTextBrowser, QPushButton, QDesktopWidget, QHBoxLayout, \
    QSlider, QLabel, QToolButton, QDialog, QGroupBox, QGridLayout, QColorDialog, QMessageBox, QScrollArea, QFrame
from PyQt5.QtCore import QTimer, QThread, pyqtSignal, QObject, Qt, QSize, QUrl
from PyQt5.QtGui import QFontDatabase, QFont, QIcon, QTextCursor, QPalette, QColor, QImage, QPixmap, QCursor

import sounddevice as sd
from opencc import OpenCC
import os,sys
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)
from ui_constants import dialogWindowDefaultCss,char_info_json
from character import PrintInfo

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
                    PrintInfo.print_info(f"颜色值有误，使用祥子配色")
                    return '#7799CC'
                return color_value
            else:
                PrintInfo.print_info("使用默认祥子配色")
                return '#7799CC'

        except Exception as e:
            PrintInfo.print_info('QT_style.json文件格式有误，使用默认祥子配色：',e)
            return '#7799CC'

class CommunicateThreadDP2QT(QThread):
    response_signal=pyqtSignal(str)
    def __init__(self,dp2qt_queue,main_timer):
        super().__init__()
        self.this_turn_response=''
        self.dp2qt_queue=dp2qt_queue
        self.main_timer=main_timer

    def run(self):
        while True:
            if not self.dp2qt_queue.empty() and not self.main_timer.isActive():     #解决了特定情况下显示不全回答的bug
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
    def __init__(self,parent_window_close_fun):
        super().__init__()
        self.setWindowTitle("更多功能...")
        self.screen = QDesktopWidget().screenGeometry()
        self.resize(int(0.2 * self.screen.width()), int(0.2 * self.screen.height()))
        layout = QVBoxLayout()
        self.open_motion_editor_button =QPushButton("运行动作组编辑程序")
        self.open_motion_editor_button.clicked.connect(self.on_click_open_motion_editor_button)  # noqa
        layout.addWidget(self.open_motion_editor_button)

        self.open_start_config_button=QPushButton("启动参数配置")
        self.open_start_config_button.clicked.connect(self.on_click_open_start_config_button)  # noqa
        layout.addWidget(self.open_start_config_button)

        self.open_small_theater_btn=QPushButton("小剧场模式（Beta）")
        self.open_small_theater_btn.clicked.connect(self.on_click_open_small_theater)  # noqa
        layout.addWidget(self.open_small_theater_btn)

        open_live2d_downloader_btn=QPushButton("Live2D模型下载器")
        open_live2d_downloader_btn.clicked.connect(self.on_click_open_live2d_downloader)  # noqa
        layout.addWidget(open_live2d_downloader_btn)

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
            PrintInfo.print_warning(f"[Warning].bat 文件未找到:\n{bat_path}")
            return
        try:
            # 使用 subprocess 模块启动 .bat 文件
            import subprocess
            # 使用 shell=True 让系统直接执行批处理文件
            # Windows 会使用 cmd.exe 来执行 .bat 文件
            subprocess.Popen([bat_path], shell=True)
        except Exception as e:
            PrintInfo.print_error("[Error]启动失败", f"启动程序时发生错误:\n{e}")

    def on_click_open_motion_editor_button(self):
        self.exec_bat("运行动作组编辑程序.bat")
        self.close()
    def on_click_open_start_config_button(self):
        self.exec_bat("启动参数配置.bat")
        self.close()
    def on_click_open_small_theater(self):
        self.exec_bat("运行小剧场模式.bat")
        self.close()
    def on_click_open_live2d_downloader(self):
        self.exec_bat("运行Live2D模型下载器.bat")
        self.close()

class WarningWindow(QDialog):
    def __init__(self,warning_text,css,parent_window_fun):
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
        self.confirm_btn.clicked.connect(parent_window_fun)  # noqa
        self.confirm_btn.clicked.connect(self.close)  # noqa
        self.cancel_btn.clicked.connect(self.close)  # noqa


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
        self.change_reference_audio_btn=QPushButton("更改当前角色参考音频")
        self.change_reference_audio_btn.clicked.connect(self.open_change_ref_audio_window)
        current_chara:character.CharacterAttributes=self.parent_window.character_list[self.parent_window.current_char_index]
        if current_chara.GPT_model_path is None or current_chara.sovits_model_path is None or current_chara.gptsovits_ref_audio is None:
            self.change_reference_audio_btn.setEnabled(False)
        self.switch_live2d_text_btn=QPushButton("开启/关闭Live2D界面文本显示")
        self.switch_live2d_text_btn.clicked.connect(self.parent_window.toggle_live2d_text_display)
        self.change_l2d_background_btn=QPushButton("切换Live2D背景")
        self.change_l2d_background_btn.clicked.connect(self.change_l2d_background)
        self.change_l2d_model_btn=QPushButton("更改当前角色Live2D模型")
        self.change_l2d_model_btn.clicked.connect(self.change_live2d_model_1)
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
        setting_group=QGroupBox("常用设置")
        setting_group.setLayout(setting_layout)
        setting_layout_2=QGridLayout()
        setting_layout_2.addWidget(self.change_theme_color_btn,0,0,1,2)
        setting_layout_2.addWidget(self.change_reference_audio_btn,1,0,1,2)
        setting_layout_2.addWidget(self.change_l2d_model_btn,2,0,1,2)
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
        self.parent_window.user_input.setText('l')
        self.parent_window.user_input.returnPressed.emit()  # noqa
        self.parent_window.user_input.clear()
    def clear_history_msg(self):
        self.parent_window.user_input.setText('clr')
        self.parent_window.user_input.returnPressed.emit()  # noqa
        self.parent_window.user_input.clear()
    def convert_sakiko_state(self):
        self.parent_window.user_input.setText('conv')
        self.parent_window.user_input.returnPressed.emit()  # noqa
        self.parent_window.user_input.clear()
    def sakiko_mask(self):
        self.parent_window.user_input.setText('mask')
        self.parent_window.user_input.returnPressed.emit()  # noqa
        self.parent_window.user_input.clear()
    def switch_voice(self):
        self.parent_window.user_input.setText('v')
        self.parent_window.user_input.returnPressed.emit()  # noqa
        self.parent_window.user_input.clear()
    def open_color_picker(self):
        color_picker = ColorPicker(self.parent_window_set_theme_color,self.current_color)
        color_picker.exec_()
    def parent_window_set_theme_color(self,color):
        self.parent_window.setStyleSheet(ThemeManager.generate_stylesheet(color))
        self.parent_window.set_btn_color(color)
    def open_change_ref_audio_window(self):
        change_ref_audio_window=ChangeReferenceAudioWindow(self.audio_gen_module,QDesktopWidget().screenGeometry())
        change_ref_audio_window.exec_()
    def change_l2d_background(self):
        self.parent_window.user_input.setText('change_l2d_background')
        self.parent_window.user_input.returnPressed.emit()  # noqa
        self.parent_window.user_input.clear()

    def change_live2d_model_1(self):
        current_char_folder_name=self.parent_window.character_list[self.audio_gen_module.current_character_index].character_folder_name
        change_l2d_model_window=ChangeL2DModelWindow(current_char_folder_name,self.change_live2d_model_2)
        change_l2d_model_window.exec_()
    def change_live2d_model_2(self,new_model_json):
        from character import is_old_l2d_json,convert_old_l2d_json
        if is_old_l2d_json(new_model_json):
            try:
                convert_old_l2d_json(new_model_json)
            except Exception as e:
                self.parent_window.QT_message_queue(f"切换模型失败，转换旧版Live2D配置文件时出错。")
                PrintInfo.print_error("[Error]切换模型失败，转换旧版Live2D配置文件时出错。\n",e)
                return
            PrintInfo.print_info("成功转换旧版Live2D配置文件。\n")
        self.parent_window.user_input.setText(f'change_l2d_model#{new_model_json}')
        self.parent_window.user_input.returnPressed.emit()  # noqa
        self.parent_window.user_input.clear()



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

        import glob
        default_live2d_json = glob.glob(
            os.path.join(f'../live2d_related/{self.current_char_folder_name}', 'live2D_model', f"*.model.json"))
        default_live2d_json = default_live2d_json[0] if default_live2d_json else None
        self.current_char_l2d_models = [{"model_name": "默认", "model_json_path": default_live2d_json}]
        self.find_extra_models(self.current_char_folder_name)
        layout = QVBoxLayout()
        title_layout=QHBoxLayout()
        title_label = QLabel("选择一个Live2D模型：")
        refresh_btn=QToolButton()
        refresh_btn.setIcon(QIcon("./icons/refresh.svg"))
        refresh_btn.clicked.connect(self.refresh_ui)  # noqa
        title_layout.addWidget(title_label)
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
                PrintInfo.print_error(f"[Error]删除Live2D模型文件夹失败\n", e)
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
        open_downloader_btn.clicked.connect(lambda _:MoreFunctionWindow.exec_bat("运行Live2D模型下载器.bat"))  # noqa
        layout.addWidget(open_downloader_btn)
        self.setLayout(layout)

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
            #在这个具体的模型文件夹里，递归查找 .model.json

            for file in os.listdir(model_dir_path):
                if file.endswith(".model.json"):
                    full_path = os.path.join(model_dir_path, file)
                    # 将路径标准化（把反斜杠\变成正斜杠/），避免Windows路径问题
                    full_path = full_path.replace("\\", "/")
                    self.current_char_l2d_models.append({"model_name":model_dir_name,"model_json_path":full_path})
                    break  # 找到一个就跳出当前文件夹的循环


class ChangeReferenceAudioWindow(QDialog):
    def __init__(self,audio_gen_module,desktop_size):
        super().__init__()
        self.audio_gen_module=audio_gen_module
        self.setWindowTitle('更改当前角色参考音频')
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(int(desktop_size.width()*0.3),int(desktop_size.height()*0.4))
        layout=QVBoxLayout()
        self.audio_player = QMediaPlayer()
        self.audio_playlist = QMediaPlaylist()

        current_ref_audio_layout=QHBoxLayout()
        current_ref_audio_name=self.audio_gen_module.ref_audio_file
        if self.audio_gen_module.if_sakiko:
            current_ref_audio_name=self.audio_gen_module.ref_audio_file_black_sakiko if self.audio_gen_module.sakiko_which_state else self.audio_gen_module.ref_audio_file_white_sakiko
        self.current_ref_audio_label=QLabel(f"当前参考音频:{os.path.basename(current_ref_audio_name)}")
        self.play_current_ref_audio_btn=QToolButton()
        self.play_current_ref_audio_btn.setIcon(QIcon("./icons/play.svg"))
        self.play_current_ref_audio_btn.clicked.connect(self.play_current_ref_audio)
        current_ref_audio_layout.addWidget(self.current_ref_audio_label)
        current_ref_audio_layout.addWidget(self.play_current_ref_audio_btn)
        layout.addLayout(current_ref_audio_layout)

        all_ref_audio_files=[]
        try:
            for file in os.listdir(f'../reference_audio/{self.audio_gen_module.character_list[self.audio_gen_module.current_character_index].character_folder_name}'):
                if file.endswith('.wav') or file.endswith('.mp3'):
                    all_ref_audio_files.append(file)
        except Exception as e:
            PrintInfo.print_error("[Error]参考音频文件夹读取错误:",e)
        select_new_ref_audio_group=QGroupBox("选择新的参考音频:")
        select_new_ref_audio_layout=QVBoxLayout()
        for ref_audio_file in all_ref_audio_files:
            single_ref_audio_layout=QHBoxLayout()
            name_label=QLabel(os.path.basename(ref_audio_file))
            play_btn=QToolButton()
            play_btn.setIcon(QIcon("./icons/play.svg"))
            play_btn.clicked.connect(lambda checked, path=os.path.join(f'../reference_audio/{self.audio_gen_module.character_list[self.audio_gen_module.current_character_index].character_folder_name}',ref_audio_file): self.play_ref_audio(path))
            select_btn=QToolButton()
            select_btn.setText("选择")
            select_btn.clicked.connect(lambda checked, path=os.path.join(f'../reference_audio/{self.audio_gen_module.character_list[self.audio_gen_module.current_character_index].character_folder_name}',ref_audio_file): self.replace_ref_audio(path))
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
        if self.audio_gen_module.if_sakiko:
            if self.audio_gen_module.sakiko_which_state:
                with open(self.audio_gen_module.ref_text_file_black_sakiko,'r',encoding='utf-8') as f:
                    current_ref_text=f.read().strip()
            else:
                with open(self.audio_gen_module.ref_text_file_white_sakiko,'r',encoding='utf-8') as f:
                    current_ref_text=f.read().strip()
        else:
            with open(self.audio_gen_module.ref_text_file,'r',encoding='utf-8') as f:
                current_ref_text=f.read().strip()
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
        current_ref_audio_path=self.audio_gen_module.ref_audio_file
        if self.audio_gen_module.if_sakiko:
            current_ref_audio_path=self.audio_gen_module.ref_audio_file_black_sakiko if self.audio_gen_module.sakiko_which_state else self.audio_gen_module.ref_audio_file_white_sakiko
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
            if self.audio_gen_module.if_sakiko:
                if self.audio_gen_module.sakiko_which_state:
                    self.audio_gen_module.ref_audio_file_black_sakiko=new_ref_audio_file
                    with open('../reference_audio/sakiko/default_ref_audio_black.txt','w',encoding='utf-8') as f:
                        f.write(new_ref_audio_file)
                else:
                    self.audio_gen_module.ref_audio_file_white_sakiko=new_ref_audio_file
                    with open('../reference_audio/sakiko/default_ref_audio_white.txt','w',encoding='utf-8') as f:
                        f.write(new_ref_audio_file)
            else:
                self.audio_gen_module.ref_audio_file=new_ref_audio_file
                self.audio_gen_module.character_list[self.audio_gen_module.current_character_index].gptsovits_ref_audio=new_ref_audio_file
                with open(f'../reference_audio/{self.audio_gen_module.character_list[self.audio_gen_module.current_character_index].character_folder_name}/default_ref_audio.txt','w',encoding='utf-8') as f:
                    f.write(new_ref_audio_file)
        except Exception as e:
            PrintInfo.print_error("[Error]更改参考音频出现错误，错误信息：",e)
        self.current_ref_audio_label.setText(f"当前参考音频:{os.path.basename(new_ref_audio_file)}")

    def change_ref_text(self):
        new_ref_text=self.new_ref_text_input.text().strip()
        if new_ref_text:
            if self.audio_gen_module.if_sakiko:
                if self.audio_gen_module.sakiko_which_state:
                    with open(self.audio_gen_module.ref_text_file_black_sakiko,'w',encoding='utf-8') as f:
                        f.write(new_ref_text)
                else:
                    with open(self.audio_gen_module.ref_text_file_white_sakiko,'w',encoding='utf-8') as f:
                        f.write(new_ref_text)
            else:
                with open(self.audio_gen_module.ref_text_file,'w',encoding='utf-8') as f:
                    f.write(new_ref_text)
                #无需更改内存中的参考音频文本，因为每次生成前都是从文件中读取
            self.change_ref_text_success_label.setText("修改成功!")
        else:
            self.change_ref_text_success_label.setText("文本不能为空!")



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

class ChatGUI(QWidget):
    def __init__(self,
                 dp2qt_queue,
                 qt2dp_queue,
                 QT_message_queue,
                 characters,
                 dp_chat,
                 audio_gen,live2d_mod,emotion_queue,audio_file_path_queue,emotion_model):
        super().__init__()
        self.audio_gen = audio_gen  # 为了获得音频文件路径，以及修改语速
        self.setWindowTitle("数字小祥")
        self.setWindowIcon(QIcon("../live2d_related/sakiko/sakiko_icon.png"))
        self.screen = QDesktopWidget().screenGeometry()
        self.resize(int(0.37 * self.screen.width()), int(0.7 * self.screen.height()))
        self.chat_display = QTextBrowser()
        self.chat_display.setPlaceholderText("这里显示聊天记录...")
        self.chat_display.setOpenExternalLinks(False)
        self.chat_display.anchorClicked.connect(self.play_history_audio)  # noqa
        self.chat_display.setOpenLinks(False)

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
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("在这里输入内容")

        self.voice_button = QPushButton()
        mic_icon=QIcon("./icons/microphone.png")
        self.voice_button.setIcon(mic_icon)
        self.voice_button.pressed.connect(self.voice_dectect)  # noqa
        self.voice_button.released.connect(self.voice_decect_end)  # noqa


        self.save_dialog_btn=QToolButton()
        self.save_dialog_btn.setIcon(QIcon("./icons/save.svg"))
        self.save_dialog_btn.setFixedSize(int(self.screen.height()*0.04),int(self.screen.height()*0.04))
        self.save_dialog_btn.setIconSize(QSize(int(self.screen.height()*0.04*0.6),int(self.screen.height()*0.04*0.6)))
        self.save_dialog_btn.setToolTip("保存聊天记录")

        layout = QVBoxLayout()

        input_layout=QHBoxLayout()
        input_layout.addWidget(self.user_input)
        input_layout.addWidget(self.voice_button)

        #处理流式输出
        self.full_response = ""
        self.current_index = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self.stream_print)   # noqa
        #处理模型的回答
        self.dp2qt_queue=dp2qt_queue
        self.get_response_thread=CommunicateThreadDP2QT(self.dp2qt_queue,self.timer)
        self.get_response_thread.response_signal.connect(self.handle_response)  # noqa
        self.get_response_thread.start()
        #处理用户输入
        self.qt2dp_queue=qt2dp_queue
        self.save_dialog_btn.clicked.connect(self.save_data)  # noqa

        self.user_input.returnPressed.connect(self.handle_user_input)  # noqa
        #处理各种消息
        self.QT_message_queue=QT_message_queue
        self.get_message_thread=CommunicateThreadMessages(self.QT_message_queue)
        self.get_message_thread.message_signal.connect(self.handle_messages)  # noqa
        self.get_message_thread.start()

        self.character_list:list = characters
        self.current_char_index = 0
        self.character_chat_history=[]
        for _ in self.character_list:
            self.character_chat_history.append('')
        if os.path.getsize('../reference_audio/history_messages_qt.json')!=0:
            with open('../reference_audio/history_messages_qt.json','r',encoding='utf-8') as f:
                json_data = json.load(f)
            for index,character in enumerate(self.character_list):
                for data in json_data:
                    if data['character']==character.character_name:
                        underline_removed=data['history'].replace("underline", "none")
                        self.character_chat_history[index]=underline_removed  # noqa

            self.chat_display.setHtml(self.character_chat_history[self.current_char_index])  # noqa

        if self.character_list[self.current_char_index].icon_path is not None:  # noqa
            self.setWindowIcon(QIcon(self.character_list[self.current_char_index].icon_path))  # noqa

        if self.character_list[self.current_char_index].qt_css is not None:  # noqa
            self.setStyleSheet(ThemeManager.generate_stylesheet(
                ThemeManager.get_QT_style_theme_color(self.character_list[self.current_char_index].qt_css)
                )
            )  # noqa
        else:
            self.setStyleSheet(ThemeManager.generate_stylesheet("#7799CC"))
        self.translation=''
        self.dp_chat=dp_chat    #仅为了保存聊天记录用
        if self.character_list[self.current_char_index].GPT_model_path is None or self.character_list[self.current_char_index].gptsovits_ref_audio is None or self.character_list[self.current_char_index].sovits_model_path is None:
            self.dp_chat.if_generate_audio=False


        self.is_ch = False
        self.saved_talk_speed_and_pause_second = [{'talk_speed': 0, 'pause_second': 0.5} for _ in self.character_list]
        for i, character in enumerate(self.character_list):
            if not self.is_ch:
                if self.character_list[self.current_char_index].character_name == '祥子':  # noqa
                    self.saved_talk_speed_and_pause_second[i]['talk_speed'] = 0.9  # noqa
                else:
                    self.saved_talk_speed_and_pause_second[i]['talk_speed'] = 0.88  # noqa
            else:
                if self.character_list[self.current_char_index].character_name == '祥子':  # noqa
                    self.saved_talk_speed_and_pause_second[i]['talk_speed'] = 0.83  # noqa
                else:
                    self.saved_talk_speed_and_pause_second[i]['talk_speed'] = 0.9  # noqa

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
        self.change_character_button.setIcon(QIcon('./icons/change_char.svg'))
        self.change_character_button.setFixedSize(int(self.screen.height()*0.04),int(self.screen.height()*0.04))
        self.change_character_button.setIconSize(QSize(int(self.screen.height()*0.04*0.6),int(self.screen.height()*0.04*0.6)))
        self.change_character_button.setToolTip("切换角色")
        self.change_character_button.clicked.connect(self.change_character_button_function)  # noqa

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
        layout.addWidget(self.chat_display)
        layout.addLayout(slider_layout)
        layout.addLayout(input_layout)
        if self.character_list[self.current_char_index].qt_css is not None:
            self.set_btn_color(ThemeManager.get_QT_style_theme_color(self.character_list[self.current_char_index].qt_css))
        else:
            self.set_btn_color('#7799CC')

        self.talk_speed_reset()

        self.setLayout(layout)  #因为需要character_list等参数，所以放在最后初始化

        self.live2d_mod=live2d_mod
        self.emotion_queue=emotion_queue
        self.emotion_model=emotion_model
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

    def toggle_live2d_text_display(self):
        self.live2d_mod.is_display_text=not self.live2d_mod.is_display_text

    def set_btn_color(self,color):
        #更改按钮图标颜色-----------------------
        icon_map = {
            self.setting_btn: './icons/setting.svg',
            self.change_character_button: './icons/change_char.svg',
            self.save_dialog_btn: './icons/save.svg',
            self.more_function_button: './icons/more.svg'
        }
        for btn,file in icon_map.items():
            try:
                with open(file,'r',encoding='utf-8') as f:
                    orig_data=f.read()
                pattern = r'fill="[^"]*"'
                replacement = f'fill="{color}"'
                new_data = re.sub(pattern, replacement, orig_data)
                svg_bytes = new_data.encode('utf-8')

                image = QImage.fromData(svg_bytes)
                pixmap = QPixmap.fromImage(image)
                btn.setIcon(QIcon(pixmap))
            except:
                pass
        #将文本框中的html文字的颜色全部换成新的主题色-----------------
        current_html=self.chat_display.toHtml()
        def update_html_colors(html_content, new_color, exclude_color="#b3d1f2"):
            """
            更新 HTML 中的颜色，但跳过指定的 exclude_color。
            """
            # 定义正则模式
            # color:     匹配字面量
            # \s* 匹配冒号后可能存在的空格
            # ([^;]+)    捕获组：匹配除了分号以外的任意字符（即颜色值）
            # ;          匹配结束的分号
            pattern = r"color:\s*([^;]+);"
            def replace_callback(match):
                full_match = match.group(0)  # 获取完整的 "color: #xxxxxx;"
                current_color = match.group(1).strip()  # 获取中间的颜色值 "#xxxxxx"

                # 判断：如果当前颜色等于排除色（忽略大小写），则不替换，原样返回
                if current_color.lower() == exclude_color.lower():
                    return full_match

                # 否则，替换成新颜色
                return f"color: {new_color};"

            # 执行替换
            new_html = re.sub(pattern, replace_callback, html_content)
            return new_html
        updated_html=update_html_colors(current_html,color)
        # 保留滚动条位置--------------------------------------------------------
        scroll_bar = self.chat_display.verticalScrollBar()
        saved_position = scroll_bar.value()
        # 获取滚动条最大值，判断用户是否本来就在最底下
        # 如果用户本来就在最底下，刷新后让他继续保持在最底下，体验更好
        was_at_bottom = (saved_position == scroll_bar.maximum())
        self.chat_display.setHtml(updated_html)
        if was_at_bottom:
            # 如果原来就在最底下，就让它保持在新的最底下（因为内容长度可能变了）
            scroll_bar.setValue(scroll_bar.maximum())
        else:
            # 否则，恢复到之前保存的像素位置
            scroll_bar.setValue(saved_position)
        #保存新颜色到本地以及修改内存中的颜色----------------------------------------------------------------
        if self.character_list[self.current_char_index].qt_css is not None:
            try:
                with open(f"../reference_audio/{self.character_list[self.current_char_index].character_folder_name}/QT_style.json",'w',encoding='utf-8') as f:
                    f.write(f'''QWidget {{
                    color: {color};
                    }}''')
                self.character_list[self.current_char_index].qt_css=f'''
                                                            QWidget {{
                                                                    color: {color};
                                                                    }}'''
            except Exception as e:
                PrintInfo.print_error(f"[Error]保存主题色失败，错误信息：{e}")
        else:
            try:
                with open(f"../reference_audio/{self.character_list[self.current_char_index].character_folder_name}/QT_style.json",'w',encoding='utf-8') as f:
                    f.write(f'''QWidget {{
                    color: {color};
                    }}''')
                self.character_list[self.current_char_index].qt_css=f'''
                                                            QWidget {{
                                                                    color: {color};
                                                                    }}'''
            except Exception as e:
                PrintInfo.print_error(f"[Error]保存主题色失败，错误信息：{e}")



    def set_pause_second(self):
        pause_second_value=self.pause_second_slider.value()
        self.audio_gen.pause_second=pause_second_value/100
        self.pause_second_label.setText(f"句间停顿时间(s)：{self.audio_gen.pause_second:.2f}")
        self.saved_talk_speed_and_pause_second[self.current_char_index]['pause_second']=self.audio_gen.pause_second  # noqa


    def open_setting_window(self):
        color =ThemeManager.get_QT_style_theme_color(self.character_list[self.current_char_index].qt_css) if self.character_list[self.current_char_index].qt_css is not None else "#7799CC"

        setting_window=SettingWindow(self,self.screen,color,self.audio_gen)
        setting_window.exec_()

    def open_more_function_window(self):
        more_function_win=MoreFunctionWindow(self.close_program)
        more_function_win.exec_()

    def close_program(self):
        self.user_input.setText('bye')
        self.user_input.returnPressed.emit()  # noqa

    def change_character_button_function(self):
        self.user_input.setText('s')
        self.user_input.returnPressed.emit()  # noqa
        self.user_input.clear()

    def talk_speed_reset(self):
        saved_speed=self.saved_talk_speed_and_pause_second[self.current_char_index]['talk_speed']  # noqa
        self.talk_speed_slider.setValue(saved_speed*100)
        self.audio_gen.speed=saved_speed
        self.talk_speed_label.setText(f"语速调节：{self.audio_gen.speed:.2f}")

    def pause_second_reset(self):
        saved_value=self.saved_talk_speed_and_pause_second[self.current_char_index]['pause_second']  # noqa
        self.pause_second_slider.setValue(saved_value*100)
        self.audio_gen.pause_second = saved_value
        self.pause_second_label.setText(f"句间停顿时间(s)：{self.audio_gen.pause_second:.2f}")


    def set_talk_speed(self):
        speed_value=self.talk_speed_slider.value()
        self.audio_gen.speed=speed_value/100
        self.talk_speed_label.setText(f"语速调节：{self.audio_gen.speed:.2f}")
        self.saved_talk_speed_and_pause_second[self.current_char_index]['talk_speed']=self.audio_gen.speed  # noqa

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
        self.start_input_stream()

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
            self.voice_button.setEnabled(True)

        except Exception as e:
            PrintInfo.print_warning(f"[Warning]无法启动麦克风串流，本次运行无法使用语音输入，请检查麦克风是否连接或被其他程序占用。错误信息: {e}")
            self.voice_button.setEnabled(False)  # 保持按鈕禁用

    def audio_callback(self, indata, frames, time, status):
        if self.is_recording:
            self.record_data.append(indata.copy())


    def check_valid(self):
        self.voice_is_valid=True

    def voice_dectect(self):

        self.voice_is_valid=False
        self.is_recording=True
        self.record_data=[]
        self.record_timer.start(300)
        self.user_input.setText('start_talking')
        self.user_input.returnPressed.emit()  # noqa
        self.setWindowTitle("正在录音...松开结束")


    def voice_decect_end(self):
        if not self.is_recording:
            return # 防止重复触发

        self.setWindowTitle("数字小祥")
        self.is_recording=False
        self.record_timer.stop()
        self.user_input.setText('stop_talking')
        self.user_input.returnPressed.emit()  # noqa

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

    def on_transcription_finished(self, text):
        cc = OpenCC('tw2s.json')
        text=cc.convert(text)
        if text=='切换角色':
            self.user_input.setText('s')
            self.user_input.returnPressed.emit()  # noqa
            self.user_input.clear()
        elif text=='切换语言':
            self.user_input.setText('l')
            self.user_input.returnPressed.emit()  # noqa
            self.user_input.clear()
        else:
            self.user_input.setText(text)

        if self.whisper_model and not self.is_recording:
            self.voice_button.setEnabled(True)
            self.setWindowTitle("数字小祥")

    def closeEvent(self, event):
        if not hasattr(self,"stream"):
            event.accept()
            return
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                PrintInfo.print_error(f"[Error]关闭麦克风串流时出错: {e}")

        event.accept()

    def play_history_audio(self,audio_path_and_emotion):
        local_pos = self.chat_display.viewport().mapFromGlobal(QCursor.pos())
        # 2. 根据坐标获取对应的文本光标 (QTextCursor)    想不到别的方法获取回放文本
        cursor = self.chat_display.cursorForPosition(local_pos)
        # 3. 获取光标所在的文本
        full_text = cursor.block().text()
        if self.live2d_mod.live2d_this_turn_motion_complete:
            self.setWindowTitle("数字小祥")
            audio_path_and_emotion=audio_path_and_emotion.toString()
            match=re.match(r"(.+?)\[(.+?)\]$", audio_path_and_emotion)
            if match:
                audio_path = match.group(1)  # 路径
                emotion = match.group(2)  #emotion标签


                if os.path.exists(audio_path):
                    #----------------------------设置live2d文本框内容逻辑
                    pattern = r"^★[^：:]+[：:](.*)"
                    match = re.search(pattern, full_text)
                    content = match.group(1).strip() if match else full_text
                    self.live2d_mod.new_text=re.sub(r"（.*?）",'',content).strip()
                    #----------------------------
                    self.audio_file_path_queue.put(audio_path)
                    self.emotion_queue.put(emotion)
                    PrintInfo.print_info("音频文件路径："+audio_path)
                    #print("注意：若你已经设置了if_delete_audio_cache.txt中的数字不为0，并且觉得这句生成的还不错，请复制该音频文件到别处，因为设置数字不为0的情况下关闭程序会自动删除该文件，以释放空间。设置数字不为0的情况下如果希望下次打开程序还能听到，再把这个文件复制回这个路径即可。\n")
                else:
                    self.setWindowTitle('所选文本对应的音频文件已经删除...')
                    PrintInfo.print_info("所选文本对应的音频文件已经删除...\n")
        else:
            self.setWindowTitle('请等待当前过程完成后重试...')



    def change_char(self):
        record = self.chat_display.toHtml()
        self.character_chat_history[self.current_char_index] = record  # noqa
        self.chat_display.clear()
        if len(self.character_list) == 1:
            self.current_char_index = 0
        else:
            if self.current_char_index < len(self.character_list) - 1:
                self.current_char_index += 1
            else:
                self.current_char_index = 0
        if self.character_list[self.current_char_index].qt_css is not None:  # noqa
            self.setStyleSheet(ThemeManager.generate_stylesheet(
                ThemeManager.get_QT_style_theme_color(self.character_list[self.current_char_index].qt_css)
                )
            )
            self.set_btn_color(
                ThemeManager.get_QT_style_theme_color(self.character_list[self.current_char_index].qt_css))
        else:
            self.setStyleSheet(ThemeManager.generate_stylesheet("#7799CC"))
            self.set_btn_color('#7799CC')

        chat_history_html_content = re.sub(r'font-family:\s*[^;"]+;', '', self.character_chat_history[self.current_char_index].replace('underline','none'))  # noqa
        self.chat_display.setHtml(chat_history_html_content)
        if self.character_list[self.current_char_index].icon_path is not None:  # noqa
            self.setWindowIcon(QIcon(self.character_list[self.current_char_index].icon_path))  # noqa
        self.talk_speed_reset()  #切换角色后重置默认语速
        self.pause_second_reset()  #切换角色后重置默认句间停顿时间

        if self.character_list[self.current_char_index].GPT_model_path is None or self.character_list[self.current_char_index].gptsovits_ref_audio is None or self.character_list[self.current_char_index].sovits_model_path is None:
            #self.last_if_generate_audio=self.dp_chat.if_generate_audio
            self.dp_chat.if_generate_audio=False
        else:
            self.dp_chat.if_generate_audio=True

    def handle_response(self,response_text):
        if response_text=='changechange':
            self.change_char()
            return
        response_text=response_text.replace("\n\n",'')
        response_text=response_text.replace("\n", '')
        response_text = response_text.replace("。。", '。')
        pattern = r'(.*?)(?:\[翻译\](.+?)\[翻译结束\])'
        response_tuple_list=re.findall(pattern,response_text,flags=re.DOTALL)
        if not response_tuple_list: #中文
            self.full_response = response_text+"\n"
            self.translation =''
        else:   #日文
            self.full_response=response_tuple_list[0][0]+'\n'  # noqa
            self.translation=response_tuple_list[0][1]  # noqa
        self.current_index = 0

        text_color = self.chat_display.palette().color(QPalette.Text).name()
        if self.dp_chat.if_generate_audio and response_text!='（再见）':

            if not response_tuple_list:
                emotion=self.emotion_model(response_text)[0]['label']
            else:
                emotion = self.emotion_model(response_tuple_list[0][1])[0]['label']
            abs_path = os.path.abspath(self.audio_gen.audio_file_path).replace('\\', '/')
            self.chat_display.append(f'<a href="{abs_path}[{emotion}]" style="text-decoration: none; color: {text_color};">★{self.character_list[self.current_char_index].character_name}：</a>')     #将emotion藏进路径中，回来解包一下即可  # noqa
            self.live2d_mod.new_text=self.full_response+self.translation    #更新live2d文本框的内容显示
        else:
            self.chat_display.append(f'<a style="text-decoration: none; color: {text_color};">{self.character_list[self.current_char_index].character_name}：</a>')  # noqa

        self.timer.start(30)

    def stream_print(self):     #模拟流式打印
        cursor = self.chat_display.textCursor()
        if self.current_index < len(self.full_response):
            cursor.movePosition(cursor.End)
            cursor.insertText(self.full_response[self.current_index])  # noqa
            self.chat_display.setTextCursor(cursor)
            self.current_index += 1
        else:
            self.timer.stop()
            if self.translation!='':
                cursor.movePosition(cursor.End)
                cursor.insertHtml(f'<span style="color: #B3D1F2; font-style: italic;">{self.translation}</span><br>')
                self.translation=''
                self.chat_display.moveCursor(QTextCursor.End)
    @staticmethod
    def is_display(text):
        text1=re.findall('切换GPT-SoVITS',text,flags=re.DOTALL)
        text2 = re.findall('已切换为', text, flags=re.DOTALL)
        text3=re.findall('整理语言',text,flags=re.DOTALL)
        text4=re.findall('思考中',text,flags=re.DOTALL)
        return not (text1 or text2 or text3 or text4)

    @staticmethod
    def is_display2(text):
        flag=True
        user_input_no_display_list=['s','l','m','clr','conv','v',
                                    'clr','mask','save','start_talking','stop_talking','change_l2d_background']
        for x in user_input_no_display_list:
            if text == x:
                flag = False
        if text.startswith('change_l2d_model'):
            flag = False
        return flag

    def handle_user_input(self):
        def clr_history():
            self.chat_display.clear()
            self.qt2dp_queue.put('clr')

        self.setWindowTitle("数字小祥")
        user_this_turn_input=self.user_input.text()
        user_this_turn_input=user_this_turn_input.strip(' ')
        if user_this_turn_input=='':
            user_this_turn_input="（什么也没说）"
        current_text = self.messages_box.toPlainText()
        if (user_this_turn_input!='clr'
            and user_this_turn_input!='save'
            and self.is_display(current_text)
            and self.qt2dp_queue.empty()):
            self.qt2dp_queue.put(user_this_turn_input)
        self.user_input.clear()
        user_input_no_display_list=[
                                    "0：deepseek-r1:14b（需安装Ollama与对应本地大模型，选项1相同）  1：deepseek-r1:32b  \n2：调用deepseek-V3官方API（无需安装Ollama，只需联网)",
                                    f"{self.character_list[self.current_char_index].character_name}思考中...",'小祥思考中...']  # noqa
        if self.is_display2(user_this_turn_input):     #判断是否显示的逻辑，比较笨的方法
            flag=True
            for x in user_input_no_display_list:
                if current_text==x:
                    flag=False
            if flag:
                flag=self.is_display(current_text)
            if flag:
                self.full_response = user_this_turn_input + "\n"
                self.current_index = 0
                text_color = self.chat_display.palette().color(QPalette.Text).name()
                self.chat_display.append(f'<a style="text-decoration: none; color: {text_color};">你：')
                self.timer.start(8)
        if user_this_turn_input=='clr':
            css = ThemeManager.generate_stylesheet(
                ThemeManager.get_QT_style_theme_color(self.character_list[self.current_char_index].qt_css)
            ) if self.character_list[self.current_char_index].qt_css is not None else ThemeManager.generate_stylesheet(
                '#7799CC')
            pop_up_clr_warning_win=WarningWindow("确定要清空当前角色的聊天记录吗？角色记忆也将同步被删除",css,clr_history)
            pop_up_clr_warning_win.exec_()

        if user_this_turn_input=='save':
            self.save_data()

        if user_this_turn_input=='l':   #切换语言时也会变更语速
            self.is_ch=not self.is_ch
            self.talk_speed_reset()
            self.pause_second_reset()

    def save_data(self):
        dp_messages=self.dp_chat.all_character_msg
        final_data_dp=[]
        for index,char_msg in enumerate(dp_messages):
            final_data_dp.append({'character':self.character_list[index].character_name,'history':char_msg})  # noqa
        with open('../reference_audio/history_messages_dp.json', 'w', encoding='utf-8') as f:
            json.dump(final_data_dp, f, ensure_ascii=False, indent=4)  # noqa
            f.close()

        record = self.chat_display.toHtml()
        self.character_chat_history[self.current_char_index] = record  # noqa
        final_data_qt=[]
        for index,char_msg in enumerate(self.character_chat_history):
            final_data_qt.append({'character':self.character_list[index].character_name,'history':char_msg})  # noqa
        with open('../reference_audio/history_messages_qt.json', 'w', encoding='utf-8') as f:
            json.dump(final_data_qt, f, ensure_ascii=False, indent=4)  # noqa
            f.close()
        self.setWindowTitle("已保存最新的聊天记录！")

    def handle_messages(self,message):
        if message=='bye':
            self.save_data()
            self.close()
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
    win = ChatGUI(dp2qt_queue, qt2dp_queue, QT_message_queue, characters, None, audio_gen_mock,None,None,None,None)

    font_id = QFontDatabase.addApplicationFont("../font/ft.ttf")
    font_family = QFontDatabase.applicationFontFamilies(font_id)[0]  # noqa
    font = QFont(font_family, 12)
    app.setFont(font)

    t1 = threading.Thread(target=dp_thread, args=(dp2qt_queue, qt2dp_queue, QT_message_queue))
    t1.start()

    win.show()
    sys.exit(app.exec_())