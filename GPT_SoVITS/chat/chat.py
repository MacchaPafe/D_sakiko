# 此文件定义了程序中每个对话存储的信息
import dataclasses
import re
import warnings
from pathlib import Path
from typing import Dict, Optional, List, Union
import json

from character import Character
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
    # live2d 模块在播放此消息时需要进行的动作（可以为空，此时 live2d 模块会根据 emotion 的数据随机选择一个情感分类下的动作进行播放）
    live2d_motion_number: Optional[int] = None

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
            audio_path=data["audio_path"],
            live2d_motion_number=data.get("live2d_motion_number")
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
            "audio_path": self.audio_path,
            "live2d_motion_number": self.live2d_motion_number
        }

    @property
    def character(self) -> Character:
        """
        获取此消息对应的 Character 实例

        :returns: Character 实例
        :raises ValueError: 如果找不到对应名称的角色
        """
        from character_manager import CharacterManager
        char_manager = CharacterManager()
        for one in char_manager.character_class_list:
            if one.character_name == self.character_name:
                character = one
                break
        else:
            for one in char_manager.user_characters:
                if one.character_name == self.character_name:
                    character = one
                    break
            else:
                raise ValueError(f"找不到名称为 '{self.character_name}' 的角色或人格。请检查该角色是否在 live2d_related 文件夹下定义，"
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


class Chat:
    """
    每个对话中包含的一切信息
    """
    def __init__(self, name: str = "新对话", start_message: str = "", message_list: Optional[List[Message]] = None):
        """
        创建一个新的对话

        :param name: 对话名称（可省略）
        :param start_message: 对话的起始消息（理论可省略，但多角色对话模式下强烈建议不要省略，否则可能导致 AI 生成质量下降）
        :param message_list: 对话中的消息列表（可省略，默认为空列表）
        """
        self.name = name
        self.start_message = start_message

        # 存储对话中所有的消息
        self.message_list: List[Message]
        if message_list is not None:
            self.message_list = message_list
        else:
            self.message_list = []
    
    def __repr__(self) -> str:
        return f"Chat(name={self.name}, messages={self.message_list})"

    def __str__(self) -> str:
        return f"{self.name}: {self.message_list}"

    def to_dict(self) -> Dict:
        """
        将此 Chat 实例转换为存储字典
        """
        return {
            "name": self.name,
            "message_list": [msg.as_dict() for msg in self.message_list],
            "start_message": self.start_message
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Chat":
        """
        从存储字典中加载一个 Chat 实例
        """
        return cls(
            name=data["name"],
            message_list=[Message.from_dict(msg_data) for msg_data in data["message_list"]],
            start_message=data.get("start_message", "")
        )
    
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
        # 尝试读取 history_messages_qt.json 以获取音频和情感信息
        qt_audio_info = []
        try:
            if qt_data is not None and qt_data.get("character") == character_name:
                char_html = qt_data.get("history", "")

                # 从 HTML 中提取音频路径和情感标签
                # 格式通常为: <a href=".../output.wav[LABEL_X]">
                if char_html:
                    # 使用正则提取 href 中的内容
                    # Group 1: 音频路径 (以 .wav 结尾)
                    # Group 2: 情感标签 (位于 [] 内)
                    links = re.findall(r'href="([^"]*?\.wav)\[([^"]*?)\]"', char_html)
                    qt_audio_info = list(links) # [(path, label), ...]
        except Exception as e:
            print(f"未能从 QT 对话记录中提取音频信息: {e}")
            qt_audio_info = []

        audio_info_index = 0
        message_list = []

        for index, item in enumerate(history):
            if index == 0 and item["role"] in ["system", "user"]:
                # 跳过第一个 system 或 user 消息（通常是角色设定）
                # 用 user 消息给模型设定指示是本程序之前的一个错误做法；因此，这里同样跳过 user 的消息，因为其大概率是给模型背景信息
                # 背景信息（system 指令）应当根据角色的数据动态生成，而不应当直接存储。
                continue

            if item["role"] == "assistant":
                # 对于 AI 的输出，需要检查其格式，进行段落分割。
                content = item["content"].strip()
                if not content:
                    continue

                # 预处理逻辑，参考 main2.py
                # 补全句号，防止切分失败
                content = content + '。'
                content = content.replace("。。", "。")
                content = content.replace("!!!!!", "")

                # 检查是否包含翻译（日英混合模式）
                # 匹配模式：原文[翻译]译文[翻译结束]
                pattern = r'(.*?)(?:\[翻译\]|\[翻訳\])(.+?)(?:\[翻译结束\]|\[翻訳終了\])'
                matches = re.findall(pattern, content, flags=re.DOTALL)

                if matches:
                    is_ja = True
                    # 提取原文和译文，去除空白字符
                    segments = [(orig.strip(), trans.strip()) for orig, trans in matches if trans.strip()]
                else:
                    is_ja = False
                    # 按标点符号切分句子
                    segments = re.findall(r'.+?[。！!]', content, flags=re.DOTALL)
                    # 合并过短的句子
                    segments = cls.merge_short_sentences(segments)

                for segment in segments:
                    if is_ja:
                        # 日英混合模式下，segment 是 (原文, 译文) 元组
                        text = segment[0]
                        translation = segment[1]
                    else:
                        # 普通模式下，segment 是文本字符串
                        text = segment
                        translation = ""

                    # 尝试匹配音频和情感
                    emotion = "normal"
                    audio_path = ""
                    
                    if audio_info_index < len(qt_audio_info):
                        path, label = qt_audio_info[audio_info_index]
                        audio_path = path
                        emotion = label
                        audio_info_index += 1

                    # 创建 Message 对象
                    msg = Message(
                        character_name=character_name,
                        text=text,
                        translation=translation,
                        emotion=EmotionEnum.from_string(emotion),
                        audio_path=audio_path,
                        live2d_motion_number=None
                    )
                    message_list.append(msg)
            else:
                # 用户消息，没有 emotion, audio_path, translation 等字段，设置默认值
                msg = Message(
                    # "User" 是程序预定义的用户角色名称
                    # 之后在 UI 里要确保不能让用户轻易删掉这个角色……
                    character_name="User",
                    text=item["content"],
                    translation="",
                    emotion=EmotionEnum.from_string("LABEL_0"), # 默认使用 "happiness"
                    audio_path="",
                    live2d_motion_number=None
                )
                message_list.append(msg)

        return cls(name=f"与{character_name}的对话", message_list=message_list)

    @classmethod
    def load_from_theater_record(cls, file="../reference_audio/small_theater_history.json") -> "Chat":
        """
        从之前的小剧场模式对话记录中读取所有对话，将其转换为新的对话格式。

        这个文件只可能存储一个对话，正好返回单个 Chat 对象，所以放在“Chat“类级别是合适的

        :raise IndexError: 如果文件格式不正确，无法找到必要字段
        :raise KeyError: 如果文件格式不正确，无法找到必要字段
        """
        with open(file, "r", encoding="utf-8") as f:
            data = json.load(f)

        actual_chat = data[0]
        message_list = []
        all_characters = []
        for message in actual_chat:
            if message["char_name"] not in all_characters:
                all_characters.append(message["char_name"])

            message_obj = Message(
                character_name=message["char_name"],
                text=message["text"],
                translation=message["translation"],
                emotion=EmotionEnum.from_string(message["emotion"]),
                audio_path=message.get("audio_path", ""),
                live2d_motion_number=message["live2d_motion_num"]
            )
            message_list.append(message_obj)

        # 理论上应该只有两个角色参与对话。嗯，应该吧……只要没有人手动改存档
        name = f"{all_characters[0]} 与 {all_characters[1]} 的小剧场对话" if len(all_characters) >= 2 else "小剧场对话"
        return cls(name=name, message_list=message_list)

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
            return

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
            chat = Chat.load_from_main_record(one_chat_data, qt_data)
            chat_list.append(chat)

        return cls(chat_list=chat_list)

    def add_theater_mode_history(self, file: Union[Path, str] = "../reference_audio/small_theater_history.json") -> None:
        """
        从小剧场模式对话记录中读取对话，并添加到当前的对话列表中。

        :param file: 小剧场模式对话记录文件路径
        :raises FileNotFoundError: 如果指定的文件不存在
        :raises json.JSONDecodeError: 如果文件格式不正确，无法解析为 JSON
        """
        theater_chat = Chat.load_from_theater_record(file)
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
