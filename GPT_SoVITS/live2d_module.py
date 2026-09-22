from __future__ import annotations

import contextlib
import re
import time
from live2d.utils.lipsync import WavHandler
import glob, os, sys

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# 屏蔽 pygame 相关的警告和介绍信息
with open(os.devnull, 'w') as devnull:
    with contextlib.redirect_stdout(devnull):
        with contextlib.redirect_stderr(devnull):
            import pygame
            from pygame.locals import DOUBLEBUF, OPENGL

from OpenGL.GL import *
import queue

from multi_char_live2d_module import TextOverlay, ModelLoadNoticeOverlay
from qconfig import d_sakiko_config, qconfig
from log import setup_worker_logging, get_logger
from live2d_support.runtime_adapter import (
    Live2DModelAdapter,
    Live2DModelProtocol,
    NullLive2DModel,
)
from live2d_support.motion_semantics import motion_group_for_emotion
from live2d_support.runtime_session import Live2DRuntimeSession
from live2d_support.layout import (
    Live2DLayout,
    format_live2d_layout_status,
    get_live2d_layout,
    reset_live2d_layout,
    save_live2d_layout,
)

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


from runtime.single_character_performance import SingleCharacterPerformance


class Live2DModule(SingleCharacterPerformance):
    def __init__(self):
        self.PATH_JSON=None
        self.BACK_IMAGE=None
        self.BACKGROUND_POSITION=((-1.0, 1.0, 0), (1.0, -1.0, 0), (1.0, 1.0, 0), (-1.0, -1.0, 0))
        super().__init__()
        self.character_list = []
        self.character_by_name = {}
        self.character_by_folder = {}
        self.current_character_name = ''
        self.current_model_json = None

    @property
    def current_character(self):
        """获取当前 Live2D 显示角色对象。"""
        character = self.character_by_name.get(self.current_character_name)
        if character is not None:
            return character
        if self.character_list:
            return self.character_list[0]
        raise ValueError("Live2D 角色列表为空。")

    def _default_model_json_for_character(self, character) -> str | None:
        """获取角色默认模型路径，缺失时尝试从角色目录中恢复。"""
        if character.live2d_json and os.path.exists(character.live2d_json):
            return character.live2d_json
        default_path = f"../live2d_related/{character.character_folder_name}/live2D_model"
        candidates = [
            *glob.glob(os.path.join(default_path, "**", "*.model.json"), recursive=True),
            *glob.glob(os.path.join(default_path, "**", "*.model3.json"), recursive=True),
        ]
        if not candidates:
            character.live2d_json = None
            return None
        character.live2d_json = max(candidates, key=os.path.getmtime)
        return character.live2d_json

    def switch_live2d_target(
            self,
            character_name: str,
            model_json: str | None = None,
            *,
            character_folder_name: str = "",
            use_default: bool = True,
    ) -> str | None:
        """切换当前 Live2D 目标角色和模型路径。"""
        character = (
            self.character_by_folder.get(character_folder_name)
            if character_folder_name
            else None
        )
        if character is None:
            character = self.character_by_name.get(character_name)
        if character is None:
            raise ValueError(f"找不到 Live2D 角色：{character_name}")
        target_model_json = model_json
        if use_default:
            target_model_json = self._default_model_json_for_character(character)
        self.current_character_name = character.character_name
        self.current_model_json = target_model_json
        self.PATH_JSON = target_model_json
        self.if_sakiko = character.character_name == "祥子"
        return target_model_json

    def live2D_initialize(self,characters):
        # self.PATH_JSON=glob.glob(os.path.join("../live2d_related/anon/live2D_model", f"*.model.json"))
        # if not self.PATH_JSON:
        #     raise FileNotFoundError("没有找到Live2D模型json文件(.model.json)")
        # self.PATH_JSON=max(self.PATH_JSON,key=os.path.getmtime)
        self.character_list=characters
        self.character_by_name = {character.character_name: character for character in self.character_list}
        self.character_by_folder = {
            character.character_folder_name: character
            for character in self.character_list
        }
        if self.character_list:
            self.switch_live2d_target(self.character_list[0].character_name)

        back_img_png=glob.glob(os.path.join("../live2d_related",f"*.png"))
        back_img_jpg = glob.glob(os.path.join("../live2d_related", f"*.jpg"))
        if not (back_img_png+back_img_jpg):
            raise FileNotFoundError("没有找到背景图片文件(.png/.jpg)，自带的也被删了吗...")
        self.BACK_IMAGE=back_img_jpg+back_img_png
        self.back_img_index=0
        config_data = d_sakiko_config.background_image_path.value
        if config_data in self.BACK_IMAGE:
            self.back_img_index = self.BACK_IMAGE.index(config_data)


    def save_l2d_json_paths_and_bg(self):
        l2d_json_paths_dict = {}
        for char in self.character_list:
            if char.character_name!="祥子" and char.live2d_json:
                l2d_json_paths_dict[char.character_name] = char.live2d_json
        with d_sakiko_config as cfg:
            cfg.set(cfg.l2d_json_paths_dict, l2d_json_paths_dict)
            cfg.set(cfg.background_image_path, self.BACK_IMAGE[self.back_img_index])

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
                    desktop_h,
                    log_queue, playback_events=None, ready_event=None):
        setup_worker_logging(log_queue)
        logger = get_logger(__name__)

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

        display = (win_w_and_h, win_w_and_h)
        pygame.display.set_mode(display, DOUBLEBUF | OPENGL)
        glViewport(0, 0, *display)
        session = Live2DRuntimeSession()
        model_notice = ModelLoadNoticeOverlay(display, slot_count=1)
        model: Live2DModelProtocol = NullLive2DModel()
        if self.PATH_JSON is not None:
            try:
                loaded_model = session.create_model(self.PATH_JSON)
                model = loaded_model
                loaded_model.Resize(win_w_and_h, win_w_and_h)
                loaded_model.SetAutoBlinkEnable(True)
                loaded_model.SetAutoBreathEnable(True)
                model = loaded_model
            except Exception:
                logger.exception(
                    "Live2D 模型加载失败，将使用无模型展示：%s",
                    self.PATH_JSON,
                )
                model_notice.set_failed_slots({0})
                model.dispose()
                model = NullLive2DModel()

        frame_clock = pygame.time.Clock()
        self.target_fps = 60

        initial_icon_path = self.current_character.icon_path
        if not initial_icon_path or not os.path.exists(initial_icon_path):
            initial_icon_path = "../live2d_related/sakiko/sakiko_icon.png"
        pygame.display.set_icon(pygame.image.load(initial_icon_path))

        if self.if_sakiko and isinstance(model, Live2DModelAdapter):
            model.SetSemanticExpression('serious')

        overlay=TextOverlay((win_w_and_h, win_w_and_h),[self.current_character.character_name])
        self.on_event = playback_events.put if playback_events is not None else lambda event: None
        self.on_subtitle = lambda text: overlay.set_text(self.current_character.character_name, text)
        glEnable(GL_TEXTURE_2D)

        texture = BackgroundRen.render(pygame.image.load(self.BACK_IMAGE[self.back_img_index]).convert_alpha())

        def render_background(texture_id: object) -> None:
            """显式绑定背景纹理并绘制背景图。"""
            glUseProgram(0)
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, texture_id)
            BackgroundRen.blit(*self.BACKGROUND_POSITION)

        def replace_model(
                previous: Live2DModelProtocol,
                model_path: str | None,
        ) -> Live2DModelProtocol:
            """立即清空旧模型并独立加载目标，失败时持续显示错误提示。"""
            previous.dispose()
            model_notice.clear()
            self._reset_eye_open_transition()
            self.wavHandler = WavHandler()
            glClear(GL_COLOR_BUFFER_BIT)
            render_background(texture)
            pygame.display.flip()
            candidate: Live2DModelAdapter | None = None
            try:
                if model_path is None:
                    return NullLive2DModel()
                candidate = session.create_model(model_path)
                candidate.Resize(win_w_and_h, win_w_and_h)
                candidate.SetAutoBlinkEnable(True)
                candidate.SetAutoBreathEnable(True)
                return candidate
            except Exception:
                if candidate is not None:
                    candidate.dispose()
                logger.exception("Live2D 模型切换失败：%s", model_path)
                model_notice.set_failed_slots({0})
                return NullLive2DModel()

        layout_scene = "single"
        current_layout_model_path = self.PATH_JSON
        current_layout = (
            get_live2d_layout(current_layout_model_path, model.version, layout_scene, "desktop")
            if current_layout_model_path is not None and isinstance(model, Live2DModelAdapter)
            else Live2DLayout(scale=1.0, offset_x=0.0, offset_y=0.0)
        )
        layout_editing = False
        layout_dirty = False
        layout_dragging = False
        layout_last_mouse_pos: tuple[int, int] | None = None

        def apply_current_layout() -> None:
            """将当前布局应用到正在显示的 Live2D 模型。"""
            model.SetScale(current_layout.scale)
            model.SetOffset(current_layout.offset_x, current_layout.offset_y)

        def show_layout_edit_overlay() -> None:
            """刷新布局编辑模式下的提示文本。"""
            overlay.set_text(
                self.current_character.character_name,
                "布局编辑中：左键拖动，滚轮缩放，R重置，Esc/Q保存并退出"
            )

        def restore_normal_overlay() -> None:
            """退出布局编辑后恢复普通对话文本。"""
            overlay.set_text(self.current_character.character_name, self.new_text or "...")

        def enter_layout_edit_mode() -> None:
            """进入 Live2D 布局编辑模式。"""
            nonlocal layout_editing, layout_dragging, layout_last_mouse_pos
            if not isinstance(model, Live2DModelAdapter):
                overlay.set_text(
                    self.current_character.character_name,
                    "当前角色未加载 Live2D 模型，无法编辑布局。",
                )
                return
            layout_editing = True
            layout_dragging = False
            layout_last_mouse_pos = None
            pygame.display.set_caption(f"{self.current_character.character_name}布局编辑中")
            show_layout_edit_overlay()

        def exit_layout_edit_mode() -> None:
            """退出 Live2D 布局编辑模式，并在有修改时保存布局。"""
            nonlocal layout_editing, layout_dirty, layout_dragging, layout_last_mouse_pos
            if layout_dirty and current_layout_model_path is not None:
                try:
                    save_live2d_layout(current_layout_model_path, layout_scene, current_layout, "desktop")
                except Exception:
                    logger.exception("保存 Live2D 布局配置失败")
                layout_dirty = False
            layout_editing = False
            layout_dragging = False
            layout_last_mouse_pos = None
            restore_normal_overlay()

        def reset_current_layout() -> None:
            """重置当前模型在普通对话场景下的自定义布局。"""
            nonlocal current_layout, layout_dirty
            if current_layout_model_path is None or not isinstance(model, Live2DModelAdapter):
                return
            try:
                reset_live2d_layout(current_layout_model_path, layout_scene, "desktop")
            except Exception:
                logger.exception("重置 Live2D 布局配置失败")
            current_layout = get_live2d_layout(current_layout_model_path, model.version, layout_scene, "desktop")
            apply_current_layout()
            layout_dirty = False
            show_layout_edit_overlay()

        apply_current_layout()

        interaction_requested = False
        last_saved_time=time.time()     #待机动作计时器
        last_saved_time_think=time.time()

        interval_think=1
        if_bye = False
        last_emotion = None
        logger.info("当前Live2D界面渲染硬件 %s", glGetString(GL_RENDERER).decode())
        if ready_event is not None:
            ready_event.set()

        is_update_mouth_sync = 0
        mouth_keep_open_value:float=0.0
        while self.run:
            for event in pygame.event.get():    #退出程序逻辑
                if event.type == pygame.QUIT:
                    if layout_editing:
                        exit_layout_edit_mode()
                    self.run = False
                    break
                elif event.type == pygame.KEYDOWN and layout_editing:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        exit_layout_edit_mode()
                    elif event.key == pygame.K_r:
                        reset_current_layout()
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 3:
                        if layout_editing:
                            exit_layout_edit_mode()
                        else:
                            enter_layout_edit_mode()
                    elif layout_editing and event.button == 1:
                        layout_dragging = True
                        layout_last_mouse_pos = event.pos
                    elif layout_editing and event.button in (4, 5):
                        current_layout = current_layout.zoomed(1 if event.button == 4 else -1)
                        apply_current_layout()
                        layout_dirty = True
                        show_layout_edit_overlay()
                elif event.type == pygame.MOUSEBUTTONUP:
                    if layout_editing and event.button == 1:
                        layout_dragging = False
                        layout_last_mouse_pos = None
                    elif not layout_editing and event.button == 1:
                        interaction_requested = True
                elif event.type == pygame.MOUSEMOTION and layout_editing and layout_dragging:
                    if layout_last_mouse_pos is not None:
                        last_x, last_y = layout_last_mouse_pos
                        current_x, current_y = event.pos
                        current_layout = current_layout.moved_by_pixels(
                            current_x - last_x,
                            current_y - last_y,
                            win_w_and_h,
                            win_w_and_h,
                        )
                        apply_current_layout()
                        layout_dirty = True
                        show_layout_edit_overlay()
                    layout_last_mouse_pos = event.pos
                elif event.type == pygame.MOUSEWHEEL and layout_editing:
                    current_layout = current_layout.zoomed(int(event.y))
                    apply_current_layout()
                    layout_dirty = True
                    show_layout_edit_overlay()
                else:
                    pass

            if if_bye:
                # 退出动画期间不再处理残留的文本、音频和动作队列，避免对话框被普通渲染分支重新画出。
                while True:
                    try:
                        live2d_text_queue.get_nowait()
                    except queue.Empty:
                        break
                    except Exception:
                        break
                while True:
                    try:
                        emotion_queue.get_nowait()
                    except queue.Empty:
                        break
                    except Exception:
                        break
                while True:
                    try:
                        audio_file_queue.get_nowait()
                    except queue.Empty:
                        break
                    except Exception:
                        break
                while True:
                    try:
                        change_char_queue.get_nowait()
                    except queue.Empty:
                        break
                    except Exception:
                        break
                glClear(GL_COLOR_BUFFER_BIT)
                model.Update()
                render_background(texture)
                model.Draw()
                model_notice.draw()
                glUseProgram(0)
                pygame.display.flip()
                frame_clock.tick(self.target_fps)
                if self.motion_is_over:
                    self.run=False
                    self.save_l2d_json_paths_and_bg()
                continue

            # 从队列中获取要显示的新文本（只取最新，避免积压导致延迟）
            latest_text = None
            while True:
                try:
                    latest_text = live2d_text_queue.get_nowait()
                except queue.Empty:
                    break
                except Exception:
                    break
            if latest_text is not None and not self.structured_mode:
                self.new_text = latest_text
                if not layout_editing:
                    overlay.set_text(self.current_character.character_name, self.new_text)

            if not change_char_queue.empty():
                x=change_char_queue.get()
                if isinstance(x, str):
                    if x.lower() == "exit":
                        self.run = False
                        break
                    logger.warning("忽略旧 Live2D 字符串命令：%s", x)
                    continue
                if not isinstance(x, dict):
                    logger.warning("忽略无法识别的 Live2D 命令：%s", x)
                    continue

                command_type = str(x.get("type") or "")
                if command_type in {'play_segment', 'thinking', 'generation_finished', 'cancel_turn'}:
                    self.command(x, model)
                elif command_type =='start_talking':   #录音时
                    self.recording = True
                    self._reset_long_audio_motion_loop()
                    model.StartRandomMotion("talking_motion", 4, self.onStartCallback, position="C")
                elif command_type=='stop_talking':   #录音结束
                    self.recording = False
                    self._reset_long_audio_motion_loop()
                    self.onFinishCallback()
                elif command_type=='change_l2d_background':
                    glActiveTexture(GL_TEXTURE0)  # 必加，否则白屏
                    glDeleteTextures([texture])
                    self.back_img_index += 1
                    if self.back_img_index >= len(self.BACK_IMAGE):
                        self.back_img_index = 0
                    texture = BackgroundRen.render(
                        pygame.image.load(self.BACK_IMAGE[self.back_img_index]).convert_alpha())
                    render_background(texture)
                elif command_type == "switch_live2d":
                    self.sakiko_state = bool(x.get("sakiko_state", self.sakiko_state))
                    if layout_editing:
                        exit_layout_edit_mode()
                    self._reset_long_audio_motion_loop()
                    character_name = str(x.get("character_name") or "")
                    character_folder_name = str(x.get("character_folder_name") or "")
                    model_json = x.get("model_json")
                    target_model_path = self.switch_live2d_target(
                        character_name,
                        model_json if isinstance(model_json, str) and model_json else None,
                        character_folder_name=character_folder_name,
                        use_default=False,
                    )
                    if self.if_sakiko and self.sakiko_state and target_model_path is not None:
                        target_model_path = '../live2d_related/sakiko/live2D_model_costume/3.model.json'

                    mouth_keep_open_value = 0.0
                    self.motion_is_over = True
                    self.think_motion_is_over = True
                    model = replace_model(model, target_model_path)

                    if isinstance(model, Live2DModelAdapter) and target_model_path is not None:
                        current_layout_model_path = target_model_path
                        current_layout = get_live2d_layout(current_layout_model_path, model.version, layout_scene, "desktop")
                        apply_current_layout()
                        if self.if_sakiko and self.sakiko_state:
                            model.SetSemanticExpression('serious')
                        else:
                            model.SetSemanticExpression('idle')
                        model.StartRandomMotion("change_character",3,self.onStartCallback,self.onFinishCallback, position="C")
                        if self.current_character.icon_path is not None:
                            pygame.display.set_icon(pygame.image.load(self.current_character.icon_path))
                        logger.debug("Live2D模型切换成功：%s", self.PATH_JSON)
                    else:
                        current_layout_model_path = None
                        current_layout = Live2DLayout(scale=1.0, offset_x=0.0, offset_y=0.0)
                    overlay.set_text(self.current_character.character_name, '...')
                elif command_type == "switch_l2d_fps":
                    fps = int(x.get("fps"))
                    self.target_fps = fps
                    logger.info("已切换 Live2D 渲染帧率为 %d fps", self.target_fps)

                elif command_type == "toggle_l2d_layout_edit":
                    if layout_editing:
                        exit_layout_edit_mode()
                    else:
                        enter_layout_edit_mode()

                elif command_type == "exit":
                    if layout_editing:
                        exit_layout_edit_mode()
                    self.run = False
                    break
                else:
                    logger.warning("忽略未知 Live2D 命令：%s", x)

            if not self.structured_mode and not is_text_generating_queue.empty() and self.think_motion_is_over:  # 思考时
                if time.time()-last_saved_time_think>interval_think:
                    model.StartRandomMotion("text_generating",3,self.onStartCallback_think_motion_version, self.onFinishCallback_think_motion_version, position="C")

                    last_saved_time_think=time.time()
                    interval_think=15

            if layout_editing:
                pygame.display.set_caption(f"{self.current_character.character_name}布局编辑中")
            elif  is_text_generating_queue.empty():
                if self.if_sakiko:
                    pygame.display.set_caption("祥子") if not self.sakiko_state else pygame.display.set_caption("Oblivionis")
                else:
                    pygame.display.set_caption(f"{self.current_character.character_name}")

            if not self.structured_mode and self.motion_is_over and not pygame.mixer.music.get_busy():  #恢复idle动作
                if is_text_generating_queue.empty() and time.time()-self.idle_recover_timer>2.5:
                    model.StartRandomMotion("idle_motion", 1, self.onStartCallback, position="C")

            if not self.structured_mode and (time.time()-last_saved_time)>25 :   #待机动作
                if self.live2d_this_turn_motion_complete and is_text_generating_queue.empty():
                    model.StartRandomMotion("IDLE",1,self.onStartCallback,self.onFinishCallback, position="C")
                last_saved_time=time.time()

            if interaction_requested:
                self.play_interaction(
                    model,
                    blocked=layout_editing or not is_text_generating_queue.empty(),
                )
                interaction_requested = False

            self.live2d_this_turn_motion_complete=not pygame.mixer.music.get_busy()
            # 更新到共享变量
            motion_complete_value.value = not self.busy and self.live2d_this_turn_motion_complete

            if not char_is_converted_queue.empty():
                from runtime.character_presentation import apply_sakiko_state
                def replace_character_model(previous, path):
                    nonlocal current_layout_model_path, current_layout
                    candidate = replace_model(previous, path)
                    if isinstance(candidate, Live2DModelAdapter):
                        current_layout_model_path = path
                        current_layout = get_live2d_layout(path, candidate.version, layout_scene, 'desktop')
                        candidate.SetScale(current_layout.scale)
                        candidate.SetOffset(current_layout.offset_x, current_layout.offset_y)
                    return candidate
                model = apply_sakiko_state(self, model, char_is_converted_queue.get(), self.PATH_JSON, replace_character_model)

            if not emotion_queue.empty():
                emotion = emotion_queue.get()
                if emotion=='bye':
                    self._reset_long_audio_motion_loop()
                    if not if_bye:
                        started = model.StartRandomMotion("bye",3,self.onStartCallback,self.onFinishCallback, position="C")
                        if not started:
                            self.motion_is_over = True
                    if_bye=True
                    glClear(GL_COLOR_BUFFER_BIT)
                    model.Update()
                    render_background(texture)
                    model.Draw()
                    model_notice.draw()
                    glUseProgram(0)
                    pygame.display.flip()
                    frame_clock.tick(self.target_fps)
                    if self.motion_is_over:
                        self.run=False
                        self.save_l2d_json_paths_and_bg()
                    continue

                this_turn_audio_file_path=audio_file_queue.get()
                motion_group = motion_group_for_emotion(str(emotion), default="")
                if not motion_group:
                    logger.warning("忽略未知情感标签：%s", emotion)
                    continue
                self._prepare_long_audio_motion_loop(motion_group, this_turn_audio_file_path)
                self.motion_is_over = False
                started = model.StartRandomMotion(motion_group,3,lambda *args:self.onStartCallback_emotion_version(audio_file_path=this_turn_audio_file_path),self.onFinishCallback, position="C")
                if not started:
                    self.onStartCallback_emotion_version(audio_file_path=this_turn_audio_file_path)
                    self.motion_is_over = True
                    self._reset_long_audio_motion_loop()
                self.think_motion_is_over=True  #放在这里就对了。。
                overlay.set_text(self.current_character.character_name,self.new_text)  #有感情标签传入，说明角色肯定要说话，此时更新文本


            # 清除缓冲区
            self._update_long_audio_motion_loop(model)

            glClear(GL_COLOR_BUFFER_BIT)
            # 更新live2d到缓冲区
            model.Update()
            self._update_eye_open_transition(model)
            # 渲染背景图片
            render_background(texture)
            # 渲染live2d到屏幕
            if not self.structured_mode and self.wavHandler.Update() and is_update_mouth_sync % 3==0:  # 控制说话时的嘴型
                mouth_keep_open_value=self.wavHandler.GetRms() * self.lipSyncN
                self.idle_recover_timer = time.time()
            if self.structured_mode:
                self.update_playback(model)
            else:
                model.set_parameter_value("mouth_open_y", mouth_keep_open_value)
            is_update_mouth_sync += 1

            model.Draw()
            model_notice.draw()
            overlay.update()
            # 从共享变量读取是否显示文本
            if layout_editing or is_display_text_value.value:
                overlay.draw()
            glUseProgram(0)
            # 4、pygame刷新
            pygame.display.flip()
            frame_clock.tick(self.target_fps)


        try:
            if layout_editing:
                exit_layout_edit_mode()
        except Exception:
            logger.exception("退出 Live2D 布局编辑模式失败")
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        try:
            model.dispose()
        except Exception:
            pass
        try:
            glDeleteTextures([texture])
        except Exception:
            pass

        model_notice.dispose()
        session.close()
        #结束pygame
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        pygame.quit()


