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
