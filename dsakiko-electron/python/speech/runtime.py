"""管理语音运行目录，只回收归属明确且已停止使用的临时文件。"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, cast


logger = logging.getLogger(__name__)
OWNER_FILE = ".owner.json"
OWNER_FORMAT = "dsakiko-speech-run-v1"


def process_exists(pid: int) -> bool:
    """查询失败时保守视为存活；Windows 不能用 os.kill(pid, 0) 探测。"""
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        # SYNCHRONIZE 仅查询退出状态，不获取终止进程的权限。
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def remove_runtime_path(path: Path) -> bool:
    """清理失败只记录日志，保留目录供下次启动重试。"""
    try:
        if path.is_symlink():
            logger.warning("拒绝清理符号链接运行目录：%s", path)
            return False
        shutil.rmtree(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        logger.warning("语音临时目录清理失败，稍后重试：%s", path, exc_info=True)
        return False


def remove_temporary_audio(output: Path) -> None:
    """只在写入者已经结束后删除未发布的音频。"""
    temporary = Path(str(output) + ".tmp.wav")
    try:
        temporary.unlink(missing_ok=True)
    except OSError:
        logger.warning("语音临时音频清理失败：%s", temporary, exc_info=True)


def remove_run_directory(path: Path) -> None:
    """最后才删除归属记录，部分清理失败后仍可在下次启动识别。"""
    if path.is_symlink():
        logger.warning("拒绝清理符号链接运行目录：%s", path)
        return
    marker = path / OWNER_FILE
    owner: Optional[bytes] = None
    try:
        if not path.exists():
            return
        owner = marker.read_bytes()
        for entry in path.iterdir():
            if entry.name == OWNER_FILE:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        path.rmdir()
    except OSError:
        logger.warning("语音运行目录清理失败，稍后重试：%s", path, exc_info=True)
        # 目录删除前若出现新文件或暂时占用，恢复归属记录以便重试。
        if owner is not None and path.is_dir() and not marker.exists():
            try:
                marker.write_bytes(owner)
            except OSError:
                logger.warning("无法恢复语音目录归属记录：%s", path, exc_info=True)


def read_owner(path: Path) -> Optional[Dict[str, object]]:
    """未知、损坏或不完整的归属记录不构成删除依据。"""
    marker = path / OWNER_FILE
    try:
        if marker.is_symlink():
            return None
        value = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None
        if value.get("format") != OWNER_FORMAT or value.get("runId") != path.name:
            return None
        for key in ("hostPid", "pythonPid"):
            pid = value.get(key)
            if type(pid) is not int or not 0 < pid <= 0x7FFFFFFF:
                return None
        group = value.get("processGroupId")
        if group is not None and (type(group) is not int or group != value["pythonPid"]):
            return None
        return cast(Dict[str, object], value)
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class RunDirectory:
    """一次 Python 服务运行独占的输出和缓存目录。"""

    path: Path

    @classmethod
    def create(cls, root: Path, host_pid: int) -> RunDirectory:
        """先回收已结束的运行，再登记本次归属，登记完成前不得创建 worker。"""
        if type(host_pid) is not int or not 0 < host_pid <= 0x7FFFFFFF:
            raise ValueError("语音宿主 PID 无效")
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        cls.collect_stale(root)
        path = root / ("run-" + uuid.uuid4().hex)
        path.mkdir()
        try:
            pid = os.getpid()
            # Electron 在 POSIX 创建独立进程组；独立调试时不能把整个 shell 组当作自己。
            group = pid if os.name != "nt" and os.getpgrp() == pid else None
            owner = {"format": OWNER_FORMAT, "runId": path.name, "hostPid": host_pid,
                     "pythonPid": pid, "processGroupId": group}
            temporary = path / (OWNER_FILE + ".tmp")
            temporary.write_text(json.dumps(owner), encoding="utf-8")
            temporary.replace(path / OWNER_FILE)
        except BaseException:
            remove_runtime_path(path)
            raise
        return cls(path)

    @staticmethod
    def collect_stale(root: Path) -> None:
        """只有宿主、Python 和可用的进程组都已消失时才回收。"""
        try:
            paths = list(root.iterdir())
        except OSError:
            logger.warning("无法扫描语音残留目录：%s", root, exc_info=True)
            return
        for path in paths:
            if not re.fullmatch(r"run-[0-9a-f]{32}", path.name) or path.is_symlink() or not path.is_dir():
                continue
            owner = read_owner(path)
            if owner is None:
                logger.warning("保留缺少有效归属记录的语音目录：%s", path)
                continue
            if process_exists(cast(int, owner["hostPid"])) or process_exists(cast(int, owner["pythonPid"])):
                continue
            group = owner.get("processGroupId")
            if os.name != "nt" and group is not None and process_exists(-cast(int, group)):
                continue
            remove_run_directory(path)

    def cleanup(self) -> None:
        """调用方确认所有 worker 和文件交接结束后，回收本次独占目录。"""
        remove_run_directory(self.path)
