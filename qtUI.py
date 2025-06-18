import os.path
import re
import time
import json
import glob


from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QTextBrowser, QPushButton, QDesktopWidget, QHBoxLayout
from PyQt5.QtCore import QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QFontDatabase, QFont, QIcon, QTextCursor




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
            time.sleep(0.5)

class CommunicateThreadMessages(QThread):
    message_signal=pyqtSignal(str)
    def __init__(self,message_queue):
        super().__init__()
        self.message=''
        self.message_queue=message_queue

    def run(self):
        while True:
            if not self.message_queue.empty():
                self.message=self.message_queue.get()
                self.message_signal.emit(self.message)
            time.sleep(0.5)

class ChatGUI(QWidget):
    def __init__(self,dp2qt_queue,qt2dp_queue,QT_message_queue,characters,dp_chat):
        super().__init__()
        self.setWindowTitle("对话框")
        #self.setWindowIcon(QIcon("../live2d_related/sakiko_icon.png"))
        self.screen = QDesktopWidget().screenGeometry()
        self.resize(int(0.4 * self.screen.width()), int(0.7 * self.screen.height()))
        self.chat_display = QTextBrowser()
        self.chat_display.setPlaceholderText("这里显示聊天记录...")
        self.messages_box = QTextBrowser()
        self.messages_box.setPlaceholderText("这里是各种消息提示框...")
        self.messages_box.setMaximumHeight(int(0.085*self.screen.height()))
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("在这里输入内容")
        self.send_button=QPushButton("保存聊天记录")

        layout = QVBoxLayout()
        layout.addWidget(self.chat_display)
        layout.addWidget(self.messages_box)
        layout.addWidget(self.user_input)


        input_layout = QHBoxLayout()
        input_layout.addWidget(self.send_button)
        self.play_button = QPushButton("播放上一条语音")
        input_layout.addWidget(self.play_button)

        layout.addLayout(input_layout)

        self.setLayout(layout)
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
        self.play_button.clicked.connect(self.play_last_audio)
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
                        self.character_chat_history[index]=data['history']

            self.chat_display.setHtml(self.character_chat_history[self.current_char_index])

        if self.character_list[self.current_char_index].icon_path is not None:
            self.setWindowIcon(QIcon(self.character_list[self.current_char_index].icon_path))

        if self.character_list[self.current_char_index].qt_css is not None:
            self.setStyleSheet(self.character_list[self.current_char_index].qt_css)
        else:
            self.setStyleSheet("""
                QWidget {
                    background-color: #E6F2FF;
                    color: #7799CC;
                }
    
                QTextBrowser{
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
    
            """)
        self.user_last_turn_input=''
        self.translation=''
        self.dp_chat=dp_chat    #仅为了保存聊天记录用


    def play_last_audio(self):
        file=glob.glob(os.path.join("../reference_audio/generated_audios_temp",f"*.wav"))
        if not file:
            self.play_button.setText("暂无可播放音频...")
        else:
            self.play_button.setText("播放上一条语音")


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
        self.chat_display.setHtml(self.character_chat_history[self.current_char_index])
        if self.character_list[self.current_char_index].icon_path is not None:
            self.setWindowIcon(QIcon(self.character_list[self.current_char_index].icon_path))

    def handle_response(self,response_text):
        if response_text=='changechange':
            self.change_char()
            return
        response_text=response_text.replace("\n\n",'')
        response_text=response_text.replace("\n", '')
        response_text = response_text.replace("。。", '。')
        pattern = r'(.*?)(?:\[翻译\](.+?)\[翻译结束\])'
        response_tuple_list=re.findall(pattern,response_text,flags=re.DOTALL)
        if not response_tuple_list:
            self.full_response = response_text+"\n"
            self.translation =''
        else:
            self.full_response=response_tuple_list[0][0]+'\n'
            self.translation=response_tuple_list[0][1]

        self.current_index = 0
        self.chat_display.append(f"{self.character_list[self.current_char_index].character_name}：")
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
        if text1 or text2 or text3 or text4:
            return False
        else:
            return True

    def is_display2(self, text):
        flag=True
        user_input_no_display_list=['s','l','m','clr','conv','v',
                                    'clr','mask','save']
        for x in user_input_no_display_list:
            if text == x:
                flag = False
        return flag

    def handle_user_input(self):
        self.setWindowTitle("对话框")
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
                self.chat_display.append(f"你：")
                self.timer.start(20)
        if user_this_turn_input=='clr':
            self.chat_display.clear()

        if user_this_turn_input=='save':
            self.save_data()

    def save_data(self):
        dp_messages=self.dp_chat.all_character_msg
        final_data_dp=[]
        for index,char_msg in enumerate(dp_messages):
            print(index)
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

    app = QApplication(sys.argv)
    win = ChatGUI(dp2qt_queue, qt2dp_queue, QT_message_queue)

    font_id = QFontDatabase.addApplicationFont("../font/ft.ttf")
    font_family = QFontDatabase.applicationFontFamilies(font_id)[0]
    font = QFont(font_family, 12)
    app.setFont(font)

    t1 = threading.Thread(target=dp_thread, args=(dp2qt_queue, qt2dp_queue, QT_message_queue))
    t1.start()

    win.show()
    sys.exit(app.exec_())