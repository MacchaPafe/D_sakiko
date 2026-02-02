import json
import multiprocessing
import sys,os
import pathlib

from PyQt5.QtCore import Qt

script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

import time
import threading
from queue import Queue

import live2d.v2 as live2d
from qtUI import ChangeL2DModelWindow
import pygame
from pygame.locals import DOUBLEBUF, OPENGL
from OpenGL.GL import *
import glob,os
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTextBrowser, QPushButton, QDesktopWidget, QHBoxLayout, \
    QApplication, QLabel

from PyQt5.QtGui import QFontDatabase, QFont, QIcon, QTextCursor, QPalette

import character
# 兼容性 Hook
from live2d_module import LAppModel

class BackgroundRen(object):

    @staticmethod
    def render(surface: pygame.SurfaceType) -> object:
        texture_data = pygame.image.tostring(surface, "RGBA", True)
        id: object = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, id)
        glTexImage2D(
            GL_TEXTURE_2D,
            0,
            GL_RGBA,
            surface.get_width(),
            surface.get_height(),
            0,
            GL_RGBA,
            GL_UNSIGNED_BYTE,
            texture_data
        )
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        return id

    @staticmethod
    def blit( *args: tuple[tuple[3], tuple[3], tuple[3], tuple[3]]) -> None:

        glBegin(GL_QUADS)
        glTexCoord2f(0, 0);glVertex3f(*args[3])
        glTexCoord2f(1, 0);glVertex3f(*args[1])
        glTexCoord2f(1, 1);glVertex3f(*args[2])
        glTexCoord2f(0, 1);glVertex3f(*args[0])
        glEnd()

idle_recover_timer=time.time()


