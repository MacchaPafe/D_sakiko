import time
from random import randint
from live2d.utils.lipsync import WavHandler
import live2d.v2 as live2d
import pygame
from pygame.locals import DOUBLEBUF, OPENGL
from OpenGL.GL import *
import glob,os



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

    def live2D_initialize(self):
        self.PATH_JSON=glob.glob(os.path.join("../live2d_related/live2D_model", f"*.model.json"))
        if not self.PATH_JSON:
            raise FileNotFoundError("没有找到Live2D模型json文件(.model.json)")
        self.PATH_JSON=max(self.PATH_JSON,key=os.path.getmtime)
        back_img_png=glob.glob(os.path.join("../live2d_related",f"*.png"))
        back_img_jpg = glob.glob(os.path.join("../live2d_related", f"*.jpg"))
        if not (back_img_png+back_img_jpg):
            raise FileNotFoundError("没有找到背景图片文件(.png/.jpg)，自带的也被删了吗...")
        self.BACK_IMAGE=max((back_img_jpg+back_img_png),key=os.path.getmtime)
        #print("Live2D初始化...OK")


    # 动作播放开始后调用
    def onStartCallback(self):
        self.motion_is_over=False
        #print(f"touched and motion [] is started")

    def onStartCallback_think_motion_version(self):
        self.think_motion_is_over = False
        pygame.display.set_caption("小祥思考中")

    def onStartCallback_emotion_version(self,audio_file_path):
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

    def onFinishCallback_think_motion_version(self):
        self.think_motion_is_over=True


    def play_live2d(self,emotion_queue,audio_file_queue,is_text_generating_queue,char_is_converted_queue):
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
        pygame.display.set_icon(pygame.image.load("../live2d_related/sakiko_icon.png"))
        model = live2d.LAppModel()
        model.LoadModelJson(self.PATH_JSON)
        model.Resize(win_w_and_h, win_w_and_h)
        model.SetAutoBlinkEnable(True)
        model.SetAutoBreathEnable(True)
        model.SetExpression('serious')
        glEnable(GL_TEXTURE_2D)

        #texture_thinking=BackgroundRen.render(pygame.image.load('mygo_wp.jpg').convert_alpha())    #想做背景切换功能，但无论如何都会有bug
        texture = BackgroundRen.render(pygame.image.load(self.BACK_IMAGE).convert_alpha())
        glBindTexture(GL_TEXTURE_2D, texture)
        mtn_group = ['idle', 'start', 'shake','rana']

        mouse_position_x = None
        last_saved_time=time.time()
        #print("Live2D模块已开启")

        if_bye = False
        last_emotion = None
        while self.run:
            for event in pygame.event.get():    #退出程序逻辑
                if event.type == pygame.QUIT:
                    self.run = False
                    break
                elif event.type == pygame.MOUSEBUTTONUP:
                    mouse_position_x, _= event.pos

                else:
                    pass

            if not is_text_generating_queue.empty() and self.think_motion_is_over:  # 思考时
                model.StartMotion(mtn_group[3], randint(36,39), 3, self.onStartCallback_think_motion_version, self.onFinishCallback_think_motion_version)

            if  is_text_generating_queue.empty():
                pygame.display.set_caption("客服小祥") if not self.sakiko_state else pygame.display.set_caption("客服黑祥")

            if self.motion_is_over and not pygame.mixer.music.get_busy():  #恢复idle动作

                model.StartMotion(mtn_group[0], 2, 1, self.onStartCallback)

            '''if self.motion_is_over and pygame.mixer.music.get_busy():
                if last_emotion=='LABEL_0': #happiness
                        model.StartMotion(mtn_group[3], randint(0, 5), 3, self.onStartCallback(), self.onFinishCallback)

                elif last_emotion=='LABEL_1':    #sadness
                        model.StartMotion(mtn_group[3], randint(6, 9), 3, self.onStartCallback(), self.onFinishCallback)

                elif last_emotion=='LABEL_2':    #anger
                        model.StartMotion(mtn_group[3], randint(10, 16), 3, self.onStartCallback(), self.onFinishCallback)

                elif last_emotion=='LABEL_3':    #disgust
                        model.StartMotion(mtn_group[3], randint(17, 18), 3, self.onStartCallback(), self.onFinishCallback)

                elif last_emotion=='LABEL_4':    #like
                        model.StartMotion(mtn_group[3], randint(19, 22), 3, self.onStartCallback(), self.onFinishCallback)

                elif last_emotion=='LABEL_5':    #surprise
                        model.StartMotion(mtn_group[3], randint(23, 26), 3, self.onStartCallback(), self.onFinishCallback)

                elif last_emotion=='LABEL_6':    #fear
                        model.StartMotion(mtn_group[3], randint(27, 28), 3, self.onStartCallback(), self.onFinishCallback)
                else:
                        pass'''


            if (time.time()-last_saved_time)>20:   #待机动作
                if self.live2d_this_turn_motion_complete:
                    model.StartMotion(mtn_group[3], randint(29, 35), 1, self.onStartCallback, self.onFinishCallback)
                last_saved_time=time.time()

            if mouse_position_x != 0:  # 点击画面随机做动作
                model.StartMotion(mtn_group[3], randint(0,35), 2, self.onStartCallback, self.onFinishCallback)
                mouse_position_x = 0
                self.think_motion_is_over=True

            self.live2d_this_turn_motion_complete=not pygame.mixer.music.get_busy()

            if not char_is_converted_queue.empty():
                conv_index=char_is_converted_queue.get()
                if not conv_index:      #切换为白祥
                    model.StartMotion(mtn_group[3], randint(40,42), 2, self.onStartCallback, self.onFinishCallback)
                    model.SetExpression("idle")
                    self.sakiko_state=False
                else:       #切换为黑祥
                    model.StartMotion(mtn_group[3], randint(43, 44), 2, self.onStartCallback, self.onFinishCallback)
                    model.SetExpression("serious")
                    self.sakiko_state=True

            if not emotion_queue.empty():
                emotion = emotion_queue.get()
                if emotion=='bye':
                    if not if_bye:
                        model.StartMotion(mtn_group[3],29,3,self.onStartCallback,self.onFinishCallback)
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



                last_emotion=emotion
                if emotion=='LABEL_0': #happiness
                        model.StartMotion(mtn_group[3], randint(0, 5), 3, lambda :self.onStartCallback_emotion_version(this_turn_audio_file_path), self.onFinishCallback)

                elif emotion=='LABEL_1':    #sadness
                        model.StartMotion(mtn_group[3], randint(6, 9), 3, lambda :self.onStartCallback_emotion_version(this_turn_audio_file_path), self.onFinishCallback)

                elif emotion=='LABEL_2':    #anger

                        model.StartMotion(mtn_group[3],randint(10, 16), 3, lambda :self.onStartCallback_emotion_version(this_turn_audio_file_path), self.onFinishCallback)

                elif emotion=='LABEL_3':    #disgust
                        model.StartMotion(mtn_group[3], randint(17, 18), 3, lambda :self.onStartCallback_emotion_version(this_turn_audio_file_path), self.onFinishCallback)

                elif emotion=='LABEL_4':    #like
                        model.StartMotion(mtn_group[3], randint(19, 22), 3, lambda :self.onStartCallback_emotion_version(this_turn_audio_file_path), self.onFinishCallback)

                elif emotion=='LABEL_5':    #surprise
                        model.StartMotion(mtn_group[3], randint(23, 26), 3, lambda :self.onStartCallback_emotion_version(this_turn_audio_file_path), self.onFinishCallback)

                elif emotion=='LABEL_6':    #fear
                        model.StartMotion(mtn_group[3], randint(27, 28), 3, lambda :self.onStartCallback_emotion_version(this_turn_audio_file_path), self.onFinishCallback)
                else:
                        pass
                self.think_motion_is_over=True  #放在这里就对了。。


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

            model.Draw()
            glUseProgram(0)
            # 4、pygame刷新
            pygame.display.flip()


        live2d.dispose()
        #结束pygame
        pygame.quit()


if __name__=='__main__':        #单独测试live2d
    a=Live2DModule()
    a.live2D_initialize()
    from queue import Queue
    text_queue = Queue()
    emotion_queue = Queue()
    audio_file_path_queue = Queue()
    is_audio_play_complete = Queue()
    is_text_generating_queue = Queue()
    a.play_live2d(emotion_queue,audio_file_path_queue,is_text_generating_queue)

