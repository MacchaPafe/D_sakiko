import os,sys
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
sys.path.insert(0, script_dir)

from queue import Queue
import threading
import multiprocessing
import time
import json
import re

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont, QFontDatabase

import character
import dp_local2
import audio_generator
import inference_emotion_detect
import live2d_module
import qtUI
from chat.chat import get_chat_manager

from emotion_enum import EmotionEnum

import faulthandler

faulthandler.enable(file=open("faulthandler_log.txt", "a"), all_threads=True)

def merge_short_sentences(sentences, min_length=25):
    merged = []
    i = 0
    n = len(sentences)

    while i < n:
        current = sentences[i]
        # 如果当前句子已经足够长，直接加入
        if len(current) >= min_length:
            merged.append(current)
            i += 1
        else:
            # 否则，尝试合并后续句子，直到足够长或没有更多句子
            j = i + 1
            while j < n and len(current) < min_length:
                current += sentences[j]
                j += 1
            merged.append(current)
            i = j  # 跳过已合并的句子

    return merged


def clean_text_for_audio(text):
    """清洗文本使其适合送入语音合成模块：移除括号内容、中括号、书名号等"""
    cleaned = re.sub(r"（.*?）", "", text)
    cleaned = re.sub(r"\(.*?\)", "", cleaned)
    cleaned = re.sub(r"\[.*?]", "", cleaned)
    cleaned = cleaned.replace('「', '')
    cleaned = cleaned.replace('」', '')
    cleaned = cleaned.strip()
    if not cleaned or bool(re.fullmatch(r'[\W_]+', cleaned)):
        cleaned = '不能送去合成'
    return cleaned


def parse_llm_response(response_text):
    """
    解析 LLM 回复。优先尝试 JSON 格式（数组或单对象），失败则回退到旧版正则。
    返回: list of (text, translation, emotion_label)
      - text: 原始文本
      - translation: 翻译（可能为空）
      - emotion_label: 情感标签（如 'LABEL_0'），若无法确定则返回 None
    """
    # 尝试解析 JSON 格式
    try:
        json_text = response_text.strip()
        if json_text.startswith('```'):
            json_text = re.sub(r'^```(?:json)?\s*', '', json_text)
            json_text = re.sub(r'\s*```$', '', json_text)

        data = json.loads(json_text)

        # JSON 数组格式（多段回复）
        if isinstance(data, list):
            segments = []
            for item in data:
                text = item.get('text', '')
                translation = item.get('translation', '')
                emotion_str = item.get('emotion', 'happiness')
                emotion_label = EmotionEnum.from_string(emotion_str).as_label()
                if text:
                    segments.append((text, translation, emotion_label))
            if segments:
                return segments

        # JSON 单对象格式（单段回复）
        if isinstance(data, dict):
            text = data.get('text', '')
            translation = data.get('translation', '')
            emotion_str = data.get('emotion', 'happiness')
            emotion_label = EmotionEnum.from_string(emotion_str).as_label()
            if text:
                return [(text, translation, emotion_label)]

    except (json.JSONDecodeError, KeyError, AttributeError):
        pass

    # 回退到旧版 [翻译]...[翻译结束] 格式
    pattern = r'(.*?)(?:\[翻译\]|\[翻訳\])(.+?)(?:\[翻译结束\]|\[翻訳終了\])'
    match_result = re.findall(pattern, response_text, flags=re.DOTALL)
    if match_result:
        return [(orig.strip(), trans.strip(), None) for orig, trans in match_result if trans.strip()]

    # 纯文本回复（中文模式），按句号分割
    text = response_text.strip() + '。'
    text = text.replace("。。", "。")
    sentences = re.findall(r'.+?[。！!]', text, flags=re.DOTALL)
    sentences = merge_short_sentences(sentences)
    if sentences:
        return [(s, '', None) for s in sentences]

    return [(response_text.strip(), '', None)]


