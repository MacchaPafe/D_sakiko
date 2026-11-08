"""共同存档、迁移和台词投影的一致性验证。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from chat.archive import Timeline, legacy_archive, python_meta
from chat.chat import Chat, Message, ChatManager, StaticPromptGenerator
from chat.portable import export_archive, import_archive
from emotion_enum import EmotionEnum
from llm.types import LLMResult
from runtime.conversation_storage import migrate_document


def line(text: str) -> Message:
    """创建没有语音资源的测试台词。"""
    return Message("测试角色", text, "", EmotionEnum.HAPPINESS, "NO_AUDIO")


def test_one_response_many_lines_and_hidden_tool_survive_roundtrip() -> None:
    """同响应分组、密文、隐藏工具和多句台词不会因保存重启而丢失。"""
    chat = Chat(prompt_generator=StaticPromptGenerator("角色设定"))
    chat.add_message(Message("User", "你好", "", EmotionEnum.HAPPINESS, ""))
    result = LLMResult(content=[{"type": "reasoning", "text": "", "providerOptions": {
        "openai": {"reasoningEncryptedContent": "opaque"}}}, {"type": "tool-call",
        "toolCallId": "c1", "toolName": "search_worldbook_lore", "input": {}}],
        protocol={"version": 1, "content": [], "origin": {}})
    chat.record_model_response(result)
    chat.record_tool_result({"tool_call_id": "c1", "content": "完整工具结果" * 4000, "ok": True})
    chat.record_model_response(LLMResult(text="完整输出"))
    chat.add_message(line("第一句"))
    chat.add_message(line("第二句"))
    restored = Chat.from_dict(chat.to_dict())
    assert [message.text for message in restored.message_list] == ["你好", "第一句", "第二句"]
    messages = restored.turns[0]["messages"]
    assert [message["kind"] for message in messages] == ["user", "assistant", "tool-result", "assistant"]
    assert len(messages[-1]["parts"][0]["lines"]) == 2
    assert messages[1]["parts"][0]["protocol"]["openai"]["reasoningEncryptedContent"] == "opaque"
    assert len(messages[2]["outcome"]["value"]) > 12000
    Timeline(restored.to_dict()).validate()


def test_view_edit_keeps_identity_and_invalidates_replay() -> None:
    """旧界面的字段编辑写回唯一历史，稳定身份不因投影而改变。"""
    chat = Chat()
    chat.record_model_response(LLMResult(protocol={"version": 1}, text="原文"))
    chat.add_message(line("原文"))
    identity = chat.turns[0]["messages"][0]["parts"][0]["lines"][0]["id"]
    chat.message_list[0].text = "修改"
    restored = Chat.from_dict(chat.to_dict())
    assert restored.message_list[0].text == "修改"
    assert restored.turns[0]["messages"][0]["parts"][0]["lines"][0]["id"] == identity
    assert restored.turns[0]["messages"][0]["protocol"]["invalidated"] is True


def test_migration_expands_summary_and_reports_missing_material(tmp_path: Path) -> None:
    """旧摘要扩大至轮次末尾，备份保持原字节且重复加载不再迁移。"""
    old = {"name": "旧对话", "type": 1, "prompt_config": {"type": "static", "prompt_content": ""},
           "message_list": [Message("User", "问题", "", EmotionEnum.HAPPINESS, "").as_dict(),
                            line("第一句").as_dict(), line("第二句").as_dict()],
           "meta": {"rolling_summary": {"version": 1, "text": "原摘要", "summary_until_message_index": 1}}}
    target = tmp_path / "all_conversation.json"
    original = json.dumps({"chat_list": [old]}, ensure_ascii=False)
    target.write_text(original)
    migrated, notice = migrate_document(target, json.loads(original))
    assert notice and list((tmp_path / "backup").iterdir())[0].read_text() == original
    restored = Chat.from_dict(migrated["chat_list"][0])
    assert len(restored.message_list) == 3
    assert restored.meta.extra["rolling_summary"]["summary_until_message_index"] == 2
    assert restored.turns[0]["messages"][-1]["kind"] == "abstract"
    assert migrate_document(target, migrated)[1] == ""
    report = json.loads(target.with_name(target.name + ".migration-report.json").read_text())
    assert report["status"] == "completed" and report["chats"][0]["warnings"]


def test_migration_failure_never_replaces_original(tmp_path: Path) -> None:
    """结构转换失败只生成报告，原文件和备份均可恢复。"""
    target = tmp_path / "history.json"
    original = '{"chat_list": [{"message_list": [42]}]}'
    target.write_text(original)
    with pytest.raises(RuntimeError, match="迁移失败"):
        migrate_document(target, json.loads(original))
    assert target.read_text() == original


def test_common_archive_resources_are_portable(tmp_path: Path) -> None:
    """共同交换包不依赖导出机器路径，导入后保留稳定资源引用。"""
    audio = tmp_path / "sound.wav"
    audio.write_bytes(b"test-audio")
    chat = Chat(message_list=[line("你好")])
    chat.message_list[0].audio_path = str(audio)
    output = tmp_path / "chats.dsakiko.json"
    result = export_archive([chat.to_dict()], output)
    assert result["audio"] == 1
    envelope = json.loads(output.read_text())
    assert envelope["format"] == "dsakiko" and envelope["version"] == 2
    assert str(audio) not in output.read_text()
    imported, warnings = import_archive(output, tmp_path / "imported")
    assert warnings == []
    restored = Chat.from_dict(imported[0])
    assert Path(restored.message_list[0].audio_path).read_bytes() == b"test-audio"


def test_interrupted_tool_is_not_faked_as_success() -> None:
    """未得到结果的工具在轮次结束后明确中断，不恢复执行。"""
    chat = Chat()
    chat.record_model_response(LLMResult(content=[{"type": "tool-call", "toolCallId": "c1", "toolName": "test", "input": {}}]))
    chat._timeline.finish_turn("cancelled")
    assert chat.turns[0]["messages"][-1]["outcome"]["status"] == "interrupted"
    Timeline(chat.to_dict()).validate()


def test_saved_reasoning_survives_next_turn_model_projection() -> None:
    """保存后续轮次仍按相同前缀回传原生推理和文本。"""
    from llm.adapters.responses import build_input, parse_response
    from llm.types import LLMConfig, LLMRequest
    chat = Chat(prompt_generator=StaticPromptGenerator("固定设定"))
    chat.add_message(Message("User", "你好", "", EmotionEnum.HAPPINESS, ""))
    controls = {"role": "user", "content": "<runtime_controls>固定规则</runtime_controls>"}
    messages = chat.build_llm_query("测试角色", is_simplify=True) + [controls]
    config = LLMConfig("custom", "test-model", "key", "https://example.invalid/v1", "responses")
    result = parse_response({"status": "completed", "output": [
        {"type": "reasoning", "id": "r", "summary": [], "encrypted_content": "opaque"},
        {"type": "message", "content": [{"type": "output_text", "text": '[{"text":"答复"}]'}]}]},
        LLMRequest(messages), config)
    chat.remember_runtime_controls(messages)
    chat.record_model_response(result)
    chat.add_message(line("答复"))
    restored = Chat.from_dict(chat.to_dict())
    restored.add_message(Message("User", "继续", "", EmotionEnum.HAPPINESS, ""))
    output = build_input(LLMRequest(restored.build_llm_query("测试角色", is_simplify=True)), config)
    assert any(item.get("encrypted_content") == "opaque" for item in output)
    restored.message_list[1].text = "编辑答复"
    output = build_input(LLMRequest(restored.build_llm_query("测试角色", is_simplify=True)), config)
    assert not any(item.get("type") == "reasoning" for item in output)


def test_unknown_fields_and_narrator_roundtrip() -> None:
    """读取和编排不修写未知情绪、扩展或旁白身份。"""
    raw = {"message_list": [{"character_name": "", "text": "旁白", "emotion": "unknown-emotion",
                            "future_data": {"keep": True}}]}
    data, _ = legacy_archive(raw)
    chat = Chat.from_dict(data)
    assert chat.message_list[0].character_name == ""
    chat.message_list[0].muted = True
    restored = Chat.from_dict(chat.to_dict())
    saved_line = restored.turns[0]["messages"][0]["parts"][0]["lines"][0]
    assert saved_line["speakerId"] is None
    assert saved_line["emotion"] == "unknown-emotion"
    assert saved_line["spoken"]["language"] == "und"
    assert saved_line["extensions"]["python"]["future_data"] == {"keep": True}


def test_failed_save_blocks_edits_until_retry(tmp_path: Path) -> None:
    """保存失败保留内容，重试成功后才允许继续修改历史。"""
    chat = Chat(message_list=[line("已完成")])
    manager = ChatManager([chat])
    target = tmp_path / "history.json"
    with patch("chat.chat.write_document", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            manager.save(target)
    with pytest.raises(RuntimeError, match="保存失败"):
        chat.message_list[0].text = "不能丢失旧结果"
    with pytest.raises(RuntimeError):
        chat.message_list.append(line("不应添加"))
    assert chat.message_list[0].text == "已完成"
    manager.save(target)
    chat.message_list[0].text = "重试成功后可编辑"
    assert manager.storage_error is None


def test_failed_theater_insert_restores_hidden_history() -> None:
    """编辑回滚恢复完整节点和对象，不能通过台词列表重建隐藏过程。"""
    from ui_small_theater.multi_char_message_edit import insert_saved_message, delete_saved_message
    chat = Chat()
    chat.record_model_response(LLMResult(text="结果", content=[{"type": "reasoning", "text": "保留过程"}]))
    first = line("原句")
    chat.add_message(first)
    before = chat.to_dict()
    assert not insert_saved_message(chat, first, line("新句"), False, lambda: False)
    assert chat.message_list[0] is first
    assert chat.to_dict() == before
    assert not delete_saved_message(chat, first, lambda: False)
    assert chat.message_list[0] is first
    assert chat.to_dict() == before


def test_move_keeps_line_identity_and_clear_removes_processes() -> None:
    """台词移动保留身份；清空剧本也必须清除不可见过程。"""
    chat = Chat()
    chat.record_model_response(LLMResult(text="完整结果", content=[{"type": "reasoning", "text": "过程"}]))
    first, second = line("一"), line("二")
    chat.message_list.extend([first, second])
    identities = [message.id for message in chat.message_list]
    del chat.message_list[0]
    chat.message_list.insert(1, first)
    assert [message.text for message in chat.message_list] == ["二", "一"]
    assert [message.id for message in chat.message_list] == identities[::-1]
    Timeline(chat.to_dict()).validate()
    chat.message_list.clear()
    assert chat.turns == []


@pytest.mark.parametrize("location", ["turn", "part", "line"])
def test_invalid_nested_entries_rejected(location: str) -> None:
    """异常条目不允许在转换时被静默过滤。"""
    data, _ = legacy_archive({"message_list": [line("一句").as_dict()]})
    if location == "turn":
        data["turns"].append(42)
    elif location == "part":
        data["turns"][0]["messages"][0]["parts"].append(42)
    else:
        data["turns"][0]["messages"][0]["parts"][0]["lines"].append(42)
    with pytest.raises(ValueError):
        Timeline(data)


def test_summary_includes_stored_hidden_worldbook_tool() -> None:
    """重启后摘要仍能读取隐藏世界书结果，界面隐藏不导致知识丢失。"""
    from chat.rolling_summary import build_rolling_summary_update
    chat = Chat()
    chat.add_message(Message("User", "查询", "", EmotionEnum.HAPPINESS, ""))
    chat.record_model_response(LLMResult(content=[{"type": "tool-call", "toolCallId": "c1",
        "toolName": "search_worldbook_lore", "input": {"query": "人物关系"}}]))
    chat.record_tool_result({"tool_call_id": "c1", "content": "世界书中已确认的重要关系", "ok": True})
    chat.record_model_response(LLMResult(text="结果"))
    chat.add_message(line("答复"))
    chat.add_message(Message("User", "下一轮", "", EmotionEnum.HAPPINESS, ""))
    restored = Chat.from_dict(chat.to_dict())
    assert restored.meta.tool_call_records == []
    update = build_rolling_summary_update(restored, perspective="测试角色", reserved_recent_rounds=1)
    assert update is not None
    content = json.dumps(update.messages, ensure_ascii=False)
    assert "世界书中已确认的重要关系" in content
    assert "search_worldbook_lore" in content


def test_delayed_interim_line_keeps_original_response() -> None:
    """下一次模型响应先完成，也不能改变后台过渡台词的归属。"""
    chat = Chat()
    first = LLMResult(text="查询中", tool_calls=[{"id": "c1"}], content=[
        {"type": "tool-call", "toolCallId": "c1", "toolName": "test", "input": {}}],
        protocol={"contextFingerprint": "first", "origin": {}})
    chat.record_model_response(first)
    chat.record_tool_result({"tool_call_id": "c1", "content": "结果", "ok": True})
    second = LLMResult(text="最终答复", protocol={"contextFingerprint": "second", "origin": {}})
    chat.record_model_response(second)
    chat.add_message(line("查询中"), assistant_message_id=chat.response_message_id(first))
    chat.add_message(line("最终答复"))
    messages = chat.turns[0]["messages"]
    assert messages[0]["parts"][-1]["lines"][0]["text"] == "查询中"
    assert messages[-1]["parts"][-1]["lines"][0]["text"] == "最终答复"
    Timeline(chat.to_dict()).validate()


def test_edit_display_text_keeps_existing_audio_and_spoken_text(tmp_path: Path) -> None:
    """保留 Python 编辑台词不自动重合成的行为，语音文本单独记录原内容。"""
    audio = tmp_path / "old.wav"
    audio.write_bytes(b"existing-audio")
    chat = Chat(message_list=[line("原来的语音文本")])
    chat.message_list[0].audio_path = str(audio)
    chat.message_list[0].text = "修改后的显示文本"
    restored = Chat.from_dict(chat.to_dict())
    entry = restored.turns[0]["messages"][0]["parts"][0]["lines"][0]
    assert restored.message_list[0].audio_path == str(audio)
    assert entry["spoken"]["text"] == "原来的语音文本"
    assert entry["text"] == "修改后的显示文本"
