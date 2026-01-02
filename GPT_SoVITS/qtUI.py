import os.path
import re
import time
import json

import numpy as np
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QTextBrowser, QPushButton, QDesktopWidget, QHBoxLayout, QSlider, QLabel
from PyQt5.QtCore import QTimer, QThread, pyqtSignal, QObject, Qt
from PyQt5.QtGui import QFontDatabase, QFont, QIcon, QTextCursor, QPalette

import sounddevice as sd
from opencc import OpenCC


class CommunicateThreadDP2QT(QThread):
    response_signal=pyqtSignal(str)
    def __init__(self,dp2qt_queue,main_timer):
        super().__init__()
        self.this_turn_response=''
        self.dp2qt_queue=dp2qt_queue
        self.main_timer=main_timer

    def run(self):
        while True:
            if not self.dp2qt_queue.empty() and not self.main_timer.isActive():     #解决了特定情况下显示不全回答的bug
                self.this_turn_response=self.dp2qt_queue.get()
                self.response_signal.emit(self.this_turn_response)
            time.sleep(0.1)

class CommunicateThreadMessages(QThread):
    message_signal=pyqtSignal(str)
    def __init__(self,message_queue):
        super().__init__()
        self.message=''
        self.message_queue=message_queue

    def run(self):
        while True:
            self.message=self.message_queue.get()
            self.message_signal.emit(self.message)


class ModelLoaderThread(QThread):   #加载语音识别模型的线程
    model_loaded = pyqtSignal(object)
    model_load_failed = pyqtSignal(str)

    def __init__(self, model_size, device, compute_type):
        super().__init__()
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type

    def run(self):
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            self.model_loaded.emit(model)
        except Exception as e:
            self.model_load_failed.emit(str(e))


class TranscriptionWorker(QObject):
    finished = pyqtSignal(str)

    def __init__(self, model):
        super().__init__()
        self.model = model

    def transcribe(self, audio_data):
        try:
            segments, _ = self.model.transcribe(audio_data, beam_size=5, language="zh")
            text = "".join([seg.text for seg in segments])
            self.finished.emit(text)
        except Exception as e:
            self.finished.emit(f"识别错误 {e}")


class MoreFunctionWindow(QWidget):
    def __init__(self,qt_css,parent_window_close_fun):
        super().__init__()
        self.setWindowTitle("更多功能...")
        self.screen = QDesktopWidget().screenGeometry()
        self.resize(int(0.2 * self.screen.width()), int(0.2 * self.screen.height()))
        layout = QVBoxLayout()
        self.open_motion_editor_button =QPushButton("运行动作组编辑程序")
        self.open_motion_editor_button.clicked.connect(self.on_click_open_motion_editor_button)
        layout.addWidget(self.open_motion_editor_button)

        self.open_start_config_button=QPushButton("启动参数配置")
        self.open_start_config_button.clicked.connect(self.on_click_open_start_config_button)
        layout.addWidget(self.open_start_config_button)

        self.open_small_theater_btn=QPushButton("小剧场模式（Beta）")
        self.open_small_theater_btn.clicked.connect(self.on_click_open_small_theater)
        layout.addWidget(self.open_small_theater_btn)

        self.text_label = QLabel("更多小功能还在开发中...")
        self.text_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.text_label)

        self.close_program_button=QPushButton("退出程序")
        self.close_program_button.clicked.connect(parent_window_close_fun)
        self.close_program_button.clicked.connect(self.close)
        layout.addWidget(self.close_program_button)

        self.setLayout(layout)
        if qt_css is not None:
            self.setStyleSheet(qt_css)

    def on_click_open_motion_editor_button(self):
        try:
            # 使用 subprocess 模块启动
            import subprocess
            import sys
            subprocess.Popen([sys.executable, "live2d_viewer.py"])
        except Exception as e:
            print("启动失败", f"启动程序时发生错误:\n{e}")
        self.close()
    def on_click_open_start_config_button(self):
        try:
            # 必须在同进程下启动，否则设置界面的修改不会立刻同步
            from dsakiko_configuration import DSakikoConfigWindow
            w = DSakikoConfigWindow()
            w.show()
        except Exception as e:
            print("启动失败", f"启动程序时发生错误:\n{e}")
        self.close()

    def on_click_open_small_theater(self):
        try:
            # 使用 subprocess 模块启动
            import subprocess
            import sys
            subprocess.Popen([sys.executable, "multi_char_main.py"])
        except Exception as e:
            print("启动失败", f"启动程序时发生错误:\n{e}")
        self.close()

