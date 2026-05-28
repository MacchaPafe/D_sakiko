from __future__ import annotations

import os,sys
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
sys.path.insert(0, script_dir)

from queue import Queue, Empty
import threading
import multiprocessing
import time

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont, QFontDatabase

import character
import dp_local2
import audio_generator
import live2d_module
import qtUI
from chat.chat import get_chat_manager

from chat_flow.audio_synthesizer import ChatAudioSynthesizer
from chat_flow.dispatcher import DpChatCancellationProvider
from chat_flow.dispatcher import MainResponseDispatcher
from chat_flow.dispatcher import MotionValuePlaybackGate
from chat_flow.dispatcher import QueueThinkingIndicator
from chat_flow.events import ApplicationQuitUiMessageEvent
from chat_flow.events import DispatcherInputEvent
from chat_flow.events import DispatcherInputEventType
from chat_flow.state_updater import ConversationStateUpdater
from log import setup_logging, get_logger, get_log_queue, setup_worker_logging, shutdown_logging

import faulthandler

faulthandler.enable(file=open("faulthandler_log.txt", "a"), all_threads=True)

# 日志记录
main_logger = get_logger(__name__)

main_response_dispatcher: MainResponseDispatcher | None = None


def get_character_by_name(character_name: str) -> character.CharacterAttributes | None:
    """按角色名查找角色对象。"""
    for one_character in characters:
        if one_character.character_name == character_name:
            return one_character
    return None


def get_main_response_dispatcher() -> MainResponseDispatcher:
    """创建或复用主回复 dispatcher。"""
    global main_response_dispatcher
    if main_response_dispatcher is None:
        main_response_dispatcher = MainResponseDispatcher(
            input_queue=text_queue,
            conversation_ui_queue=dp2qt_queue,
            status_queue=QT_message_queue,
            playback_audio_queue=audio_file_path_queue,
            emotion_queue=emotion_queue,
            completion_queue=is_audio_play_complete,
            ui_message_queue=QT_message_queue,
            live2d_exit_queue=emotion_queue,
            audio_worker_shutdown=audio_gen.shutdown_worker,
            live2d_process_join=tr1.join,
            synthesizer=ChatAudioSynthesizer(audio_gen),
            state_updater=ConversationStateUpdater(dp_chat.chat_manager),
            character_resolver=get_character_by_name,
            playback_gate=MotionValuePlaybackGate(motion_complete_value),
            cancellation_provider=DpChatCancellationProvider(dp_chat),
            thinking_indicator=QueueThinkingIndicator(is_text_generating_queue),
        )
    return main_response_dispatcher


def handle_model_response_payload(payload: dict[str, object]) -> None:
    """处理结构化模型回复事件，逐段合成语音并通知 UI。"""
    get_main_response_dispatcher().process_model_response(payload)


def main_thread():

    while True:
        time.sleep(1)   #防GIL
        dispatcher = get_main_response_dispatcher()
        dispatcher.run_loop_once()
        if dispatcher.should_stop:
            break


