import os
import glob

from inference_cli import synthesize

import re





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
        self.ref_audio_file_white_sakiko = '../reference_audio/white_sakiko.wav'
        self.ref_audio_file_black_sakiko = '../reference_audio/black_sakiko.wav'
        self.ref_audio_language =''
        self.ref_text_file_white_sakiko = '../reference_audio/reference_text_white_sakiko.txt'
        self.ref_text_file_black_sakiko = '../reference_audio/reference_text_black_sakiko.txt'
        self.program_output_path="../reference_audio/generated_audios_temp"
        self.speed=1.0

        self.audio_file_path=''
        self.is_completed=False
        self.audio_language_choice = "中文"
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
            '立希': 'たき',
            '素世': 'そよ',
            '爽世': 'そよ',
            '千早愛音':'ちはやあのん',
            '愛音': 'あのん',
            '要楽奈':'かなめらーな',
            '楽奈': 'らーな',
            '春日影':'はるひかげ',
            'Doloris':'ドロリス',
            'Mortis':'モーティス',
            'Timoris':'ティモリス',
            'Amoris':'アモーリス',
            'Oblivionis':'オブリビオニス',
            'live':'ライブ',
            'MyGO':'まいご'
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
            'MyGO':'mygo'
        }


    def initialize(self):
        self.GPT_model_file = glob.glob(os.path.join("../GPT_weights_v2", f"*.ckpt"))
        if not self.GPT_model_file:
            raise FileNotFoundError(f"没有找到GPT模型文件(.ckpt)")
        self.GPT_model_file = max(self.GPT_model_file, key=os.path.getmtime)  # 找出最新的文件
        #print(f"GPT模型文件...OK")

        self.SoVITS_model_file = glob.glob(os.path.join("../SoVITS_weights_v2", f"*.pth"))
        if not self.SoVITS_model_file:
            raise FileNotFoundError(f"没有找到SoVITS模型文件(.pth)")
        self.SoVITS_model_file = max(self.SoVITS_model_file, key=os.path.getmtime)
        #print(f"SoVITS模型文件...OK")

        ref_audio_file_wav = glob.glob(os.path.join("../reference_audio", f"*.wav"))
        ref_audio_file_mp3 = glob.glob(os.path.join("../reference_audio", f"*.mp3"))
        if not ref_audio_file_wav + ref_audio_file_mp3:
            raise FileNotFoundError(f"没有找到推理参考音频文件(.wav/.mp3)")
        #self.ref_audio_file = max(ref_audio_file_mp3 + ref_audio_file_wav, key=os.path.getmtime)   #黑白祥特殊设计，这句就不要了
        #print(f"推理参考音频文件...OK")

        ref_audio_language_file = '../reference_audio/reference_audio_language.txt'
        with open(ref_audio_language_file, "r", encoding="utf-8") as f:
            try:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self.ref_audio_language = ref_audio_language_list[int(line) - 1]
                        break
            except Exception:
                raise ValueError("参考音频的语言参数文件读取错误")
        #print(f"推理参考音频的语言为：{self.ref_audio_language}")



    def audio_generator(self,text,which_state):
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

        self.is_completed=False

        if self.audio_language_choice=='日英混合':
            self.speed=0.9
            text = re.sub(r'CRYCHIC', 'クライシック',text,flags=re.IGNORECASE)
            text = re.sub(r'\bave\s*mujica\b', 'あヴぇムジカ', text, flags=re.IGNORECASE)
            for key, value in self.replacements_jap.items():
                text = re.sub(re.escape(key), value, text,flags=re.IGNORECASE)

        else:
            self.speed=0.83
            for key, value in self.replacements_chi.items():
                text = re.sub(re.escape(key), value, text, flags=re.IGNORECASE)

        if text=="不能送去合成":
            self.audio_file_path='../reference_audio/silent_audio/silence.wav'
            self.is_completed = True
            return

        pattern = r'^[^A-Za-z0-9\u3040-\u30FF\u4E00-\u9FFF]+'    #去除句首的所有标点
        text=re.sub(pattern, '', text)
        text=text.replace(' ','')  #空格替换为逗号
        text = text.replace('...', '，')
        if which_state:     #黑白祥
            ref_audio_file=self.ref_audio_file_black_sakiko
            ref_text_file=self.ref_text_file_black_sakiko
        else:
            ref_audio_file=self.ref_audio_file_white_sakiko
            ref_text_file=self.ref_text_file_white_sakiko
        self.audio_file_path = synthesize(GPT_model_path=self.GPT_model_file,
                                          SoVITS_model_path=self.SoVITS_model_file,
                                          ref_audio_path=ref_audio_file,
                                          ref_text_path=ref_text_file,
                                          ref_language=self.ref_audio_language,
                                          target_text=text,
                                          target_language=self.audio_language_choice,
                                          output_path=self.program_output_path,
                                          speed=self.speed,
                                          how_to_cut='按中文句号。切')
        self.is_completed=True


if __name__=="__main__":
    a=AudioGenerate()
    a.initialize()
    while True:
        text=input(">>>")
        if text=='bye':
            break
        a.audio_generator(text,True)
        import pygame
        pygame.mixer.init()
        pygame.mixer.music.load(a.audio_file_path)
        pygame.mixer.music.play()
