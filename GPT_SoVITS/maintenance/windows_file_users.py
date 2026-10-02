from __future__ import annotations

import os
import threading
from pathlib import Path


def query_file_users(path: Path) -> list[dict[str, object]]:
    """使用 Restart Manager 只查询资源使用者，不关闭任何程序或服务。"""
    if os.name != 'nt':
        return []
    import ctypes
    from ctypes import wintypes

    class UniqueProcess(ctypes.Structure):
        _fields_ = [('pid', wintypes.DWORD), ('started', wintypes.FILETIME)]

    class ProcessInfo(ctypes.Structure):
        _fields_ = [
            ('process', UniqueProcess), ('name', wintypes.WCHAR * 256),
            ('service', wintypes.WCHAR * 64), ('type', ctypes.c_int),
            ('status', wintypes.ULONG), ('session', wintypes.DWORD),
            ('restartable', wintypes.BOOL),
        ]

    manager = ctypes.WinDLL('Rstrtmgr.dll')
    manager.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
                                      wintypes.LPWSTR]
    manager.RmRegisterResources.argtypes = [wintypes.DWORD, wintypes.UINT,
                                           ctypes.POINTER(wintypes.LPCWSTR), wintypes.UINT,
                                           ctypes.POINTER(UniqueProcess), wintypes.UINT,
                                           ctypes.POINTER(wintypes.LPCWSTR)]
    manager.RmGetList.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.UINT),
                                 ctypes.POINTER(wintypes.UINT), ctypes.POINTER(ProcessInfo),
                                 ctypes.POINTER(wintypes.DWORD)]
    manager.RmEndSession.argtypes = [wintypes.DWORD]
    for name in ('RmStartSession', 'RmRegisterResources', 'RmGetList', 'RmEndSession'):
        getattr(manager, name).restype = wintypes.DWORD

    handle = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    error = manager.RmStartSession(ctypes.byref(handle), 0, key)
    if error:
        raise OSError(f'RmStartSession 返回 {error}')
    try:
        filenames = (wintypes.LPCWSTR * 1)(str(path.absolute()))
        error = manager.RmRegisterResources(handle, 1, filenames, 0, None, 0, None)
        if error:
            raise OSError(f'RmRegisterResources 返回 {error}')
        needed, count, reason = wintypes.UINT(), wintypes.UINT(), wintypes.DWORD()
        processes = None
        # 查询期间进程列表可能变化，限制次数，避免诊断无限循环。
        for _ in range(3):
            error = manager.RmGetList(handle, ctypes.byref(needed), ctypes.byref(count),
                                      processes, ctypes.byref(reason))
            if error == 0:
                return [dict(pid=item.process.pid, name=item.name, service=item.service,
                             session=item.session) for item in (processes or [])[:count.value]]
            if error != 234:  # ERROR_MORE_DATA
                raise OSError(f'RmGetList 返回 {error}')
            if needed.value > 4096:
                raise OSError('RmGetList 返回的进程列表过大')
            count.value = needed.value
            processes = (ProcessInfo * count.value)()
        raise OSError('RmGetList 进程列表持续变化')
    finally:
        manager.RmEndSession(handle)


def describe_file_users(path: Path, timeout: float = 2.0) -> str:
    """诊断查询有超时且捕获异常，未知或空结果不证明文件未被占用。"""
    if os.name != 'nt':
        return '[占用诊断] 当前平台不支持 Restart Manager 查询。'
    result: list[str] = []

    def query() -> None:
        try:
            users = query_file_users(path)
            if users:
                result.append('[占用诊断] ' + str(path) + '：' + '; '.join(
                    f'PID={user["pid"]}, name={user["name"]}, service={user["service"]}, '
                    f'session={user["session"]}' for user in users))
            else:
                result.append(f'[占用诊断] 未查询到相关进程（不能排除占用）：{path}')
        except Exception as exc:
            result.append(f'[占用诊断] 查询失败：{path}，{exc}')

    worker = threading.Thread(target=query, name='update-file-users', daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return f'[占用诊断] 查询超时，继续文件处理：{path}'
    return result[0] if result else f'[占用诊断] 查询未返回结果：{path}'
