import os,sys
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

from queue import Queue
import threading
import time
import re

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont, QFontDatabase

import character
import dp_local2
import audio_generator
import inference_emotion_detect
import live2d_module
import qtUI

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

def main_thread():

    while True:

        time.sleep(1)   #防GIL
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
                to_audio_generator_text_queue.put('bye')
                break
            this_turn_response=this_turn_response+'。'   #有可能这句话就没有句号，导致下面的过程无法发生，出现严重bug...
            #print('ccccccccccccccc',this_turn_response,'dddddddddddddddd')
            this_turn_response=this_turn_response.replace("。。","。")
            this_turn_response = this_turn_response.replace("!!!!!", "")
            pattern = r'(.*?)(?:\[翻译\]|\[翻訳\])(.+?)(?:\[翻译结束\]|\[翻訳終了\])'
            is_ja=False
            if dp_chat.audio_language_choice=='日英混合':
                this_turn_response=re.findall(pattern, this_turn_response, flags=re.DOTALL)
                this_turn_response = [(orig.strip(), trans.strip()) for orig, trans in this_turn_response if trans.strip()]
                this_turn_response=this_turn_response
                is_ja=True
            else:
                this_turn_response = re.findall(r'.+?[。！!]', this_turn_response,flags=re.DOTALL)
                #print('aaaaaaaaaa', this_turn_response, 'sssssssssswwdfwdfw')
                this_turn_response=merge_short_sentences(this_turn_response)
            #print('ssssssssssss', this_turn_response, 'ttttttttttttttt')
            audio_gen.audio_language_choice=dp_chat.audio_language_choice
            QT_message_queue.put("整理语言...")

            iter_step=1 if is_ja else 1
            for i in range(0,len(this_turn_response),iter_step):    #一次合成三句话（三个句号）
                if not is_ja:
                    group=this_turn_response[i:i+1]
                    output_for_audio="".join(group)
                else:
                    output_for_audio=this_turn_response[i][0]
                #print('oooooooooooooooooooo',output_for_audio,'pppppppppppppppppp')
                cleaned_text=re.sub(r"（.*?）", "", output_for_audio)
                #print('eeeeeeeeeeeeeeee', cleaned_text, 'fffffffffffffff')
                cleaned_text = re.sub(r"\(.*?\)", "", cleaned_text)     #删除括号内的内容，模型回答的表示动作或状态的语句，不进行语音合成（deepseek模型喜欢加入这些）
                cleaned_text = re.sub(r"\[.*?]", "", cleaned_text)
                cleaned_text=cleaned_text.replace('「','')
                cleaned_text=cleaned_text.replace('」', '')
                #print('gggggggggggggg', cleaned_text, 'hhhhhhhhhhhhhh')
                if bool(re.fullmatch(r'[\W_]+', cleaned_text.strip())):     #若只包括符号，无法生成语音，导致严重bug
                    cleaned_text='。'
                if cleaned_text=='。':
                    cleaned_text='不能送去合成'     #若模型给出的答案只有括号，就会导致上一步全删了，如果不处理也会有bug
                #print('iiiiiiiiiiiiiiiiii',cleaned_text,'jjjjjjjjjjjjjjjj')
                audio_generate_count=1
                if not dp_chat.if_generate_audio:   #不合成语音
                    audio_generate_count=99

                while audio_generate_count<=2:  #语音合成异常处理
                    try:
                        to_audio_generator_text_queue.put(cleaned_text)
                        audio_gen.is_completed=False
                        while True:

                            if audio_gen.is_completed:
                                break
                            time.sleep(0.4)
                        break


                    except Exception as e:
                        QT_message_queue.put(f"语音合成出错，重试中")
                        audio_generate_count+=1
                        print(f"语音合成错误信息： {str(e)}")
                        time.sleep(1)
                if audio_generate_count!=1:
                    # if audio_generate_count != 99:
                    #     QT_message_queue.put("语音合成失败")
                    #     is_audio_play_complete.put('yes')
                    audio_file_path_queue.put('../reference_audio/silent_audio/silence.wav')
                    emotion_this_three_sentences = emotion_model(cleaned_text)[0]['label']
                    is_text_generating_queue.get()  #让模型停止思考动作

                    emotion_queue.put(emotion_this_three_sentences)
                    if not is_ja:
                        dp2qt_queue.put(''.join(this_turn_response[:]))
                    else:
                        orig_text=''
                        trans_text=''
                        for (orig,trans) in this_turn_response:
                            orig_text=orig_text+orig+'。'
                            trans_text=trans_text+trans+'。'
                        dp2qt_queue.put(orig_text + '\n[翻译]' + trans_text + '[翻译结束]')
                    break

                while not live2d_player.live2d_this_turn_motion_complete:      #为了等待这句话说完，以免下一句先生成完了导致直接打断
                    time.sleep(0.5)
                audio_file_path_queue.put(audio_gen.audio_file_path)    #音频文件队列
                if cleaned_text!="不能送去合成":

                    if not is_ja:
                        emotion_this_three_sentences = emotion_model(cleaned_text)[0]['label']
                    else:
                        a=re.sub(r"（.*?）", "",re.sub(r"\(.*?\)", "", this_turn_response[i][1]))
                        #print(a)
                        emotion_this_three_sentences=emotion_model(a)[0]['label']
                    #print(emotion_this_three_sentences)
                else:
                    emotion_this_three_sentences ='LABEL_0'
                if i==0:
                    is_text_generating_queue.get() #让模型停止思考动作
                while not live2d_player.live2d_this_turn_motion_complete:      #为了等待这句话说完，以免下一句先生成完了导致直接打断
                    time.sleep(0.5)
                emotion_queue.put(emotion_this_three_sentences)     #情感标签队列
                if not is_ja:
                    dp2qt_queue.put(output_for_audio)
                else:
                    dp2qt_queue.put(output_for_audio+'\n[翻译]'+this_turn_response[i][1]+'[翻译结束]')
            is_audio_play_complete.put('yes')   #不让LLM模块提前进入下一个循环