class Live2DModule:
    def __init__(self):
        self.PATH_JSON=None
        self.BACK_IMAGE=None
        self.BACKGROUND_POSITION=((-1.0, 1.0, 0), (1.0, -1.0, 0), (1.0, 1.0, 0), (-1.0, -1.0, 0))
        self.run=True
        self.sakiko_state=True
        self.if_sakiko=False
        self.if_mask=True
        self.character_list=[]
        self.current_character_num=0

    def change_character(self):
        if len(self.character_list)==1:
            self.current_character_num = 0
        else:
            if self.current_character_num<len(self.character_list)-1:
                self.current_character_num+=1
            else:
                self.current_character_num=0

        self.PATH_JSON=self.character_list[self.current_character_num].live2d_json

        if self.character_list[self.current_character_num].character_name=='祥子':
            self.if_sakiko=True
        else:
            self.if_sakiko=False

    def live2D_initialize(self,characters):
        self.character_list=characters
        self.PATH_JSON=self.character_list[self.current_character_num].live2d_json
        if self.character_list[self.current_character_num].character_name=='祥子':
            self.if_sakiko=True
        else:
            self.if_sakiko=False

        back_img_png=glob.glob(os.path.join("../live2d_related",f"*.png"))
        back_img_jpg = glob.glob(os.path.join("../live2d_related", f"*.jpg"))
        if not (back_img_png+back_img_jpg):
            raise FileNotFoundError("没有找到背景图片文件(.png/.jpg)，自带的也被删了吗...")
        self.BACK_IMAGE=max((back_img_jpg+back_img_png),key=os.path.getmtime)
        #print("Live2D初始化...OK")

    def play_live2d(self,
                    motion_queue,
                    change_char_queue):
        #print("正在开启Live2D模块")
        import tkinter as tk    # 获取屏幕分辨率
        root = tk.Tk()
        desktop_w,desktop_h=root.winfo_screenwidth(),root.winfo_screenheight()
        root.destroy()
        win_w_and_h = int(0.7 * desktop_h)  # 根据显示器分辨率定义窗口大小，保证每个人看到的效果相同
        pygame_win_pos_w,pygame_win_pos_h=int(0.5*desktop_w-win_w_and_h),int(0.5*desktop_h-0.5*win_w_and_h)
        #以上设置后，会差出一个恶心的标题栏高度，因此还要加上一个标题栏高度
        import ctypes
        caption_height = (ctypes.windll.user32.GetSystemMetrics(4)
                          +ctypes.windll.user32.GetSystemMetrics(33)
                          +ctypes.windll.user32.GetSystemMetrics(92))    # 标题栏高度+厚度
        os.environ['SDL_VIDEO_WINDOW_POS'] = f"{pygame_win_pos_w},{pygame_win_pos_h+caption_height}"   #设置窗口位置，与qt窗口对齐
        pygame.init()
        live2d.init()

        display = (win_w_and_h, win_w_and_h)
        pygame.display.set_mode(display, DOUBLEBUF | OPENGL)
        #pygame.display.set_icon(pygame.image.load("../live2d_related/sakiko_icon.png"))
        model = live2d.LAppModel()
        model.LoadModelJson(self.PATH_JSON)

        model.Resize(win_w_and_h, win_w_and_h)
        model.SetAutoBlinkEnable(True)
        model.SetAutoBreathEnable(True)
        if self.if_sakiko:
            model.SetExpression('serious')
        glEnable(GL_TEXTURE_2D)

        #texture_thinking=BackgroundRen.render(pygame.image.load('X:\\D_Sakiko2.0\\live2d_related\\costumeBG.png').convert_alpha())    #想做背景切换功能，但无论如何都会有bug
        texture = BackgroundRen.render(pygame.image.load(self.BACK_IMAGE).convert_alpha())
        glBindTexture(GL_TEXTURE_2D, texture)

        global idle_recover_timer

        print("当前Live2D界面渲染硬件", glGetString(GL_RENDERER).decode())
        while self.run:

            for event in pygame.event.get():    #退出程序逻辑
                if event.type == pygame.QUIT:
                    self.run = False
                    break

                else:
                    pass

            if not change_char_queue.empty():
                x=change_char_queue.get()
                if x=="exit":
                    self.run=False
                    continue
                # 传入 change_character 字符串，表示要求切换角色
                if x == "change_character":
                    self.change_character()
                    if self.if_sakiko and self.sakiko_state:
                        model.LoadModelJson('../live2d_related/sakiko/live2D_model_costume/3.model.json')
                    else:
                        model.LoadModelJson(self.PATH_JSON)
                    model.Resize(win_w_and_h, win_w_and_h)
                    model.SetAutoBlinkEnable(True)
                    model.SetAutoBreathEnable(True)

                    if self.character_list[self.current_character_num].icon_path is not None:
                        pygame.display.set_icon(pygame.image.load(self.character_list[self.current_character_num].icon_path))
                # 传入一个路径，表示要求加载同角色一个新的 live2d 模型
                elif os.path.exists(x):
                    model.LoadModelJson(x, disable_precision=True)
                    model.Resize(win_w_and_h, win_w_and_h)
                    model.SetAutoBlinkEnable(True)
                    model.SetAutoBreathEnable(True)

            if not motion_queue.empty():
                motion_name=motion_queue.get()
                mtn = model.loadMotion(None, motion_name)
                model._LAppModel__setFadeInFadeOut(None,None,4,mtn)


            # 清除缓冲区
            #live2d.clearBuffer()
            glClear(GL_COLOR_BUFFER_BIT)
            # 更新live2d到缓冲区
            model.Update()
            # 渲染背景图片
            BackgroundRen.blit(*self.BACKGROUND_POSITION)

            model.Draw()
            glUseProgram(0)
            # 4、pygame刷新
            pygame.display.flip()


        live2d.dispose()
        #结束pygame
        pygame.quit()


