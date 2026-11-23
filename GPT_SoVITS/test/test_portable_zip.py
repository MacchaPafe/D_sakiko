"""验证通用 ZIP 和两代 Python 旧归档在主线时间线上的行为。"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chat.chat import (
    Chat, ChatManager, ChatType, EmotionEnum, Message, SmallTheaterPromptGenerator, _resolve_runtime_audio_path,
)
from chat.portable_zip import FORMAT


def test_theater_zip_preserves_master_scene_and_stable_ids(tmp_path: Path) -> None:
    """场景、静音和角色设定随轮次导出，关掉录音不会影响编排。"""
    audio = tmp_path / "声.wav"
    audio.write_bytes(b"recording")
    line = Message.from_dict({"character_name": "祥子", "text": "同一句", "audio_path": str(audio),
                              "muted": True, "scene_before": {"visible_characters": ["祥子"]}})
    chat = Chat("小剧场", SmallTheaterPromptGenerator(["祥子", "素世"]), ChatType.SMALL_THEATER,
                message_list=[line, Message.from_dict(line.as_dict())])
    chat.meta.theater.situation = "排练室"
    chat.meta.theater.character_0.talk_style = "低声"
    original = [one.id for one in chat.message_list]
    path = tmp_path / "剧本.dsakiko-chat.zip"
    manager = ChatManager([chat])
    manager.export_chats_to_backup([chat.chat_id], path, include_audio=False)
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["format"] == FORMAT and manifest["resources"]["audio"] == {}
    entry = manifest["chats"][0]
    assert entry["electron"]["meta"]["settings"]["scenario"]["situation"] == "排练室"
    assert original == [row["archive_id"] for row in entry["chat"]["message_list"]]
    assert len(set(original)) == 2 and str(audio) not in json.dumps(manifest, ensure_ascii=False)
    restored = manager.import_chats_from_backup(path).imported_chats[0]
    assert restored.chat_id != chat.chat_id and restored.meta.theater.character_0.talk_style == "低声"
    assert [one.id for one in restored.message_list] == original
    assert all(one.muted and one.scene_before == line.scene_before for one in restored.message_list)


def test_theater_zip_uses_saved_opening_ids_and_round_trips_later_cast(tmp_path: Path) -> None:
    audio = tmp_path / "原录音.wav"
    audio.write_bytes(b"recording")
    timeline = {"characters": {"guest": {"motions": [{"at_ms": 100}], "expressions": []}}}
    opening = Message.from_dict({"character_name": "甲旧名", "speaker_id": "left", "text": "第一句"})
    later = Message.from_dict({"character_name": "丙", "speaker_id": "guest", "text": "换人后",
                              "scene_before": {"cast": ["guest", "right"]}, "muted": True,
                              "performance": {"motion": "nod", "expression": "smile"},
                              "performance_timeline": timeline, "audio_path": str(audio)})
    roles = [SimpleNamespace(character_folder_name=identifier, character_name=name)
             for identifier, name in [("left", "甲新名"), ("right", "乙"), ("guest", "丙")]]
    with patch("character.GetCharacterAttributes", return_value=SimpleNamespace(character_class_list=roles)):
        chat = Chat("多角色剧本", SmallTheaterPromptGenerator(["甲旧名", "乙"]), ChatType.SMALL_THEATER,
                    meta={"theater": {"character_0": {"character_id": "left", "talk_style": "低声",
                                                    "interaction_details": "先问好"},
                                      "character_1": {"character_id": "right", "talk_style": "急促",
                                                    "interaction_details": "回应"},
                                      "extra_character_ids": ["guest"]}}, message_list=[opening, later])
        path = tmp_path / "多角色剧本.zip"
        manager = ChatManager([chat])
        manager.export_chats_to_backup([chat.chat_id], path, include_audio=True)
    with zipfile.ZipFile(path) as archive:
        entry = json.loads(archive.read("manifest.json"))["chats"][0]["electron"]
    assert entry["characterIds"][0].startswith("missing-")  # 旧提示词中的显示名先被登记
    assert entry["meta"]["settings"]["scenario"]["characters"] == {
        "left": {"speakingStyle": "低声", "interaction": "先问好"},
        "right": {"speakingStyle": "急促", "interaction": "回应"},
    }
    with patch("chat.chat._reference_audio_dir", return_value=tmp_path / "restored-audio"):
        restored = manager.import_chats_from_backup(path).imported_chats[0]
        restored_audio = _resolve_runtime_audio_path(restored.message_list[1].audio_path)
    assert restored.meta.theater.to_dict() == chat.meta.theater.to_dict()
    assert [message.speaker_id for message in restored.message_list] == ["left", "guest"]
    restored_later = restored.message_list[1]
    assert restored_later.scene_before == later.scene_before
    assert restored_later.muted is True
    assert restored_later.performance == later.performance
    assert restored_later.performance_timeline == timeline
    assert restored_audio is not None and restored_audio.read_bytes() == audio.read_bytes()


@pytest.mark.parametrize("nested", [False, True])
def test_reads_historical_python_zips(tmp_path: Path, nested: bool) -> None:
    """兼容旧版平面 ZIP 和 master 之前写出的嵌套 ZIP。"""
    path = tmp_path / "旧档.zip"
    row = Message("角色", "旧回复", "", EmotionEnum.HAPPINESS, "NO_AUDIO")
    chat = Chat(name="旧会话", message_list=[row])
    payload = chat.to_dict() if nested else {"name": "旧会话", "type": 1, "message_list": [row.as_dict()]}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": "d_sakiko.chat_backup", "version": 1,
                                                      "chats": [{"chat": payload}]}))
    imported = ChatManager().import_chats_from_backup(path).imported_chats[0]
    assert [one.text for one in imported.message_list] == ["旧回复"]
    assert imported.chat_id != chat.chat_id


def test_damaged_archive_does_not_add_chat(tmp_path: Path) -> None:
    """资源校验失败时对话列表不会出现半条记录。"""
    audio = tmp_path / "录音.wav"
    audio.write_bytes(b"recording")
    chat = Chat(message_list=[Message("角色", "台词", "", EmotionEnum.HAPPINESS, str(audio))])
    manager = ChatManager([chat])
    original = tmp_path / "正常.zip"
    damaged = tmp_path / "损坏.zip"
    manager.export_chats_to_backup([chat.chat_id], original)
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(damaged, "w") as target:
        for member in source.namelist():
            target.writestr(member, source.read(member) if member == "manifest.json" else b"corrupt")
    with patch("chat.chat._reference_audio_dir", return_value=tmp_path), pytest.raises(ValueError, match="校验"):
        manager.import_chats_from_backup(damaged)
    assert manager.chat_list == [chat]
