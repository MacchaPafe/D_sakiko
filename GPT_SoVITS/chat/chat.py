# 此文件定义了程序中每个对话存储的信息
import dataclasses
import enum
import copy
import re
import warnings
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional, List, Union, Any
import json

import jinja2
import sys

sys.path.append(str(Path(__file__).resolve().parent.parent))
from character import CharacterAttributes
from emotion_enum import EmotionEnum


@dataclasses.dataclass
class Message:
    """
    对话中的单条消息
    """
    # 发送此消息的人物
    character_name: str
    # 消息内容
    text: str
    # 消息的中文翻译
    translation: str
    # 情绪分类
    emotion: EmotionEnum
    # 消息对应的 GPT-SoVITS 生成的音频文件路径
    audio_path: str
    @classmethod
    def from_dict(cls, data: dict):
        """
        从存储字典中加载一个 Message 实例
        """
        return cls(
            character_name=data["character_name"],
            text=data["text"],
            translation=data["translation"],
            emotion=EmotionEnum.from_string(data["emotion"]),
            audio_path=data["audio_path"]
        )

    def as_dict(self) -> Dict:
        """
        将此 Message 实例转换为存储字典
        """
        return {
            "character_name": self.character_name,
            "text": self.text,
            "translation": self.translation,
            "emotion": self.emotion.as_string(),
            "audio_path": self.audio_path
        }

    def to_display_html(self, theme_color: str = "#7799CC", user_name: str = "User", msg_index: Optional[int] = None) -> str:
        """
        将此消息转换为 QTextBrowser 可显示的 HTML 片段

        :param theme_color: 主题颜色（十六进制）
        :param user_name: 用户角色名称，用于判断是否为用户消息
        :param msg_index: 消息索引，用于生成可定位的锚点
        :returns: HTML 字符串
        """
        import html as html_module
        safe_text = html_module.escape(self.text)
        
        # 将 msg_index 编码进 href 以方便 UI 获取点击位置的消息索引
        msg_param = f"?msg={msg_index}" if msg_index is not None else ""

        if self.character_name == user_name or self.character_name == "User":
            return f'<a href="user:{msg_param}" style="text-decoration: none; color: {theme_color};">你：{safe_text}</a>'
        else:
            if self.audio_path != "NO_AUDIO":
                abs_path = os.path.abspath(self.audio_path).replace('\\', '/')
                header = (f'<a href="{abs_path}[{self.emotion.as_label()}]{msg_param}" '
                          f'style="text-decoration: none; color: {theme_color};">'
                          f'★{self.character_name}：{safe_text}</a>')
            else:
                header = (f'<a href="no_audio:{msg_param}" style="text-decoration: none; color: {theme_color};">'
                          f'{self.character_name}：{safe_text}</a>')
            if self.translation:
                safe_translation = html_module.escape(self.translation)
                header += (f'<br><span style="color: #B3D1F2; font-style: italic;">'
                           f'{safe_translation}</span>')
            return header

    @property
    def character(self) -> CharacterAttributes:
        """
        获取此消息对应的 Character 实例

        :returns: Character 实例
        :raises ValueError: 如果找不到对应名称的角色
        """
        from character import GetCharacterAttributes
        char_manager = GetCharacterAttributes()
        for one in char_manager.character_class_list:
            if one.character_name == self.character_name:
                character = one
                break
        else:
            if hasattr(char_manager, "user_characters"):
                for one in char_manager.user_characters:
                    if one.character_name == self.character_name:
                        character = one
                        break
                else:
                    raise ValueError(f"找不到名称为 '{self.character_name}' 的角色或人格。请检查该角色是否在 live2d_related 文件夹下定义，"
                                     f"且正确设置了 name.txt 。")
            else:
                raise ValueError(
                    f"找不到名称为 '{self.character_name}' 的角色。请检查该角色是否在 live2d_related 文件夹下定义，"
                    f"且正确设置了 name.txt 。")

        return character

    def to_llm_query(self, role: str) -> str:
        """
        将此消息转换为用于 LLM 的对话格式

        :param role: 角色视角，可以是 "user" 或 "assistant"
        :raises ValueError: 如果 role 参数不是 "user" 或 "assistant"

        :returns: 用于 LLM 的对话内容字符串
        """
        # 对于用户消息，我们应当认为其内容只有 text 字段
        # 在用户-角色对话模式下，用户消息显然就是用户输入的消息，因此只有 text 字段描述，用户不可能输入翻译、audio_path、情感等内容
        # 在多角色对话模式下，**除了当前 Agent 外其他 Agent 说的话被视为用户消息**，这些消息同样应当只有 text 字段描述（日语），
        # 因为我们不应当让 Agent 专注于其他 Agent 的翻译、情感等信息，这些都是无关紧要的杂音
        if role == "user":
            # 只返回 text 字段
            return f"[{self.character_name}]: {self.text}"
        elif role == "assistant":
            # 格式参考为小剧场模式下 AI 需要采用的格式
            # 我们需要要求 LLM 输出格式化的内容；因此，assistant 消息需要包含多项数据
            return json.dumps(
                {
                    "speaker": self.character_name,
                    "emotion": self.emotion.as_string(),
                    "text": self.text,
                    "translation": self.translation
                }, ensure_ascii=False # 帮帮 AI，把输入格式化的好看一点
            )
        else:
            raise ValueError(f"未知的角色视角: {role}")


