# 此文件定义了程序中每个对话存储的信息
from __future__ import annotations

import dataclasses
import enum
import copy
import re
import warnings
import os
import shutil
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Union, Any, Iterable, Literal, Sequence
import json

import jinja2
import sys

sys.path.append(str(Path(__file__).resolve().parent.parent))
from character import CharacterAttributes
from emotion_enum import EmotionEnum
from log import get_logger
from chat.chat_meta import ChatMeta, TheaterMeta, ToolCallHistoryRecordMeta, ToolCallRecordMeta


logger = get_logger(__name__)


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

    def to_llm_query(
            self,
            role: str,
            llm_character_name: str | None = None,
    ) -> str:
        """
        将此消息转换为用于 LLM 的对话格式

        :param role: 角色视角，可以是 "user" 或 "assistant"
        :param llm_character_name: 可选的发送者标签覆盖值，仅影响发送给 LLM 的文本。
        :raises ValueError: 如果 role 参数不是 "user" 或 "assistant"

        :returns: 用于 LLM 的对话内容字符串
        """
        # 对于用户消息，我们应当认为其内容只有 text 字段
        # 在用户-角色对话模式下，用户消息显然就是用户输入的消息，因此只有 text 字段描述，用户不可能输入翻译、audio_path、情感等内容
        # 在多角色对话模式下，**除了当前 Agent 外其他 Agent 说的话被视为用户消息**，这些消息同样应当只有 text 字段描述（日语），
        # 因为我们不应当让 Agent 专注于其他 Agent 的翻译、情感等信息，这些都是无关紧要的杂音
        if role == "user":
            # 只返回 text 字段
            character_name = llm_character_name or self.character_name
            return f"[{character_name}]: {self.text}"
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


@dataclasses.dataclass(frozen=True)
class MessageRange:
    """表示消息列表中的左闭右开范围。"""

    start: int
    end: int


@dataclasses.dataclass(frozen=True)
class DeleteMessagesResult:
    """记录一次消息范围删除的结果。"""

    start: int
    end: int
    deleted_messages: list[Message]
    deleted_user_count: int
    deleted_character_count: int


@dataclasses.dataclass(frozen=True)
class AudioDeleteResult:
    """记录一次音频文件清理的结果。"""

    deleted_paths: list[str]
    failed_paths: list[str]


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


