from __future__ import annotations

import os
import queue as threading_queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from multiprocessing import Process, Queue
from queue import Empty
from typing import cast, TYPE_CHECKING, Optional, Any

from character import CharacterAttributes
from inference_cli import synthesize
from qconfig import d_sakiko_config


if TYPE_CHECKING:
    import queue

SILENCE_WAV_PATH = '../reference_audio/silent_audio/silence.wav'
SAKIKO_DEFAULT_LANGUAGE = '日文'

ref_audio_language_list = [
    "中文",
    "英文",
    "日文",
    "粤语",
    "韩文",
    "中英混合",
    "日英混合",
    "粤英混合",
    "韩英混合",
    "多语种混合",
    "多语种混合(粤语)"
]


WorkerCommand = dict[str, object]
WorkerResult = dict[str, object]


@dataclass
class VoiceTaskHandle:
    """表示一条已提交 worker 命令的等待句柄。"""

    request_id: str
    command_type: str
    done_event: threading.Event = field(default_factory=threading.Event)
    result: WorkerResult | None = None


class AudioGenerate:
    """管理主线程侧的语音生成流程与 worker 进程通信。"""

    def __init__(self, log_queue: Any | None = None) -> None:
        # gpt（t2s）模型路径
        self.GPT_model_file: str | None = ''
        # sovits 模型路径
        self.SoVITS_model_file: str | None = ''
        # 参考音频文件的相对路径
        self.ref_audio_file: str | None = ''
        # 白祥的参考音频路径（固定的）
        self.ref_audio_file_white_sakiko: str = '../reference_audio/sakiko/white_sakiko.wav'
        # 黑祥的参考音频路径（固定的）
        self.ref_audio_file_black_sakiko: str = '../reference_audio/sakiko/black_sakiko.wav'
        # 参考音频的语言类型
        self.ref_audio_language: str | None = ''
        # 存放参考音频文本的文件
        self.ref_text_file: str | None = ''
        # 存放白祥参考音频文本的文件路径（固定的）
        self.ref_text_file_white_sakiko: str = '../reference_audio/sakiko/reference_text_white_sakiko.txt'
        # 存放黑祥参考音频文本的文件路径（固定的）
        self.ref_text_file_black_sakiko: str = '../reference_audio/sakiko/reference_text_black_sakiko.txt'
        # 生成的所有音频文件的相对目录
        self.program_output_path: str = "../reference_audio/generated_audios_temp"
        # 生成的语音的速度
        self.speed: float = 1.0
        # 生成语音中，每两句话之间暂停的时间
        self.pause_second: float = 0.5
        # 生成出的音频文件的路径。默认为一个空白的 wav 文件（用作生成失败时的占位符）
        self.audio_file_path: str = SILENCE_WAV_PATH
        # 当前切换角色过程是否完成了
        self.is_change_complete: bool = False
        # 待生成文本的语言类型，一般从“中文”、“日英混合“、”中英混合“中选择。
        self.audio_language_choice: str = "中文"
        # 当前正在生成音频的角色是否是祥子
        self.if_sakiko: bool = False
        # 常见的名词（角色名称）-> 假名的映射，用于强制模型按照特定发音合成日语音频
        self.replacements_jap: dict[str, str] = {
            '豊川祥子': 'とがわさきこ',
            '祥子': 'さきこ',
            '三角初華': 'みすみういか',
            '初華': 'ういか',
            '若葉睦': 'わかばむつみ',
            '睦': 'むつみ',
            '八幡海鈴': 'やはたうみり',
            '海鈴': 'うみり',
            '祐天寺': 'ゆうてんじ',
            '若麦': 'にゃむ',
            '喵梦': 'にゃむ',
            '高松燈': 'たかまつともり',
            '燈': 'ともり',
            '灯': 'ともり',
            '椎名立希': 'しいなたき',
            '素世': 'そよ',
            '爽世': 'そよ',
            '千早愛音': 'ちはやアノン',
            '愛音': 'アノン',
            '要楽奈': 'かなめらーな',
            '楽奈': 'らーな',
            '春日影': 'はるひかげ',
            'Doloris': 'ドロリス',
            'Mortis': 'モーティス',
            'Timoris': 'ティモリス',
            'Amoris': 'アモーリス',
            'Oblivionis': 'オブリビオニス',
            'live': 'ライブ',
            'MyGO': 'まいご',
            'RiNG': 'リング',
            '戸山香澄': 'とやまかすみ',
            '户山香澄': 'とやまかすみ',
            '香澄': 'かすみ',
            '市ヶ谷有咲': 'いちがやありさ',
            '市谷有咲': 'いちがやありさ',
            '有咲': 'ありさ',
            '有咲ちゃん': 'ありさ',
            '牛込里美': 'うしごめりみ',
            '里美': 'りみ',
            'りみっち': 'りみりん',
            '山吹沙綾': 'やまぶきさあや',
            '沙綾': 'さあや',
            '沙绫': 'さあや',
            '沙綾さん': 'さあや',
            '花园多惠': 'はなぞのたえ',
            '花園多惠': 'はなぞのたえ',
            '多惠': 'おたえ',
            'たえちゃん': 'おたえ',
            'Afterglow': 'アフターグロウ',
            'Pastel*Palettes': 'パステルパレット',
            "Poppin'Party": 'ポッピンパーティー',
            'Roselia': 'ロゼリア',
            'STAR BEAT': 'スタービート',
            'RAISE A SUILEN': 'レイズアスイレン',
            'Morfonica': 'モルフォニカ',
            'SPACE': 'スペース',
            '六花': 'ろっか',
        }
        # 常见的名词（角色名称）-> 中文的映射
        self.replacements_chi: dict[str, str] = {
            'CRYCHIC': 'C团',
            'live': "演出",
            'RiNG': "ring",
            'Doloris': '初华',
            'Mortis': '睦',
            'Timoris': '海铃',
            'Amoris': '喵梦',
            'Oblivionis': '我',
            'MyGO': 'mygo',
            'ちゃん': ''
        }
        # 软件中所有角色的列表
        self.character_list: list[CharacterAttributes] = []
        # 主进程日志队列。正式 GUI 入口会传入；测试或独立脚本可保持 None。
        self.log_queue = log_queue

        # 和子进程通信的几条管道
        self.to_gptsovits_com_queue: Queue = Queue()
        self.from_gptsovits_com_queue: Queue = Queue()
        self.from_gptsovits_com_queue2: Queue = Queue()
        # 实际执行音频生成的子进程
        self.gptsovits_process: Process = Process(
            target=synthesize,
            args=(
                self.to_gptsovits_com_queue,
                self.from_gptsovits_com_queue,
                self.from_gptsovits_com_queue2,
                self.log_queue,
            ),
            daemon=True
        )
        # 当前是否是小剧场模式。True：是小剧场；False：是普通的对话
        self.if_small_theater_mode: bool = False
        # 祥子状态。True：黑祥 False：白祥
        self.sakiko_which_state: bool = True
        # 转发子进程的语音合成进度到界面中
        self.message_queue: queue.Queue | None = None
        # 主线程侧待发送给 worker 的命令队列
        self.pending_worker_commands: threading_queue.Queue[tuple[WorkerCommand, VoiceTaskHandle]] = (
            threading_queue.Queue()
        )
        # 已提交命令的等待句柄表
        self.command_handles: dict[str, VoiceTaskHandle] = {}
        self.command_handles_lock = threading.Lock()
        self.worker_dispatch_thread: threading.Thread | None = None
        # 语音模型调度状态：正式合成任务走 pending_worker_commands，预加载目标只保留最新一个。
        self.voice_schedule_lock = threading.Lock()
        self.loaded_voice_key: str | None = None
        self.loading_voice_key: str | None = None
        self.pending_preload_key: str | None = None
        self.pending_preload_command: WorkerCommand | None = None
        self.pending_preload_not_before: float = 0.0    # 预加载命令最早可执行的时间（单调时钟），用于防止模型加载过慢，加载时又切换了一次，角色导致正式合成时耗时反而更长（要加载两次模型）

        self._load_sakiko_default_reference_paths()

    def _load_sakiko_default_reference_paths(self) -> None:
        """
        读取祥子黑白状态的参考音频路径。
        只有在手动设置过参考音频时，这两个文件才会存在
        """
        black_path = os.path.join("../reference_audio", 'sakiko', "default_ref_audio_black.txt")
        if os.path.exists(black_path):
            with open(black_path, 'r', encoding='utf-8') as file:
                default_ref_audio_black_path = file.read().strip()
            if os.path.exists(default_ref_audio_black_path):
                self.ref_audio_file_black_sakiko = default_ref_audio_black_path
        white_path = os.path.join("../reference_audio", 'sakiko', "default_ref_audio_white.txt")
        if os.path.exists(white_path):
            with open(white_path, 'r', encoding='utf-8') as file:
                default_ref_audio_white_path = file.read().strip()
            if os.path.exists(default_ref_audio_white_path):
                self.ref_audio_file_white_sakiko = default_ref_audio_white_path

    @staticmethod
    def _read_reference_text_file(path: str | None) -> str | None:
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8") as file:
                return file.read().strip()
        except Exception:
            return None

    def initialize(self, character_list: list[CharacterAttributes], message_queue: queue.Queue) -> None:
        """初始化语音生成模块并启动 worker 进程。"""
        self.character_list = character_list
        self.message_queue = message_queue
        if not self.gptsovits_process.is_alive():
            self.gptsovits_process.start()
        self._ensure_worker_dispatch_thread()
        self.is_change_complete = True
        if self.character_list:
            self.request_preload_character(self.character_list[0])

    def _resolve_reference_materials(self, character: CharacterAttributes,
                                     sakiko_state: bool | None = None,
                                     emotion: str | None = None) -> tuple[str | None, str | None, str | None]:
        """
        根据显式角色对象和祥子状态解析参考音频材料。

        :returns: 三元组：参考音频文件路径、参考音频文本内容、参考音频的语言
        """
        if sakiko_state is None:
            sakiko_state: bool = self.sakiko_which_state

        if character.is_sakiko:
            if sakiko_state:
                return (
                    self.ref_audio_file_black_sakiko,
                    self._read_reference_text_file(self.ref_text_file_black_sakiko),
                    character.gptsovits_ref_audio_lan,
                )
            return (
                self.ref_audio_file_white_sakiko,
                self._read_reference_text_file(self.ref_text_file_white_sakiko),
                character.gptsovits_ref_audio_lan,
            )
        return character.get_reference_materials_for_emotion(emotion)

    @staticmethod
    def _build_model_payload(character: CharacterAttributes) -> dict[str, object]:
        """构造模型加载命令需要的最小 payload。"""
        return {
            "gpt_model_path": character.GPT_model_path,
            "sovits_model_path": character.sovits_model_path,
        }

    def build_generation_payload(
        self,
        text: str,
        audio_lang_choice: str,
        character: CharacterAttributes,
        sakiko_state: bool | None = None,
        segment_index: int | None = None,
        segment_total: int | None = None,
        emotion: str | None = None,
    ) -> dict[str, object]:
        """构造角色的一次语音生成 payload。"""
        ref_audio_path, prompt_text, ref_language = self._resolve_reference_materials(
            character=character,
            sakiko_state=sakiko_state,
            emotion=emotion,
        )
        payload = {
            "character_name": character.character_name,
            "gpt_model_path": character.GPT_model_path,
            "sovits_model_path": character.sovits_model_path,
            "ref_audio_path": ref_audio_path,
            "prompt_text": prompt_text,
            "ref_language": ref_language,
            "text": text,
            "text_language": audio_lang_choice,
            "output_dir": self.program_output_path,
            "speed_factor": self.speed,
            "fragment_interval": self.pause_second,
            "text_split_method": "cut0",
        }
        if segment_index is not None:
            payload["segment_index"] = segment_index
        if segment_total is not None:
            payload["segment_total"] = segment_total
        return payload

    def _build_synthesize_command(self, text: str, audio_lang_choice: str,
                                  character: CharacterAttributes,
                                  sakiko_state: bool | None = None,
                                  segment_index: int | None = None,
                                  segment_total: int | None = None,
                                  emotion: str | None = None) -> WorkerCommand:
        """构造发送给 worker 的语音生成命令。"""
        return {
            "type": "synthesize",
            "character_name": character.character_name,
            "character": character,
            "payload": self.build_generation_payload(
                text,
                audio_lang_choice,
                character,
                sakiko_state,
                segment_index,
                segment_total,
                emotion,
            ),
        }
