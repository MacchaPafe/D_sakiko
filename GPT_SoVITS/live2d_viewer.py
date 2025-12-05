import json
import multiprocessing
import sys,os
import re

from PyQt5.QtCore import Qt

script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

import time
import threading
from queue import Queue

import live2d.v2 as live2d
import pygame
from pygame.locals import DOUBLEBUF, OPENGL
from OpenGL.GL import *
import glob,os
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QTextBrowser, QPushButton, QDesktopWidget, QHBoxLayout, \
    QGridLayout, QApplication, QLabel

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
                    change_char_queue,
                    desktop_w,
                    desktop_h):
        # 不使用 tkinter 获得屏幕分辨率，而改用 PyQt 获得的内容传入参数
        # 因为在 macOS 上，同一线程同时注册 PyQt 和 tkinter 窗口会立刻崩溃

        #print("正在开启Live2D模块")
        # import tkinter as tk    # 获取屏幕分辨率
        # root = tk.Tk()
        # desktop_w,desktop_h=root.winfo_screenwidth(),root.winfo_screenheight()
        # root.destroy()
        win_w_and_h = int(0.7 * desktop_h)  # 根据显示器分辨率定义窗口大小，保证每个人看到的效果相同
        pygame_win_pos_w,pygame_win_pos_h=int(0.5*desktop_w-win_w_and_h),int(0.5*desktop_h-0.5*win_w_and_h)
        #以上设置后，会差出一个恶心的标题栏高度，因此还要加上一个标题栏高度
        
        caption_height = 0
        if os.name == 'nt':
            try:
                import ctypes
                caption_height = (ctypes.windll.user32.GetSystemMetrics(4)
                                +ctypes.windll.user32.GetSystemMetrics(33)
                                +ctypes.windll.user32.GetSystemMetrics(92))    # 标题栏高度+厚度
            except Exception:
                pass
        
        os.environ['SDL_VIDEO_WINDOW_POS'] = f"{pygame_win_pos_w},{pygame_win_pos_h+caption_height}"   #设置窗口位置，与qt窗口对齐
        pygame.init()
        live2d.init()

        display = (win_w_and_h, win_w_and_h)
        pygame.display.set_mode(display, DOUBLEBUF | OPENGL)
        #pygame.display.set_icon(pygame.image.load("../live2d_related/sakiko_icon.png"))
        model = LAppModel()
        model.LoadModelJson(self.PATH_JSON, disable_precision=True)

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
                self.change_character()
                if self.if_sakiko and self.sakiko_state:
                    model.LoadModelJson('../live2d_related/sakiko/live2D_model_costume/3.model.json', disable_precision=True)
                else:
                    model.LoadModelJson(self.PATH_JSON, disable_precision=True)
                model.Resize(win_w_and_h, win_w_and_h)
                model.SetAutoBlinkEnable(True)
                model.SetAutoBreathEnable(True)

                if self.character_list[self.current_character_num].icon_path is not None:
                    pygame.display.set_icon(pygame.image.load(self.character_list[self.current_character_num].icon_path))




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



