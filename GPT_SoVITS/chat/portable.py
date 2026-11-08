"""与 Electron 共用的 dsakiko v2 交换包及本机资源定位。"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import mimetypes
import shutil
import uuid
from pathlib import Path
from .archive import Timeline, object_dict, python_meta, records


def resource_kinds(chat: dict[str, object]) -> dict[str, str]:
    """收集实际引用的资源，不导出已经删除或失效的索引残留。"""
    result: dict[str, str] = {}
    for turn in records(chat.get("turns")):
        for message in records(turn.get("messages")):
            for attachment in records(object_dict(message.get("content")).get("attachments")):
                identifier = object_dict(attachment.get("asset")).get("id")
                if isinstance(identifier, str):
                    result[identifier] = "chat-image"
            for part in records(message.get("parts")):
                for line in records(part.get("lines")):
                    identifier = object_dict(object_dict(line.get("audio")).get("asset")).get("id")
                    if isinstance(identifier, str):
                        result[identifier] = "audio"
    def visit(value: object) -> None:
        """与 Electron 一致识别只含 id 的 AssetRef，保留头像等附加资源。"""
        if isinstance(value, dict):
            if set(value) == {"id"} and isinstance(value["id"], str):
                result.setdefault(value["id"], "attachment")
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(chat)
    return result


def resolve_resource(path: str) -> Path | None:
    """按 Python 运行目录与项目目录解析现存资源。"""
    if not path:
        return None
    source = Path(path.split("[", 1)[0])
    root = Path(__file__).resolve().parents[2]
    candidates = [source] if source.is_absolute() else [Path.cwd() / source, root / "GPT_SoVITS" / source, root / source]
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


def export_archive(chats: list[dict[str, object]], target: Path, *, include_audio: bool = True) -> dict[str, object]:
    """生成共同交换包；资源内联，不输出本机路径或厂商文件引用。"""
    from runtime.conversation_storage import write_document
    resources: dict[str, dict[str, object]] = {}
    warnings: list[str] = []
    exported = copy.deepcopy(chats)
    counts = {"audio": 0, "chat-image": 0}
    for chat in exported:
        paths = object_dict(python_meta(chat).get("resources"))
        for identifier, purpose in resource_kinds(chat).items():
            if purpose == "audio" and not include_audio:
                continue
            source = resolve_resource(str(paths.get(identifier, "")))
            if source is None:
                warnings.append(f"对话 {chat.get('title', '')} 缺少资源 {identifier}。")
                continue
            data = source.read_bytes()
            if identifier not in resources:
                resources[identifier] = {"info": {"asset": {"id": identifier}, "kind": "file",
                    "purpose": purpose, "name": source.name,
                    "mediaType": mimetypes.guess_type(source.name)[0] or "application/octet-stream",
                    "sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
                    "bytes": base64.b64encode(data).decode("ascii")}
                counts["audio" if purpose == "audio" else "chat-image"] += 1
        for turn in records(chat.get("turns")):
            for message in records(turn.get("messages")):
                for attachment in records(object_dict(message.get("content")).get("attachments")):
                    extensions = object_dict(attachment.get("extensions"))
                    extensions.pop("python", None)
                    if not extensions:
                        attachment.pop("extensions", None)
                for part in records(message.get("parts")):
                    for line in records(part.get("lines")):
                        if not include_audio:
                            line["audio"] = None
        python_meta(chat)["resources"] = {}
    envelope = {"format": "dsakiko", "version": 2, "chats": exported,
                "resources": list(resources.values()), "warnings": warnings}
    write_document(target, envelope)
    return {"warnings": warnings, "audio": counts["audio"], "attachments": counts["chat-image"]}


def import_archive(target: Path, resource_root: Path) -> tuple[list[dict[str, object]], list[str]]:
    """验证共同交换包，资源只写入本次导入拥有的目录。"""
    envelope = object_dict(json.loads(target.read_text(encoding="utf-8")))
    if envelope.get("format") != "dsakiko" or envelope.get("version") != 2 or not isinstance(envelope.get("chats"), list):
        raise ValueError("不支持的共同对话交换包。")
    chats = copy.deepcopy(records(envelope["chats"]))
    if len(chats) != len(envelope["chats"]):
        raise ValueError("交换包包含无效对话条目。")
    raw_resources = envelope.get("resources", [])
    if not isinstance(raw_resources, list) or len(records(raw_resources)) != len(raw_resources):
        raise ValueError("交换包包含无效资源条目。")
    for chat in chats:
        Timeline(chat).validate()
    directory = resource_root / uuid.uuid4().hex
    paths: dict[str, str] = {}
    warnings = [str(item) for item in envelope.get("warnings", []) or []]
    try:
        directory.mkdir(parents=True, exist_ok=False)
        for resource in records(envelope.get("resources")):
            info = object_dict(resource.get("info"))
            identifier = str(object_dict(info.get("asset")).get("id") or "")
            if not identifier or identifier in paths or info.get("kind") != "file":
                raise ValueError("交换包包含无效或重复资源身份。")
            if info.get("purpose") not in {"chat-image", "avatar", "attachment", "workspace", "audio", "model", "live2d"}:
                raise ValueError("交换包包含不支持的资源用途。")
            data = base64.b64decode(str(resource.get("bytes", "")), validate=True)
            if info.get("sha256") and hashlib.sha256(data).hexdigest() != info["sha256"]:
                raise ValueError("交换包资源摘要不匹配。")
            name = Path(str(info.get("name") or "resource")).name
            path = directory / (uuid.uuid4().hex + "-" + name)
            path.write_bytes(data)
            paths[identifier] = str(path)
        for chat in chats:
            chat["id"] = uuid.uuid4().hex
            chat["revision"] = 0
            python_meta(chat)["resources"] = dict(paths)
            for identifier in resource_kinds(chat):
                if identifier not in paths:
                    warnings.append(f"导入对话 {chat.get('title', '')} 缺少资源 {identifier}。")
        return chats, warnings
    except Exception:
        shutil.rmtree(directory)
        raise
