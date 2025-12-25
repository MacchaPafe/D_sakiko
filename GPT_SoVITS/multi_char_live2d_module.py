import live2d.v2 as live2d
from live2d.utils.lipsync import WavHandler
import pygame
from pygame.locals import DOUBLEBUF, OPENGL
from OpenGL.GL import *
import glob, os,time
from random import randint
import sys


def _load_cjk_font(size: int, bold: bool = False) -> pygame.font.Font:
    """尽量加载支持中日韩(CJK)字符的字体。

    优先：项目内置 font/msyh.ttc
    其次：在系统字体里按候选列表匹配（Windows/macOS/Linux）

    说明：macOS 上原先写死的 "Microsoft YaHei" 往往不存在，
    pygame 会回退到不含中文的默认字体，从而显示为“方块”。
    """

    if not pygame.font.get_init():
        pygame.font.init()

    # 1) 项目内置字体（相对仓库根目录）
    # script_dir = os.path.dirname(os.path.abspath(__file__))
    # project_root = os.path.abspath(os.path.join(script_dir, ".."))
    # bundled_font_path = os.path.join(project_root, "font", "msyh.ttc")
    # if os.path.exists(bundled_font_path):
    #     font = pygame.font.Font(bundled_font_path, size)
    #     font.set_bold(bold)
    #     return font

    # 2) 系统字体候选（尽量覆盖 Win/macOS/Linux）
    candidates = [
        # Windows
        "Microsoft YaHei",
        "Microsoft YaHei UI",
        "SimHei",
        "SimSun",
        "MS Gothic",
        # macOS
        "PingFang SC",
        "Hiragino Sans GB",
        "Heiti SC",
        "STHeiti",
        # 常见跨平台/发行版字体
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "WenQuanYi Micro Hei",
        "Arial Unicode MS",
    ]

    for name in candidates:
        try:
            matched_path = pygame.font.match_font(name)
        except Exception:
            matched_path = None
        if matched_path:
            font = pygame.font.Font(matched_path, size)
            font.set_bold(bold)
            return font

    # 3) 最后兜底：默认字体（可能不含中文，但避免崩溃）
    font = pygame.font.Font(None, size)
    font.set_bold(bold)
    return font

from live2d_module import LAppModel