class ConfirmWindow(QWidget):
    def __init__(self,parent_window,selected_motion,group_motions,selected_group_index):
        super().__init__()
        self.parent_window = parent_window
        self.setWindowTitle("确认替换窗口")
        self.setWindowFlags(Qt.Window)
        self.screen = QDesktopWidget().screenGeometry()
        self.resize(int(0.3 * self.screen.width()), int(0.3 * self.screen.height()))
        self.selected_motion_display = QTextBrowser()
        self.selected_motion_display.setOpenExternalLinks(False)
        self.selected_motion_display.setOpenLinks(False)
        self.selected_motion_display.anchorClicked.connect(self.play_motion)
        self.current_group_mnt_display = QTextBrowser()
        self.current_group_mnt_display.setOpenExternalLinks(False)
        self.current_group_mnt_display.setOpenLinks(False)
        self.current_group_mnt_display.anchorClicked.connect(self.play_motion_group_ver)
        self.btn_confirm=QPushButton("确认")
        self.btn_cancel=QPushButton("关闭窗口")
        self.btn_confirm.clicked.connect(self.on_confirm)
        self.btn_cancel.clicked.connect(self.on_cancel)

        self.selected_motion_title = QLabel("刚选择的动作文件")
        self.current_group_mnt_title = QLabel("选择要替换的动作")

        self.selected_motion_title.setAlignment(Qt.AlignCenter)
        self.current_group_mnt_title.setAlignment(Qt.AlignCenter)

        # 2.1 顶部左右并排的显示区
        top_display_layout = QHBoxLayout()

        # --- 创建左侧垂直布局 (标题 + 文本框) ---
        left_column_layout = QVBoxLayout()
        left_column_layout.addWidget(self.selected_motion_title)
        left_column_layout.addWidget(self.selected_motion_display)

        # --- 创建右侧垂直布局 (标题 + 文本框) ---
        right_column_layout = QVBoxLayout()
        right_column_layout.addWidget(self.current_group_mnt_title)
        right_column_layout.addWidget(self.current_group_mnt_display)

        # --- 将两个垂直布局添加到顶部的水平布局中 ---
        top_display_layout.addLayout(left_column_layout)
        top_display_layout.addLayout(right_column_layout)

        btn_layout=QHBoxLayout()
        btn_layout.addWidget(self.btn_confirm)
        btn_layout.addWidget(self.btn_cancel)
        main_layout=QVBoxLayout()
        main_layout.addLayout(top_display_layout)
        main_layout.addLayout(btn_layout)
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
        self._group_motions=group_motions
        self.group_motions={}
        for i,motion in enumerate(self._group_motions):
            self.group_motions[i+1]=motion
        self.user_selection_key=None
        self.selected_group_index=selected_group_index  #用户选择的动作组编号，仅作为传回主窗口的confirm_window_confirmed函数的一个入参
        self.fill_2_displays(selected_motion)


    def fill_2_displays(self,selected_motion):
        self.selected_motion_display.append(f'<a href="{selected_motion}" style="text-decoration: none; color: #7799CC;">{os.path.basename(selected_motion)}</a>'+'\n')

        for key,motion in self.group_motions.items():
            self.current_group_mnt_display.append(f'<a href="{motion}[{key}]" style="text-decoration: none; color: #7799CC;">★{key}：{os.path.basename(motion)}</a>'+'\n')

    def play_motion(self,motion_path):
        self.parent_window.motion_queue.put(motion_path.toString())

    def play_motion_group_ver(self,key_and_motion_path):
        key_and_motion_path=key_and_motion_path.toString()
        match = re.match(r"(.+?)\[(.+?)\]$", key_and_motion_path)
        if match:
            motion_path = match.group(1)
            key = int(match.group(2))
            self.user_selection_key=key #用户选择的要替换的动作组内动作编号
            self.parent_window.motion_queue.put(motion_path)
            self.current_group_mnt_display.clear()
            for _key,motion in self.group_motions.items():
                if _key!=key:
                    self.current_group_mnt_display.append(f'<a href="{motion}[{_key}]" style="text-decoration: none; color: #7799CC;">★{_key}：{os.path.basename(motion)}</a>'+'\n')
                else:
                    self.current_group_mnt_display.append(f'<a href="{motion}[{_key}]" style="text-decoration: none; color: #ED784A;">★{_key}：{os.path.basename(motion)}</a>'+'\n')




    def on_confirm(self):
        if self.user_selection_key is None:
            self.setWindowTitle("请选择要替换的动作！")
            return
        self.parent_window.confirm_window_confirmed(self.selected_group_index,self.user_selection_key)
        self.close()  # 关闭子窗口


    def on_cancel(self):
        self.close()  # 关闭子窗口


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
        self.exit_edit=QPushButton("关闭程序")
        self.exit_edit.clicked.connect(self.exit)
        special_btn_layout=QHBoxLayout()
        special_btn_layout.addWidget(self.btn_change_char)
        special_btn_layout.addWidget(self.exit_edit)
        self.btn_add_mtn_to_group1=QPushButton("替换动作到动作组1（高兴）")
        self.btn_add_mtn_to_group2=QPushButton("替换动作到动作组2（伤心）")
        self.btn_add_mtn_to_group3 = QPushButton("替换动作到动作组3（生气）")
        self.btn_add_mtn_to_group4 = QPushButton("替换动作到动作组4（反感）")
        self.btn_add_mtn_to_group5 = QPushButton("替换动作到动作组5（喜欢）")
        self.btn_add_mtn_to_group6 = QPushButton("替换动作到动作组6（惊讶）")
        self.btn_add_mtn_to_group7 = QPushButton("替换动作到动作组7（害怕）")
        self.btn_add_mtn_to_group8=QPushButton("替换动作到动作组8（待机时随机）")
        self.btn_add_mtn_to_group9=QPushButton("替换动作到动作组9（思考时）")
        self.btn_add_mtn_to_group10=QPushButton("替换动作到动作组10（退出程序）")
        self.btn_add_mtn_to_group11=QPushButton("替换动作到动作组11（登场动作）")
        self.btn_add_mtn_to_group12=QPushButton("替换动作到动作组12（待机）")
        self.btn_add_mtn_to_group13 = QPushButton("替换动作到动作组13（按下语音按钮）")

        self.btn_add_mtn_to_group1.clicked.connect(lambda: self.btn_add_mtn_to_group_clicked(1))
        self.btn_add_mtn_to_group2.clicked.connect(lambda: self.btn_add_mtn_to_group_clicked(2))
        self.btn_add_mtn_to_group3.clicked.connect(lambda: self.btn_add_mtn_to_group_clicked(3))
        self.btn_add_mtn_to_group4.clicked.connect(lambda: self.btn_add_mtn_to_group_clicked(4))
        self.btn_add_mtn_to_group5.clicked.connect(lambda: self.btn_add_mtn_to_group_clicked(5))
        self.btn_add_mtn_to_group6.clicked.connect(lambda: self.btn_add_mtn_to_group_clicked(6))
        self.btn_add_mtn_to_group7.clicked.connect(lambda: self.btn_add_mtn_to_group_clicked(7))
        self.btn_add_mtn_to_group8.clicked.connect(lambda: self.btn_add_mtn_to_group_clicked(8))
        self.btn_add_mtn_to_group9.clicked.connect(lambda: self.btn_add_mtn_to_group_clicked(9))
        self.btn_add_mtn_to_group10.clicked.connect(lambda: self.btn_add_mtn_to_group_clicked(10))
        self.btn_add_mtn_to_group11.clicked.connect(lambda: self.btn_add_mtn_to_group_clicked(11))
        self.btn_add_mtn_to_group12.clicked.connect(lambda: self.btn_add_mtn_to_group_clicked(12))
        self.btn_add_mtn_to_group13.clicked.connect(lambda: self.btn_add_mtn_to_group_clicked(13))

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

        # 2.2 中部的情绪按钮网格 (推荐 3x3 布局)
        button_grid_layout = QGridLayout()

        # (行, 列)
        # 第 1 行
        button_grid_layout.addWidget(self.btn_add_mtn_to_group1, 0, 0)  # 高兴
        button_grid_layout.addWidget(self.btn_add_mtn_to_group2, 0, 1)  # 伤心
        button_grid_layout.addWidget(self.btn_add_mtn_to_group3, 0, 2)  # 生气

        # 第 2 行
        button_grid_layout.addWidget(self.btn_add_mtn_to_group4, 1, 0)  # 反感
        button_grid_layout.addWidget(self.btn_add_mtn_to_group5, 1, 1)  # 喜欢
        button_grid_layout.addWidget(self.btn_add_mtn_to_group6, 1, 2)  # 惊讶

        # 第 3 行
        button_grid_layout.addWidget(self.btn_add_mtn_to_group7, 2, 0)  # 害怕
        button_grid_layout.addWidget(self.btn_add_mtn_to_group8, 2, 1)  # 待机时随机做的动作
        button_grid_layout.addWidget(self.btn_add_mtn_to_group9, 2, 2)  # 思考时
        # 第 4 行
        button_grid_layout.addWidget(self.btn_add_mtn_to_group10, 3, 0)  # 退出程序
        button_grid_layout.addWidget(self.btn_add_mtn_to_group11, 3, 1)  # 登场动作
        button_grid_layout.addWidget(self.btn_add_mtn_to_group12, 3, 2)  # 待机
        # 第 5 行
        button_grid_layout.addWidget(self.btn_add_mtn_to_group13, 4, 1)  # 按下语音按钮

        # --- 3. 将所有子布局添加到主布局 ---
        main_layout.addLayout(top_display_layout, stretch=5)  # stretch=5 让显示区占据更多垂直空间
        main_layout.addWidget(self.message_box, stretch=1)  # stretch=1 给日志区一点空间
        main_layout.addLayout(button_grid_layout, stretch=1)  # stretch=1 分配较少空间给按钮
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
        self.current_char_folder_path = ""  # 存储当前角色的动作文件夹路径
        self.load_mtns()
        self.message_box.append("当前角色："+self.character_list[self.current_char_index].character_name)
        self.confirm_window=None
        self.user_selection=None


    def load_mtns(self):
        if self.character_list[self.current_char_index].character_name == '祥子':
            self.current_mnt_display.append("祥子暂时不能编辑动作组")
            self.btn_add_mtn_to_group1.setEnabled(False)
            self.btn_add_mtn_to_group2.setEnabled(False)
            self.btn_add_mtn_to_group3.setEnabled(False)
            self.btn_add_mtn_to_group4.setEnabled(False)
            self.btn_add_mtn_to_group5.setEnabled(False)
            self.btn_add_mtn_to_group6.setEnabled(False)
            self.btn_add_mtn_to_group7.setEnabled(False)
            self.btn_add_mtn_to_group8.setEnabled(False)
            self.btn_add_mtn_to_group9.setEnabled(False)
            self.btn_add_mtn_to_group10.setEnabled(False)
            self.btn_add_mtn_to_group11.setEnabled(False)
            self.btn_add_mtn_to_group12.setEnabled(False)
            self.btn_add_mtn_to_group13.setEnabled(False)
        else:
            self.current_char_folder_path = f"../live2d_related/{self.character_list[self.current_char_index].character_folder_name}/live2D_model"
            with open(os.path.join(self.current_char_folder_path, '3.model.json'),'r',encoding='utf-8') as f:
                self.all_motion_data=json.load(f)

            current_mnt_display_titles=["1.开心：","2.伤心：","3.生气：","4.反感：","5.喜欢：","6.惊讶：","7.害怕：",'8.待机时随机做的动作：','9.思考时：','10.退出程序：','11.登场动作：','12.待机','13.按下语音按钮']
            current_mnt_display_titles_pointer=0
            in_group_counter=1
            for idx,motion in enumerate(self.all_motion_data['motions']['rana']):
                if idx in [0,6,12,18,24,30,36,42,51,54,56,59,60]:
                    self.current_mnt_display.append(f'<a style="color: #FFB099;">{current_mnt_display_titles[current_mnt_display_titles_pointer]}</a>'+'\n')
                    current_mnt_display_titles_pointer+=1
                    in_group_counter=1
                abs_path=os.path.abspath(f"../live2d_related/{self.character_list[self.current_char_index].character_folder_name}/live2D_model/{motion['file']}").replace('\\','/')
                self.current_mnt_display.append(f'<a href="{abs_path}" style="text-decoration: none; color: #7799CC;">★{in_group_counter}：{motion["file"]}</a>'+'\n')
                in_group_counter+=1

        folder_path=f"../live2d_related/{self.character_list[self.current_char_index].character_folder_name}/live2D_model"
        idx=1

        for filename in os.listdir(folder_path):
            full_path = os.path.join(folder_path, filename)
            if os.path.isfile(full_path) and filename.lower().endswith('.mtn'):
                full_path=full_path.replace('\\','/')
                self.all_mnt_display.append(f'<a href="{full_path}" style="text-decoration: none; color: #7799CC;">{idx}. {filename}</a>'+'\n')
                idx+=1




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
        self.load_mtns()
        self.message_box.append("当前角色：" + self.character_list[self.current_char_index].character_name)
        self.change_char_queue.put("1")




    def play_motion_cur_mtn_ver(self,motion_path):

        motion_path=motion_path.toString()
        self.message_box.clear()
        self.message_box.append(f"当前选中动作，右边栏的：\n{os.path.basename(motion_path)}")
        self.btn_add_mtn_to_group1.setEnabled(False)
        self.btn_add_mtn_to_group2.setEnabled(False)
        self.btn_add_mtn_to_group3.setEnabled(False)
        self.btn_add_mtn_to_group4.setEnabled(False)
        self.btn_add_mtn_to_group5.setEnabled(False)
        self.btn_add_mtn_to_group6.setEnabled(False)
        self.btn_add_mtn_to_group7.setEnabled(False)
        self.btn_add_mtn_to_group8.setEnabled(False)
        self.btn_add_mtn_to_group9.setEnabled(False)
        self.btn_add_mtn_to_group10.setEnabled(False)
        self.btn_add_mtn_to_group11.setEnabled(False)
        self.btn_add_mtn_to_group12.setEnabled(False)
        self.btn_add_mtn_to_group13.setEnabled(False)

        self.motion_queue.put(motion_path)
        self.user_selection = motion_path


    def play_motion_all_mtn_ver(self,motion_path):

        motion_path=motion_path.toString()
        self.message_box.clear()
        self.message_box.append(f"当前选中动作，左边栏的：\n{os.path.basename(motion_path)}")
        if self.character_list[self.current_char_index].character_name != '祥子':
            self.btn_add_mtn_to_group1.setEnabled(True)
            self.btn_add_mtn_to_group2.setEnabled(True)
            self.btn_add_mtn_to_group3.setEnabled(True)
            self.btn_add_mtn_to_group4.setEnabled(True)
            self.btn_add_mtn_to_group5.setEnabled(True)
            self.btn_add_mtn_to_group6.setEnabled(True)
            self.btn_add_mtn_to_group7.setEnabled(True)
            self.btn_add_mtn_to_group8.setEnabled(True)
            self.btn_add_mtn_to_group9.setEnabled(True)
            self.btn_add_mtn_to_group10.setEnabled(True)
            self.btn_add_mtn_to_group11.setEnabled(True)
            self.btn_add_mtn_to_group12.setEnabled(True)
            self.btn_add_mtn_to_group13.setEnabled(True)
        else:
            self.btn_add_mtn_to_group1.setEnabled(False)
            self.btn_add_mtn_to_group2.setEnabled(False)
            self.btn_add_mtn_to_group3.setEnabled(False)
            self.btn_add_mtn_to_group4.setEnabled(False)
            self.btn_add_mtn_to_group5.setEnabled(False)
            self.btn_add_mtn_to_group6.setEnabled(False)
            self.btn_add_mtn_to_group7.setEnabled(False)
            self.btn_add_mtn_to_group8.setEnabled(False)
            self.btn_add_mtn_to_group9.setEnabled(False)
            self.btn_add_mtn_to_group10.setEnabled(False)
            self.btn_add_mtn_to_group11.setEnabled(False)
            self.btn_add_mtn_to_group12.setEnabled(False)
            self.btn_add_mtn_to_group13.setEnabled(False)

        self.user_selection = motion_path

        self.motion_queue.put(motion_path)

    def btn_add_mtn_to_group_clicked(self,group_index):
        mtn_index={1:0,2:6,3:12,4:18,5:24,6:30,7:36,8:42,9:51,10:54,11:56,12:59,13:60,14:61}
        group_motions=self.all_motion_data['motions']['rana'][mtn_index[group_index]:mtn_index[group_index+1]]
        group_motions_abs_path=[os.path.abspath(f"{self.current_char_folder_path}/{item['file']}").replace('\\','/') for item in group_motions]

        if self.user_selection is None:
            self.message_box.clear()
            self.message_box.append("请先从左侧栏选择一个动作文件！")
            return
        self.confirm_window = ConfirmWindow(self,self.user_selection,group_motions_abs_path,group_index)

        self.confirm_window.show()
        self.confirm_window.activateWindow()

    def confirm_window_confirmed(self,group_index,in_group_key):
        mtn_index = {1: 0, 2: 6, 3: 12, 4: 18, 5: 24, 6: 30, 7: 36, 8: 42, 9: 51, 10: 54, 11: 56, 12: 59, 13: 60}
        target_index = mtn_index[group_index] + in_group_key - 1
        self.all_motion_data['motions']['rana'][target_index]['file'] = os.path.basename(self.user_selection)
        with open(os.path.join(self.current_char_folder_path, '3.model.json'), 'w', encoding='utf-8') as f:
            json.dump(self.all_motion_data, f, indent=4, ensure_ascii=False)
        scroll_bar_right = self.current_mnt_display.verticalScrollBar()
        scroll_bar_left= self.all_mnt_display.verticalScrollBar()
        saved_position_right = scroll_bar_right.value()
        saved_position_left=scroll_bar_left.value()
        self.current_mnt_display.clear()
        self.all_mnt_display.clear()
        time.sleep(0.4)
        self.load_mtns()
        scroll_bar_right.setValue(saved_position_right) #恢复之前用户浏览的位置
        scroll_bar_left.setValue(saved_position_left)

    def exit(self):
        self.change_char_queue.put("exit")
        self.close()





