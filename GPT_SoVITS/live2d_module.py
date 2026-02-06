import math
import re
import time
from random import random
from live2d.utils.lipsync import WavHandler
import live2d.v2 as live2d
import pygame
from pygame.locals import DOUBLEBUF, OPENGL
from OpenGL.GL import *
import glob,os
import sys
import queue

from multi_char_live2d_module import TextOverlay


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
        self.motion_is_over=False
        self.wavHandler=WavHandler()
        self.lipSyncN:float=1.4
        self.live2d_this_turn_motion_complete=True
        self.think_motion_is_over=True
        self.run=True
        self.sakiko_state=True
        self.if_sakiko=False
        self.if_mask=True
        self.character_list=[]
        self.current_character_num=0
        self.is_display_text=True
        self.new_text=''

    def change_character(self):
        if len(self.character_list)==1:
            self.current_character_num = 0
        else:
            if self.current_character_num<len(self.character_list)-1:
                self.current_character_num+=1
            else:
                self.current_character_num=0
        if os.path.exists(self.character_list[self.current_character_num].live2d_json): #防止切换到一个已删除的模型导致崩溃
            self.PATH_JSON=self.character_list[self.current_character_num].live2d_json
        else:
            default_path=f"../live2d_related/{self.character_list[self.current_character_num].character_folder_name}/live2D_model"
            self.character_list[self.current_character_num].live2d_json=glob.glob(os.path.join(default_path, f"*.model.json"))[0]
            self.PATH_JSON=self.character_list[self.current_character_num].live2d_json

        if self.character_list[self.current_character_num].character_name=='祥子':
            self.if_sakiko=True
        else:
            self.if_sakiko=False

    def live2D_initialize(self,characters):
        # self.PATH_JSON=glob.glob(os.path.join("../live2d_related/anon/live2D_model", f"*.model.json"))
        # if not self.PATH_JSON:
        #     raise FileNotFoundError("没有找到Live2D模型json文件(.model.json)")
        # self.PATH_JSON=max(self.PATH_JSON,key=os.path.getmtime)
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
        self.BACK_IMAGE = back_img_jpg + back_img_png
        self.back_img_index = 0
        #print("Live2D初始化...OK")


    # 动作播放开始后调用
    def onStartCallback(self,*args):
        self.motion_is_over=False
        #print(f"touched and motion [] is started")

    def onStartCallback_think_motion_version(self,*args):
        self.think_motion_is_over = False
        if self.if_sakiko:
            pygame.display.set_caption("小祥思考中")
        else:
            pygame.display.set_caption(f"{self.character_list[self.current_character_num].character_name}思考中")

    def onStartCallback_emotion_version(self,audio_file_path,*args):
        self.motion_is_over=False
        #print(f"touched and motion [] is started")
        # 播放音频
        pygame.mixer.music.load(audio_file_path)
        pygame.mixer.music.play()
        # 处理口型同步
        if audio_file_path!='../reference_audio/silent_audio/silence.wav':  #该函数无法处理无声音频
            self.wavHandler.Start(audio_file_path)

    # 动作播放结束后调用
    def onFinishCallback(self):
        #print("motion finished")
        self.motion_is_over=True
        global idle_recover_timer
        idle_recover_timer = time.time()

    def onFinishCallback_think_motion_version(self):
        self.think_motion_is_over=True



    def play_live2d(self,
                    emotion_queue,
                    audio_file_queue,
                    is_text_generating_queue,
                    char_is_converted_queue,
                    change_char_queue,
                    live2d_text_queue,
                    is_display_text_value,
                    motion_complete_value,
                    desktop_w,
                    desktop_h):
        if self.wavHandler is None:
            self.wavHandler = WavHandler()
        # print("正在开启Live2D模块")
        # import tkinter as tk    # 获取屏幕分辨率
        # root = tk.Tk()
        # desktop_w,desktop_h=root.winfo_screenwidth(),root.winfo_screenheight()
        # root.destroy()
        win_w_and_h = int(0.7 * desktop_h)  # 根据显示器分辨率定义窗口大小，保证每个人看到的效果相同
        pygame_win_pos_w,pygame_win_pos_h=int(0.5*desktop_w-win_w_and_h),int(0.5*desktop_h-0.5*win_w_and_h)
        #以上设置后，会差出一个恶心的标题栏高度，因此还要加上一个标题栏高度

        # macOS 适配性检查
        # 在 macOS 下，live2d-py 库必须是最新的 0.5.4 版本，否则会导致 “disable_precision” 参数无效
        if sys.platform == "darwin":
            # 由于 live2d-py 库没有一个 __version__ 属性判断版本，我们不得不采用一个比较难看的判断方式
            function = live2d.LAppModel.LoadModelJson
            # 在 0.5.4 版本之前，这个函数只有一个参数，而在 0.5.4 版本及之后，新增了一个 disable_precision 参数
            # 如果 disable_precision 参数不存在，说明不是 0.5.4 版本及之后的库
            if "disable_precision" not in function.__code__.co_varnames:
                raise RuntimeError("当前 live2d-py 库版本低于 0.5.4。\nmacOS 用户请确保 live2d-py 库版本为 0.5.4 及以上，否则程序将因为 OpenGL 错误崩溃。请运行 pip install --upgrade live2d-py 来升级该库。对于打包版用户，请向分发者寻求帮助。")

        caption_height = 0
        # 只在 Windows 上使用这些 ctype 方法
        # macOS 窗口标题栏很小，本身就不需要
        # print("正在执行 ctypes 方法以获取标题栏高度...")
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
        pygame.display.set_icon(pygame.image.load("../live2d_related/sakiko/sakiko_icon.png"))
        model = live2d.LAppModel()
        model.LoadModelJson(self.PATH_JSON, disable_precision=True)

        model.Resize(win_w_and_h, win_w_and_h)
        model.SetAutoBlinkEnable(True)
        model.SetAutoBreathEnable(True)
        if self.if_sakiko:
            model.SetExpression('serious')

        overlay=TextOverlay((win_w_and_h, win_w_and_h),[self.character_list[self.current_character_num].character_name])
        glEnable(GL_TEXTURE_2D)

        #texture_thinking=BackgroundRen.render(pygame.image.load('X:/D_Sakiko2.0/live2d_related/costumeBG.png').convert_alpha())    #想做背景切换功能，但无论如何都会有bug
        texture = BackgroundRen.render(pygame.image.load(self.BACK_IMAGE[self.back_img_index]).convert_alpha())
        glBindTexture(GL_TEXTURE_2D, texture)
        mtn_group_mapping={
            "LABEL_0":"happiness",
            "LABEL_1":"sadness",
            "LABEL_2": "anger",
            "LABEL_3": "disgust",
            "LABEL_4": "like",
            "LABEL_5": "surprise",
            "LABEL_6": "fear"
        }

        mouse_position_x = None
        last_saved_time=time.time()     #待机动作计时器
        last_saved_time_think=time.time()
        global idle_recover_timer

        interval_think=1
        if_bye = False
        last_emotion = None
        print("当前Live2D界面渲染硬件", glGetString(GL_RENDERER).decode())

        while self.run:
            for event in pygame.event.get():    #退出程序逻辑
                if event.type == pygame.QUIT:
                    self.run = False
                    break
                elif event.type == pygame.MOUSEBUTTONUP:
                    mouse_position_x, _= event.pos

                else:
                    pass

            # 从队列中获取要显示的新文本（只取最新，避免积压导致延迟）
            latest_text = None
            while True:
                try:
                    latest_text = live2d_text_queue.get_nowait()
                except queue.Empty:
                    break
                except Exception:
                    break
            if latest_text is not None:
                self.new_text = latest_text

            if not change_char_queue.empty():
                x=change_char_queue.get()
                if x =='start_talking':   #录音时
                    model.StartRandomMotion("talking_motion", 4, self.onStartCallback)
                elif x=='stop_talking':   #录音结束
                    self.onFinishCallback()
                elif x=='change_l2d_background':
                    glActiveTexture(GL_TEXTURE0)  # 必加，否则白屏
                    glDeleteTextures([texture])
                    self.back_img_index += 1
                    if self.back_img_index >= len(self.BACK_IMAGE):
                        self.back_img_index = 0
                    texture = BackgroundRen.render(
                        pygame.image.load(self.BACK_IMAGE[self.back_img_index]).convert_alpha())
                    glBindTexture(GL_TEXTURE_2D, texture)
                    BackgroundRen.blit(*self.BACKGROUND_POSITION)
                elif x.startswith('change_l2d_model'):
                    match=re.search(r"change_l2d_model#(.*)", x)
                    if match:
                        new_model_path = match.group(1)
                        #print("正在切换Live2D模型，路径为：", new_model_path)
                        try:
                            new_model=live2d.LAppModel()
                            new_model.LoadModelJson(new_model_path, disable_precision=True)
                            new_model.Resize(win_w_and_h, win_w_and_h)
                            new_model.SetAutoBlinkEnable(True)
                            new_model.SetAutoBreathEnable(True)
                        except Exception as e:
                            print("Live2D模型切换失败，请检查模型组成文件是否齐全以及是否完好。错误信息：", e)
                            new_model=None
                        if new_model is not None:
                            del model
                            model=new_model
                            print("Live2D模型切换成功！")
                            #model.StartRandomMotion("change_character",3,self.onStartCallback,self.onFinishCallback)    #todo:考虑新开一个动作组，换服装时触发
                            self.character_list[self.current_character_num].live2d_json = new_model_path
                        else:
                            del new_model
                else:
                    self.change_character()
                    overlay.set_text(self.character_list[self.current_character_num].character_name,'...')
                    if self.if_sakiko and self.sakiko_state:
                        model.LoadModelJson('../live2d_related/sakiko/live2D_model_costume/3.model.json', disable_precision=True)
                    else:
                        model.LoadModelJson(self.PATH_JSON, disable_precision=True)
                    model.Resize(win_w_and_h, win_w_and_h)
                    model.SetAutoBlinkEnable(True)
                    model.SetAutoBreathEnable(True)
                    if self.if_sakiko and self.sakiko_state:
                        model.SetExpression('serious')
                    else:
                        model.SetExpression('idle')

                    model.StartRandomMotion("change_character",3,self.onStartCallback,self.onFinishCallback)
                    if self.character_list[self.current_character_num].icon_path is not None:
                        pygame.display.set_icon(pygame.image.load(self.character_list[self.current_character_num].icon_path))

            if not is_text_generating_queue.empty() and self.think_motion_is_over:  # 思考时
                if time.time()-last_saved_time_think>interval_think:
                    model.StartRandomMotion("text_generating",3,self.onStartCallback_think_motion_version, self.onFinishCallback_think_motion_version)

                    last_saved_time_think=time.time()
                    interval_think=15

            if  is_text_generating_queue.empty():
                if self.if_sakiko:
                    pygame.display.set_caption("祥子") if not self.sakiko_state else pygame.display.set_caption("Oblivionis")
                else:
                    pygame.display.set_caption(f"{self.character_list[self.current_character_num].character_name}")

            if self.motion_is_over and not pygame.mixer.music.get_busy():  #恢复idle动作
                if is_text_generating_queue.empty() and time.time()-idle_recover_timer>2.5:
                    model.StartRandomMotion("idle_motion", 1, self.onStartCallback)

            if (time.time()-last_saved_time)>25 :   #待机动作
                if self.live2d_this_turn_motion_complete and is_text_generating_queue.empty():
                    model.StartRandomMotion("IDLE",1,self.onStartCallback,self.onFinishCallback)
                last_saved_time=time.time()

            if mouse_position_x != 0:  # 点击画面随机做动作
                if self.if_sakiko:
                    model.StartRandomMotion("IDLE",1,self.onStartCallback,self.onFinishCallback)
                mouse_position_x = 0
                self.think_motion_is_over=True

            self.live2d_this_turn_motion_complete=not pygame.mixer.music.get_busy()
            # 更新到共享变量
            motion_complete_value.value = self.live2d_this_turn_motion_complete

            if not char_is_converted_queue.empty():
                if self.if_sakiko:
                    conv_index=char_is_converted_queue.get()
                    if conv_index!='maskoff':
                        if not conv_index:      #切换为白祥
                            model.LoadModelJson(self.PATH_JSON, disable_precision=True)
                            model.Resize(win_w_and_h, win_w_and_h)
                            model.StartRandomMotion("change_character",2,self.onStartCallback,self.onFinishCallback)
                            model.SetExpression("idle")
                            self.sakiko_state=False

                        else:       #切换为黑祥
                            model.LoadModelJson('../live2d_related/sakiko/live2D_model_costume/3.model.json', disable_precision=True)
                            model.Resize(win_w_and_h, win_w_and_h)

                            self.if_mask=random()<0.5
                            model.StartRandomMotion("change_character" if self.if_mask else "change_character_maskoff",2,self.onStartCallback,self.onFinishCallback)
                            model.SetExpression("serious")
                            self.sakiko_state=True
                    else:
                        if self.sakiko_state:   #黑祥
                            model.StartRandomMotion("change_character_maskoff" if self.if_mask else "maskon",3,self.onStartCallback,self.onFinishCallback)
                            self.if_mask = not self.if_mask
                        else:
                            model.StartMotion("text_generating", 0, 3, self.onStartCallback, self.onFinishCallback)

            if not emotion_queue.empty():
                emotion = emotion_queue.get()
                if emotion=='bye':
                    if not if_bye:
                        model.StartRandomMotion("bye",3,self.onStartCallback,self.onFinishCallback)
                    if_bye=True
                    emotion_queue.put("bye")    #为了动作结束前一直进入该if分支
                    glClear(GL_COLOR_BUFFER_BIT)
                    model.Update()
                    BackgroundRen.blit(*self.BACKGROUND_POSITION)
                    model.Draw()
                    glUseProgram(0)
                    pygame.display.flip()
                    if self.motion_is_over:
                        self.run=False
                    continue

                this_turn_audio_file_path=audio_file_queue.get()
                model.StartRandomMotion(mtn_group_mapping[emotion],3,lambda *args:self.onStartCallback_emotion_version(audio_file_path=this_turn_audio_file_path),self.onFinishCallback)
                self.think_motion_is_over=True  #放在这里就对了。。
                overlay.set_text(self.character_list[self.current_character_num].character_name,self.new_text)  #有感情标签传入，说明角色肯定要说话，此时更新文本


            # 清除缓冲区
            #live2d.clearBuffer()
            glClear(GL_COLOR_BUFFER_BIT)
            # 更新live2d到缓冲区
            model.Update()
            # 渲染背景图片
            BackgroundRen.blit(*self.BACKGROUND_POSITION)
            # 渲染live2d到屏幕
            if self.wavHandler.Update():  # 控制说话时的嘴型
                model.SetParameterValue("PARAM_MOUTH_OPEN_Y", self.wavHandler.GetRms() * self.lipSyncN)

                idle_recover_timer = time.time()

            model.Draw()
            overlay.update()
            # 从共享变量读取是否显示文本
            if is_display_text_value.value:
                overlay.draw()
            glUseProgram(0)
            # 4、pygame刷新
            pygame.display.flip()


        live2d.dispose()
        #结束pygame
        pygame.quit()


if __name__=='__main__':        #单独测试live2d
    import os, sys

    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    os.chdir(script_dir)

    import character

    get_all = character.GetCharacterAttributes()

    characters = get_all.character_class_list
    a=Live2DModule()
    a.live2D_initialize(characters)
    from queue import Queue
    text_queue = Queue()
    emotion_queue = Queue()
    audio_file_path_queue = Queue()
    is_audio_play_complete = Queue()
    is_text_generating_queue = Queue()
    char_is_converted_queue=Queue()
    change_char_queue=Queue()
    a.play_live2d(emotion_queue,audio_file_path_queue,is_text_generating_queue,char_is_converted_queue,change_char_queue)

