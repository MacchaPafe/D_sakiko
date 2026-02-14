import os,glob,json,copy



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

def is_old_l2d_json(old_l2d_json_path) -> bool:
    """
        判断是否为老版 Live2D model.json 格式。
    """
    try:
        with open(old_l2d_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"错误，读取 Live2D JSON 文件失败: {e}")
        return False

    if 'motions' in data and 'rana' in data['motions']:
        return True
    return False

def convert_old_l2d_json(old_l2d_json_path):
    """
        读取老版 Live2D model.json，转换为新版格式并返回字典。
    """
    with open(old_l2d_json_path, 'r', encoding='utf-8') as f:
        old_data = json.load(f)


    new_data = copy.deepcopy(old_data)
    if 'controllers' in new_data:
        del new_data['controllers']

    if 'hit_areas' in new_data:
        del new_data['hit_areas']
    # 处理 Motions
    old_rana_list = old_data.get('motions', {}).get('rana', [])
    if not old_rana_list:
        return  # 如果没有rana字段，认为是新版格式，直接返回
    # 定义新版 13 个分类的切片映射关系
    # 格式: (新键名, 起始索引, 结束索引)
    motion_mapping = [
        ("happiness", 0, 6),  # 0-5 (共6个)
        ("sadness", 6, 12),  # 6-11 (共6个)
        ("anger", 12, 18),  # 12-17 (共6个)
        ("disgust", 18, 24),  # 18-23 (共6个)
        ("like", 24, 30),  # 24-29 (共6个)
        ("surprise", 30, 36),  # 30-35 (共6个)
        ("fear", 36, 42),  # 36-41 (共6个)
        ("IDLE", 42, 51),  # 42-50 (共9个)
        ("text_generating", 51, 54),  # 51-53 (共3个)
        ("bye", 54, 56),  # 54-55 (共2个)
        ("change_character", 56, 59),  # 56-58 (共3个)
        ("idle_motion", 59, 60),  # 59 (共1个)
        ("talking_motion", 60, 61)  # 60 (共1个)
    ]

    new_motions = {}
    for name, start_idx, end_idx in motion_mapping:
        # 进行切片操作。如果 old_rana_list 长度不够，Python 不会报错，只会返回空列表或部分列表
        new_motions[name] = old_rana_list[start_idx:end_idx]

    # 将重构好的 motions 覆盖回去
    new_data['motions'] = new_motions

    with open(old_l2d_json_path, 'w', encoding='utf-8') as f:
        json.dump(new_data, f, ensure_ascii=False, indent=4)
        f.close()




