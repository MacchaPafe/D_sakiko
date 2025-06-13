import os,glob


class CharacterAttributes:
    def __init__(self):
        self.character_folder_name =''
        self.character_name=''
        self.icon_path=None
        self.live2d_json=''
        self.GPT_model_path=''
        self.sovits_model_path=''
        self.character_description=''
        self.gptsovits_ref_audio=''
        self.gptsovits_ref_audio_text = ''
        self.gptsovits_ref_audio_lan=''
        self.qt_css=None

    def print_attributes(self):
        for key, value in self.__dict__.items():
            print(f"{key} = {value}")

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

class GetCharacterAttributes:
    def __init__(self):
        self.character_num = 0
        self.character_class_list=[]
        self.load_data()

    def load_data(self):
        for char in os.listdir("../live2d_related"):
            full_path = os.path.join("../live2d_related", char)
            if  os.path.isdir(full_path):    #只遍历文件夹
                self.character_num+=1
                character=CharacterAttributes()
                character.character_folder_name=char

                if not os.path.exists(os.path.join(full_path,'name.txt')):
                    raise FileNotFoundError("没有找到name.txt文件！")
                with open(os.path.join(full_path,'name.txt'),'r',encoding='utf-8') as f:
                    character.character_name=f.read()
                    f.close()

                program_icon_path = glob.glob(os.path.join(full_path, f"*.png"))
                if program_icon_path:
                    program_icon_path=max(program_icon_path, key=os.path.getmtime)
                    character.icon_path=program_icon_path

                live2d_json=glob.glob(os.path.join(full_path,'live2D_model',f"*.model.json"))
                if not live2d_json:
                    raise FileNotFoundError("没有找到Live2D模型json文件(.model.json)")
                live2d_json=max(live2d_json, key=os.path.getmtime)
                character.live2d_json=live2d_json

                if not os.path.exists(os.path.join(full_path, 'character_description.txt')):
                    raise FileNotFoundError("没有找到角色描述文件！")
                with open(os.path.join(full_path,'character_description.txt'),'r',encoding='utf-8') as f:
                    character.character_description=f.read()
                    f.close()

                gpt_model_path=glob.glob(os.path.join('../reference_audio',char,'GPT-SoVITS_models',f"*.ckpt"))
                if not gpt_model_path:
                    raise FileNotFoundError(f"没有找到GPT模型文件(.ckpt)")
                gpt_model_path=max(gpt_model_path,key=os.path.getmtime)
                character.GPT_model_path=gpt_model_path

                SoVITS_model_file = glob.glob(os.path.join('../reference_audio',char,'GPT-SoVITS_models',f"*.pth"))
                if not SoVITS_model_file:
                    raise FileNotFoundError(f"没有找到SoVITS模型文件(.pth)")
                SoVITS_model_file = max(SoVITS_model_file, key=os.path.getmtime)
                character.sovits_model_path=SoVITS_model_file

                ref_audio_file_wav = glob.glob(os.path.join("../reference_audio",char, f"*.wav"))
                ref_audio_file_mp3 = glob.glob(os.path.join("../reference_audio",char, f"*.mp3"))
                if not ref_audio_file_wav + ref_audio_file_mp3:
                    raise FileNotFoundError(f"没有找到推理参考音频文件(.wav/.mp3)")
                ref_audio=max(ref_audio_file_mp3 + ref_audio_file_wav, key=os.path.getmtime)
                character.gptsovits_ref_audio=ref_audio
                
                if char!='sakiko':
                    if not os.path.exists(os.path.join("../reference_audio",char, 'reference_text.txt')):
                        raise FileNotFoundError(f"没有找到推理参考音频的文本文件！(reference_text.txt)")
                    character.gptsovits_ref_audio_text=os.path.join("../reference_audio",char, 'reference_text.txt')

                if not os.path.exists(os.path.join('../reference_audio',char,'reference_audio_language.txt')):
                    raise FileNotFoundError("没有找到参考音频语言文件！")
                ref_audio_language_file =os.path.join('../reference_audio',char,'reference_audio_language.txt')
                with open(ref_audio_language_file, "r", encoding="utf-8") as f:
                    try:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#"):
                                ref_audio_language = ref_audio_language_list[int(line) - 1]
                                break
                        character.gptsovits_ref_audio_lan = ref_audio_language
                        f.close()
                    except Exception:
                        raise ValueError("参考音频的语言参数文件读取错误")

                if os.path.exists(os.path.join("../reference_audio",char, 'QT_style.json')):
                    with open(os.path.join("../reference_audio",char, 'QT_style.json'),'r',encoding="utf-8") as f:
                        character.qt_css=f.read()
                        f.close()

                self.character_class_list.append(character)

if __name__=="__main__":

    a=GetCharacterAttributes()
    print(a.character_num)
    a.character_class_list[0].print_attributes()
    a.character_class_list[1].print_attributes()

