"""在主线的嵌套时间线和 Electron 通用 ZIP 之间转换。"""
from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .archive import Timeline, object_dict, python_meta, records
from .portable import resource_kinds

if TYPE_CHECKING:
    from .chat import Chat

FORMAT = "d_sakiko.portable_chat"
MAX_BYTES = 512 * 1024 * 1024
PREFIX = "backup-resource://"
ResourceTable = dict[str, dict[str, dict[str, object]]]


def _asset_id(ref: object) -> str:
    """读取时间线资源引用中的标识。"""
    return str(object_dict(ref).get("id") or "")


def _wire_id(value: object, kind: str) -> str:
    """只从归档 URI 中读取资源身份，忽略导出机的路径。"""
    prefix = PREFIX + kind + "/"
    return value[len(prefix):] if isinstance(value, str) and value.startswith(prefix) else ""


def _map_assets(value: object, mapping: dict[str, str], models: set[str]) -> object:
    """把在不同机器上可能重名的资源 ID 映射到包内身份。"""
    if isinstance(value, dict):
        if set(value) == {"id"} and isinstance(value["id"], str):
            if value["id"] in models:
                return {"externalModel": value["id"]}
            mapped = mapping.get(value["id"])
            return {"id": mapped} if mapped else None
        return {key: _map_assets(child, mapping, models) for key, child in value.items()}
    if isinstance(value, list):
        return [_map_assets(child, mapping, models) for child in value]
    return value


def _pack_entry(chat: Chat, archive: zipfile.ZipFile, resources: ResourceTable,
                include_audio: bool, warnings: list[str]) -> dict[str, object]:
    """从唯一的时间线生成可见行及有序扩展，打包仍被引用的文件。"""
    from .attachments import resolve_attachment_path
    from .chat import _resolve_runtime_audio_path

    data = chat.to_dict()
    timeline = Timeline(data)
    extra = python_meta(data)
    paths = object_dict(extra.pop("resources", {}))
    media = object_dict(extra.pop("portableMedia", {}))
    extensions = object_dict(data["meta"]["extensions"])
    extensions.pop("portablePython", None)
    for key in ("acceptedRequests", "reminderCalls", "fileCalls"):
        extensions.pop(key, None)
    extensions["characterNames"] = {**object_dict(extensions.get("characterNames")),
                                   **object_dict(extra.get("characterNames"))}
    if data["mode"] == "scripted-dialogue":
        from character import GetCharacterAttributes
        from live2d_support.theater_cast import TheaterCast, theater_opening_names

        settings = object_dict(data["meta"]["settings"])
        scenario = object_dict(settings.get("scenario"))
        actors = object_dict(scenario.get("characters"))
        theater = chat.meta.theater
        try:
            installed = GetCharacterAttributes().character_class_list
        except OSError:
            installed = []
        roster = TheaterCast(chat, theater_opening_names(chat), installed, "screen")
        for identifier, actor in zip(roster.opening, (theater.character_0, theater.character_1)):
            if not identifier:
                continue
            if identifier not in data["characterIds"]:
                data["characterIds"].append(identifier)
            extensions["characterNames"].setdefault(identifier, roster.label(identifier))
            actors[identifier] = {**object_dict(actors.get(identifier)),
                                 "speakingStyle": actor.talk_style, "interaction": actor.interaction_details}
        settings["scenario"] = {**scenario, "situation": theater.situation or chat.start_message,
                                "characters": actors}
    mapping: dict[str, str] = {}
    models: set[str] = set()
    for turn in timeline.turns:
        scene = object_dict(object_dict(turn.get("meta")).get("replayScene"))
        for slot in records(scene.get("slots")):
            model_id = _asset_id(object_dict(slot.get("presentation")).get("model"))
            if model_id:
                models.add(model_id)
    for identifier, inferred in resource_kinds(data).items():
        info = object_dict(media.get(identifier))
        purpose = str(info.get("purpose") or inferred)
        if identifier in models or purpose in ("model", "live2d"):
            models.add(identifier)
            continue
        kind = "audio" if purpose == "audio" else "attachment"
        if kind == "audio" and not include_audio:
            continue
        path = str(paths.get(identifier) or "")
        if not path:
            continue
        source = _resolve_runtime_audio_path(path) if kind == "audio" else resolve_attachment_path(path)
        if source is None or not source.is_file():
            warnings.append(f"对话“{chat.name}”的资源 {Path(path).name} 缺失，已跳过。")
            continue
        if source.stat().st_size > MAX_BYTES:
            raise ValueError("归档资源超过 512 MiB。")
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        filename = str(info.get("filename") or source.name)
        wire = hashlib.sha256(json.dumps([identifier, digest, purpose, filename]).encode()).hexdigest()
        member = "resources/" + digest
        if member not in archive.NameToInfo:
            if sum(entry.file_size for entry in archive.infolist()) + len(content) > MAX_BYTES:
                raise ValueError("归档超过 512 MiB。")
            archive.writestr(member, content)
        resources[kind][wire] = {"zip_path": member, "sha256": digest, "size": len(content),
                                 "filename": filename, "purpose": purpose,
                                 "mediaType": info.get("mediaType") or mimetypes.guess_type(filename)[0]
                                 or "application/octet-stream"}
        mapping[identifier] = wire

    rows: list[dict[str, object]] = []
    for turn, message, node in timeline.entries():
        row = timeline.read_view(node, message)
        row.update(archive_id=node["id"], archive_turn=turn["id"])
        rows.append(row)
        if message["kind"] == "user":
            content = object_dict(message.get("content"))
            for item, attached in zip(records(row.get("attachments")), records(content.get("attachments"))):
                wire = mapping.get(_asset_id(attached.get("asset")))
                item.pop("remote_refs", None)
                item["path"] = PREFIX + "attachment/" + wire if wire else ""
                if not wire:
                    item.update(availability="unavailable", unavailable_reason="归档缺少附件")
            content.pop("text", None)
            content.pop("attachments", None)
            message.pop("text", None)
        else:
            audio = object_dict(node.get("audio"))
            wire = mapping.get(_asset_id(audio.pop("asset", None)))
            if wire:
                row["audio_path"] = PREFIX + "audio/" + wire
            elif node.get("audio"):
                row["audio_path"] = "NO_AUDIO"
            if not wire:
                node["audio"] = None
            node["archiveTextHash"] = hashlib.sha256(str(node.pop("text", "")).encode()).hexdigest()
            node.pop("translation", None)
    for turn in timeline.turns:
        meta = object_dict(turn.get("meta"))
        meta["requestId"] = None
        object_dict(meta.get("extensions")).pop("requestFingerprint", None)
    turns = data.pop("turns")
    py = {"chat_id": chat.chat_id, "name": chat.name, "type": chat.type.value,
          "prompt_config": chat.prompt_generator.to_dict(), "start_message": chat.start_message,
          "meta": chat.meta.to_dict(), "message_list": rows}
    return {"source_chat_id": chat.chat_id, "chat": py, "turns": _map_assets(turns, mapping, models),
            "electron": _map_assets(data, mapping, models)}