@dataclasses.dataclass(frozen=True)
class UserPersonaSnapshot:
    """保存创建对话时实际生效的用户人设快照。"""

    persona_id: str
    name: str
    description: str
    kind: Literal["custom", "system_character"]
    source_character_folder_name: str | None = None

    @classmethod
    def from_character(cls, character: CharacterAttributes) -> UserPersonaSnapshot:
        """根据用户人设当前实际生效的信息创建不可变快照。"""
        if character.is_default_user:
            raise ValueError("默认用户人设不应创建快照")

        persona_id = str(character.persona_id or "").strip()
        name = character.effective_character_name.strip()
        if not persona_id or not name:
            raise ValueError("用户人设缺少有效的 persona_id 或名称")

        linked_character = character.user_as_character
        return cls(
            persona_id=persona_id,
            name=name,
            description=character.effective_character_description,
            kind="system_character" if linked_character is not None else "custom",
            source_character_folder_name=(
                linked_character.character_folder_name
                if linked_character is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, str | None]:
        """将用户人设快照转换为可写入 JSON 的字典。"""
        return {
            "persona_id": self.persona_id,
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "source_character_folder_name": self.source_character_folder_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> UserPersonaSnapshot:
        """从存档字典恢复用户人设快照，并拒绝不完整的数据。"""
        persona_id = data.get("persona_id")
        name = data.get("name")
        description = data.get("description")
        kind = data.get("kind")
        source_folder = data.get("source_character_folder_name")

        if not isinstance(persona_id, str) or not persona_id.strip():
            raise ValueError("用户人设快照缺少 persona_id")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("用户人设快照缺少名称")
        if not isinstance(description, str):
            raise ValueError("用户人设快照描述格式错误")
        if kind not in {"custom", "system_character"}:
            raise ValueError("用户人设快照类型错误")
        if source_folder is not None and not isinstance(source_folder, str):
            raise ValueError("用户人设来源角色目录格式错误")

        normalized_kind: Literal["custom", "system_character"] = (
            "system_character" if kind == "system_character" else "custom"
        )
        return cls(
            persona_id=persona_id.strip(),
            name=name.strip(),
            description=description,
            kind=normalized_kind,
            source_character_folder_name=source_folder,
        )


class SingleCharacterPromptGenerator(SystemPromptGenerator):
    def __init__(
            self,
            character_name: str,
            user_persona: UserPersonaSnapshot | None = None,
    ) -> None:
        """创建单角色对话提示词生成器。"""
        self.character_name = character_name
        self.user_persona = user_persona
        
        # 加载模板
        # 模板位于 ../templates/single_character.jinja (相对于此文件)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(current_dir, "..", "templates", "single_character.jinja")

        with open(template_path, "r", encoding="utf-8") as f:
            self.template = jinja2.Template(f.read())

    def generate(self, perspective: str) -> str:
        """
        生成单角色对话的 system prompt。输出内容会根据角色信息与冻结的用户人设快照动态生成。
         - character_name 对应的角色描述会被包含在 system prompt 中，作为对该角色的背景介绍，帮助 LLM 更好地理解和扮演该角色。
         - 如果存在用户人设快照，则快照中的名称和描述会被包含在 system prompt 中，帮助 LLM 理解用户在对话中的设定。
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
        
        return self.template.render(
            character_name=self.character_name,
            character_description=character_description,
            user_persona=self.user_persona,
            anime_name="BangDream", # 感觉几乎没人导入其他ip的角色，先写死了，之后再改成动态的吧
            ensure_age=False    #暂时取消这个吧
        )

    def to_dict(self) -> Dict:
        return {
            "type": "single_character",
            "character_name": self.character_name,
            "user_persona": (
                self.user_persona.to_dict()
                if self.user_persona is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "SingleCharacterPromptGenerator":
        """从存档配置恢复单角色对话提示词生成器。"""
        user_persona = None
        raw_user_persona = data.get("user_persona")
        if isinstance(raw_user_persona, dict):
            try:
                user_persona = UserPersonaSnapshot.from_dict(raw_user_persona)
            except ValueError:
                logger.warning("用户人设快照损坏，已按无用户人设加载：%r", raw_user_persona)

        return cls(
            character_name=data["character_name"],
            user_persona=user_persona,
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
                 meta: Optional[Union[ChatMeta, dict[str, object]]] = None,
                 chat_id: Optional[str] = None):
        """
        创建一个新的对话

        :param name: 对话名称（可省略）
        :param prompt_generator: 系统提示词生成器（可省略，默认为 StaticPromptGenerator("")）
        :param type_: 对话类型，表示该对话是一般的单角色对话，还是小剧场对话（可省略，默认为单角色对话）
        :param start_message: 对话的起始消息（理论可省略，但多角色对话模式下强烈建议不要省略，否则可能导致 AI 生成质量下降）
        :param message_list: 对话中的消息列表（可省略，默认为空列表）
        :param meta: 其他需要存储的元信息（可省略，默认为空字典）。对于单角色对话模式，目前没有使用该字段；对于小剧场模式，该字段用于存储剧本的相关信息，如角色的说话风格、互动细节、剧本设定等。
        :param chat_id: 对话的稳定标识。旧存档缺失时会自动生成。
        """
        self.chat_id = chat_id or uuid.uuid4().hex
        self.name = name
        self.type = type_

        if prompt_generator is None:
            self.prompt_generator = StaticPromptGenerator("")
        else:
            self.prompt_generator = prompt_generator

        self.start_message = start_message
        if isinstance(meta, ChatMeta):
            self.meta = copy.deepcopy(meta)
        else:
            self.meta = ChatMeta.from_dict(meta)

        # 存储对话中所有的消息
        self.message_list: List[Message]
        if message_list is not None:
            self.message_list = message_list
        else:
            self.message_list = []

    @classmethod
    def new_single_chat(
            cls,
            character: CharacterAttributes,
            user_character: CharacterAttributes | None = None,
            name: str = "新对话",
    ) -> "Chat":
        """
        创建一个新的用户-单角色对话

        :param character: 对话中的角色。在创建时，会自动拼接从角色信息中 System Prompt
        :param user_character: 可选的用户人设。创建时会冻结其当前实际生效的名称与描述。
        :param name: 对话名称（可省略）
        :returns: 新的 Chat 实例
        """
        user_persona = None
        if user_character is not None and not user_character.is_default_user:
            user_persona = UserPersonaSnapshot.from_character(user_character)
        generator = SingleCharacterPromptGenerator(character.character_name, user_persona)
        
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

    def __repr__(self) -> str:
        return f"Chat(name={self.name}, messages={self.message_list})"

    def remove_message(self, index: int) -> None:
        """
        根据索引删除记录中的一条消息
        """
        if 0 <= index < len(self.message_list):
            self.delete_message_at(index)

    @staticmethod
    def is_internal_event_user_message(message: Message) -> bool:
        """判断消息是否为系统内部事件注入的用户消息。"""
        return (
            message.character_name == "User"
            and message.text.strip().startswith("【系统内部事件触发")
        )

    @staticmethod
    def is_real_user_message(message: Message) -> bool:
        """判断消息是否为用户实际输入的消息。"""
        return (
            message.character_name == "User"
            and not Chat.is_internal_event_user_message(message)
        )

    @staticmethod
    def can_edit_and_resend_user_message(message: Message) -> bool:
        """判断消息是否允许作为编辑并重发的用户消息。"""
        return Chat.is_real_user_message(message)

    @staticmethod
    def can_edit_message(message: Message) -> bool:
        """判断消息是否允许手动编辑。"""
        return (
            Chat.is_real_user_message(message)
            or message.character_name != "User"
        )

    @staticmethod
    def can_rollback_to_message(message: Message) -> bool:
        """判断消息是否允许作为回溯锚点。"""
        return not Chat.is_internal_event_user_message(message)

    def find_last_real_user_message_index(self) -> int | None:
        """查找最后一条真实用户消息的索引。"""
        for index in range(len(self.message_list) - 1, -1, -1):
            if Chat.is_real_user_message(self.message_list[index]):
                return index
        return None

    def find_turn_range(self, message_index: int) -> MessageRange:
        """根据消息索引定位其所属的普通聊天轮次。"""
        if not (0 <= message_index < len(self.message_list)):
            raise IndexError(f"消息索引越界：{message_index}")

        start = message_index
        if self.message_list[message_index].character_name != "User":
            start = 0
            for index in range(message_index, -1, -1):
                if self.message_list[index].character_name == "User":
                    start = index
                    break

        end = len(self.message_list)
        for index in range(start + 1, len(self.message_list)):
            if self.message_list[index].character_name == "User":
                end = index
                break

        return MessageRange(start=start, end=end)

    def delete_message_at(self, message_index: int) -> DeleteMessagesResult:
        """删除指定索引处的单条消息。"""
        return self.delete_message_range(message_index, message_index + 1)

    def delete_turn_at(self, message_index: int) -> DeleteMessagesResult:
        """删除指定消息所属的完整普通聊天轮次。"""
        message_range = self.find_turn_range(message_index)
        return self.delete_message_range(message_range.start, message_range.end)

    def delete_message_range(self, start: int, end: int) -> DeleteMessagesResult:
        """删除消息范围并同步维护绑定到消息索引的工具调用记录。"""
        if not (0 <= start <= end <= len(self.message_list)):
            raise IndexError(f"消息范围越界：start={start}, end={end}")
        if start == end:
            return DeleteMessagesResult(
                start=start,
                end=end,
                deleted_messages=[],
                deleted_user_count=0,
                deleted_character_count=0,
            )

        deleted_messages = list(self.message_list[start:end])
        deleted_user_count = sum(1 for message in deleted_messages if message.character_name == "User")
        deleted_character_count = len(deleted_messages) - deleted_user_count
        del self.message_list[start:end]
        self._shift_tool_call_records_after_delete(start, end)

        return DeleteMessagesResult(
            start=start,
            end=end,
            deleted_messages=deleted_messages,
            deleted_user_count=deleted_user_count,
            deleted_character_count=deleted_character_count,
        )

    def _shift_tool_call_records_after_delete(self, start: int, end: int) -> None:
        """删除消息范围后，移除或平移工具调用记录的消息索引。"""
        removed_count = end - start
        updated_records: list[ToolCallRecordMeta] = []

        for record in self.meta.tool_call_records:
            message_index = record.message_index
            if start <= message_index < end:
                continue
            if message_index >= end:
                record.message_index = message_index - removed_count
            if 0 <= record.message_index < len(self.message_list):
                updated_records.append(record)

        self.meta.tool_call_records = updated_records

    def __str__(self) -> str:
        return f"{self.name}: {self.message_list}"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Chat):
            return self.to_dict() == other.to_dict()
        return False

    def to_dict(self) -> Dict:
        """
        将此 Chat 实例转换为存储字典
        """
        return {
            "chat_id": self.chat_id,
            "name": self.name,
            "prompt_config": self.prompt_generator.to_dict(),
            "type": self.type.value,
            "message_list": [msg.as_dict() for msg in self.message_list],
            "start_message": self.start_message,
            "meta": self.meta.to_dict()
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
            meta=data.get("meta", {}),
            chat_id=data.get("chat_id")
        )

    def get_theater_meta(self) -> TheaterMeta:
        """
        获取并确保小剧场对话元数据的基础结构存在。

        返回值为 `TheaterMeta`，需要传递给旧 UI 字典边界时请显式调用 `to_dict()`。
        """
        if not self.meta.theater.situation and self.start_message:
            self.meta.theater.situation = self.start_message
        if self.meta.theater.situation:
            self.start_message = self.meta.theater.situation

        return self.meta.theater

    def update_theater_meta(self, data: Dict[str, Any]) -> None:
        """更新小剧场元数据，并自动同步 `start_message`。"""
        theater_meta = self.get_theater_meta()

        for key, character_meta in (
            ("character_0", theater_meta.character_0),
            ("character_1", theater_meta.character_1),
        ):
            incoming = data.get(key)
            if isinstance(incoming, dict):
                talk_style = incoming.get("talk_style")
                interaction_details = incoming.get("interaction_details")
                if isinstance(talk_style, str):
                    character_meta.talk_style = talk_style
                if isinstance(interaction_details, str):
                    character_meta.interaction_details = interaction_details

        if "situation" in data:
            situation = data.get("situation")
            theater_meta.situation = situation if isinstance(situation, str) else ""

        self.start_message = theater_meta.situation
    
    def get_custom_live2d_model_meta(self, character_name: str) -> Optional[str]:
        """
        获取指定角色的自定义 Live2D 模型路径（如果有的话）。如果没有设置自定义模型或者自定义模型路径不存在，则从角色信息中读取默认模型路径。
        """
        model_path = self.meta.live2d_models.get(character_name)
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

        self.meta.live2d_models[character_name] = model_path.strip()

    def get_tool_call_records(self) -> List[ToolCallRecordMeta]:
        """获取当前对话的工具调用展示记录。"""
        return self.meta.tool_call_records

    def get_tool_call_record_dicts(self) -> List[dict[str, object]]:
        """获取工具调用展示记录的字典副本，供 UI 边界使用。"""
        return [record.to_dict() for record in self.meta.tool_call_records]

    def append_tool_call_record(self, record: ToolCallRecordMeta, limit: int = 300) -> None:
        """追加一条工具调用展示记录，并按上限裁剪。"""
        self.meta.tool_call_records.append(record)
        self.meta.tool_call_records = self.meta.tool_call_records[-limit:]

    def update_tool_call_record(self, record: ToolCallRecordMeta, limit: int = 300) -> None:
        """按 `tool_call_id` 更新工具调用展示记录，未找到时保持不变。"""
        for index in range(len(self.meta.tool_call_records) - 1, -1, -1):
            current = self.meta.tool_call_records[index]
            if current.tool_call_id == record.tool_call_id:
                self.meta.tool_call_records[index] = record
                break
        self.meta.tool_call_records = self.meta.tool_call_records[-limit:]

    def append_tool_call_history(self, record: ToolCallHistoryRecordMeta, limit: int = 100) -> None:
        """追加一条工具调用摘要轨迹，并按上限裁剪。"""
        self.meta.tool_call_history.append(record)
        self.meta.tool_call_history = self.meta.tool_call_history[-limit:]

    def clear_tool_call_meta(self) -> None:
        """清空与消息索引绑定的工具调用元数据。"""
        self.meta.tool_call_records.clear()
        self.meta.tool_call_history.clear()
        
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
        except Exception:
            logger.exception("未能从 QT 对话记录中提取音频信息")

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

    def build_llm_query(
        self,
        perspective: str,
        is_simplify: bool = False,
        include_translation: bool = True,
    ) -> List[Dict]:
        """
        根据当前对话内容，构建用于发送给 LLM 的对话历史列表

        :param perspective: 当前该说话的角色，可以从当前对话中涉及的角色选择，或者使用 "User" 选中用户视角（通常情况下，不应当选择 "User"，因为你不会希望 LLM 代替你说话的）
        :param is_simplify: 是否为主程序简化消息json
        :param include_translation: 简化 assistant 消息时是否保留 translation 字段
        :returns: 用于 LLM 的对话历史列表
        """
        llm_history = []
        system_prompt_content = self.prompt_generator.generate(perspective=perspective)
        if system_prompt_content:
            llm_history.append({
                "role": "system",
                "content": system_prompt_content
            })

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
            llm_character_name = self._llm_character_name_for_message(msg, role)
            llm_history.append({
                "role": role,
                "content": msg.to_llm_query(role, llm_character_name),
            })

        if not has_start_message and llm_history and llm_history[0]["role"] == "assistant":
            warnings.warn("当前对话没有设置起始消息，如果该对话为小剧场对话，可能会导致 LLM 生成质量下降和 API 请求错误。")

        return self.merge_llm_query(llm_history, is_simplify, include_translation)

    def _llm_character_name_for_message(self, message: Message, role: str) -> str:
        """返回消息发送给 LLM 时使用的角色标签。"""
        if role != "user" or message.character_name != "User":
            return message.character_name
        if message.text.strip().startswith("【系统内部事件触发"):
            return "User"
        if not isinstance(self.prompt_generator, SingleCharacterPromptGenerator):
            return "User"
        if self.prompt_generator.user_persona is None:
            return "User"
        return self.prompt_generator.user_persona.name

    @staticmethod
    def merge_llm_query(
        message_list: List[Dict],
        is_simplify: bool = False,
        include_translation: bool = True,
    ) -> List[Dict]:
        """
        直接修改 message_list 的内容，将所有相邻的 role 相同的消息合并为一条消息，以遵循 LLM 的对话格式要求
        请注意：列表会被原地修改，返回值和输入参数是同一个对象
        """
        if not message_list:
            return message_list
        if is_simplify:
            return Chat._simplify_llm_query(message_list, include_translation)

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

    @staticmethod
    def _append_assistant_content_to_simplified_segment(
        segment: dict[str, str],
        content: str,
        include_translation: bool,
    ) -> bool:
        """
        将一条 assistant JSON 内容追加到压缩段落中。
        """
        try:
            parsed_data: object = json.loads(content)
        except json.JSONDecodeError:
            return False

        if isinstance(parsed_data, dict):
            source_items: list[object] = [parsed_data]
        elif isinstance(parsed_data, list):
            source_items = parsed_data
        else:
            return False

        for item in source_items:
            if not isinstance(item, dict):
                return False
            segment["text"] += str(item.get("text") or "")
            if include_translation:
                segment["translation"] += str(item.get("translation") or "")
            if not segment["emotion"]:
                segment["emotion"] = str(item.get("emotion") or "")

        return True

    @staticmethod
    def _simplify_llm_query(message_list: List[Dict], include_translation: bool = True) -> List[Dict]:
        """
        合并相邻消息，并将 assistant 消息压缩为符合主程序输出协议的 JSON array。
        """
        merged_list: list[dict[str, str]] = []
        active_role = ""
        active_content = ""
        active_assistant_segment: dict[str, str] | None = None

        def flush_active_message() -> None:
            """
            将当前累计消息写入合并列表。
            """
            nonlocal active_role, active_content, active_assistant_segment
            if not active_role:
                return

            if active_role == "assistant" and active_assistant_segment is not None:
                segment = {
                    "text": active_assistant_segment["text"],
                    "emotion": active_assistant_segment["emotion"] or "happiness",
                }
                if include_translation:
                    segment["translation"] = active_assistant_segment["translation"]
                content = json.dumps([segment], ensure_ascii=False)
            else:
                content = active_content

            merged_list.append({
                "role": active_role,
                "content": content,
            })

        for msg in message_list:
            role = str(msg["role"])
            content = str(msg["content"])
            if role != active_role:
                flush_active_message()
                active_role = role
                active_content = ""
                active_assistant_segment = None

            if role == "assistant":
                if active_assistant_segment is None:
                    active_assistant_segment = {"text": "", "translation": "", "emotion": ""}
                if not Chat._append_assistant_content_to_simplified_segment(
                    active_assistant_segment,
                    content,
                    include_translation,
                ):
                    active_assistant_segment["text"] += content
                    if not active_assistant_segment["emotion"]:
                        active_assistant_segment["emotion"] = "happiness"
            elif active_content:
                active_content += "\n" + content
            else:
                active_content = content

        flush_active_message()
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
        # 必须同时清除该对话的工具调用记录
        # 这些元数据绑定到 message_index；清空消息后若保留，重启渲染时会挂到新消息上。
        self.clear_tool_call_meta()

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

    def add_chat(self, chat: Chat) -> None:
        """
        添加一个对话到管理器中。如果该对话已经存在，则忽略。
        """
        if chat not in self.chat_list:
            self.chat_list.append(chat)

    def add_chats(self, chats: Iterable[Chat]) -> None:
        """
        添加多个对话到管理器中，忽略已经存在的对话。
        """
        for chat in chats:
            self.add_chat(chat)

    def single_character_chats(self) -> list[Chat]:
        """
        获取所有普通单角色对话。
        """
        return [chat for chat in self.chat_list if chat.type == ChatType.SINGLE_CHARACTER]

    def theater_chats(self) -> list[Chat]:
        """
        获得所有小剧场模式的对话。
        """
        return [chat for chat in self.chat_list if chat.type == ChatType.SMALL_THEATER]
    
    def chats_by_type(self, chat_type: ChatType) -> list[Chat]:
        """
        获取指定类型的所有对话。
        """
        return [chat for chat in self.chat_list if chat.type == chat_type]

    def get_chat_by_id(self, chat_id: str) -> Optional[Chat]:
        """
        根据稳定标识查找对话。
        """
        for chat in self.chat_list:
            if chat.chat_id == chat_id:
                return chat
        return None

    def create_single_character_chat(
            self,
            character: CharacterAttributes,
            name: str | None = None,
            user_character: CharacterAttributes | None = None,
    ) -> Chat:
        """
        创建一条新的普通单角色对话；同一角色允许存在多条对话。
        """
        chat_name = name.strip() if isinstance(name, str) and name.strip() else self._default_single_chat_name(character)
        chat = Chat.new_single_chat(
            character,
            user_character=user_character,
            name=chat_name,
        )
        self.chat_list.append(chat)
        return chat

    def delete_chat(self, chat_id: str) -> Optional[Chat]:
        """
        删除指定对话，并返回被删除的对话；若不存在则返回 None。
        """
        for index, chat in enumerate(self.chat_list):
            if chat.chat_id == chat_id:
                return self.chat_list.pop(index)
        return None

    def reorder_chats_by_type(self, chat_type: ChatType, ordered_chat_ids: Sequence[str]) -> None:
        """
        在指定对话类型内部重排顺序，不改变其他类型对话的相对顺序。

        :param chat_type: 需要重排的对话类型
        :param ordered_chat_ids: 按照新顺序排列的对话 ID 列表。列表中 ID 的顺序将被应用于对应的对话，但列表中不需要包含所有对话 ID，缺失的对话将被放在末尾；如果列表中包含不存在的 ID，则会被忽略。
        """
        chats_of_type = self.chats_by_type(chat_type)
        chat_by_id = {chat.chat_id: chat for chat in chats_of_type}
        ordered_chats: list[Chat] = []
        used_chat_ids: set[str] = set()

        # 重排所有在 ordered_chat_ids 中出现的对话
        for chat_id in ordered_chat_ids:
            chat = chat_by_id.get(chat_id)
            if chat is not None and chat_id not in used_chat_ids:
                ordered_chats.append(chat)
                used_chat_ids.add(chat_id)

        # 将所有未在 ordered_chat_ids 中出现的对话追加到末尾，保持它们的相对顺序不变
        for chat in chats_of_type:
            if chat.chat_id not in used_chat_ids:
                ordered_chats.append(chat)

        # 将重排后的对话列表与其他类型的对话合并，形成新的 chat_list
        ordered_iter = iter(ordered_chats)
        new_chat_list: list[Chat] = []
        for chat in self.chat_list:
            if chat.type == chat_type:
                new_chat_list.append(next(ordered_iter))
            else:
                new_chat_list.append(chat)
        self.chat_list = new_chat_list

    def ensure_default_single_character_chat(self, characters: Sequence[CharacterAttributes]) -> Chat:
        """
        确保至少存在一条普通单角色对话，并返回默认选中的普通对话。
        """
        existing_chats = self.single_character_chats()
        if existing_chats:
            return existing_chats[0]
        if not characters:
            raise ValueError("无法创建默认普通对话：角色列表为空。")
        return self.create_single_character_chat(characters[0], name=characters[0].character_name)

    def collect_audio_paths_for_chat(self, chat_id: str) -> list[str]:
        """
        收集指定对话中引用的真实本地音频路径。
        """
        chat = self.get_chat_by_id(chat_id)
        if chat is None:
            return []
        return self.collect_audio_paths_from_messages(chat.message_list)

    def delete_unreferenced_audio_files_for_chat(self, chat_id: str) -> list[str]:
        """
        删除指定对话独占引用的历史音频文件，并返回已删除文件列表。
        """
        target_chat = self.get_chat_by_id(chat_id)
        if target_chat is None:
            return []

        return self.delete_unreferenced_audio_files_for_messages(target_chat.message_list).deleted_paths

    def _default_single_chat_name(self, character: CharacterAttributes) -> str:
        """
        为普通单角色对话生成默认名称。
        """
        base_name = f"{character.character_name}的对话"
        existing_names = {chat.name for chat in self.single_character_chats()}
        if base_name not in existing_names:
            return base_name
        counter = 2
        while f"{base_name} {counter}" in existing_names:
            counter += 1
        return f"{base_name} {counter}"

    def collect_audio_paths_from_messages(self, messages: Sequence[Message]) -> list[str]:
        """从一组消息中收集可安全处理的本地音频文件路径。"""
        audio_paths: list[str] = []
        seen_paths: set[str] = set()
        for message in messages:
            normalized_path = self._normalize_audio_path(message.audio_path)
            if normalized_path is None or normalized_path in seen_paths:
                continue
            audio_paths.append(normalized_path)
            seen_paths.add(normalized_path)
        return audio_paths

    def collect_deletable_audio_paths_from_messages(self, messages: Sequence[Message]) -> list[str]:
        """收集删除给定消息后不再被任何剩余消息引用的音频路径。"""
        candidate_paths = self.collect_audio_paths_from_messages(messages)
        if not candidate_paths:
            return []

        candidate_message_ids = {id(message) for message in messages}
        candidate_path_set = set(candidate_paths)
        referenced_paths: set[str] = set()

        for chat in self.chat_list:
            for message in chat.message_list:
                if id(message) in candidate_message_ids:
                    continue
                normalized_path = self._normalize_audio_path(message.audio_path)
                if normalized_path in candidate_path_set:
                    referenced_paths.add(normalized_path)

        return [audio_path for audio_path in candidate_paths if audio_path not in referenced_paths]

    def delete_unreferenced_audio_files_for_messages(
        self,
        messages: Sequence[Message],
    ) -> AudioDeleteResult:
        """删除给定消息对应且不再被其他消息引用的音频文件。"""
        deleted_paths: list[str] = []
        failed_paths: list[str] = []

        for audio_path in self.collect_deletable_audio_paths_from_messages(messages):
            try:
                os.remove(audio_path)
                deleted_paths.append(audio_path)
            except FileNotFoundError:
                continue
            except OSError:
                logger.exception("删除音频文件失败：%s", audio_path)
                failed_paths.append(audio_path)

        return AudioDeleteResult(deleted_paths=deleted_paths, failed_paths=failed_paths)

    def _collect_audio_paths_from_other_chats(self, excluded_chat_id: str) -> set[str]:
        """
        收集除指定对话以外其他对话引用的音频文件路径。
        """
        audio_paths: set[str] = set()
        for chat in self.chat_list:
            if chat.chat_id == excluded_chat_id:
                continue
            audio_paths.update(self.collect_audio_paths_from_messages(chat.message_list))
        return audio_paths

    @staticmethod
    def _normalize_audio_path(audio_path: str) -> Optional[str]:
        """
        标准化消息中的音频路径，并排除占位音频。
        """
        # 排除三种占位音频：空、NO_AUDIO 和 silence.wav
        if not audio_path or audio_path == "NO_AUDIO":
            return None
        if "silence.wav" in audio_path:
            return None
        clean_path = audio_path.split("[", 1)[0]
        path = Path(clean_path)
        if not path.exists() or not path.is_file():
            return None
        return str(path.resolve())

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
            except Exception:
                logger.exception("未能加载 QT 对话记录文件 '%s'", qt_file)
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


def _move_legacy_files_to_backup(legacy_files: Optional[Iterable[Union[str, Path]]] = None):
    """
    将旧版聊天记录文件移动到 old_history_messages 文件夹中，
    避免每次启动时重复触发迁移操作。
    """
    if legacy_files is None:
        legacy_files: Iterable[Union[str, Path]] = [
            "../reference_audio/history_messages_dp.json",
            "../reference_audio/history_messages_qt.json",
            "../reference_audio/small_theater_history.json",
        ]

    backup_dir = "../reference_audio/old_history_messages"
    legacy_file_names = [str(f) for f in legacy_files]
    any_exists = any(os.path.exists(f) for f in legacy_file_names)
    if not any_exists:
        return

    os.makedirs(backup_dir, exist_ok=True)
    for f in legacy_file_names:
        if os.path.exists(f):
            dest = os.path.join(backup_dir, os.path.basename(f))
            try:
                shutil.move(f, dest)
                logger.info("已将旧版记录文件 %s 移动到 %s", f, dest)
            except Exception:
                logger.exception("移动旧版记录文件 %s 失败", f)


def _generate_filename_with_timestamp(
    filename: Union[str, Path],
) -> str:
    """
    根据一个文件的文件名，生成一个带时间戳版本的文件名。

    >>> _generate_filename_with_timestamp("../reference_audio/history_messages_qt.json")
    'history_messages_qt_20240610-153045.json'
    """
    path = Path(filename)
    return f"{path.stem}_{datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]}{path.suffix}"


def backup_file(files: Iterable[Union[str, Path]], destination_folder = "../reference_audio/backup") -> None:
    """
    备份指定的一系列文件，将其复制到指定的文件夹中并且按照当前时间进行重命名。

    :param files: 需要备份的一系列文件
    :param destination_folder: 备份应当复制到的目标文件夹。如果该文件夹尚不存在，则会被创建。
    :raises OSError: 如果给定的文件不存在，给定的目标文件夹无法写入……
    """
    os.makedirs(destination_folder, exist_ok=True)
    for file in files:
        shutil.copyfile(file, os.path.join(destination_folder, _generate_filename_with_timestamp(file)))


def get_chat_manager() -> ChatManager:
    """
    获取全局的 ChatManager 实例。如果尚未创建，则创建一个新的实例。
    创建新的实例时，尝试从默认存储文件加载对话记录；如果加载失败，则尝试从主程序的对话记录中转换数据。
    成功从旧格式迁移后，会将旧文件移动到 old_history_messages 文件夹中。

    :returns: 全局的 ChatManager 实例
    """
    global _global_chat_manager

    # 我们可能遇到如下几种情况（取决于用户多久没更新程序）
    # 1. 用户已经有了 all_conversation.json ，存放了普通对话和小剧场对话，直接加载它。
    # 2. 用户已经有了 all_conversation.json ，但其中只包含小剧场记录。先加载存档，再补充（迁移）旧版本的普通对话。
    # 如何区分 1/2 两种情况呢？如果说 reference_audio/history_messages_dp.json 和 history_messages_qt.json 存在的话，我们就认为是情况 2
    # （普通对话没有转换）；如果说它们不存在，我们就认为是情况 1。
    # 3. 用户没有 all_conversation.json。此时，需要同时迁移小剧场记录和普通对话记录。如果用户同时也没有旧的对话记录，那么就新建一个空白存档。

    # 这对应了三个存档时期：
    # 1. 最新版本：all_conversation.json 同时存储普通对话和小剧场对话
    # 2. 过渡版本：引入了 all_conversation.json，但只用于存储小剧场对话，普通对话仍然存储在 histroy_messages_dp.json 和 history_messages_qt.json 中
    # 3. 最旧版本：没有引入 all_conversation.json，普通对话存储在 histroy_messages_dp.json 和 history_messages_qt.json 中。
    # 小剧场对话存储在 small_theater_history.json
    # 可以看到，只要 all_conversation.json 存在，那么它一定包含小剧场对话（排除版本 3，此时不用加载旧版本小剧场对话），但不确定是否包含普通对话（无法区分版本 1/2）
    # 区分版本 1/2 需要检查文件夹下是否存在 histroy_messages_dp.json 和 history_messages_qt.json。

    if _global_chat_manager is None:
        try:
            all_conversation_file = "../reference_audio/all_conversation.json"
            legacy_llm_file = "../reference_audio/history_messages_dp.json"
            legacy_qt_file = "../reference_audio/history_messages_qt.json"
            legacy_theater_file = "../reference_audio/small_theater_history.json"

            has_new_record = os.path.exists(all_conversation_file)
            # 旧的主对话记录和小剧场记录默认都是空白文件，
            # 空白文件会在 json.load 时被判定为加载失败；但我们不希望将其按照加载失败处理（打印错误消息），因为本来就没东西
            # 因此，只在这两个文件不为空时认为它可能存在。
            has_legacy_main_record = os.path.exists(legacy_llm_file) and not os.path.getsize(legacy_llm_file) == 0
            has_legacy_theater_record = os.path.exists(legacy_theater_file) and not os.path.getsize(legacy_theater_file) == 0
            migrated_legacy_files: List[str] = []

            # 优先级 1->2->3：只要有 all_conversation.json，就一定要加载并读取数据，避免情况 1/2 下的数据丢失。
            if has_new_record:
                try:
                    _global_chat_manager = ChatManager.load(all_conversation_file)
                # 如果这个文件因为自身格式错误无法加载，尝试备份一份。
                except (json.JSONDecodeError, KeyError, ValueError):
                    logger.exception("all_conversation.json 已损坏，未能加载其中的对话记录。")
                    try:
                        backup_file(files=[all_conversation_file])
                        logger.info("已将损坏的 all_conversation.json 备份到 ../reference_audio/backup 文件夹中。")
                    except OSError:
                        logger.exception("备份损坏的 all_conversation.json 失败")

                    _global_chat_manager = ChatManager()
            else:
                _global_chat_manager = ChatManager()

            # 有旧的普通对话记录时，将其补充到当前存档中。
            if has_legacy_main_record:
                try:
                    old_chat_manager = ChatManager.load_from_main_record(
                        llm_file=legacy_llm_file,
                        qt_file=legacy_qt_file,
                    )
                    _global_chat_manager.add_chats(old_chat_manager.chat_list)
                    migrated_legacy_files.append(legacy_llm_file)
                    if os.path.exists(legacy_qt_file):
                        migrated_legacy_files.append(legacy_qt_file)
                except Exception:
                    logger.exception("未能成功加载旧格式的普通对话记录。若确认旧记录仍需保留，请备份 ../reference_audio/history_messages_dp.json 后再重启程序排查。")

            # 如果有旧的小剧场对话（且不存在 all_conversation.json），那么加载旧版本小剧场对话。
            if not has_new_record and has_legacy_theater_record:
                try:
                    _global_chat_manager.add_theater_mode_history(file=legacy_theater_file)
                    migrated_legacy_files.append(legacy_theater_file)
                except Exception:
                    logger.exception("未能成功加载旧格式的小剧场对话记录。若确认旧记录仍需保留，请备份 ../reference_audio/small_theater_history.json 后再重启程序排查。")

            # 如果什么都不存在，那么新建存档。
            if not has_new_record and not has_legacy_main_record and not has_legacy_theater_record:
                logger.warning("未找到任何存在的对话记录文件，将新建。")

            # 迁移那些已经不需要的文件到另一个文件夹，避免每次启动都触发迁移流程。
            if migrated_legacy_files:
                _global_chat_manager.save(all_conversation_file)
                _move_legacy_files_to_backup(migrated_legacy_files)
                logger.info("已成功将旧版聊天记录迁移到新格式。旧记录文件备份位置：../reference_audio/old_history_messages")
        except Exception:
            logger.exception("加载对话记录时出错，将新建空白存档。如果确定已有对话文件（../reference_audio/all_conversation.json），请务必先备份文件！")
            input("按回车确认继续运行，但可能导致本次对话所产生的聊天记录内容丢失！")
            _global_chat_manager = ChatManager()

    return _global_chat_manager


if __name__ == "__main__":
    get_chat_manager().save()