if __name__ == "__main__":
    live2d_player = Live2DModule()
    get_char_attr = character.GetCharacterAttributes()
    motion_queue = multiprocessing.Queue()
    change_char_queue=multiprocessing.Queue()

    live2d_player.live2D_initialize(get_char_attr.character_class_list)
    # live2d_thread=threading.Thread(target=live2d_player.play_live2d,args=(motion_queue,change_char_queue))


    app = QApplication(sys.argv)

    window = ViewerGUI(get_char_attr.character_class_list,motion_queue,change_char_queue)
    # 如果出现加载字体问题，则忽略设置字体
    font_id = QFontDatabase.addApplicationFont("../font/ft.ttf")  # 设置字体
    font_family = QFontDatabase.applicationFontFamilies(font_id)
    if font_family:
        font = QFont(font_family[0], 12)
        app.setFont(font)
    from PyQt5.QtWidgets import QDesktopWidget  # 设置qt窗口位置，与live2d对齐

    desktop_w = QDesktopWidget().screenGeometry().width()
    desktop_h = QDesktopWidget().screenGeometry().height()
    
    live2d_thread=multiprocessing.Process(target=live2d_player.play_live2d,args=(motion_queue,change_char_queue, desktop_w, desktop_h))

    screen_w_mid = int(0.5 * desktop_w)
    screen_h_mid = int(0.5 * desktop_h)
    window.move(screen_w_mid,
                int(screen_h_mid - 0.35 * desktop_h))  # 因为窗口高度设置的是0.7倍桌面宽

    live2d_thread.start()
    window.show()
    sys.exit(app.exec_())