def write_zip(chats: list[Chat], target: Path, include_audio: bool, warnings: list[str]) -> ResourceTable:
    """以单次原子替换写出完整 ZIP，失败时保留已有备份。"""
    resources: ResourceTable = {"audio": {}, "attachment": {}}
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            entries = [_pack_entry(chat, archive, resources, include_audio, warnings) for chat in chats]
            manifest = {"format": FORMAT, "version": 1, "backup_id": uuid.uuid4().hex,
                        "exported_at": datetime.now(timezone.utc).isoformat(), "chats": entries,
                        "resources": resources, "warnings": warnings}
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            if sum(item.file_size for item in archive.infolist()) > MAX_BYTES:
                raise ValueError("归档超过 512 MiB。")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return resources


def unpack_timeline(entry: dict[str, object]) -> dict[str, object]:
    """按稳定 ID 合并可见消息与扩展，生成 Python 能直接读写的时间线。"""
    py = object_dict(entry.get("chat"))
    header = copy.deepcopy(object_dict(entry.get("electron")))
    header.update(title=py["name"], turns=copy.deepcopy(entry["turns"]))
    timeline = Timeline(header)
    for reminder in records(header["meta"].get("reminders")):
        reminder["state"] = "disabled"
    header["meta"]["lotteries"] = []
    rows = records(py.get("message_list"))
    indexed = {row.get("archive_id"): row for row in rows}
    if len(indexed) != len(rows) or set(indexed) != {node["id"] for _, _, node in timeline.entries()}:
        raise ValueError("归档的消息与轮次标识不一致。")
    extra = python_meta(header)
    names = dict(object_dict(object_dict(header["meta"]["extensions"]).get("characterNames")))
    extra.update(promptConfig=py.get("prompt_config", {}), chatType=py.get("type", 1),
                 startMessage=py.get("start_message", ""), settings=py.get("meta", {}),
                 resources={}, portableMedia={})
    for turn, message, node in timeline.entries():
        row = indexed[node["id"]]
        if turn["id"] != row.get("archive_turn"):
            raise ValueError("归档消息所属的轮次不一致。")
        if message["kind"] == "user":
            source = object_dict(message.get("source")).get("kind")
            if source == "manual" or "content" in message:
                content = object_dict(message.setdefault("content", {}))
                content["text"] = row.get("text", "")
                attachments = []
                for item in records(row.get("attachments")):
                    identifier = _wire_id(item.get("path"), "attachment")
                    attachments.append({"asset": {"id": identifier} if identifier else None,
                                        "name": item.get("original_name", ""), "mediaType": item.get("mime_type", ""),
                                        "extensions": {"python": {key: item[key] for key in
                                            ("type", "availability", "unavailable_reason") if key in item}}})
                content["attachments"] = attachments
            elif source == "reminder":
                message["text"] = row.get("text", "")
        else:
            if node.get("speakerId"):
                names[str(node["speakerId"])] = row.get("character_name", "")
            node.update(text=row["text"], translation=row.get("translation") or None)
            node.setdefault("emotion", row.get("emotion"))
            node.setdefault("performance", row.get("performance"))
            recorded_hash = node.pop("archiveTextHash", None)
            if recorded_hash and recorded_hash != hashlib.sha256(node["text"].encode()).hexdigest():
                timeline.invalidate_from(str(node["id"]))
                spoken = object_dict(node.setdefault("spoken", {}))
                spoken.update(text=node["text"], pronunciationOverrides=[])
            identifier = _wire_id(row.get("audio_path"), "audio")
            node["audio"] = {**object_dict(node.get("audio")), "asset": {"id": identifier}} if identifier else None
            if row.get("audio_path") == "NO_AUDIO":
                object_dict(node.setdefault("extensions", {}))["noAudio"] = True
        # Python 专有的编排、场景和世界书信息随同一条消息保留。
        local = object_dict(object_dict(node.setdefault("extensions", {})).setdefault("python", {}))
        for key in ("performance_timeline", "scene_before", "muted", "worldbook_snapshot"):
            if key in row:
                local[key] = row[key]
    extra["characterNames"] = names
    return header


