import time

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QTextBrowser, QPushButton, QDesktopWidget
from PyQt5.QtCore import QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QFontDatabase, QFont, QIcon


class CommunicateThreadDP2QT(QThread):
    response_signal=pyqtSignal(str)
    def __init__(self,dp2qt_queue):
        super().__init__()
        self.this_turn_response=''
        self.dp2qt_queue=dp2qt_queue

    def run(self):
        while True:
            if not self.dp2qt_queue.empty():
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
    def __init__(self,dp2qt_queue,qt2dp_queue,QT_message_queue):
        super().__init__()
        self.setWindowTitle("小祥对话框")
        self.setWindowIcon(QIcon("../live2d_related/sakiko_icon.png"))
        self.screen = QDesktopWidget().screenGeometry()
        self.resize(int(0.4 * self.screen.width()), int(0.7 * self.screen.height()))
        self.chat_display = QTextBrowser()
        self.chat_display.setPlaceholderText("这里显示聊天记录...")
        self.messages_box = QTextBrowser()
        self.messages_box.setPlaceholderText("这里是各种消息提示框...")
        self.messages_box.setMaximumHeight(int(0.085*self.screen.height()))
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("在这里输入内容")
        self.send_button=QPushButton("发送")

        layout = QVBoxLayout()
        layout.addWidget(self.chat_display)
        layout.addWidget(self.messages_box)
        layout.addWidget(self.user_input)
        layout.addWidget(self.send_button)
        self.setLayout(layout)
        #处理流式输出
        self.full_response = ""
        self.current_index = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self.stream_print)
        #处理模型的回答
        self.dp2qt_queue=dp2qt_queue
        self.get_response_thread=CommunicateThreadDP2QT(self.dp2qt_queue)
        self.get_response_thread.response_signal.connect(self.handle_response)
        self.get_response_thread.start()
        #处理用户输入
        self.qt2dp_queue=qt2dp_queue
        self.send_button.clicked.connect(self.handle_user_input)
        self.user_input.returnPressed.connect(self.handle_user_input)
        #处理各种消息
        self.QT_message_queue=QT_message_queue
        self.get_message_thread=CommunicateThreadMessages(self.QT_message_queue)
        self.get_message_thread.message_signal.connect(self.handle_messages)
        self.get_message_thread.start()

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



    def handle_response(self,response_text):
        self.full_response = response_text+"\n"
        self.current_index = 0
        self.chat_display.append("祥子：")
        self.timer.start(60)

    def stream_print(self):     #模拟流式打印
        if self.current_index < len(self.full_response):
            cursor = self.chat_display.textCursor()
            cursor.movePosition(cursor.End)
            cursor.insertText(self.full_response[self.current_index])
            self.chat_display.setTextCursor(cursor)
            self.current_index += 1
        else:
            self.timer.stop()

    def handle_user_input(self):
        user_this_turn_input=self.user_input.text()
        user_this_turn_input=user_this_turn_input.strip(' ')
        if user_this_turn_input=='':
            user_this_turn_input="（什么也没说）"
        current_text = self.messages_box.toPlainText()
        if (user_this_turn_input!='clr'
            and current_text!="小祥思考中..."
            and current_text!="祥子在整理语言..."):
            self.qt2dp_queue.put(user_this_turn_input)
        self.user_input.clear()
        user_input_no_display_list=["0：中英混合  1：日英混合\n更改后会自动切换为对应语言的语音","输入内容不合法，重新输入",
                                    "0：deepseek-r1:14b（需安装Ollama与对应本地大模型，选项1相同）  1：deepseek-r1:32b  \n2：调用deepseek-V3官方API（无需安装Ollama，只需联网)",
                                    "小祥思考中...","祥子在整理语言..."]
        if (user_this_turn_input!='lan' and user_this_turn_input!='model'
            and user_this_turn_input!='clr' and user_this_turn_input!='conv'):     #判断是否显示的逻辑，比较笨的方法
            flag=True
            for x in user_input_no_display_list:
                if current_text==x:
                    flag=False
            if flag:
                self.full_response = user_this_turn_input + "\n"
                self.current_index = 0
                self.chat_display.append(f"你：")
                self.timer.start(20)
        if user_this_turn_input=='clr':
            self.chat_display.clear()

    def handle_messages(self,message):
        if message=='bye':
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

                response = user_input + "。"
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