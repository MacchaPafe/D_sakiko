"""小剧场正式播放状态机；舞台和句内计时仍由既有演出模块执行。"""
from __future__ import annotations

from copy import deepcopy
import time


PLAYBACK_HELP_TEXT = "按空格键自动播放，鼠标单击播放本句"


class MultiCharPlayback:
    """管理待播位置、自动推进和临时单句，不依赖 Qt、Pygame 或 OpenGL。"""

    def __init__(self, stage, performance, music, start_sentence, reset_round, send,
                 occupied=lambda: False, show_hint=lambda: None, clock=None):
        self.stage = stage
        self.performance = performance
        self.music = music
        self.start_sentence = start_sentence
        self.reset_round = reset_round
        self.send = send
        self.occupied = occupied
        self.show_hint = show_hint
        self.clock = clock or time.monotonic
        self._hint_due = None
        self.messages = []
        self.session = ""
        self.revision = -1
        self.phase = "empty"
        self.auto = False
        self.current_index = None
        self.pending_index = 0
        self.blocks = set()
        self.reservation = None
        self.single = False
        self.load_failed = False
        self._single_return = None
        self._restore_scene = None
        self._fresh_start = True
        self._last_report = None

    @property
    def active(self):
        return self.phase in ("transition", "playing")

    @property
    def busy(self):
        return bool(self.active or self.auto or self.blocks or self.reservation is not None
                    or self.occupied())

    @property
    def idle(self):
        return not self.busy

    def state(self):
        return {"type": "multi_char_playback", "session": self.session,
                "revision": self.revision, "phase": self.phase, "auto": self.auto,
                "current_index": self.current_index, "pending_index": self.pending_index,
                "busy": self.busy, "single": self.single, "load_failed": self.load_failed}

    def report(self, **extra):
        """只推送改变后的状态；命令应答即使状态相同也始终返回。"""
        state = self.state()
        if extra or state != self._last_report:
            self._last_report = dict(state)
            self.send(dict(state, **extra))

    def _ack(self, command, accepted=True, error=None):
        extra = {"action": command.get("action"), "request_id": command.get("request_id"),
                 "accepted": accepted}
        if error:
            extra["error"] = error
        self.report(**extra)
        return accepted

    def matches(self, session, revision):
        return session == self.session and revision == self.revision

    def accepts_load(self, command):
        revision = command.get("revision")
        return (isinstance(command.get("session"), str) and isinstance(revision, int)
                and not isinstance(revision, bool) and revision >= self.revision
                and (revision > self.revision or command.get("session") == self.session))

    @staticmethod
    def _messages(value):
        if not isinstance(value, list) or any(not isinstance(turn, dict) for turn in value):
            raise ValueError("剧本消息格式无效")
        return deepcopy(value)

    def fail_load(self, command, error):
        """失败装载也接管新版身份；拒绝旧演出，并允许之后重新装载恢复。"""
        if not self.accepts_load(command):
            return False
        self._hint_due = None
        self.music.stop()
        self.performance.playing = False
        self.performance.paused = False
        self.performance.cursor = None
        self.stage.cancel()
        self.session = command["session"]
        self.revision = command["revision"]
        self.messages = []
        self.phase = "empty"
        self.current_index = None
        self.pending_index = 0
        self.auto = False
        self.reservation = None
        self.single = False
        self._single_return = None
        self._restore_scene = None
        self._fresh_start = True
        self.load_failed = True
        reasons = command.get("blocked_reasons")
        if isinstance(reasons, list):
            self.blocks = {reason for reason in reasons if reason in ("generation", "audio", "audio_fill")}
        return self._ack(command, False, error)

    def command(self, command):
        """旧版本命令静默丢弃；同版忙碌请求立即拒绝，不保留执行队列。"""
        action = command.get("action")
        if action == "load":
            if not self.accepts_load(command):
                return False
        elif not self.matches(command.get("session"), command.get("revision")):
            return False
        try:
            if action == "load":
                messages = self._messages(command.get("messages"))
                self.stop()
                self.messages = messages
                self.session = command["session"]
                self.revision = command["revision"]
                self.current_index = None
                self.pending_index = 0
                self.phase = "ready" if messages else "empty"
                self.auto = False
                self.reservation = None
                self.blocks = {reason for reason in command.get("blocked_reasons", self.blocks)
                               if reason in ("generation", "audio", "audio_fill")}
                self._fresh_start = True
                self._restore_scene = None
                self.load_failed = False
                if not command.get("preserve_scene", False):
                    self.stage.new_playback()
                    if messages:
                        self.stage.show(self.stage.resolve(messages, 0))
            elif action in ("single", "auto_from"):
                if self.load_failed:
                    return self._ack(command, False, "剧本尚未成功装载，请重新选择对话")
                if not self.idle:
                    return self._ack(command, False, "请等待当前播放结束")
                messages = self._messages(command["messages"]) if "messages" in command else self.messages
                index = command.get("index")
                if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(messages):
                    return self._ack(command, False, "所选台词已不存在，请重新选择")
                self.messages = messages
                if action == "single":
                    self._begin_single(index)
                else:
                    self.auto = True
                    self._restore_scene = None
                    self._start(index, first=True)
            elif action == "block":
                reason = command.get("reason")
                if reason not in ("generation", "audio", "audio_fill"):
                    return self._ack(command, False, "未知的播放占用原因")
                self._hint_due = None
                # 编排内部音频合成也占用正式输入，不取消它自己的预览或场景。
                if self.active and not self.occupied():
                    self.stop()
                self.blocks.add(reason)
            elif action == "unblock":
                self.blocks.discard(command.get("reason"))
            elif action == "reserve_editor":
                if self.load_failed:
                    return self._ack(command, False, "剧本尚未成功装载，请重新选择对话")
                if not self.idle:
                    return self._ack(command, False, "请等待当前播放结束")
                self._hint_due = None
                self.reservation = command.get("request_id") or "editor"
            elif action == "release_editor":
                reservation = command.get("reservation_id")
                if reservation is None or reservation == self.reservation:
                    self.reservation = None
            else:
                return self._ack(command, False, "未知的正式播放命令")
        except (ValueError, TypeError, KeyError) as exc:
            if action == "load":
                return self.fail_load(command, str(exc))
            return self._ack(command, False, str(exc))
        return self._ack(command)

    def stop(self):
        """外部停止取消语音和黑场；空格关闭自动不走此入口。"""
        self._hint_due = None
        self.music.stop()
        self.performance.playing = False
        self.performance.paused = False
        self.performance.cursor = None
        self.stage.cancel()
        self.stage.show(self.stage.state)
        if self.single:
            self._finish_single()
        elif self.active and self.current_index is not None:
            self.pending_index = self.current_index
        self.auto = False
        self.phase = ("empty" if not self.messages else
                      "ended" if self.pending_index >= len(self.messages) else "ready")

    def cancel_hint(self):
        """进入其他舞台操作时立即取消提示，不依赖下一帧仍处于占用状态。"""
        self._hint_due = None

    def click(self):
        if not self.idle or not self.messages:
            return False
        self._continue()
        self.report()
        return True

    def toggle_auto(self):
        if self.blocks or self.reservation is not None or self.occupied() or self.single or not self.messages:
            return False
        self.auto = not self.auto
        if self.auto and not self.active:
            self._continue()
        self.report()
        return True

    def _continue(self):
        if self.phase == "ended" or self.pending_index >= len(self.messages):
            self._restore_scene = None
            self._start(0, first=True)
            return
        if self._restore_scene is not None:
            state, base = self._restore_scene
            self.stage.base_background = base
            self.stage.show(state)
            self.reset_round()
            self._restore_scene = None
        self._start(self.pending_index, first=self._fresh_start)

    def _start(self, index, first=False):
        self._hint_due = None
        if first:
            self.reset_round()
            self.stage.new_playback()
        self._fresh_start = False
        self.current_index = index
        self.pending_index = index
        turn = self.messages[index]
        context = turn.get("scene_context") or {}
        messages = context.get("messages") or self.messages
        scene_index = context.get("index", index)
        target = self.stage.resolve(messages, scene_index)
        self.phase = "transition"
        self.stage.begin(target, turn.get("scene_before"), first=first)
        if not self.stage.busy:
            self._begin_sentence()

    def _begin_sentence(self):
        index = self.current_index
        self.phase = "playing"
        if not self.single:
            self.pending_index = index + 1
        self.start_sentence(self.messages[index])

    def _begin_single(self, index):
        # 连续临时回放仍须保留最初的正式舞台，不能把上次临时画面当起点。
        scene = self._restore_scene or (deepcopy(self.stage.state), self.stage.base_background)
        self._single_return = (self.phase, self.current_index, self.pending_index, self._fresh_start, scene)
        self.single = True
        self.auto = False
        self._start(index, first=True)

    def _finish_single(self):
        self.single = False
        self.phase, self.current_index, self.pending_index, self._fresh_start, self._restore_scene = self._single_return
        self._single_return = None
        self.auto = False

    def tick(self):
        """由宿主先推进舞台和句内时钟，再统一决定句末状态及下一句。"""
        if self.occupied() or self.blocks or self.reservation is not None:
            self._hint_due = None
        if not self.occupied() and not self.blocks and self.reservation is None:
            if self.phase == "transition" and not self.stage.busy:
                self._begin_sentence()
            elif self.phase == "playing" and not self.performance.playing and not self.music.get_busy():
                if self.single:
                    self._finish_single()
                elif self.pending_index >= len(self.messages):
                    self.phase = "ended"
                    if self.auto:
                        self._hint_due = self.clock() + 10.0
                    self.auto = False
                else:
                    self.phase = "ready"
                    if self.auto:
                        self._continue()
        if self._hint_due is not None and self.clock() >= self._hint_due:
            self._hint_due = None
            if self.phase == "ended" and self.idle:
                self.show_hint()
        self.report()