def restore_files(data: dict[str, object], archive: zipfile.ZipFile, resources: ResourceTable,
                  chat_id: str) -> tuple[int, int, int]:
    """只从校验过的包内资源重建当前对话的本地索引。"""
    from .chat import ChatManager

    extra = python_meta(data)
    paths = cast(dict[str, str], extra["resources"])
    media = cast(dict[str, dict[str, object]], extra["portableMedia"])
    attachments = restored = reused = 0
    for identifier in resource_kinds(data):
        if identifier in resources["audio"] and identifier in resources["attachment"]:
            raise ValueError("归档资源标识重复。")
        info = resources["audio"].get(identifier) or resources["attachment"].get(identifier)
        if not info or not info.get("sha256"):
            raise ValueError("归档缺少引用的资源或校验值。")
        if identifier in resources["audio"]:
            path, new, existing, warning = ChatManager._restore_audio_resource(archive, resources["audio"], identifier)
            restored += new
            reused += existing
        else:
            path, warning = ChatManager._restore_attachment_resource(archive, resources["attachment"], identifier, chat_id)
            attachments += 1
        if not path or warning:
            raise ValueError(warning or "无法恢复归档资源。")
        paths[identifier] = path
        media[identifier] = {key: info[key] for key in ("filename", "mediaType", "purpose") if key in info}
    return attachments, restored, reused


def sanitize_legacy_paths(data: dict[str, object]) -> None:
    """旧 ZIP 的运行时索引也只允许引用包内文件。"""
    if "turns" in data:
        extra = python_meta(data)
        extra["resources"] = {key: value for key, value in object_dict(extra.get("resources")).items()
                              if _wire_id(value, "audio") or _wire_id(value, "attachment")}
        return
    for row in records(data.get("message_list")):
        if not _wire_id(row.get("audio_path"), "audio"):
            row["audio_path"] = "NO_AUDIO"
        for item in records(row.get("attachments")):
            item.pop("remote_refs", None)
            if not _wire_id(item.get("path"), "attachment"):
                item.update(path="", availability="unavailable", unavailable_reason="归档缺少附件")
