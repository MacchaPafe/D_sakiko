import json
import os
import unittest
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from chat.chat import Chat, ChatManager
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
        self.assertEqual(user_message.text, "你好呀，今天过的怎么样？（本句话你的回答请务必用日语，并且请将额外的中文翻译内容放到“[翻译]”这一标记格式之后，并以“[翻译结束]”作为翻译的结束标志，每三句话翻译一次！请务必严格遵守这一格式回答！！）")
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


if __name__ == '__main__':
    unittest.main()
