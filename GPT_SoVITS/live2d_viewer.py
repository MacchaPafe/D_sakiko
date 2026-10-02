from __future__ import annotations

import json
import multiprocessing
from multiprocessing.queues import Queue
import sys,os
import pathlib
import uuid
from queue import Empty
from typing import Optional

from PyQt5.QtCore import Qt, QTimer, QSize

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

import time

from qtUI import ChangeL2DModelWindow
from live2d_support.model_catalog import Live2DModelCatalog, Live2DModelOption
from ui.components.live2d_performance_editor import Live2DPerformanceEditor
from ui.components.live2d_viewer_widgets import (
    CharacterPicker, VIEWER_STYLE, V3_INTRO_TITLE, V3_INTRO_TEXT, show_v3_intro_once,
    configure_viewer_dpi, place_viewer_window, preview_desktop_size,
)
from live2d_support.viewer_preview import execute_viewer_preview
from live2d_support.expression_preview import ExpressionPreviewSession
from qconfig import d_sakiko_config
import pygame
from pygame.locals import DOUBLEBUF, OPENGL
from OpenGL.GL import *
import glob,os
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTextBrowser, QPushButton, QHBoxLayout, \
    QApplication, QLabel, QStackedWidget, QToolButton, QMenu, QDialog, QMessageBox, QComboBox, QListView, QStyleFactory

from PyQt5.QtGui import QFontDatabase, QFont, QIcon, QCloseEvent, QShowEvent

import character
from log import get_logger, setup_logging, shutdown_logging, setup_worker_logging, get_log_queue
from live2d_support.runtime_adapter import (
    Live2DModelAdapter,
    NullLive2DModel,
    detect_live2d_runtime_version,
)
from live2d_support.layout import (
    Live2DLayout,
    Live2DLayoutRuntime,
    default_live2d_layout,
    format_live2d_layout_status,
)
from live2d_support.motion_semantics import motion_group_display_title
from live2d_support.runtime_session import Live2DRuntimeSession
from multi_char_live2d_module import ModelLoadNoticeOverlay