def main_thread():

    while True:
        time.sleep(1)   #防GIL
        if not text_queue.empty():

            this_turn_response=text_queue.get()
            if this_turn_response=='bye':
                emotion_queue.put('bye')    #退出live2D进程
                dp2qt_queue.put("（再见）")
                audio_gen.shutdown_worker()

                # tr1 是 live2d 进程变量，我们等待 live2d 进程结束，再向 Qt 窗口发送退出信息。
                global tr1
                tr1.join()

                QT_message_queue.put('bye')
                break

            audio_gen.audio_language_choice = dp_chat.audio_language_choice
            QT_message_queue.put("整理语言...")

            # --- 解析 LLM 回复为多个段落 ---
            segments = parse_llm_response(this_turn_response)

            # 对于没有 emotion 的段落，用 bert 模型推断
            for i, (text, translation, emotion_label) in enumerate(segments):
                if emotion_label is None:
                    emotion_for_detect = translation if translation else text
                    emotion_label = emotion_model(re.sub(r"（.*?）", "", emotion_for_detect))[0]['label']
                    segments[i] = (text, translation, emotion_label)

            # --- 逐段处理：流水线式语音合成 + 播放 ---
            for i, (text, translation, emotion_label) in enumerate(segments):
                cleaned_text = clean_text_for_audio(text)

                # 语音合成
                audio_generate_count = 1
                if not dp_chat.if_generate_audio:
                    audio_generate_count = 99

                while audio_generate_count <= 2:
                    try:
                        audio_gen.audio_file_path = audio_gen.generate_current_character_audio_sync(cleaned_text, dp_chat)
                        break
                    except Exception as e:
                        QT_message_queue.put("语音合成出错，重试中")
                        audio_generate_count += 1
                        character.PrintInfo.print_error(f"[Error]语音合成错误信息： {str(e)}")
                        time.sleep(1)

                if audio_generate_count != 1:
                    # 语音合成失败或未启用
                    audio_file_path_queue.put('../reference_audio/silent_audio/silence.wav')
                    if i == 0:
                        is_text_generating_queue.get()  # 让模型停止思考动作

                    # 将全部剩余文本和翻译一次性传给 qtUI 显示
                    remaining_texts = [s[0] for s in segments[i:]]
                    remaining_trans = [s[1] for s in segments[i:] if s[1]]
                    if remaining_trans:
                        dp2qt_queue.put(''.join(remaining_texts) + '\n[翻译]' + ''.join(remaining_trans) + '[翻译结束]')
                    else:
                        dp2qt_queue.put(''.join(remaining_texts))
                    live2d_player.new_text = ''.join(remaining_texts)
                    emotion_queue.put(emotion_label)
                    break

                # 语音合成成功 —— 等待上一段播放完毕（避免打断）
                while not motion_complete_value.value:      #为了等待这句话说完，以免下一句先生成完了导致直接打断
                    time.sleep(0.2)

                audio_file_path_queue.put(audio_gen.audio_file_path)

                if i == 0:
                    is_text_generating_queue.get()  # 第一段合成完后让模型停止思考动作

                # 等待当前播放完毕后再送文本到 qtUI（保持顺序）
                while not live2d_player.live2d_this_turn_motion_complete:
                    time.sleep(0.5)

                # 将本段文本和翻译传给 qtUI 显示
                if translation:
                    dp2qt_queue.put(text + '\n[翻译]' + translation + '[翻译结束]')
                else:
                    dp2qt_queue.put(text)
                live2d_player.new_text = re.sub(r"（.*?）", '', text).strip()
                emotion_queue.put(emotion_label)

            is_audio_play_complete.put('yes')  # 本轮全部段落处理完毕


def run_live2d_process(emotion_queue, audio_file_path_queue, is_text_generating_queue, char_is_converted_queue,
                       change_char_queue, live2d_text_queue, is_display_text_value, motion_complete_value, desktop_w,
                       desktop_h):
    """
    Live2D 子进程入口函数
    不接收 characters 对象，而是在子进程内重新加载，避免 Windows 下 pickle 序列化截断问题
    """
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

    try:

        # 临时静默标准输出，防止子进程二次加载 characters 时在命令行狂刷重复信息
        old_stdout = sys.stdout
        sys.stdout = open(os.devnull, 'w')
        try:
            # 在子进程中重新导入和创建 characters
            import character
            get_all = character.GetCharacterAttributes()
            characters = get_all.character_class_list
        finally:
            sys.stdout.close()
            sys.stdout = old_stdout

        import live2d_module
        live2d_player = live2d_module.Live2DModule()
        live2d_player.live2D_initialize(characters)
        live2d_player.play_live2d(emotion_queue, audio_file_path_queue, is_text_generating_queue,
                                  char_is_converted_queue, change_char_queue, live2d_text_queue, is_display_text_value,
                                  motion_complete_value, desktop_w, desktop_h)
    except Exception as e:
        print(f"[Live2D进程错误] {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__=='__main__':
    # 强制设置多进程实现为 spawn
    multiprocessing.set_start_method('spawn', force=True)

    # 添加本文件的目录到导入 Path
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))

    from qconfig import d_sakiko_config

    print("数字小祥程序...")
    get_all=character.GetCharacterAttributes()
    characters=get_all.character_class_list

    # 初始化全局 ChatManager（自动处理旧版聊天记录迁移）
    chat_manager = get_chat_manager()

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

    audio_gen=audio_generator.AudioGenerate()


    audio_gen.initialize(characters,QT_message_queue)

    live2d_player=live2d_module.Live2DModule()
    live2d_player.live2D_initialize(characters)

    emotion_detector=inference_emotion_detect.EmotionDetect()
    emotion_model = emotion_detector.launch_emotion_detect()



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
    tr1=multiprocessing.Process(target=run_live2d_process,args=(emotion_queue,audio_file_path_queue,is_text_generating_queue,char_is_converted_queue,change_char_queue,live2d_text_queue,is_display_text_value,motion_complete_value, desktop_w, desktop_h))
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
                          emotion_model=emotion_model)

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
        text_queue.put('bye')
    except Exception:
        pass
    try:
        # DeepSeek 推理线程
        qt2dp_queue.put('bye')
    except Exception:
        pass
    try:
        # live2d 播放进程
        emotion_queue.put('bye')
    except Exception:
        pass
    try:
        # 主窗口
        QT_message_queue.put('bye')
    except Exception:
        pass
    try:
        # 语音生成 worker
        audio_gen.shutdown_worker()
    except Exception:
        pass

    # 理论上讲 main_thread 函数中已经调用过 tr1.join，等待过 live2d 进程结束；这里再调用一次不是必要的，但也没有副作用。
    tr1.join()
    tr2.join()
    tr3.join()

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
