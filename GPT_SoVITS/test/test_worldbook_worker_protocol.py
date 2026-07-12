"""世界书 worker NDJSON 协议测试。"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

from rag.worldbook.worker import emit_event


class WorldbookWorkerProtocolTest(unittest.TestCase):
    """验证 stdout 事件能够逐行独立解析。"""

    def test_emit_event_outputs_one_json_line(self) -> None:
        """协议事件必须是单行 UTF-8 JSON。"""

        output = io.StringIO()
        with redirect_stdout(output):
            emit_event("progress", stage="loading_packages")

        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), {"event": "progress", "stage": "loading_packages"})


if __name__ == "__main__":
    unittest.main()