class SystemPromptGenerator(ABC):
    @abstractmethod
    def generate(self, perspective: str) -> str:
        """
        生成 System Prompt

        :param perspective: 使用了本 PromptGenerator 的角色的名称。
        在单角色对话中，这个参数不是很有用处，因为整个对话只有 perspective 对应的一个 AI 角色；
        在多角色对话时，这个参数可以让程序针对每个 AI 角色生成不同的 system prompt。
        """
        pass

    @abstractmethod
    def to_dict(self) -> Dict:
        """
        序列化配置，以便存储到 JSON
        """
        pass

    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict) -> "SystemPromptGenerator":
        """
        从字典反序列化
        """
        pass


class StaticPromptGenerator(SystemPromptGenerator):
    def __init__(self, prompt_content: str):
        self.prompt_content = prompt_content

    def generate(self, perspective: str) -> str:
        """
        生成一个固定的 system prompt。由于输出是固定的，忽略 perspective 参数
        """
        return self.prompt_content

    def to_dict(self) -> Dict:
        return {"type": "static", "content": self.prompt_content}

    @classmethod
    def from_dict(cls, data: Dict) -> "StaticPromptGenerator":
        return cls(prompt_content=data.get("content", ""))


class SingleCharacterPromptGenerator(SystemPromptGenerator):
    def __init__(self, character_name: str, user_character_name: str = "User"):
        self.character_name = character_name
        self.user_character_name = user_character_name
        
        # 加载模板
        # 模板位于 ../templates/single_character.jinja (相对于此文件)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(current_dir, "..", "templates", "single_character.jinja")

        with open(template_path, "r", encoding="utf-8") as f:
            self.template = jinja2.Template(f.read())

    def generate(self, perspective: str) -> str:
        """
        生成单角色对话的 system prompt。输出内容会根据 character_name 和 user_character_name 从角色信息中动态生成。
         - character_name 对应的角色描述会被包含在 system prompt 中，作为对该角色的背景介绍，帮助 LLM 更好地理解和扮演该角色。
         - 如果 user_character_name 不为 "User"，则对应的用户角色描述也会被包含在 system prompt 中，作为对用户角色的背景介绍，帮助 LLM 理解用户在对话中的设定。
         - 模板文件 single_character.jinja 定义了 system prompt 的格式和内容结构，确保输出的一致性和可读性。
         - perspective 参数在单角色对话中没有实际作用，因为整个对话只有一个 AI 角色，但为了保持接口一致性，仍然保留该参数。
        """
        # 延迟导入以避免循环依赖
        from character import GetCharacterAttributes
        char_manager = GetCharacterAttributes()
        
        # 获取角色描述
        character_description = ""
        
        target_char = None
        for char in char_manager.character_class_list:
            if char.character_name == self.character_name:
                target_char = char
                break
        
        if target_char:
            character_description = target_char.character_description
        
        # 获取用户描述
        user_character_description = ""
        '''if self.user_character_name != "User":
            for char in char_manager.user_characters:
                if char.character_name == self.user_character_name:
                    user_character_description = char.character_description
                    break
        '''
        
        return self.template.render(
            character_name=self.character_name,
            character_description=character_description,
            user_character_name=self.user_character_name,
            user_character_description=user_character_description,
            anime_name="BangDream", # 感觉几乎没人导入其他ip的角色，先写死了，之后再改成动态的吧
            ensure_age=False    #暂时取消这个吧
        )

    def to_dict(self) -> Dict:
        return {
            "type": "single_character",
            "character_name": self.character_name,
            "user_character_name": self.user_character_name
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "SingleCharacterPromptGenerator":
        return cls(
            character_name=data["character_name"],
            user_character_name=data.get("user_character_name", "User")
        )


class SmallTheaterPromptGenerator(SystemPromptGenerator):
    """
    小剧场模式下，单个 AI 生成整个小剧场剧本所使用的 system prompt。
    这个 generator 目前并没有实际使用，只是用来存储涉及到的角色信息的。
    在 dp_local_multi_char.py 中，system prompt 固定，通过 Chat.meta 字段存储的小剧场信息动态拼接得到 user prompt。
    该类目前没有参与 prompt 的生成。
    """
    def __init__(self, character_names: List[str]):
        self.character_names = character_names

    def generate(self, perspective: str) -> str:
        return ""

    def to_dict(self) -> Dict:
        return {
            "type": "small_theater",
            "character_names": self.character_names
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "SmallTheaterPromptGenerator":
        return cls(character_names=data["character_names"])


class MultiCharacterPromptGenerator(SystemPromptGenerator):
    def __init__(self, character_names: List[str]):
        self.character_names = character_names
        # 暂时留空实现

    def generate(self, perspective: str) -> str:
        return "" # TODO: Implement

    def to_dict(self) -> Dict:
        return {
            "type": "multi_character",
            "character_names": self.character_names
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "MultiCharacterPromptGenerator":
        return cls(character_names=data["character_names"])


class ChatType(enum.Enum):
    """
    存储某个对话的类型，以便区分单角色对话/小剧场模式的对话记录，在每个程序中只展示对应的记录。
    """
    # 单角色对话模式的对话记录
    SINGLE_CHARACTER = 1
    # 小剧场模式的对话记录
    SMALL_THEATER = 2
    # 多角色对话模式的对话记录（尚未实现）
    MULTI_CHARACTER = 3


class Chat:
    """
    每个对话中包含的一切信息
    """
    def __init__(self, name: str = "新对话",
                 prompt_generator: Optional[SystemPromptGenerator] = None,
                 type_: ChatType = ChatType.SINGLE_CHARACTER,
                 start_message: str = "", message_list: Optional[List[Message]] = None,
                 meta: Optional[Dict[str, Any]] = None):
        """
        创建一个新的对话

        :param name: 对话名称（可省略）
        :param prompt_generator: 系统提示词生成器（可省略，默认为 StaticPromptGenerator("")）
        :param type_: 对话类型，表示该对话是一般的单角色对话，还是小剧场对话（可省略，默认为单角色对话）
        :param start_message: 对话的起始消息（理论可省略，但多角色对话模式下强烈建议不要省略，否则可能导致 AI 生成质量下降）
        :param message_list: 对话中的消息列表（可省略，默认为空列表）
        :param meta: 其他需要存储的元信息（可省略，默认为空字典）。对于单角色对话模式，目前没有使用该字段；对于小剧场模式，该字段用于存储剧本的相关信息，如角色的说话风格、互动细节、剧本设定等。
        """
        self.name = name
        self.type = type_

        if prompt_generator is None:
            self.prompt_generator = StaticPromptGenerator("")
        else:
            self.prompt_generator = prompt_generator

        self.start_message = start_message
        self.meta: Dict[str, Any] = copy.deepcopy(meta) if meta is not None else {}

        # 存储对话中所有的消息
        self.message_list: List[Message]
        if message_list is not None:
            self.message_list = message_list
        else:
            self.message_list = []

    @classmethod
    def new_single_chat(cls, character: CharacterAttributes, user_character: Optional[CharacterAttributes] = None, name: str = "新对话") -> "Chat":
        """
        创建一个新的用户-单角色对话

        :param character: 对话中的角色。在创建时，会自动拼接从角色信息中 System Prompt
        :param user_character: 可选的用户角色。如果提供了该参数，则会在起始消息中包含用户角色的设定描述。否则，用户角色将被视为普通用户，不包含任何设定描述。
        这样做的目的是允许用户自定义其在对话中的人格设定
        :param name: 对话名称（可省略）
        :returns: 新的 Chat 实例
        """
        user_name = user_character.character_name if user_character else "User"
        generator = SingleCharacterPromptGenerator(character.character_name, user_name)
        
        chat = cls(name=name, prompt_generator=generator, start_message="", message_list=[])
        return chat

    def get_character_name(self) -> Optional[str]:
        """
        获取当前对话对应的 AI 角色名称。
        优先从 prompt_generator 中获取，若不可用则从消息列表中推断。

        :returns: 角色名称，若无法确定则返回 None
        """
        if isinstance(self.prompt_generator, SingleCharacterPromptGenerator):
            return self.prompt_generator.character_name
        elif isinstance(self.prompt_generator, (SmallTheaterPromptGenerator, MultiCharacterPromptGenerator)):
            if hasattr(self.prompt_generator, 'character_names') and self.prompt_generator.character_names:
                return self.prompt_generator.character_names[0]
        # 从消息列表中推断（找第一条非 User 消息）
        for msg in self.message_list:
            if msg.character_name != "User":
                return msg.character_name
        # 作为最后的手段，从 name 字段推断
        return self.name if self.name != "新对话" else None

    def to_display_html(self, theme_color: str = "#7799CC", user_name: str = "User") -> str:
        """
        将整个对话渲染为 QTextBrowser 可显示的完整 HTML

        :param theme_color: 主题颜色（十六进制）
        :param user_name: 用户角色名称
        :returns: 完整的 HTML 字符串
        """
        html_parts = []
        for i, msg in enumerate(self.message_list):
            html_parts.append(msg.to_display_html(theme_color, user_name, msg_index=i))
        return "<br><br>".join(html_parts)
    
    def __repr__(self) -> str:
        return f"Chat(name={self.name}, messages={self.message_list})"

    def remove_message(self, index: int):
        """
        根据索引删除记录中的一条消息
        """
        if 0 <= index < len(self.message_list):
            self.message_list.pop(index)

    def __str__(self) -> str:
        return f"{self.name}: {self.message_list}"

    def to_dict(self) -> Dict:
        """
        将此 Chat 实例转换为存储字典
        """
        return {
            "name": self.name,
            "prompt_config": self.prompt_generator.to_dict(),
            "type": self.type.value,
            "message_list": [msg.as_dict() for msg in self.message_list],
            "start_message": self.start_message,
            "meta": self.meta
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Chat":
        """
        从存储字典中加载一个 Chat 实例
        """
        prompt_config = data.get("prompt_config", {})
        generator_type = prompt_config.get("type", "static")
        
        generator: SystemPromptGenerator
        if generator_type == "single_character":
            generator = SingleCharacterPromptGenerator.from_dict(prompt_config)
        elif generator_type == "multi_character":
            generator = MultiCharacterPromptGenerator.from_dict(prompt_config)
        elif generator_type == "small_theater":
            generator = SmallTheaterPromptGenerator.from_dict(prompt_config)
        else:
            # 兼容旧数据或 static 类型
            generator = StaticPromptGenerator.from_dict(prompt_config)

        return cls(
            name=data["name"],
            prompt_generator=generator,
            type_ =ChatType(data["type"]),
            message_list=[Message.from_dict(msg_data) for msg_data in data["message_list"]],
            start_message=data.get("start_message", ""),
            meta=data.get("meta", {})
        )

    def get_theater_meta(self) -> Dict[str, Any]:
        """
        获取并确保小剧场对话元数据的基础结构存在。

        返回值结构为：
        {
            "character_0": {"talk_style": "", "interaction_details": ""},
            "character_1": {"talk_style": "", "interaction_details": ""},
            "situation": ""
        }
        """
        raw_theater_meta = self.meta.get("theater")
        theater_meta: Dict[str, Any] = raw_theater_meta if isinstance(raw_theater_meta, dict) else {}

        raw_character_0 = theater_meta.get("character_0")
        raw_character_1 = theater_meta.get("character_1")
        character_0: Dict[str, Any] = raw_character_0 if isinstance(raw_character_0, dict) else {}
        character_1: Dict[str, Any] = raw_character_1 if isinstance(raw_character_1, dict) else {}

        theater_meta = {
            "character_0": {
                "talk_style": character_0.get("talk_style", ""),
                "interaction_details": character_0.get("interaction_details", "")
            },
            "character_1": {
                "talk_style": character_1.get("talk_style", ""),
                "interaction_details": character_1.get("interaction_details", "")
            },
            "situation": theater_meta.get("situation", self.start_message if self.start_message else "")
        }

        self.meta["theater"] = theater_meta
        if theater_meta["situation"]:
            self.start_message = theater_meta["situation"]

        return theater_meta

    def update_theater_meta(self, data: Dict[str, Any]) -> None:
        """更新小剧场元数据，并自动同步 `start_message`。"""
        theater_meta = self.get_theater_meta()

        for key in ["character_0", "character_1"]:
            incoming = data.get(key)
            if isinstance(incoming, dict):
                theater_meta[key]["talk_style"] = incoming.get("talk_style", theater_meta[key]["talk_style"])
                theater_meta[key]["interaction_details"] = incoming.get(
                    "interaction_details", theater_meta[key]["interaction_details"]
                )

        if "situation" in data:
            theater_meta["situation"] = data.get("situation", "")

        self.meta["theater"] = theater_meta
        self.start_message = theater_meta.get("situation", "")
    
    def get_custom_live2d_model_meta(self, character_name: str) -> Optional[str]:
        """
        获取指定角色的自定义 Live2D 模型路径（如果有的话）。如果没有设置自定义模型或者自定义模型路径不存在，则从角色信息中读取默认模型路径。
        """
        live2d_meta = self.meta.get("live2d_models", {})
        model_path = live2d_meta.get(character_name)
        if isinstance(model_path, str) and os.path.exists(model_path):
            return model_path.strip()
        else:
            from character import GetCharacterAttributes
            char_manager = GetCharacterAttributes()
            for one in char_manager.character_class_list:
                if one.character_name == character_name:
                    return one.live2d_json
            else:
                return None  # 如果没找到对应角色，返回 None
    
    def update_custom_live2d_model_meta(self, character_name: str, model_path: str) -> None:
        """
        更新指定角色的自定义 Live2D 模型路径，并确保路径存在。更新后会覆盖之前的设置。
        """
        if not os.path.exists(model_path):
            raise ValueError(f"提供的模型路径不存在: {model_path}")

        if "live2d_models" not in self.meta or not isinstance(self.meta["live2d_models"], dict):
            self.meta["live2d_models"] = {}

        self.meta["live2d_models"][character_name] = model_path.strip()
        
    @staticmethod
    def merge_short_sentences(sentences: List[str], min_length: int = 25):
        """
        合并过短的句子，避免语音合成时停顿过多
        """
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

    @classmethod
    def load_from_main_record(cls, llm_data: Dict, qt_data: Optional[Dict] = None) -> "Chat":
        """
        从之前的主程序对话记录中读取单个对话，将其转换为新的对话格式。

        :param llm_data: 从 history_messages_dp.json 中读取的单个对话记录
        :param qt_data: 可选的从 history_messages_qt.json 中读取的数据（将用于获得对话的 emotion 和 audio_path 信息）
        :raises KeyError: 如果输入数据格式不正确，缺少必要字段
        """
        character_name = llm_data["character"]
        history = llm_data["history"]

        # ======================================================
        # 从 HTML 中解析出每个 AI 显示块及其音频信息
        # HTML 中每个 <p> 块代表一行显示内容：
        #   - 有音频: <a href="xxx.wav[LABEL_X]"><span>★角色名：文本</span></a>
        #   - 无音频: <span>角色名：文本</span>  (注意没有 ★ 前缀和 <a> 标签)
        # 我们从 HTML 中提取每个 AI 显示块的文本片段，以及其对应的音频路径和情感标签
        # ======================================================
        qt_display_blocks = []  # [(text_snippet, audio_path, emotion_label), ...]
        try:
            if qt_data is not None and qt_data.get("character") == character_name:
                char_html = qt_data.get("history", "")
                if char_html:
                    # 提取带有音频的 AI 消息块: <a href="path.wav[LABEL_X]"><span ...>★角色名：text</span></a>
                    # 正则：匹配 href 中的 path 和 label，以及 span 中 ★角色名：后面的文本
                    audio_blocks = re.findall(
                        r'href="([^"]*?\.wav)\[([^"]*?)\]"[^>]*><span[^>]*>★' + re.escape(character_name) + r'：(.*?)</span></a>',
                        char_html, flags=re.DOTALL
                    )
                    for path, label, text_snippet in audio_blocks:
                        # 清洗文本以便后续匹配
                        clean = text_snippet.strip()
                        qt_display_blocks.append((clean, path, label))
        except Exception as e:
            print(f"未能从 QT 对话记录中提取音频信息: {e}")

        message_list = []
        system_prompt = None

        for index, item in enumerate(history):
            if index == 0 and item["role"] in ["system", "user"]:
                # 跳过第一个 system 或 user 消息（通常是角色设定）
                system_prompt = item["content"]
                continue

            if item["role"] == "assistant":
                # 对于 AI 的输出，需要检查其格式，进行段落分割。
                content = item["content"].strip()
                if not content:
                    continue

                # 预处理逻辑
                content = content + '。'
                content = content.replace("。。", "。")
                content = content.replace("!!!!!", "")

                # 检查是否包含翻译（日英混合模式）
                pattern = r'(.*?)(?:\[翻译\]|\[翻訳\])(.+?)(?:\[翻译结束\]|\[翻訳終了\])'
                matches = re.findall(pattern, content, flags=re.DOTALL)

                if matches:
                    is_ja = True
                    segments = [(orig.strip(), trans.strip()) for orig, trans in matches if trans.strip()]
                else:
                    is_ja = False
                    segments = re.findall(r'.+?[。！!]', content, flags=re.DOTALL)
                    segments = cls.merge_short_sentences(segments)

                for segment in segments:
                    if is_ja:
                        text = segment[0]
                        translation = segment[1]
                    else:
                        text = segment
                        translation = ""

                    # 在 HTML 显示块中查找包含该文本片段的条目
                    # 使用子串匹配，因为 HTML 中的文本可能包含额外的格式字符
                    audio_path = "NO_AUDIO"
                    emotion = "happiness"

                    # 尝试用文本内容匹配 HTML 中的音频块
                    # dp_data 中的文本可能有换行符，HTML 中可能有多个空格或没有空格
                    # 因此完全移除所有空白字符后再比较（避免空格/换行差异导致失配）
                    text_for_match = re.sub(r"（.*?）", "", text).strip()
                    text_for_match = re.sub(r"\(.*?\)", "", text_for_match).strip()
                    text_for_match = re.sub(r'\s+', '', text_for_match)  # 移除全部空白
                    best_match_idx = -1
                    best_match_len = 0

                    for block_idx, (block_text, block_path, block_label) in enumerate(qt_display_blocks):
                        block_text_clean = re.sub(r"（.*?）", "", block_text).strip()
                        block_text_clean = re.sub(r"\(.*?\)", "", block_text_clean).strip()
                        block_text_clean = re.sub(r'\s+', '', block_text_clean)  # 移除全部空白
                        # 检查关键文本是否匹配（取前30个字符进行匹配以避免末尾差异）
                        key_len = min(30, len(text_for_match), len(block_text_clean))
                        if key_len > 5 and text_for_match[:key_len] == block_text_clean[:key_len]:
                            if key_len > best_match_len:
                                best_match_len = key_len
                                best_match_idx = block_idx

                    if best_match_idx >= 0:
                        _, audio_path, emotion = qt_display_blocks[best_match_idx]
                        # 移除已匹配的块，避免重复匹配
                        qt_display_blocks.pop(best_match_idx)

                    msg = Message(
                        character_name=character_name,
                        text=text,
                        translation=translation,
                        emotion=EmotionEnum.from_string(emotion),
                        audio_path=audio_path
                    )
                    message_list.append(msg)
            else:
                # 用户消息
                # 去除用户消息末尾自动追加的语言控制提示词，使用非贪婪匹配以兼容中日两种情况
                # 同时使用 \n* 将前置的可能换行符也一并删掉
                cleaned_content=re.sub(r"\n*（本句话你的回答请务必.*?）", "", item["content"])
                msg = Message(
                    character_name="User",
                    text=cleaned_content,
                    translation="",
                    emotion=EmotionEnum.from_string("LABEL_0"),
                    audio_path=""
                )
                message_list.append(msg)

        return cls(name=str(character_name), message_list=message_list, type_=ChatType.SINGLE_CHARACTER,
                   prompt_generator=SingleCharacterPromptGenerator(character_name))

    @classmethod
    def load_from_theater_record(cls, data: list[dict[str, Union[str, int]]]) -> "Chat":
        """
        从之前的小剧场模式对话记录的单个对话中，将其转换为新的对话格式。

        :raise IndexError: 如果文件格式不正确，无法找到必要字段
        :raise KeyError: 如果文件格式不正确，无法找到必要字段
        """
        message_list = []
        all_characters = []
        for message in data:
            if message["char_name"] not in all_characters:
                all_characters.append(message["char_name"])

            message_obj = Message(
                character_name=message["char_name"],
                text=message["text"],
                translation=message["translation"],
                emotion=EmotionEnum.from_string(message["emotion"]),
                audio_path=message.get("audio_path", "")
            )
            message_list.append(message_obj)

        # 理论上应该只有两个角色参与对话。嗯，应该吧……只要没有人手动改存档
        name = f"{all_characters[0]}与{all_characters[1]}的对话" if len(all_characters) >= 2 else "小剧场对话"
        return cls(name=name, message_list=message_list, prompt_generator=SmallTheaterPromptGenerator(all_characters),
                   type_=ChatType.SMALL_THEATER)

    @property
    def involved_characters(self) -> List[str]:
        """
        获取当前对话中涉及的所有角色名称列表。如果对话中包含用户消息，则会包含 "User" 角色名称或其他用户自定义人格。

        :returns: 角色名称列表
        """
        characters = set()
        for msg in self.message_list:
            characters.add(msg.character_name)
        return list(characters)

    def build_llm_query(self, perspective: str) -> List[Dict]:
        """
        根据当前对话内容，构建用于发送给 LLM 的对话历史列表

        :param perspective: 当前该说话的角色，可以从当前对话中涉及的角色选择，或者使用 "User" 选中用户视角（通常情况下，不应当选择 "User"，因为你不会希望 LLM 代替你说话的）
        :returns: 用于 LLM 的对话历史列表
        """
        llm_history = []
        has_start_message = False
        if self.start_message:
            llm_history.append(
            # 构建一条信息，描述当前的场景等内容
            # 这条信息始终以用户身份发送给 LLM
            # 因为无论从哪个角色视角出发，场景描述都是用户提供给 LLM 的信息
            {
                "role": "user",
                "content": self.start_message
            })
            has_start_message = True

        system_prompt_content = self.prompt_generator.generate(perspective=perspective)
        if system_prompt_content:
            llm_history.append({
                "role": "system",
                "content": system_prompt_content
            })

        for msg in self.message_list:
            role = "assistant" if msg.character_name == perspective else "user"
            llm_history.append({
                "role": role,
                "content": msg.to_llm_query(role),
            })

        if not has_start_message and llm_history and llm_history[0]["role"] == "assistant":
            warnings.warn("当前对话没有设置起始消息，如果该对话为小剧场对话，可能会导致 LLM 生成质量下降和 API 请求错误。")

        return self.merge_llm_query(llm_history)

    @staticmethod
    def merge_llm_query(message_list: List[Dict]) -> List[Dict]:
        """
        直接修改 message_list 的内容，将所有相邻的 role 相同的消息合并为一条消息，以遵循 LLM 的对话格式要求
        请注意：列表会被原地修改，返回值和输入参数是同一个对象
        """
        if not message_list:
            return message_list

        merged_list = [message_list[0]]
        for msg in message_list[1:]:
            last_msg = merged_list[-1]
            if msg["role"] == last_msg["role"]:
                # 合并内容
                last_msg["content"] += "\n" + msg["content"]
            else:
                merged_list.append(msg)

        # 清空原列表并添加合并后的内容
        message_list.clear()
        message_list.extend(merged_list)
        return message_list

    def add_message(self, message: Message) -> None:
        """
        向当前对话中添加一条消息

        :param message: 需要添加的消息
        """
        self.message_list.append(message)

    def clear_message_list(self) -> None:
        """
        清空当前对话中的所有消息
        """
        self.message_list.clear()

    def is_my_turn(self, perspective: str) -> bool:
        """
        判断当前对话中，指定角色是否可以发言。如果该角色是当前对话的最后一条消息的发送者，则返回 False，否则返回 True。

        :param perspective: 角色视角，可以从当前对话中涉及的角色选择，或者使用 "User" 选中用户视角
        :returns: 如果指定角色可以发言则返回 True，否则返回 False
        """
        if not self.message_list:
            return True
        last_message = self.message_list[-1]
        return last_message.character_name != perspective


class ChatManager:
    """
    管理所有对话
    """
    def __init__(self, chat_list: Optional[List[Chat]] = None):
        """
        创建一个新的对话管理器

        :param chat_list: 需要管理的对话列表（可省略，默认为空列表）
        """
        if chat_list is not None:
            self.chat_list = chat_list
        else:
            self.chat_list = []

    @classmethod
    def load_from_main_record(cls, llm_file: Union[str, Path] = "../reference_audio/history_messages_dp.json",
                              qt_file: Union[str, Path, None] = None) -> "ChatManager":
        """
        从之前的主程序对话记录中读取所有对话，将其转换为新的对话格式，然后返回一个存储了这些对话的 ChatManager 实例。

        :param llm_file: 主程序对话记录文件路径（history_messages_dp.json）
        :param qt_file: 可选的 QT 对话记录文件路径（history_messages_qt.json）
        :raises FileNotFoundError: 如果指定的文件不存在
        :raises json.JSONDecodeError: 如果文件格式不正确，无法解析为 JSON
        """
        with open(llm_file, "r", encoding="utf-8") as f:
            llm_data = json.load(f)

        qt_data = None
        if qt_file is not None:
            try:
                with open(qt_file, "r", encoding="utf-8") as f:
                    qt_data = json.load(f)
            except Exception as e:
                print(f"未能加载 QT 对话记录文件 '{qt_file}': {e}")
                qt_data = None

        chat_list = []
        for one_chat_data in llm_data:
            # 为每个角色查找对应的 qt_data
            current_qt_data = None
            char_name = one_chat_data.get("character")
            for one_qt_data in qt_data or []:
                if one_qt_data.get("character") == char_name:
                    current_qt_data = one_qt_data
                    break

            chat = Chat.load_from_main_record(one_chat_data, current_qt_data)
            chat_list.append(chat)

        return cls(chat_list=chat_list)

    @classmethod
    def load_from_theater_record(cls, file: Union[Path, str] = "../reference_audio/small_theater_history.json") -> "ChatManager":
        """
        从小剧场模式对话记录中读取所有对话，将其转换为新的对话格式，然后返回一个存储了这些对话的 ChatManager 实例。

        :param file: 小剧场模式对话记录文件路径
        :raises FileNotFoundError: 如果指定的文件不存在
        :raises json.JSONDecodeError: 如果文件格式不正确，无法解析为 JSON
        """
        # 借用 add_theater_mode_history 方法来加载小剧场对话记录，并将其添加到一个新的 ChatManager 实例中
        self = cls()
        self.add_theater_mode_history(file)
        return self

    def add_theater_mode_history(self, file: Union[Path, str] = "../reference_audio/small_theater_history.json") -> None:
        """
        从小剧场模式对话记录中读取对话，并添加到当前的对话列表中。

        :param file: 小剧场模式对话记录文件路径
        :raises FileNotFoundError: 如果指定的文件不存在
        :raises KeyError: 如果文件格式不正确，无法找到必要字段
        :raises IndexError: 如果文件格式不正确，无法找到必要字段
        :raises json.JSONDecodeError: 如果文件格式不正确，无法解析为 JSON
        （如果你需要处理错误，建议直接 except Exception 来捕获所有可能的错误，因为之前的对话记录格式比较混乱，可能存在各种各样的读取问题）
        """
        with open(file, "r", encoding="utf-8") as f:
            data_json = json.load(f)

        # 第一个项目存储了所有的对话消息（不区分目前是哪个对话）
        all_history: list[dict[str, Union[str, int]]] = data_json[0]
        # 第二个项目为字典的列表，每个字典的键为该对话名称，值存储了该对话开始的消息编号，以及该对话包含多少条消息两个整数。
        start_index_store: list[dict[str, tuple[int, int]]] = data_json[1]

        # 遍历所有的对话元数据（即 start_index_store 中的每个字典）
        for chat_info in start_index_store:
            # 根据每个对话的开始位置和长度，从 all_history 中提取出该对话的消息数据
            # 理论上讲每个字典都只会存在一个对话起始/长度数据…但是我们还是用遍历吧，以防万一
            for chat_name, (start_index, length) in chat_info.items():
                # 文件中对话条数是从 0 开始的，因此 start_index 已经是正确的起始位置了，不需要再减 1
                theater_chat_data = all_history[start_index: start_index + length]
                # 加载该对话数据，并转换为 Chat 实例
                theater_chat = Chat.load_from_theater_record(theater_chat_data)

                self.chat_list.append(theater_chat)

    def to_dict(self) -> Dict:
        """
        将此 ChatManager 实例转换为存储字典
        """
        return {
            "chat_list": [chat.to_dict() for chat in self.chat_list]
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ChatManager":
        """
        从存储字典中加载一个 ChatManager 实例
        """
        return cls(
            chat_list=[Chat.from_dict(chat_data) for chat_data in data["chat_list"]]
        )

    def get_or_create_chat_for_character(self, character: "CharacterAttributes") -> Chat:
        """
        获取指定角色的单角色对话，若不存在则创建新的。

        :param character: 角色属性对象
        :returns: 对应的 Chat 实例
        """
        for chat_obj in self.chat_list:
            if chat_obj.type != ChatType.SINGLE_CHARACTER:
                continue
            if isinstance(chat_obj.prompt_generator, SingleCharacterPromptGenerator):
                if chat_obj.prompt_generator.character_name == character.character_name:
                    return chat_obj
            # 兼容旧格式迁移后的 StaticPromptGenerator（已经设定为加载SingleCharacterPromptGenerator，这个判断不再需要）
            # elif isinstance(chat_obj.prompt_generator, StaticPromptGenerator):
            #     if chat_obj.name == character.character_name:
            #         return chat_obj
        # 没有找到，创建新的
        new_chat = Chat.new_single_chat(character, name=character.character_name)
        self.chat_list.append(new_chat)
        return new_chat

    def save(self, file: Union[Path, str] = "../reference_audio/all_conversation.json") -> None:
        """
        将当前的对话列表保存到指定文件中。

        :param file: 保存文件路径
        """
        with open(file, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=4)

    @classmethod
    def load(cls, file: Union[Path, str] = "../reference_audio/all_conversation.json") -> "ChatManager":
        """
        从指定文件中加载对话列表，并返回一个 ChatManager 实例。

        :param file: 加载文件路径
        :raises FileNotFoundError: 如果指定的文件不存在
        :raises json.JSONDecodeError: 如果文件格式不正确，无法解析为 JSON
        """
        with open(file, "r", encoding="utf-8") as f:
            data = json.load(f)

        return cls.from_dict(data)


_global_chat_manager = None


def _move_legacy_files_to_backup():
    """
    将旧版聊天记录文件移动到 old_history_messages 文件夹中，
    避免每次启动时重复触发迁移操作。
    """
    legacy_files = [
        "../reference_audio/history_messages_dp.json",
        "../reference_audio/history_messages_qt.json",
    ]
    backup_dir = "../reference_audio/old_history_messages"
    any_exists = any(os.path.exists(f) for f in legacy_files)
    if not any_exists:
        return

    os.makedirs(backup_dir, exist_ok=True)
    for f in legacy_files:
        if os.path.exists(f):
            dest = os.path.join(backup_dir, os.path.basename(f))
            try:
                shutil.move(f, dest)
                print(f"已将旧版记录文件 {f} 移动到 {dest}")
            except Exception as e:
                print(f"移动旧版记录文件 {f} 失败: {e}")


def get_chat_manager() -> ChatManager:
    """
    获取全局的 ChatManager 实例。如果尚未创建，则创建一个新的实例。
    创建新的实例时，尝试从默认存储文件加载对话记录；如果加载失败，则尝试从主程序的对话记录中转换数据。
    成功从旧格式迁移后，会将旧文件移动到 old_history_messages 文件夹中。

    :returns: 全局的 ChatManager 实例
    """
    from character import PrintInfo
    global _global_chat_manager
    if _global_chat_manager is None:
        try:
            if os.path.exists("../reference_audio/history_messages_dp.json"):
                try:
                    _global_chat_manager = ChatManager.load_from_main_record(llm_file="../reference_audio/history_messages_dp.json",
                                                                         qt_file="../reference_audio/history_messages_qt.json")
                    _global_chat_manager.save()
                    _move_legacy_files_to_backup()
                    PrintInfo.print_info("已成功将旧版聊天记录迁移到新格式。旧记录文件备份位置：../reference_audio/old_history_messages")
                    # 迁移成功，保存新格式并将旧文件移到备份目录
                except Exception:
                    PrintInfo.print_error("未能成功加载旧格式的对话记录，将新建空白存档！建议关闭本窗口后重启一遍程序！")
                    input("按回车确认继续运行，但可能导致本次对话所产生的聊天记录内容丢失！")
                    _global_chat_manager = ChatManager()

            elif os.path.exists("../reference_audio/all_conversation.json"):
                _global_chat_manager = ChatManager.load()
            else:
                PrintInfo.print_warning("未找到任何存在的对话记录文件，将新建。")
                _global_chat_manager = ChatManager()
        except Exception:
            #try:
            #    _global_chat_manager = ChatManager.load_from_main_record(llm_file="../reference_audio/history_messages_dp.json",
            #                                                             qt_file="../reference_audio/history_messages_qt.json")
            #    try:
            #        _global_chat_manager.add_theater_mode_history(file="../reference_audio/small_theater_history.json")
            #    except Exception:
            #        pass  # 小剧场记录不存在也没关系
            #    # 迁移成功，保存新格式并将旧文件移到备份目录
            #    _global_chat_manager.save()
            #    _move_legacy_files_to_backup()
            #    print("已成功将旧版聊天记录迁移到新格式。")
            #except Exception:
            PrintInfo.print_error("加载对话记录时出错，将新建空白存档。如果确定已有对话文件（../reference_audio/all_conversation.json或../reference_audio/history_messages_dp.json），请务必先备份文件！！")
            input("按回车确认继续运行，但可能导致本次对话所产生的聊天记录内容丢失！")
            _global_chat_manager = ChatManager()

    return _global_chat_manager


if __name__ == "__main__":
    get_chat_manager().save()