#-------------------------------加载模型调度状态机相关的函数-------------------------------
    def _build_load_model_command(self, character: CharacterAttributes) -> WorkerCommand:
        """构造后台预加载语音模型的命令。"""
        return {
            "type": "load_model",
            "character_name": character.character_name,
            "character": character,
            "payload": self._build_model_payload(character),
            "silent": True,
        }

    def _clear_pending_preload_locked(self) -> None:
        """清空尚未提交给 worker 的语音模型预加载请求。"""
        self.pending_preload_key = None
        self.pending_preload_command = None
        self.pending_preload_not_before = 0.0

    def request_preload_character(self, character: CharacterAttributes | None) -> None:
        """
        请求后台预加载某个角色的语音模型。

        预加载不会进入正式 worker FIFO 队列，只在调度线程空闲且没有正式合成任务时执行。
        连续切换角色时，等待区只保留最新目标。
        """
        if not d_sakiko_config.enable_voice_model_preload.value:
            with self.voice_schedule_lock:
                self._clear_pending_preload_locked()
            return

        if character is None or not character.has_valid_voice_model():
            return

        key = character.character_name
        command = self._build_load_model_command(character)
        not_before = time.monotonic() + 1.5 #用户在同一角色页面停留1.5s后才正式预加载
        with self.voice_schedule_lock:
            if self.loading_voice_key is not None:
                if key == self.loading_voice_key:
                    self._clear_pending_preload_locked()
                else:
                    self.pending_preload_key = key
                    self.pending_preload_command = command
                    self.pending_preload_not_before = not_before
                return

            if key == self.loaded_voice_key:
                self._clear_pending_preload_locked()
                return

            self.pending_preload_key = key
            self.pending_preload_command = command
            self.pending_preload_not_before = not_before

    def _take_pending_preload_command(self) -> tuple[WorkerCommand, VoiceTaskHandle] | None:
        """在正式任务队列为空时，取出最新的预加载目标。"""
        with self.voice_schedule_lock:
            if not d_sakiko_config.enable_voice_model_preload.value:
                self._clear_pending_preload_locked()
                return None
            if self.pending_preload_command is None:
                return None
            if time.monotonic() < self.pending_preload_not_before:
                return None
            command = dict(self.pending_preload_command)
            key = self.pending_preload_key or cast(str, command.get("character_name", ""))
            self._clear_pending_preload_locked()
            self.loading_voice_key = key or None

        request_id = self._create_request_id()
        command["request_id"] = request_id
        handle = VoiceTaskHandle(
            request_id=request_id,
            command_type=cast(str, command.get("type", "")),
        )
        self._register_command_handle(handle)
        return command, handle

    def _mark_worker_command_started(self, command: WorkerCommand) -> None:
        """记录当前 worker 正在处理的语音模型目标。"""
        command_type = cast(str, command.get("type", ""))
        character_name = cast(str, command.get("character_name", ""))
        if character_name == "":
            return
        with self.voice_schedule_lock:
            if command_type == "load_model":
                self.loading_voice_key = character_name
            elif command_type == "synthesize":
                if self.loaded_voice_key != character_name:
                    self.loading_voice_key = character_name
                if self.pending_preload_key == character_name:
                    self._clear_pending_preload_locked()

    def _mark_worker_command_finished(self, command: WorkerCommand, result: WorkerResult) -> None:
        """根据 worker 返回结果更新语音模型调度状态。"""
        command_type = cast(str, command.get("type", ""))
        character_name = cast(str, command.get("character_name", ""))
        result_type = cast(str, result.get("type", ""))
        if character_name == "":
            return

        with self.voice_schedule_lock:
            if self.loading_voice_key == character_name:
                self.loading_voice_key = None

            if command_type == "load_model":
                if result_type == "ack" and bool(result.get("ok", False)):
                    self.loaded_voice_key = character_name
            elif command_type == "synthesize":
                if result_type == "synthesize_result":
                    self.loaded_voice_key = character_name

            if self.pending_preload_key == self.loaded_voice_key:
                self._clear_pending_preload_locked()