if __name__=='__main__':
    # 强制设置多进程实现为 spawn
    multiprocessing.set_start_method('spawn', force=True)
    setup_logging()

    # 添加本文件的目录到导入 Path
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))

    from qconfig import d_sakiko_config

    main_logger.info("数字小祥程序...")
    get_all=character.GetCharacterAttributes()
    characters=get_all.character_class_list

    # 初始化全局 ChatManager（自动处理旧版聊天记录迁移）
    chat_manager = get_chat_manager()
    chat_manager.ensure_default_single_character_chat(characters)

    #模块间传参队列
    text_queue=Queue()
    emotion_queue=multiprocessing.Queue()
    audio_file_path_queue=multiprocessing.Queue()
    is_audio_play_complete=Queue()
    is_text_generating_queue=multiprocessing.Queue()
    dp2qt_queue=Queue()
    qt2dp_queue=Queue()
    QT_message_queue=Queue()
    char_is_converted_queue=multiprocessing.Queue()
    change_char_queue=multiprocessing.Queue()
    # Live2D 跨进程通信
    live2d_text_queue=multiprocessing.Queue()  # 用于传递要显示的文本
    is_display_text_value=multiprocessing.Value('b', True)  # 是否显示文本
    motion_complete_value=multiprocessing.Value('b', True)  # 动作是否完成

    dp_chat=dp_local2.DSLocalAndVoiceGen(characters, chat_manager)

    audio_gen=audio_generator.AudioGenerate(log_queue=get_log_queue())


    audio_gen.initialize(characters,QT_message_queue)

    def get_timestamp_from_filename(filepath):
        """
        从路径中提取时间戳，只为读取字体文件使用
        假设文件名格式为: .../custom_font_1715668823.ttf
        """
        try:
            # 1. 只取文件名: "custom_font_1715668823.ttf"
            filename = os.path.basename(filepath)

            # 2. 去掉后缀: "custom_font_1715668823"
            name_no_ext = os.path.splitext(filename)[0]

            # 3. 取最后一个下划线后面的部分: "1715668823"
            timestamp_str = name_no_ext.split('_')[-1]

            return int(timestamp_str)
        except (IndexError, ValueError):
            return 0  # 如果文件名格式不对，返回0，当作最老的处理
    font_path = ''
    import glob

    font_dir = os.path.join(project_root, 'font')
    font_files = glob.glob(os.path.join(font_dir, 'custom_font_*.*'))
    if not font_files:
        font_path = os.path.join(font_dir, 'msyh.ttc')  # 默认字体路径
    else:
        # 比文件名里的数字大小，而不是比文件系统的元数据
        font_path = max(font_files, key=get_timestamp_from_filename)
        #print(f"检测到最新导入的字体: {font_path}")

        # --- 清理旧文件 (逻辑不变) ---
        for f in font_files:
            if os.path.abspath(f) != os.path.abspath(font_path):
                try:
                    os.remove(f)
                except Exception:
                    pass  # 删不掉就跳过

    qt_app = QApplication(sys.argv)
    from PyQt5.QtWidgets import QDesktopWidget  # 设置qt窗口位置，与live2d对齐

    desktop_w = QDesktopWidget().screenGeometry().width()
    desktop_h = QDesktopWidget().screenGeometry().height()
    screen_w_mid = int(0.5 * desktop_w)
    screen_h_mid = int(0.5 * desktop_h)

    # 不传递 characters 给子进程，在子进程中重新创建，避免 Windows 下 pickle 序列化截断问题
    # live2d 模块（该模块为不同进程）
    # 在 MacOS 下，所有的 NSWindow（Qt 窗口）只能在独立进程中创建，不可以在子线程中创建窗口。
    # 由于 live2d 模块会创建一个窗口，我们必须使用多进程而非多线程实现并行。
    main_logger.info("加载Live2D界面中...")
    tr1=multiprocessing.Process(target=live2d_module.run_live2d_process,args=(emotion_queue,audio_file_path_queue,is_text_generating_queue,char_is_converted_queue,change_char_queue,live2d_text_queue,is_display_text_value,motion_complete_value, desktop_w, desktop_h, get_log_queue()))
    # LLM 生成模块（该模块为不同线程）
    tr2=threading.Thread(target=dp_chat.text_generator,args=(text_queue,
                                                             is_audio_play_complete,
                                                             is_text_generating_queue,
                                                             dp2qt_queue,
                                                             qt2dp_queue,
                                                             QT_message_queue,
                                                             char_is_converted_queue,
                                                             change_char_queue,
                                                             audio_gen))
    # 主要的循环线程
    tr3=threading.Thread(target=main_thread)
    tr1.start()
    tr2.start()
    tr3.start()

    qt_win = qtUI.ChatGUI(dp2qt_queue=dp2qt_queue,
                          qt2dp_queue=qt2dp_queue,
                          QT_message_queue=QT_message_queue
                          , characters=characters,
                          dp_chat=dp_chat,
                          audio_gen=audio_gen, live2d_text_queue=live2d_text_queue,
                          is_display_text_value=is_display_text_value, motion_complete_value=motion_complete_value,
                          emotion_queue=emotion_queue, audio_file_path_queue=audio_file_path_queue,
                          emotion_model=None,
                          change_char_queue=change_char_queue)

    font_id = QFontDatabase.addApplicationFont(os.path.abspath(font_path))  # 设置字体
    # font_id = -1 表示 Qt 无法加载给定的字体。此时，不设置程序的字体。
    if font_id != -1:
        font_family = QFontDatabase.applicationFontFamilies(font_id)
        font = QFont(font_family[0], 12)
        qt_app.setFont(font)

    qt_win.move(screen_w_mid, int(screen_h_mid - 0.35 * desktop_h))  # 因为窗口高度设置的是0.7倍桌面宽

    qt_win.show()
    qt_app.exec_()

    # 尝试退出所有子程序。
    # 由于有些程序可能已经退出，所以使用 try-except 来捕获异常，防止程序崩溃。
    try:
        try:
            character_name = dp_chat.get_current_character().character_name
        except Exception:
            character_name = ""
        text_queue.put(DispatcherInputEvent(
            DispatcherInputEventType.EXIT,
            {"character_name": character_name},
        ).to_dict())
    except Exception:
        pass
    try:
        # DeepSeek 推理线程
        qt2dp_queue.put('bye')
    except Exception:
        pass
    try:
        # live2d 播放进程
        change_char_queue.put('exit')
        emotion_queue.put('bye')
    except Exception:
        pass
    try:
        # 主窗口
        QT_message_queue.put(ApplicationQuitUiMessageEvent())
    except Exception:
        pass
    try:
        # 语音生成 worker
        audio_gen.shutdown_worker()
    except Exception:
        pass

    # 理论上讲 main_thread 函数中已经调用过 tr1.join，等待过 live2d 进程结束；这里再调用一次不是必要的，但也没有副作用。
    tr1.join(timeout=3)
    if tr1.is_alive():
        try:
            tr1.terminate()
            tr1.join(timeout=3)
        except Exception:
            pass
    tr2.join()
    tr3.join()

    shutdown_logging()

    if_delete = d_sakiko_config.delete_audio_cache_on_exit.value

    if if_delete:
        folder_path = '../reference_audio/generated_audios_temp'    #删除音频缓存

        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
# 修改库的源码：
# ffmpeg/_run.py 196
# jieba_fast/__init__.py 117/136/150/168/170
# AR/models/t2s_model.py 560/736/875
# text/chinese2.py 27
# runtime\Lib\site-packages\pygame\__init__.py 336
# AR\models\\t2s_model.py 845
# runtime\Lib\site-packages\live2d\\utils\lipsync.py 55 防止出现 nan，使程序崩溃
# runtime\Lib\site-packages/live2d/v2/core/graphics/draw_param_opengl.py 45 330 解决腮红变黑问题
# runtime/Lib/site-packages/live2d/v2/lapp_model.py 173
# runtime\Lib\site-packages\\faster_whisper\\transcribe.py
# inference_webui.py 大改
# inference_cli.py 大改
#
# 更改角色皮肤
# 可更改参考音频
# 可重新生成音频
