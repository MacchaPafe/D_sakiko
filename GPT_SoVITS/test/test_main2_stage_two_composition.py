from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class Main2StageTwoCompositionTestCase(unittest.TestCase):
    """验证单角色生产入口不再装配阶段一 dispatcher 拓扑。"""

    def test_main2_uses_controller_composition_root(self) -> None:
        """生产入口应显式装配 controller/runner，且不启动旧永久循环。"""
        main2_path = Path(__file__).resolve().parents[1] / "main2.py"
        source = main2_path.read_text(encoding="utf-8")

        self.assertIn("ChatFlowController", source)
        self.assertIn("ChatGenerationSession", source)
        self.assertIn("ChatAudioSynthesizer", source)
        self.assertIn("AudioScheduler", source)
        self.assertIn("create_live2d_client_process", source)
        self.assertIn("ToolCallingAgentRuntime", source)
        self.assertIn("register_contextual_lottery_tool(tool_registry)", source)
        self.assertIn("register_contextual_live2d_tools(tool_registry)", source)
        self.assertIn("ToolSideEffectPorts", source)
        self.assertIn("export_document=qt_win", source)
        self.assertIn("flow_controller.attach_audio_scheduler(audio_scheduler)", source)
        self.assertIn("flow_controller.shutdown(", source)
        self.assertIn("audio_worker=audio_gen", source)
        self.assertIn("foreground_resolver=lambda chat_id: chat_id == flow_controller.visible_chat_id", source)
        self.assertNotIn("MainResponseDispatcher", source)
        self.assertNotIn("main_response_dispatcher", source)
        self.assertNotIn("def main_thread", source)
        self.assertNotIn("target=main_thread", source)
        self.assertNotIn("target=dp_chat.text_generator", source)
        self.assertNotIn("handle_model_response_payload", source)
        self.assertNotIn("DispatcherInputEvent", source)
        self.assertNotIn("DispatcherInputEventType", source)
        self.assertNotIn("dp2qt_queue", source)
        self.assertNotIn("qt2dp_queue", source)
        self.assertNotIn("QT_message_queue", source)
        self.assertNotRegex(source, r"(^|\n)\s*emotion_queue\s*=")
        self.assertNotRegex(source, r"(^|\n)\s*audio_file_path_queue\s*=")
        self.assertNotRegex(source, r"(^|\n)\s*live2d_text_queue\s*=")
        self.assertNotRegex(source, r"(^|\n)\s*text_queue\s*=")
        self.assertNotIn("is_audio_play_complete", source)
        self.assertNotIn("is_text_generating_queue", source)


if __name__ == "__main__":
    unittest.main()
