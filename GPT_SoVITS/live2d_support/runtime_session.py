from __future__ import annotations

from types import ModuleType

from OpenGL.GL import glUseProgram

from live2d_support.runtime_adapter import (
    Live2DModelAdapter,
    Live2DVersion,
    detect_live2d_runtime_version,
    initialize_live2d_runtime,
    load_live2d_runtime,
    release_live2d_runtime,
)


class Live2DRuntimeSession:
    """管理一个窗口 context 内常驻的运行时；所有操作均在渲染线程执行。"""

    def __init__(self) -> None:
        """创建尚未加载运行时的会话。"""
        self._runtimes: dict[Live2DVersion, ModuleType] = {}

    def create_model(self, path: str) -> Live2DModelAdapter:
        """按需初始化目标运行时并创建独立模型，失败不影响其他运行时。"""
        version = detect_live2d_runtime_version(path)
        glUseProgram(0)
        if version not in self._runtimes:
            runtime = load_live2d_runtime(version)
            initialize_live2d_runtime(runtime)
            self._runtimes[version] = runtime
        return Live2DModelAdapter.create(path)

    def close(self) -> None:
        """在全部模型销毁后、窗口 context 销毁前释放运行时。"""
        glUseProgram(0)
        first_error: Exception | None = None
        try:
            for runtime in self._runtimes.values():
                try:
                    release_live2d_runtime(runtime)
                except Exception as error:
                    if first_error is None:
                        first_error = error
        finally:
            self._runtimes.clear()
        if first_error is not None:
            raise first_error
