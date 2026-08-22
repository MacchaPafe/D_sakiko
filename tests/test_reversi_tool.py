from __future__ import annotations

import json
import sys
from pathlib import Path


GPT_ROOT = Path(__file__).resolve().parents[1] / "GPT_SoVITS"
if str(GPT_ROOT) not in sys.path:
    sys.path.insert(0, str(GPT_ROOT))

from chat.tool_calling import ToolCallRequest, ToolRegistry, register_reversi_tool  # noqa: E402


def test_play_reversi_tool_opens_fixed_standard_game():
    calls = []
    registry = ToolRegistry()
    register_reversi_tool(registry, lambda: calls.append(True) or True)
    ok, result = registry.execute(ToolCallRequest("call-1", "play_reversi", {}))
    result = json.loads(result)
    assert ok is True
    assert calls == [True]
    assert result["board_size"] == 8
    assert result["user_side"] == "black"


def test_play_reversi_tool_reports_ui_failure():
    registry = ToolRegistry()
    register_reversi_tool(registry, lambda: False)
    ok, result = registry.execute(ToolCallRequest("call-2", "play_reversi", {}))
    result = json.loads(result)
    assert ok is True
    assert result == {"ok": False, "error": "前台黑白棋面板创建失败"}
