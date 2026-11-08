"""共同嵌套存档、旧格式转换及台词的可写兼容投影。"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
import weakref
from collections.abc import MutableSequence
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .chat import Message


class UnsupportedArchive(RuntimeError):
    """较新或不支持的共同存档不能按损坏文件覆盖。"""


def new_id() -> str:
    """创建一次分配、保存后不再重建的内容身份。"""
    return uuid.uuid4().hex


def object_dict(value: object) -> dict[str, object]:
    """读取普通记录，拒绝把 SDK 实例带入存档。"""
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def records(value: object) -> list[dict[str, object]]:
    """读取有序记录列表。"""
    return [object_dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def semantic_parts(parts: object) -> list[dict[str, object]]:
    """与 Electron 一致地摘要业务内容，忽略身份、音频和协议副本。"""
    result = []
    for part in records(parts):
        kind = part.get("kind")
        if kind == "reasoning":
            result.append({"kind": kind, "text": part.get("text", "")})
        elif kind == "tool-call":
            result.append({"kind": kind, "callId": part.get("callId"),
                           "toolName": part.get("toolName"), "arguments": part.get("arguments", {})})
        elif kind == "dialogue":
            result.append({"kind": kind, "lines": [{key: line.get(key) for key in
                ("speakerId", "formId", "text", "translation", "emotion", "performance")}
                for line in records(part.get("lines"))]})
    return result


def python_meta(chat: dict[str, object]) -> dict[str, object]:
    """取得 Python 专有运行设置与资源位置，不建立第二份消息历史。"""
    meta = cast(dict[str, object], chat.setdefault("meta", {}))
    extensions = cast(dict[str, object], meta.setdefault("extensions", {}))
    return cast(dict[str, object], extensions.setdefault("python", {}))


def legacy_turn() -> dict[str, object]:
    """创建允许缺少用户输入的小剧场或迁移轮次。"""
    return {"id": new_id(), "input": {"kind": "scenario"},
            "meta": {"requestId": None, "generation": {}, "speech": {}, "scenario": None,
                     "characters": {}, "tools": [], "worldbook": None,
                     "worldbookDiagnostics": [], "extensions": {}}, "messages": []}


def default_chat_settings() -> dict[str, object]:
    """采用 Electron 的空覆盖结构，不把当前默认值冒充历史值。"""
    return {"generation": {}, "speech": {}, "scenario": None, "tools": {"enabled": []},
            "worldbook": {"enabled": False, "rootPackageIds": [], "progress": {},
                          "directContext": False, "queryTools": False},
            "characters": {}, "presentation": {"background": None, "characters": {}}}


def legacy_archive(data: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """迁移旧存档；只重建可确定的内容，不猜测历史推理或供应商数据。"""
    identifier = str(data.get("chat_id") or new_id())
    known = {"chat_id", "name", "type", "message_list", "start_message", "prompt_config", "meta"}
    meta = copy.deepcopy(object_dict(data.get("meta")))
    tool_records = records(meta.pop("tool_call_records", []))
    warnings = ["旧记录缺少原始模型响应分组、推理及请求快照；迁移分组不代表原响应。"]
    chat: dict[str, object] = {
        "id": identifier, "revision": 0, "title": str(data.get("name") or "新对话"),
        "characterIds": [], "userPersona": None,
        "mode": "scripted-dialogue" if data.get("type") == 2 else "single-character",
        "meta": {"settings": default_chat_settings(), "reminders": [], "extensions": {}}, "turns": [],
    }
    extras = python_meta(chat)
    extras.update({"promptConfig": copy.deepcopy(data.get("prompt_config", {})),
                   "chatType": data.get("type", 1),
                   "legacyPromptConfig": copy.deepcopy(data.get("prompt_config", {})),
                   "startMessage": data.get("start_message", ""), "settings": meta,
                   "resources": {}, "characterNames": {},
                   "legacyExtensions": {key: copy.deepcopy(value) for key, value in data.items() if key not in known}})
    persona = object_dict(object_dict(data.get("prompt_config")).get("user_persona"))
    if persona:
        chat["userPersona"] = {"personaId": persona.get("persona_id") or new_id(),
                              "sourceCharacterId": persona.get("source_character_folder_name") or None,
                              "sourceFormId": None,
                              "displayName": persona.get("name", "User"),
                              "description": persona.get("description", ""), "avatar": None}
        if persona.get("source_character_folder_name"):
            warnings.append("旧对话身份保留来源角色编号；来源形态及头像未保存，保持未知。")
    archive = Timeline(chat)
    prompt = object_dict(data.get("prompt_config"))
    participants = prompt.get("character_names", [])
    if prompt.get("character_name"):
        participants = [prompt["character_name"]]
    for name in participants if isinstance(participants, list) else []:
        archive.character_id(str(name))
    for raw in data.get("message_list", []) or []:
        if not isinstance(raw, dict) or not isinstance(raw.get("text", ""), str):
            raise ValueError("旧存档含无法转换的消息结构。")
        archive.insert_legacy(len(archive.entries()), copy.deepcopy(raw), migrated=True)
    web_metadata = records(meta.get("webui_messages"))
    used_ids: set[str] = set()
    turn_ids: dict[str, str] = {}
    for index, (turn, message, entry) in enumerate(archive.entries()):
        if index >= len(web_metadata):
            break
        known = web_metadata[index]
        identifier = known.get("id")
        if isinstance(identifier, str) and identifier and identifier not in used_ids:
            entry["id"] = identifier
            used_ids.add(identifier)
            if message.get("kind") == "user":
                turn["input"] = {"kind": "message", "messageId": identifier}
        turn_id = known.get("turn_id")
        previous = str(turn["id"])
        if isinstance(turn_id, str) and turn_id and turn_id not in turn_ids.values():
            turn_ids[previous] = turn_id
    for turn in archive.turns:
        turn["id"] = turn_ids.get(str(turn["id"]), turn["id"])
    archive.invalidate_entries()
    for raw in tool_records:
        index = raw.get("message_index")
        entries = archive.entries()
        if not isinstance(index, int) or not 0 <= index < len(entries):
            warnings.append("工具记录关联位置缺失，原记录保留在迁移信息中。")
            continue
        turn = entries[index][0]
        call_id = str(raw.get("tool_call_id") or new_id())
        assistant = {"id": new_id(), "kind": "assistant", "parts": [
            {"id": new_id(), "kind": "tool-call", "callId": call_id,
             "toolName": str(raw.get("tool_name") or "unknown"),
             "arguments": copy.deepcopy(raw.get("arguments", {})), "protocol": {}}],
            "protocol": {}, "extensions": {"migration": {"reconstructed": True}}}
        messages = cast(list[dict[str, object]], turn["messages"])
        messages.append(assistant)
        text = str(raw.get("raw_result_content") or raw.get("result_content") or "")
        complete = raw.get("status") == "completed"
        status = "succeeded" if complete and raw.get("ok") is True else "failed" if complete else "interrupted"
        outcome: dict[str, object] = {"status": status}
        if status == "succeeded":
            outcome["value"] = text
        else:
            outcome["problem"] = {"code": "legacy_tool_result", "message": text, "retryable": False}
        messages.append({"id": new_id(), "kind": "tool-result", "assistantMessageId": assistant["id"],
                         "callId": call_id, "origin": "tool" if complete else "runner", "outcome": outcome,
                         "extensions": {"migration": {"possiblyTruncated": True}, "pythonDisplay": copy.deepcopy(raw)}})
    if tool_records:
        warnings.append("旧工具结果可能截断或缺少记录；已保留现存内容及不确定性。")
        extras["legacyToolRecords"] = tool_records
    summary = object_dict(meta.get("rolling_summary"))
    boundary = summary.get("summary_until_message_index")
    entries = archive.entries()
    if summary.get("text") and isinstance(boundary, int) and 0 <= boundary < len(entries):
        turn = entries[boundary][0]
        expanded = max(index for index, item in enumerate(entries) if item[0] is turn)
        summary["summary_until_message_index"] = expanded
        cast(list[dict[str, object]], turn["messages"]).append(
            {"id": new_id(), "kind": "abstract", "text": summary["text"],
             "extensions": {"migration": {"originalBoundary": boundary, "expandedThrough": expanded}}})
        if expanded != boundary:
            warnings.append(f"旧摘要覆盖由第 {boundary + 1} 条扩大至第 {expanded + 1} 条；原文仍保留。")
    extras["migration"] = {"source": "python-message-list", "warnings": warnings}
    archive.validate()
    return chat, warnings


class Timeline:
    """持有唯一嵌套历史，现有台词访问只是引用这些节点的投影。"""

    def __init__(self, data: dict[str, object]) -> None:
        """绑定一份共同 Chat 数据，不复制第二份可编辑消息列表。"""
        self.data = data
        self.lock = threading.RLock()
        self.views: dict[str, Message] = {}
        self.active_assistant_id: str | None = None
        self.summary_invalidated = False
        self.storage_owner: weakref.ReferenceType[object] | None = None
        self.detached: dict[str, dict[str, object]] = {}
        self.visible_proxy: VisibleMessages | None = None
        self._entries_cache: list[tuple[dict[str, object], dict[str, object], dict[str, object]]] | None = None
        self._entry_index: dict[str, tuple[dict[str, object], dict[str, object], dict[str, object]]] = {}
        self.validate()

    @property
    def turns(self) -> list[dict[str, object]]:
        """返回权威有序轮次。"""
        return cast(list[dict[str, object]], self.data["turns"])

    def check_editable(self) -> None:
        """保存失败后拒绝新的历史修改；不阻止重试保存本身。"""
        owner = self.storage_owner() if self.storage_owner is not None else None
        error = getattr(owner, "storage_error", None)
        if isinstance(error, str) and error:
            raise RuntimeError(error)

    def validate(self) -> None:
        """校验基本结构、稳定身份及工具关联，不修写损坏数据。"""
        if not isinstance(self.data.get("id"), str) or not isinstance(self.data.get("turns"), list):
            raise ValueError("新聊天存档缺少 id 或 turns。")
        identities: set[str] = set()
        for turn in self.turns:
            if not isinstance(turn, dict):
                raise ValueError("聊天包含无效轮次条目。")
            nodes = [turn]
            calls: dict[str, str] = {}
            results: set[str] = set()
            if not isinstance(turn.get("messages"), list):
                raise ValueError("轮次缺少 messages。")
            for message in records(turn["messages"]):
                nodes.append(message)
                if message.get("kind") not in {"user", "assistant", "tool-result", "abstract"}:
                    raise UnsupportedArchive("存档包含尚未支持的消息类型，原文件不能覆盖。")
                if message.get("kind") == "assistant":
                    if not isinstance(message.get("parts"), list):
                        raise ValueError("模型响应缺少有序 parts。")
                    if len(records(message["parts"])) != len(message["parts"]):
                        raise ValueError("模型响应包含无效内容条目。")
                    for part in records(message.get("parts")):
                        if part.get("kind") not in {"reasoning", "tool-call", "dialogue"}:
                            raise UnsupportedArchive("存档包含尚未支持的模型内容类型。")
                        if part.get("kind") == "dialogue" and not isinstance(part.get("lines"), list):
                            raise ValueError("台词批次缺少 lines。")
                        if part.get("kind") == "dialogue" and len(records(part["lines"])) != len(part["lines"]):
                            raise ValueError("台词批次包含无效台词条目。")
                        nodes.append(part)
                        nodes.extend(records(part.get("lines")))
                        if part.get("kind") == "tool-call":
                            call_id = str(part.get("callId"))
                            if call_id in calls:
                                raise ValueError("同一轮次存在重复工具调用 ID。")
                            calls[call_id] = str(message["id"])
                elif message.get("kind") == "tool-result":
                    if object_dict(message.get("outcome")).get("status") not in {"succeeded", "failed", "interrupted", "accepted"}:
                        raise UnsupportedArchive("存档包含尚未支持的工具结果状态。")
                    call_id = str(message.get("callId"))
                    if calls.get(call_id) != message.get("assistantMessageId") or call_id in results:
                        raise ValueError("工具结果缺少唯一的前置调用。")
                    results.add(call_id)
            if len(records(turn["messages"])) != len(turn["messages"]):
                raise ValueError("轮次包含无效消息条目。")
            for node in nodes:
                identifier = node.get("id")
                if not isinstance(identifier, str) or not identifier or identifier in identities:
                    raise ValueError("聊天存档存在缺失或重复的稳定身份。")
                identities.add(identifier)

    def entries(self) -> list[tuple[dict[str, object], dict[str, object], dict[str, object]]]:
        """展开用户输入与台词，隐藏推理、工具和摘要节点。"""
        if self._entries_cache is not None:
            return self._entries_cache
        entries = []
        for turn in self.turns:
            for message in records(turn.get("messages")):
                if message.get("kind") == "user":
                    entries.append((turn, message, message))
                elif message.get("kind") == "assistant":
                    for part in records(message.get("parts")):
                        if part.get("kind") == "dialogue":
                            entries.extend((turn, message, line) for line in records(part.get("lines")))
        self._entries_cache = entries
        self._entry_index = {str(entry[2]["id"]): entry for entry in entries}
        return entries

    def invalidate_entries(self) -> None:
        """只在结构变化时失效索引，字段编辑和保存不反复扫描整份历史。"""
        self._entries_cache = None
        self._entry_index = {}

    def character_id(self, name: str) -> str:
        """优先使用实际目录身份；无法解析时保留明确的缺失身份。"""
        names = cast(dict[str, object], python_meta(self.data).setdefault("characterNames", {}))
        for identifier, label in names.items():
            if label == name:
                return identifier
        identifier = "missing-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        try:
            from character import GetCharacterAttributes
            for character in GetCharacterAttributes().character_class_list:
                if character.character_name == name:
                    identifier = character.character_folder_name
                    break
        except (ImportError, AttributeError, OSError, ValueError):
            pass
        names[identifier] = name
        characters = cast(list[str], self.data.setdefault("characterIds", []))
        if identifier not in characters:
            characters.append(identifier)
        return identifier

    def asset(self, path: str) -> dict[str, object] | None:
        """把本地资源路径登记成稳定 AssetRef；数据内不嵌入图片内容。"""
        if not path or path == "NO_AUDIO":
            return None
        identifier = "python-asset-" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:32]
        resources = cast(dict[str, object], python_meta(self.data).setdefault("resources", {}))
        resources[identifier] = path
        return {"id": identifier}

    def path(self, ref: object) -> str:
        """从本机资源索引查找引用，导入缺失时保留未知而不猜路径。"""
        resources = object_dict(python_meta(self.data).get("resources"))
        return str(resources.get(str(object_dict(ref).get("id")), ""))

    def write_view(self, identifier: str, raw: dict[str, object]) -> None:
        """把台词兼容视图的修改直接写回所属权威节点。"""
        self.entries()
        selected = self._entry_index.get(identifier)
        if selected is not None:
            _, message, entry = selected
            extensions = cast(dict[str, object], entry.setdefault("extensions", {}))
            # Python 的舞台编排和文件引用保留在扩展中，不复制正文、翻译和情绪。
            extra = copy.deepcopy(object_dict(extensions.get("python")))
            for key in ("performance_timeline", "scene_before", "muted", "worldbook_snapshot"):
                if key not in raw:
                    extra.pop(key, None)
            extra.update({key: copy.deepcopy(value) for key, value in raw.items()
                          if key not in {"text", "translation", "emotion", "performance", "character_name", "attachments", "audio_path"}})
            extensions["python"] = extra
            if message.get("kind") == "user":
                attachments = []
                previous_content = object_dict(message.get("content"))
                previous_attachments = records(previous_content.get("attachments"))
                for index, attachment in enumerate(records(raw.get("attachments"))):
                    previous = copy.deepcopy(previous_attachments[index]) if index < len(previous_attachments) else {}
                    previous.update(asset=self.asset(str(attachment.get("path", ""))),
                                    name=attachment.get("original_name", ""), mediaType=attachment.get("mime_type", ""))
                    attachment_extensions = cast(dict[str, object], previous.setdefault("extensions", {}))
                    attachment_extensions["python"] = copy.deepcopy(attachment)
                    attachments.append(previous)
                content = {**previous_content, "text": raw.get("text", ""), "attachments": attachments}
                if message.get("content") and message["content"] != content:
                    self.invalidate_from(identifier)
                message["content"] = content
                return
            old_text = entry.get("text")
            names = object_dict(python_meta(self.data).get("characterNames"))
            previous_name = str(names.get(str(entry.get("speakerId")), entry.get("speakerId")) or "")
            name = str(raw.get("character_name") or "")
            if name != previous_name:
                entry["speakerId"] = self.character_id(name) if name else None
                self.invalidate_from(identifier)
            entry.update(text=raw.get("text", ""), translation=raw.get("translation") or None,
                         emotion=raw.get("emotion") or None,
                         performance=copy.deepcopy(raw.get("performance") or {"motion": None, "expression": None}))
            spoken = cast(dict[str, object], entry.setdefault("spoken", {"text": entry["text"], "language": "und", "pronunciationOverrides": []}))
            if old_text != entry["text"]:
                if not entry.get("audio"):
                    spoken["text"] = entry["text"]
                self.invalidate_from(identifier)
            audio_path = str(raw.get("audio_path") or "")
            if audio_path:
                ref = self.asset(audio_path)
                if ref:
                    extensions.pop("noAudio", None)
                    previous_audio = object_dict(entry.get("audio"))
                    same_audio = self.path(previous_audio.get("asset")) == audio_path
                    duration = previous_audio.get("durationMs", 0) if same_audio else 0
                    if not same_audio:
                        spoken["text"] = entry["text"]
                    if not duration:
                        from .portable import resolve_resource
                        source = resolve_resource(audio_path)
                        if source is not None:
                            try:
                                import soundfile
                                information = soundfile.info(str(source))
                                duration = round(1000 * information.duration)
                            except (ImportError, OSError, RuntimeError, ValueError):
                                extensions["audioDurationUnknown"] = True
                    entry["audio"] = {"asset": ref, "durationMs": duration}
                elif audio_path == "NO_AUDIO":
                    entry["audio"] = None
                    extensions["noAudio"] = True
            else:
                entry["audio"] = None
                extensions.pop("noAudio", None)
            return
        raise IndexError("台词身份已经失效。")

    def read_view(self, entry: dict[str, object], message: dict[str, object]) -> dict[str, object]:
        """投影成现有界面和语音代码所需的台词字段。"""
        extensions = object_dict(entry.get("extensions"))
        raw = copy.deepcopy(object_dict(extensions.get("python")))
        if message.get("kind") == "user":
            content = object_dict(message.get("content"))
            raw.update(character_name="User", text=content.get("text", message.get("text", "")),
                       translation="", emotion="happiness", audio_path="")
            attachments = []
            for attachment in records(content.get("attachments")):
                old = copy.deepcopy(object_dict(object_dict(attachment.get("extensions")).get("python")))
                old.update(type="image", path=self.path(attachment.get("asset")),
                           mime_type=attachment.get("mediaType", ""), original_name=attachment.get("name", ""))
                attachments.append(old)
            raw["attachments"] = attachments
        else:
            names = object_dict(python_meta(self.data).get("characterNames"))
            raw.update(character_name=str(names.get(str(entry.get("speakerId")), entry.get("speakerId")) or ""),
                       text=entry.get("text", ""), translation=entry.get("translation") or "",
                       emotion=entry.get("emotion") or "happiness", performance=copy.deepcopy(entry.get("performance")),
                       audio_path="NO_AUDIO" if extensions.get("noAudio") else self.path(object_dict(entry.get("audio")).get("asset")))
        return raw

    def insert_legacy(self, index: int, raw: dict[str, object], *, migrated: bool = False,
                      preserved: dict[str, object] | None = None,
                      assistant_message_id: str | None = None) -> str:
        """将一次输入或一条已验收台词加入嵌套历史，而非另存平面消息。"""
        entries = self.entries()
        if index < 0:
            index = max(0, len(entries) + index)
        if index < len(entries):
            turn, target, _ = entries[index]
        else:
            turn = self.turns[-1] if self.turns else legacy_turn()
            target = {}
        if raw.get("character_name") == "User":
            turn = legacy_turn()
            if target:
                self.turns.insert(self.turns.index(entries[index][0]), turn)
            else:
                self.turns.append(turn)
            source = {"kind": "manual"}
            message: dict[str, object] = {"id": new_id(), "kind": "user", "source": source,
                                         "content": {}, "extensions": {}}
            if str(raw.get("text", "")).startswith("【系统内部事件触发"):
                message["extensions"] = {"migration": {"inputSourceUnknown": True}}
            cast(list[dict[str, object]], turn["messages"]).append(message)
            turn["input"] = {"kind": "message", "messageId": message["id"]}
            self.active_assistant_id = None
            identifier = str(message["id"])
        else:
            if assistant_message_id is not None:
                turn = next((turn for turn in self.turns if any(message.get("id") == assistant_message_id
                            for message in records(turn["messages"]))), None)
                if turn is None:
                    raise ValueError("过渡台词所属的模型响应已经失效。")
            if not self.turns:
                self.turns.append(turn)
            assistant = next((item for item in records(turn["messages"])
                              if item.get("id") == (assistant_message_id or self.active_assistant_id)), None)
            target_line = entries[index][2] if index < len(entries) and target.get("kind") == "assistant" else None
            if assistant is None and target_line is not None:
                assistant = target
            if assistant is None:
                assistant = {"id": new_id(), "kind": "assistant", "parts": [], "protocol": {},
                             "extensions": {"migration": {"reconstructed": True}} if migrated else {}}
                messages = cast(list[dict[str, object]], turn["messages"])
                position = messages.index(target) if target in messages else len(messages)
                messages.insert(position, assistant)
            part = next((part for part in records(assistant["parts"])
                         if part.get("kind") == "dialogue" and
                         (target_line is None or target_line in records(part.get("lines")))), None)
            if part is None:
                part = {"id": new_id(), "kind": "dialogue", "lines": []}
                cast(list[dict[str, object]], assistant["parts"]).append(part)
            line: dict[str, object] = {"id": new_id(), "speakerId": self.character_id(str(raw["character_name"])) if raw.get("character_name") else None,
                                      "formId": None, "text": raw.get("text", ""), "translation": None,
                                      "emotion": None, "spoken": {"text": raw.get("text", ""), "language": "und", "pronunciationOverrides": []},
                                      "performance": {}, "audio": None, "extensions": {}}
            if preserved is not None:
                line = preserved
            lines = cast(list[dict[str, object]], part["lines"])
            position = lines.index(target_line) if target_line in lines else len(lines)
            lines.insert(position, line)
            identifier = str(line["id"])
        self.invalidate_entries()
        self.write_view(identifier, raw)
        if index < len(entries):
            self.invalidate_from(identifier)
        elif raw.get("character_name") != "User" and assistant.get("id") == (assistant_message_id or self.active_assistant_id):
            self.seal_response(assistant)
        return identifier

    def seal_response(self, message: dict[str, object]) -> None:
        """台词验收后固定业务摘要，后续编辑不能重新签发旧协议材料。"""
        from llm.replay import fingerprint
        protocol = object_dict(message.get("protocol"))
        if protocol and not protocol.get("invalidated"):
            protocol["projectionFingerprint"] = fingerprint(semantic_parts(message.get("parts")))

    def validate_replay(self) -> None:
        """加载时检测外部编辑，避免把旧密文与已修改的正文一起回传。"""
        from llm.replay import fingerprint
        for turn in self.turns:
            for message in records(turn["messages"]):
                protocol = object_dict(message.get("protocol"))
                expected = protocol.get("projectionFingerprint")
                if expected and expected != fingerprint(semantic_parts(message.get("parts"))):
                    protocol["invalidated"] = True

    def invalidate_from(self, identifier: str) -> None:
        """正文变更后使受影响的协议材料失效，同时保留原始诊断材料。"""
        active = False
        for turn in self.turns:
            for message in records(turn["messages"]):
                if message.get("id") == identifier:
                    active = True
                for part in records(message.get("parts")):
                    if any(line.get("id") == identifier for line in records(part.get("lines"))):
                        active = True
                if active and message.get("kind") == "assistant":
                    object_dict(message.get("protocol"))["invalidated"] = True
            if active:
                turn["messages"] = [message for message in records(turn["messages"])
                                    if message.get("kind") != "abstract"]
                self.summary_invalidated = True

    def append_response(self, result: object) -> str:
        """每个完整模型响应建立独立容器，台词验收后再关联到此响应。"""
        turn = self.turns[-1] if self.turns else legacy_turn()
        if not self.turns:
            self.turns.append(turn)
        parts: list[dict[str, object]] = []
        for block in getattr(result, "content", []):
            kind = block.get("type")
            if kind == "reasoning":
                parts.append({"id": new_id(), "kind": "reasoning", "text": block.get("text", ""),
                              "protocol": copy.deepcopy(block.get("providerOptions", {}))})
            elif kind == "tool-call":
                parts.append({"id": new_id(), "kind": "tool-call", "callId": block.get("toolCallId"),
                              "toolName": block.get("toolName"), "arguments": copy.deepcopy(block.get("input", {})),
                              "protocol": copy.deepcopy(block.get("providerOptions", {}))})
        message = {"id": new_id(), "kind": "assistant", "parts": parts,
                   "protocol": copy.deepcopy(getattr(result, "protocol", {})),
                   "extensions": {"usage": copy.deepcopy(getattr(result, "usage", {})),
                                  "diagnostics": list(getattr(result, "diagnostics", [])),
                                  "rawText": str(getattr(result, "text", ""))}}
        cast(list[dict[str, object]], turn["messages"]).append(message)
        self.seal_response(message)
        self.active_assistant_id = str(message["id"])
        return self.active_assistant_id

    def append_tool_result(self, payload: dict[str, object]) -> None:
        """立即保存所有工具结果，包括界面隐藏的世界书工具。"""
        if not self.turns:
            raise ValueError("工具结果缺少轮次。")
        turn = self.turns[-1]
        call_id = payload.get("tool_call_id")
        for message in records(turn["messages"]):
            if message.get("kind") != "assistant":
                continue
            if any(part.get("callId") == call_id for part in records(message.get("parts"))):
                value = str(payload.get("content", ""))
                outcome: dict[str, object] = {"status": "succeeded", "value": value}
                if payload.get("ok") is False:
                    outcome = {"status": "failed", "problem": {"code": "tool_failed", "message": value, "retryable": False}}
                cast(list[dict[str, object]], turn["messages"]).append(
                    {"id": new_id(), "kind": "tool-result", "assistantMessageId": message["id"],
                     "callId": call_id, "origin": "tool", "outcome": outcome,
                     "extensions": {"pythonDisplay": copy.deepcopy(payload)}})
                return
        raise ValueError("工具结果没有对应的模型调用。")

    def finish_turn(self, status: str) -> None:
        """终止时结算没有收到结果的工具调用，重启不自动恢复执行。"""
        if not self.turns:
            return
        turn = self.turns[-1]
        meta = object_dict(turn.get("meta"))
        cast(dict[str, object], meta.setdefault("extensions", {}))["pythonCompletion"] = status
        finished = {message.get("callId") for message in records(turn["messages"])
                    if message.get("kind") == "tool-result"}
        for message in list(records(turn["messages"])):
            for part in records(message.get("parts")):
                if part.get("kind") == "tool-call" and part.get("callId") not in finished:
                    cast(list[dict[str, object]], turn["messages"]).append(
                        {"id": new_id(), "kind": "tool-result", "assistantMessageId": message["id"],
                         "callId": part["callId"], "origin": "runner", "outcome": {
                             "status": "interrupted", "problem": {"code": "turn_ended", "message": "本轮已结束，工具结果未确认。", "retryable": False}},
                         "extensions": {}})

    def settle_pending(self) -> bool:
        """重启时只结算未知工具结果，不恢复执行或伪造成功。"""
        changed = False
        for turn in self.turns:
            results = {message.get("callId") for message in records(turn["messages"])
                       if message.get("kind") == "tool-result"}
            for message in list(records(turn["messages"])):
                for part in records(message.get("parts")):
                    if part.get("kind") != "tool-call" or part.get("callId") in results:
                        continue
                    cast(list[dict[str, object]], turn["messages"]).append(
                        {"id": new_id(), "kind": "tool-result", "assistantMessageId": message["id"],
                         "callId": part["callId"], "origin": "runner", "outcome": {
                             "status": "interrupted", "problem": {"code": "runtime_restarted", "message": "上次运行未保存工具结果，结果未知；不会自动重跑。", "retryable": False}},
                         "extensions": {}})
                    changed = True
        return changed

    def truncate_after(self, identifier: str) -> None:
        """按稳定句身份截断后续内部过程，不能把隐藏过程留到分叉之后。"""
        found = False
        kept_turns = []
        for turn in self.turns:
            if found:
                break
            kept_messages = []
            for message in records(turn["messages"]):
                kept_messages.append(message)
                if message.get("id") == identifier:
                    found = True
                    break
                kept_parts = []
                for part in records(message.get("parts")):
                    kept_parts.append(part)
                    lines = records(part.get("lines"))
                    for index, line in enumerate(lines):
                        if line.get("id") == identifier:
                            part["lines"] = lines[:index + 1]
                            found = True
                            break
                    if found:
                        message["parts"] = kept_parts
                        object_dict(message.get("protocol"))["invalidated"] = True
                        break
                if found:
                    break
            if found:
                remainder = records(turn["messages"])[len(kept_messages):]
                if all(message.get("kind") == "abstract" for message in remainder):
                    kept_messages.extend(remainder)
            turn["messages"] = kept_messages
            kept_turns.append(turn)
        if found:
            self.data["turns"] = kept_turns
            self.invalidate_entries()
            self.settle_pending()

    def sync_tool_display(self, raw: dict[str, object]) -> None:
        """将旧调用者的展示更新附着到权威工具记录，必要时标明历史重建。"""
        index = raw.get("message_index", -1)
        entries = self.entries()
        if not isinstance(index, int) or not 0 <= index < len(entries):
            return
        turn = entries[index][0]
        call_id = str(raw.get("tool_call_id") or "")
        if not call_id:
            return
        messages = cast(list[dict[str, object]], turn["messages"])
        assistant = next((message for message in messages if any(
            part.get("callId") == call_id for part in records(message.get("parts")))), None)
        if assistant is None:
            assistant = {"id": new_id(), "kind": "assistant", "parts": [{"id": new_id(),
                "kind": "tool-call", "callId": call_id, "toolName": raw.get("tool_name", "unknown"),
                "arguments": copy.deepcopy(raw.get("arguments", {})), "protocol": {}}],
                "protocol": {}, "extensions": {"migration": {"reconstructed": True}}}
            messages.append(assistant)
            content = str(raw.get("raw_result_content") or raw.get("result_content") or "")
            succeeded = raw.get("ok") is True
            messages.append({"id": new_id(), "kind": "tool-result", "assistantMessageId": assistant["id"],
                "callId": call_id, "origin": "runner", "outcome": {
                    "status": "succeeded" if succeeded else "interrupted",
                    **({"value": content} if succeeded else {"problem": {"code": "legacy_result", "message": content, "retryable": False}})},
                "extensions": {"migration": {"possiblyTruncated": True}}})
        result = next((message for message in messages if message.get("kind") == "tool-result"
                       and message.get("callId") == call_id), None)
        if result is not None:
            cast(dict[str, object], result.setdefault("extensions", {}))["pythonDisplay"] = copy.deepcopy(raw)

    def tool_displays(self) -> list[dict[str, object]]:
        """从工具配对生成普通工具卡片；世界书工具始终保持隐藏。"""
        output = []
        entries = self.entries()
        hidden = {"search_worldbook_lore", "search_character_memory", "query_character_relation"}
        for turn in self.turns:
            calls = {part.get("callId"): part for message in records(turn["messages"])
                     for part in records(message.get("parts")) if part.get("kind") == "tool-call"}
            for message in records(turn["messages"]):
                if message.get("kind") != "tool-result":
                    continue
                call = calls.get(message.get("callId"), {})
                if call.get("toolName") in hidden:
                    continue
                raw = copy.deepcopy(object_dict(object_dict(message.get("extensions")).get("pythonDisplay")))
                outcome = object_dict(message.get("outcome"))
                value = outcome.get("value", object_dict(outcome.get("problem")).get("message", ""))
                indices = [index for index, entry in enumerate(entries) if entry[0] is turn]
                raw.setdefault("message_index", indices[-1] if indices else -1)
                raw.update(tool_call_id=message.get("callId"), tool_name=call.get("toolName", "unknown"),
                           arguments=copy.deepcopy(call.get("arguments", {})),
                           raw_result_content=str(value), ok=outcome.get("status") == "succeeded")
                raw.setdefault("status", "completed")
                raw.setdefault("result_content", str(value)[:12000])
                if message.get("origin") == "runner" and outcome.get("status") == "interrupted":
                    raw["result_content"] = str(value)[:12000]
                output.append(raw)
        return output


class VisibleMessages(MutableSequence["Message"]):
    """兼容旧界面的台词访问，所有操作最终修改嵌套历史节点。"""

    def __init__(self, timeline: Timeline) -> None:
        """创建轻量投影，不保存台词副本列表。"""
        self.timeline = timeline

    def __len__(self) -> int:
        """返回可见用户输入和台词数量。"""
        return len(self.timeline.entries())

    def __getitem__(self, index: int | slice) -> Message | list[Message]:
        """按旧界面的位置取得绑定稳定身份的视图。"""
        from .chat import Message
        if isinstance(index, slice):
            return [self[position] for position in range(*index.indices(len(self)))]
        _, message, entry = self.timeline.entries()[index]
        identifier = str(entry["id"])
        if identifier not in self.timeline.views:
            view = Message.from_dict(self.timeline.read_view(entry, message))
            object.__setattr__(view, "_archive_binding", (self.timeline, identifier))
            self.timeline.views[identifier] = view
        return self.timeline.views[identifier]

    def __setitem__(self, index: int | slice, value: Message | list[Message]) -> None:
        """替换台词时沿用稳定身份，范围替换保留其余内部过程。"""
        self.timeline.check_editable()
        if isinstance(index, slice):
            values = list(cast("list[Message]", value))
            start, stop, step = index.indices(len(self))
            if step != 1:
                positions = list(range(start, stop, step))
                if len(positions) != len(values):
                    raise ValueError("带步长的切片赋值长度必须相同。")
                for position, message in zip(positions, values):
                    self[position] = message
                return
            previous = self[index]
            prefix = 0
            while prefix < min(len(previous), len(values)) and previous[prefix] is values[prefix]:
                prefix += 1
            suffix = 0
            while (suffix < min(len(previous), len(values)) - prefix
                   and previous[len(previous) - suffix - 1] is values[len(values) - suffix - 1]):
                suffix += 1
            del self[start + prefix:stop - suffix]
            for offset, message in enumerate(values[prefix:len(values) - suffix]):
                self.insert(start + prefix + offset, message)
            return
        _, _, entry = self.timeline.entries()[index]
        self.timeline.write_view(str(entry["id"]), cast("Message", value).as_dict())
        self.timeline.views.pop(str(entry["id"]), None)

    def __delitem__(self, index: int | slice) -> None:
        """删除选定台词，隐藏工具的调用/结果配对仍然保留。"""
        self.timeline.check_editable()
        if isinstance(index, slice):
            positions = list(range(*index.indices(len(self))))
        else:
            position = index if index >= 0 else len(self) + index
            if not 0 <= position < len(self):
                raise IndexError("台词索引越界。")
            positions = [position]
        original = self.timeline.entries()
        selected = {str(original[position][2]["id"]) for position in positions}
        complete_turns = {str(turn["id"]) for turn in self.timeline.turns if
                          any(entry[0] is turn for entry in original) and
                          all(str(entry[2]["id"]) in selected for entry in original if entry[0] is turn)}
        for position in sorted(positions, reverse=True):
            turn, message, entry = self.timeline.entries()[position]
            identifier = str(entry["id"])
            self.timeline.detached[identifier] = entry
            self.timeline.invalidate_from(identifier)
            self.timeline.views.pop(identifier, None)
            if message.get("kind") == "user":
                cast(list[dict[str, object]], turn["messages"]).remove(message)
                turn["input"] = {"kind": "scenario"}
            else:
                for part in records(message["parts"]):
                    lines = cast(list[dict[str, object]], part.get("lines", []))
                    if entry in lines:
                        lines.remove(entry)
                        if not lines:
                            cast(list[dict[str, object]], message["parts"]).remove(part)
                        break
            if not turn["messages"]:
                self.timeline.turns.remove(turn)
            self.timeline.invalidate_entries()
        self.timeline.data["turns"] = [turn for turn in self.timeline.turns if turn["id"] not in complete_turns]
        self.timeline.invalidate_entries()

    def insert(self, index: int, value: Message) -> None:
        """追加或插入既有 Message，通过绑定直接维护权威节点。"""
        self.timeline.check_editable()
        binding = getattr(value, "_archive_binding", None)
        preserved = self.timeline.detached.pop(binding[1], None) if binding and binding[0] is self.timeline else None
        if binding and preserved is None:
            from .chat import Message
            value = Message.from_dict(value.as_dict())
        identifier = self.timeline.insert_legacy(index, value.as_dict(), preserved=preserved)
        object.__setattr__(value, "_archive_binding", (self.timeline, identifier))
        self.timeline.views[identifier] = value

    def append_to_response(self, value: Message, identifier: str) -> None:
        """异步过渡台词绑定原响应，后续模型完成不改变它的归属。"""
        with self.timeline.lock:
            self.timeline.check_editable()
            line_id = self.timeline.insert_legacy(len(self), value.as_dict(), assistant_message_id=identifier)
            object.__setattr__(value, "_archive_binding", (self.timeline, line_id))
            self.timeline.views[line_id] = value

    def clear(self) -> None:
        """清空整份历史，同时移除不可见的推理和工具过程。"""
        self.timeline.check_editable()
        self.timeline.turns.clear()
        self.timeline.invalidate_entries()
        self.timeline.views.clear()
        self.timeline.active_assistant_id = None

    def __eq__(self, other: object) -> bool:
        """保留现有测试及界面的序列比较语义。"""
        return list(self) == list(other) if isinstance(other, (list, VisibleMessages)) else False
