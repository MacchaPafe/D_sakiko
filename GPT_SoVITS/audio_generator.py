import re

import time
from multiprocessing import Process, Queue

# script_dir = os.path.dirname(os.path.abspath(__file__))
# sys.path.insert(0, script_dir)


from inference_cli import synthesize

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

class AudioGenerate:
    def __init__(self):
        self.GPT_model_file=''
        self.SoVITS_model_file=''
        self.ref_audio_file=''
        self.ref_audio_file_white_sakiko = '../reference_audio/sakiko/white_sakiko.wav'
        self.ref_audio_file_black_sakiko = '../reference_audio/sakiko/black_sakiko.wav'
        self.ref_audio_language =''
        self.ref_text_file=''
        self.ref_text_file_white_sakiko = '../reference_audio/sakiko/reference_text_white_sakiko.txt'
        self.ref_text_file_black_sakiko = '../reference_audio/sakiko/reference_text_black_sakiko.txt'
        self.program_output_path="../reference_audio/generated_audios_temp"
        self.speed=1.0
        self.pause_second=0.5

        self.audio_file_path='../reference_audio\\silent_audio\\silence.wav'
        self.is_completed=False
        self.audio_language_choice = "中文"
        self.if_sakiko = False
        self.replacements_jap = {       #日语语音合成时，人名很容易读错
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
            '高松燈':'たかまつともり',
            '燈':'ともり',
            '灯': 'ともり',
            '椎名立希':'しいなたき',
            '素世': 'そよ',
            '爽世': 'そよ',
            '千早愛音':'ちはやアノン',
            '愛音': 'アノン',
            '要楽奈':'かなめらーな',
            '楽奈': 'らーな',
            '春日影':'はるひかげ',
            'Doloris':'ドロリス',
            'Mortis':'モーティス',
            'Timoris':'ティモリス',
            'Amoris':'アモーリス',
            'Oblivionis':'オブリビオニス',
            'live':'ライブ',
            'MyGO':'まいご',
            'RiNG':'リング',
            '戸山香澄':'とやまかすみ',
            '户山香澄':'とやまかすみ',
            '香澄':'かすみ',
            '市ヶ谷有咲': 'いちがやありさ',
            '市谷有咲': 'いちがやありさ',
            '有咲': 'ありさ',
            '有咲ちゃん': 'ありさ',
            '牛込里美':'うしごめりみ',
            '里美':'りみ',
            'りみっち':'りみりん',
            '山吹沙綾':'やまぶきさあや',
            '沙綾':'さあや',
            '沙绫': 'さあや',
            '沙綾さん':'さあや',
            '花园多惠':'はなぞのたえ',
            '花園多惠':'はなぞのたえ',
            '多惠':'おたえ',
            'たえちゃん': 'おたえ',
            'Afterglow':'アフターグロウ',
            'Pastel*Palettes':'パステルパレット',
            'Poppin\'Party':'ポッピンパーティー',
            'Roselia':'ロゼリア',
            'STAR BEAT':'スタービート',
            'RAISE A SUILEN':'レイズアスイレン',
            'Morfonica':'モルフォニカ',
            'SPACE': 'スペース',
            '六花': 'ろっか',


        }
        self.replacements_chi ={
            'CRYCHIC':'C团',
            'live':"演出",
            'RiNG':"ring",
            'Doloris': '初华',
            'Mortis': '睦',
            'Timoris': '海铃',
            'Amoris': '喵梦',
            'Oblivionis': '我',
            'MyGO':'mygo',
            'ちゃん':''
        }
        self.character_list=[]
        self.current_character_index=0

        self.to_gptsovits_com_queue=Queue()
        self.from_gptsovits_com_queue = Queue()
        self.from_gptsovits_com_queue2 = Queue()
        self.neccerary_matirials=''     #初始化
        self.gptsovits_process =Process(target=synthesize,args=(self.to_gptsovits_com_queue,
                                                                self.from_gptsovits_com_queue,
                                                                self.from_gptsovits_com_queue2))
        self.sakiko_which_state=True
        self.message_queue=None
        self.is_change_complete=False

    def initialize(self,character_list,message_queue):

        self.character_list=character_list
        self.message_queue=message_queue
        self.GPT_model_file=self.character_list[self.current_character_index].GPT_model_path
        self.SoVITS_model_file=self.character_list[self.current_character_index].sovits_model_path
        self.ref_audio_language=self.character_list[self.current_character_index].gptsovits_ref_audio_lan
        self.ref_text_file=self.character_list[self.current_character_index].gptsovits_ref_audio_text
        if self.character_list[self.current_character_index].character_name == '祥子':
            self.if_sakiko = True
        else:
            self.if_sakiko = False
        if not self.if_sakiko:
            self.ref_audio_file=self.character_list[self.current_character_index].gptsovits_ref_audio


        self.neccerary_matirials=[0,self.GPT_model_file,self.SoVITS_model_file]
        self.to_gptsovits_com_queue.put(self.neccerary_matirials)
        self.gptsovits_process.start()

        self.is_change_complete = False
        while self.from_gptsovits_com_queue.empty():
            time.sleep(0.2)
        self.from_gptsovits_com_queue.get()
        self.message_queue.put('正在加载GPT-SoVITS模型')
        while self.from_gptsovits_com_queue.empty():
            time.sleep(0.4)
        self.from_gptsovits_com_queue.get()
        self.is_change_complete = True



        # self.GPT_model_file = glob.glob(os.path.join("../GPT_weights_v4", f"*.ckpt"))
        # if not self.GPT_model_file:
        #     raise FileNotFoundError(f"没有找到GPT模型文件(.ckpt)")
        # self.GPT_model_file = max(self.GPT_model_file, key=os.path.getmtime)  # 找出最新的文件
        # #print(f"GPT模型文件...OK")
        #
        # self.SoVITS_model_file = glob.glob(os.path.join("../SoVITS_weights_v4", f"*.pth"))
        # if not self.SoVITS_model_file:
        #     raise FileNotFoundError(f"没有找到SoVITS模型文件(.pth)")
        # self.SoVITS_model_file = max(self.SoVITS_model_file, key=os.path.getmtime)
        # #print(f"SoVITS模型文件...OK")
        #
        # ref_audio_file_wav = glob.glob(os.path.join("../reference_audio", f"*.wav"))
        # ref_audio_file_mp3 = glob.glob(os.path.join("../reference_audio", f"*.mp3"))
        # if not ref_audio_file_wav + ref_audio_file_mp3:
        #     raise FileNotFoundError(f"没有找到推理参考音频文件(.wav/.mp3)")
        # #self.ref_audio_file = max(ref_audio_file_mp3 + ref_audio_file_wav, key=os.path.getmtime)   #黑白祥特殊设计，这句就不要了
        # #print(f"推理参考音频文件...OK")
        #
        # ref_audio_language_file = '../reference_audio/reference_audio_language.txt'
        # with open(ref_audio_language_file, "r", encoding="utf-8") as f:
        #     try:
        #         for line in f:
        #             line = line.strip()
        #             if line and not line.startswith("#"):
        #                 self.ref_audio_language = ref_audio_language_list[int(line) - 1]
        #                 break
        #     except Exception:
        #         raise ValueError("参考音频的语言参数文件读取错误")
        # #print(f"推理参考音频的语言为：{self.ref_audio_language}")

        #initialize_models(self.GPT_model_file,self.SoVITS_model_file)


    def change_character(self):
        if len(self.character_list) == 1:
            self.current_character_index = 0
        else:
            if self.current_character_index < len(self.character_list) - 1:
                self.current_character_index += 1
            else:
                self.current_character_index = 0
        if self.character_list[self.current_character_index].character_name == '祥子':
            self.if_sakiko = True
        else:
            self.if_sakiko = False
        self.GPT_model_file=self.character_list[self.current_character_index].GPT_model_path
        self.SoVITS_model_file = self.character_list[self.current_character_index].sovits_model_path
        self.ref_text_file=self.character_list[self.current_character_index].gptsovits_ref_audio_text
        self.ref_audio_language = self.character_list[self.current_character_index].gptsovits_ref_audio_lan
        if not self.if_sakiko:
            self.ref_audio_file=self.character_list[self.current_character_index].gptsovits_ref_audio
        self.neccerary_matirials=[0,self.GPT_model_file,self.SoVITS_model_file]
        self.to_gptsovits_com_queue.put(self.neccerary_matirials)   #修改模型

        self.is_change_complete = False
        while self.from_gptsovits_com_queue.empty():
            time.sleep(0.2)
        self.from_gptsovits_com_queue.get()
        while self.from_gptsovits_com_queue.empty():
            time.sleep(0.4)
        self.from_gptsovits_com_queue.get()
        self.is_change_complete = True





    def audio_generator(self,dp_chat,text_queue):
        #v1：调用API（需要额外安装GPTSoVITS依赖，而且推理耗时更长，并且用户退出程序后进程不自动结束），已废弃
        '''client = Client("http://localhost:9872/")
        result = client.predict(
            ref_wav_path=file(
                'Q:\mygo\\rana\GPT-SoVITS-v2-240821\output\slicer_opt\\rana_vocal_final.wav_0001564160_0001697920.wav'),
            prompt_text="やる。やる。にゃーん。お蕎麦。はい。おばあちゃんの。",
            prompt_language="日文",
            text=text,
            text_language=self.audio_language_choice,
            how_to_cut="不切",
            top_k=15,
            top_p=1,
            temperature=1,
            ref_free=False,
            speed=0.883,
            if_freeze=False,
            inp_refs=[],
            api_name="/get_tts_wav"
        )
        '''
        while True:

            while True:
                if not text_queue.empty():
                    text=text_queue.get()
                    break

                time.sleep(1)
            if text=='bye':
                self.to_gptsovits_com_queue.put('bye')
                break

            self.is_completed = False
            self.sakiko_which_state=dp_chat.sakiko_state
            if self.audio_language_choice=='日英混合':
                # if self.if_sakiko:
                #     self.speed=0.9
                # else:
                #     self.speed=0.88
                text = re.sub(r'CRYCHIC', 'クライシック',text,flags=re.IGNORECASE)
                text = re.sub(r'\bave\s*mujica\b', 'あヴぇムジカ', text, flags=re.IGNORECASE)
                text = re.sub(r'立希',( 'たき' if self.if_sakiko else 'りっき'), text, flags=re.IGNORECASE)  #りっきだよ、りっき！
                for key, value in self.replacements_jap.items():
                    text = re.sub(re.escape(key), value, text,flags=re.IGNORECASE)

            else:
                # if self.if_sakiko:
                #     self.speed=0.83
                # else:
                #     self.speed = 0.9
                for key, value in self.replacements_chi.items():
                    text = re.sub(re.escape(key), value, text, flags=re.IGNORECASE)

            # if text=="不能送去合成":
            #     text="嗯"
            #     self.audio_file_path='../reference_audio/silent_audio/silence.wav'
            #     self.is_completed = True
            #     return

            pattern = r'^[^A-Za-z0-9\u3040-\u30FF\u4E00-\u9FFF]+'    #去除句首的所有标点
            text=re.sub(pattern, '', text)
            text=text.replace(' ','')  #空格替换为逗号
            text = text.replace('...', '，')

            if text=='' or text=='不能送去合成':
                text='今年'
                flag=False
            else:
                flag=True
            if self.if_sakiko:
                if self.sakiko_which_state:     #黑白祥
                    ref_audio_file=self.ref_audio_file_black_sakiko
                    ref_text_file=self.ref_text_file_black_sakiko
                else:
                    ref_audio_file=self.ref_audio_file_white_sakiko
                    ref_text_file=self.ref_text_file_white_sakiko
            else:
                ref_audio_file=self.ref_audio_file
                ref_text_file=self.ref_text_file

            self.neccerary_matirials=[1,
                                      ref_audio_file,
                                      ref_text_file,
                                      self.ref_audio_language,
                                      text,
                                      self.audio_language_choice,
                                      self.program_output_path,
                                      self.speed,
                                      '不切',
                                      self.pause_second
                                      ]
            #print("sssssssss",text,'wwwwwwwwwwwwwwww')
            self.to_gptsovits_com_queue.put(self.neccerary_matirials)
            while True:
                if not self.from_gptsovits_com_queue2.empty():
                    self.message_queue.put(self.from_gptsovits_com_queue2.get())

                if not self.from_gptsovits_com_queue.empty():
                    self.audio_file_path = self.from_gptsovits_com_queue.get()
                    if not flag:
                        self.audio_file_path='../reference_audio/silent_audio/silence.wav'
                    #print(self.audio_file_path)
                    break

                time.sleep(0.2)
            #print('comcocmocmcocmoc')
            # self.audio_file_path = synthesize(
            #                                   ref_audio_path=ref_audio_file,
            #                                   ref_text_path=ref_text_file,
            #                                   ref_language=self.ref_audio_language,
            #                                   target_text=text,
            #                                   target_language=self.audio_language_choice,
            #                                   output_path=self.program_output_path,
            #                                   speed=self.speed,
            #                                   how_to_cut='不切',
            #                                   message_queue=message_queue)
            self.is_completed=True


if __name__=="__main__":

    a=AudioGenerate()
    a.initialize()
    from queue import Queue
    message_queue=Queue()
    while True:
        text=input(">>>")
        if text=='bye':
            break
        a.audio_language_choice='中英混合'
        a.audio_generator(text,True,message_queue=message_queue)
        import pygame
        pygame.mixer.init()
        pygame.mixer.music.load(a.audio_file_path)
        pygame.mixer.music.play()
