from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from collections.abc import Callable, Mapping

from filelock import FileLock


class CorruptConversation(ValueError):
    """存档不是可合并的会话列表。"""


def conversation_lock(target: Path) -> FileLock:
    """为读取、合并、迁移和启动登记提供同一把短期锁。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(target) + ".lock", timeout=5)


def read_document(target: Path) -> dict[str, object]:
    """读取并验证存档外层结构，缺失文件允许首次创建。"""
    if not target.exists():
        return {"chat_list": []}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise CorruptConversation("聊天存档内容损坏") from exc
    if not isinstance(data, dict) or not isinstance(data.get("chat_list"), list):
        raise CorruptConversation("聊天存档缺少会话列表")
    if any(not isinstance(chat, dict) for chat in data["chat_list"]):
        raise CorruptConversation("聊天存档包含无效会话")
    return data


def backup_corrupt(target: Path) -> Path:
    """独占备份损坏文件，写入失败时不允许继续覆盖原文件。"""
    backup_dir = target.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{target.name}.corrupt-{uuid.uuid4().hex}.bak"
    with target.open("rb") as source, backup.open("xb") as destination:
        shutil.copyfileobj(source, destination)
        destination.flush()
        os.fsync(destination.fileno())
    return backup


def write_document(target: Path, data: Mapping[str, object]) -> None:
    """原子替换存档；调用者必须持有存档锁。"""
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=4)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_document(target: Path, data: dict[str, object]) -> tuple[dict[str, object], str]:
    """锁内备份并一次迁移旧消息列表；任何错误都不覆盖原文件。"""
    from chat.archive import Timeline, legacy_archive
    raw_chats = data.get("chat_list", [])
    if not isinstance(raw_chats, list):
        raise ValueError("聊天存档缺少 chat_list。")
    version = data.get("conversation_schema_version", 0)
    if not isinstance(version, int) or version > 1:
        raise RuntimeError("聊天存档来自较新版本，不能覆盖。")
    if all(isinstance(chat, dict) and "turns" in chat for chat in raw_chats):
        for chat in raw_chats:
            Timeline(chat).validate()
        return data, ""
    backup_dir = target.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / (target.name + ".before-timeline-" + uuid.uuid4().hex + ".bak")
    if target.exists():
        with target.open("rb") as source, backup.open("xb") as destination:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
    report_path = target.with_name(target.name + ".migration-report.json")
    report: dict[str, object] = {"status": "failed", "backup": str(backup), "chats": []}
    try:
        converted = []
        for original in raw_chats:
            if not isinstance(original, dict):
                raise ValueError("聊天列表包含无效条目。")
            if "turns" in original:
                Timeline(original).validate()
                chat, warnings = original, []
            else:
                chat, warnings = legacy_archive(original)
            converted.append(chat)
            report["chats"].append({"chatId": chat["id"], "title": chat.get("title"), "warnings": warnings})
        migrated = {**data, "conversation_schema_version": 1, "chat_list": converted}
        report["status"] = "completed"
        write_document(report_path, report)
        write_document(target, migrated)
        return migrated, f"聊天存档已迁移，原文件备份：{backup}；迁移报告：{report_path}"
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        write_document(report_path, report)
        raise RuntimeError(f"聊天存档迁移失败，原文件已保留。报告：{report_path}") from exc


def save_before_terminal_close(save: Callable[[], None]) -> None:
    """终端正常关闭前重试保存，只有明确放弃才忽略写入失败。"""
    while True:
        try:
            save()
            return
        except Exception as exc:
            print(f"聊天记录保存失败，内存记录尚未保存：{exc}", flush=True)
            answer = input("输入 r 重试；输入 discard 放弃未保存内容并退出：").strip().lower()
            if answer == "discard":
                return
