from __future__ import annotations

import json
import os
import unittest
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from character import CharacterAttributes
from chat.chat import (
    Chat,
    ChatManager,
    ChatType,
    Message,
    SingleCharacterPromptGenerator,
    StaticPromptGenerator,
    UserPersonaSnapshot,
)
from emotion_enum import EmotionEnum


class ChatTestCase(unittest.TestCase):
    def setUp(self):
        self.llm_dialog_history = \
        {
            "character": "素世",
            "history": [
                {
                    "role": "system",
                    "content": "你要扮演一位动画角色，她的名字叫长崎爽世（日语：長崎そよ）。不会示人的那一面。女生的错”，问答过程中我会告诉你本次是用中文还是日文回答。请直接进入角色的状态。另外，当使用日语回答时，用简体即可，除非有要求说用敬语。\n"
                },
                {
                    "role": "user",
                    "content": "你好呀，今天过的怎么样？（本句话你的回答请务必用日语，并且请将额外的中文翻译内容放到“[翻译]”这一标记格式之后，并以“[翻译结束]”作为翻译的结束标志，每三句话翻译一次！请务必严格遵守这一格式回答！！）"
                },
                {
                    "role": "assistant",
                    "content": "こんにちは。今日は吹奏楽部の練習で低音提琴を弾いて、少し疲れたけど、充実してたよ。家に帰ってから野菜スープを飲みながら、紅茶も淹れたんだ。たまにはこうして静かに過ごすのも悪くないね。\n\n[翻译]\n你好。今天在吹奏乐部练习了低音提琴，虽然有点累，但很充实。回家后一边喝蔬菜通心粉汤，一边泡了红茶。偶尔这样安静地度过也不坏呢。\n[翻译结束]"
                }
            ]
        }

        self.qt_dialog_history = \
        {
            "character": "素世",
            "history": "<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.0//EN\" \"http://www.w3.org/TR/REC-html40/strict.dtd\">\n<html><head><meta name=\"qrichtext\" content=\"1\" /><style type=\"text/css\">\np, li { white-space: pre-wrap; }\n</style></head><body style=\" font-family:'.AppleSystemUIFont'; font-size:13pt; font-weight:400; font-style:normal;\">\n<p style=\" margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;\"><span style=\" color:#7799cc;\">你：你好呀，今天过的怎么样？</span></p>\n<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; color:#7799cc;\"><br /></p>\n<p style=\" margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;\"><a href=\"/Users/liyanxiao/Documents/Python Files/D_sakiko/reference_audio/generated_audios_temp/output38.wav[LABEL_0]\"><span style=\" color:#7799cc;\">★素世：こんにちは。今日は吹奏楽部の練習で低音提琴を弾いて、少し疲れたけど、充実してたよ。家に帰ってから野菜スープを飲みながら、紅茶も淹れたんだ。たまにはこうして静かに過ごすのも悪くないね。</span></a></p>\n<p style=\" margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;\"><span style=\" font-style:italic; color:#b3d1f2;\">你好。今天在吹奏乐部练习了低音提琴，虽然有点累，但很充实。回家后一边喝蔬菜通心粉汤，一边泡了红茶。偶尔这样安静地度过也不坏呢。</span><br /></p>\n<p style=\" margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;\"><span style=\" color:#7799cc;\">你：bye</span></p>\n<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; color:#7799cc;\"><br /></p></body></html>"
        }

        self.theater_dialog_history = \
        [
            [
                {
                    "text": "あっ…！ さきちゃん！",
                    "audio_path": "../reference_audio/generated_audios_temp/output75.wav",
                    "char_name": "素世",
                    "emotion": "surprise",
                    "translation": "啊……！祥子！",
                    "live2d_motion_num": 33
                },
                {
                    "text": "…はあ。",
                    "audio_path": "../reference_audio/generated_audios_temp/output61.wav",
                    "char_name": "祥子",
                    "emotion": "disgust",
                    "translation": "……唉。",
                    "live2d_motion_num": 18
                }
            ],
            [
                {
                    "新的对话0": [0, 2]
                }
            ]
        ]
        # 小剧场测试需要从文件读取数据
        # 我们造一个临时文件给函数，用于测试
        self.theater_dialog_temp_file = tempfile.NamedTemporaryFile(mode="w", delete=False)
        json.dump(self.theater_dialog_history, self.theater_dialog_temp_file)
        self.theater_dialog_temp_file.flush()
        self.theater_dialog_temp_file.close()

    def tearDown(self):
        # 测试完成后需要手动删掉临时文件
        os.unlink(self.theater_dialog_temp_file.name)

    def test_compatibility_of_normal_mode(self):
        """
        测试 Chat 类是否能读取旧版本的单角色对话模式存档
        """
        chat = Chat.load_from_main_record(self.llm_dialog_history, self.qt_dialog_history)
        # 测试读取内容是否正确
        user_message = chat.message_list[0]
        self.assertEqual(user_message.character_name, "User")
        self.assertEqual(user_message.text, "你好呀，今天过的怎么样？")
        self.assertEqual(user_message.translation, "")
        self.assertEqual(user_message.emotion, EmotionEnum.HAPPINESS)
        self.assertEqual(user_message.audio_path, "")

        assistant_message = chat.message_list[1]
        self.assertEqual(assistant_message.character_name, "素世")
        self.assertEqual(assistant_message.text, "こんにちは。今日は吹奏楽部の練習で低音提琴を弾いて、少し疲れたけど、充実してたよ。家に帰ってから野菜スープを飲みながら、紅茶も淹れたんだ。たまにはこうして静かに過ごすのも悪くないね。")
        self.assertEqual(assistant_message.translation, "你好。今天在吹奏乐部练习了低音提琴，虽然有点累，但很充实。回家后一边喝蔬菜通心粉汤，一边泡了红茶。偶尔这样安静地度过也不坏呢。")
        self.assertEqual(assistant_message.emotion, EmotionEnum.HAPPINESS)
        self.assertTrue(assistant_message.audio_path.endswith("output38.wav"))

    def test_compatibility_of_theater_mode(self):
        """
        测试 Chat 类是否能读取旧版本的小剧场模式存档
        """
        chat_manager = ChatManager.load_from_theater_record(file=self.theater_dialog_temp_file.name)
        chat = chat_manager.chat_list[0]
        # 测试读取内容是否正确
        first_message = chat.message_list[0]
        self.assertEqual(first_message.character_name, "素世")
        self.assertEqual(first_message.text, "あっ…！ さきちゃん！")
        self.assertEqual(first_message.translation, "啊……！祥子！")
        self.assertEqual(first_message.emotion, EmotionEnum.SURPRISE)
        self.assertTrue(first_message.audio_path.endswith("output75.wav"))

        second_message = chat.message_list[1]
        self.assertEqual(second_message.character_name, "祥子")
        self.assertEqual(second_message.text, "…はあ。")
        self.assertEqual(second_message.translation, "……唉。")
        self.assertEqual(second_message.emotion, EmotionEnum.DISGUST)
        self.assertTrue(second_message.audio_path.endswith("output61.wav"))

    def test_chat_save_load(self):
        """
        测试 Chat 类的存档与读取功能是否正常
        通过将读取的存档再存档一次，然后对比两次存档的内容是否一致来验证
        """
        chat = Chat.load_from_main_record(self.llm_dialog_history, self.qt_dialog_history)
        diction = chat.to_dict()
        chat_restored = Chat.from_dict(diction)
        self.assertEqual(len(chat.message_list), len(chat_restored.message_list))
        for original_msg, restored_msg in zip(chat.message_list, chat_restored.message_list):
            self.assertEqual(original_msg.character_name, restored_msg.character_name)
            self.assertEqual(original_msg.text, restored_msg.text)
            self.assertEqual(original_msg.translation, restored_msg.translation)
            self.assertEqual(original_msg.emotion, restored_msg.emotion)
            self.assertEqual(original_msg.audio_path, restored_msg.audio_path)

    def test_clear_message_list_removes_tool_call_records(self):
        """
        清空聊天记录时，工具调用记录也应一并清空，避免按旧 message_index 重新渲染到新消息上。
        """
        chat = Chat(
            message_list=[
                Message(
                    character_name="User",
                    text="今天天气怎么样？",
                    translation="",
                    emotion=EmotionEnum.HAPPINESS,
                    audio_path="",
                )
            ],
            meta={
                "tool_call_records": [{"tool_call_id": "call_1", "message_index": 0}],
                "tool_call_history": [{"tool_rounds": 1}],
                "theater": {"situation": "保留的小剧场设定"},
                "live2d_models": {"爱音": "/tmp/anon.model3.json"},
            },
        )

        chat.clear_message_list()

        self.assertEqual(chat.message_list, [])
        self.assertEqual(chat.meta.tool_call_records, [])
        self.assertEqual(chat.meta.tool_call_history, [])
        self.assertEqual(chat.meta.theater.situation, "保留的小剧场设定")
        self.assertEqual(chat.meta.live2d_models["爱音"], "/tmp/anon.model3.json")

    def test_find_turn_range_for_user_and_assistant_messages(self):
        """
        普通聊天轮次应从 User 消息开始，到下一条 User 消息之前结束。
        """
        chat = Chat(
            message_list=[
                self._message("User", "第一轮用户"),
                self._message("祥子", "第一轮回复1"),
                self._message("祥子", "第一轮回复2"),
                self._message("User", "第二轮用户"),
                self._message("祥子", "第二轮回复"),
            ],
        )

        user_range = chat.find_turn_range(0)
        assistant_range = chat.find_turn_range(2)
        last_range = chat.find_turn_range(4)

        self.assertEqual((user_range.start, user_range.end), (0, 3))
        self.assertEqual((assistant_range.start, assistant_range.end), (0, 3))
        self.assertEqual((last_range.start, last_range.end), (3, 5))

    def test_find_turn_range_for_opening_assistant_messages(self):
        """
        对话开头没有 User 时，开头连续角色消息应被视为开场轮次。
        """
        chat = Chat(
            message_list=[
                self._message("祥子", "开场1"),
                self._message("祥子", "开场2"),
                self._message("User", "用户"),
                self._message("祥子", "回复"),
            ],
        )

        message_range = chat.find_turn_range(1)

        self.assertEqual((message_range.start, message_range.end), (0, 2))

    def test_delete_message_range_updates_tool_call_records(self):
        """
        删除消息范围时，应删除范围内工具记录并平移后续工具记录索引。
        """
        chat = Chat(
            message_list=[
                self._message("User", "第一轮用户"),
                self._message("祥子", "第一轮回复1"),
                self._message("祥子", "第一轮回复2"),
                self._message("User", "第二轮用户"),
                self._message("祥子", "第二轮回复"),
            ],
            meta={
                "tool_call_records": [
                    {"tool_call_id": "invalid", "message_index": -1},
                    {"tool_call_id": "inside", "message_index": 1},
                    {"tool_call_id": "after_user", "message_index": 3},
                    {"tool_call_id": "after_assistant", "message_index": 4},
                ],
                "tool_call_history": [{"tool_rounds": 1}],
            },
        )

        result = chat.delete_message_range(0, 3)

        self.assertEqual(result.deleted_user_count, 1)
        self.assertEqual(result.deleted_character_count, 2)
        self.assertEqual([message.text for message in chat.message_list], ["第二轮用户", "第二轮回复"])
        records = chat.get_tool_call_record_dicts()
        self.assertEqual(
            [(record["tool_call_id"], record["message_index"]) for record in records],
            [("after_user", 0), ("after_assistant", 1)],
        )
        self.assertEqual(len(chat.meta.tool_call_history), 1)

    def test_delete_turn_at_reuses_turn_range(self):
        """
        删除角色消息所属轮次时，应连带删除最近的上一条用户消息。
        """
        chat = Chat(
            message_list=[
                self._message("User", "第一轮用户"),
                self._message("祥子", "第一轮回复"),
                self._message("User", "第二轮用户"),
                self._message("祥子", "第二轮回复"),
            ],
        )

        result = chat.delete_turn_at(1)

        self.assertEqual(result.deleted_user_count, 1)
        self.assertEqual(result.deleted_character_count, 1)
        self.assertEqual([message.text for message in chat.message_list], ["第二轮用户", "第二轮回复"])

    def test_can_edit_and_resend_only_real_user_message(self) -> None:
        """
        只有真实用户消息允许编辑并重发，内部事件和角色消息不允许。
        """
        self.assertTrue(Chat.is_real_user_message(self._message("User", "普通消息")))
        self.assertFalse(
            Chat.is_real_user_message(
                self._message("User", "【系统内部事件触发：提醒】\n该喝水了")
            )
        )
        self.assertTrue(Chat.can_edit_and_resend_user_message(self._message("User", "普通消息")))
        self.assertFalse(
            Chat.can_edit_and_resend_user_message(
                self._message("User", "【系统内部事件触发：提醒】\n该喝水了")
            )
        )
        self.assertFalse(Chat.can_edit_and_resend_user_message(self._message("祥子", "角色消息")))

    def test_can_rollback_to_message_excludes_internal_event_user_message(self) -> None:
        """
        回溯锚点应排除系统内部事件 User 消息，但允许真实用户消息和角色消息。
        """
        self.assertTrue(Chat.can_rollback_to_message(self._message("User", "普通消息")))
        self.assertTrue(Chat.can_rollback_to_message(self._message("祥子", "角色消息")))
        self.assertFalse(
            Chat.can_rollback_to_message(
                self._message("User", "【系统内部事件触发：提醒】\n该喝水了")
            )
        )

    def test_find_last_real_user_message_index_skips_internal_events(self) -> None:
        """
        最后一条真实用户消息应跳过系统内部事件 User 消息。
        """
        chat = Chat(
            message_list=[
                self._message("User", "第一条真实消息"),
                self._message("祥子", "回复"),
                self._message("User", "最后真实消息"),
                self._message("User", "【系统内部事件触发：提醒】\n该喝水了"),
                self._message("祥子", "后续回复"),
            ],
        )

        self.assertEqual(chat.find_last_real_user_message_index(), 2)

    def test_involved_characters(self):
        """
        测试 Chat 类能否正确识别对话中涉及的角色
        """
        chat = Chat.load_from_main_record(self.llm_dialog_history, self.qt_dialog_history)
        self.assertEqual(set(chat.involved_characters), {"素世", "User"})

    def test_llm_query_for_single_character(self):
        """
        测试单个智能体对话时，构建 LLM 查询的正确性
        """
        chat = Chat.load_from_main_record(self.llm_dialog_history, self.qt_dialog_history)
        query = chat.build_llm_query("素世")
        self.assertEqual(query[0]["role"], "system")
        self.assertEqual(query[1]["role"], "user")
        self.assertEqual(query[2]["role"], "assistant")
        self.assertIn("User", query[1]["content"])
        self.assertIn("素世", query[2]["content"])

        self.assertFalse(chat.is_my_turn("素世"))

    def test_llm_query_places_system_before_start_message(self):
        """
        有起始场景和 system prompt 时，system prompt 应位于请求开头。
        """
        chat = Chat(
            prompt_generator=StaticPromptGenerator("系统设定"),
            start_message="场景说明",
            message_list=[
                Message(
                    character_name="User",
                    text="你好",
                    translation="",
                    emotion=EmotionEnum.HAPPINESS,
                    audio_path="",
                )
            ],
        )

        query = chat.build_llm_query("角色")

        self.assertEqual(query[0]["role"], "system")
        self.assertEqual(query[0]["content"], "系统设定")
        self.assertEqual(query[1]["role"], "user")
        self.assertIn("场景说明", query[1]["content"])

    def test_simplified_llm_query_keeps_assistant_content_as_array(self):
        """
        简化后的 assistant 历史应保持 JSON array 顶层，并合并连续消息字段。
        """
        chat = Chat(
            start_message="场景说明",
            message_list=[
                Message(
                    character_name="素世",
                    text="こんにちは。",
                    translation="你好。",
                    emotion=EmotionEnum.HAPPINESS,
                    audio_path="",
                ),
                Message(
                    character_name="素世",
                    text="少し疲れた。",
                    translation="有点累。",
                    emotion=EmotionEnum.SADNESS,
                    audio_path="",
                ),
            ],
        )

        query = chat.build_llm_query("素世", is_simplify=True)
        content = json.loads(query[1]["content"])

        self.assertEqual(query[1]["role"], "assistant")
        self.assertEqual(
            content,
            [
                {
                    "text": "こんにちは。少し疲れた。",
                    "translation": "你好。有点累。",
                    "emotion": "happiness",
                }
            ],
        )
        self.assertNotIn("speaker", content[0])

    def test_simplified_llm_query_can_omit_translation(self):
        """
        简化历史可按运行期语言模式省略 translation 字段。
        """
        chat = Chat(
            start_message="场景说明",
            message_list=[
                Message(
                    character_name="素世",
                    text="你好。",
                    translation="hello.",
                    emotion=EmotionEnum.HAPPINESS,
                    audio_path="",
                )
            ],
        )

        query = chat.build_llm_query("素世", is_simplify=True, include_translation=False)
        content = json.loads(query[1]["content"])

        self.assertEqual(content, [{"text": "你好。", "emotion": "happiness"}])

    def test_llm_query_for_multiple_character(self):
        """
        测试多个智能体对话时，构建 LLM 查询的正确性
        """
        chat = ChatManager.load_from_theater_record(self.theater_dialog_temp_file.name).chat_list[0]
        chat.start_message = "在飞鸟山公园的谈话中，祥子向素世提到“不要再和我扯上关系”。然而，在之后的一天晚上，放学后的素世在路上偶然遇到了祥子，素世想从祥子口中得知她退出 Crychic 的真正原因，向她追了过去"

        self.assertTrue(chat.is_my_turn("素世"))
        self.assertFalse(chat.is_my_turn("祥子"))

        query = chat.build_llm_query("祥子")
        self.assertEqual(query[0]["role"], "user")
        self.assertEqual(query[1]["role"], "assistant")
        self.assertIn("素世", query[0]["content"])
        self.assertIn("祥子", query[1]["content"])

        query_2 = chat.build_llm_query("素世")
        self.assertEqual(query_2[0]["role"], "user")
        self.assertEqual(query_2[1]["role"], "assistant")
        self.assertIn("祥子", query_2[0]["content"])
        self.assertIn("素世", query_2[1]["content"])

    def test_chat_id_serialization_and_legacy_loading(self):
        """
        Chat 应持久化 chat_id，旧存档缺失 chat_id 时应自动补齐。
        """
        chat = Chat.load_from_main_record(self.llm_dialog_history, self.qt_dialog_history)
        data = chat.to_dict()
        self.assertIn("chat_id", data)

        legacy_data = dict(data)
        legacy_data.pop("chat_id")
        restored = Chat.from_dict(legacy_data)

        self.assertIsInstance(restored.chat_id, str)
        self.assertTrue(restored.chat_id)
        self.assertNotEqual(restored.chat_id, chat.chat_id)

    def test_create_multiple_single_chats_for_same_character(self):
        """
        同一个角色可以创建多条普通对话。
        """
        character = self._character("素世")
        manager = ChatManager()

        first = manager.create_single_character_chat(character)
        second = manager.create_single_character_chat(character)

        self.assertEqual(len(manager.single_character_chats()), 2)
        self.assertNotEqual(first.chat_id, second.chat_id)
        self.assertEqual(first.get_character_name(), "素世")
        self.assertEqual(second.get_character_name(), "素世")

    def test_user_persona_snapshot_is_frozen_and_serialized(self) -> None:
        """用户人设应在创建对话时冻结，并随提示词配置完成序列化。"""
        character = self._character("素世")
        persona = CharacterAttributes.create_user(
            name="小春",
            description="喜欢音乐。",
            persona_id="persona-1",
        )

        chat = Chat.new_single_chat(character, user_character=persona)
        persona.character_name = "已修改"
        persona.character_description = "已修改的描述"

        self.assertIsInstance(chat.prompt_generator, SingleCharacterPromptGenerator)
        snapshot = chat.prompt_generator.user_persona
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.name, "小春")
        self.assertEqual(snapshot.description, "喜欢音乐。")
        self.assertEqual(snapshot.kind, "custom")

        data = chat.to_dict()
        self.assertEqual(
            data["prompt_config"]["user_persona"],
            {
                "persona_id": "persona-1",
                "name": "小春",
                "description": "喜欢音乐。",
                "kind": "custom",
                "source_character_folder_name": None,
            },
        )
        restored = Chat.from_dict(data)
        self.assertEqual(restored.prompt_generator.to_dict(), chat.prompt_generator.to_dict())

    def test_system_character_persona_snapshot_uses_effective_fields(self) -> None:
        """扮演已有角色时，快照应保存系统角色的生效信息与来源目录。"""
        character = self._character("素世")
        system_character = self._character("爱音")
        system_character.character_folder_name = "anon"
        system_character.character_description = "开朗活泼。"
        persona = CharacterAttributes.create_user(
            name="原始人设",
            description="原始描述",
            persona_id="persona-2",
        )
        persona.user_as_character = system_character

        chat = Chat.new_single_chat(character, user_character=persona)
        self.assertIsInstance(chat.prompt_generator, SingleCharacterPromptGenerator)
        snapshot = chat.prompt_generator.user_persona
        self.assertEqual(
            snapshot,
            UserPersonaSnapshot(
                persona_id="persona-2",
                name="爱音",
                description="开朗活泼。",
                kind="system_character",
                source_character_folder_name="anon",
            ),
        )

    def test_missing_or_broken_user_persona_loads_as_no_persona(self) -> None:
        """旧存档缺少快照或快照损坏时，都应按无用户人设加载。"""
        legacy_generator = SingleCharacterPromptGenerator.from_dict(
            {
                "type": "single_character",
                "character_name": "素世",
                "user_character_name": "旧字段",
            }
        )
        broken_generator = SingleCharacterPromptGenerator.from_dict(
            {
                "type": "single_character",
                "character_name": "素世",
                "user_persona": {
                    "persona_id": "",
                    "name": "小春",
                    "description": "",
                    "kind": "custom",
                },
            }
        )

        self.assertIsNone(legacy_generator.user_persona)
        self.assertIsNone(broken_generator.user_persona)
        self.assertIsNone(legacy_generator.to_dict()["user_persona"])

    def test_user_persona_changes_only_real_user_message_label(self) -> None:
        """用户人设只应替换真实用户消息标签，内部事件仍使用 User。"""
        generator = SingleCharacterPromptGenerator(
            "素世",
            UserPersonaSnapshot(
                persona_id="persona-3",
                name="小春",
                description="",
                kind="custom",
            ),
        )
        chat = Chat(
            prompt_generator=generator,
            message_list=[
                Message("User", "你好", "", EmotionEnum.HAPPINESS, ""),
                Message(
                    "User",
                    "【系统内部事件触发：定时提醒】提醒喝水。",
                    "",
                    EmotionEnum.HAPPINESS,
                    "",
                ),
            ],
        )

        with mock.patch.object(SingleCharacterPromptGenerator, "generate", return_value=""):
            query = chat.build_llm_query("素世")

        self.assertEqual(len(query), 1)
        self.assertIn("[小春]: 你好", query[0]["content"])
        self.assertIn("[User]: 【系统内部事件触发：定时提醒】", query[0]["content"])

    def test_user_persona_prompt_distinguishes_persona_kind(self) -> None:
        """提示词应区分自定义人设与扮演已有角色，并允许只有名称。"""
        character = self._character("素世")
        character.character_description = "温柔而敏锐。"
        manager = mock.Mock(character_class_list=[character])
        custom_generator = SingleCharacterPromptGenerator(
            "素世",
            UserPersonaSnapshot(
                persona_id="custom",
                name="小春",
                description="",
                kind="custom",
            ),
        )
        system_generator = SingleCharacterPromptGenerator(
            "素世",
            UserPersonaSnapshot(
                persona_id="system",
                name="爱音",
                description="开朗活泼。",
                kind="system_character",
                source_character_folder_name="anon",
            ),
        )

        with mock.patch("character.GetCharacterAttributes", return_value=manager):
            custom_prompt = custom_generator.generate("素世")
            system_prompt = system_generator.generate("素世")

        self.assertIn("名为小春的用户角色", custom_prompt)
        self.assertNotIn("[正在与你对话的小春的人物设定]", custom_prompt)
        self.assertIn("用户正在扮演动漫BangDream中的角色爱音", system_prompt)
        self.assertIn("[正在与你对话的爱音的人物设定]", system_prompt)

    def test_reorder_chats_by_type_keeps_other_type_order(self):
        """
        类型内重排不应改变其他类型对话的相对顺序。
        """
        character = self._character("素世")
        single_a = Chat.new_single_chat(character, name="single a")
        theater_a = Chat.load_from_theater_record(self.theater_dialog_history[0])
        single_b = Chat.new_single_chat(character, name="single b")
        theater_b = Chat.load_from_theater_record(self.theater_dialog_history[0])
        manager = ChatManager([single_a, theater_a, single_b, theater_b])

        manager.reorder_chats_by_type(ChatType.SINGLE_CHARACTER, [single_b.chat_id, single_a.chat_id])

        self.assertEqual(manager.chat_list, [single_b, theater_a, single_a, theater_b])

    def test_audio_path_collection_and_safe_deletion(self):
        """
        删除对话音频时，只删除没有被其他对话引用的真实音频文件。
        """
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as shared_audio:
            shared_path = shared_audio.name
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as unique_audio:
            unique_path = unique_audio.name

        try:
            chat_a = Chat(
                message_list=[
                    Message("素世", "a", "", EmotionEnum.HAPPINESS, shared_path),
                    Message("素世", "b", "", EmotionEnum.HAPPINESS, unique_path),
                    Message("素世", "c", "", EmotionEnum.HAPPINESS, "NO_AUDIO"),
                    Message("素世", "d", "", EmotionEnum.HAPPINESS, "../reference_audio/silent_audio/silence.wav"),
                ]
            )
            chat_b = Chat(
                message_list=[
                    Message("素世", "shared", "", EmotionEnum.HAPPINESS, shared_path)
                ]
            )
            manager = ChatManager([chat_a, chat_b])

            collected = manager.collect_audio_paths_for_chat(chat_a.chat_id)
            deleted = manager.delete_unreferenced_audio_files_for_chat(chat_a.chat_id)

            self.assertEqual(set(collected), {os.path.realpath(shared_path), os.path.realpath(unique_path)})
            self.assertEqual(deleted, [os.path.realpath(unique_path)])
            self.assertTrue(os.path.exists(shared_path))
            self.assertFalse(os.path.exists(unique_path))
        finally:
            if os.path.exists(shared_path):
                os.unlink(shared_path)
            if os.path.exists(unique_path):
                os.unlink(unique_path)

    def test_audio_deletion_for_deleted_messages_checks_remaining_references(self):
        """
        删除消息音频时，应同时检查当前对话剩余消息和其他对话中的引用。
        """
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as shared_audio:
            shared_path = shared_audio.name
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as current_shared_audio:
            current_shared_path = current_shared_audio.name
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as unique_audio:
            unique_path = unique_audio.name

        try:
            deleted_shared = self._message("祥子", "其他对话也引用", shared_path)
            deleted_current_shared = self._message("祥子", "当前对话仍引用", current_shared_path)
            deleted_unique = self._message("祥子", "独占音频", unique_path)
            remaining_current_shared = self._message("祥子", "保留引用", current_shared_path)
            chat_a = Chat(
                message_list=[
                    deleted_shared,
                    deleted_current_shared,
                    deleted_unique,
                    remaining_current_shared,
                ],
            )
            chat_b = Chat(message_list=[self._message("祥子", "其他引用", shared_path)])
            manager = ChatManager([chat_a, chat_b])

            result = chat_a.delete_message_range(0, 3)
            deletable_paths = manager.collect_deletable_audio_paths_from_messages(result.deleted_messages)
            audio_result = manager.delete_unreferenced_audio_files_for_messages(result.deleted_messages)

            self.assertEqual(deletable_paths, [os.path.realpath(unique_path)])
            self.assertEqual(audio_result.deleted_paths, [os.path.realpath(unique_path)])
            self.assertEqual(audio_result.failed_paths, [])
            self.assertTrue(os.path.exists(shared_path))
            self.assertTrue(os.path.exists(current_shared_path))
            self.assertFalse(os.path.exists(unique_path))
        finally:
            for path in (shared_path, current_shared_path, unique_path):
                if os.path.exists(path):
                    os.unlink(path)

    @staticmethod
    def _character(name: str) -> CharacterAttributes:
        """
        构造测试用角色对象。
        """
        character = CharacterAttributes()
        character.character_name = name
        character.character_folder_name = name
        return character

    @staticmethod
    def _message(character_name: str, text: str, audio_path: str = "") -> Message:
        """
        构造测试用消息对象。
        """
        return Message(
            character_name=character_name,
            text=text,
            translation="",
            emotion=EmotionEnum.HAPPINESS,
            audio_path=audio_path,
        )


if __name__ == '__main__':
    unittest.main()