class WarningWindow(QWidget):
    def __init__(self,warning_text,css,parent_window_fun):
        super().__init__()
        self.setWindowTitle("确认操作")
        self.label=QLabel(warning_text)
        layout=QVBoxLayout()
        layout.addWidget(self.label)
        self.btn_layout=QHBoxLayout()
        self.confirm_btn=QPushButton("确定")
        self.cancel_btn=QPushButton("取消")
        self.btn_layout.addWidget(self.confirm_btn)
        self.btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(self.btn_layout)
        self.setLayout(layout)
        self.setStyleSheet(css)
        self.confirm_btn.clicked.connect(parent_window_fun)
        self.confirm_btn.clicked.connect(self.close)
        self.cancel_btn.clicked.connect(self.close)





class ChatGUI(QWidget):
    def __init__(self,
                 dp2qt_queue,
                 qt2dp_queue,
                 QT_message_queue,
                 characters,
                 dp_chat,
                 audio_gen,live2d_mod,emotion_queue,audio_file_path_queue,emotion_model):
        super().__init__()
        self.audio_gen = audio_gen  # 为了获得音频文件路径，以及修改语速
        self.setWindowTitle("数字小祥")
        #self.setWindowIcon(QIcon("../live2d_related/sakiko_icon.png"))
        self.screen = QDesktopWidget().screenGeometry()
        self.resize(int(0.4 * self.screen.width()), int(0.7 * self.screen.height()))
        self.chat_display = QTextBrowser()
        self.chat_display.setPlaceholderText("这里显示聊天记录...")
        self.chat_display.setOpenExternalLinks(False)
        self.chat_display.anchorClicked.connect(self.play_history_audio)
        self.chat_display.setOpenLinks(False)

        self.messages_box = QTextBrowser()
        self.messages_box.setPlaceholderText("这里是各种消息提示框...")
        self.messages_box.setMaximumHeight(int(0.085*self.screen.height()))
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("在这里输入内容")

        self.voice_button = QPushButton()
        mic_icon=QIcon("./microphone.png")
        self.voice_button.setIcon(mic_icon)
        self.voice_button.pressed.connect(self.voice_dectect)
        self.voice_button.released.connect(self.voice_decect_end)


        self.send_button=QPushButton("保存聊天记录")

        layout = QVBoxLayout()
        layout.addWidget(self.chat_display)
        layout.addWidget(self.messages_box)

        input_layout=QHBoxLayout()
        input_layout.addWidget(self.user_input)
        input_layout.addWidget(self.voice_button)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.send_button)

        #处理流式输出
        self.full_response = ""
        self.current_index = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self.stream_print)
        #处理模型的回答
        self.dp2qt_queue=dp2qt_queue
        self.get_response_thread=CommunicateThreadDP2QT(self.dp2qt_queue,self.timer)
        self.get_response_thread.response_signal.connect(self.handle_response)
        self.get_response_thread.start()
        #处理用户输入
        self.qt2dp_queue=qt2dp_queue
        self.send_button.clicked.connect(self.save_data)

        self.user_input.returnPressed.connect(self.handle_user_input)
        #处理各种消息
        self.QT_message_queue=QT_message_queue
        self.get_message_thread=CommunicateThreadMessages(self.QT_message_queue)
        self.get_message_thread.message_signal.connect(self.handle_messages)
        self.get_message_thread.start()

        self.character_list:list = characters
        self.current_char_index = 0
        self.character_chat_history=[]
        for _ in self.character_list:
            self.character_chat_history.append('')
        if os.path.getsize('../reference_audio/history_messages_qt.json')!=0:
            with open('../reference_audio/history_messages_qt.json','r',encoding='utf-8') as f:
                json_data = json.load(f)
            for index,character in enumerate(self.character_list):
                for data in json_data:
                    if data['character']==character.character_name:
                        underline_removed=data['history'].replace("underline", "none")
                        self.character_chat_history[index]=underline_removed

            self.chat_display.setHtml(self.character_chat_history[self.current_char_index])

        self.default_css = """
                        QWidget {
                            background-color: #E6F2FF;
                            color: #7799CC;
                        }

                        QTextBrowser{
                            text-decoration: none;
                            background-color: #FFFFFF;
                            border: 3px solid #B3D1F2;
                            border-radius:9px;
                            padding: 5px;
                        }

                        QLineEdit {
                            background-color: #FFFFFF;
                            border: 2px solid #B3D1F2;
                            border-radius: 9px;
                            padding: 5px;
                        }

                        QPushButton {                
                            background-color: #7FB2EB;
                            color: #ffffff;
                            border-radius: 6px;
                            padding: 6px;
                        }

                        QPushButton:hover {
                            background-color: #3FB2EB;
                        }

                        QScrollBar:vertical {
                            border: none;
                            background: #D0E2F0;
                            width: 10px;
                            margin: 0px 0px 0px 0px;
                        }

                        QScrollBar::handle:vertical {
                            background: #B3D1F2;
                            min-height: 20px;
                            border-radius: 3px;
                        }

                        QSlider::groove:horizontal {
                            /* 滑槽背景 */
                            border: 1px solid #B3D1F2;  /* 使用边框色作为滑槽边框 */
                            height: 8px;
                            background: #D0E2F0;       /* 使用浅色背景 */
                            margin: 2px 0;
                            border-radius: 4px;
                        }

                        QSlider::handle:horizontal {
                            /* 滑块手柄 */
                            background: #7FB2EB;       /* 使用按钮的亮蓝色 */
                            border: 1px solid #4F80E0;
                            width: 16px;
                            margin: -4px 0;            /* 垂直方向上的偏移，使手柄在滑槽上居中 */
                            border-radius: 8px;        /* 使手柄成为圆形 */
                        }

                        QSlider::handle:horizontal:hover {
                            /* 鼠标悬停时的手柄颜色 */
                            background: #3FB2EB;       /* 使用按钮的 hover 亮色 */
                            border: 1px solid #3F60D0;
                        }

                        QSlider::sub-page:horizontal {
                            /* 进度条（已滑过部分） */
                            background: #AACCFF;       /* 使用一个中间的蓝色，比滑槽背景深，比手柄浅 */
                            border-radius: 4px;
                            margin: 2px 0;
                        }

                    """

        if self.character_list[self.current_char_index].icon_path is not None:
            self.setWindowIcon(QIcon(self.character_list[self.current_char_index].icon_path))

        if self.character_list[self.current_char_index].qt_css is not None:
            self.setStyleSheet(self.character_list[self.current_char_index].qt_css)
        else:
            self.setStyleSheet(self.default_css)
        self.user_last_turn_input=''
        self.translation=''
        self.dp_chat=dp_chat    #仅为了保存聊天记录用

        self.is_ch = False
        self.saved_talk_speed_and_pause_second = [{'talk_speed': 0, 'pause_second': 0.5} for _ in self.character_list]
        for i, character in enumerate(self.character_list):
            if not self.is_ch:
                if self.character_list[self.current_char_index].character_name == '祥子':
                    self.saved_talk_speed_and_pause_second[i]['talk_speed'] = 0.9
                else:
                    self.saved_talk_speed_and_pause_second[i]['talk_speed'] = 0.88
            else:
                if self.character_list[self.current_char_index].character_name == '祥子':
                    self.saved_talk_speed_and_pause_second[i]['talk_speed'] = 0.83
                else:
                    self.saved_talk_speed_and_pause_second[i]['talk_speed'] = 0.9

        self.talk_speed_label = QLabel(f"语速调节：{self.audio_gen.speed}")  # 此时的数值不是真实的，audio_gen还没有初始化完成
        self.talk_speed_label.setToolTip("调整生成语音的语速，数值越大语速越快。如果觉得生成质量不佳，适当调整一下。")
        self.talk_speed_slider = QSlider(Qt.Horizontal)
        self.talk_speed_slider.setRange(60, 140)
        self.talk_speed_slider.valueChanged.connect(self.set_talk_speed)



        self.more_function_button =QPushButton("More")
        self.more_function_button.clicked.connect(self.open_more_function_window)
        self.change_character_button = QPushButton("切换角色")
        self.change_character_button.clicked.connect(self.change_character_button_function)

        self.pause_second_label=QLabel(f"句间停顿时间(s)：{self.audio_gen.pause_second}")
        self.pause_second_label.setToolTip("调整句子之间的停顿时间，数值越大停顿越久。如果觉得生成质量不佳，适当调整一下。")
        self.pause_second_slider=QSlider(Qt.Horizontal)
        self.pause_second_slider.valueChanged.connect(self.set_pause_second)
        self.pause_second_slider.setRange(10,80)
        self.pause_second_slider.setValue(50)

        slider_layout = QHBoxLayout()
        slider_layout.addWidget(self.talk_speed_label)
        slider_layout.addWidget(self.talk_speed_slider)
        slider_layout.addWidget(self.pause_second_label)
        slider_layout.addWidget(self.pause_second_slider)
        layout.addLayout(slider_layout)

        button_layout.addWidget(self.change_character_button)
        button_layout.addWidget(self.more_function_button)
        layout.addLayout(button_layout)

        layout.addLayout(input_layout)


        self.talk_speed_reset()

        self.setLayout(layout)  #因为需要character_list等参数，所以放在最后初始化

        self.live2d_mod=live2d_mod
        self.emotion_queue=emotion_queue
        self.emotion_model=emotion_model
        self.audio_file_path_queue=audio_file_path_queue    #为了播放历史记录
        #-------------------------------------------------------------------------------以下为语音识别部分
        self.voice_button.setCheckable(True)
        self.voice_button.setEnabled(False)
        self.record_timer = QTimer()
        self.record_timer.timeout.connect(self.check_valid)
        self.is_recording=False
        self.record_data=[]
        self.voice_is_valid=False
        self.load_whisper_model()

    def set_pause_second(self):
        pause_second_value=self.pause_second_slider.value()
        self.audio_gen.pause_second=pause_second_value/100
        self.pause_second_label.setText(f"句间停顿时间(s)：{self.audio_gen.pause_second:.2f}")
        self.saved_talk_speed_and_pause_second[self.current_char_index]['pause_second']=self.audio_gen.pause_second


    def open_more_function_window(self):
        css=self.character_list[self.current_char_index].qt_css if self.character_list[self.current_char_index].qt_css is not None else self.default_css
        self.more_function_win=MoreFunctionWindow(css,self.close_program)
        self.more_function_win.show()

    def close_program(self):
        self.user_input.setText('bye')
        self.user_input.returnPressed.emit()

    def change_character_button_function(self):
        self.user_input.setText('s')
        self.user_input.returnPressed.emit()
        self.user_input.clear()

    def talk_speed_reset(self):
        # if not self.is_ch:
        #     if self.character_list[self.current_char_index].character_name=='祥子':
        #         self.talk_speed_slider.setValue(0.9*100)
        #         self.audio_gen.speed=0.9
        #         self.talk_speed_label.setText(f"语速调节：{self.audio_gen.speed}")
        #     else:
        #         self.talk_speed_slider.setValue(0.88*100)
        #         self.audio_gen.speed=0.88
        #         self.talk_speed_label.setText(f"语速调节：{self.audio_gen.speed}")
        # else:
        #     if self.character_list[self.current_char_index].character_name=='祥子':
        #         self.talk_speed_slider.setValue(0.83*100)
        #         self.audio_gen.speed=0.83
        #         self.talk_speed_label.setText(f"语速调节：{self.audio_gen.speed}")
        #     else:
        #         self.talk_speed_slider.setValue(0.9*100)
        #         self.audio_gen.speed=0.9
        #         self.talk_speed_label.setText(f"语速调节：{self.audio_gen.speed}")
        saved_speed=self.saved_talk_speed_and_pause_second[self.current_char_index]['talk_speed']
        self.talk_speed_slider.setValue(int(saved_speed*100))
        self.audio_gen.speed=saved_speed
        self.talk_speed_label.setText(f"语速调节：{self.audio_gen.speed:.2f}")

    def pause_second_reset(self):
        saved_value=self.saved_talk_speed_and_pause_second[self.current_char_index]['pause_second']
        self.pause_second_slider.setValue(int(saved_value*100))
        self.audio_gen.pause_second = saved_value
        self.pause_second_label.setText(f"句间停顿时间(s)：{self.audio_gen.pause_second:.2f}")


    def set_talk_speed(self):
        speed_value=self.talk_speed_slider.value()
        self.audio_gen.speed=speed_value/100
        self.talk_speed_label.setText(f"语速调节：{self.audio_gen.speed:.2f}")
        self.saved_talk_speed_and_pause_second[self.current_char_index]['talk_speed']=self.audio_gen.speed

    def load_whisper_model(self):
        self.model_loader= ModelLoaderThread("./pretrained_models/faster_whisper_small", device="cpu", compute_type="int8")
        self.model_loader.model_loaded.connect(self.on_model_loaded)
        self.model_loader.model_load_failed.connect(self.on_model_load_failed)
        self.model_loader.start()

    def on_model_load_failed(self,error_message):
        self.setWindowTitle(f"模型加载失败: {error_message}")

    def on_model_loaded(self,model):
        self.whisper_model=model
        self.setWindowTitle("数字小祥")
        self.start_input_stream()

    def start_input_stream(self):
        try:
            self.stream = sd.InputStream(
                samplerate=16000,
                channels=1,
                callback=self.audio_callback,
                dtype='int16'  # 使用 int16 格式
            )
            # 啟動串流，它將在背景執行緒中持續呼叫 audio_callback
            self.stream.start()
            self.voice_button.setEnabled(True)

        except Exception as e:
            print(f"错误：无法启动麦克风串流。")
            print(f"请检查麦克风是否连接或被其他程序占用。")
            print(f"错误信息: {e}")
            self.voice_button.setEnabled(False)  # 保持按鈕禁用

    def audio_callback(self, indata, frames, time, status):
        if self.is_recording:
            self.record_data.append(indata.copy())


    def check_valid(self):
        self.voice_is_valid=True

    def voice_dectect(self):

        self.voice_is_valid=False
        self.is_recording=True
        self.record_data=[]
        self.record_timer.start(300)
        self.user_input.setText('start_talking')
        self.user_input.returnPressed.emit()
        self.setWindowTitle("正在录音...松开结束")


    def voice_decect_end(self):
        if not self.is_recording:
            return # 防止重复触发

        self.setWindowTitle("数字小祥")
        self.is_recording=False
        self.record_timer.stop()
        self.user_input.setText('stop_talking')
        self.user_input.returnPressed.emit()

        if not self.voice_is_valid:
            self.setWindowTitle("录音时间过短，请重试...")
            return
        else:
            try:
                if not self.record_data:
                    self.setWindowTitle("录音失败，没有捕获到音频数据。")
                    return

                audio_data = np.concatenate(self.record_data, axis=0).flatten()
                audio_data = audio_data.astype(np.float32) / 32768.0

            except ValueError:
                self.setWindowTitle("录音数据处理失败，请重试。")
                return
            self.setWindowTitle("正在识别语音...")
            self.run_transcription_thread(audio_data)

    def run_transcription_thread(self, audio_data):
        self.voice_button.setEnabled(False)
        self.transcription_worker = TranscriptionWorker(self.whisper_model)
        self.transcription_thread = QThread()
        self.transcription_worker.moveToThread(self.transcription_thread)
        self.transcription_thread.started.connect(lambda: self.transcription_worker.transcribe(audio_data))
        self.transcription_worker.finished.connect(self.on_transcription_finished)

        self.transcription_thread.finished.connect(self.transcription_thread.deleteLater)
        self.transcription_worker.finished.connect(self.transcription_thread.quit)
        self.transcription_worker.finished.connect(self.transcription_worker.deleteLater)

        self.transcription_thread.start()

    def on_transcription_finished(self, text):
        cc = OpenCC('tw2s.json')
        text=cc.convert(text)
        if text=='切换角色':
            self.user_input.setText('s')
            self.user_input.returnPressed.emit()
            self.user_input.clear()
        elif text=='切换语言':
            self.user_input.setText('l')
            self.user_input.returnPressed.emit()
            self.user_input.clear()
        else:
            self.user_input.setText(text)

        if self.whisper_model and not self.is_recording:
            self.voice_button.setEnabled(True)
            self.setWindowTitle("数字小祥")

    def closeEvent(self, event):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                print(f"關閉串流時出錯: {e}", file=sys.stderr)

        event.accept()

    def play_history_audio(self,audio_path_and_emotion):

        if self.live2d_mod.live2d_this_turn_motion_complete:
            self.setWindowTitle("数字小祥")
            audio_path_and_emotion=audio_path_and_emotion.toString()
            match=re.match(r"(.+?)\[(.+?)\]$", audio_path_and_emotion)
            if match:
                audio_path = match.group(1)  # 路径
                emotion = match.group(2)  #emotion标签


                if os.path.exists(audio_path):
                    self.audio_file_path_queue.put(audio_path)
                    self.emotion_queue.put(emotion)
                    print("这句话对应的音频文件路径：", audio_path)
                    #print("注意：若你已经设置了if_delete_audio_cache.txt中的数字不为0，并且觉得这句生成的还不错，请复制该音频文件到别处，因为设置数字不为0的情况下关闭程序会自动删除该文件，以释放空间。设置数字不为0的情况下如果希望下次打开程序还能听到，再把这个文件复制回这个路径即可。\n")
                else:
                    self.setWindowTitle('所选文本对应的音频文件已经删除...')
                    print("所选文本对应的音频文件已经删除...\n")
        else:
            self.setWindowTitle('请等待当前过程完成后重试...')



    def change_char(self):
        record = self.chat_display.toHtml()
        self.character_chat_history[self.current_char_index] = record
        self.chat_display.clear()
        if len(self.character_list) == 1:
            self.current_char_index = 0
        else:
            if self.current_char_index < len(self.character_list) - 1:
                self.current_char_index += 1
            else:
                self.current_char_index = 0
        if self.character_list[self.current_char_index].qt_css is not None:
            self.setStyleSheet(self.character_list[self.current_char_index].qt_css)
        chat_history_html_content = re.sub(r'font-family:\s*[^;"]+;', '', self.character_chat_history[self.current_char_index].replace('underline','none'))
        self.chat_display.setHtml(chat_history_html_content)
        if self.character_list[self.current_char_index].icon_path is not None:
            self.setWindowIcon(QIcon(self.character_list[self.current_char_index].icon_path))
        self.talk_speed_reset()  #切换角色后重置默认语速
        self.pause_second_reset()  #切换角色后重置默认句间停顿时间

    def handle_response(self,response_text):
        if response_text=='changechange':
            self.change_char()
            return
        response_text=response_text.replace("\n\n",'')
        response_text=response_text.replace("\n", '')
        response_text = response_text.replace("。。", '。')
        pattern = r'(.*?)(?:\[翻译\](.+?)\[翻译结束\])'
        response_tuple_list=re.findall(pattern,response_text,flags=re.DOTALL)
        if not response_tuple_list: #中文
            self.full_response = response_text+"\n"
            self.translation =''
        else:   #日文
            self.full_response=response_tuple_list[0][0]+'\n'
            self.translation=response_tuple_list[0][1]
        self.current_index = 0

        text_color = self.chat_display.palette().color(QPalette.Text).name()
        if self.dp_chat.if_generate_audio and response_text!='（再见）':

            if not response_tuple_list:
                emotion=self.emotion_model(response_text)[0]['label']
            else:
                emotion = self.emotion_model(response_tuple_list[0][1])[0]['label']
            abs_path = os.path.abspath(self.audio_gen.audio_file_path).replace('\\', '/')
            self.chat_display.append(f'<a href="{abs_path}[{emotion}]" style="text-decoration: none; color: {text_color};">★{self.character_list[self.current_char_index].character_name}：</a>')     #将emotion藏进路径中，回来解包一下即可
        else:
            self.chat_display.append(f'<a style="text-decoration: none; color: {text_color};">{self.character_list[self.current_char_index].character_name}：</a>')

        self.timer.start(30)

    def stream_print(self):     #模拟流式打印
        cursor = self.chat_display.textCursor()
        if self.current_index < len(self.full_response):
            cursor.movePosition(cursor.End)
            cursor.insertText(self.full_response[self.current_index])
            self.chat_display.setTextCursor(cursor)
            self.current_index += 1
        else:
            self.timer.stop()
            if self.translation!='':
                cursor.movePosition(cursor.End)
                cursor.insertHtml(f'<span style="color: #B3D1F2; font-style: italic;">{self.translation}</span><br>')
                self.translation=''
                self.chat_display.moveCursor(QTextCursor.End)

    def is_display(self,text):
        text1=re.findall('切换GPT-SoVITS',text,flags=re.DOTALL)
        text2 = re.findall('已切换为', text, flags=re.DOTALL)
        text3=re.findall('整理语言',text,flags=re.DOTALL)
        text4=re.findall('思考中',text,flags=re.DOTALL)
        return not (text1 or text2 or text3 or text4)

    def is_display2(self, text):
        flag=True
        user_input_no_display_list=['s','l','m','clr','conv','v',
                                    'clr','mask','save','start_talking','stop_talking']
        for x in user_input_no_display_list:
            if text == x:
                flag = False
        return flag

    def handle_user_input(self):
        def clr_history():
            self.chat_display.clear()
            self.qt2dp_queue.put('clr')

        self.setWindowTitle("数字小祥")
        user_this_turn_input=self.user_input.text()
        user_this_turn_input=user_this_turn_input.strip(' ')
        if user_this_turn_input=='':
            user_this_turn_input="（什么也没说）"
        current_text = self.messages_box.toPlainText()
        if (user_this_turn_input!='clr'
            and user_this_turn_input!='save'
            and self.is_display(current_text)
            and self.qt2dp_queue.empty()):
            self.qt2dp_queue.put(user_this_turn_input)
        self.user_input.clear()
        user_input_no_display_list=[
                                    "0：deepseek-r1:14b（需安装Ollama与对应本地大模型，选项1相同）  1：deepseek-r1:32b  \n2：调用deepseek-V3官方API（无需安装Ollama，只需联网)",
                                    f"{self.character_list[self.current_char_index].character_name}思考中...",'小祥思考中...']
        if self.is_display2(user_this_turn_input):     #判断是否显示的逻辑，比较笨的方法
            flag=True
            for x in user_input_no_display_list:
                if current_text==x:
                    flag=False
            if flag:
                flag=self.is_display(current_text)
            if flag:
                self.full_response = user_this_turn_input + "\n"
                self.current_index = 0
                text_color = self.chat_display.palette().color(QPalette.Text).name()
                self.chat_display.append(f'<a style="text-decoration: none; color: {text_color};">你：')
                self.timer.start(20)
        if user_this_turn_input=='clr':
            css=self.character_list[self.current_char_index].qt_css if self.character_list[self.current_char_index].qt_css is not None else self.default_css
            self.pop_up_clr_warning_win=WarningWindow("确定要清空当前角色的聊天记录吗？角色记忆也将同步被删除",css,clr_history)
            self.pop_up_clr_warning_win.show()

        if user_this_turn_input=='save':
            self.save_data()

        if user_this_turn_input=='l':   #切换语言时也会变更语速
            self.is_ch=not self.is_ch
            self.talk_speed_reset()
            self.pause_second_reset()

    def save_data(self):
        dp_messages=self.dp_chat.all_character_msg
        final_data_dp=[]
        for index,char_msg in enumerate(dp_messages):
            final_data_dp.append({'character':self.character_list[index].character_name,'history':char_msg})
        with open('../reference_audio/history_messages_dp.json', 'w', encoding='utf-8') as f:
            json.dump(final_data_dp, f, ensure_ascii=False, indent=4)
            f.close()

        record = self.chat_display.toHtml()
        self.character_chat_history[self.current_char_index] = record
        final_data_qt=[]
        for index,char_msg in enumerate(self.character_chat_history):
            final_data_qt.append({'character':self.character_list[index].character_name,'history':char_msg})
        with open('../reference_audio/history_messages_qt.json', 'w', encoding='utf-8') as f:
            json.dump(final_data_qt, f, ensure_ascii=False, indent=4)
            f.close()
        self.setWindowTitle("已保存最新的聊天记录！")

    def handle_messages(self,message):
        if message=='bye':
            self.save_data()
            self.close()
        self.messages_box.clear()
        self.messages_box.append(message)

