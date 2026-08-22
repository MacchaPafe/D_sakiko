from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GPT_ROOT = ROOT / "GPT_SoVITS"
if str(GPT_ROOT) not in sys.path:
    sys.path.insert(0, str(GPT_ROOT))

from chat.tool_calling import (  # noqa: E402
    ToolCallRequest,
    ToolRegistry,
    register_gomoku_tool,
    register_reversi_tool,
)
from gomoku_game import GomokuAI, GomokuEngine, STONE_BLACK  # noqa: E402
from reversi_game import BLACK, ReversiAI, ReversiEngine  # noqa: E402


def _chat_gui_method(name: str) -> ast.FunctionDef:
    tree = ast.parse((GPT_ROOT / "qtUI.py").read_text(encoding="utf-8"))
    chat_gui = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ChatGUI")
    return next(node for node in chat_gui.body if isinstance(node, ast.FunctionDef) and node.name == name)


def _calls(method: ast.FunctionDef) -> set[str]:
    result = set()
    for node in ast.walk(method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            parts = []
            value = node.func
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                parts.append(value.id)
            result.add(".".join(reversed(parts)))
    return result


def test_engines_interleave_without_shared_state():
    gomoku = GomokuEngine(9)
    reversi = ReversiEngine()
    assert gomoku.place(4, 4, STONE_BLACK)
    flips = reversi.place(2, 3, BLACK)

    assert flips == [(3, 3)]
    assert gomoku.move_count == 1
    assert reversi.counts() == (4, 1)
    assert gomoku.board[4][4] == STONE_BLACK

    reversi.undo()
    assert reversi.counts() == (2, 2)
    assert gomoku.move_count == 1
    assert gomoku.board[4][4] == STONE_BLACK


def test_both_ais_return_legal_moves_in_same_process():
    gomoku = GomokuEngine(9)
    gomoku_move = GomokuAI(time_budget=0.05).choose_move(gomoku.board, STONE_BLACK)
    assert gomoku_move == (4, 4)

    reversi = ReversiEngine()
    reversi_legal = reversi.legal_moves(BLACK)
    reversi_move = ReversiAI(time_budget=0.05, max_depth=4).choose_move(reversi.board, BLACK)
    assert reversi_move in reversi_legal


def test_tool_registry_routes_each_game_to_its_own_ui_callback():
    opened = []
    registry = ToolRegistry()
    register_gomoku_tool(registry, lambda size: opened.append(("gomoku", size)) or True)
    register_reversi_tool(registry, lambda: opened.append(("reversi", 8)) or True)

    ok_g, raw_g = registry.execute(ToolCallRequest("g", "play_gomoku", {"board_size": 9}))
    ok_r, raw_r = registry.execute(ToolCallRequest("r", "play_reversi", {}))
    gomoku_result, reversi_result = json.loads(raw_g), json.loads(raw_r)

    assert ok_g and ok_r
    assert opened == [("gomoku", 9), ("reversi", 8)]
    assert gomoku_result["board_size"] == 9
    assert reversi_result["board_size"] == 8
    assert {tool["function"]["name"] for tool in registry.build_tools_schema()} >= {"play_gomoku", "play_reversi"}


def test_ui_event_prefixes_are_distinct_and_registered_front_to_back():
    qt_source = (GPT_ROOT / "qtUI.py").read_text(encoding="utf-8")
    dp_source = (GPT_ROOT / "dp_local2.py").read_text(encoding="utf-8")
    assert '__GOMOKU_UI_CMD__:' in qt_source and '__REVERSI_UI_CMD__:' in qt_source
    assert '__GOMOKU_UI_CMD__:' in dp_source and '__REVERSI_UI_CMD__:' in dp_source
    assert "_register_message_command_handler(GOMOKU_UI_EVENT_PREFIX" in qt_source
    assert "_register_message_command_handler(REVERSI_UI_EVENT_PREFIX" in qt_source
    assert "register_gomoku_tool(" in dp_source and "register_reversi_tool(" in dp_source


def test_opening_one_panel_closes_the_other_panel():
    gomoku_calls = _calls(_chat_gui_method("open_gomoku_game"))
    reversi_calls = _calls(_chat_gui_method("open_reversi_game"))
    assert "self.reversi_panel.close_game" in gomoku_calls
    assert "self.gomoku_panel.close_game" in reversi_calls
    assert "self.gomoku_panel.start_new_game" in gomoku_calls
    assert "self.reversi_panel.start_new_game" in reversi_calls


def test_context_and_commentary_queues_remain_game_specific():
    gomoku_context = ast.unparse(_chat_gui_method("_append_gomoku_board_context_if_needed"))
    reversi_context = ast.unparse(_chat_gui_method("_append_reversi_board_context_if_needed"))
    gomoku_queue = ast.unparse(_chat_gui_method("_queue_gomoku_commentary"))
    reversi_queue = ast.unparse(_chat_gui_method("_queue_reversi_commentary"))
    assert "self.gomoku_panel" in gomoku_context and "self.reversi_panel" not in gomoku_context
    assert "self.reversi_panel" in reversi_context and "self.gomoku_panel" not in reversi_context
    assert "_pending_gomoku_commentary" in gomoku_queue and "_pending_reversi_commentary" not in gomoku_queue
    assert "_pending_reversi_commentary" in reversi_queue and "_pending_gomoku_commentary" not in reversi_queue


def test_both_menu_entries_are_wired():
    tree = ast.parse((GPT_ROOT / "qtUI.py").read_text(encoding="utf-8"))
    more = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MoreFunctionWindow")
    source = ast.unparse(more)
    assert "五子棋小游戏" in source and "黑白棋小游戏" in source
    assert "open_gomoku_fun()" in source and "open_reversi_fun()" in source