class GetCharacterAttributes:
    def __init__(self):
        self.character_num = 0
        self.character_class_list=[]
        self.load_data()
        print('所有角色：')
        for char in self.character_class_list:
            print(char.character_name, end=' ')
        print('\n')

    def load_data(self):
        if os.path.exists("../dsakiko_config.json"):
            with open("../dsakiko_config.json",'r',encoding='utf-8') as f:
                config_data=json.load(f)
            l2d_json_paths_dict=config_data.get("l2d_json_paths",None)
        else:
            l2d_json_paths_dict=None
        for char in os.listdir("../live2d_related"):
            full_path = os.path.join("../live2d_related", char)
            if  os.path.isdir(full_path):    #只遍历文件夹
                self.character_num+=1
                character=CharacterAttributes()
                character.character_folder_name=char

                is_ready = True  # 为了显示全报错信息
                if not os.path.exists(os.path.join(full_path,'name.txt')):
                    print(f"[Error]没有找到角色：'{char}'的name.txt文件！")
                    is_ready=False
                    character.character_name=char   #只是为了下面不报错
                else:
                    with open(os.path.join(full_path,'name.txt'),'r',encoding='utf-8') as f:
                        character.character_name=f.read()
                        f.close()

                program_icon_path = glob.glob(os.path.join(full_path, f"*.png"))
                if program_icon_path:
                    program_icon_path=max(program_icon_path, key=os.path.getmtime)
                    character.icon_path=program_icon_path

                live2d_json=glob.glob(os.path.join(full_path,'live2D_model',f"*.model.json"))
                if not live2d_json:
                    print(f"[Error]没有找到角色：'{character.character_name}'的默认Live2D模型json文件(.model.json)")
                    is_ready=False
                if (l2d_json_paths_dict is not None) and (character.character_name in l2d_json_paths_dict):
                    if os.path.exists(l2d_json_paths_dict[character.character_name]):
                        live2d_json=l2d_json_paths_dict[character.character_name]
                    else:
                        live2d_json = max(live2d_json, key=os.path.getmtime)
                    character.live2d_json=live2d_json
                else:
                    live2d_json=max(live2d_json, key=os.path.getmtime)
                    if is_old_l2d_json(live2d_json):
                        try:
                            convert_old_l2d_json(live2d_json)
                        except Exception as e:
                            print(f"[Error]角色：'{character.character_name}'的旧版Live2D模型json文件(.model.json)转换失败！错误信息：{e}")
                            del character
                            continue
                        print(f"已将角色：{character.character_name} 的旧版Live2D模型json文件(.model.json)转换为新版格式并覆盖保存。")
                        character.live2d_json=live2d_json
                    else:
                        character.live2d_json=live2d_json

                if not os.path.exists(os.path.join(full_path, 'character_description.txt')):
                    print(f"[Error]没有找到角色：'{character.character_name}'的角色描述文件！")
                    is_ready=False
                else:
                    with open(os.path.join(full_path,'character_description.txt'),'r',encoding='utf-8') as f:
                        character.character_description=f.read()
                        f.close()

                gpt_model_path=glob.glob(os.path.join('../reference_audio',char,'GPT-SoVITS_models',f"*.ckpt"))
                if not gpt_model_path:
                    print(f"[Error]没有找到角色：'{character.character_name}'的GPT模型文件(.ckpt)，前往reference_audio/{char}/GPT-SoVITS_models/ 文件夹放入对应模型文件。")
                    is_ready=False
                else:
                    gpt_model_path=max(gpt_model_path,key=os.path.getmtime)
                    character.GPT_model_path=gpt_model_path

                SoVITS_model_file = glob.glob(os.path.join('../reference_audio',char,'GPT-SoVITS_models',f"*.pth"))
                if not SoVITS_model_file:
                    print(
                        f"[Error]没有找到角色：'{character.character_name}'的SoVITS模型文件(.pth)，请前往reference_audio/{char}/GPT-SoVITS_models/ 文件夹放入对应模型文件。")
                    is_ready=False
                else:
                    SoVITS_model_file = max(SoVITS_model_file, key=os.path.getmtime)
                    character.sovits_model_path=SoVITS_model_file

                #加载上次设置的参考音频（如果有设置过），而不是每次都用最新的参考音频
                if char!='sakiko':
                    if os.path.exists(os.path.join("../reference_audio",char, f"default_ref_audio.txt")):
                        with open(os.path.join("../reference_audio",char, f"default_ref_audio.txt"),'r',encoding='utf-8') as f:
                            default_ref_audio_path=f.read().strip()
                            f.close()
                        if os.path.exists(default_ref_audio_path):
                            character.gptsovits_ref_audio=default_ref_audio_path
                    else:
                        ref_audio_file_wav = glob.glob(os.path.join("../reference_audio", char, f"*.wav"))
                        ref_audio_file_mp3 = glob.glob(os.path.join("../reference_audio", char, f"*.mp3"))
                        if not ref_audio_file_wav + ref_audio_file_mp3:
                            print(
                                f"[Error]没有找到角色：'{character.character_name}'的推理参考音频文件(.wav/.mp3)")
                            is_ready=False
                        else:
                            ref_audio=max(ref_audio_file_mp3 + ref_audio_file_wav, key=os.path.getmtime)
                            character.gptsovits_ref_audio=ref_audio
                
                if char!='sakiko':
                    if not os.path.exists(os.path.join("../reference_audio",char, 'reference_text.txt')):
                        print(f"[Error]没有找到角色：'{character.character_name}'的推理参考音频的文本文件！(reference_text.txt)")
                        is_ready=False
                    else:
                        character.gptsovits_ref_audio_text=os.path.join("../reference_audio",char, 'reference_text.txt')

                if not os.path.exists(os.path.join('../reference_audio',char,'reference_audio_language.txt')):
                    print(f"[Error]没有找到角色：'{character.character_name}'的参考音频语言文件！")
                    is_ready=False
                else:
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
                            print(f"[Warning]角色：'{character.character_name}'的参考音频的语言参数文件读取错误，使用默认语言日文。")
                            character.gptsovits_ref_audio_lan = "日文"

                if os.path.exists(os.path.join("../reference_audio",char, 'QT_style.json')):
                    with open(os.path.join("../reference_audio",char, 'QT_style.json'),'r',encoding="utf-8") as f:
                        character.qt_css=f.read()
                        f.close()

                if is_ready:
                    self.character_class_list.append(character)
                else:
                    print(f"加载角色：'{char}' 时出现以上错误，跳过该角色的加载。\n")

        #新增调整角色顺序的功能
        if os.path.exists("../reference_audio/character_order.json"):
            with open("../reference_audio/character_order.json",'r',encoding='utf-8') as f:
                char_order_list=json.load(f)
                f.close()
        elif os.path.exists("../dsakiko_config.json"):
            with open("../dsakiko_config.json",'r',encoding='utf-8') as f:
                config=json.load(f)
                char_order_list=config["character_setting"]["character_order"]
                f.close()
        else:
            print("[Warning]没有找到启动配置文件！")
            return
        if len(self.character_class_list)>int(char_order_list['character_num']):
            is_convert_1=False
            print("似乎有新角色加入了，之前设置的角色顺序不适用，重新设置一下吧")
        elif len(self.character_class_list)<int(char_order_list['character_num']):
            is_convert_1=False
            print("似乎有角色被删除了，之前设置的角色顺序不适用，重新设置一下吧")
        else:
            is_convert_1=True
        this_character_names=[char.character_name for char in self.character_class_list]
        is_convert_2=True
        if is_convert_1:
            for name in this_character_names:
                if name not in char_order_list['character_names']:
                    print("似乎有角色的名字被修改了，之前设置的角色顺序不适用，重新设置一下吧")
                    is_convert_2=False
                    break
        if is_convert_1 and is_convert_2:
            new_character_class_list=[]
            char_name2char={char.character_name:char for char in self.character_class_list}
            for name in char_order_list['character_names']:
                new_character_class_list.append(char_name2char[name])

            self.character_class_list=new_character_class_list





if __name__=="__main__":

    a=GetCharacterAttributes()
    print(a.character_num)
    a.character_class_list[0].print_attributes()
    a.character_class_list[1].print_attributes()