def run_live2d_process(emotion_queue, audio_file_path_queue, is_text_generating_queue, char_is_converted_queue,
                       change_char_queue, live2d_text_queue, is_display_text_value, motion_complete_value, desktop_w,
                       desktop_h, log_queue, playback_events=None, ready_event=None):
    """
    Live2D 子进程入口函数
    不接收 characters 对象，而是在子进程内重新加载，避免 Windows 下 pickle 序列化截断问题
    """
    setup_worker_logging(log_queue)

    import sys, os
    if os.name == 'nt':
        try:
            import ctypes
            # 设置子进程的高DPI感知(与Qt主进程保持一致)，防止Win的高分辨率缩放导致的窗口巨大
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    # 临时静默标准输出，防止子进程二次加载 characters 时在命令行狂刷重复信息
    with open(os.devnull, 'w') as devnull:
        with contextlib.redirect_stdout(devnull):
            # 在子进程中重新导入和创建 characters
            import character
            get_all = character.GetCharacterAttributes()
            characters = get_all.character_class_list

    live2d_player = Live2DModule()
    live2d_player.live2D_initialize(characters)
    live2d_player.play_live2d(emotion_queue, audio_file_path_queue, is_text_generating_queue,
                                char_is_converted_queue, change_char_queue, live2d_text_queue, is_display_text_value,
                                motion_complete_value, desktop_w, desktop_h, log_queue, playback_events, ready_event)



if __name__=='__main__':        #单独测试live2d
    import os, sys

    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
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
