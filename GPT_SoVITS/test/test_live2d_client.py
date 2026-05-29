from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from chat_flow.live2d_client import Live2DClient


class FakeQueue:
    """记录测试中写入队列的载荷。"""

    def __init__(self) -> None:
        """初始化空的载荷列表。"""
        self.items: list[object] = []

    def put(self, item: object) -> None:
        """记录一次队列写入。"""
        self.items.append(item)

    def get(self) -> object:
        """读取最早写入的队列载荷。"""
        return self.items.pop(0)

    def empty(self) -> bool:
        """返回队列是否为空。"""
        return len(self.items) == 0


class FakeSharedBool:
    """模拟 multiprocessing.Value 暴露的布尔 value 字段。"""

    def __init__(self, value: bool) -> None:
        """保存初始布尔值。"""
        self.value = value


class Live2DClientTestCase(unittest.TestCase):
    """验证 Live2D 客户端对现有队列协议的包装。"""

    def test_play_segment_writes_display_text_audio_and_emotion_when_ready(self) -> None:
        """播放片段时应按现有协议写入文本、音频与情绪队列。"""
        text_queue = FakeQueue()
        audio_queue = FakeQueue()
        emotion_queue = FakeQueue()
        client = Live2DClient(
            live2d_text_queue=text_queue,
            audio_file_path_queue=audio_queue,
            emotion_queue=emotion_queue,
            motion_complete_value=FakeSharedBool(True),
        )

        played = client.play_segment(
            audio_path="/tmp/voice.wav",
            emotion="LABEL_0",
            display_text="你好",
        )

        self.assertTrue(played)
        self.assertEqual(text_queue.items, ["你好"])
        self.assertEqual(audio_queue.items, ["/tmp/voice.wav"])
        self.assertEqual(emotion_queue.items, ["LABEL_0"])

    def test_set_text_writes_live2d_display_text_queue(self) -> None:
        """设置文本时应只写入 Live2D 文本队列。"""
        text_queue = FakeQueue()
        audio_queue = FakeQueue()
        emotion_queue = FakeQueue()
        client = Live2DClient(
            live2d_text_queue=text_queue,
            audio_file_path_queue=audio_queue,
            emotion_queue=emotion_queue,
            motion_complete_value=FakeSharedBool(True),
        )

        client.set_text("新文本")

        self.assertEqual(text_queue.items, ["新文本"])
        self.assertEqual(audio_queue.items, [])
        self.assertEqual(emotion_queue.items, [])

    def test_switch_model_emits_existing_live2d_switch_payload(self) -> None:
        """切换模型时应发送现有 Live2D 结构化切换命令。"""
        change_queue = FakeQueue()
        client = Live2DClient(
            live2d_text_queue=FakeQueue(),
            audio_file_path_queue=FakeQueue(),
            emotion_queue=FakeQueue(),
            motion_complete_value=FakeSharedBool(True),
            change_char_queue=change_queue,
        )

        client.switch_model("祥子", "../live2d_related/sakiko/live2D_model/1.model.json")

        self.assertEqual(
            change_queue.items,
            [
                {
                    "type": "switch_live2d",
                    "character_name": "祥子",
                    "model_json": "../live2d_related/sakiko/live2D_model/1.model.json",
                }
            ],
        )

    def test_switch_fps_emits_existing_live2d_fps_payload(self) -> None:
        """切换帧率时应发送现有 Live2D 帧率命令。"""
        change_queue = FakeQueue()
        client = Live2DClient(
            live2d_text_queue=FakeQueue(),
            audio_file_path_queue=FakeQueue(),
            emotion_queue=FakeQueue(),
            motion_complete_value=FakeSharedBool(True),
            change_char_queue=change_queue,
        )

        sent = client.switch_fps(120)

        self.assertTrue(sent)
        self.assertEqual(change_queue.items, [{"type": "switch_l2d_fps", "fps": 120}])

    def test_change_background_emits_existing_payload(self) -> None:
        """切换背景时应发送现有 Live2D 背景命令。"""
        change_queue = FakeQueue()
        client = Live2DClient(
            live2d_text_queue=FakeQueue(),
            audio_file_path_queue=FakeQueue(),
            emotion_queue=FakeQueue(),
            motion_complete_value=FakeSharedBool(True),
            change_char_queue=change_queue,
        )

        sent = client.change_background()

        self.assertTrue(sent)
        self.assertEqual(change_queue.items, [{"type": "change_l2d_background"}])

    def test_toggle_text_display_updates_shared_value(self) -> None:
        """切换文本显示时应只更新共享显示开关。"""
        display_value = FakeSharedBool(True)
        client = Live2DClient(
            live2d_text_queue=FakeQueue(),
            audio_file_path_queue=FakeQueue(),
            emotion_queue=FakeQueue(),
            motion_complete_value=FakeSharedBool(True),
            text_display_value=display_value,
        )

        self.assertFalse(client.toggle_text_display())
        self.assertFalse(client.is_text_display_enabled())
        self.assertTrue(client.toggle_text_display())
        self.assertTrue(client.is_text_display_enabled())

    def test_sakiko_state_and_mask_use_existing_convert_queue(self) -> None:
        """黑白祥状态和面具切换应写入角色转换队列。"""
        convert_queue = FakeQueue()
        client = Live2DClient(
            live2d_text_queue=FakeQueue(),
            audio_file_path_queue=FakeQueue(),
            emotion_queue=FakeQueue(),
            motion_complete_value=FakeSharedBool(True),
            char_is_converted_queue=convert_queue,
        )

        self.assertTrue(client.switch_sakiko_state(False))
        self.assertTrue(client.toggle_sakiko_mask())
        self.assertEqual(convert_queue.items, [False, "maskoff"])

    def test_cancel_turn_playback_emits_existing_cancel_payload(self) -> None:
        """取消轮次播放时应发送现有 Live2D 取消命令。"""
        change_queue = FakeQueue()
        client = Live2DClient(
            live2d_text_queue=FakeQueue(),
            audio_file_path_queue=FakeQueue(),
            emotion_queue=FakeQueue(),
            motion_complete_value=FakeSharedBool(True),
            change_char_queue=change_queue,
        )

        sent = client.cancel_turn_playback(chat_id="chat-1", turn_id="turn-1")

        self.assertTrue(sent)
        self.assertEqual(
            change_queue.items,
            [{"type": "cancel_turn", "chat_id": "chat-1", "turn_id": "turn-1"}],
        )

    def test_stop_playback_emits_existing_stop_talking_payload(self) -> None:
        """停止播放时应发送现有停止说话命令。"""
        change_queue = FakeQueue()
        client = Live2DClient(
            live2d_text_queue=FakeQueue(),
            audio_file_path_queue=FakeQueue(),
            emotion_queue=FakeQueue(),
            motion_complete_value=FakeSharedBool(True),
            change_char_queue=change_queue,
        )

        sent = client.stop_playback()

        self.assertTrue(sent)
        self.assertEqual(change_queue.items, [{"type": "stop_talking"}])

    def test_start_and_stop_thinking_update_existing_thinking_queue(self) -> None:
        """开始和停止思考时应维护现有思考标记队列。"""
        thinking_queue = FakeQueue()
        client = Live2DClient(
            live2d_text_queue=FakeQueue(),
            audio_file_path_queue=FakeQueue(),
            emotion_queue=FakeQueue(),
            motion_complete_value=FakeSharedBool(True),
            thinking_queue=thinking_queue,
        )

        self.assertTrue(client.start_thinking())
        self.assertEqual(thinking_queue.items, ["no_complete"])

        self.assertTrue(client.stop_thinking())
        self.assertEqual(thinking_queue.items, [])

    def test_shutdown_emits_existing_exit_and_bye_signals(self) -> None:
        """关闭时应发送现有 Live2D 退出和再见信号。"""
        change_queue = FakeQueue()
        emotion_queue = FakeQueue()
        client = Live2DClient(
            live2d_text_queue=FakeQueue(),
            audio_file_path_queue=FakeQueue(),
            emotion_queue=emotion_queue,
            motion_complete_value=FakeSharedBool(True),
            change_char_queue=change_queue,
        )

        client.shutdown()

        self.assertEqual(change_queue.items, ["exit"])
        self.assertEqual(emotion_queue.items, ["bye"])

    def test_is_playback_complete_reads_shared_motion_value(self) -> None:
        """查询播放完成状态时应读取现有共享 motion 值。"""
        shared_value = FakeSharedBool(False)
        client = Live2DClient(
            live2d_text_queue=FakeQueue(),
            audio_file_path_queue=FakeQueue(),
            emotion_queue=FakeQueue(),
            motion_complete_value=shared_value,
        )

        self.assertFalse(client.is_playback_complete())

        shared_value.value = True
        self.assertTrue(client.is_playback_complete())


if __name__ == "__main__":
    unittest.main()