class TextOverlay:
    def __init__(self, window_size,char_names):
        self.win_w, self.win_h = window_size
        self.texture_id = glGenTextures(1)

        # === 1. 动态计算画布尺寸 (基于窗口大小) ===
        self.surface_w = int(self.win_w * 0.8)  # 宽度占窗口 90%
        self.surface_h = int(self.win_h * 0.15)  # 高度占窗口 35%

        self.rect_surface = pygame.Surface((self.surface_w, self.surface_h), pygame.SRCALPHA)

        # === 2. 动态计算字体大小 (基于画布高度) ===
        # 设定字号为画布高度的 14% (主文本) 和 12% (名字)
        # max(12, ...) 是为了防止窗口太小时字号变为 0 或太小看不清
        self.font_size_main = max(12, int(self.surface_h * 0.155))
        self.font_size_name = max(10, int(self.surface_h * 0.15))

        # macOS 下常见问题：找不到“微软雅黑”导致回退字体不含中文，显示成方块
        self.main_font = _load_cjk_font(self.font_size_main, bold=True)
        self.name_font = _load_cjk_font(self.font_size_name, bold=True)

        # === 3. 动态计算 UI 元素的尺寸参数 ===
        # 名字框高度：占画布高度的 22%
        self.ui_name_box_h = int(self.surface_h * 0.22)
        # 名字框圆角
        self.ui_name_radius = self.ui_name_box_h // 2

        # 主框圆角
        self.ui_main_radius = int(self.surface_h * 0.15)
        # 边框粗细：至少 2 像素
        self.ui_border_thick = max(3, int(self.surface_h * 0.015))

        # 文本边距
        self.ui_padding_x = int(self.surface_w * 0.02)

        # === 颜色定义 ===
        self.COLOR_WHITE_BG = (255, 255, 255, 220)
        self.COLOR_BLACK_BORDER = (0, 0, 0, 48)
        self.COLOR_PINK_BG = (255, 59, 113, 255)
        self.COLOR_TEXT_MAIN = (60, 60, 60)
        self.COLOR_TEXT_NAME = (255, 255, 255)

        # === OpenGL 坐标 ===
        bottom_y = -0.95
        # 动态计算上边界：根据 surface_h 在 win_h 中的占比推算
        # OpenGL 坐标系高度是 2.0 (-1 到 1)
        top_y = bottom_y + (self.surface_h / self.win_h) * 2 * 0.95

        self.quad_vertices = [
            -0.8, top_y, 0.0,
            0.8, top_y, 0.0,
            0.8, bottom_y, 0.0,
            -0.8, bottom_y, 0.0
        ]

        # === 【新增】打字机效果所需的状态变量 ===
        self.full_text = ""  # 完整的目标文本
        self.current_text = ""  # 当前屏幕上显示的文本
        self.char_pointer = 0.0  # 当前显示到的字符索引 (用浮点数以支持非整数速度)
        self.typing_speed = 2  # 打字速度：每帧显示的字符数
        self.current_char_name = ""  # 当前角色名 (用于重绘)
        self.is_typing = False  # 是否正在打字中

        try:
            raw_star = pygame.image.load('./star.png').convert_alpha()
            # 动态缩放：让星星的大小等于主字体的大小 (或者稍微大一点，比如 1.2倍)
            star_size = int(self.font_size_main * 1.0)
            self.star_img = pygame.transform.smoothscale(raw_star, (star_size, star_size))
        except Exception as e:
            print(f"星星图标加载失败: {e}")
            self.star_img = None  # 标记为 None，防止后面报错

        self.set_text(f"{char_names[0]} & {char_names[1]}", "...")

    def set_text(self, char_name, text):
        """
        【功能变更】这里只负责设置目标，不再直接渲染
        当外部队列传来新的一句话时调用此方法
        """
        # 如果文本没变，就不重置（防止重复调用导致闪烁）
        if text == self.full_text and char_name == self.current_char_name:
            return

        self.current_char_name = char_name
        self.full_text = text
        self.char_pointer = 0.0  # 重置指针
        self.current_text = ""  # 清空当前显示
        self.is_typing = True  # 开始打字状态

        # 立即渲染第一帧（空的或者只有名字），避免黑屏
        self._render_texture()

    def update(self):
        """
        【新增】需要在 Pygame 主循环中每帧调用
        负责计算当前应该显示多少个字
        """
        if not self.is_typing:
            return

        # 1. 增加指针
        if self.char_pointer < len(self.full_text):
            self.char_pointer += self.typing_speed

            # 2. 截取当前应该显示的文本
            # int() 向下取整，保证索引是整数
            display_count = int(self.char_pointer)

            # 防越界处理
            if display_count > len(self.full_text):
                display_count = len(self.full_text)

            new_slice = self.full_text[:display_count]

            # 3. 只有当显示的文字真的变多了，才重新生成纹理 (节省性能)
            if new_slice != self.current_text:
                self.current_text = new_slice
                self._render_texture()  # 调用绘图逻辑

            # 检查是否打完了
            if display_count >= len(self.full_text):
                self.is_typing = False
        else:
            self.is_typing = False

    def _render_texture(self):
        char_name = self.current_char_name
        text = self.current_text  # <--- 关键：画的是截取后的部分文本
        self.rect_surface.fill((0, 0, 0, 0))

        # === 使用 __init__ 里计算好的动态参数 ===

        # 1. 布局定位
        # 主框向下偏移，给名字框留出位置（正好等于名字框高度）
        main_box_offset_y = self.ui_name_box_h

        main_rect = pygame.Rect(
            self.ui_border_thick // 2,
            main_box_offset_y,
            self.surface_w - self.ui_border_thick,
            self.surface_h - main_box_offset_y - self.ui_border_thick // 2
        )

        # 2. 绘制主对话框
        pygame.draw.rect(self.rect_surface, self.COLOR_WHITE_BG, main_rect, border_radius=self.ui_main_radius)
        pygame.draw.rect(self.rect_surface, self.COLOR_BLACK_BORDER, main_rect, width=self.ui_border_thick,
                         border_radius=self.ui_main_radius)

        # 3. 绘制名字框
        name_w, _ = self.name_font.size(char_name)
        # 名字框宽度 = 文字宽 + 左右各 0.8倍字号的 padding
        name_box_width = int(self.surface_w * 0.15)
        # 位置：紧贴主框上沿 (main_box_offset_y - self.ui_name_box_h = 0)
        # x坐标：左边距
        # name_rect_x = int(self.surface_w * 0.05)
        name_rect = pygame.Rect(0, 0, name_box_width, self.ui_name_box_h)
        if char_name:
            pygame.draw.rect(self.rect_surface, self.COLOR_PINK_BG, name_rect, border_radius=self.ui_name_radius)
            # 名字框的白描边
            pygame.draw.rect(self.rect_surface, (255, 255, 255, 255), name_rect,
                             width=max(2, self.ui_border_thick // 2), border_radius=self.ui_name_radius)

            name_txt_img = self.name_font.render(char_name, True, self.COLOR_TEXT_NAME)
            name_txt_offset_x = name_rect.center[0]-name_box_width/2+self.font_size_name*len(char_name)/2
            name_txt_rect = name_txt_img.get_rect(center=name_rect.center)
            self.rect_surface.blit(name_txt_img, name_txt_rect)

        # 4. 绘制文本
        # 文本起始Y：主框偏移 + 名字框高度的一半作为 Padding
        text_start_y = main_box_offset_y + int(self.ui_name_box_h * 0.75)

        max_text_width = self.surface_w - (self.ui_padding_x * 2)
        lines = self.wrap_text(text, self.main_font, max_text_width)

        y_offset = text_start_y
        # 动态行距：字号的 1.5 倍
        line_spacing = int(self.font_size_main * 1.5)

        for line in lines:
            txt_img = self.main_font.render(line, True, self.COLOR_TEXT_MAIN)
            self.rect_surface.blit(txt_img, (self.ui_padding_x, y_offset))
            y_offset += line_spacing

        if self.star_img is not None:
            star_x = name_rect.center[0] + name_box_width / 2 - 2 * self.ui_name_radius
            star_y=name_rect.center[1]-self.ui_name_box_h/4
            #self.rect_surface.blit(self.star_img, (star_x, star_y))

        # 5. 生成纹理 (保持不变)
        texture_data = pygame.image.tostring(self.rect_surface, "RGBA", True)
        width, height = self.rect_surface.get_size()
        glBindTexture(GL_TEXTURE_2D, self.texture_id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, texture_data)
        glBindTexture(GL_TEXTURE_2D, 0)


    def draw(self):
        # 【关键修改2】保存之前的状态 (存档)
        # 这会保存深度测试、混合模式、剔除模式等所有状态
        glPushAttrib(GL_ALL_ATTRIB_BITS)

        # 【关键修改3】强制设置 UI 绘制所需的状态
        glUseProgram(0)  # 必加：切回固定管线，禁用Live2D shader
        # glBindBuffer(GL_ARRAY_BUFFER, 0)  # 解绑顶点缓冲区 !!!关键!!!
        # glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0)  # 解绑索引缓冲区 !!!关键!!!
        # glBindVertexArray(0)  # 解绑 VAO (如果有)
        # glDisable(GL_DEPTH_TEST)  # 必加：关闭深度测试，让文字直接画在屏幕最前面
        # glDisable(GL_CULL_FACE)  # 必加：关闭面剔除，防止画反了看不见
        # glDisable(GL_LIGHTING)  # 关闭光照（如果有的话）
        # glMatrixMode(GL_PROJECTION)  # 切换到投影矩阵
        # glLoadIdentity()  # 重置为单位矩阵 (默认视口 -1 到 1)
        # glMatrixMode(GL_MODELVIEW)  # 切换到模型视图矩阵
        # glLoadIdentity()  # 重置
        # for i in range(5):
        #     glDisableVertexAttribArray(i)

            # 确保只使用 0号纹理单元
        glActiveTexture(GL_TEXTURE0)
        #

        glEnable(GL_BLEND)  # 开启混合
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, self.texture_id)

        # 重置绘制颜色为纯白，防止之前 Live2D 的颜色残留影响贴图颜色
        glColor4f(1, 1, 1, 1)

        glBegin(GL_QUADS)
        # 纹理坐标 (0,0) 通常对应左下，(0,1) 对应左上
        # 如果 tostring(..., True) 翻转了，这里的映射应该是直观的
        glTexCoord2f(0, 1);
        glVertex3f(self.quad_vertices[0], self.quad_vertices[1], self.quad_vertices[2])  # 左上
        glTexCoord2f(1, 1);
        glVertex3f(self.quad_vertices[3], self.quad_vertices[4], self.quad_vertices[5])  # 右上
        glTexCoord2f(1, 0);
        glVertex3f(self.quad_vertices[6], self.quad_vertices[7], self.quad_vertices[8])  # 右下
        glTexCoord2f(0, 0);
        glVertex3f(self.quad_vertices[9], self.quad_vertices[10], self.quad_vertices[11])  # 左下
        glEnd()

        # 【关键修改4】恢复之前的状态 (读档)
        glPopAttrib()

    def wrap_text(self, text, font,max_width):
        # ... (保持原来的换行代码不变) ...
        words = list(text)
        lines = []
        current_line = []
        for word in words:
            if word=='\n':
                lines.append(''.join(current_line))
                current_line = []
                continue
            test_line = ''.join(current_line + [word])
            w, h = font.size(test_line)
            if w < max_width:
                current_line.append(word)
            else:
                lines.append(''.join(current_line))
                current_line = [word]
        lines.append(''.join(current_line))
        return lines


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
    def blit(*args: tuple[tuple[3], tuple[3], tuple[3], tuple[3]]) -> None:
        glBegin(GL_QUADS)
        glTexCoord2f(0, 0);
        glVertex3f(*args[3])
        glTexCoord2f(1, 0);
        glVertex3f(*args[1])
        glTexCoord2f(1, 1);
        glVertex3f(*args[2])
        glTexCoord2f(0, 1);
        glVertex3f(*args[0])
        glEnd()


idle_recover_timer = time.time()


class Live2DModule:
    def __init__(self,characters):
        self.DELAY_BETWEEN_TURNS = 0.2
        self.PATH_JSON_0 = None
        self.PATH_JSON_1 = None
        self.BACK_IMAGE = None
        self.BACKGROUND_POSITION = ((-1.0, 1.0, 0), (1.0, -1.0, 0), (1.0, 1.0, 0), (-1.0, -1.0, 0))
        self.run = True
        # self.sakiko_state = True
        # self.if_sakiko = False
        # self.if_mask = True
        self.character_list = []
        self.current_character_num = [0,1]
        self.live2D_initialize(characters)
        self.model_pos_offset=[(-0.4,0),(0.4,0)]  #两个模型的位置偏移设置
        self.wavHandler = WavHandler()
        self.lipSyncN: float = 1.4
        self.motion_is_over=True

        self.playlist=[]
        self.playlist_pointer=0



    def live2D_initialize(self, characters):
        if len(characters)<2:
            raise ValueError("至少需要两个角色...")
        self.character_list = characters
        self.PATH_JSON_0 = self.character_list[self.current_character_num[0]].live2d_json
        self.PATH_JSON_1 = self.character_list[self.current_character_num[1]].live2d_json
        # if self.character_list[self.current_character_num].character_name == '祥子':
        #     self.if_sakiko = True
        # else:
        #     self.if_sakiko = False

        back_img_png = glob.glob(os.path.join("../live2d_related", f"*.png"))
        back_img_jpg = glob.glob(os.path.join("../live2d_related", f"*.jpg"))
        if not (back_img_png + back_img_jpg):
            raise FileNotFoundError("没有找到背景图片文件(.png/.jpg)，自带的也被删了吗...")
        self.BACK_IMAGE = max((back_img_jpg + back_img_png), key=os.path.getmtime)
        # print("Live2D初始化...OK")

    def onStartCallback_emotion_version(self,audio_file_path):
        self.motion_is_over=False
        #print(f"touched and motion [] is started")
        # 播放音频

        audio_length=pygame.mixer.Sound(audio_file_path).get_length()
        if audio_length<1.5:
            self.DELAY_BETWEEN_TURNS=1.5
        else:
            self.DELAY_BETWEEN_TURNS=0.5
        pygame.mixer.music.load(audio_file_path)
        pygame.mixer.music.play()
        # 处理口型同步
        if audio_file_path!='../reference_audio/silent_audio/silence.wav':  #该函数无法处理无声音频
            self.wavHandler.Start(audio_file_path)

    def onFinishCallback_motion(self):
        self.motion_is_over=True



    def play_live2d(self,
                    change_char_queue,
                    to_live2d_module_queue,
                    tell_qt_this_turn_finish_queue):
        # print("正在开启Live2D模块")
        # 只在 Windows 下处理高DPI问题，因为 MacOS 下 PyQt 根本就没有模糊问题
        # 还有获得窗口位置的 api 是 Windows 特有的，MacOS 不能用
        if sys.platform == "win32":
            import ctypes

            # 1. 【关键】开启高DPI感知，解决模糊问题
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()

            # 2. 直接用 ctypes 获取真实分辨率，不再需要 tkinter
            user32 = ctypes.windll.user32
            desktop_w = user32.GetSystemMetrics(0)
            desktop_h = user32.GetSystemMetrics(1)
            win_w_and_h = int(0.7 * desktop_h)  # 根据显示器分辨率定义窗口大小，保证每个人看到的效果相同
            pygame_win_pos_w, pygame_win_pos_h = int(0.55 * desktop_w - 1.33*win_w_and_h), int(
                0.5 * desktop_h - 0.5 * win_w_and_h)
            # 以上设置后，会差出一个恶心的标题栏高度，因此还要加上一个标题栏高度
            caption_height = (ctypes.windll.user32.GetSystemMetrics(4)
                              + ctypes.windll.user32.GetSystemMetrics(33)
                              + ctypes.windll.user32.GetSystemMetrics(92))  # 标题栏高度+厚度
            os.environ['SDL_VIDEO_WINDOW_POS'] = f"{pygame_win_pos_w},{pygame_win_pos_h + caption_height}"  # 设置窗口位置，与qt窗口对齐
        else:
            # MacOS / Linux 适配方案
            # 提前初始化 display 模块以获取屏幕信息
            if not pygame.display.get_init():
                pygame.display.init()
            
            info = pygame.display.Info()
            desktop_w = info.current_w
            desktop_h = info.current_h
            
            # 保持与 Windows 相同的窗口大小比例 (屏幕高度的 70%)
            win_w_and_h = int(0.7 * desktop_h)
            
            # 计算窗口位置 (保持相对位置逻辑一致)
            pygame_win_pos_w = int(0.55 * desktop_w - 1.33 * win_w_and_h)
            pygame_win_pos_h = int(0.5 * desktop_h - 0.5 * win_w_and_h)
            
            # 设置窗口位置环境变量
            # MacOS/Linux 不需要像 Windows 那样复杂的标题栏高度补偿，或者难以精确获取，直接使用计算出的位置
            os.environ['SDL_VIDEO_WINDOW_POS'] = f"{pygame_win_pos_w},{pygame_win_pos_h}"

        pygame.init()
        live2d.init()

        display = (int(win_w_and_h*1.33), win_w_and_h)
        pygame.display.set_mode(display, DOUBLEBUF | OPENGL)
        pygame.display.set_icon(pygame.image.load("../live2d_related/sakiko/sakiko_icon.png"))
        pygame.display.set_caption('数字小祥 小剧场')
        model_0 = LAppModel()
        model_0.LoadModelJson(self.PATH_JSON_0, disable_precision=True)
        model_1 = LAppModel()
        model_1.LoadModelJson(self.PATH_JSON_1, disable_precision=True)
        model_0.Resize(int(win_w_and_h*1.33), win_w_and_h)
        model_0.SetAutoBlinkEnable(True)
        model_0.SetAutoBreathEnable(True)
        model_1.Resize(int(win_w_and_h*1.33), win_w_and_h)
        model_1.SetAutoBlinkEnable(True)
        model_1.SetAutoBreathEnable(True)

        model_0.SetOffset(*self.model_pos_offset[0])
        model_1.SetOffset(*self.model_pos_offset[1])
        model_0.SetScale(0.8)
        model_1.SetScale(0.8)
        model_group = [model_0, model_1]

        overlay = TextOverlay((int(win_w_and_h*1.33), win_w_and_h),[self.character_list[self.current_character_num[0]].character_name,self.character_list[self.current_character_num[1]].character_name])
        # if self.if_sakiko:
        #     model.SetExpression('serious')
        glEnable(GL_TEXTURE_2D)

        # texture_thinking=BackgroundRen.render(pygame.image.load('X:\\D_Sakiko2.0\\live2d_related\\costumeBG.png').convert_alpha())    #想做背景切换功能，但无论如何都会有bug
        texture = BackgroundRen.render(pygame.image.load(self.BACK_IMAGE).convert_alpha())
        glBindTexture(GL_TEXTURE_2D, texture)

        global idle_recover_timer

        print("当前Live2D界面渲染硬件", glGetString(GL_RENDERER).decode())
        this_turn_model = None
        last_audio_finish_time = 0

        # 设置缓冲时间 (秒)
        self.DELAY_BETWEEN_TURNS = 0.5  # 建议设为 0.5秒，200ms其实有点太快了，听感上几乎没区别

        while self.run:
            for event in pygame.event.get():  # 退出程序逻辑
                if event.type == pygame.QUIT:
                    self.run = False
                    break
                # if event.type == pygame.MOUSEMOTION:
                #     x,y=pygame.mouse.get_pos()
                #     model_0.Drag(x,y)
                #     model_1.Drag(x,y)
                else:
                    pass

            if not change_char_queue.empty():
                x = change_char_queue.get()
                if x == "EXIT":
                    self.run = False
                    tell_qt_this_turn_finish_queue.put('EXIT')
                    continue
                if x =='change_sakiko_model':
                    sakiko_idx=0
                    for i,index in enumerate(self.current_character_num):
                        if self.character_list[index].character_name=='祥子':
                            sakiko_idx=i
                    this_sakiko_model=model_0 if sakiko_idx == 0 else model_1
                    if "live2D_model_costume" in this_sakiko_model.modelHomeDir:
                        this_sakiko_model.LoadModelJson('../live2d_related/sakiko/live2D_model/3.model.json', disable_precision=True)
                    else:
                        this_sakiko_model.LoadModelJson('../live2d_related/sakiko/live2D_model_costume/3.model.json', disable_precision=True)
                        this_sakiko_model.StartMotion('rana', 45, 3)
                    this_sakiko_model.Resize(int(win_w_and_h*1.33), win_w_and_h)
                    this_sakiko_model.SetAutoBlinkEnable(True)
                    this_sakiko_model.SetAutoBreathEnable(True)
                    continue


                is_change=True
                if type(x[0])==str:
                    current_name_list=[self.character_list[self.current_character_num[0]].character_name,self.character_list[self.current_character_num[1]].character_name]
                    if (x[0] in current_name_list) and (x[1] in current_name_list):
                        is_change=False
                    else:
                        pointer=0
                        for i,char in enumerate(self.character_list):
                            if char.character_name in x:
                                self.current_character_num[pointer]=i
                                pointer+=1
                            if pointer==2:
                                break

                else:
                    if self.playlist_pointer == len(self.playlist):
                        self.current_character_num=x
                # if self.if_sakiko and self.sakiko_state:
                #     model.LoadModelJson('../live2d_related\\sakiko\\live2D_model_costume\\3.model.json')
                # else:
                #     model.LoadModelJson(self.PATH_JSON)
                if self.playlist_pointer != len(self.playlist):
                    is_change=False
                if is_change:
                    model_0.LoadModelJson(self.character_list[self.current_character_num[0]].live2d_json, disable_precision=True)
                    model_1.LoadModelJson(self.character_list[self.current_character_num[1]].live2d_json, disable_precision=True)
                    model_0.Resize(int(win_w_and_h*1.33), win_w_and_h)
                    model_0.SetAutoBlinkEnable(True)
                    model_0.SetAutoBreathEnable(True)
                    model_1.Resize(int(win_w_and_h*1.33), win_w_and_h)
                    model_1.SetAutoBlinkEnable(True)
                    model_1.SetAutoBreathEnable(True)
                    overlay.set_text(f"{self.character_list[self.current_character_num[0]].character_name} & {self.character_list[self.current_character_num[1]].character_name}", "...")

                    # if self.character_list[self.current_character_num].icon_path is not None:
                #     pygame.display.set_icon(
                #         pygame.image.load(self.character_list[self.current_character_num].icon_path))

            if not to_live2d_module_queue.empty():
                if self.playlist_pointer==len(self.playlist):
                    self.playlist=to_live2d_module_queue.get()
                    self.playlist_pointer=0
                else:
                    to_live2d_module_queue.get()



            is_audio_playing = pygame.mixer.music.get_busy()
            # 2. 如果还有对话没播完
            if self.playlist_pointer < len(self.playlist):
                # 情况 A: 音频正在播放
                if is_audio_playing:
                    # 重置时间戳，表示“现在还没结束”
                    last_audio_finish_time = 0
                    # 情况 B: 音频没在播放 (可能是刚开始，也可能是刚播完)
                else:
                    # 获取当前系统时间
                    current_time = time.time()
                    # 如果 last_audio_finish_time 是 0，说明音频是【刚刚】才停的
                    # 或者这是第一句话（还没开始播）
                    if last_audio_finish_time == 0:
                        last_audio_finish_time = current_time
                    # 计算已经等待了多久
                    elapsed_time = current_time - last_audio_finish_time
                    # 只有等待时间超过了设定值，才开始播放下一句
                    # 注意：如果是第一句(pointer=0)，不需要等待，直接播
                    if elapsed_time > self.DELAY_BETWEEN_TURNS or self.playlist_pointer == 0:

                        this_turn_elements = self.playlist[self.playlist_pointer]

                        if this_turn_elements['char_name'] == self.character_list[
                            self.current_character_num[0]].character_name:
                            this_turn_model = model_group[0]
                        else:
                            this_turn_model = model_group[1]

                        if this_turn_elements['live2d_motion_num']=='':
                            num=0
                            if this_turn_elements['emotion'] == 'happiness':
                                num=randint(0, 5)
                            elif this_turn_elements['emotion'] == 'sadness':
                                num=randint(6, 11) if this_turn_elements['char_name']!='祥子' else randint(6, 9)
                            elif this_turn_elements['emotion'] == 'anger':
                                num=randint(12, 17) if this_turn_elements['char_name']!='祥子' else randint(10, 16)
                            elif this_turn_elements['emotion'] == 'disgust':
                                num=randint(18, 23) if this_turn_elements['char_name']!='祥子' else randint(17, 18)
                            elif this_turn_elements['emotion'] == 'like':
                                num=randint(24, 29) if this_turn_elements['char_name']!='祥子' else randint(19, 22)
                            elif this_turn_elements['emotion'] == 'surprise':
                                num=randint(30, 35) if this_turn_elements['char_name']!='祥子' else randint(23, 26)
                            elif this_turn_elements['emotion'] == 'fear':
                                num=randint(36, 41) if this_turn_elements['char_name']!='祥子' else randint(27, 28)
                            else:  # 大模型返回有问题，做待机动作
                                num=randint(43, 50) if this_turn_elements['char_name']!='祥子' else randint(29, 35)
                            this_turn_model.StartMotion('rana', num, 3,lambda *args: self.onStartCallback_emotion_version(this_turn_elements['audio_path']),self.onFinishCallback_motion)
                            this_turn_elements['live2d_motion_num']=num
                        else:
                            this_turn_model.StartMotion('rana', int(this_turn_elements['live2d_motion_num']), 3,lambda *args: self.onStartCallback_emotion_version(this_turn_elements['audio_path']),self.onFinishCallback_motion)

                        tell_qt_this_turn_finish_queue.put(this_turn_elements)
                        overlay.set_text(this_turn_elements['char_name'],
                                       this_turn_elements['text'] + '\n' + this_turn_elements['translation'])
                        self.playlist_pointer += 1
                        last_audio_finish_time = 0




            # 清除缓冲区
            # live2d.clearBuffer()
            glClear(GL_COLOR_BUFFER_BIT)
            # 更新live2d到缓冲区

            model_0.Update(breath_weight_1=0.2,breath_weight_2=0.7)
            model_1.Update(breath_weight_1=0.2,breath_weight_2=0.7)
            # 渲染背景图片
            BackgroundRen.blit(*self.BACKGROUND_POSITION)
            if self.wavHandler.Update():  # 控制说话时的嘴型
                this_turn_model.SetParameterValue("PARAM_MOUTH_OPEN_Y", self.wavHandler.GetRms() * self.lipSyncN)

                idle_recover_timer = time.time()

            model_0.Draw()
            model_1.Draw()

            overlay.update()
            overlay.draw()
            glUseProgram(0)
            # 4、pygame刷新
            pygame.display.flip()

        live2d.dispose()
        # 结束pygame
        pygame.quit()


def run_live2d_process(change_char_queue, to_live2d_module_queue, tell_qt_this_turn_finish_queue):
    """Live2D 子进程入口。

    注意：该函数必须在模块顶层定义，才能在 Windows/macOS 的 spawn 模式下被 pickle。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    import character
    from multi_char_live2d_module import Live2DModule

    get_char_attr = character.GetCharacterAttributes()
    live2d_player = Live2DModule(get_char_attr.character_class_list)
    live2d_player.play_live2d(change_char_queue, to_live2d_module_queue, tell_qt_this_turn_finish_queue)


if __name__ == "__main__":
    import sys
    from queue import Queue
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    import character

    motion_queue = Queue()
    change_char_queue = Queue()
    char_getter = character.GetCharacterAttributes()
    live2d_module = Live2DModule(char_getter.character_class_list)
    live2d_module.play_live2d(motion_queue, change_char_queue,motion_queue)
