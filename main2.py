import os,sys
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

from queue import Queue
import threading
import time
import re

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont, QFontDatabase

import dp_local2
import audio_generator
import inference_emotion_detect
import live2d_module
import qtUI


#模块间传参队列
text_queue=Queue()
emotion_queue=Queue()
audio_file_path_queue=Queue()
is_audio_play_complete=Queue()
is_text_generating_queue=Queue()
dp2qt_queue=Queue()
qt2dp_queue=Queue()
QT_message_queue=Queue()
char_is_converted_queue=Queue()

dp_chat=dp_local2.DSLocalAndVoiceGen()

audio_gen=audio_generator.AudioGenerate()
audio_gen.initialize()

live2d_player=live2d_module.Live2DModule()
live2d_player.live2D_initialize()

emotion_detector=inference_emotion_detect.EmotionDetect()
emotion_model = emotion_detector.launch_emotion_detect()

os.system('cls')
print("数字祥子程序...这个黑窗口无用，但不能关掉")

def main_thread():

    while True:

        time.sleep(1)   #加这个就运行正常，不知道为什么
        if not text_queue.empty():
            this_turn_response=text_queue.get()
            if this_turn_response=='bye':
                emotion_queue.put('bye')    #退出live2D线程
                dp2qt_queue.put("（再见）")
                while True:
                    if not live2d_player.run:
                        break
                    time.sleep(1)
                QT_message_queue.put('bye')
                break
            this_turn_response=this_turn_response+'。'   #有可能这句话就没有句号，导致下面的过程无法发生，出现严重bug...

            this_turn_response = re.findall(r'.+?。', this_turn_response)
            audio_gen.audio_language_choice=dp_chat.audio_language_choice
            QT_message_queue.put("祥子在整理语言...")
            for i in range(0,len(this_turn_response),2):

                group=this_turn_response[i:i+2]     #一次合成三句话（三个句号）
                output_for_audio="".join(group)
                cleaned_text=re.sub(r"（.*?）", "", output_for_audio)
                cleaned_text = re.sub(r"\(.*?\)", "", cleaned_text)     #删除括号内的内容，模型回答的表示动作或状态的语句，不进行语音合成（deepseek模型喜欢加入这些）
                if bool(re.fullmatch(r'[\W_]+', cleaned_text.strip())):     #若只包括符号，无法生成语音，导致严重bug
                    cleaned_text='。'
                if cleaned_text=='。':
                    cleaned_text='不能送去合成'     #若模型给出的答案只有括号，就会导致上一步全删了，如果不处理也会有bug

                audio_generate_count=1

                while audio_generate_count<=3:  #语音合成异常处理
                    try:
                        audio_gen.audio_generator(cleaned_text,dp_chat.sakiko_state)
                        break
                    except Exception as e:
                        QT_message_queue.put("语音合成出错，重试中")
                        audio_generate_count+=1
                        time.sleep(1)
                        QT_message_queue.put(f"语音合成错误信息： {str(e)}")
                else:
                    QT_message_queue.put("语音合成失败")
                    dp2qt_queue.put(output_for_audio)
                    is_text_generating_queue.get()
                    break


                audio_file_path_queue.put(audio_gen.audio_file_path)    #音频文件队列
                if cleaned_text!="不能送去合成":
                    emotion_this_three_sentences = emotion_model(cleaned_text)[0]['label']
                else:
                    emotion_this_three_sentences ='LABEL_0'
                if i==0:
                    is_text_generating_queue.get() #让模型停止思考动作
                while not live2d_player.live2d_this_turn_motion_complete:      #为了等待这句话说完，以免下一句先生成完了导致直接打断
                    time.sleep(0.5)
                emotion_queue.put(emotion_this_three_sentences)     #情感标签队列
                dp2qt_queue.put(output_for_audio)
            is_audio_play_complete.put('yes')   #不让LLM模块提前进入下一个循环

qt_app=QApplication(sys.argv)
qt_win=qtUI.ChatGUI(dp2qt_queue=dp2qt_queue,
                    qt2dp_queue=qt2dp_queue,
                    QT_message_queue=QT_message_queue)

font_id = QFontDatabase.addApplicationFont("../font/ft.ttf")    #设置字体
font_family = QFontDatabase.applicationFontFamilies(font_id)[0]
font = QFont(font_family, 12)
qt_app.setFont(font)

from PyQt5.QtWidgets import QDesktopWidget          #设置qt窗口位置，与live2d对齐
screen_w_mid=int(0.5*QDesktopWidget().screenGeometry().width())
screen_h_mid=int(0.5*QDesktopWidget().screenGeometry().height())
qt_win.move(screen_w_mid,int(screen_h_mid-0.35*QDesktopWidget().screenGeometry().height()))   #因为窗口高度设置的是0.7倍桌面宽


tr1=threading.Thread(target=live2d_player.play_live2d,args=(emotion_queue,audio_file_path_queue,is_text_generating_queue,char_is_converted_queue))
tr2=threading.Thread(target=dp_chat.text_generator,args=(text_queue,is_audio_play_complete,is_text_generating_queue,dp2qt_queue,qt2dp_queue,QT_message_queue,char_is_converted_queue))
tr3=threading.Thread(target=main_thread)
tr1.start()
tr2.start()
tr3.start()

qt_win.show()
qt_app.exec_()


folder_path = '../reference_audio/generated_audios_temp'    #删除音频缓存

for filename in os.listdir(folder_path):
    file_path = os.path.join(folder_path, filename)
    if os.path.isfile(file_path):
        os.remove(file_path)
'''
修改库的源码：ffmpeg/_run.py 196
        jieba_fast/__init__.py 117/136/150/168/170
        project\runtime\Lib\site-packages\torch\nn\modules\module.py 30/2043
        AR/models/t2s_model.py 560/736/875
        text/chinese2.py 27
        runtime\Lib\site-packages\pygame\__init__.py 336
        AR\models\t2s_model.py 845
        inference_webui.py 大改
        inference_cli.py 大改
'''
