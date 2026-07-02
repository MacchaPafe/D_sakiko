from __future__ import annotations

import glob
import json
import os
import sys
import uuid

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from live2d_support.model_normalizer import (
    convert_old_l2d_json,
    is_l2d_model3_json,
    is_old_l2d_json,
    normalize_model3_for_project as rebuild_model3_motion_groups,
)
from qconfig import d_sakiko_config
from log import get_logger


logger = get_logger(__name__)


class CharacterAttributes:
    def __init__(self):
        # 角色文件夹单层目录的名称，比如“sakiko“
        self.character_folder_name: str = ''
        # 角色文件夹内，name.txt 记录的角色名称，比如“祥子“
        self.character_name: str = ''
        # 角色图标（文件夹内一个 .png 文件）的相对路径。如果该图标存在，切换到该角色时，应用程序会切换为此图标。
        self.icon_path: str | None = None
        # 角色的 live2d 模型的 model.json 所在的相对路径。这里只会指向角色的默认模型，不会指向自定义模型
        self.live2d_json: str = ''
        # 角色的 GPT (t2s）模型相对路径
        self.GPT_model_path: str | None = ''
        # 角色的 sovits 模型相对路径
        self.sovits_model_path: str | None = ''
        # 角色的描述，即角色文件夹内 character_description.txt 记录的内容
        self.character_description: str = ''
        # 角色语音模型参考音频的相对路径。祥子的该属性为 None。
        self.gptsovits_ref_audio: str | None = ''
        # 角色语音模型参考音频文本的相对路径。祥子的该属性为 None。
        self.gptsovits_ref_audio_text: str | None = ''
        # 角色语音模型参考音频的语言（例如‘日文’）
        self.gptsovits_ref_audio_lan: str | None = ''
        # 角色的 qt css 样式表
        self.qt_css: str | None = None

        # 用户人设相关属性
        # 用户人设的稳定标识。系统角色没有 persona_id。
        self.persona_id: str | None = None
        # 标记当前对象是否表示用户人设。
        self.is_user: bool = False
        # 用户选择扮演已有角色时，指向对应的系统角色。
        self.user_as_character: CharacterAttributes | None = None

    @property
    def is_default_user(self) -> bool:
        """判断当前对象是否为内置的空白用户人设。"""
        return self.persona_id == "default"

    @property
    def effective_character(self) -> CharacterAttributes:
        """返回当前实际生效的角色；用户扮演系统角色时返回对应系统角色。"""
        return self.user_as_character or self

    @property
    def effective_character_name(self) -> str:
        """返回当前实际生效的角色名称。"""
        return self.effective_character.character_name

    @property
    def effective_character_description(self) -> str:
        """返回当前实际生效的角色描述。"""
        return self.effective_character.character_description

    @property
    def effective_icon_path(self) -> str | None:
        """返回当前实际生效的角色头像路径。"""
        return self.effective_character.icon_path

    @classmethod
    def create_user(
            cls,
            name: str = "User",
            description: str = "",
            icon_path: str | None = None,
            persona_id: str | None = None,
    ) -> CharacterAttributes:
        """创建一个用户人设对象。"""
        character = cls()
        character.persona_id = persona_id or uuid.uuid4().hex
        character.character_name = name
        character.character_description = description
        character.icon_path = icon_path
        character.is_user = True
        return character

    @classmethod
    def create_default_user(cls) -> CharacterAttributes:
        """创建不会向模型提供人设信息的内置默认用户。"""
        return cls.create_user(persona_id="default")

    @staticmethod
    def _path_exists(path: str | None) -> bool:
        """判断给定路径是否存在。"""
        return path is not None and os.path.exists(path)

    def has_valid_voice_model(self) -> bool:
        """判断当前角色是否具备可用的语音生成配置。"""
        # 所有角色都必须具有 gpt 模型/sovits 模型
        has_models = self._path_exists(self.GPT_model_path) and self._path_exists(self.sovits_model_path)
        if not has_models:
            return False
        if not self.gptsovits_ref_audio_lan:
            return False
        # 祥子的参考音频和参考文本由语音生成模块动态选择。
        if self.character_name == '祥子':
            return True
        return (
            self._path_exists(self.gptsovits_ref_audio)
            and self._path_exists(self.gptsovits_ref_audio_text)
        )

    def print_attributes(self) -> None:
        """打印当前角色对象的全部属性。"""
        for key, value in self.__dict__.items():
            logger.debug("%s = %s", key, value)

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


