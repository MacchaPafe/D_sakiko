import os,glob,json
from typing import Optional

from qconfig import d_sakiko_config


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


class Character:
    def __init__(self):
        # live2d_related 下的角色文件夹名称
        self.character_folder_name =''
        # 角色名称（从 name.txt 读取）
        self.character_name=''
        # 角色图标路径（会用于程序图标）
        self.icon_path: Optional[str] = None
        # Live2D 模型 json 文件路径
        self.live2d_json=''
        # GPT 模型的 ckpt 文件路径
        self.GPT_model_path=''
        # SoVITS 模型的 pth 文件路径
        self.sovits_model_path=''
        # 角色描述（从 character_description.txt 读取），用作 AI 提示词
        self.character_description=''
        # 推理参考音频文件路径
        self.gptsovits_ref_audio=''
        # 推理参考音频的文本存储在的文件路径
        self.gptsovits_ref_audio_text = ''
        # 推理参考音频语言
        self.gptsovits_ref_audio_lan=''
        # 角色对话时，界面的 Qt 样式表
        self.qt_css=None
        # 角色的 RAG 知识库路径，可以是 knowledge.txt 文件或 knowledge_db 文件夹（还没做完）
        self.personal_knowledge_path = None
        # 标记该角色是否为用户扮演的角色（无语音模型，仅有人设）
        self.is_user = False
        # 如果用户选择扮演已有角色，那么此属性应当指向该角色的 Character 实例
        self.user_as_character = None

    def print_attributes(self):
        for key, value in self.__dict__.items():
            print(f"{key} = {value}")

    @staticmethod
    def create_user(name="User", description="", icon_path=None):
        """
        创建一个用户角色实例
        """
        character = Character()
        character.character_name = name
        character.character_description = description
        character.icon_path = icon_path
        character.is_user = True
        # 用户角色没有模型路径，保持默认的空值或None
        return character

    @staticmethod
    def load_from_folder(folder_path):
        """
        从指定文件夹加载角色信息
        """
        if not os.path.isdir(folder_path):
            return None
        
        char_folder_name = os.path.basename(folder_path)
        character = Character()
        character.character_folder_name = char_folder_name

        # Name
        name_path = os.path.join(folder_path, 'name.txt')
        if not os.path.exists(name_path):
            raise FileNotFoundError(f"没有找到角色：'{char_folder_name}'的name.txt文件！")
        with open(name_path, 'r', encoding='utf-8') as f:
            character.character_name = f.read().strip()

        # Icon
        program_icon_path = glob.glob(os.path.join(folder_path, "*.png"))
        if program_icon_path:
            character.icon_path = max(program_icon_path, key=os.path.getmtime)

        # Live2D Model
        live2d_json = glob.glob(os.path.join(folder_path, 'live2D_model', "*.model.json"))
        if not live2d_json:
            raise FileNotFoundError(f"没有找到角色：'{character.character_name}'的Live2D模型json文件(.model.json)")
        character.live2d_json = max(live2d_json, key=os.path.getmtime)

        # Description
        desc_path = os.path.join(folder_path, 'character_description.txt')
        if not os.path.exists(desc_path):
            raise FileNotFoundError(f"没有找到角色：'{character.character_name}'的角色描述文件！")
        with open(desc_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            # 避免重复添加，因为可以在程序中保存角色描述了
            if not content.endswith("最后，你绝对不会与用户建立恋爱关系，你必须严格符合原作的角色形象！"):
                character.character_description = content + "\n最后，你绝对不会与用户建立恋爱关系，你必须严格符合原作的角色形象！"

        # GPT-SoVITS Models & Audio
        # Construct path to reference_audio folder
        # Assuming standard structure: ../reference_audio/{char_name} relative to ../live2d_related/{char_name}
        
        # Reference Audio 基准文件夹路径
        ref_audio_base = os.path.join("../reference_audio", char_folder_name)
        
        # GPT Model
        gpt_model_path = glob.glob(os.path.join(ref_audio_base, 'GPT-SoVITS_models', "*.ckpt"))
        if not gpt_model_path:
            raise FileNotFoundError(f"没有找到角色：'{character.character_name}'的GPT模型文件(.ckpt)")
        character.GPT_model_path = max(gpt_model_path, key=os.path.getmtime)

        # SoVITS Model
        sovits_model_path = glob.glob(os.path.join(ref_audio_base, 'GPT-SoVITS_models', "*.pth"))
        if not sovits_model_path:
            raise FileNotFoundError(f"没有找到角色：'{character.character_name}'的SoVITS模型文件(.pth)")
        character.sovits_model_path = max(sovits_model_path, key=os.path.getmtime)

        # Reference Audio
        ref_audio_file_wav = glob.glob(os.path.join(ref_audio_base, "*.wav"))
        ref_audio_file_mp3 = glob.glob(os.path.join(ref_audio_base, "*.mp3"))
        if not (ref_audio_file_wav + ref_audio_file_mp3):
            raise FileNotFoundError(f"没有找到角色：'{character.character_name}'的推理参考音频文件(.wav/.mp3)")
        character.gptsovits_ref_audio = max(ref_audio_file_mp3 + ref_audio_file_wav, key=os.path.getmtime)

        # Reference Text
        if char_folder_name != 'sakiko':
            ref_text_path = os.path.join(ref_audio_base, 'reference_text.txt')
            if not os.path.exists(ref_text_path):
                raise FileNotFoundError(f"没有找到角色：'{character.character_name}'的推理参考音频的文本文件！(reference_text.txt)")
            character.gptsovits_ref_audio_text = ref_text_path

        # Reference Language
        ref_lan_path = os.path.join(ref_audio_base, 'reference_audio_language.txt')
        if not os.path.exists(ref_lan_path):
            raise FileNotFoundError(f"没有找到角色：'{character.character_name}'的参考音频语言文件！")
        
        with open(ref_lan_path, "r", encoding="utf-8") as f:
            try:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        character.gptsovits_ref_audio_lan = ref_audio_language_list[int(line) - 1]
                        break
            except Exception:
                raise ValueError(f"角色：'{character.character_name}'的参考音频的语言参数文件读取错误")

        # QT Style
        qt_style_path = os.path.join(ref_audio_base, 'QT_style.json')
        if os.path.exists(qt_style_path):
            with open(qt_style_path, 'r', encoding="utf-8") as f:
                character.qt_css = f.read()

        # RAG Knowledge
        # Check for knowledge.txt or knowledge_db folder in live2d_related folder
        knowledge_txt = os.path.join(folder_path, 'knowledge.txt')
        knowledge_db = os.path.join(folder_path, 'knowledge_db')
        
        if os.path.exists(knowledge_txt):
            character.personal_knowledge_path = knowledge_txt
        elif os.path.exists(knowledge_db):
             character.personal_knowledge_path = knowledge_db
             
        return character


class CharacterManager:
    """
    从各个文件夹中读取角色信息、模型等内容并整合，以便各个模块直接使用
    显然启动程序时扫描一次角色信息就够，因此，此类设计为单例模式
    """
    __instance = None

    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super().__new__(cls)
        return cls.__instance

    def __init__(self):
        # 限制运行时总共只扫描一次
        if hasattr(self, 'initialized') and self.initialized:
            return
        self.initialized = True

        self.character_num = 0
        self.character_class_list=[]
        # 用户对自己的人格设定
        self.user_characters=[]

        self.load_data()
        print('所有角色：')
        for char in self.character_class_list:
            print(char.character_name, end=' ')
        print('\n')

    def load_data(self):
        """
        加载并获得角色的所有信息
        """
        # 1. 加载普通角色
        # 扫描 live2d_related 文件夹下的各个角色文件夹
        # 我们认为每个子文件夹都是一个角色
        for char_folder in os.listdir("../live2d_related"):
            full_path = os.path.join("../live2d_related", char_folder)
            if os.path.isdir(full_path):
                character = Character.load_from_folder(full_path)
                if character:
                    self.character_num += 1
                    self.character_class_list.append(character)

        # 2. 加载用户角色
        # 尝试从配置中读取用户角色列表，如果不存在则创建一个默认的
        user_configs = d_sakiko_config.user_characters.value
        
        if not user_configs:
            # 如果没有配置或配置为空，生成一个默认用户角色
            default_user = Character.create_user(name="User", description="")
            self.user_characters.append(default_user)
        else:
            for conf in user_configs:
                user = Character.create_user(
                    name=conf.get('name', 'User'),
                    description=conf.get('description', ''),
                    icon_path=conf.get('avatar_path', None)
                )
                if conf.get("user_as_character_name"):
                    # 如果配置中指定了 user_as_character_name，则尝试关联已有角色
                    for char in self.character_class_list:
                        if char.character_name == conf["user_as_character_name"]:
                            user.user_as_character = char
                            break

                self.user_characters.append(user)

        #新增调整角色顺序的功能
        if len(self.character_class_list)>int(d_sakiko_config.character_order.value['character_num']):
            is_convert_1=False
            print("似乎有新角色加入了，之前设置的角色顺序不适用，重新设置一下吧")
        elif len(self.character_class_list)<int(d_sakiko_config.character_order.value['character_num']):
            is_convert_1=False
            print("似乎有角色被删除了，之前设置的角色顺序不适用，重新设置一下吧")
        else:
            is_convert_1=True
        this_character_names=[char.character_name for char in self.character_class_list]
        is_convert_2=True
        if is_convert_1:
            for name in this_character_names:
                if name not in d_sakiko_config.character_order.value['character_names']:
                    print("似乎有角色的名字被修改了，之前设置的角色顺序不适用，重新设置一下吧")
                    is_convert_2=False
                    break
        if is_convert_1 and is_convert_2:
            new_character_class_list=[]
            char_name2char={char.character_name:char for char in self.character_class_list}
            for name in d_sakiko_config.character_order.value['character_names']:
                if name in char_name2char:
                    new_character_class_list.append(char_name2char[name])

            self.character_class_list=new_character_class_list

        # 将最终的结果同步到配置中
        self.save_data()

    def save_data(self):
        """
        保存角色列表缓存信息和用户自定义人格等内容到配置文件中
        """
        d_sakiko_config.character_order.value['character_names'] = [char.character_name for char in
                                                                    self.character_class_list]
        d_sakiko_config.character_order.value['character_num'] = self.character_num
        d_sakiko_config.user_characters.value = [
            {
                'name': user_char.character_name,
                'description': user_char.character_description,
                'avatar_path': user_char.icon_path,
                'user_as_character_name': user_char.user_as_character.character_name if user_char.user_as_character else None
            }
            for user_char in self.user_characters
        ]

        d_sakiko_config.save()

# Alias for backward compatibility
CharacterAttributes = Character
GetCharacterAttributes = CharacterManager

if __name__=="__main__":

    a=CharacterManager()
    print(a.character_num)
    if len(a.character_class_list) > 0:
        a.character_class_list[0].print_attributes()
    if len(a.character_class_list) > 1:
        a.character_class_list[1].print_attributes()