if __name__=='__main__':
    from PyQt5.QtWidgets import QApplication
    import sys,threading
    from queue import Queue
    dp2qt_queue = Queue()
    qt2dp_queue = Queue()
    QT_message_queue = Queue()


    def dp_thread(dp2qt_queue, qt2dp_queue, QT_message_queue):      #测试用
        while True:
            time.sleep(0.5)
            if not qt2dp_queue.empty():
                user_input = qt2dp_queue.get()
                if user_input == 'bye':
                    break
                if user_input == '思考中' or user_input == '有错误发生':
                    QT_message_queue.put(user_input)

                response = user_input + "祥子选择入学有特待生制度的羽丘女子学园，在校期间几乎不和任何人交流，\n期间她多次和睦在羽泽咖啡店会面，要求她不要向前队友透露自己的动向，但素世还是从爱音口中得知了祥子就在羽丘的消息，多次堵校门，这让祥子很厌烦。于是祥子决定与长崎爽世正式谈话，并顺便观看其所在乐队的演出。"
                time.sleep(2)
                dp2qt_queue.put(response)
        dp2qt_queue.put("结束了")


    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    import character
    get_all = character.GetCharacterAttributes()
    characters = get_all.character_class_list

    app = QApplication(sys.argv)
    class AudioGenMock:
        def __init__(self):
            self.speed=1.0
            self.pause_second=0.3
            self.audio_file_path="../reference_audio/audio_cache/temp_output.wav"
    audio_gen_mock = AudioGenMock()
    win = ChatGUI(dp2qt_queue, qt2dp_queue, QT_message_queue, characters, None, audio_gen_mock,None,None,None,None)

    font_id = QFontDatabase.addApplicationFont("../font/ft.ttf")
    font_family = QFontDatabase.applicationFontFamilies(font_id)[0]
    font = QFont(font_family, 12)
    app.setFont(font)

    t1 = threading.Thread(target=dp_thread, args=(dp2qt_queue, qt2dp_queue, QT_message_queue))
    t1.start()

    win.show()
    sys.exit(app.exec_())