logger = get_logger(__name__)

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

    def live2D_initialize(self, characters: list[character.CharacterAttributes]) -> None:
        """只使用已经配置默认 Live2D 模型的角色初始化动作预览器。"""
        self.character_list = [
            one_character
            for one_character in characters
            if one_character.live2d_json
        ]
        if not self.character_list:
            raise ValueError("没有可供动作编辑器使用的 Live2D 模型。")
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
                    desktop_h,
                    log_queue: Queue | None = None,
                    preview_result_queue: Queue | None = None):

        if log_queue is not None:
            setup_worker_logging(log_queue)

        win_w_and_h = int(0.7 * desktop_h)  # 根据显示器分辨率定义窗口大小，保证每个人看到的效果相同
        pygame_win_pos_w,pygame_win_pos_h=int(0.5*desktop_w-win_w_and_h),int(0.5*desktop_h-0.5*win_w_and_h)
        #以上设置后，会差出一个恶心的标题栏高度，因此还要加上一个标题栏高度

        caption_height = 0
        if os.name == 'nt':
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
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
        expression_session = ExpressionPreviewSession()
        model_notice = ModelLoadNoticeOverlay(display, slot_count=1)
        #pygame.display.set_icon(pygame.image.load("../live2d_related/sakiko_icon.png"))

        viewer_layout: Live2DLayout = Live2DLayout(scale=1.0, offset_x=0.0, offset_y=0.0)
        layout_editing = False
        layout_dragging = False
        layout_last_mouse_pos: tuple[int, int] | None = None

        def apply_viewer_layout(model_adapter: Live2DModelAdapter) -> None:
            """将动作预览窗口的临时布局应用到当前模型。"""
            model_adapter.SetScale(viewer_layout.scale)
            model_adapter.SetOffset(viewer_layout.offset_x, viewer_layout.offset_y)

        def update_viewer_caption() -> None:
            """根据临时布局编辑状态刷新动作预览窗口标题。"""
            if layout_editing:
                pygame.display.set_caption(f"Live2D动作预览布局编辑中 {format_live2d_layout_status(viewer_layout)}")
            else:
                pygame.display.set_caption("Live2D动作预览")

        def reset_transient_viewer_layout(
                runtime_version: Live2DLayoutRuntime,
                model_adapter: Live2DModelAdapter | None = None,
        ) -> None:
            """重置动作预览窗口的临时布局，不写入配置。"""
            nonlocal viewer_layout
            viewer_layout = default_live2d_layout(runtime_version, "single")
            if model_adapter is not None:
                apply_viewer_layout(model_adapter)
            update_viewer_caption()

        def enter_layout_edit_mode() -> None:
            """进入动作预览窗口的临时布局编辑模式。"""
            nonlocal layout_editing, layout_dragging, layout_last_mouse_pos
            if not isinstance(model, Live2DModelAdapter):
                return
            layout_editing = True
            layout_dragging = False
            layout_last_mouse_pos = None
            update_viewer_caption()

        def exit_layout_edit_mode() -> None:
            """退出动作预览窗口的临时布局编辑模式，不保存布局。"""
            nonlocal layout_editing, layout_dragging, layout_last_mouse_pos
            layout_editing = False
            layout_dragging = False
            layout_last_mouse_pos = None
            update_viewer_caption()

        def setup_model(model_adapter: Live2DModelAdapter) -> Live2DModelAdapter:
            """配置预览模型的尺寸、布局及自动动作。"""
            model_adapter.Resize(win_w_and_h, win_w_and_h)
            apply_viewer_layout(model_adapter)
            model_adapter.SetAutoBlinkEnable(True)
            model_adapter.SetAutoBreathEnable(True)
            return model_adapter

        def create_viewer_model(model_json_path: str) -> Live2DModelAdapter:
            """从窗口会话加载预览模型。"""
            return session.create_model(model_json_path)

        model: Live2DModelAdapter | NullLive2DModel = NullLive2DModel()
        update_viewer_caption()
        glEnable(GL_TEXTURE_2D)

        #texture_thinking=BackgroundRen.render(pygame.image.load('X:\\D_Sakiko2.0\\live2d_related\\costumeBG.png').convert_alpha())    #想做背景切换功能，但无论如何都会有bug
        texture = BackgroundRen.render(pygame.image.load(self.BACK_IMAGE).convert_alpha())

        def render_background(texture_id: object) -> None:
            """恢复固定管线后绘制窗口背景。"""
            glUseProgram(0)
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, texture_id)
            BackgroundRen.blit(*self.BACKGROUND_POSITION)

        def switch_model_runtime(
                model_adapter: Live2DModelAdapter | NullLive2DModel,
                model_json_path: str,
        ) -> Live2DModelAdapter | NullLive2DModel:
            """立即清空旧模型，在常驻运行时中加载目标模型，失败保留空白。"""
            nonlocal layout_editing, layout_dragging, layout_last_mouse_pos
            layout_editing = False
            layout_dragging = False
            layout_last_mouse_pos = None
            expression_session.close()
            model_adapter.dispose()
            model_notice.clear()
            glClear(GL_COLOR_BUFFER_BIT)
            render_background(texture)
            pygame.display.flip()
            candidate: Live2DModelAdapter | None = None
            try:
                target_version = detect_live2d_runtime_version(model_json_path)
                reset_transient_viewer_layout(target_version)
                candidate = create_viewer_model(model_json_path)
                return setup_model(candidate)
            except Exception:
                if candidate is not None:
                    candidate.dispose()
                logger.exception("动作预览模型加载失败：%s", model_json_path)
                model_notice.set_failed_slots({0})
                return NullLive2DModel()

        model = switch_model_runtime(model, self.PATH_JSON)

        logger.info("当前 Live2D 界面渲染硬件：%s", glGetString(GL_RENDERER).decode())
        while self.run:

            for event in pygame.event.get():    #退出程序逻辑
                if event.type == pygame.QUIT:
                    self.run = False
                    break
                elif event.type == pygame.KEYDOWN and layout_editing:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        exit_layout_edit_mode()
                    elif event.key == pygame.K_r:
                        reset_transient_viewer_layout(model.version, model) if isinstance(model, Live2DModelAdapter) else None
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
                        viewer_layout = viewer_layout.zoomed(1 if event.button == 4 else -1)
                        apply_viewer_layout(model)
                        update_viewer_caption()
                elif event.type == pygame.MOUSEBUTTONUP and layout_editing and event.button == 1:
                    layout_dragging = False
                    layout_last_mouse_pos = None
                elif event.type == pygame.MOUSEMOTION and layout_editing and layout_dragging:
                    if layout_last_mouse_pos is not None:
                        last_x, last_y = layout_last_mouse_pos
                        current_x, current_y = event.pos
                        viewer_layout = viewer_layout.moved_by_pixels(
                            current_x - last_x,
                            current_y - last_y,
                            win_w_and_h,
                            win_w_and_h,
                        )
                        apply_viewer_layout(model)
                        update_viewer_caption()
                    layout_last_mouse_pos = event.pos
                elif event.type == pygame.MOUSEWHEEL and layout_editing:
                    viewer_layout = viewer_layout.zoomed(int(event.y))
                    apply_viewer_layout(model)
                    update_viewer_caption()

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
                    model = switch_model_runtime(model, self.PATH_JSON)

                    if self.character_list[self.current_character_num].icon_path is not None:
                        pygame.display.set_icon(pygame.image.load(self.character_list[self.current_character_num].icon_path))
                elif isinstance(x, dict) and x.get("type") == "select_character":
                    index = x.get("index")
                    if isinstance(index, int) and 0 <= index < len(self.character_list):
                        self.current_character_num = index
                        selected = self.character_list[index]
                        self.if_sakiko = selected.character_name == "祥子"
                        self.PATH_JSON = x.get("model_path")
                        model = switch_model_runtime(model, self.PATH_JSON)
                        if selected.icon_path:
                            pygame.display.set_icon(pygame.image.load(selected.icon_path))
                # 传入一个路径，表示要求加载同角色一个新的 live2d 模型
                elif isinstance(x, str):
                    model = switch_model_runtime(model, x)

            if not motion_queue.empty():
                motion_name=motion_queue.get()
                if isinstance(motion_name, dict) and motion_name.get("type") == "expression_editor":
                    result = expression_session.execute(model, motion_name)
                else:
                    result = execute_viewer_preview(model, motion_name)
                if preview_result_queue is not None:
                    preview_result_queue.put(result)

            # 清除缓冲区
            #live2d.clearBuffer()
            glClear(GL_COLOR_BUFFER_BIT)
            # 更新live2d到缓冲区
            if not expression_session.static:
                model.Update()
                expression_session.after_update()
            # 渲染背景图片
            render_background(texture)

            model.Draw()
            model_notice.draw()
            glUseProgram(0)
            # 4、pygame刷新
            pygame.display.flip()


        try:
            expression_session.close()
            model.dispose()
        except Exception:
            logger.debug("释放 Live2D 模型失败", exc_info=True)
        model_notice.dispose()
        session.close()
        #结束pygame
        pygame.quit()


