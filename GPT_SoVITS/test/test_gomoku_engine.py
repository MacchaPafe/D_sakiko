# -*- coding: utf-8 -*-
"""五子棋引擎 / AI / 事件格式化单元测试（不依赖 PyQt5）。"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from gomoku_game import (  # noqa: E402
    STONE_BLACK,
    STONE_WHITE,
    GomokuAI,
    GomokuEngine,
    build_game_event,
    detect_threat,
)


class GomokuEngineTestCase(unittest.TestCase):
    def test_win_horizontal(self):
        engine = GomokuEngine(13)
        for col in range(5):
            self.assertTrue(engine.place(6, col, STONE_BLACK))
        result = engine.check_five(6, 4)
        self.assertIsNotNone(result)
        winner, line = result
        self.assertEqual(winner, STONE_BLACK)
        self.assertEqual(len(line), 5)

    def test_win_vertical(self):
        engine = GomokuEngine(13)
        for row in range(5):
            self.assertTrue(engine.place(row, 6, STONE_WHITE))
        winner, _ = engine.check_five(4, 6)
        self.assertEqual(winner, STONE_WHITE)

    def test_win_diagonal_both_directions(self):
        engine = GomokuEngine(13)
        for i in range(5):
            self.assertTrue(engine.place(i, i, STONE_BLACK))
        self.assertIsNotNone(engine.check_five(4, 4))

        engine2 = GomokuEngine(13)
        for i in range(5):
            self.assertTrue(engine2.place(i, 4 - i, STONE_WHITE))
        self.assertIsNotNone(engine2.check_five(4, 0))

    def test_overline_still_wins(self):
        engine = GomokuEngine(13)
        for col in range(6):
            self.assertTrue(engine.place(6, col, STONE_BLACK))
        self.assertIsNotNone(engine.check_five(6, 5))

    def test_occupied_and_out_of_bounds_rejected(self):
        engine = GomokuEngine(13)
        self.assertTrue(engine.place(6, 6, STONE_BLACK))
        self.assertFalse(engine.place(6, 6, STONE_WHITE))
        self.assertFalse(engine.place(-1, 0, STONE_BLACK))
        self.assertFalse(engine.place(0, 13, STONE_BLACK))
        self.assertEqual(engine.move_count, 1)

    def test_undo(self):
        engine = GomokuEngine(13)
        engine.place(6, 6, STONE_BLACK)
        engine.place(6, 7, STONE_WHITE)
        undone = engine.undo()
        self.assertEqual(undone, (6, 7, STONE_WHITE))
        self.assertTrue(engine.is_empty(6, 7))
        self.assertEqual(engine.move_count, 1)
        self.assertEqual(engine.current_player, STONE_WHITE)

    def test_full_board_draw(self):
        engine = GomokuEngine(5)
        pattern = [
            [1, 2, 1, 2, 1],
            [2, 1, 2, 1, 2],
            [1, 2, 1, 2, 1],
            [2, 1, 2, 1, 2],
            [2, 1, 2, 1, 2],
        ]
        for r in range(5):
            for c in range(5):
                self.assertTrue(engine.place(r, c, pattern[r][c]))
        self.assertTrue(engine.is_full())
        for r in range(5):
            for c in range(5):
                self.assertIsNone(engine.check_five(r, c))

    def test_coord_name_and_record(self):
        engine = GomokuEngine(13)
        self.assertEqual(engine.coord_name(0, 0), "1A")
        self.assertEqual(engine.coord_name(12, 12), "13N")
        self.assertEqual(engine.coord_name(2, 8), "3J")
        engine.place(6, 6, STONE_BLACK)
        engine.place(7, 7, STONE_WHITE)
        self.assertEqual(engine.record_text(), "7G, 8H")

    def test_size_switch(self):
        for size in (9, 13, 19):
            engine = GomokuEngine(size)
            self.assertEqual(len(engine.board), size)
            self.assertEqual(len(engine.board[0]), size)
            engine.reset(size)
            self.assertEqual(engine.move_count, 0)

    def test_detect_threat_live_three(self):
        engine = GomokuEngine(13)
        engine.place(6, 5, STONE_BLACK)
        engine.place(6, 7, STONE_BLACK)
        engine.place(6, 6, STONE_BLACK)
        self.assertEqual(detect_threat(engine.board, 6, 6, STONE_BLACK), "活三")

    def test_detect_threat_four(self):
        engine = GomokuEngine(13)
        engine.place(6, 5, STONE_BLACK)
        engine.place(6, 6, STONE_BLACK)
        engine.place(6, 7, STONE_BLACK)
        engine.place(6, 4, STONE_WHITE)  # 堵住左侧
        engine.place(6, 8, STONE_BLACK)
        self.assertEqual(detect_threat(engine.board, 6, 8, STONE_BLACK), "冲四")

    def test_build_game_event(self):
        event = build_game_event(
            "user_win",
            13,
            coord="7G",
            record_text="7G, 8H",
            winner="user",
            line=[[6, 2], [6, 3], [6, 4], [6, 5], [6, 6]],
        )
        self.assertEqual(event["type"], "user_win")
        self.assertEqual(event["board_size"], 13)
        self.assertEqual(event["coord"], "7G")
        self.assertEqual(event["record_text"], "7G, 8H")
        self.assertEqual(event["winner"], "user")


class GomokuAITestCase(unittest.TestCase):
    def _board_with_black_four(self):
        engine = GomokuEngine(13)
        for col in range(5, 9):
            engine.place(6, col, STONE_BLACK)
        return [row[:] for row in engine.board]

    def test_ai_takes_winning_move(self):
        board = self._board_with_black_four()
        ai = GomokuAI(time_budget=1.0)
        move = ai.choose_move(board, STONE_BLACK)
        self.assertIn(move, [(6, 4), (6, 9)])

    def test_ai_blocks_opponent_win(self):
        engine = GomokuEngine(13)
        for col in range(5, 9):
            engine.place(6, col, STONE_WHITE)
        ai = GomokuAI(time_budget=1.0)
        move = ai.choose_move([row[:] for row in engine.board], STONE_BLACK)
        self.assertIn(move, [(6, 4), (6, 9)])

    def test_ai_returns_valid_center_move_on_empty_board(self):
        engine = GomokuEngine(13)
        ai = GomokuAI(time_budget=1.0)
        move = ai.choose_move(engine.board, STONE_BLACK)
        self.assertEqual(move, (6, 6))

    def test_ai_move_is_legal_after_few_moves(self):
        engine = GomokuEngine(13)
        engine.place(6, 6, STONE_BLACK)
        engine.place(6, 7, STONE_WHITE)
        engine.place(7, 6, STONE_BLACK)
        ai = GomokuAI(time_budget=1.0)
        move = ai.choose_move([row[:] for row in engine.board], STONE_WHITE)
        self.assertIsNotNone(move)
        r, c = move
        self.assertTrue(engine.is_empty(r, c))

    def test_ai_completes_within_budget(self):
        engine = GomokuEngine(13)
        engine.place(6, 6, STONE_BLACK)
        engine.place(6, 7, STONE_WHITE)
        engine.place(7, 6, STONE_BLACK)
        engine.place(7, 7, STONE_WHITE)
        ai = GomokuAI(time_budget=0.5)
        started = time.monotonic()
        move = ai.choose_move([row[:] for row in engine.board], STONE_BLACK)
        elapsed = time.monotonic() - started
        self.assertIsNotNone(move)
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
