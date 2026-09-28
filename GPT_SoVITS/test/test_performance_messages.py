"""验证演出选择在消息持久化和回复整理中不丢失。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chat.chat import Chat, ChatManager, Message
from chat.tool_calling import ToolCallingAgentRuntime
from dp_local2 import DSLocalAndVoiceGen
from performance_types import PerformanceSelection, performance_payload


def test_message_roundtrip_and_stable_history() -> None:
    """存档保留演出，LLM 历史不暴露随模型变化的资源。"""
    data = {"character_name": "灯", "text": "你好", "emotion": "like",
            "performance": {"motion": "mtn_nod01", "expression": "exp_smile01"}}
    message = Message.from_dict(data)
    assert Message.from_dict(message.as_dict()).performance == message.performance
    assert message.as_dict()["performance"] == data["performance"]
    assert "performance" not in json.loads(message.to_llm_query("assistant"))
    assert Message.from_dict({"text": "旧消息"}).performance is None


def test_visual_errors_degrade_independently() -> None:
    """损坏通道自动回退，未知字符串保留给实际模型验证。"""
    assert performance_payload({"motion": 3, "expression": "unknown"}) == {
        "motion": "auto", "expression": "unknown"}
    assert performance_payload([]) is None
    assert performance_payload({}) == {"motion": "auto", "expression": "auto"}


def test_tool_and_segment_preserve_performance() -> None:
    """工具规范化、严格回复解析和跨线程切片都保留选择。"""
    selection = {"motion": "mtn_nod01", "expression": "exp_smile01"}
    normalized = ToolCallingAgentRuntime._normalize_structured_text_payload([
        {"text": "你好", "emotion": "like", "performance": selection}])
    subject = DSLocalAndVoiceGen.__new__(DSLocalAndVoiceGen)
    subject.audio_language_choice = "中英混合"
    segments = subject._parse_model_segments_payload(normalized)
    assert segments[0]["performance"] == selection
    message = Message.from_dict(segments[0])
    assert message.performance == PerformanceSelection(**selection)
    assert subject._segment_from_message(1, message, True)["performance"] == selection
    assert subject._parse_model_segments_payload(
        '[{"text":"你好","emotion":"like","performance":false}]'
    ) == [{"text": "你好", "emotion": "like"}]


def test_backup_preserves_performance(tmp_path: Path) -> None:
    """对话备份往返不依赖资源目录，也不丢弃独立选择。"""
    message = Message.from_dict({"character_name": "灯", "text": "你好", "emotion": "like",
                                 "performance": {"motion": "nod", "expression": "smile"}})
    chat = Chat(name="演出备份", message_list=[message])
    with patch("chat.chat._reference_audio_dir", return_value=tmp_path):
        manager = ChatManager([chat])
        backup = tmp_path / "backup.zip"
        manager.export_chats_to_backup([chat.chat_id], backup)
        restored = ChatManager().import_chats_from_backup(backup).imported_chats[0]
    assert restored.message_list[0].performance == message.performance


def test_turn_controls_are_reused_after_model_switch() -> None:
    """模型切换不改 system；格式修正沿用当前轮次已经发送的协议。"""
    from live2d_support.performance_catalog import PerformanceCatalog

    subject = DSLocalAndVoiceGen.__new__(DSLocalAndVoiceGen)
    subject.audio_language_choice, subject.if_sakiko = "中英混合", False
    subject.restr = "角色边界"
    current = PerformanceCatalog(Path("model.model3.json"), "v3")
    with patch.object(subject, "_performance_catalog_for_turn", return_value=current):
        subject._turn_runtime_controls = subject._build_turn_runtime_controls()
    system = subject._build_runtime_system_instruction()
    with patch.object(subject, "_performance_catalog_for_turn", return_value=None):
        retry = subject._build_format_retry_messages("不合格的草稿", "格式错误")
        assert subject._turn_runtime_controls in retry[-1]["content"]
        assert "performance" in subject._turn_runtime_controls
        assert "performance" not in subject._build_turn_runtime_controls()
        assert system == subject._build_runtime_system_instruction()


def test_theater_playlist_and_replay_keep_speaker_selections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """两位角色的独立选择各自随台词进入播放列表和重播消息。"""
    monkeypatch.chdir(tmp_path)
    from multi_char_main import ViewerGUI
    from multi_char_live2d_module import Live2DModule

    selections = [{"motion": "nod", "expression": "smile"}, {"motion": "serious", "expression": "sad"}]
    host = SimpleNamespace(
        original_response=[{"speaker": speaker, "text": text, "performance": selection}
                           for speaker, text, selection in zip(("灯", "爱音"), ("你好", "嗯"), selections)],
        char_talk_texts_match_original_response_indices=["char_0", "char_1"],
        char_0_talk_texts=["你好"], char_1_talk_texts=["嗯"],
        char_0_audio_path_list=[], char_1_audio_path_list=[], current_char_index=[0, 1],
        character_list=[SimpleNamespace(character_name="灯"), SimpleNamespace(character_name="爱音")],
        _next_turn_uid=Mock(side_effect=["1", "2"]), _safe_get_audio_path=Mock(return_value=None),
    )
    playlist = ViewerGUI._build_playlist_queue(host)
    assert [item["character_name"] for item in playlist] == ["灯", "爱音"]
    assert [item["performance"] for item in playlist] == selections
    assert ViewerGUI._message_from_turn_dict(playlist[1]).performance.as_dict() == selections[1]
    renderer = SimpleNamespace(onFinishCallback_motion=Mock())
    models = [Mock(version="v3"), Mock(version="v3")]
    for model, selection in zip(models, selections):
        Live2DModule._try_start_emotion_motion(renderer, model, "like", "C", selection, "turn")
        assert model.apply_performance.call_args.args[0] == selection
        model.apply_performance.assert_called_once()
    assert not Live2DModule._try_start_emotion_motion(renderer, None, "like", "C", selections[0], "turn")


def test_token_preview_does_not_replace_inflight_controls() -> None:
    """界面估算换装后的下一轮 token 时，不改变正在进行的工具轮次协议。"""
    subject = DSLocalAndVoiceGen.__new__(DSLocalAndVoiceGen)
    subject.audio_language_choice = "中英混合"
    subject.current_chat_id = "chat"
    subject.chat_manager = Mock()
    subject.current_chat.build_llm_query.return_value = [{"role": "system", "content": "角色设定"}]
    subject._turn_runtime_controls = "已发送的 V3 协议"
    with (patch.object(subject, "_current_deepseek_file_service", return_value=None),
          patch.object(subject, "_rolling_summary_enabled", return_value=False),
          patch.object(subject, "_build_turn_runtime_controls", return_value="新模型 V2 协议"),
          patch.object(subject, "_prepare_runtime_messages", side_effect=lambda messages: messages),
          patch.object(subject, "_count_current_request_tokens", return_value=42)):
        assert subject.estimate_current_context_tokens("灯") == 42
    assert subject._turn_runtime_controls == "已发送的 V3 协议"
