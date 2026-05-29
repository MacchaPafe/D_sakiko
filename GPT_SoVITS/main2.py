from __future__ import annotations

import os,sys
import uuid
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

import multiprocessing

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont, QFontDatabase

import character
import dp_local2
import audio_generator
import qtUI
from chat.chat import get_chat_manager
from chat.reminder_manager import ReminderManager
from chat.tool_calling import ToolCallingAgentRuntime
from chat.tool_calling import build_default_tool_registry
from chat.tool_calling import register_contextual_live2d_tools
from chat.tool_calling import register_contextual_lottery_tool
from chat.tool_calling import register_reminder_tool
from chat_flow.tool_context import ToolSideEffectPorts

from chat_flow.audio_scheduler import AudioScheduler
from chat_flow.audio_synthesizer import ChatAudioSynthesizer
from chat_flow.controller import ChatFlowController
from chat_flow.live2d_client import create_live2d_client_process
from chat_flow.turn_runner import ChatGenerationSession
from log import setup_logging, get_logger, get_log_queue, shutdown_logging

import faulthandler

faulthandler.enable(file=open("faulthandler_log.txt", "a"), all_threads=True)

# 日志记录
main_logger = get_logger(__name__)


if __name__=='__main__':
    # 强制设置多进程实现为 spawn
    multiprocessing.set_start_method('spawn', force=True)
    setup_logging()

    # 添加本文件的目录到导入 Path
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(script_dir)

    from qconfig import d_sakiko_config

    main_logger.info("数字小祥程序...")
    get_all = character.GetCharacterAttributes()
    characters = get_all.character_class_list

    # 初始化全局 ChatManager（自动处理旧版聊天记录迁移）
    chat_manager = get_chat_manager()
    chat_manager.ensure_default_single_character_chat(characters)

    qt_app = QApplication(sys.argv)
    dp_chat=dp_local2.DSLocalAndVoiceGen(characters, chat_manager)

    audio_gen=audio_generator.AudioGenerate(log_queue=get_log_queue())
    flow_controller = ChatFlowController(qt_parent=qt_app)

    audio_gen.initialize(characters,status_callback=flow_controller.status_callback)

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
    live2d_handle = create_live2d_client_process(
        desktop_w=desktop_w,
        desktop_h=desktop_h,
        log_queue=get_log_queue(),
    )
    live2d_handle.process.start()

    qt_win = qtUI.ChatGUI(characters=characters,
                          dp_chat=dp_chat,
                          audio_gen=audio_gen,
                          live2d_client=live2d_handle.client,
                          flow_controller=flow_controller)
    tool_registry = build_default_tool_registry()
    register_contextual_lottery_tool(tool_registry)
    register_contextual_live2d_tools(tool_registry)
    reminder_mgr = ReminderManager(
        trigger_callback=lambda text: qt_win._submit_text_via_controller(text, uuid.uuid4().hex)
    )
    register_reminder_tool(tool_registry, add_reminder_func=reminder_mgr.add_reminder)
    tool_runtime = ToolCallingAgentRuntime(
        llm_completion=dp_chat._completion_with_current_config,
        tool_registry=tool_registry,
        debug=True,
    )
    generation_session = ChatGenerationSession(
        completion=dp_chat._completion_with_current_config,
        model_name=dp_chat._current_litellm_model_name(),
        tool_runtime=tool_runtime,
        foreground_resolver=flow_controller,
        side_effect_ports=ToolSideEffectPorts(
            lottery=qt_win,
            live2d=qt_win,
            export_document=qt_win,
        ),
    )
    flow_controller.attach_generation_session(generation_session)
    audio_synthesizer = ChatAudioSynthesizer(audio_gen)
    audio_scheduler = AudioScheduler(
        synthesizer=audio_synthesizer,
        foreground_resolver=lambda chat_id: chat_id == flow_controller.visible_chat_id,
        status_callback=flow_controller.status_callback,
        completion_callback=qt_win.dispatch_audio_completed,
    )
    flow_controller.attach_audio_scheduler(audio_scheduler)

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
        # controller 统一收束文本、语音、Live2D 和最终保存。
        flow_controller.shutdown(
            chat_manager=chat_manager,
            live2d_client=live2d_handle.client,
            audio_worker=audio_gen,
            timeout_seconds=0.0,
        )
    except Exception:
        pass

    live2d_handle.process.join(timeout=3)
    if live2d_handle.process.is_alive():
        try:
            live2d_handle.process.terminate()
            live2d_handle.process.join(timeout=3)
        except Exception:
            pass

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