if __name__=='__main__':
    get_all=character.GetCharacterAttributes()
    characters=get_all.character_class_list


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
    change_char_queue=Queue()
    to_audio_generator_text_queue=Queue()

    dp_chat=dp_local2.DSLocalAndVoiceGen(characters)

    audio_gen=audio_generator.AudioGenerate()


    audio_gen.initialize(characters,QT_message_queue)

    live2d_player=live2d_module.Live2DModule()
    live2d_player.live2D_initialize(characters)

    emotion_detector=inference_emotion_detect.EmotionDetect()
    emotion_model = emotion_detector.launch_emotion_detect()

    os.system('cls')
    print("数字祥子程序...这个黑窗口无用，但不能关掉")


    qt_app=QApplication(sys.argv)
    qt_win=qtUI.ChatGUI(dp2qt_queue=dp2qt_queue,
                        qt2dp_queue=qt2dp_queue,
                        QT_message_queue=QT_message_queue
                        ,characters=characters)

    font_id = QFontDatabase.addApplicationFont("../font/ft.ttf")    #设置字体
    font_family = QFontDatabase.applicationFontFamilies(font_id)[0]
    font = QFont(font_family, 12)
    qt_app.setFont(font)

    from PyQt5.QtWidgets import QDesktopWidget          #设置qt窗口位置，与live2d对齐
    screen_w_mid=int(0.5*QDesktopWidget().screenGeometry().width())
    screen_h_mid=int(0.5*QDesktopWidget().screenGeometry().height())
    qt_win.move(screen_w_mid,int(screen_h_mid-0.35*QDesktopWidget().screenGeometry().height()))   #因为窗口高度设置的是0.7倍桌面宽


    tr1=threading.Thread(target=live2d_player.play_live2d,args=(emotion_queue,audio_file_path_queue,is_text_generating_queue,char_is_converted_queue,change_char_queue))
    tr2=threading.Thread(target=dp_chat.text_generator,args=(text_queue,
                                                             is_audio_play_complete,
                                                             is_text_generating_queue,
                                                             dp2qt_queue,
                                                             qt2dp_queue,
                                                             QT_message_queue,
                                                             char_is_converted_queue,
                                                             change_char_queue,
                                                             audio_gen))
    tr3=threading.Thread(target=audio_gen.audio_generator,args=(dp_chat,to_audio_generator_text_queue))
    tr4=threading.Thread(target=main_thread)
    tr1.start()
    tr2.start()
    tr3.start()
    tr4.start()

    qt_win.show()
    qt_app.exec_()


    folder_path = '../reference_audio/generated_audios_temp'    #删除音频缓存

    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)
'''
推理进程



修改库的源码：ffmpeg/_run.py 196
        jieba_fast/__init__.py 117/136/150/168/170
        project\\runtime\Lib\site-packages\\torch\\nn\modules\module.py 30/2043
        AR/models/t2s_model.py 560/736/875
        text/chinese2.py 27
        runtime\Lib\site-packages\pygame\__init__.py 336
        AR\models\\t2s_model.py 845
        runtime\Lib\site-packages\live2d\\utils\lipsync.py   55 防止出现nan，使程序崩溃
        inference_webui.py 大改
        inference_cli.py 大改
'''