#-------------------------------加载模型调度状态机相关的函数（结束）-------------------------------
    @staticmethod
    def _create_request_id() -> str:
        """生成一条新的 worker 请求编号。"""
        return uuid.uuid4().hex

    def _ensure_worker_dispatch_thread(self) -> None:
        """确保主线程侧的 worker 调度线程已经启动。"""
        if self.worker_dispatch_thread is not None and self.worker_dispatch_thread.is_alive():
            return
        self.worker_dispatch_thread = threading.Thread(
            target=self._dispatch_worker_commands,
            daemon=True,
            name="AudioGenerateWorkerDispatch",
        )
        self.worker_dispatch_thread.start()

    def _register_command_handle(self, handle: VoiceTaskHandle) -> None:
        """登记一条待完成命令的等待句柄。"""
        with self.command_handles_lock:
            self.command_handles[handle.request_id] = handle

    def _pop_command_handle(self, request_id: str) -> None:
        """从等待句柄表中移除一条已经完成的句柄。"""
        with self.command_handles_lock:
            self.command_handles.pop(request_id, None)

    def _submit_worker_command_via_dispatch(self, command: WorkerCommand) -> VoiceTaskHandle:
        """将命令提交到主线程侧调度队列，并返回等待句柄。"""
        self._ensure_worker_dispatch_thread()
        queued_command = dict(command)

        request_id = cast(Optional[str], command.get("request_id"))
        # 如果 request_id 不知道为啥为空，就新分配一个
        if request_id is None or not isinstance(request_id, str) or request_id == '':
            request_id = self._create_request_id()
            queued_command["request_id"] = request_id

        handle = VoiceTaskHandle(
            request_id=request_id,
            command_type=cast(str, queued_command.get("type", "")),
        )
        self._register_command_handle(handle)
        self.pending_worker_commands.put((queued_command, handle))
        return handle

    def _send_worker_command(self, command: WorkerCommand) -> None:
        """
        向 worker 进程发送一条命令。

        :raises ValueError: 如果管道已经关闭（即子进程已经退出）
        """
        self.to_gptsovits_com_queue.put(command)

    def _drain_progress_messages(self) -> None:
        """转发 worker 侧的进度消息到主界面。"""
        while True:
            try:
                progress_message = self.from_gptsovits_com_queue2.get(block=False)
            except Empty:
                break
            else:
                message = ''
                if isinstance(progress_message, dict):
                    if cast(str, progress_message.get("type", "")) == "progress":
                        message = cast(str, progress_message.get("message", ""))
                elif isinstance(progress_message, str):
                    message = progress_message

                if message and self.message_queue is not None:
                    self.message_queue.put(message)

    def _try_get_worker_result(self) -> WorkerResult | None:
        """
        尝试读取一条 worker 返回结果。

        :raises ValueError: 如果管道已经关闭（即子进程已经退出）
        """
        try:
            result = self.from_gptsovits_com_queue.get(block=False)
        except Empty:
            return None
        return result

    def _set_handle_result(self, handle: VoiceTaskHandle, result: WorkerResult) -> None:
        """写入一条等待句柄的最终结果。"""
        handle.result = result
        handle.done_event.set()
        self._pop_command_handle(handle.request_id)

    @staticmethod
    def _matches_request_id(result: WorkerResult, request_id: str) -> bool:
        """判断 worker 返回结果是否属于指定请求。"""
        result_request_id = cast(str, result.get("request_id", ""))
        if result_request_id:
            return result_request_id == request_id
        return True

    def _dispatch_worker_commands(self) -> None:
        """持续从调度队列中取出命令，并串行发送给 worker。"""
        while True:
            try:
                command, handle = self.pending_worker_commands.get(timeout=0.1)
            except Empty:
                preload_item = self._take_pending_preload_command()
                if preload_item is None:
                    self._drain_progress_messages()
                    continue
                command, handle = preload_item

            command_type = cast(str, command.get("type", ""))
            if command_type == '':
                self._set_handle_result(
                    handle,
                    {
                        "type": "error",
                        "command": "",
                        "request_id": handle.request_id,
                        "character_name": cast(str, command.get("character_name", "")),
                        "message": "无效的 worker 命令类型",
                    },
                )
                continue

            self._mark_worker_command_started(command)
            try:
                self._send_worker_command(command)
            except ValueError:
                error_result = {
                    "type": "error",
                    "command": command_type,
                    "request_id": handle.request_id,
                    "character_name": cast(str, command.get("character_name", "")),
                    "message": "语音合成模块已经退出（崩溃）",
                }
                self._mark_worker_command_finished(command, error_result)
                self._set_handle_result(handle, error_result)
                if command_type == "shutdown":
                    break
                continue

            if command_type == "shutdown":
                self.gptsovits_process.join()
                self._set_handle_result(
                    handle,
                    {
                        "type": "ack",
                        "command": "shutdown",
                        "request_id": handle.request_id,
                        "character_name": cast(str, command.get("character_name", "")),
                        "ok": True,
                    },
                )
                break

            while True:
                self._drain_progress_messages()
                result = self._try_get_worker_result()
                if result is None:
                    time.sleep(0.1)
                    continue
                if self._matches_request_id(result, handle.request_id):
                    self._mark_worker_command_finished(command, result)
                    self._set_handle_result(handle, result)
                    break

    def _wait_for_handle_result(self, handle: VoiceTaskHandle) -> WorkerResult:
        """等待某条命令句柄完成，并返回 worker 结果。"""
        while True:
            if handle.done_event.wait(0.1):
                if handle.result is not None:
                    return handle.result
                return {
                    "type": "error",
                    "command": handle.command_type,
                    "request_id": handle.request_id,
                    "character_name": "",
                    "message": "worker 没有返回结果",
                }
            if self.worker_dispatch_thread is not None and not self.worker_dispatch_thread.is_alive():
                return {
                    "type": "error",
                    "command": handle.command_type,
                    "request_id": handle.request_id,
                    "character_name": "",
                    "message": "worker 调度线程已退出",
                }

    def _wait_for_ack(self, handle: VoiceTaskHandle, command_name: str) -> bool:
        """等待某个管理命令的确认结果。"""
        result = self._wait_for_handle_result(handle)
        result_type = cast(str, result.get("type", ""))
        if result_type == "ack" and cast(str, result.get("command", "")) == command_name:
            return cast(bool, result.get("ok", False))
        return False

    def _wait_for_status(self, handle: VoiceTaskHandle) -> WorkerResult:
        """等待 worker 返回当前角色运行时状态。"""
        result = self._wait_for_handle_result(handle)
        if cast(str, result.get("type", "")) == "status":
            return result
        return dict()

    @staticmethod
    def _format_worker_error_result(result: WorkerResult) -> str:
        """把 worker 返回的错误结果格式化为可定位的一行异常消息。"""
        command = cast(str, result.get("command", ""))
        character_name = cast(str, result.get("character_name", ""))
        request_id = cast(str, result.get("request_id", ""))
        message = cast(str, result.get("message", ""))
        return (
            "语音合成 worker 返回错误 "
            f"command={command or '<unknown>'} "
            f"character={character_name or '<unknown>'} "
            f"request_id={request_id or '<unknown>'}: "
            f"{message or '<empty message>'}"
        )

    def _wait_for_synthesize_result(self, handle: VoiceTaskHandle) -> str:
        """等待 worker 返回一次语音生成结果。"""
        result = self._wait_for_handle_result(handle)
        result_type = cast(str, result.get("type", ""))
        if result_type == "synthesize_result":
            return cast(str, result.get("output_wav_path", SILENCE_WAV_PATH))
        if result_type == "error":
            raise RuntimeError(self._format_worker_error_result(result))
        return SILENCE_WAV_PATH

    def submit_voice_task(self, command: WorkerCommand) -> VoiceTaskHandle:
        """提交一条 worker 命令，并返回可等待的句柄。"""
        return self._submit_worker_command_via_dispatch(command)

    def wait_for_task(self, handle: VoiceTaskHandle) -> WorkerResult:
        """等待某条已提交命令完成，并返回结果字典。"""
        return self._wait_for_handle_result(handle)

    def _normalize_text_for_generation(
        self,
        text: str,
        audio_language_choice: str,
        character: CharacterAttributes,
    ) -> tuple[str, bool]:
        """将待合成文本转换为适合送入语音模块的内容。"""
        if audio_language_choice == '日英混合':
            text = re.sub(r'CRYCHIC', 'クライシック', text, flags=re.IGNORECASE)
            text = re.sub(r'\bave\s*mujica\b', 'アヴェムジカ', text, flags=re.IGNORECASE)
            if character.character_name == "爱音":
                text = re.sub(r'立希', 'りっき', text, flags=re.IGNORECASE)
            else:
                text = re.sub(r'立希', 'りっき', text, flags=re.IGNORECASE)
            for key, value in self.replacements_jap.items():
                text = re.sub(re.escape(key), value, text, flags=re.IGNORECASE)
        else:
            for key, value in self.replacements_chi.items():
                text = re.sub(re.escape(key), value, text, flags=re.IGNORECASE)

        pattern = r'^[^A-Za-z0-9\u3040-\u30FF\u4E00-\u9FFF]+'
        text = re.sub(pattern, '', text)
        text = text.replace(' ', '')
        text = text.replace('...', '，')
        if text == '' or text == '不能送去合成':
            return '今年', False
        return text, True

    def generate_audio(
        self,
        text: str,
        character: CharacterAttributes,
        sakiko_state: bool,
        audio_lan_choice: str,
        segment_index: int | None = None,
        segment_total: int | None = None,
        emotion: str | None = None,
    ) -> VoiceTaskHandle:
        """提交一条语音生成命令，并返回等待句柄。"""
        command = self._build_synthesize_command(
            text,
            audio_lan_choice,
            character=character,
            sakiko_state=sakiko_state,
            segment_index=segment_index,
            segment_total=segment_total,
            emotion=emotion,
        )
        return self.submit_voice_task(command)

    def shutdown_worker(self) -> None:
        """通过调度线程关闭 GPT-SoVITS worker 进程。"""
        if not self.gptsovits_process.is_alive():
            return
        handle = self.submit_voice_task({"type": "shutdown"})
        self._wait_for_ack(handle, "shutdown")

    def generate_audio_for_character_sync(
        self,
        text: str,
        character: CharacterAttributes,
        sakiko_state: bool,
        audio_lan_choice: str,
        segment_index: int | None = None,
        segment_total: int | None = None,
        emotion: str | None = None,
    ) -> str:
        """按显式给定角色配置同步生成语音。"""
        if not character.has_valid_voice_model():
            self.audio_file_path = SILENCE_WAV_PATH
            return SILENCE_WAV_PATH

        normalized_text, is_valid_text = self._normalize_text_for_generation(
            text,
            audio_lan_choice,
            character,
        )
        if not is_valid_text:
            self.audio_file_path = SILENCE_WAV_PATH
            return SILENCE_WAV_PATH

        handle = self.generate_audio(
            normalized_text,
            character,
            sakiko_state,
            audio_lan_choice,
            segment_index=segment_index,
            segment_total=segment_total,
            emotion=emotion,
        )
        self.audio_file_path = self._wait_for_synthesize_result(handle)
        return self.audio_file_path

    def clean_text_for_audio(self, text: str) -> str:
        """清洗文本使其适合送入语音合成模块。"""
        cleaned = re.sub(r"（.*?）", "", text)
        cleaned = re.sub(r"\(.*?\)", "", cleaned)
        cleaned = re.sub(r"\[.*?]", "", cleaned)
        cleaned = cleaned.replace('「', '')
        cleaned = cleaned.replace('」', '')
        cleaned = cleaned.strip()
        pattern = r'^[^A-Za-z0-9\u3040-\u30FF\u4E00-\u9FFF]+'
        cleaned = re.sub(pattern, '', cleaned)
        cleaned = cleaned.replace(' ', '')
        cleaned = cleaned.replace('...', '，')
        if not cleaned or bool(re.fullmatch(r'[\W_]+', cleaned)):
            cleaned = '不能送去合成'
        for key, value in self.replacements_jap.items():
            cleaned = re.sub(re.escape(key), value, cleaned, flags=re.IGNORECASE)
        return cleaned

    def generate_audio_sync(
        self,
        text: str,
        character: CharacterAttributes,
        sakiko_state: bool,
        audio_lan_choice: str,
        emotion: str | None = None,
    ) -> str:
        """同步生成音频，用于重新生成音频功能。"""
        if not character.has_valid_voice_model():
            return SILENCE_WAV_PATH

        if audio_lan_choice == '日英混合':
            text = re.sub(r'CRYCHIC', 'クライシック', text, flags=re.IGNORECASE)
            text = re.sub(r'\bave\s*mujica\b', 'アヴェムジカ', text, flags=re.IGNORECASE)
            text = re.sub(r'立希', 'りっき', text, flags=re.IGNORECASE)
        text = self.clean_text_for_audio(text)
        if text == '' or text == '不能送去合成':
            return SILENCE_WAV_PATH

        handle = self.generate_audio(text, character, sakiko_state, audio_lan_choice, emotion=emotion)
        return self._wait_for_synthesize_result(handle)
