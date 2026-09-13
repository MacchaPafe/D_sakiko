from __future__ import annotations

import unittest
from typing import cast

import uvicorn

from dsakiko_webui.backend.main import run_server_until_stopped


class _FakeServer:
    """模拟正常返回或在优雅退出后抛出中断的 Uvicorn 服务器。"""

    def __init__(self, *, interrupt: bool) -> None:
        """配置本次运行是否抛出 KeyboardInterrupt。"""
        self.interrupt = interrupt
        self.run_count = 0

    def run(self) -> None:
        """记录运行次数，并按配置模拟 Uvicorn 的退出行为。"""
        self.run_count += 1
        if self.interrupt:
            raise KeyboardInterrupt


class MainServerShutdownTest(unittest.TestCase):
    """验证命令行主服务器的预期中断不会泄漏为 traceback。"""

    def test_keyboard_interrupt_after_shutdown_is_suppressed(self) -> None:
        """优雅关闭后重新抛出的 KeyboardInterrupt 应被启动边界吞掉。"""
        server = _FakeServer(interrupt=True)

        run_server_until_stopped(cast(uvicorn.Server, server))

        self.assertEqual(server.run_count, 1)

    def test_normal_server_return_is_preserved(self) -> None:
        """主服务器正常返回时不应改变其调用流程。"""
        server = _FakeServer(interrupt=False)

        run_server_until_stopped(cast(uvicorn.Server, server))

        self.assertEqual(server.run_count, 1)


if __name__ == "__main__":
    unittest.main()
