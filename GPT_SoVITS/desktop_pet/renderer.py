"""Qt 原生 Live2D 控件，窗口生命周期与公共演出逻辑分离。"""

from __future__ import annotations

import queue
from typing import TYPE_CHECKING
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QRect
from PyQt5.QtGui import QImage, QMouseEvent, QWheelEvent
from PyQt5.QtWidgets import QOpenGLWidget, QWidget
from OpenGL.GL import (
    glBindFramebuffer,
    GL_FRAMEBUFFER,
    glClearColor,
    glClear,
    glViewport,
    glColorMask,
    glDisable,
    GL_SCISSOR_TEST,
    GL_COLOR_BUFFER_BIT,
    GL_DEPTH_BUFFER_BIT,
)
from live2d_support.runtime_session import Live2DRuntimeSession
from live2d_support.runtime_adapter import NullLive2DModel
from runtime.single_character_performance import SingleCharacterPerformance
from log import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from multiprocessing.queues import Queue as ProcessQueue
    from multiprocessing.sharedctypes import Synchronized


class PetRenderer(QOpenGLWidget):
    subtitleChanged = pyqtSignal(str)
    identityChanged = pyqtSignal(str)
    modelReady = pyqtSignal()
    failed = pyqtSignal(str)
    clicked = pyqtSignal()
    zoomRequested = pyqtSignal(float)
    dragFinished = pyqtSignal()

    def __init__(
        self,
        commands: queue.Queue[dict[str, object]],
        playback_events: ProcessQueue,
        motion_complete: Synchronized,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.commands, self.playback_events = commands, playback_events
        self.motion_complete = motion_complete
        self.session = None
        self.model = NullLive2DModel()
        self.performance = SingleCharacterPerformance()
        self.performance.on_event = playback_events.put
        self.performance.on_subtitle = self.subtitleChanged.emit
        self.performance.subtitle_hide_delay = 6.0
        self.interaction_requested = False
        self.interaction_blocked = False
        self.hit_bounds = self.rect()
        self.identity = ""
        self.target = None
        self.closed = False
        self.scale = 1.0
        self.offset = (0.0, 0.0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(33)
        self.press = None
        self.origin = None
        self.dragged = False

    def initializeGL(self):
        import pygame

        if not pygame.mixer.get_init():
            try:
                pygame.mixer.init()
            except pygame.error as error:
                logger.warning("音频设备不可用：%s", error)
        self.session = Live2DRuntimeSession()
        self.context().aboutToBeDestroyed.connect(self.release_context)

    def release_context(self):
        if self.session is None:
            return
        self.makeCurrent()
        self.performance.stop()
        self.model.dispose()
        self.model = NullLive2DModel()
        self.session.close()
        self.session = None
        self.doneCurrent()

    def load_target(self, event):
        self.target = dict(event)
        self.identity = str(event.get("character_name") or "角色")
        self.identityChanged.emit(self.identity)
        # 工具可以在同一回复中换装；换模型不能丢弃未回执的音频段。
        if not self.performance.busy and not self.performance.thinking:
            self.performance.stop()
        self.model.dispose()
        self.model = NullLive2DModel()
        path = event.get("model_json")
        self.performance.if_sakiko = (
            event.get("character_folder_name") == "sakiko" or self.identity == "祥子"
        )
        self.performance.sakiko_state = bool(
            event.get("sakiko_state", self.performance.sakiko_state)
        )
        from runtime.character_presentation import SAKIKO_COSTUME

        if self.performance.if_sakiko and self.performance.sakiko_state and path:
            path = SAKIKO_COSTUME
        try:
            if path:
                self.model = self.session.create_model(str(path))
                self.model.SetAutoBlinkEnable(True)
                self.model.SetAutoBreathEnable(True)
                self.scale = 1.0
                self.offset = (0.0, 0.0)
                self.model.SetScale(self.scale)
                self.model.SetOffset(*self.offset)
            else:
                self.failed.emit(f"{self.identity} · 未配置模型")
        except Exception:
            logger.exception("桌宠模型加载失败：%s", path)
            self.failed.emit(f"{self.identity} · 模型加载失败")
        QTimer.singleShot(0, self.modelReady.emit)

    def paintGL(self) -> None:
        if self.closed:
            return
        try:
            # 即使窗口隐藏也由宿主 tick 消费命令；绘图时始终有 current context。
            self.consume_commands()
            if self.interaction_requested:
                self.performance.play_interaction(
                    self.model, blocked=self.interaction_blocked
                )
                self.interaction_requested = False
            glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
            width, height = (
                round(self.width() * self.devicePixelRatioF()),
                round(self.height() * self.devicePixelRatioF()),
            )
            glViewport(0, 0, width, height)
            glDisable(GL_SCISSOR_TEST)
            glColorMask(True, True, True, True)
            glClearColor(0, 0, 0, 0)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            self.model.Resize(width, height)
            self.model.Update()
            self.performance.update_playback(self.model)
            self.motion_complete.value = not self.performance.busy
            self.model.Draw()
        except Exception as error:
            logger.exception("桌宠帧绘制失败")
            self.failed.emit(str(error))
            # 降级为无模型，后续帧仍推进音频队列和完成回执。
            try:
                self.model.dispose()
            finally:
                self.model = NullLive2DModel()

    def consume_commands(self):
        for _ in range(128):
            try:
                event = self.commands.get_nowait()
            except queue.Empty:
                break
            if not isinstance(event, dict):
                continue
            if self.performance.command(event, self.model):
                continue
            kind = event.get("type")
            if kind == "switch_live2d":
                self.load_target(event)
            elif kind == "character_state":
                from runtime.character_presentation import apply_sakiko_state

                def replace(previous, path):
                    previous.dispose()
                    try:
                        loaded = self.session.create_model(path)
                        loaded.SetAutoBlinkEnable(True)
                        loaded.SetAutoBreathEnable(True)
                        QTimer.singleShot(0, self.modelReady.emit)
                        return loaded
                    except Exception:
                        logger.exception("角色专属模型切换失败")
                        self.failed.emit(f"{self.identity} · 模型加载失败")
                        return NullLive2DModel()

                self.model = apply_sakiko_state(
                    self.performance,
                    self.model,
                    event.get("value"),
                    (self.target or {}).get("model_json"),
                    replace,
                )
            elif kind == "start_talking":
                self.model.StartRandomMotion("talking_motion", 4, position="C")
            elif kind == "stop_talking":
                self.model.StartRandomMotion("idle_motion", 1, position="C")
            elif kind == "switch_l2d_fps":
                self.timer.setInterval(
                    max(8, 1000 // max(1, int(event.get("fps", 30))))
                )

    def tick_hidden(self):
        if self.session is not None and not self.isVisible():
            self.makeCurrent()
            self.consume_commands()
            self.model.Update()
            self.performance.update_playback(self.model)
            self.motion_complete.value = not self.performance.busy
            self.doneCurrent()

    def fit_and_bounds(self):
        """只在换模型/尺寸后标定一次，返回稳定轮廓，避免面板随呼吸抖动。"""
        import numpy as np

        if isinstance(self.model, NullLive2DModel):
            return QRect(
                60, 80, max(80, self.width() - 120), max(80, self.height() - 120)
            )
        self.makeCurrent()
        self.scale = 0.5
        self.model.SetScale(self.scale)
        self.model.SetOffset(0, 0)

        def bounds():
            image = self.grabFramebuffer().convertToFormat(QImage.Format_RGBA8888)
            ptr = image.constBits()
            ptr.setsize(image.byteCount())
            a = np.frombuffer(ptr, np.uint8).reshape(
                image.height(), image.bytesPerLine()
            )
            alpha = a[:, : image.width() * 4].reshape(image.height(), image.width(), 4)[
                :, :, 3
            ]
            y, x = np.nonzero(alpha > 10)
            if not len(x):
                return None
            d = self.devicePixelRatioF()
            return QRect(
                round(x.min() / d),
                round(y.min() / d),
                max(1, round((x.max() - x.min() + 1) / d)),
                max(1, round((y.max() - y.min() + 1) / d)),
            )

        rect = bounds()
        if rect is not None:
            factor = min(
                (self.width() - 48) / rect.width(), (self.height() - 64) / rect.height()
            )
            self.scale *= min(factor, 4.0)
            self.model.SetScale(self.scale)
            rect = bounds()
            if rect is not None:
                self.offset = (
                    (self.width() / 2 - rect.center().x()) / self.width() * 2,
                    -(self.height() / 2 - rect.center().y()) / self.height() * 2,
                )
                self.model.SetOffset(*self.offset)
                rect = bounds()
        return rect or self.rect().adjusted(30, 30, -30, -30)

    def request_interaction(self) -> None:
        """将点击动作延迟到持有 OpenGL 上下文的绘制阶段。"""
        if not self.interaction_blocked:
            self.interaction_requested = True
            self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """仅在角色范围内将鼠标或触控板滚动转换为连续缩放。"""
        if not self.hit_bounds.contains(event.pos()):
            event.ignore()
            return
        pixels = event.pixelDelta().y()
        steps = pixels / 40.0 if pixels else event.angleDelta().y() / 120.0
        if steps:
            self.zoomRequested.emit(1.05 ** max(-10.0, min(10.0, steps)))
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """只允许从角色可见范围内开始点击或拖动。"""
        if event.button() == Qt.LeftButton and self.hit_bounds.contains(event.pos()):
            self.press, self.origin = event.globalPos(), self.window().pos()
            self.dragged = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.press is not None and event.buttons() & Qt.LeftButton:
            delta = event.globalPos() - self.press
            if delta.manhattanLength() > 6:
                self.dragged = True
                self.window().move(self.origin + delta)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """区分单击与拖动结束，避免移动桌宠时误触动作。"""
        if (
            event.button() == Qt.LeftButton
            and self.press is not None
            and not self.dragged
        ):
            self.clicked.emit()
        if self.press is not None and self.dragged:
            self.dragFinished.emit()
        self.press = None

    def shutdown(self):
        self.closed = True
        self.timer.stop()
        self.release_context()
        import pygame

        if pygame.mixer.get_init():
            pygame.mixer.quit()