class ViewerGUI(QWidget):
    def __init__(
            self, characters: list[character.CharacterAttributes], motion_queue: Queue,
            change_char_queue: Queue, preview_result_queue: Queue | None = None,
    ) -> None:
        """按模型版本展示编辑流程，并将预览、角色选择和保存状态分层。"""
        super().__init__()
        self.setWindowTitle("Live2D 演出编辑器")
        self.setStyleSheet(VIEWER_STYLE)
        self.character_list = characters
        self.current_char_index = 0
        self.editing_form = "black"
        self.motion_queue = motion_queue
        self.change_char_queue = change_char_queue
        self.preview_result_queue = preview_result_queue
        self._preview_request_id: str | None = None
        self._preview_started_at = 0.0
        self._intro_pending = False
        self._closed = False
        self.all_motion_data = None
        self.left_selected_motion_path: str | None = None
        self.right_selected_group: str | None = None
        self.right_selected_index: int | None = None
        self.current_char_base_folder_name = ""
        self.current_char_folder_path = pathlib.Path("")
        self.current_model_json_path: pathlib.Path | None = None
        self.current_model_version: str | None = None
        self.use_default_model = {0: True}
        self.extra_model_name = {0: None}
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 18, 20, 16)
        main_layout.setSpacing(14)
        header = QHBoxLayout()
        self.btn_change_char = QPushButton()
        self.btn_change_char.clicked.connect(self.change_char)
        self.btn_change_costume = QPushButton()
        self.btn_change_costume.clicked.connect(self.change_costume)
        header.addWidget(self.btn_change_char)
        self.form_selector = QComboBox()
        self.form_selector.setObjectName("formSelector")
        # 使用普通列表弹窗，避免 macOS 原生菜单与编辑器样式混用导致偏移和黑边。
        form_style = QStyleFactory.create("Fusion")
        form_style.setParent(self.form_selector)
        self.form_selector.setStyle(form_style)
        form_view = QListView(self.form_selector)
        self.form_selector.setView(form_view)
        self.form_selector.setStyleSheet(VIEWER_STYLE)
        self.form_selector.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.form_selector.addItem("黑祥", "black")
        self.form_selector.addItem("白祥", "white")
        self.form_selector.setAccessibleName("编辑的祥子形态")
        self.form_selector.currentIndexChanged.connect(self.select_form)
        header.addWidget(self.form_selector)
        header.addWidget(self.btn_change_costume)
        self.mask_button = QPushButton("面具动作…")
        self.mask_button.clicked.connect(self.open_mask_actions)
        header.addWidget(self.mask_button)
        header.addStretch()
        self.version_badge = QLabel()
        self.version_badge.setObjectName("muted")
        header.addWidget(self.version_badge)
        self.more_button = QToolButton()
        self.more_button.setText("更多 ⋯")
        self.more_button.setObjectName("quiet")
        self.more_button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.more_button)
        self.automatic_action = menu.addAction("旧版动作组设置…", self.open_automatic_settings)
        self.intro_action = menu.addAction("关于 V3 编辑方式…", self.show_v3_help)
        self.more_button.setMenu(menu)
        header.addWidget(self.more_button)
        main_layout.addLayout(header)
        self.page_title = QLabel()
        self.page_title.setObjectName("pageTitle")
        main_layout.addWidget(self.page_title)
        self.pages = QStackedWidget()
        main_layout.addWidget(self.pages, 1)

        self.groups_panel = QWidget()
        groups_layout = QVBoxLayout(self.groups_panel)
        groups_layout.setContentsMargins(0, 0, 0, 0)
        self.group_hint = QLabel("根据对话情绪，从对应组中选择动作。点击动作即可预览。")
        self.group_hint.setObjectName("muted")
        self.group_hint.setWordWrap(True)
        groups_layout.addWidget(self.group_hint)
        columns = QHBoxLayout()
        self.all_mnt_display, self.current_mnt_display = QTextBrowser(), QTextBrowser()
        self.all_mnt_title, self.current_mnt_title = QLabel("可用动作"), QLabel("动作组")
        for label, browser in ((self.all_mnt_title, self.all_mnt_display), (self.current_mnt_title, self.current_mnt_display)):
            label.setObjectName("sectionTitle")
            column = QVBoxLayout()
            column.addWidget(label)
            browser.setOpenExternalLinks(False)
            browser.setOpenLinks(False)
            column.addWidget(browser, 1)
            columns.addLayout(column)
        self.all_mnt_display.anchorClicked.connect(self.play_motion_all_mtn_ver)
        self.current_mnt_display.anchorClicked.connect(self.play_motion_cur_mtn_ver)
        groups_layout.addLayout(columns, 1)
        operations = QHBoxLayout()
        self.btn_add_motion = QPushButton("添加到组")
        self.btn_replace_motion = QPushButton("替换选中动作")
        self.btn_delete_motion = QPushButton("从组中移除")
        self.btn_add_motion.clicked.connect(self.on_add_motion)
        self.btn_replace_motion.clicked.connect(self.on_replace_motion)
        self.btn_delete_motion.clicked.connect(self.on_delete_motion)
        for button in (self.btn_add_motion, self.btn_replace_motion, self.btn_delete_motion):
            operations.addWidget(button)
        groups_layout.addLayout(operations)
        self.pages.addWidget(self.groups_panel)
        self.performance_editor = Live2DPerformanceEditor(self.send_preview, self)
        self.pages.addWidget(self.performance_editor)
        self.footer_panel = QWidget()
        footer = self.footer_layout = QHBoxLayout(self.footer_panel)
        footer.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel("选择动作，在模型窗口预览")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        footer.addStretch()
        self.details_button = QToolButton()
        self.details_button.setText("详情")
        self.details_button.setObjectName("quiet")
        self.details_button.setCheckable(True)
        footer.addWidget(self.details_button)
        main_layout.addWidget(self.footer_panel)
        self.message_box = QTextBrowser()
        self.message_box.setMaximumHeight(100)
        self.message_box.hide()
        self.details_button.toggled.connect(self.message_box.setVisible)
        self.message_box.textChanged.connect(self._sync_log_status)
        main_layout.addWidget(self.message_box)
        self.result_timer = QTimer(self)
        self.result_timer.timeout.connect(self.poll_preview_results)
        self.result_timer.start(100)
        self.load_suitable_model()
        place_viewer_window(self)

    def _sync_log_status(self) -> None:
        """把最近一条操作摘要放在状态栏，详情默认收起。"""
        if self._preview_request_id:
            return
        lines = self.message_box.toPlainText().splitlines()
        if lines:
            self.status_label.setText(lines[-1])

    def _refresh_model_page(self) -> None:
        """V2 展示分组，V3 展示独立选择，不保留无效的技术标签页。"""
        independent = self.current_model_version == "v3" and self.all_motion_data is not None
        self.pages.setCurrentWidget(self.performance_editor if independent else self.groups_panel)
        self.page_title.setText("动作与表情" if independent else "情绪与动作")
        self.version_badge.setText((self.current_model_version or "无模型").upper())
        self.btn_change_char.setText(self.character_list[self.current_char_index].character_name + " ▾")
        self.btn_change_costume.setText(str(self.extra_model_name.get(self._selection_key()) or "默认服装") + " ▾")
        sakiko = self.character_list[self.current_char_index].character_folder_name == "sakiko"
        self.form_selector.setVisible(sakiko)
        self.mask_button.setVisible(sakiko and self.editing_form == "black")
        self.mask_button.setEnabled(self.current_model_json_path is not None and self.all_motion_data is not None)
        self.automatic_action.setVisible(independent)
        self.intro_action.setVisible(independent)
        self.more_button.setVisible(independent)
        self.status_label.setVisible(not independent)
        self.footer_layout.removeWidget(self.details_button)
        self.performance_editor.preview_actions.removeWidget(self.details_button)
        target = self.performance_editor.preview_actions if independent else self.footer_layout
        target.addWidget(self.details_button)
        self.details_button.show()
        self.footer_panel.setVisible(not independent)
        self.group_hint.setText("用于待机、思考等事件，通常不会影响演出效果" if independent else
                                "")
        if independent and not self._intro_pending:
            self._intro_pending = True
            QTimer.singleShot(0, self._show_initial_v3_help)

    def _show_initial_v3_help(self) -> None:
        """延后首次介绍，避免在窗口布局和模型信息建立之前弹出。"""
        self._intro_pending = False
        if not self._closed and self.isVisible() and self.all_motion_data is not None:
            try:
                show_v3_intro_once(self, self.current_model_version, d_sakiko_config)
            except (OSError, RuntimeError) as exc:
                self.message_box.append(f"首次使用提示的已读状态保存失败：{exc}")

    def show_v3_help(self) -> None:
        """允许已读用户从更多菜单再次查看说明。"""
        QMessageBox.information(self, V3_INTRO_TITLE, V3_INTRO_TEXT, QMessageBox.Ok)

    def showEvent(self, event: QShowEvent) -> None:
        """首次显示窗口时补发可能在隐藏状态被跳过的介绍。"""
        super().showEvent(event)
        if self.current_model_version == "v3" and not self._intro_pending:
            self._intro_pending = True
            QTimer.singleShot(0, self._show_initial_v3_help)

    def send_preview(self, command: dict[str, object] | str) -> None:
        """给预览请求加上身份，过期模型或旧请求的回执不覆盖当前状态。"""
        request_id = uuid.uuid4().hex
        if isinstance(command, dict) and command.get("type") == "expression_editor":
            self.motion_queue.put(dict(command, request_id=request_id,
                model_path=str(self.current_model_json_path.resolve()) if self.current_model_json_path else ""))
            return
        self._preview_request_id = request_id
        self._preview_started_at = time.monotonic()
        payload = dict(command) if isinstance(command, dict) else {"file": command}
        payload.update(request_id=request_id, model_path=str(self.current_model_json_path.resolve()) if self.current_model_json_path else "")
        self.status_label.setText("正在加载预览…")
        self.performance_editor.set_preview_status("正在加载预览…")
        self.motion_queue.put(payload)

    def poll_preview_results(self) -> None:
        """只展示最新请求的真实结果，并对渲染器无响应给出明确反馈。"""
        if self.preview_result_queue is not None:
            while True:
                try:
                    result = self.preview_result_queue.get_nowait()
                except Empty:
                    break
                if isinstance(result, dict) and result.get("type") == "expression_editor":
                    self.performance_editor.expressionResult.emit(result)
                    continue
                if isinstance(result, dict) and result.get("request_id") == self._preview_request_id:
                    self._preview_request_id = None
                    message = str(result.get("message") or "预览失败")
                    self.status_label.setText(message)
                    self.performance_editor.set_preview_status(message)
                    self.message_box.append(message)
                    if not result.get("ok") and self.current_model_version == "v3":
                        self.details_button.setChecked(True)
        if self._preview_request_id and time.monotonic() - self._preview_started_at > 30:
            self._preview_request_id = None
            message = "预览窗口未响应，请检查模型是否加载完成"
            self.status_label.setText(message)
            self.performance_editor.set_preview_status(message)
            self.message_box.append(message)
            if self.current_model_version == "v3":
                self.details_button.setChecked(True)

    def open_automatic_settings(self) -> None:
        """在次级窗口保留 V3 事件和回退分组，沿用原来的编辑流程。"""
        dialog = QDialog(self)
        dialog.setWindowTitle("旧版动作组设置")
        layout = QVBoxLayout(dialog)
        self.pages.removeWidget(self.groups_panel)
        layout.addWidget(self.groups_panel)
        # 从堆叠页移出的面板仍保留隐藏状态，需要在新容器中显式显示。
        self.groups_panel.show()
        close = QPushButton("完成")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        place_viewer_window(dialog, QSize(740, 570))
        try:
            dialog.exec_()
        finally:
            layout.removeWidget(self.groups_panel)
            self.pages.addWidget(self.groups_panel)
            self.pages.setCurrentWidget(self.performance_editor)
            dialog.deleteLater()

    def _selection_key(self):
        return (self.current_char_index, self.editing_form) if self.character_list[self.current_char_index].character_folder_name == "sakiko" else self.current_char_index

    def select_form(self):
        form = self.form_selector.currentData()
        if form == self.editing_form:
            return
        if not self.performance_editor.confirm_leave():
            self.form_selector.blockSignals(True)
            self.form_selector.setCurrentIndex(self.form_selector.findData(self.editing_form))
            self.form_selector.blockSignals(False)
            return
        self.editing_form = form
        self.load_suitable_model()
        self.change_char_queue.put({"type": "select_character", "index": self.current_char_index,
            "model_path": str(self.current_model_json_path) if self.current_model_json_path else None})

    def open_mask_actions(self):
        if not self.current_model_json_path or not self.performance_editor.confirm_leave():
            return
        from ui.components.live2d_mask_editor import MaskActionsDialog
        dialog = MaskActionsDialog(self.current_model_json_path, self.send_preview, self)
        dialog.exec_()
        dialog.deleteLater()

    def load_suitable_model(self):
        """
        根据 self.use_default_model 和 self.extra_model_name 的值，加载合适的模型。
        """
        if self.use_default_model.get(self._selection_key(), True):
            self.load_model(None)
        else:
            self.load_model(self.extra_model_name.get(self._selection_key()))

    def use_default_model_for_current_character(self):
        """
        切换显示模块使用当前角色的默认模型，并且更新类属性，保存这一设置
        """
        self.use_default_model[self._selection_key()] = True
        self.extra_model_name[self._selection_key()] = None
        self.load_model(None)

    def use_extra_model_for_current_character(self, extra_model_name):
        """
        切换为使用当前角色的 extra_model 中的模型，并且更新类属性，保存这一设置

        :param extra_model_name: 要使用的 extra_model 名称。
        """
        self.use_default_model[self._selection_key()] = False
        self.extra_model_name[self._selection_key()] = extra_model_name
        self.load_model(extra_model_name)

    def _find_model_json_in_folder(self, folder_path: pathlib.Path) -> pathlib.Path | None:
        catalog = Live2DModelCatalog(
            pathlib.Path(project_root) / "live2d_related",
            pathlib.Path(project_root),
        )
        resolved_folder = folder_path.resolve()
        for option in catalog.list_options(self.current_char_base_folder_name, form=self.editing_form):
            if option.available and option.model_directory.resolve() == resolved_folder:
                return option.model_json_path
        return None

    def _get_motion_groups(self) -> dict:
        if self.all_motion_data is None:
            return {}
        if self.current_model_version == "v3":
            file_references = self.all_motion_data.get("FileReferences", {})
            if not isinstance(file_references, dict):
                return {}
            motions = file_references.get("Motions", {})
        else:
            motions = self.all_motion_data.get("motions", {})
        if isinstance(motions, dict):
            return motions
        return {}

    def _motion_file_key(self) -> str:
        return "File" if self.current_model_version == "v3" else "file"

    def _get_motion_file_name(self, motion: object) -> str:
        if not isinstance(motion, dict):
            return ""
        file_name = motion.get(self._motion_file_key(), "")
        return file_name if isinstance(file_name, str) else ""

    def _set_motion_file_name(self, motion: object, file_name: str) -> None:
        if isinstance(motion, dict):
            motion[self._motion_file_key()] = file_name

    def _current_motion_suffix(self) -> str:
        return ".motion3.json" if self.current_model_version == "v3" else ".mtn"

    def load_model(self, extra_model_name=None):
        """按角色、编辑形态与模型选择进入通用 V2/V3 编辑流程。"""
        self._preview_request_id = None
        self.current_model_version = None
        self.all_motion_data = None
        self.left_selected_motion_path = None
        self.right_selected_group = None
        self.right_selected_index = None
        selected = self.character_list[self.current_char_index]
        self.current_char_base_folder_name = selected.character_folder_name
        self.btn_change_costume.setEnabled(True)
        catalog = Live2DModelCatalog(pathlib.Path(project_root) / "live2d_related", pathlib.Path(project_root))
        options = catalog.list_options(self.current_char_base_folder_name, form=self.editing_form)
        option = next((item for item in options if
                       (item.is_default if extra_model_name is None else item.model_directory.name == extra_model_name)), None)
        self.current_model_json_path = option.model_json_path if option else None
        if option is None and extra_model_name is None and selected.character_folder_name != "sakiko" and selected.live2d_json:
            self.current_model_json_path = pathlib.Path(selected.live2d_json)
        self.current_char_folder_path = self.current_model_json_path.parent if self.current_model_json_path else pathlib.Path(project_root) / "live2d_related" / selected.character_folder_name
        self.all_mnt_display.clear()
        self.current_mnt_display.clear()
        if self.current_model_json_path is None or not self.current_model_json_path.exists():
            self.message_box.append("没有找到当前模型的 model.json/model3.json，无法编辑。")
            self.update_button_states(disable_all=True)
            self.performance_editor.load_model(None)
            self._refresh_model_page()
            return
        from live2d_support.model_normalizer import normalize_live2d_model_for_project
        result = normalize_live2d_model_for_project(str(self.current_model_json_path))
        if not result.ok:
            self.message_box.append("模型配置无法读取：" + result.error_message)
            self.update_button_states(disable_all=True)
            self.performance_editor.load_model(None)
            self._refresh_model_page()
            return
        self.current_model_version = detect_live2d_runtime_version(str(self.current_model_json_path))
        with open(self.current_model_json_path, encoding="utf-8") as model_file:
            self.all_motion_data = json.load(model_file)
        self.performance_editor.load_model(self.current_model_json_path)
        self.refresh_current_mnt_display(preserve_scroll=False)
        self.update_button_states()
        self._refresh_model_page()
        self.all_mnt_title.setText("可用动作 · 点击预览")
        suffix = self._current_motion_suffix()
        paths = sorted(path.resolve() for path in self.current_char_folder_path.rglob("*" + suffix) if path.is_file())
        for index, path in enumerate(paths, start=1):
            self.all_mnt_display.append(f'<a href="{path.as_posix()}" style="text-decoration:none; color:#426BAA;">{index}. {path.name}</a>\n')
        self.all_mnt_display.verticalScrollBar().setValue(0)

    def change_char(self) -> None:
        """通过搜索列表直接选择角色，而不是依次循环。"""
        dialog = CharacterPicker(self.character_list, self.current_char_index, self)
        if dialog.exec_() == QDialog.Accepted:
            index = dialog.selected_index()
            if index is not None:
                self.select_character(index)
        dialog.deleteLater()

    def select_character(self, index: int) -> bool:
        """保护说明草稿后切换指定角色，并同步渲染进程的实际索引。"""
        if index == self.current_char_index or not 0 <= index < len(self.character_list):
            return False
        if not self.performance_editor.confirm_leave():
            return False
        self.current_char_index = index
        icon = getattr(self.character_list[index], "icon_path", None)
        self.setWindowIcon(QIcon(icon) if icon else QIcon())
        self.message_box.clear()
        self.load_suitable_model()
        self.change_char_queue.put({"type": "select_character", "index": index,
                                    "model_path": str(self.current_model_json_path) if self.current_model_json_path else None})
        return True

    def change_costume(self):
        """
        弹出管理对话框，允许用户选择并切换切换角色的服装
        """
        dialog = ChangeL2DModelWindow(
            self.current_char_base_folder_name,
            self._on_change_costume_confirmed,
            form=self.editing_form if self.current_char_base_folder_name == "sakiko" else None,
        )
        dialog.exec()

    def _on_change_costume_confirmed(self, option: Live2DModelOption) -> None:
        """根据共享目录选项切换 Viewer 当前角色的服装。"""
        new_model_path = str(option.model_json_path)
        if self.current_model_json_path and self.current_model_json_path.resolve() == option.model_json_path.resolve():
            return
        if not self.performance_editor.confirm_leave():
            return
        logger.info("用户选择了新的服装模型路径：%s", new_model_path)
        if option.is_default:
            self.use_default_model_for_current_character()
        else:
            self.use_extra_model_for_current_character(option.model_directory.name)

        self.message_box.append(f"已切换到角色 {self.character_list[self.current_char_index].character_name} 的新服装模型。")
        self.change_char_queue.put(new_model_path)

    def play_motion_cur_mtn_ver(self,motion_path):

        # 右侧栏点击：可能是组标题，也可能是某个具体动作
        url_str = motion_path.toString()
        if self.all_motion_data is None:
            return

        if url_str.startswith("group:"):
            group_key = url_str.split(":", 1)[1]
            self.right_selected_group = group_key
            self.right_selected_index = None
            self.message_box.clear()
            self.message_box.append(f"已选中动作组：{motion_group_display_title(group_key)}\n"
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
                motion_filename = self._get_motion_file_name(self._get_motion_groups()[group_key][index])
            except Exception:
                return

            abs_path = (self.current_char_folder_path / motion_filename).resolve().as_posix()
            self.send_preview(abs_path)
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
            self.send_preview(url_str)
            self.message_box.clear()
            self.message_box.append(f"当前预览动作：\n{os.path.basename(url_str)}")

    def play_motion_all_mtn_ver(self,motion_path):

        motion_path=motion_path.toString()
        self.left_selected_motion_path = motion_path
        self.message_box.clear()
        self.message_box.append(f"当前选中动作（左侧）：\n{os.path.basename(motion_path)}")

        if os.path.exists(motion_path):
            self.send_preview(motion_path)

        self.update_button_states()

    def update_button_states(self, disable_all: bool = False):
        if disable_all:
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
        motions = self._get_motion_groups()
        for group_key, motion_values in motions.items():
            title = motion_group_display_title(group_key)
            if group_key == self.right_selected_group and self.right_selected_index is None:
                title_color = "#294E87"
            else:
                title_color = "#263449"

            html_parts.append(
                f'<div style="margin-top:8px;">'
                f'<a href="group:{group_key}" style="text-decoration:none; color:{title_color}; font-weight:bold;">'
                f'【{title}】'
                f'</a>'
                f'</div>'
            )

            for idx, motion in enumerate(motion_values):
                file_name = self._get_motion_file_name(motion)
                if group_key == self.right_selected_group and self.right_selected_index == idx:
                    item_color = "#294E87"
                else:
                    item_color = "#526985"
                html_parts.append(
                    f'<div style="margin-left:12px;">'
                    f'<a href="item:{group_key}:{idx}" style="text-decoration:none; color:{item_color};">'
                    f'★{idx + 1}：{file_name}'
                    f'</a></div>'
                )

        self.current_mnt_display.setHtml("\n".join(html_parts))

        if scroll_bar is not None:
            scroll_bar.setValue(saved_pos)

    def _write_motion_json(self) -> None:
        """保存标准动作组，并刷新独立演出目录中的可用资源。"""
        if self.all_motion_data is None or self.current_model_json_path is None:
            return
        with open(self.current_model_json_path, 'w', encoding='utf-8') as f:
            json.dump(self.all_motion_data, f, indent=4, ensure_ascii=False)
        self.performance_editor.load_model(self.current_model_json_path)

    def _generate_unique_motion_name(self, group_key: str) -> str:
        """生成动作 name：{group_key}_{n}，n 为从 1 开始的最小可用正整数。

        扫描整个 model.json 的 motions，确保 name 不重复。
        """
        if self.all_motion_data is None:
            return f"{group_key}_1"

        existing_names: set[str] = set()
        motions = self._get_motion_groups()
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

    def _make_motion_entry(self, file_name: str, reference_entry: Optional[dict], motion_name: str) -> dict:
        if self.current_model_version == "v3":
            return {"File": file_name}
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
        if self.all_motion_data is not None:
            self.left_selected_motion_path = old_left
            self.right_selected_group = old_group
            self.right_selected_index = old_index

            motions = self._get_motion_groups()
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
        target_list = self._get_motion_groups().get(group_key)
        if target_list is None:
            self.message_box.clear()
            self.message_box.append(f"动作组 '{group_key}' 不存在，无法添加！")
            return

        file_name = pathlib.Path(self.left_selected_motion_path).resolve().relative_to(self.current_char_folder_path.resolve()).as_posix()

        # 新动作的 name：{group}_{n}，n 取最小可用
        new_motion_name = self._generate_unique_motion_name(group_key) if self.current_model_version != "v3" else ""

        # 找一个参考 entry 来复制参数（fade 等）
        reference_entry = target_list[0] if len(target_list) > 0 else None
        new_entry = self._make_motion_entry(file_name, reference_entry, new_motion_name)

        if self.right_selected_index is None:
            # 选中的是组标题：追加到末尾
            target_list.append(new_entry)
            self.message_box.clear()
            self.message_box.append(f"已添加动作到组 {motion_group_display_title(group_key)}：{file_name}")
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
                f"已在组 {motion_group_display_title(group_key)} 中添加动作：{file_name}"
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
        target_list = self._get_motion_groups().get(group_key)
        if target_list is None or idx < 0 or idx >= len(target_list):
            self.message_box.clear()
            self.message_box.append("右侧选中动作无效，无法替换！")
            return

        file_name = pathlib.Path(self.left_selected_motion_path).resolve().relative_to(self.current_char_folder_path.resolve()).as_posix()
        self._set_motion_file_name(target_list[idx], file_name)
        self._write_motion_json()
        self.message_box.clear()
        self.message_box.append(
            f"已替换组 {motion_group_display_title(group_key)} 的第 {idx + 1} 个动作为：{file_name}"
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
        target_list = self._get_motion_groups().get(group_key)
        if target_list is None or idx < 0 or idx >= len(target_list):
            self.message_box.clear()
            self.message_box.append("右侧选中动作无效，无法删除！")
            return

        removed = target_list.pop(idx)
        removed_name = self._get_motion_file_name(removed) if isinstance(removed, dict) else str(removed)
        self._write_motion_json()

        # 删除后，将右侧选中移动到同组的一个合理位置
        if len(target_list) == 0:
            self.right_selected_index = None
        else:
            self.right_selected_index = min(idx, len(target_list) - 1)

        self.message_box.clear()
        self.message_box.append(
            f"已从组 {motion_group_display_title(group_key)} 删除动作：{removed_name}"
        )
        self._reload_after_change()

    def exit(self) -> None:
        """走统一关闭流程，保留未保存修改的保护。"""
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭前处理草稿，确认离开后通知渲染进程退出。"""
        if not self.performance_editor.confirm_leave():
            event.ignore()
            return
        self._closed = True
        self.result_timer.stop()
        self.change_char_queue.put("exit")
        event.accept()


if __name__ == "__main__":
    # 设置当前工作目录为脚本所在目录，避免相对路径问题
    os.chdir(os.path.abspath(os.path.dirname(__file__)))

    setup_logging()

    live2d_player = Live2DModule()
    get_char_attr = character.GetCharacterAttributes()
    model_characters = [
        one_character
        for one_character in get_char_attr.character_class_list
        if one_character.live2d_json
    ]
    if not model_characters:
        logger.error("没有已配置 Live2D 模型的角色，无法启动动作编辑器。")
        raise SystemExit(1)
    motion_queue = multiprocessing.Queue()
    change_char_queue=multiprocessing.Queue()
    preview_result_queue = multiprocessing.Queue()

    live2d_player.live2D_initialize(model_characters)

    configure_viewer_dpi()
    app = QApplication(sys.argv)

    # 如果出现加载字体问题，则忽略设置字体
    font_path = os.path.join(project_root, "font", "ft.ttf")
    font_id = QFontDatabase.addApplicationFont(os.path.abspath(font_path))  # 设置字体
    if font_id != -1:
        font_family = QFontDatabase.applicationFontFamilies(font_id)
        font = QFont(font_family[0], 12)
        app.setFont(font)

    window = ViewerGUI(model_characters, motion_queue, change_char_queue, preview_result_queue)
    desktop = preview_desktop_size(window.screen())
    desktop_w, desktop_h = desktop.width(), desktop.height()
    live2d_thread=multiprocessing.Process(target=live2d_player.play_live2d,args=(motion_queue,change_char_queue, desktop_w, desktop_h, get_log_queue(), preview_result_queue))

    live2d_thread.start()
    window.show()
    try:
        sys.exit(app.exec_())
    finally:
        shutdown_logging()
