from __future__ import annotations

import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GPT_ROOT = ROOT / "GPT_SoVITS"
if str(GPT_ROOT) not in sys.path:
    sys.path.insert(0, str(GPT_ROOT))

from reversi_game import BLACK, EMPTY, WHITE, ReversiAI, ReversiEngine  # noqa: E402


def test_standard_initial_position_and_legal_moves():
    engine = ReversiEngine()
    assert engine.counts() == (2, 2)
    assert set(engine.legal_moves(BLACK)) == {(2, 3), (3, 2), (4, 5), (5, 4)}
    assert set(engine.legal_moves(WHITE)) == {(2, 4), (3, 5), (4, 2), (5, 3)}


def test_place_flips_and_illegal_move_is_unchanged():
    engine = ReversiEngine()
    before = [row[:] for row in engine.board]
    assert engine.place(0, 0) == []
    assert engine.board == before
    assert engine.actions == []

    flips = engine.place(2, 3)
    assert flips == [(3, 3)]
    assert engine.board[2][3] == BLACK
    assert engine.board[3][3] == BLACK
    assert engine.counts() == (4, 1)
    assert engine.current_player == WHITE


def test_move_can_flip_in_multiple_directions():
    engine = ReversiEngine()
    engine.board = [[EMPTY] * 8 for _ in range(8)]
    engine.current_player = BLACK
    for r, c in ((3, 1), (3, 5), (1, 3), (5, 3), (1, 1), (5, 5), (1, 5), (5, 1)):
        engine.board[r][c] = BLACK
    for r, c in ((3, 2), (3, 4), (2, 3), (4, 3), (2, 2), (4, 4), (2, 4), (4, 2)):
        engine.board[r][c] = WHITE
    flips = engine.place(3, 3)
    assert len(flips) == 8
    assert all(engine.board[r][c] == BLACK for r, c in flips)


def test_pass_and_undo_restore_exact_state():
    engine = ReversiEngine()
    engine.board = [[BLACK] * 8 for _ in range(8)]
    engine.board[0][0] = EMPTY
    engine.board[0][1] = WHITE
    engine.current_player = WHITE
    before = [row[:] for row in engine.board]
    assert engine.legal_moves(WHITE) == []
    assert engine.pass_turn(WHITE)
    assert engine.actions[-1]["coord"] is None
    assert engine.current_player == BLACK
    action = engine.undo()
    assert action and action["player"] == WHITE
    assert engine.board == before
    assert engine.current_player == WHITE
    assert engine.consecutive_passes == 0


def test_game_over_and_winner_when_neither_side_can_move():
    engine = ReversiEngine()
    engine.board = [[BLACK] * 8 for _ in range(8)]
    engine.board[7][7] = WHITE
    assert engine.is_game_over()
    assert engine.winner() == BLACK
    assert engine.counts() == (63, 1)


def test_record_contains_moves_and_passes():
    engine = ReversiEngine()
    engine.place(2, 3)
    engine.board = [[BLACK] * 8 for _ in range(8)]
    engine.board[0][0] = EMPTY
    engine.current_player = WHITE
    engine.pass_turn()
    assert "1.黑D3(翻1)" in engine.record_text()
    assert "2.白跳过" in engine.record_text()


def test_ai_returns_legal_move_and_respects_budget():
    engine = ReversiEngine()
    legal = engine.legal_moves(BLACK)
    started = time.perf_counter()
    move = ReversiAI(time_budget=0.08, max_depth=5).choose_move(engine.board, BLACK)
    elapsed = time.perf_counter() - started
    assert move in legal
    assert elapsed < 0.6


def test_ai_takes_available_corner_and_returns_none_without_moves():
    board = [[EMPTY] * 8 for _ in range(8)]
    board[0][1] = WHITE
    board[0][2] = BLACK
    board[1][0] = WHITE
    board[2][0] = BLACK
    assert ReversiAI(time_budget=0.08, max_depth=4).choose_move(board, BLACK) == (0, 0)
    full = [[BLACK] * 8 for _ in range(8)]
    assert ReversiAI().choose_move(full, WHITE) is None