def find_default_l2d_json(model_dir: str) -> str | None:
    """
        查找角色默认 Live2D 模型 JSON。
        V2 使用 *.model.json，V3 使用 *.model3.json；优先保留现有 V2 行为。
    """
    for pattern in ("*.model.json", "*.model3.json"):
        candidates = glob.glob(os.path.join(model_dir, pattern))
        if candidates:
            return max(candidates, key=os.path.getmtime)
    return None


class GetCharacterAttributes:
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
        self.character_class_list: list[CharacterAttributes] = []
        self.user_characters: list[CharacterAttributes] = []
        self.load_data()
        logger.info('所有角色：')
        logger.info(' '.join([char.character_name for char in self.character_class_list]))

    def load_data(self) -> None:
        """加载系统角色和用户人设。"""
        # 角色的默认 live2d 信息
        l2d_json_paths_dict = d_sakiko_config.l2d_json_paths_dict.value
        # 有多少角色没有完整的信息（is_ready = False）
        partial_character_count = 0

        for char in os.listdir("../live2d_related"):
            full_path = os.path.join("../live2d_related", char)
            if  os.path.isdir(full_path):    #只遍历文件夹
                self.character_num+=1
                character=CharacterAttributes()
                character.character_folder_name=char

                is_ready = True  # 为了显示全报错信息
                if not os.path.exists(os.path.join(full_path,'name.txt')):
                    logger.error("没有找到角色：'%s' 的 name.txt 文件！", char)
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

                live2d_json = find_default_l2d_json(os.path.join(full_path, 'live2D_model'))
                if not live2d_json:
                    logger.error("没有找到角色：'%s' 的默认 Live2D 模型 json 文件(.model.json/.model3.json)", character.character_name)
                    is_ready=False
                if character.character_name in l2d_json_paths_dict:
                    if os.path.exists(l2d_json_paths_dict[character.character_name]):
                        live2d_json=l2d_json_paths_dict[character.character_name]
                if live2d_json:
                    if is_old_l2d_json(live2d_json):
                        try:
                            convert_old_l2d_json(live2d_json)
                        except Exception as e:
                            logger.exception("角色：'%s' 的旧版 Live2D 模型 json 文件(.model.json)转换失败", character.character_name)
                            del character
                            continue
                        logger.info("已将角色：%s 的旧版 Live2D 模型 json 文件(.model.json)转换为新版格式并覆盖保存。", character.character_name)
                        character.live2d_json=live2d_json
                    elif is_l2d_model3_json(live2d_json):
                        try:
                            if rebuild_model3_motion_groups(live2d_json):
                                logger.info("已将角色：%s 的 Live2D V3 Motions 重构为项目标准动作组。", character.character_name)
                        except Exception as e:
                            logger.exception("角色：'%s' 的 Live2D V3 模型 json 文件(.model3.json)检查失败", character.character_name)
                            del character
                            continue
                        character.live2d_json=live2d_json
                    else:
                        character.live2d_json=live2d_json

                if not os.path.exists(os.path.join(full_path, 'character_description.txt')):
                    logger.error("没有找到角色：'%s' 的角色描述文件！", character.character_name)
                    is_ready=False
                else:
                    with open(os.path.join(full_path,'character_description.txt'),'r',encoding='utf-8') as f:
                        character.character_description=f.read()
                        f.close()

                gpt_model_path=glob.glob(os.path.join('../reference_audio',char,'GPT-SoVITS_models',f"*.ckpt"))
                if not gpt_model_path:
                    logger.warning("没有找到角色：'%s' 的 GPT 模型文件(.ckpt)，前往 reference_audio/%s/GPT-SoVITS_models/ 文件夹放入对应模型文件。本次运行无法进行语音生成。", character.character_name, char)
                    character.GPT_model_path=None
                else:
                    gpt_model_path=max(gpt_model_path,key=os.path.getmtime)
                    character.GPT_model_path=gpt_model_path

                SoVITS_model_file = glob.glob(os.path.join('../reference_audio',char,'GPT-SoVITS_models',f"*.pth"))
                if not SoVITS_model_file:
                    logger.warning(
                        "没有找到角色：'%s' 的 SoVITS 模型文件(.pth)，请前往 reference_audio/%s/GPT-SoVITS_models/ 文件夹放入对应模型文件。本次运行无法进行语音生成。",
                        character.character_name,
                        char,
                    )
                    character.sovits_model_path=None
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
                            logger.warning(
                                "没有找到角色：'%s' 的推理参考音频文件(.wav/.mp3)，本次运行无法进行语音生成。",
                                character.character_name,
                            )

                            character.gptsovits_ref_audio=None
                        else:
                            ref_audio=max(ref_audio_file_mp3 + ref_audio_file_wav, key=os.path.getmtime)
                            character.gptsovits_ref_audio=ref_audio

                if char!='sakiko':
                    if not os.path.exists(os.path.join("../reference_audio",char, 'reference_text.txt')):
                        logger.warning(
                            "没有找到角色：'%s' 的推理参考音频文本文件(reference_text.txt)，本次运行无法进行语音生成。",
                            character.character_name,
                        )
                        character.gptsovits_ref_audio_text = None
                    else:
                        character.gptsovits_ref_audio_text=os.path.join("../reference_audio",char, 'reference_text.txt')

                if not os.path.exists(os.path.join('../reference_audio',char,'reference_audio_language.txt')):
                    logger.warning(
                        "没有找到角色：'%s' 的参考音频语言文件(reference_audio_language.txt)，本次运行无法进行语音生成。",
                        character.character_name,
                    )
                    character.gptsovits_ref_audio_lan = None
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
                            logger.warning("角色：'%s' 的参考音频的语言参数文件读取错误，使用默认语言日文。", character.character_name)
                            character.gptsovits_ref_audio_lan = "日文"

                if os.path.exists(os.path.join("../reference_audio",char, 'QT_style.json')):
                    with open(os.path.join("../reference_audio",char, 'QT_style.json'),'r',encoding="utf-8") as f:
                        character.qt_css=f.read()
                        f.close()

                if is_ready:
                    self.character_class_list.append(character)
                    logger.info("成功加载角色：'%s'", character.character_name)
                else:
                    logger.info("加载角色：'%s' 时出现以上错误，跳过该角色的加载。", char)
                    partial_character_count += 1

        # 新增调整角色顺序的功能
        char_order_list = d_sakiko_config.character_order.value
        if len(self.character_class_list) > int(char_order_list['character_num']):
            is_convert_1 = False
            logger.info("似乎有新角色加入了，之前设置的角色顺序不适用，重新设置一下吧")
        elif len(self.character_class_list) < int(char_order_list['character_num']):
            # 经过测试，事实上，如果一个角色是在加载时被判定为不完整而被跳过的，那么它不会影响角色顺序的应用
            # 只有目前角色数量 + 不完整角色数量 != 之前设置的角色数量时，才会出现角色被删除的情况，才需要重置角色顺序
            if len(self.character_class_list) + partial_character_count != int(char_order_list['character_num']):
                is_convert_1 = False
                logger.info("似乎有角色被删除了，之前设置的角色顺序不适用，重新设置一下吧")
            else:
                is_convert_1 = True
        else:
            is_convert_1 = True
        this_character_names = [char.character_name for char in self.character_class_list]

        is_convert_2=True
        if is_convert_1:
            for name in this_character_names:
                if name not in char_order_list['character_names']:
                    logger.info("似乎有角色的名字被修改了，之前设置的角色顺序不适用，重新设置一下吧")
                    is_convert_2 = False
                    break
        if is_convert_1 and is_convert_2:
            new_character_class_list=[]
            char_name2char={char.character_name:char for char in self.character_class_list}
            for name in d_sakiko_config.character_order.value['character_names']:
                new_character_class_list.append(char_name2char[name])

            self.character_class_list=new_character_class_list

        self._load_user_characters()

        # 将最终的结果同步到配置中
        d_sakiko_config.set(
            d_sakiko_config.character_order,
            {
                "character_names": [char.character_name for char in self.character_class_list],
                "character_num": self.character_num,
            },
        )

    def _load_user_characters(self) -> None:
        """从配置加载自定义用户人设，并在首位加入内置默认人设。"""
        self.user_characters = [CharacterAttributes.create_default_user()]
        user_configs = d_sakiko_config.user_characters.value
        if not isinstance(user_configs, list):
            logger.warning("用户人设配置不是列表，已忽略")
            return

        characters_by_folder = {
            character.character_folder_name: character
            for character in self.character_class_list
            if character.character_folder_name
        }
        characters_by_name = {
            character.character_name: character
            for character in self.character_class_list
        }
        loaded_persona_ids = {"default"}

        for user_config in user_configs:
            if not isinstance(user_config, dict):
                logger.warning("忽略格式错误的用户人设配置：%r", user_config)
                continue

            configured_persona_id = user_config.get("persona_id")
            persona_id = (
                configured_persona_id
                if isinstance(configured_persona_id, str) and configured_persona_id
                else uuid.uuid4().hex
            )
            if persona_id in loaded_persona_ids:
                logger.warning("用户人设 ID 重复，已为该人设生成新 ID：%s", persona_id)
                persona_id = uuid.uuid4().hex
            loaded_persona_ids.add(persona_id)

            name = user_config.get("name", "User")
            description = user_config.get("description", "")
            avatar_path = user_config.get("avatar_path")
            user = CharacterAttributes.create_user(
                name=name if isinstance(name, str) else "User",
                description=description if isinstance(description, str) else "",
                icon_path=avatar_path if isinstance(avatar_path, str) else None,
                persona_id=persona_id,
            )

            character_folder_name = user_config.get("user_as_character_folder_name")
            character_name = user_config.get("user_as_character_name")
            # 首先尝试根据文件夹猜测用户角色到底是哪个系统角色
            if isinstance(character_folder_name, str):
                user.user_as_character = characters_by_folder.get(character_folder_name)
            # 无法通过文件夹匹配的情况下，用名称匹配。
            if user.user_as_character is None and isinstance(character_name, str):
                user.user_as_character = characters_by_name.get(character_name)

            if user.user_as_character is not None:
                # 尝试重新加载被引用的系统角色的描述文件，以覆盖用户人设中可能过时的描述信息
                try:
                    full_path = os.path.join("../live2d_related", user.user_as_character.character_folder_name)
                    with open(os.path.join(full_path, 'character_description.txt'),'r',encoding='utf-8') as f:
                        user.user_as_character.character_description=f.read()
                        f.close()
                except (FileNotFoundError, OSError):
                    import traceback
                    traceback.print_exc()
                    pass

            self.user_characters.append(user)

    def reload_user_characters(self) -> None:
        """根据当前配置值重新加载用户人设，并重新扫描系统角色的描述。"""
        self._load_user_characters()

    def save_data(self) -> None:
        """将自定义用户人设保存到配置文件。"""
        serialized_user_characters: list[dict[str, str | None]] = []
        for user_character in self.user_characters:
            if user_character.is_default_user:
                continue

            linked_character = user_character.user_as_character
            serialized_user_characters.append(
                {
                    "persona_id": user_character.persona_id,
                    "name": user_character.character_name,
                    "description": user_character.character_description,
                    "avatar_path": user_character.icon_path,
                    "user_as_character_folder_name": (
                        linked_character.character_folder_name
                        if linked_character is not None
                        else None
                    ),
                    "user_as_character_name": (
                        linked_character.character_name
                        if linked_character is not None
                        else None
                    ),
                }
            )

        d_sakiko_config.set(
            d_sakiko_config.user_characters,
            serialized_user_characters,
        )


if __name__=="__main__":

    a=GetCharacterAttributes()
    logger.debug("character_num = %s", a.character_num)
    a.character_class_list[0].print_attributes()
    a.character_class_list[1].print_attributes()
