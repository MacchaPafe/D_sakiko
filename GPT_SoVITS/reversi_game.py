# -*- coding: utf-8 -*-
"""标准 8×8 黑白棋：纯规则/AI 与可选 PyQt5 内嵌面板。"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:  # pragma: no cover - Qt 环境探测
    from PyQt5.QtCore import QRectF, Qt, QThread, pyqtSignal
    from PyQt5.QtGui import QColor, QPainter, QPen, QRadialGradient
    from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

    PYQT_AVAILABLE = True
except Exception:  # pragma: no cover
    PYQT_AVAILABLE = False


EMPTY, BLACK, WHITE = 0, 1, 2
BOARD_SIZE = 8
COLUMN_LABELS = "ABCDEFGH"
DIRECTIONS = tuple(
    (dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (dr, dc) != (0, 0)
)
POSITION_WEIGHTS = (
    (120, -35, 15, 5, 5, 15, -35, 120),
    (-35, -55, -5, -5, -5, -5, -55, -35),
    (15, -5, 12, 3, 3, 12, -5, 15),
    (5, -5, 3, 2, 2, 3, -5, 5),
    (5, -5, 3, 2, 2, 3, -5, 5),
    (15, -5, 12, 3, 3, 12, -5, 15),
    (-35, -55, -5, -5, -5, -5, -55, -35),
    (120, -35, 15, 5, 5, 15, -35, 120),
)
_INF = 10**12


class _SearchTimeout(Exception):
    pass


def opponent(player: int) -> int:
    return WHITE if player == BLACK else BLACK


def build_reversi_event(event_type: str, record_text: str = "", **extra: object) -> Dict[str, object]:
    return {"type": event_type, "board_size": BOARD_SIZE, "record_text": record_text, **extra}


class ReversiEngine:
    """标准黑白棋规则引擎；每次落子或跳过均可独立撤销。"""

    def __init__(self) -> None:
        self.board: List[List[int]] = []
        self.current_player = BLACK
        self.consecutive_passes = 0
        self.actions: List[Dict[str, object]] = []
        self._history: List[Tuple[List[List[int]], int, int]] = []
        self.reset()

    def reset(self) -> None:
        self.board = [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        self.board[3][3] = self.board[4][4] = WHITE
        self.board[3][4] = self.board[4][3] = BLACK
        self.current_player = BLACK
        self.consecutive_passes = 0
        self.actions.clear()
        self._history.clear()

    @staticmethod
    def coord_name(r: int, c: int) -> str:
        return f"{COLUMN_LABELS[c]}{r + 1}"

    @staticmethod
    def captures_on(board: Sequence[Sequence[int]], r: int, c: int, player: int) -> List[Tuple[int, int]]:
        if not (0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE) or board[r][c] != EMPTY:
            return []
        captured: List[Tuple[int, int]] = []
        other = opponent(player)
        for dr, dc in DIRECTIONS:
            line: List[Tuple[int, int]] = []
            rr, cc = r + dr, c + dc
            while 0 <= rr < BOARD_SIZE and 0 <= cc < BOARD_SIZE and board[rr][cc] == other:
                line.append((rr, cc))
                rr += dr
                cc += dc
            if line and 0 <= rr < BOARD_SIZE and 0 <= cc < BOARD_SIZE and board[rr][cc] == player:
                captured.extend(line)
        return captured

    @classmethod
    def legal_moves_on(cls, board: Sequence[Sequence[int]], player: int) -> List[Tuple[int, int]]:
        return [
            (r, c)
            for r in range(BOARD_SIZE)
            for c in range(BOARD_SIZE)
            if cls.captures_on(board, r, c, player)
        ]

    def legal_moves(self, player: Optional[int] = None) -> List[Tuple[int, int]]:
        return self.legal_moves_on(self.board, self.current_player if player is None else player)

    def place(self, r: int, c: int, player: Optional[int] = None) -> List[Tuple[int, int]]:
        player = self.current_player if player is None else int(player)
        if player != self.current_player:
            return []
        captured = self.captures_on(self.board, r, c, player)
        if not captured:
            return []
        self._history.append(([row[:] for row in self.board], self.current_player, self.consecutive_passes))
        self.board[r][c] = player
        for rr, cc in captured:
            self.board[rr][cc] = player
        self.actions.append({"player": player, "coord": (r, c), "flipped": tuple(captured)})
        self.current_player = opponent(player)
        self.consecutive_passes = 0
        return captured

    def pass_turn(self, player: Optional[int] = None) -> bool:
        player = self.current_player if player is None else int(player)
        if player != self.current_player or self.legal_moves(player):
            return False
        self._history.append(([row[:] for row in self.board], self.current_player, self.consecutive_passes))
        self.actions.append({"player": player, "coord": None, "flipped": ()})
        self.current_player = opponent(player)
        self.consecutive_passes += 1
        return True

    def undo(self) -> Optional[Dict[str, object]]:
        if not self._history:
            return None
        action = self.actions.pop()
        self.board, self.current_player, self.consecutive_passes = self._history.pop()
        return action

    def counts(self) -> Tuple[int, int]:
        black = sum(cell == BLACK for row in self.board for cell in row)
        white = sum(cell == WHITE for row in self.board for cell in row)
        return black, white

    def is_game_over(self) -> bool:
        return self.consecutive_passes >= 2 or all(cell != EMPTY for row in self.board for cell in row) or (
            not self.legal_moves(BLACK) and not self.legal_moves(WHITE)
        )

    def winner(self) -> int:
        black, white = self.counts()
        return BLACK if black > white else WHITE if white > black else EMPTY

    def record_text(self) -> str:
        parts = []
        for index, action in enumerate(self.actions, 1):
            side = "黑" if action["player"] == BLACK else "白"
            coord = action["coord"]
            if coord is None:
                parts.append(f"{index}.{side}跳过")
            else:
                r, c = coord
                parts.append(f"{index}.{side}{self.coord_name(int(r), int(c))}(翻{len(action['flipped'])})")
        return " ".join(parts)

    def board_text(self) -> str:
        symbols = {EMPTY: ".", BLACK: "B", WHITE: "W"}
        return "/".join("".join(symbols[cell] for cell in row) for row in self.board)


class ReversiAI:
    """带时间预算的确定性迭代加深 alpha-beta AI。"""

    def __init__(self, time_budget: float = 0.8, max_depth: int = 7) -> None:
        self.time_budget = max(0.02, float(time_budget))
        self.max_depth = max(1, int(max_depth))
        self._deadline = 0.0

    @staticmethod
    def _apply(board: Sequence[Sequence[int]], move: Tuple[int, int], player: int) -> List[List[int]]:
        result = [list(row) for row in board]
        r, c = move
        flips = ReversiEngine.captures_on(result, r, c, player)
        result[r][c] = player
        for rr, cc in flips:
            result[rr][cc] = player
        return result

    @staticmethod
    def _ordered(moves: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
        return sorted(moves, key=lambda p: (-POSITION_WEIGHTS[p[0]][p[1]], p[0], p[1]))

    @classmethod
    def evaluate(cls, board: Sequence[Sequence[int]], player: int) -> int:
        other = opponent(player)
        mine = sum(cell == player for row in board for cell in row)
        theirs = sum(cell == other for row in board for cell in row)
        empty = BOARD_SIZE * BOARD_SIZE - mine - theirs
        positional = sum(
            POSITION_WEIGHTS[r][c] * (1 if board[r][c] == player else -1 if board[r][c] == other else 0)
            for r in range(BOARD_SIZE) for c in range(BOARD_SIZE)
        )
        mobility = len(ReversiEngine.legal_moves_on(board, player)) - len(ReversiEngine.legal_moves_on(board, other))
        frontier = [0, 0, 0]
        for r in range(BOARD_SIZE):
            for c in range(BOARD_SIZE):
                stone = board[r][c]
                if stone and any(
                    0 <= r + dr < BOARD_SIZE and 0 <= c + dc < BOARD_SIZE and board[r + dr][c + dc] == EMPTY
                    for dr, dc in DIRECTIONS
                ):
                    frontier[stone] += 1
        value = positional + 12 * mobility - 4 * (frontier[player] - frontier[other])
        if empty <= 12:
            value += 30 * (mine - theirs)
        return int(value)

    def _search(self, board: Sequence[Sequence[int]], turn: int, root: int, depth: int, alpha: int, beta: int, passed: bool) -> int:
        if time.perf_counter() >= self._deadline:
            raise _SearchTimeout
        moves = ReversiEngine.legal_moves_on(board, turn)
        if depth <= 0:
            return self.evaluate(board, root)
        if not moves:
            other_moves = ReversiEngine.legal_moves_on(board, opponent(turn))
            if passed or not other_moves:
                mine = sum(cell == root for row in board for cell in row)
                theirs = sum(cell == opponent(root) for row in board for cell in row)
                return (mine - theirs) * 100000
            return self._search(board, opponent(turn), root, depth - 1, alpha, beta, True)
        maximizing = turn == root
        best = -_INF if maximizing else _INF
        for move in self._ordered(moves):
            value = self._search(self._apply(board, move, turn), opponent(turn), root, depth - 1, alpha, beta, False)
            if maximizing:
                best = max(best, value)
                alpha = max(alpha, best)
            else:
                best = min(best, value)
                beta = min(beta, best)
            if beta <= alpha:
                break
        return int(best)

    def choose_move(self, board: Sequence[Sequence[int]], player: int) -> Optional[Tuple[int, int]]:
        moves = self._ordered(ReversiEngine.legal_moves_on(board, player))
        if not moves:
            return None
        self._deadline = time.perf_counter() + self.time_budget
        best_move = moves[0]
        for depth in range(1, self.max_depth + 1):
            try:
                scored = []
                for move in moves:
                    value = self._search(self._apply(board, move, player), opponent(player), player, depth - 1, -_INF, _INF, False)
                    scored.append((value, -moves.index(move), move))
                best_move = max(scored)[2]
            except _SearchTimeout:
                break
        return best_move


if PYQT_AVAILABLE:  # pragma: no cover - 需要 Qt 环境

    class ReversiBoardWidget(QWidget):
        move_requested = pyqtSignal(int, int)

        def __init__(self, parent: Optional[QWidget] = None) -> None:
            super().__init__(parent)
            self._board: List[List[int]] = []
            self._legal: set[Tuple[int, int]] = set()
            self._last_move: Optional[Tuple[int, int]] = None
            self._flipped: set[Tuple[int, int]] = set()
            self._cell, self._margin = 54, 24
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.setFixedSize(self._margin * 2 + self._cell * 8, self._margin * 2 + self._cell * 8)

        def set_board_state(self, board: Sequence[Sequence[int]], legal: Sequence[Tuple[int, int]] = (), last_move: Optional[Tuple[int, int]] = None, flipped: Sequence[Tuple[int, int]] = ()) -> None:
            self._board = [list(row) for row in board]
            self._legal, self._last_move, self._flipped = set(legal), last_move, set(flipped)
            self.update()

        def mousePressEvent(self, event) -> None:  # noqa: N802
            c = (event.pos().x() - self._margin) // self._cell
            r = (event.pos().y() - self._margin) // self._cell
            if (r, c) in self._legal:
                self.move_requested.emit(int(r), int(c))

        def paintEvent(self, event) -> None:  # noqa: N802
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.fillRect(self.rect(), QColor(26, 102, 63))
            painter.setPen(QPen(QColor(10, 55, 35), 1))
            m, cell = self._margin, self._cell
            for i in range(9):
                painter.drawLine(m, m + i * cell, m + 8 * cell, m + i * cell)
                painter.drawLine(m + i * cell, m, m + i * cell, m + 8 * cell)
            for r in range(8):
                for c in range(8):
                    x, y = m + c * cell + cell / 2, m + r * cell + cell / 2
                    stone = self._board[r][c] if self._board else EMPTY
                    if stone:
                        radius = cell * 0.39
                        grad = QRadialGradient(x - radius * .3, y - radius * .3, radius)
                        if stone == BLACK:
                            grad.setColorAt(0, QColor(90, 90, 90)); grad.setColorAt(1, QColor(8, 8, 8))
                        else:
                            grad.setColorAt(0, QColor(255, 255, 255)); grad.setColorAt(1, QColor(190, 190, 190))
                        painter.setBrush(grad); painter.setPen(QPen(QColor(20, 20, 20), 1))
                        painter.drawEllipse(QRectF(x - radius, y - radius, radius * 2, radius * 2))
                        if (r, c) in self._flipped:
                            painter.setBrush(Qt.NoBrush); painter.setPen(QPen(QColor(255, 190, 45), 2))
                            painter.drawEllipse(QRectF(x - radius - 2, y - radius - 2, radius * 2 + 4, radius * 2 + 4))
                    elif (r, c) in self._legal:
                        painter.setBrush(QColor(190, 240, 190, 130)); painter.setPen(Qt.NoPen)
                        painter.drawEllipse(QRectF(x - 6, y - 6, 12, 12))
            if self._last_move:
                r, c = self._last_move; x, y = m + c * cell + cell / 2, m + r * cell + cell / 2
                painter.setBrush(QColor(255, 70, 70)); painter.setPen(Qt.NoPen); painter.drawEllipse(QRectF(x - 4, y - 4, 8, 8))
            painter.end()


    class _ReversiAIThread(QThread):
        move_ready = pyqtSignal(object)

        def __init__(self, board: Sequence[Sequence[int]], player: int, parent: Optional[QWidget] = None) -> None:
            super().__init__(parent); self.board = [list(row) for row in board]; self.player = player

        def run(self) -> None:
            move = ReversiAI().choose_move(self.board, self.player)
            if not self.isInterruptionRequested():
                self.move_ready.emit(move)


    class ReversiGamePanel(QWidget):
        """内嵌标准黑白棋人机对战面板。"""

        def __init__(self, parent: Optional[QWidget] = None) -> None:
            super().__init__(parent)
            self.on_game_event: Optional[Callable[[Dict[str, object]], None]] = None
            self.on_manual_end: Optional[Callable[[], None]] = None
            self.engine = ReversiEngine(); self.user_player = BLACK; self.ai_player = WHITE
            self.game_active = self.thinking = False
            self._ai_thread: Optional[_ReversiAIThread] = None
            self._last_score_leader = 0
            self._build_ui(); self._refresh_controls(); self.setVisible(False)

        def _build_ui(self) -> None:
            root = QVBoxLayout(self); header = QHBoxLayout(); self.title_label = QLabel("黑白棋 · 8×8")
            self.new_btn = QPushButton("新开局"); self.undo_btn = QPushButton("悔棋")
            self.resign_btn = QPushButton("认输"); self.close_btn = QPushButton("结束")
            header.addWidget(self.title_label); header.addStretch(1)
            for button in (self.new_btn, self.undo_btn, self.resign_btn, self.close_btn): header.addWidget(button)
            root.addLayout(header); self.board_widget = ReversiBoardWidget(); root.addWidget(self.board_widget)
            self.score_label = QLabel(""); self.status_label = QLabel(""); root.addWidget(self.score_label); root.addWidget(self.status_label)
            self.board_widget.move_requested.connect(self._on_user_move)
            self.new_btn.clicked.connect(self.start_new_game); self.undo_btn.clicked.connect(self.undo)
            self.resign_btn.clicked.connect(self.resign); self.close_btn.clicked.connect(self.close_game)
            self.setStyleSheet("QFrame, QWidget { background: transparent; } QLabel { color: #333333; }")

        def start_new_game(self) -> None:
            self._stop_ai_thread(); self.engine.reset(); self.game_active = True; self.thinking = False; self._last_score_leader = 0
            self._refresh_board(); self.status_label.setText("新开局：你执黑棋先行。"); self._refresh_controls(); self._emit_event("game_started")

        def record_text(self) -> str:
            black, white = self.engine.counts()
            return f"黑{black}:白{white}；{self.engine.record_text()}；盘面={self.engine.board_text()}"

        def undo(self) -> None:
            if not self.game_active or self.thinking or not self.engine.actions: return
            while self.engine.actions:
                action = self.engine.undo()
                if action and action["player"] == self.user_player: break
            self.engine.current_player = self.user_player; self.engine.consecutive_passes = 0
            self._last_score_leader = 0; self._refresh_board(); self.status_label.setText("已悔棋，轮到你落子。"); self._refresh_controls()

        def resign(self) -> None:
            if not self.game_active: return
            self.game_active = False; self._stop_ai_thread(); self.status_label.setText("你认输了。"); self._emit_event("resigned"); self._refresh_controls()

        def close_game(self) -> None:
            self.game_active = False; self._stop_ai_thread()
            if self.on_manual_end:
                try: self.on_manual_end()
                except Exception: pass
            self.hide()

        def _emit_event(self, event_type: str, **extra: object) -> None:
            if self.on_game_event:
                try: self.on_game_event(build_reversi_event(event_type, self.record_text(), **extra))
                except Exception: pass

        def _refresh_board(self, last_move: Optional[Tuple[int, int]] = None, flipped: Sequence[Tuple[int, int]] = ()) -> None:
            legal = self.engine.legal_moves(self.user_player) if self.game_active and not self.thinking and self.engine.current_player == self.user_player else []
            self.board_widget.set_board_state(self.engine.board, legal, last_move, flipped)
            black, white = self.engine.counts(); self.score_label.setText(f"● 黑棋（你） {black}    ○ 白棋（对手） {white}")

        def _refresh_controls(self) -> None:
            can_act = self.game_active and not self.thinking
            self.new_btn.setEnabled(True); self.undo_btn.setEnabled(can_act and bool(self.engine.actions))
            self.resign_btn.setEnabled(can_act); self.close_btn.setEnabled(True)

        def _on_user_move(self, r: int, c: int) -> None:
            if not self.game_active or self.thinking or self.engine.current_player != self.user_player: return
            flips = self.engine.place(r, c)
            if not flips: return
            self._after_move(self.user_player, (r, c), flips)
            if not self._finish_if_needed(): self._advance_turns()

        def _after_move(self, player: int, move: Tuple[int, int], flips: Sequence[Tuple[int, int]]) -> None:
            self._refresh_board(move, flips)
            if move in ((0, 0), (0, 7), (7, 0), (7, 7)):
                self._emit_event("corner_taken", player="user" if player == self.user_player else "ai", coord=self.engine.coord_name(*move))
            black, white = self.engine.counts(); leader = 1 if black - white >= 8 else -1 if white - black >= 8 else 0
            if leader and self._last_score_leader and leader != self._last_score_leader:
                self._emit_event("score_swing", leader="user" if leader > 0 else "ai", black=black, white=white)
            if leader: self._last_score_leader = leader

        def _advance_turns(self) -> None:
            while self.game_active:
                player = self.engine.current_player
                if self.engine.legal_moves(player):
                    if player == self.ai_player: self._start_ai_move()
                    else:
                        self.thinking = False; self.status_label.setText("轮到你落子。"); self._refresh_board(); self._refresh_controls()
                    return
                self.engine.pass_turn(player)
                self._emit_event("turn_passed", player="user" if player == self.user_player else "ai")
                if self._finish_if_needed(): return

        def _start_ai_move(self) -> None:
            self.thinking = True; self.status_label.setText("对手思考中…"); self._refresh_board(); self._refresh_controls()
            thread = _ReversiAIThread(self.engine.board, self.ai_player, self); thread.move_ready.connect(self._on_ai_move_ready)
            self._ai_thread = thread; thread.start()

        def _on_ai_move_ready(self, move: object) -> None:
            if not self.game_active: return
            self.thinking = False
            if not isinstance(move, tuple) or len(move) != 2:
                self._advance_turns(); return
            r, c = int(move[0]), int(move[1]); flips = self.engine.place(r, c)
            if flips: self._after_move(self.ai_player, (r, c), flips)
            if not self._finish_if_needed(): self._advance_turns()

        def _finish_if_needed(self) -> bool:
            if not self.engine.is_game_over(): return False
            self.game_active = False; self.thinking = False; black, white = self.engine.counts(); winner = self.engine.winner()
            if winner == BLACK: event, status = "user_win", f"你获胜！最终比分 黑 {black} : 白 {white}"
            elif winner == WHITE: event, status = "ai_win", f"对手获胜。最终比分 黑 {black} : 白 {white}"
            else: event, status = "draw", f"平局。最终比分 黑 {black} : 白 {white}"
            self.status_label.setText(status); self._refresh_board(); self._emit_event(event, black=black, white=white); self._refresh_controls(); return True

        def _stop_ai_thread(self) -> None:
            self.thinking = False; thread = self._ai_thread; self._ai_thread = None
            if thread and thread.isRunning(): thread.requestInterruption()