class ViewerGUI(QWidget):
    def __init__(self,
                 characters,
                 motion_queue,
                 change_char_queue):
        super().__init__()
        self.setWindowTitle("动作编辑器")
        # self.setWindowIcon(QIcon("../live2d_related/sakiko_icon.png"))
        self.screen = QDesktopWidget().screenGeometry()
        self.resize(int(0.4 * self.screen.width()), int(0.7 * self.screen.height()))
        self.all_mnt_display = QTextBrowser()
        self.all_mnt_display.setOpenExternalLinks(False)
        self.all_mnt_display.setOpenLinks(False)
        self.all_mnt_display.anchorClicked.connect(self.play_motion_all_mtn_ver)
        self.current_mnt_display = QTextBrowser()
        self.current_mnt_display.setOpenExternalLinks(False)
        self.current_mnt_display.setOpenLinks(False)
        self.current_mnt_display.anchorClicked.connect(self.play_motion_cur_mtn_ver)
        self.message_box=QTextBrowser()
        self.btn_change_char=QPushButton("切换角色")
        self.btn_change_char.clicked.connect(self.change_char)
        self.btn_change_costume = QPushButton("切换当前角色服装")
        self.btn_change_costume.clicked.connect(self.change_costume)
        self.exit_edit=QPushButton("关闭程序")
        self.exit_edit.clicked.connect(self.exit)
        special_btn_layout=QHBoxLayout()
        special_btn_layout.addWidget(self.btn_change_char)
        special_btn_layout.addWidget(self.btn_change_costume)
        special_btn_layout.addWidget(self.exit_edit)
        # 操作栏（替代原先 13 个“替换到动作组X”按钮）
        self.btn_add_motion = QPushButton("添加")
        self.btn_replace_motion = QPushButton("替换")
        self.btn_delete_motion = QPushButton("删除")
        self.btn_add_motion.clicked.connect(self.on_add_motion)
        self.btn_replace_motion.clicked.connect(self.on_replace_motion)
        self.btn_delete_motion.clicked.connect(self.on_delete_motion)
        op_btn_layout = QHBoxLayout()
        op_btn_layout.addWidget(self.btn_add_motion)
        op_btn_layout.addWidget(self.btn_replace_motion)
        op_btn_layout.addWidget(self.btn_delete_motion)

        # ---创建两个标题 QLabel ---
        self.all_mnt_title = QLabel("所有mtn文件")
        self.current_mnt_title = QLabel("动作组编辑")

        self.all_mnt_title.setAlignment(Qt.AlignCenter)
        self.current_mnt_title.setAlignment(Qt.AlignCenter)
        main_layout = QVBoxLayout()

        # 2.1 顶部左右并排的显示区
        top_display_layout = QHBoxLayout()

        # --- 创建左侧垂直布局 (标题 + 文本框) ---
        left_column_layout = QVBoxLayout()
        left_column_layout.addWidget(self.all_mnt_title)
        left_column_layout.addWidget(self.all_mnt_display)

        # --- 创建右侧垂直布局 (标题 + 文本框) ---
        right_column_layout = QVBoxLayout()
        right_column_layout.addWidget(self.current_mnt_title)
        right_column_layout.addWidget(self.current_mnt_display)

        # --- 将两个垂直布局添加到顶部的水平布局中 ---
        top_display_layout.addLayout(left_column_layout)
        top_display_layout.addLayout(right_column_layout)

        # --- 3. 将所有子布局添加到主布局 ---
        main_layout.addLayout(top_display_layout, stretch=5)  # stretch=5 让显示区占据更多垂直空间
        main_layout.addWidget(self.message_box, stretch=1)  # stretch=1 给日志区一点空间
        main_layout.addLayout(op_btn_layout, stretch=1)  # 操作栏
        main_layout.addLayout(special_btn_layout)  # 单独的按钮
        self.setLayout(main_layout)
        self.setStyleSheet("""
                        QWidget {
                            background-color: #E6F2FF;
                            color: #7799CC;
                        }

                        QTextBrowser{
                            text-decoration: none;
                            background-color: #FFFFFF;
                            border: 3px solid #B3D1F2;
                            border-radius:9px;
                            padding: 5px;
                        }
                        
                        QPushButton {                
                            background-color: #7FB2EB;
                            color: #ffffff;
                            border-radius: 6px;
                            padding: 6px;
                        }

                        QPushButton:hover {
                            background-color: #3FB2EB;
                        }

                        QPushButton:disabled {
                            background-color: #D0E2F0;
                            color: #7799CC;
                        }    

                        QScrollBar:vertical {
                            border: none;
                            background: #D0E2F0;
                            width: 10px;
                            margin: 0px 0px 0px 0px;
                        }

                        QScrollBar::handle:vertical {
                            background: #B3D1F2;
                            min-height: 20px;
                            border-radius: 3px;
                        }

                    """)

        self.character_list=characters
        self.current_char_index = 0
        self.motion_queue=motion_queue
        self.change_char_queue=change_char_queue
        self.all_motion_data=None   # 存储当前角色的所有动作数据

        # 选中状态：左侧动作文件 / 右侧动作组或动作
        self.left_selected_motion_path: str | None = None
        self.right_selected_group: str | None = None
        self.right_selected_index: int | None = None

        # 动作组标题显示（key -> 显示名）
        self.group_display_titles = {
            "happiness": "1. 开心",
            "sadness": "2. 伤心",
            "anger": "3. 生气",
            "disgust": "4. 反感",
            "like": "5. 喜欢",
            "surprise": "6. 惊讶",
            "fear": "7. 害怕",
            "IDLE": "8. 待机时随机",
            "text_generating": "9. 思考时",
            "bye": "10. 退出程序",
            "change_character": "11. 登场动作",
            "idle_motion": "12. 待机",
            "talking_motion": "13. 按下语音按钮",
        }

        self.current_char_base_folder_name = ""  # 存储当前角色的基础文件夹名称（其实就是角色名称）

        self.current_char_folder_path = pathlib.Path("")  # 存储当前角色的动作文件夹路径（这个路径可能为默认 live2d 模型的路径，也可能为 extra_model 中的某个模型路径，取决于用户选择）

        # 默认使用默认模型（这句话多少有点废话了）
        self.use_default_model = {0: True} # 角色 index-bool；True 表示使用默认模型，False 表示使用 extra_model 中的模型
        self.extra_model_name = {0: None} # 角色 index-str；如果 use_default_model 为 False，那么该变量存储要使用的 extra_model 名称
        # 如果角色 index 不存在于上述两个字典中，则表示该角色第一次加载模型，默认使用默认模型

        self.load_suitable_model()
        self.message_box.append("当前角色："+self.character_list[self.current_char_index].character_name)
        self.update_button_states()

    def load_suitable_model(self):
        """
        根据 self.use_default_model 和 self.extra_model_name 的值，加载合适的模型。
        """
        if self.use_default_model.get(self.current_char_index, True):
            self.load_model(None)
        else:
            self.load_model(self.extra_model_name.get(self.current_char_index))

    def use_default_model_for_current_character(self):
        """
        切换显示模块使用当前角色的默认模型，并且更新类属性，保存这一设置
        """
        self.use_default_model[self.current_char_index] = True
        self.extra_model_name[self.current_char_index] = None
        self.load_model(None)

    def use_extra_model_for_current_character(self, extra_model_name):
        """
        切换为使用当前角色的 extra_model 中的模型，并且更新类属性，保存这一设置

        :param extra_model_name: 要使用的 extra_model 名称。
        """
        self.use_default_model[self.current_char_index] = False
        self.extra_model_name[self.current_char_index] = extra_model_name
        self.load_model(extra_model_name)

    def load_model(self, extra_model_name=None):
        """
        加载一个角色模型的所有动作名称信息。

        :param extra_model_name: 如果该参数不是 None，那么改为加载该角色文件夹中 extra_model/{extra_model_name} 文件夹中的模型，而非默认模型。
        """
        if self.character_list[self.current_char_index].character_name == '祥子':
            self.current_mnt_display.clear()
            self.current_mnt_display.append("祥子暂时不能编辑动作组")
            self.btn_change_costume.setEnabled(False)
            # 仍然设置一个可用的动作文件夹路径，保证左侧 mtn 列表可用
            self.current_char_base_folder_name = self.character_list[self.current_char_index].character_folder_name
            costume_path = pathlib.Path("../live2d_related") / self.current_char_base_folder_name / "live2D_model_costume"
            default_path = pathlib.Path("../live2d_related") / self.current_char_base_folder_name / "live2D_model"
            if costume_path.exists():
                self.current_char_folder_path = costume_path
            else:
                self.current_char_folder_path = default_path

            self.all_motion_data = None
            self.left_selected_motion_path = None
            self.right_selected_group = None
            self.right_selected_index = None
            self.update_button_states(disable_all=True)
        else:
            self.btn_change_costume.setEnabled(True)

            self.current_char_base_folder_name = self.character_list[self.current_char_index].character_folder_name
            if extra_model_name is not None:
                self.current_char_folder_path = pathlib.Path("../live2d_related") / self.character_list[self.current_char_index].character_folder_name / "extra_model" / extra_model_name
            else:
                self.current_char_folder_path = pathlib.Path("../live2d_related") / self.character_list[self.current_char_index].character_folder_name / "live2D_model"

            with open(self.current_char_folder_path / '3.model.json','r',encoding='utf-8') as f:
                self.all_motion_data=json.load(f)

            # 切换角色/模型时，重置选中状态
            self.left_selected_motion_path = None
            self.right_selected_group = None
            self.right_selected_index = None

            self.current_mnt_display.clear()
            self.refresh_current_mnt_display(preserve_scroll=False)
            self.update_button_states()

        # 重新填充左侧可选 mtn 文件列表
        self.all_mnt_display.clear()
        folder_path=self.current_char_folder_path

        all_paths = []
        for filename in os.listdir(folder_path):
            full_path = (folder_path / filename).resolve()
            if full_path.is_file() and filename.lower().endswith('.mtn'):
                full_path=full_path.as_posix()
                all_paths.append(full_path)

        all_paths.sort()
        for idx, path in enumerate(all_paths, start=1):
            self.all_mnt_display.append(f'<a href="{path}" style="text-decoration: none; color: #7799CC;">{idx}. {os.path.basename(path)}</a>'+'\n')

    def change_char(self):
        if len(self.character_list) == 1:
            self.current_char_index = 0
        else:
            if self.current_char_index < len(self.character_list) - 1:
                self.current_char_index += 1
            else:
                self.current_char_index = 0

        if self.character_list[self.current_char_index].icon_path is not None:
            self.setWindowIcon(QIcon(self.character_list[self.current_char_index].icon_path))
        self.all_mnt_display.clear()
        self.current_mnt_display.clear()
        self.message_box.clear()
        # 加载新角色的默认模型
        self.load_suitable_model()
        self.message_box.append("当前角色：" + self.character_list[self.current_char_index].character_name)
        self.change_char_queue.put("change_character")

    def change_costume(self):
        """
        弹出管理对话框，允许用户选择并切换切换角色的服装
        """
        dialog = ChangeL2DModelWindow(self.current_char_base_folder_name, lambda path: self._on_change_costume_confirmed(path))
        dialog.exec()

    def _on_change_costume_confirmed(self, new_model_path):
        """
        在用户选择了一个新的服装模型后，执行切换动作。
        由于这个参数是从 ChangeL2DModelWindow 窗口传回来的，不能修改原代码的实现，这个 new_model_path 参数直接指向了 3.model.json
        我们需要解析上一层文件夹的路径，来确定这是个默认模型还是自定义模型；如果是自定义模型，它的文件夹名称（模型名称）是什么。
        """
        print(f"用户选择了新的服装模型路径：{new_model_path}")
        parent_path = pathlib.Path(new_model_path).parent
        extra_model_folder = pathlib.Path("../live2d_related") / self.current_char_base_folder_name / "extra_model"
        if parent_path == pathlib.Path("../live2d_related") / self.current_char_base_folder_name / "live2D_model":
            # 用户选择了默认模型
            self.use_default_model_for_current_character()
        elif parent_path.parent == extra_model_folder:
            # 用户选择了 extra_model 中的某个模型
            extra_model_name = parent_path.name
            self.use_extra_model_for_current_character(extra_model_name)
        else:
            # 其他情况，理论上不应该发生
            self.message_box.append("无法识别所选模型的类型，未进行切换。")
            return

        self.message_box.append(f"已切换到角色 {self.character_list[self.current_char_index].character_name} 的新服装模型。")
        self.change_char_queue.put(new_model_path)

    def play_motion_cur_mtn_ver(self,motion_path):

        # 右侧栏点击：可能是组标题，也可能是某个具体动作
        url_str = motion_path.toString()
        if self.character_list[self.current_char_index].character_name == '祥子':
            return
        if self.all_motion_data is None:
            return

        if url_str.startswith("group:"):
            group_key = url_str.split(":", 1)[1]
            self.right_selected_group = group_key
            self.right_selected_index = None
            self.message_box.clear()
            self.message_box.append(f"已选中动作组：{self.group_display_titles.get(group_key, group_key)}\n"
                                    f"选择左侧动作后，点击『添加』将动作添加到该组中。")
            self.refresh_current_mnt_display(preserve_scroll=True)
            self.update_button_states()
            return

        if url_str.startswith("item:"):
            # item:{group}:{index}
            try:
                _, group_key, index_str = url_str.split(":", 2)
                index = int(index_str)
            except Exception:
                return

            self.right_selected_group = group_key
            self.right_selected_index = index

            try:
                motion_filename = self.all_motion_data['motions'][group_key][index]['file']
            except Exception:
                return

            abs_path = (self.current_char_folder_path / motion_filename).resolve().as_posix()
            self.motion_queue.put(abs_path)
            self.message_box.clear()
            self.message_box.append(
                f"已选中动作：{motion_filename}\n"
                f"可删除/替换动作，或者添加左侧动作到该组中。"
            )
            self.refresh_current_mnt_display(preserve_scroll=True)
            self.update_button_states()
            return

        # 兼容旧格式：如果仍然是路径链接，就当作“预览动作”
        if os.path.exists(url_str):
            self.motion_queue.put(url_str)
            self.message_box.clear()
            self.message_box.append(f"当前预览动作：\n{os.path.basename(url_str)}")

    def play_motion_all_mtn_ver(self,motion_path):

        motion_path=motion_path.toString()
        self.left_selected_motion_path = motion_path
        self.message_box.clear()
        self.message_box.append(f"当前选中动作（左侧）：\n{os.path.basename(motion_path)}")

        if os.path.exists(motion_path):
            self.motion_queue.put(motion_path)

        self.update_button_states()

    def update_button_states(self, disable_all: bool = False):
        if disable_all or self.character_list[self.current_char_index].character_name == '祥子':
            self.btn_add_motion.setEnabled(False)
            self.btn_replace_motion.setEnabled(False)
            self.btn_delete_motion.setEnabled(False)
            return

        has_left = self.left_selected_motion_path is not None
        has_right_group = self.right_selected_group is not None
        has_right_item = self.right_selected_index is not None

        # 添加：左侧有动作 & 右侧选择了组（标题或动作均可，动作时表示插入）
        self.btn_add_motion.setEnabled(has_left and has_right_group)
        # 替换：左侧有动作 & 右侧选择了具体动作
        self.btn_replace_motion.setEnabled(has_left and has_right_item)
        # 删除：右侧选择了具体动作
        self.btn_delete_motion.setEnabled(has_right_item)

    def refresh_current_mnt_display(self, preserve_scroll: bool = True):
        if self.all_motion_data is None:
            self.current_mnt_display.clear()
            return

        scroll_bar = self.current_mnt_display.verticalScrollBar()
        saved_pos = scroll_bar.value() if (preserve_scroll and scroll_bar is not None) else 0

        # 使用自定义 URL scheme：
        # - group:{group_key}
        # - item:{group_key}:{index}
        html_parts: list[str] = []
        motions = self.all_motion_data.get('motions', {})
        for group_key, motion_values in motions.items():
            title = self.group_display_titles.get(group_key, group_key)
            if group_key == self.right_selected_group and self.right_selected_index is None:
                title_color = "#ED784A"
            else:
                title_color = "#FFB099"

            html_parts.append(
                f'<div style="margin-top:8px;">'
                f'<a href="group:{group_key}" style="text-decoration:none; color:{title_color}; font-weight:bold;">'
                f'【{title}】'
                f'</a>'
                f'</div>'
            )

            for idx, motion in enumerate(motion_values):
                file_name = motion.get('file', '')
                if group_key == self.right_selected_group and self.right_selected_index == idx:
                    item_color = "#ED784A"
                else:
                    item_color = "#7799CC"
                html_parts.append(
                    f'<div style="margin-left:12px;">'
                    f'<a href="item:{group_key}:{idx}" style="text-decoration:none; color:{item_color};">'
                    f'★{idx + 1}：{file_name}'
                    f'</a></div>'
                )

        self.current_mnt_display.setHtml("\n".join(html_parts))

        if scroll_bar is not None:
            scroll_bar.setValue(saved_pos)

    def _write_motion_json(self):
        if self.all_motion_data is None:
            return
        with open(self.current_char_folder_path / '3.model.json', 'w', encoding='utf-8') as f:
            json.dump(self.all_motion_data, f, indent=4, ensure_ascii=False)

    def _generate_unique_motion_name(self, group_key: str) -> str:
        """生成动作 name：{group_key}_{n}，n 为从 1 开始的最小可用正整数。

        扫描整个 model.json 的 motions，确保 name 不重复。
        """
        if self.all_motion_data is None:
            return f"{group_key}_1"

        existing_names: set[str] = set()
        motions = self.all_motion_data.get('motions', {})
        for _g, motion_list in motions.items():
            for entry in motion_list:
                if isinstance(entry, dict):
                    name = entry.get('name')
                    if isinstance(name, str) and name:
                        existing_names.add(name)

        n = 1
        while True:
            candidate = f"{group_key}_{n}"
            if candidate not in existing_names:
                return candidate
            n += 1

    def _make_motion_entry(self, file_name: str, reference_entry: dict | None, motion_name: str) -> dict:
        if reference_entry is None:
            return {"name": motion_name, "file": file_name}
        # 复制参考 entry 的所有字段，但覆盖 name/file
        new_entry = dict(reference_entry)
        new_entry["name"] = motion_name
        new_entry["file"] = file_name
        return new_entry

    def _reload_after_change(self):
        # 尽量保留用户的选中状态，便于连续编辑
        old_left = self.left_selected_motion_path
        old_group = self.right_selected_group
        old_index = self.right_selected_index

        # 保存左右滚动位置
        scroll_bar_right = self.current_mnt_display.verticalScrollBar()
        scroll_bar_left = self.all_mnt_display.verticalScrollBar()
        saved_position_right = scroll_bar_right.value() if scroll_bar_right is not None else 0
        saved_position_left = scroll_bar_left.value() if scroll_bar_left is not None else 0

        # 重新加载并刷新
        self.load_suitable_model()

        # 恢复选中状态（仅在可编辑角色时）
        if self.character_list[self.current_char_index].character_name != '祥子' and self.all_motion_data is not None:
            self.left_selected_motion_path = old_left
            self.right_selected_group = old_group
            self.right_selected_index = old_index

            motions = self.all_motion_data.get('motions', {})
            if self.right_selected_group not in motions:
                self.right_selected_group = None
                self.right_selected_index = None
            else:
                motion_list = motions[self.right_selected_group]
                if self.right_selected_index is not None:
                    if len(motion_list) == 0:
                        self.right_selected_index = None
                    else:
                        self.right_selected_index = max(0, min(self.right_selected_index, len(motion_list) - 1))

            self.refresh_current_mnt_display(preserve_scroll=True)
            self.update_button_states()

        # 恢复滚动条
        scroll_bar_right = self.current_mnt_display.verticalScrollBar()
        scroll_bar_left = self.all_mnt_display.verticalScrollBar()
        if scroll_bar_right is not None:
            scroll_bar_right.setValue(saved_position_right)
        if scroll_bar_left is not None:
            scroll_bar_left.setValue(saved_position_left)

    def on_add_motion(self):
        if self.all_motion_data is None:
            self.message_box.clear()
            self.message_box.append("动作数据未加载成功，无法添加动作！")
            return
        if self.left_selected_motion_path is None:
            self.message_box.clear()
            self.message_box.append("请先从左侧栏选择一个动作文件！")
            return
        if self.right_selected_group is None:
            self.message_box.clear()
            self.message_box.append("请先在右侧选择一个动作组标题或动作条目！")
            return

        group_key = self.right_selected_group
        target_list = self.all_motion_data.get('motions', {}).get(group_key)
        if target_list is None:
            self.message_box.clear()
            self.message_box.append(f"动作组 '{group_key}' 不存在，无法添加！")
            return

        file_name = os.path.basename(self.left_selected_motion_path)

        # 新动作的 name：{group}_{n}，n 取最小可用
        new_motion_name = self._generate_unique_motion_name(group_key)

        # 找一个参考 entry 来复制参数（fade 等）
        reference_entry = target_list[0] if len(target_list) > 0 else None
        new_entry = self._make_motion_entry(file_name, reference_entry, new_motion_name)

        if self.right_selected_index is None:
            # 选中的是组标题：追加到末尾
            target_list.append(new_entry)
            self.message_box.clear()
            self.message_box.append(f"已添加动作到组 {self.group_display_titles.get(group_key, group_key)}：{file_name}")
        else:
            # 选中的是具体动作：插入到该动作之后
            insert_pos = self.right_selected_index + 1
            if insert_pos < 0:
                insert_pos = 0
            if insert_pos > len(target_list):
                insert_pos = len(target_list)
            target_list.insert(insert_pos, new_entry)
            self.message_box.clear()
            self.message_box.append(
                f"已在组 {self.group_display_titles.get(group_key, group_key)} 中添加动作：{file_name}"
            )

        self._write_motion_json()
        self._reload_after_change()

    def on_replace_motion(self):
        if self.all_motion_data is None:
            self.message_box.clear()
            self.message_box.append("动作数据未加载成功，无法替换动作！")
            return
        if self.left_selected_motion_path is None:
            self.message_box.clear()
            self.message_box.append("请先从左侧栏选择一个动作文件！")
            return
        if self.right_selected_group is None or self.right_selected_index is None:
            self.message_box.clear()
            self.message_box.append("请先在右侧选中一个具体动作条目，再进行替换！")
            return

        group_key = self.right_selected_group
        idx = self.right_selected_index
        target_list = self.all_motion_data.get('motions', {}).get(group_key)
        if target_list is None or idx < 0 or idx >= len(target_list):
            self.message_box.clear()
            self.message_box.append("右侧选中动作无效，无法替换！")
            return

        file_name = os.path.basename(self.left_selected_motion_path)
        target_list[idx]['file'] = file_name
        self._write_motion_json()
        self.message_box.clear()
        self.message_box.append(
            f"已替换组 {self.group_display_titles.get(group_key, group_key)} 的第 {idx + 1} 个动作为：{file_name}"
        )
        self._reload_after_change()

    def on_delete_motion(self):
        if self.all_motion_data is None:
            self.message_box.clear()
            self.message_box.append("动作数据未加载成功，无法删除动作！")
            return
        if self.right_selected_group is None or self.right_selected_index is None:
            self.message_box.clear()
            self.message_box.append("请先在右侧选中一个具体动作条目，再进行删除！")
            return

        group_key = self.right_selected_group
        idx = self.right_selected_index
        target_list = self.all_motion_data.get('motions', {}).get(group_key)
        if target_list is None or idx < 0 or idx >= len(target_list):
            self.message_box.clear()
            self.message_box.append("右侧选中动作无效，无法删除！")
            return

        removed = target_list.pop(idx)
        removed_name = removed.get('file', '') if isinstance(removed, dict) else str(removed)
        self._write_motion_json()

        # 删除后，将右侧选中移动到同组的一个合理位置
        if len(target_list) == 0:
            self.right_selected_index = None
        else:
            self.right_selected_index = min(idx, len(target_list) - 1)

        self.message_box.clear()
        self.message_box.append(
            f"已从组 {self.group_display_titles.get(group_key, group_key)} 删除动作：{removed_name}"
        )
        self._reload_after_change()

    def exit(self):
        self.change_char_queue.put("exit")
        self.close()


