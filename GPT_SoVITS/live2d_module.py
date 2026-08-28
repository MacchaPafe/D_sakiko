from __future__ import annotations

import contextlib
import time
from live2d.utils.lipsync import WavHandler
import glob, os, sys
from uuid import uuid4

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

from live2d_support.text_overlay import TextOverlay
from qconfig import d_sakiko_config
from log import setup_worker_logging, get_logger
from live2d_support.runtime_adapter import (
    Live2DModelAdapter,
    Live2DModelProtocol,
    NullLive2DModel,
    detect_live2d_runtime_version,
    initialize_live2d_runtime,
    load_live2d_runtime,
    release_live2d_runtime,
)
from live2d_support.shared_segment_executor import (
    PygameRendererCommandAdapter,
    renderer_command_is_frame_barrier,
)
from live2d_support.runtime_window import recreate_runtime_window
from live2d_support.layout import (
    Live2DLayout,
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

class Live2DModule:
    def __init__(self):
        self.PATH_JSON=None
        self.BACK_IMAGE=None
        self.BACKGROUND_POSITION=((-1.0, 1.0, 0), (1.0, -1.0, 0), (1.0, 1.0, 0), (-1.0, -1.0, 0))
        self.wavHandler=WavHandler()
        self.lipSyncN:float=1.4
        self.run=True
        self.character_list=[]
        self.character_by_name = {}
        self.character_by_folder = {}
        self.current_character_name = ""
        self.current_model_json: str | None = None
        self.is_display_text=True
        self.new_text=''

        #解决睁眼太快的突兀问题，强制睁眼过渡
        self.force_eyes_open = False
        self.eye_open_pending = False
        self.eye_open_transition_start = 0.0
        self.eye_open_transition_duration = 0.1
        self.eye_open_param_ids = ("eye_l_open", "eye_r_open")
        self.eye_open_start_values = {param_id: 1.0 for param_id in self.eye_open_param_ids}

    @property
    def current_character(self):
        character = self.character_by_name.get(self.current_character_name)
        if character is not None:
            return character
        if self.character_list:
            return self.character_list[0]
        raise ValueError("Live2D 角色列表为空。")

    def _default_model_json_for_character(self, character) -> str | None:
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

    def switch_live2d_target(self, character_name: str, model_json: str | None = None, *, character_folder_name: str = "", use_default: bool = True) -> str | None:
        character = self.character_by_folder.get(character_folder_name) if character_folder_name else None
        character = character or self.character_by_name.get(character_name)
        if character is None:
            raise ValueError(f"找不到 Live2D 角色：{character_name}")
        target = self._default_model_json_for_character(character) if use_default else model_json
        self.current_character_name = character.character_name
        self.current_model_json = target
        self.PATH_JSON = target
        return target

    def live2D_initialize(self, characters):
        self.character_list = characters
        self.character_by_name = {character.character_name: character for character in characters}
        self.character_by_folder = {character.character_folder_name: character for character in characters}
        if self.character_list:
            # Metadata is needed to construct the window. The model itself is
            # loaded only after the authoritative owner sends switch_live2d.
            self.current_character_name = self.character_list[0].character_name
        back_img_png = glob.glob(os.path.join("../live2d_related", "*.png"))
        back_img_jpg = glob.glob(os.path.join("../live2d_related", "*.jpg"))
        if not (back_img_png + back_img_jpg):
            raise FileNotFoundError("没有找到背景图片文件(.png/.jpg)，自带的也被删了吗...")
        self.BACK_IMAGE = back_img_jpg + back_img_png
        self.back_img_index = 0
        config_data = d_sakiko_config.background_image_path.value
        if config_data in self.BACK_IMAGE:
            self.back_img_index = self.BACK_IMAGE.index(config_data)

    def _reset_eye_open_transition(self):
        self.force_eyes_open = False
        self.eye_open_pending = False
        self.eye_open_transition_start = 0.0
        self.eye_open_start_values = {param_id: 1.0 for param_id in self.eye_open_param_ids}

    def _get_model_parameter_value(self, model, param_id: str, default: float = 1.0) -> float:
        getter = getattr(model, "get_parameter_value", None)
        if callable(getter):
            try:
                return float(getter(param_id, default))
            except Exception:
                return default
        try:
            for index in range(model.GetParameterCount()):
                param = model.GetParameter(index)
                if getattr(param, "id", "") == param_id:
                    return max(0.0, min(1.0, float(getattr(param, "value", default))))
        except Exception:
            pass
        return default

    def _set_model_eye_open_values(self, model, values):
        setter = getattr(model, "set_parameter_value", None)
        if callable(setter):
            for param_id, value in values.items():
                try:
                    setter(param_id, value)
                except Exception:
                    pass
            return
        for param_id, value in values.items():
            try:
                model.SetParameterValue(param_id, value)
            except Exception:
                pass

    def _update_eye_open_transition(self, model):
        if self.eye_open_pending:
            self.eye_open_start_values = {param_id: self._get_model_parameter_value(model, param_id) for param_id in self.eye_open_param_ids}
            if self.eye_open_start_values.get("eye_l_open", 1.0) > 0.5:
                self._reset_eye_open_transition()
                return
            self.eye_open_transition_start = time.time()
            self.eye_open_pending = False
        if self.eye_open_transition_start <= 0:
            return
        progress = max(0.0, min(1.0, (time.time() - self.eye_open_transition_start) / self.eye_open_transition_duration))
        values = {param_id: start + (1.0 - start) * progress for param_id, start in self.eye_open_start_values.items()}
        self._set_model_eye_open_values(model, values)
        if progress >= 1.0:
            self._set_model_eye_open_values(model, {param_id: 1.0 for param_id in self.eye_open_param_ids})
            self._reset_eye_open_transition()

    def save_l2d_json_paths_and_bg(self):
        l2d_json_paths_dict = {}
        for char in self.character_list:
            if char.character_name!="祥子" and char.live2d_json:
                l2d_json_paths_dict[char.character_name] = char.live2d_json
        with d_sakiko_config as cfg:
            cfg.set(cfg.l2d_json_paths_dict, l2d_json_paths_dict)
            cfg.set(cfg.background_image_path, self.BACK_IMAGE[self.back_img_index])

    def _start_audio_runtime(self, audio_file_path: str) -> bool:
        """Play an owner-selected audio path and update only runtime lip-sync."""
        logger = get_logger(__name__)
        if not audio_file_path or not os.path.isfile(audio_file_path):
            logger.warning("跳过无效音频路径：%s", audio_file_path)
            return False
        try:
            pygame.mixer.music.load(audio_file_path)
            pygame.mixer.music.play()
        except pygame.error as exc:
            logger.warning("播放音频失败，已跳过：%s，错误：%s", audio_file_path, exc)
            return False
        if audio_file_path != '../reference_audio/silent_audio/silence.wav':
            try:
                self.wavHandler.Start(audio_file_path)
            except Exception as exc:
                logger.warning("口型同步读取音频失败，已跳过：%s，错误：%s", audio_file_path, exc)
        return True

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
                    log_queue,
                    renderer_fact_queue=None,
                    renderer_command_queue=None,
                    renderer_trace_queue=None,
                    authoritative_emotion_audio=False):
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
        current_runtime = None
        current_runtime_version = None
        model: Live2DModelProtocol = NullLive2DModel()
        if self.PATH_JSON is not None:
            try:
                current_runtime_version = detect_live2d_runtime_version(self.PATH_JSON)
                current_runtime = load_live2d_runtime(current_runtime_version)
                initialize_live2d_runtime(current_runtime)
                loaded_model = Live2DModelAdapter.create(self.PATH_JSON)
                loaded_model.Resize(win_w_and_h, win_w_and_h)
                loaded_model.SetAutoBlinkEnable(True)
                loaded_model.SetAutoBreathEnable(True)
                model = loaded_model
            except Exception:
                logger.exception(
                    "Live2D 模型加载失败，将使用无模型展示：%s",
                    self.PATH_JSON,
                )
                release_live2d_runtime(current_runtime)
                current_runtime = None
                current_runtime_version = None
                model = NullLive2DModel()

        frame_clock = pygame.time.Clock()
        self.target_fps = 60

        initial_icon_path = self.current_character.icon_path
        if not initial_icon_path or not os.path.exists(initial_icon_path):
            initial_icon_path = "../live2d_related/sakiko/sakiko_icon.png"
        pygame.display.set_icon(pygame.image.load(initial_icon_path))

        overlay=TextOverlay((win_w_and_h, win_w_and_h),[self.current_character.character_name])
        glEnable(GL_TEXTURE_2D)

        texture = BackgroundRen.render(pygame.image.load(self.BACK_IMAGE[self.back_img_index]).convert_alpha())

        def render_background(texture_id: object) -> None:
            """显式绑定背景纹理并绘制背景图。"""
            glUseProgram(0)
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, texture_id)
            BackgroundRen.blit(*self.BACKGROUND_POSITION)

        layout_scene = "single"
        current_layout_model_path = self.PATH_JSON
        current_layout = (
            get_live2d_layout(current_layout_model_path, model.version, layout_scene)
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

        renderer_model_token = ""
        renderer_instance_id = f"pygame-{uuid4().hex}"

        def emit_renderer_ready() -> None:
            """Publish runtime capability facts for the shared owner."""
            if renderer_fact_queue is None:
                return
            motion_files = model.motion_files_by_group if isinstance(model, Live2DModelAdapter) else {}
            expression_ids = list(model.expression_ids) if isinstance(model, Live2DModelAdapter) else []
            runtime_version = model.version if isinstance(model, Live2DModelAdapter) else ""
            renderer_fact_queue.put({
                "type": "renderer_ready",
                "data": {
                    "renderer_id": "pygame-renderer",
                    "renderer_instance_id": renderer_instance_id,
                    "renderer_role": "pygame",
                    "model_token": renderer_model_token,
                    "model_key": self.current_character.character_folder_name,
                    "runtime_version": runtime_version,
                    "model_json": current_layout_model_path or self.PATH_JSON or "",
                    "motion_files_by_group": motion_files,
                    "expression_ids": expression_ids,
                    "model_urls": {
                        "model_json": current_layout_model_path or self.PATH_JSON or "",
                        "white": "../live2d_related/sakiko/live2D_model/3.model.json",
                        "black": "../live2d_related/sakiko/live2D_model_costume/3.model.json",
                    },
                    "capabilities": {
                        "motion": isinstance(model, Live2DModelAdapter),
                        "audio": True,
                        "lipsync": isinstance(model, Live2DModelAdapter),
                    },
                },
            })

        if renderer_fact_queue is not None:
            renderer_fact_queue.put({
                "type": "renderer_hello",
                "data": {
                    "renderer_id": "pygame-renderer",
                    "renderer_instance_id": renderer_instance_id,
                    "renderer_role": "pygame",
                    "model_token": "",
                    "model_key": "",
                },
            })
            # A missing model is still a healthy audio-capable renderer.  The
            # owner needs this ready fact to preserve upstream audio fallback.
            emit_renderer_ready()

        def emit_renderer_fact(fact: dict) -> None:
            if fact.get("type") == "motion_finished":
                self.eye_open_pending = True
            if renderer_fact_queue is not None:
                payload = dict(fact)
                data = dict(payload.get("data") or {})
                data.setdefault("renderer_id", "pygame-renderer")
                data.setdefault("renderer_instance_id", renderer_instance_id)
                payload["data"] = data
                renderer_fact_queue.put(payload)

        def emit_renderer_trace(trace: dict) -> None:
            if renderer_trace_queue is not None:
                renderer_trace_queue.put(trace)

        renderer_audio_token = ""
        renderer_audio_was_busy = False
        renderer_model_switch_pending = False
        renderer_adapter = None
        renderer_local_controls = queue.Queue()
        renderer_thinking_active = False

        def execute_renderer_commands() -> None:
            nonlocal renderer_audio_token, renderer_audio_was_busy, renderer_thinking_active
            nonlocal renderer_model_switch_pending, renderer_adapter
            if renderer_command_queue is None:
                return
            # The switch control is consumed later in this frame.  Do not let
            # the next frame drain following commands through the old model.
            if renderer_model_switch_pending:
                return
            if renderer_adapter is None:
                renderer_adapter = PygameRendererCommandAdapter(model, emit_renderer_fact, self._start_audio_runtime)
            else:
                renderer_adapter.bind_runtime(model)
            while True:
                try:
                    command = renderer_command_queue.get_nowait()
                except queue.Empty:
                    break
                if isinstance(command, dict):
                    data = command.get("data", {})
                    if isinstance(data, dict):
                        target_ids = data.get("target_renderer_ids")
                        if isinstance(target_ids, (list, tuple, set)) and target_ids and "pygame-renderer" not in target_ids:
                            continue
                        target_id = data.get("target_renderer_id")
                        if target_id and str(target_id) != "pygame-renderer":
                            continue
                    if command.get("type") == "close_renderer":
                        self.run = False
                        continue
                    if command.get("type") == "switch_live2d":
                        switch_data = command.get("data", {})
                        if isinstance(switch_data, dict):
                            # Preserve the exact command envelope while the
                            # local loop consumes its flattened runtime data.
                            renderer_local_controls.put({"type": "switch_live2d", **switch_data})
                        # A model switch is a runtime barrier.  Leave any
                        # following exact commands in the renderer queue so
                        # the next frame rebinds the adapter to the newly
                        # loaded model instead of the disposed one.
                        if renderer_command_is_frame_barrier(command):
                            renderer_model_switch_pending = True
                            break
                    if command.get("type") in {"stop_audio", "reset"}:
                        try:
                            pygame.mixer.music.stop()
                        except Exception:
                            pass
                        self.wavHandler = WavHandler()
                    if command.get("type") in {"stop_motion", "reset"}:
                        try:
                            stop_all = getattr(getattr(model, "model", None), "StopAllMotions", None)
                            if callable(stop_all):
                                stop_all()
                        except Exception:
                            pass
                    if command.get("type") in {"stop_audio", "stop_motion", "reset", "thinking_changed", "change_l2d_background", "switch_l2d_fps", "toggle_l2d_layout_edit"}:
                        if command.get("type") == "thinking_changed":
                            data = command.get("data", {})
                            renderer_thinking_active = isinstance(data, dict) and data.get("active") is True
                        elif command.get("type") == "change_l2d_background":
                            change_char_queue.put(command.get("data", {}))
                        elif command.get("type") == "switch_l2d_fps":
                            change_char_queue.put(command.get("data", {}))
                        elif command.get("type") == "toggle_l2d_layout_edit":
                            change_char_queue.put(command.get("data", {}))
                        continue
                    if renderer_adapter.execute(command) and command.get("type") == "play_audio":
                        data = command.get("data", {})
                        if isinstance(data, dict):
                            renderer_audio_token = str(data.get("token") or "")
                            # A successful play call owns an active lifecycle
                            # even if a zero-length/erroring stream is already
                            # idle by the first backend poll.
                            renderer_audio_was_busy = True

        def emit_audio_idle_fact() -> None:
            nonlocal renderer_audio_was_busy, renderer_audio_token
            if not renderer_audio_token:
                return
            is_busy = pygame.mixer.music.get_busy()
            if renderer_audio_was_busy and not is_busy:
                emit_renderer_fact({"type": "audio_ended", "data": {"token": renderer_audio_token}})
                renderer_audio_token = ""
            renderer_audio_was_busy = is_busy

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
                    save_live2d_layout(current_layout_model_path, layout_scene, current_layout)
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
                reset_live2d_layout(current_layout_model_path, layout_scene)
            except Exception:
                logger.exception("重置 Live2D 布局配置失败")
            current_layout = get_live2d_layout(current_layout_model_path, model.version, layout_scene)
            apply_current_layout()
            layout_dirty = False
            show_layout_edit_overlay()

        apply_current_layout()

        mouse_position_x = 0

        logger.info("当前Live2D界面渲染硬件 %s", glGetString(GL_RENDERER).decode())

        is_update_mouth_sync = 0
        mouth_keep_open_value:float=0.0
        while self.run:
            execute_renderer_commands()
            emit_audio_idle_fact()
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
                        emit_renderer_fact({
                            "type": "renderer_intent",
                            "data": {"intent": "click", "renderer_id": "pygame-renderer"},
                        })
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
                if not layout_editing:
                    overlay.set_text(self.current_character.character_name, self.new_text)

            control_command = None
            try:
                control_command = renderer_local_controls.get_nowait()
            except queue.Empty:
                if not change_char_queue.empty():
                    control_command = change_char_queue.get()
            if control_command is not None:
                x = control_command
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
                if command_type in {"start_talking", "stop_talking", "cancel_turn"}:
                    # These are owner decisions; Pygame only applies explicit
                    # stop/reset mechanics when a command is emitted there.
                    logger.debug("Ignoring legacy business control in renderer: %s", command_type)
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
                    if layout_editing:
                        exit_layout_edit_mode()
                    character_name = str(x.get("character_name") or "")
                    renderer_model_token = str(x.get("model_token") or "")
                    model_json = x.get("model_json") or x.get("model_url")
                    # Model selection is authoritative upstream. Pygame only
                    # loads the explicit path carried by the exact command.
                    target_model_path = model_json if isinstance(model_json, str) and model_json else None
                    if character_name and character_name in self.character_by_name:
                        self.current_character_name = character_name
                    try:
                        model.dispose()
                    except Exception:
                        logger.debug("释放旧 Live2D 模型失败", exc_info=True)
                    model = NullLive2DModel()
                    target_version = None
                    try:
                        if target_model_path is not None:
                            target_version = detect_live2d_runtime_version(target_model_path)
                        if target_version != current_runtime_version:
                            try:
                                pygame.mixer.music.stop()
                            except Exception:
                                pass
                            self.wavHandler = WavHandler()
                            mouth_keep_open_value = 0.0
                            self._reset_eye_open_transition()
                            recreate_result = recreate_runtime_window(
                                current_runtime=current_runtime,
                                current_texture=texture,
                                target_version=target_version,
                                display=display,
                                window_position=f"{pygame_win_pos_w},{pygame_win_pos_h+caption_height}",
                                background_path=self.BACK_IMAGE[self.back_img_index],
                                render_texture=BackgroundRen.render,
                            )
                            current_runtime = recreate_result.runtime
                            current_runtime_version = target_version
                            texture = recreate_result.texture
                            frame_clock = pygame.time.Clock()
                            overlay = TextOverlay((win_w_and_h, win_w_and_h), [self.current_character.character_name])
                            overlay.set_text(self.current_character.character_name, self.new_text or "...")
                        if target_model_path is not None:
                            loaded_model = Live2DModelAdapter.create(target_model_path)
                            loaded_model.Resize(win_w_and_h, win_w_and_h)
                            loaded_model.SetAutoBlinkEnable(True)
                            loaded_model.SetAutoBreathEnable(True)
                            model = loaded_model
                    except Exception:
                        logger.exception(
                            "Live2D 模型切换失败，将使用无模型展示：%s",
                            target_model_path,
                        )
                        recreate_result = recreate_runtime_window(
                            current_runtime=current_runtime,
                            current_texture=texture,
                            target_version=None,
                            display=display,
                            window_position=f"{pygame_win_pos_w},{pygame_win_pos_h+caption_height}",
                            background_path=self.BACK_IMAGE[self.back_img_index],
                            render_texture=BackgroundRen.render,
                        )
                        current_runtime = recreate_result.runtime
                        current_runtime_version = None
                        texture = recreate_result.texture
                        frame_clock = pygame.time.Clock()
                        overlay = TextOverlay((win_w_and_h, win_w_and_h), [self.current_character.character_name])
                        model = NullLive2DModel()

                    if isinstance(model, Live2DModelAdapter) and target_model_path is not None:
                        current_layout_model_path = target_model_path
                        current_layout = get_live2d_layout(current_layout_model_path, model.version, layout_scene)
                        apply_current_layout()
                        if self.current_character.icon_path is not None:
                            pygame.display.set_icon(pygame.image.load(self.current_character.icon_path))
                        logger.debug("Live2D模型切换成功：%s", self.PATH_JSON)
                    else:
                        current_layout_model_path = None
                        current_layout = Live2DLayout(scale=1.0, offset_x=0.0, offset_y=0.0)
                    emit_renderer_ready()
                    overlay.set_text(self.current_character.character_name, '...')
                    if renderer_adapter is not None:
                        renderer_adapter.bind_runtime(model)
                    renderer_model_switch_pending = False
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

            if layout_editing:
                pygame.display.set_caption(f"{self.current_character.character_name}布局编辑中")
            elif renderer_thinking_active:
                pygame.display.set_caption(f"{self.current_character.character_name}思考中")
            else:
                pygame.display.set_caption(f"{self.current_character.character_name}")

            glClear(GL_COLOR_BUFFER_BIT)
            # 更新live2d到缓冲区
            model.Update()
            self._update_eye_open_transition(model)
            # 渲染背景图片
            render_background(texture)
            # 渲染live2d到屏幕
            if self.wavHandler.Update() and is_update_mouth_sync % 3==0:  # 控制说话时的嘴型
                mouth_keep_open_value=self.wavHandler.GetRms() * self.lipSyncN
            model.set_parameter_value("mouth_open_y", mouth_keep_open_value)
            is_update_mouth_sync += 1

            model.Draw()
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
            self.save_l2d_json_paths_and_bg()
        except Exception:
            logger.exception("退出前保存 Live2D 配置失败")
        try:
            glDeleteTextures([texture])
        except Exception:
            pass

        release_live2d_runtime(current_runtime)
        #结束pygame
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        pygame.quit()


def run_live2d_process(emotion_queue, audio_file_path_queue, is_text_generating_queue, char_is_converted_queue,
                       change_char_queue, live2d_text_queue, is_display_text_value, motion_complete_value, desktop_w,
                       desktop_h, log_queue, renderer_fact_queue=None, renderer_command_queue=None,
                       renderer_trace_queue=None, authoritative_emotion_audio=False):
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
                                motion_complete_value, desktop_w, desktop_h, log_queue, renderer_fact_queue,
                                renderer_command_queue, renderer_trace_queue, authoritative_emotion_audio)



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
