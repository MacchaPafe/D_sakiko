"""应用级展示形态和托盘生命周期，不由聊天窗口是否可见决定。"""

from __future__ import annotations

import queue
import threading
import time
from PyQt5.QtCore import QObject, QTimer, Qt
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QMessageBox
from desktop_pet.window import PetWindow
from qconfig import d_sakiko_config


class DesktopController(QObject):
    def __init__(
        self,
        window,
        router,
        playback_events,
        character_events,
        runtime,
        motion_complete,
    ):
        super().__init__(window)
        self.window, self.router, self.runtime = window, router, runtime
        self.playback_events, self.character_events = playback_events, character_events
        self.motion_complete = motion_complete
        self.pet = None
        self.pet_mode = False
        self.exiting = False
        self._cleaned = False
        self._farewell_complete = False
        self._shutdown_deadline = 0.0
        self._audio_shutdown_complete = threading.Event()
        self._audio_shutdown_thread: threading.Thread | None = None
        self.pet_commands = queue.Queue()
        self.tray = QSystemTrayIcon(window.windowIcon(), self)
        menu = QMenu(window)
        menu.addAction("显示桌宠", self.show_pet)
        menu.addAction("打开聊天窗口", self.show_chat)
        menu.addAction("切换桌宠 / 普通形态", self.toggle_mode)
        menu.addSeparator()
        menu.addAction("退出程序", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_chat()
            if reason == QSystemTrayIcon.Trigger
            else None
        )
        self.tray.setToolTip("数字小祥")
        self.tray.show()
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.drain_events)
        self.poll.start(30)
        self.voice_poll = QTimer(self)
        self.voice_poll.timeout.connect(self.update_voice_activity)
        self.voice_poll.start(1000)
        d_sakiko_config.voice_output_enabled.valueChanged.connect(self.update_voice_activity)
        self.update_voice_activity()

    def update_voice_activity(self) -> None:
        """同步持久化语音偏好，并报告桌宠空闲状态。"""
        if self.exiting:
            return
        self.window.dp_chat.if_generate_audio = (
            bool(d_sakiko_config.voice_output_enabled.value)
            and self.window.current_character.has_valid_voice_model()
        )
        self.window.audio_gen.update_voice_activity(
            pet_mode=self.pet_mode,
            busy=bool(
                self.runtime.busy or not self.motion_complete.value
                or getattr(self.window, "regen_thread", None) is not None
                or self.window.voice_input.state in {"recording", "transcribing"}
            ),
        )

    def drain_events(self) -> None:
        if self.exiting:
            for _ in range(128):
                try:
                    event = self.playback_events.get_nowait()
                except queue.Empty:
                    break
                if isinstance(event, dict) and event.get("type") == "farewell_complete":
                    self._farewell_complete = True
            if (
                self._farewell_complete and self._audio_shutdown_complete.is_set()
                or time.monotonic() >= self._shutdown_deadline
            ):
                self.cleanup()
            return
        internal = self.runtime.take_internal(self.window.current_chat_id)
        if internal is not None:
            self.window._send_user_message_payload(internal, consume_draft=False)
        process = self.router.process
        if (
            not self.pet_mode
            and process is not None
            and not process.is_alive()
            and self.runtime.busy
        ):
            self.window.cancel_active_turn()
            self.window._set_message_box_text(
                "角色窗口已关闭，当前回复已停止。请重新切换展示形态。"
            )
        for _ in range(128):
            try:
                self.runtime.playback_event(self.playback_events.get_nowait())
            except queue.Empty:
                break
        if self.pet_mode and self.pet is not None:
            # 黑白祥/面具请求仍由原引擎产生，由当前演出宿主解释。
            try:
                value = self.character_events.get_nowait()
            except queue.Empty:
                return
            self.pet_commands.put({"type": "character_state", "value": value})

    def show_chat(self) -> None:
        if self.pet is not None:
            self.pet.clear_status()
            self.pet.collapse()
        if self.window.isMinimized():
            self.window.showNormal()
        else:
            self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def show_pet(self) -> None:
        """恢复桌宠显示，保留用户拖动后的位置。"""
        if not self.pet_mode:
            self.toggle_mode()
        elif self.pet is not None:
            self.pet.show()

    def can_switch_mode(self) -> bool:
        """统一菜单与实际切换的忙碌判断，避免录音途中销毁输入宿主。"""
        return not (
            self.runtime.busy
            or not self.motion_complete.value
            or getattr(self.window, "regen_thread", None) is not None
            or self.window.voice_input.state in {"recording", "transcribing"}
        )

    def switch_to_window(self) -> None:
        """将桌宠切回普通形态，重复请求只打开聊天窗口。"""
        if self.pet_mode:
            self.toggle_mode()
        else:
            self.show_chat()

    def start(self) -> None:
        """根据启动偏好只创建一个演出宿主，桌宠失败时回退并展示聊天窗口。"""
        if bool(d_sakiko_config.start_in_pet_mode.value):
            try:
                self._enter_pet_mode()
                return
            except Exception as error:
                message = f"无法以桌宠形态启动：{error}"
                try:
                    self.router.start_normal()
                except Exception as restore_error:
                    message += f"\n普通角色窗口也未能启动：{restore_error}"
                self.show_chat()
                QMessageBox.warning(self.window, "桌宠启动失败", message)
                return
        try:
            self.router.start_normal()
        except Exception as error:
            self.show_chat()
            QMessageBox.warning(self.window, "角色窗口启动失败", str(error))
            return
        self.show_chat()

    def _enter_pet_mode(self) -> None:
        """初始化并验证桌宠后切换路由，启动与手动切换共用同一流程。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            raise RuntimeError("当前系统没有可用托盘，无法保证隐藏后恢复。")
        if self.router.latest_model is not None:
            self.router.latest_model["sakiko_state"] = self.window.dp_chat.sakiko_state
        pet = PetWindow(
            self.window,
            self.pet_commands,
            self.playback_events,
            self.motion_complete,
        )
        try:
            pet.openChat.connect(self.show_chat)
            pet.switchToWindow.connect(self.switch_to_window, Qt.QueuedConnection)
            pet.quitRequested.connect(self.quit)
            pet.show()
            pet.renderer.grabFramebuffer()
            if not pet.renderer.isValid():
                raise RuntimeError("无法创建 Qt OpenGL 上下文。")
            self.router.use_pet(self.pet_commands)
        except Exception:
            pet.shutdown()
            raise
        self.pet = pet
        self.pet_mode = True
        self.window.hide()
        self.update_voice_activity()

    def toggle_mode(self) -> None:
        if self.exiting:
            return
        if not self.can_switch_mode():
            message = "请等待当前回复、播放或录音完成后切换形态。"
            self.window._set_message_box_text(message)
            if self.pet is not None and not self.window.isVisible():
                self.pet.show_status(message)
            return
        if self.router.latest_model is not None:
            self.router.latest_model["sakiko_state"] = self.window.dp_chat.sakiko_state
        if not self.pet_mode:
            try:
                self._enter_pet_mode()
            except Exception as error:
                if self.router.process is None:
                    try:
                        self.router.start_normal()
                    except Exception as restore_error:
                        error = RuntimeError(
                            f"{error}\n普通窗口恢复失败：{restore_error}"
                        )
                self.show_chat()
                QMessageBox.warning(self.window, "形态切换失败", str(error))
        else:
            try:
                self.router.start_normal()
            except Exception as error:
                QMessageBox.warning(self.window, "形态切换失败", str(error))
                return
            self.pet.shutdown()
            self.pet = None
            self.pet_mode = False
            self.show_chat()
        self.update_voice_activity()

    def chat_changed(self) -> None:
        if self.pet is not None:
            self.pet.clear_status()
            self.pet._subtitle("")
            self.pet.binding.switch(self.window.current_chat_id)

    def quit(self) -> None:
        """保存后并行启动告别演出和语音清理，让 Qt 继续处理绘制事件。"""
        if self.exiting:
            return
        from runtime.storage_ui import save_before_close

        if not save_before_close(self.window.chat_manager, self.window):
            return
        self.exiting = True
        self._shutdown_deadline = time.monotonic() + 10.0
        self.voice_poll.stop()
        self.runtime.close()
        self.window.setEnabled(False)
        if self.pet is not None:
            self.pet.collapse()
        self.router.put({"type": "farewell"})
        if self.pet is None and (self.router.process is None or not self.router.process.is_alive()):
            self._farewell_complete = True
        self._audio_shutdown_thread = threading.Thread(target=self._shutdown_audio, daemon=True)
        self._audio_shutdown_thread.start()

    def _shutdown_audio(self) -> None:
        """在后台等待语音 worker 退出，完成状态供界面轮询。"""
        try:
            self.window.audio_gen.shutdown_worker()
        finally:
            self._audio_shutdown_complete.set()

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self.exiting = True
        self.poll.stop()
        self.voice_poll.stop()
        for worker in (self.window.get_response_thread, self.window.get_message_thread):
            worker.requestInterruption()
            worker.wait(500)
        self.runtime.close()
        self.window.voice_input.close()
        self.window.remote_attachment_manager.shutdown()
        # 推理线程可能正在阻塞调用；退出不得无限等待同步 worker 应答。
        audio = self.window.audio_gen
        shutdown = self._audio_shutdown_thread
        if shutdown is None:
            shutdown = threading.Thread(target=self._shutdown_audio, daemon=True)
            self._audio_shutdown_thread = shutdown
            shutdown.start()
        shutdown.join(2)
        process = getattr(audio, "gptsovits_process", None)
        if process is not None and process.is_alive():
            process.terminate()
            process.join(2)
        if self.pet is not None:
            self.pet.shutdown()
            self.pet = None
        self.router.close()
        self.tray.hide()
        QApplication.instance().quit()