if __name__ == "__main__":
    # 设置当前工作目录为脚本所在目录，避免相对路径问题
    os.chdir(os.path.abspath(os.path.dirname(__file__)))

    live2d_player = Live2DModule()
    get_char_attr = character.GetCharacterAttributes()
    motion_queue = Queue()
    change_char_queue=Queue()

    live2d_player.live2D_initialize(get_char_attr.character_class_list)
    live2d_thread=threading.Thread(target=live2d_player.play_live2d,args=(motion_queue,change_char_queue))


    app = QApplication(sys.argv)

    window = ViewerGUI(get_char_attr.character_class_list,motion_queue,change_char_queue)
    font_id = QFontDatabase.addApplicationFont("../font/ft.ttf")  # 设置字体
    font_family = QFontDatabase.applicationFontFamilies(font_id)[0]
    font = QFont(font_family, 12)
    app.setFont(font)
    from PyQt5.QtWidgets import QDesktopWidget  # 设置qt窗口位置，与live2d对齐

    screen_w_mid = int(0.5 * QDesktopWidget().screenGeometry().width())
    screen_h_mid = int(0.5 * QDesktopWidget().screenGeometry().height())
    window.move(screen_w_mid,
                int(screen_h_mid - 0.35 * QDesktopWidget().screenGeometry().height()))  # 因为窗口高度设置的是0.7倍桌面宽

    live2d_thread.start()
    window.show()
    sys.exit(app.exec_())

