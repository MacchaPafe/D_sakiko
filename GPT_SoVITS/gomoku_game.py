# -*- coding: utf-8 -*-
"""数字小祥「五子棋小游戏」：引擎 / AI / 事件格式化（纯逻辑）与 PyQt5 界面部件。

设计目标：
- 纯逻辑部分（GomokuEngine / GomokuAI / detect_threat / build_game_event）不依赖
  PyQt5，可脱离 GUI 运行单元测试。
- PyQt5 界面部件（GomokuBoardWidget / GomokuGamePanel）在 PyQt5 不可用时自动跳过，
  保证纯逻辑测试在任意 Python 环境中都能运行。
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

try:  # pragma: no cover - Qt 环境探测
    from PyQt5.QtCore import QPoint, QRect, QRectF, Qt, QThread, pyqtSignal
    from PyQt5.QtGui import QColor, QPainter, QPen, QRadialGradient
    from PyQt5.QtWidgets import (
        QComboBox,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )

    PYQT_AVAILABLE = True
except Exception:  # pragma: no cover - Qt 缺失时仅保留纯逻辑
    PYQT_AVAILABLE = False


STONE_EMPTY = 0
STONE_BLACK = 1
STONE_WHITE = 2

# 列坐标标签：按围棋惯例跳过字母 I
COLUMN_LABELS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"

DIRECTIONS: Tuple[Tuple[int, int], ...] = (
    (1, 0),
    (0, 1),
    (1, 1),
    (1, -1),
)

STAR_POINTS: Dict[int, Tuple[Tuple[int, int], ...]] = {
    9: ((2, 2), (2, 6), (4, 4), (6, 2), (6, 6)),
    13: ((3, 3), (3, 9), (6, 6), (9, 3), (9, 9)),
    19: (
        (3, 3),
        (3, 9),
        (3, 15),
        (9, 3),
        (9, 9),
        (9, 15),
        (15, 3),
        (15, 9),
        (15, 15),
    ),
}

# 局面评分常量（仅用于 AI 排序/搜索，不直接影响规则）
WIN_SCORE = 10 ** 8
LIVE_FOUR_SCORE = 10 ** 7
FOUR_SCORE = 10 ** 6
LIVE_THREE_SCORE = 10 ** 5
THREE_SCORE = 10 ** 4
LIVE_TWO_SCORE = 10 ** 3
TWO_SCORE = 10 ** 2

_INF = float("inf")


class _SearchTimeout(Exception):
    """AI 搜索超过时间预算时抛出的内部信号。"""


def _in_bounds(board: Sequence[Sequence[int]], r: int, c: int) -> bool:
    size = len(board)
    return 0 <= r < size and 0 <= c < size


def _win_at(board: Sequence[Sequence[int]], r: int, c: int) -> Optional[int]:
    """判断 (r, c) 处落子后是否形成五连及以上；返回棋子方，否则 None。"""
    player = board[r][c]
    if player == STONE_EMPTY:
        return None
    size = len(board)
    for dr, dc in DIRECTIONS:
        count = 1
        rr, cc = r + dr, c + dc
        while 0 <= rr < size and 0 <= cc < size and board[rr][cc] == player:
            count += 1
            rr += dr
            cc += dc
        rr, cc = r - dr, c - dc
        while 0 <= rr < size and 0 <= cc < size and board[rr][cc] == player:
            count += 1
            rr -= dr
            cc -= dc
        if count >= 5:
            return player
    return None


def _win_line(board: Sequence[Sequence[int]], r: int, c: int) -> Optional[List[Tuple[int, int]]]:
    """返回通过 (r, c) 的五连及以上连线坐标（含两端延伸），供 UI 高亮。"""
    player = board[r][c]
    if player == STONE_EMPTY:
        return None
    size = len(board)
    for dr, dc in DIRECTIONS:
        cells = [(r, c)]
        rr, cc = r + dr, c + dc
        while 0 <= rr < size and 0 <= cc < size and board[rr][cc] == player:
            cells.append((rr, cc))
            rr += dr
            cc += dc
        rr, cc = r - dr, c - dc
        while 0 <= rr < size and 0 <= cc < size and board[rr][cc] == player:
            cells.insert(0, (rr, cc))
            rr -= dr
            cc -= dc
        if len(cells) >= 5:
            return cells
    return None


def _direction_run(
    board: Sequence[Sequence[int]],
    r: int,
    c: int,
    dr: int,
    dc: int,
) -> Tuple[int, int, int]:
    """统计以 (r, c) 处棋子在某个方向上的连子长度与两端开口数。"""
    size = len(board)
    left = 0
    rr, cc = r - dr, c - dc
    while 0 <= rr < size and 0 <= cc < size and board[rr][cc] == board[r][c]:
        left += 1
        rr -= dr
        cc -= dc
    right = 0
    rr2, cc2 = r + dr, c + dc
    while 0 <= rr2 < size and 0 <= cc2 < size and board[rr2][cc2] == board[r][c]:
        right += 1
        rr2 += dr
        cc2 += dc
    open_left = 1 if (0 <= rr < size and 0 <= cc < size and board[rr][cc] == STONE_EMPTY) else 0
    open_right = 1 if (0 <= rr2 < size and 0 <= cc2 < size and board[rr2][cc2] == STONE_EMPTY) else 0
    return left + right + 1, open_left, open_right


def _run_score(length: int, open_left: int, open_right: int) -> int:
    """把方向连子长度与开口数映射为启发式分数。"""
    opens = open_left + open_right
    if length >= 5:
        return WIN_SCORE
    if length == 4:
        if opens == 2:
            return LIVE_FOUR_SCORE
        if opens == 1:
            return FOUR_SCORE
        return 0
    if length == 3:
        if opens == 2:
            return LIVE_THREE_SCORE
        if opens == 1:
            return THREE_SCORE
        return 0
    if length == 2:
        if opens == 2:
            return LIVE_TWO_SCORE
        if opens == 1:
            return TWO_SCORE
        return 0
    if length == 1:
        return 5 if opens == 2 else (1 if opens == 1 else 0)
    return 0


def detect_threat(
    board: Sequence[Sequence[int]],
    r: int,
    c: int,
    player: int,
) -> Optional[str]:
    """检测 (r, c) 处刚落下的子是否形成威胁，返回 '活四' / '冲四' / '活三' 之一。"""
    if not _in_bounds(board, r, c) or board[r][c] != player:
        return None
    best: Optional[str] = None
    for dr, dc in DIRECTIONS:
        length, open_left, open_right = _direction_run(board, r, c, dr, dc)
        opens = open_left + open_right
        if length >= 5:
            return "五连"
        if length == 4 and opens >= 1:
            label = "活四" if opens == 2 else "冲四"
            if label == "活四":
                return label
            best = label
        elif length == 3 and opens == 2 and best is None:
            best = "活三"
    return best


def build_game_event(
    event_type: str,
    board_size: int,
    coord: Optional[str] = None,
    threat: Optional[str] = None,
    record_text: str = "",
    player: Optional[str] = None,
    winner: Optional[str] = None,
    line: Optional[Sequence[Sequence[int]]] = None,
) -> Dict[str, object]:
    """构造面板事件字典（纯函数，供单元测试与前端点评共用）。"""
    return {
        "type": event_type,
        "board_size": board_size,
        "coord": coord,
        "threat": threat,
        "record_text": record_text,
        "player": player,
        "winner": winner,
        "line": line,
    }


class GomokuEngine:
    """五子棋棋盘规则引擎（自由规则：五子及以上连珠即胜）。"""

    VALID_SIZES = (9, 13, 19)

    def __init__(self, size: int = 13) -> None:
        self.size = int(size)
        self.board: List[List[int]] = []
        self.move_stack: List[Tuple[int, int, int]] = []
        self.current_player = STONE_BLACK
        self.reset(self.size)

    def reset(self, size: Optional[int] = None) -> None:
        if size is not None:
            self.size = int(size)
        self.board = [[STONE_EMPTY] * self.size for _ in range(self.size)]
        self.move_stack.clear()
        self.current_player = STONE_BLACK

    @property
    def move_count(self) -> int:
        return len(self.move_stack)

    def in_bounds(self, r: int, c: int) -> bool:
        return _in_bounds(self.board, r, c)

    def is_empty(self, r: int, c: int) -> bool:
        return self.in_bounds(r, c) and self.board[r][c] == STONE_EMPTY

    def place(self, r: int, c: int, player: Optional[int] = None) -> bool:
        """落子；成功返回 True。player 省略时使用当前轮到的一方。"""
        if not self.is_empty(r, c):
            return False
        stone = int(player) if player is not None else self.current_player
        if stone not in (STONE_BLACK, STONE_WHITE):
            return False
        self.board[r][c] = stone
        self.move_stack.append((r, c, stone))
        self.current_player = 3 - stone
        return True

    def undo(self) -> Optional[Tuple[int, int, int]]:
        """撤销最后一手；返回被撤销的 (行, 列, 棋子方)。"""
        if not self.move_stack:
            return None
        r, c, stone = self.move_stack.pop()
        self.board[r][c] = STONE_EMPTY
        self.current_player = stone
        return (r, c, stone)

    def check_five(self, r: int, c: int) -> Optional[Tuple[int, List[Tuple[int, int]]]]:
        """若 (r, c) 形成五连及以上，返回 (棋子方, 连线坐标)。"""
        if not self.in_bounds(r, c) or self.board[r][c] == STONE_EMPTY:
            return None
        winner = _win_at(self.board, r, c)
        if winner is None:
            return None
        return (winner, _win_line(self.board, r, c) or [(r, c)])

    def is_full(self) -> bool:
        return all(self.board[r][c] != STONE_EMPTY for r in range(self.size) for c in range(self.size))

    @staticmethod
    def coord_name(r: int, c: int) -> str:
        """坐标转文本，如 (6, 6) -> '7G'（列按围棋惯例跳过 I）。"""
        return f"{r + 1}{COLUMN_LABELS[c]}"

    def record_coords(self) -> List[str]:
        return [self.coord_name(r, c) for r, c, _ in self.move_stack]

    def record_text(self) -> str:
        return ", ".join(self.record_coords())


class GomokuAI:
    """五子棋 AI：立即取胜/防守 + 启发式候选 + 迭代加深 alpha-beta。"""

    MAX_DEPTH_BY_SIZE = {9: 6, 13: 5, 19: 4}
    CANDIDATES_BY_SIZE = {9: 8, 13: 10, 19: 12}

    def __init__(self, time_budget: float = 1.5) -> None:
        self.time_budget = float(time_budget)
        self._node_count = 0
        self._candidate_count = 10

    @staticmethod
    def _empty_near_stones(board: Sequence[Sequence[int]], radius: int = 2) -> List[Tuple[int, int]]:
        size = len(board)
        stones = [(r, c) for r in range(size) for c in range(size) if board[r][c] != STONE_EMPTY]
        if not stones:
            return []
        cells: set[Tuple[int, int]] = set()
        for sr, sc in stones:
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    rr, cc = sr + dr, sc + dc
                    if _in_bounds(board, rr, cc) and board[rr][cc] == STONE_EMPTY:
                        cells.add((rr, cc))
        return list(cells)

    @staticmethod
    def _point_score(board: Sequence[Sequence[int]], r: int, c: int, player: int) -> int:
        """模拟在 (r, c) 落 player 子后的四个方向启发式得分。"""
        mutable = board  # 调用方保证传入的是可写棋盘副本
        mutable[r][c] = player
        try:
            total = 0
            for dr, dc in DIRECTIONS:
                length, open_left, open_right = _direction_run(mutable, r, c, dr, dc)
                total += _run_score(length, open_left, open_right)
            return total
        finally:
            mutable[r][c] = STONE_EMPTY

    def _find_winning_move(self, board: List[List[int]], player: int) -> Optional[Tuple[int, int]]:
        for r, c in self._empty_near_stones(board):
            board[r][c] = player
            won = _win_at(board, r, c)
            board[r][c] = STONE_EMPTY
            if won:
                return (r, c)
        return None

    def _candidates(
        self,
        board: List[List[int]],
        player: int,
        top_n: int,
    ) -> List[Tuple[float, Tuple[int, int]]]:
        size = len(board)
        cells = self._empty_near_stones(board)
        if not cells:
            center = (size // 2, size // 2)
            return [(0.0, center)]
        opponent = 3 - player
        scored: List[Tuple[float, Tuple[int, int]]] = []
        for r, c in cells:
            offense = self._point_score(board, r, c, player)
            defense = self._point_score(board, r, c, opponent)
            scored.append((offense + defense * 1.1, (r, c)))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[:top_n]

    @staticmethod
    def _window_score(window: Sequence[int], player: int) -> int:
        count = 0
        for value in window:
            if value == player:
                count += 1
            elif value != STONE_EMPTY:
                return 0
        return (0, 1, 10, 1000, 100000, 10000000)[count]

    @classmethod
    def evaluate_board(cls, board: Sequence[Sequence[int]], ai_player: int) -> int:
        """静态评估：扫描全部五连窗口，AI 得分减对手得分。"""
        size = len(board)
        opponent = 3 - ai_player
        total = 0
        for r in range(size):
            for c in range(size - 4):
                window = [board[r][c + i] for i in range(5)]
                total += cls._window_score(window, ai_player) - cls._window_score(window, opponent)
        for c in range(size):
            for r in range(size - 4):
                window = [board[r + i][c] for i in range(5)]
                total += cls._window_score(window, ai_player) - cls._window_score(window, opponent)
        for r in range(size - 4):
            for c in range(size - 4):
                window = [board[r + i][c + i] for i in range(5)]
                total += cls._window_score(window, ai_player) - cls._window_score(window, opponent)
        for r in range(4, size):
            for c in range(size - 4):
                window = [board[r - i][c + i] for i in range(5)]
                total += cls._window_score(window, ai_player) - cls._window_score(window, opponent)
        return total

    def _search(
        self,
        board: List[List[int]],
        player: int,
        depth: int,
        alpha: float,
        beta: float,
        ai_player: int,
        deadline: float,
        last_move: Optional[Tuple[int, int]],
    ) -> Tuple[float, Optional[Tuple[int, int]]]:
        self._node_count += 1
        if self._node_count % 1024 == 0 and time.monotonic() > deadline:
            raise _SearchTimeout()

        if last_move is not None:
            winner = _win_at(board, last_move[0], last_move[1])
            if winner is not None:
                value = WIN_SCORE if winner == ai_player else -WIN_SCORE
                return (value, None)

        if depth == 0:
            return (self.evaluate_board(board, ai_player), None)

        opponent = 3 - player
        candidates = self._candidates(board, player, self._candidate_count)
        best_move: Optional[Tuple[int, int]] = None

        if player == ai_player:
            best_value = -_INF
            for _, (r, c) in candidates:
                board[r][c] = player
                value, _ = self._search(
                    board, opponent, depth - 1, alpha, beta, ai_player, deadline, (r, c)
                )
                board[r][c] = STONE_EMPTY
                if value > best_value:
                    best_value = value
                    best_move = (r, c)
                alpha = max(alpha, value)
                if alpha >= beta:
                    break
            return (best_value, best_move)

        best_value = _INF
        for _, (r, c) in candidates:
            board[r][c] = player
            value, _ = self._search(
                board, opponent, depth - 1, alpha, beta, ai_player, deadline, (r, c)
            )
            board[r][c] = STONE_EMPTY
            if value < best_value:
                best_value = value
                best_move = (r, c)
            beta = min(beta, value)
            if alpha >= beta:
                break
        return (best_value, best_move)

    def choose_move(self, board: List[List[int]], ai_player: int) -> Optional[Tuple[int, int]]:
        """为 ai_player 计算一步；无合法点返回 None。"""
        size = len(board)
        opponent = 3 - ai_player
        self._node_count = 0
        self._candidate_count = self.CANDIDATES_BY_SIZE.get(size, 10)

        # 1) 自己能一步取胜则立即取胜
        move = self._find_winning_move(board, ai_player)
        if move is not None:
            return move
        # 2) 对方能一步取胜则必须堵住
        move = self._find_winning_move(board, opponent)
        if move is not None:
            return move

        candidates = self._candidates(board, ai_player, self._candidate_count)
        if not candidates:
            return None

        best_move: Tuple[int, int] = candidates[0][1]
        if not any(board[r][c] != STONE_EMPTY for r in range(size) for c in range(size)):
            center = (size // 2, size // 2)
            if board[center[0]][center[1]] == STONE_EMPTY:
                return center

        max_depth = self.MAX_DEPTH_BY_SIZE.get(size, 4)
        deadline = time.monotonic() + self.time_budget
        try:
            for depth in range(2, max_depth + 1):
                if time.monotonic() > deadline:
                    break
                try:
                    value, move = self._search(
                        board, ai_player, depth, -_INF, _INF, ai_player, deadline, None
                    )
                except _SearchTimeout:
                    break
                if move is not None:
                    best_move = move
                if abs(value) >= WIN_SCORE // 2:
                    break
        except _SearchTimeout:
            pass
        return best_move


if PYQT_AVAILABLE:  # pragma: no cover - 需要 Qt 环境

    class GomokuBoardWidget(QWidget):
        """用 QPainter 绘制的五子棋棋盘。"""

        move_requested = pyqtSignal(int, int)

        def __init__(self, parent: Optional[QWidget] = None) -> None:
            super().__init__(parent)
            self._size = 13
            self._board: List[List[int]] = []
            self._last_move: Optional[Tuple[int, int]] = None
            self._win_line: Optional[List[Tuple[int, int]]] = None
            self._cell = 30
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.setFixedSize(self._pixel_size(), self._pixel_size())

        def _margin(self) -> int:
            return 28

        def _pixel_size(self) -> int:
            return self._margin() * 2 + self._cell * max(self._size - 1, 1)

        def set_board_state(
            self,
            board: Sequence[Sequence[int]],
            last_move: Optional[Tuple[int, int]] = None,
            win_line: Optional[Sequence[Sequence[int]]] = None,
        ) -> None:
            self._board = [[int(v) for v in row] for row in board]
            self._size = len(self._board)
            self._cell = min(30, 500 // max(self._size, 1))
            self._last_move = last_move
            self._win_line = (
                [(int(p[0]), int(p[1])) for p in win_line] if win_line is not None else None
            )
            self.setFixedSize(self._pixel_size(), self._pixel_size())
            self.update()

        def mousePressEvent(self, event) -> None:  # noqa: N802
            if not self._board:
                return
            margin = self._margin()
            col = round((event.pos().x() - margin) / self._cell)
            row = round((event.pos().y() - margin) / self._cell)
            if not (0 <= row < self._size and 0 <= col < self._size):
                return
            px = margin + col * self._cell
            py = margin + row * self._cell
            if abs(event.pos().x() - px) <= self._cell * 0.4 and abs(event.pos().y() - py) <= self._cell * 0.4:
                self.move_requested.emit(row, col)

        def paintEvent(self, event) -> None:  # noqa: N802
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.fillRect(self.rect(), QColor(222, 184, 135))
            margin = self._margin()
            size = self._size
            if size <= 0:
                painter.end()
                return
            line_color = QColor(80, 50, 20)
            painter.setPen(QPen(line_color, 1))
            for i in range(size):
                p = margin + i * self._cell
                painter.drawLine(margin, p, margin + (size - 1) * self._cell, p)
                painter.drawLine(p, margin, p, margin + (size - 1) * self._cell)

            painter.setBrush(line_color)
            painter.setPen(Qt.NoPen)
            for r, c in STAR_POINTS.get(size, ()):
                x = margin + c * self._cell
                y = margin + r * self._cell
                painter.drawEllipse(x - 3, y - 3, 6, 6)

            font = painter.font()
            font.setPointSize(8)
            painter.setFont(font)
            painter.setPen(QPen(line_color, 1))
            for i in range(size):
                letter = COLUMN_LABELS[i]
                painter.drawText(QRect(margin + i * self._cell - 8, 4, 16, 14), Qt.AlignCenter, letter)
                painter.drawText(
                    QRect(margin + i * self._cell - 8, self.height() - 18, 16, 14),
                    Qt.AlignCenter,
                    letter,
                )
                painter.drawText(QRect(2, margin + i * self._cell - 8, 20, 16), Qt.AlignCenter, str(i + 1))
                painter.drawText(
                    QRect(self.width() - 22, margin + i * self._cell - 8, 20, 16),
                    Qt.AlignCenter,
                    str(i + 1),
                )

            for r in range(size):
                for c in range(size):
                    stone = self._board[r][c]
                    if stone == STONE_EMPTY:
                        continue
                    x = margin + c * self._cell
                    y = margin + r * self._cell
                    radius = self._cell * 0.44
                    if stone == STONE_BLACK:
                        gradient = QRadialGradient(x - radius * 0.3, y - radius * 0.3, radius)
                        gradient.setColorAt(0, QColor(90, 90, 90))
                        gradient.setColorAt(1, QColor(10, 10, 10))
                    else:
                        gradient = QRadialGradient(x - radius * 0.3, y - radius * 0.3, radius)
                        gradient.setColorAt(0, QColor(255, 255, 255))
                        gradient.setColorAt(1, QColor(200, 200, 200))
                    painter.setBrush(gradient)
                    painter.setPen(QPen(QColor(0, 0, 0), 1))
                    painter.drawEllipse(QRectF(x - radius, y - radius, radius * 2, radius * 2))

            if self._last_move is not None and not self._win_line:
                r, c = self._last_move
                x = margin + c * self._cell
                y = margin + r * self._cell
                painter.setBrush(QColor(255, 60, 60))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(x - 3, y - 3, 6, 6)

            if self._win_line and len(self._win_line) >= 2:
                painter.setPen(QPen(QColor(255, 60, 60), 3))
                start = QPoint(
                    margin + self._win_line[0][1] * self._cell,
                    margin + self._win_line[0][0] * self._cell,
                )
                end = QPoint(
                    margin + self._win_line[-1][1] * self._cell,
                    margin + self._win_line[-1][0] * self._cell,
                )
                painter.drawLine(start, end)
            painter.end()

    class _AIThinkThread(QThread):
        """后台计算 AI 落子，避免阻塞界面线程。"""

        move_ready = pyqtSignal(int, int)

        def __init__(
            self,
            board_snapshot: Sequence[Sequence[int]],
            ai_player: int,
            time_budget: float,
            parent: Optional[QWidget] = None,
        ) -> None:
            super().__init__(parent)
            self._board = [[int(v) for v in row] for row in board_snapshot]
            self._ai_player = int(ai_player)
            self._time_budget = float(time_budget)

        def run(self) -> None:
            ai = GomokuAI(time_budget=self._time_budget)
            move: Optional[Tuple[int, int]] = None
            try:
                move = ai.choose_move(self._board, self._ai_player)
            except Exception:
                move = None
            if move is not None and not self.isInterruptionRequested():
                self.move_ready.emit(int(move[0]), int(move[1]))

    class GomokuGamePanel(QWidget):
        """内嵌于主聊天窗口的五子棋面板（人机对战）。"""

        def __init__(self, parent: Optional[QWidget] = None) -> None:
            super().__init__(parent)
            self.on_game_event: Optional[Callable[[Dict[str, object]], None]] = None
            self.on_manual_end: Optional[Callable[[], None]] = None
            self.engine = GomokuEngine(13)
            self.user_player = STONE_BLACK
            self.ai_player = STONE_WHITE
            self.game_active = False
            self.thinking = False
            self._ai_thread: Optional[_AIThinkThread] = None
            self._ai_budget = 1.5
            self._build_ui()
            self._refresh_controls()
            self.setVisible(False)

        def _build_ui(self) -> None:
            root = QVBoxLayout(self)
            header = QHBoxLayout()
            self.title_label = QLabel("五子棋")
            self.size_combo = QComboBox()
            for size in (9, 13, 19):
                self.size_combo.addItem(f"{size}路", size)
            self.size_combo.setCurrentIndex(1)
            self.new_btn = QPushButton("新开局")
            self.undo_btn = QPushButton("悔棋")
            self.resign_btn = QPushButton("认输")
            self.close_btn = QPushButton("结束")
            header.addWidget(self.title_label)
            header.addStretch(1)
            header.addWidget(QLabel("棋盘"))
            header.addWidget(self.size_combo)
            header.addWidget(self.new_btn)
            header.addWidget(self.undo_btn)
            header.addWidget(self.resign_btn)
            header.addWidget(self.close_btn)
            root.addLayout(header)
            self.board_widget = GomokuBoardWidget()
            root.addWidget(self.board_widget)
            self.status_label = QLabel("")
            root.addWidget(self.status_label)

            self.board_widget.move_requested.connect(self._on_user_move)  # noqa
            self.new_btn.clicked.connect(lambda: self.start_new_game())  # noqa
            self.undo_btn.clicked.connect(lambda: self.undo())  # noqa
            self.resign_btn.clicked.connect(lambda: self.resign())  # noqa
            self.close_btn.clicked.connect(lambda: self.close_game())  # noqa
            self.setStyleSheet(
                "QFrame, QWidget { background: transparent; }"
                "QLabel { color: #333333; }"
            )

        # ---------- 公共操作 ----------

        def start_new_game(self, board_size: Optional[int] = None) -> None:
            if board_size is not None and board_size in (9, 13, 19):
                index = self.size_combo.findData(int(board_size))
                if index >= 0:
                    self.size_combo.setCurrentIndex(index)
            size = int(self.size_combo.currentData())
            self._stop_ai_thread()
            self.engine.reset(size)
            self.user_player = STONE_BLACK
            self.ai_player = STONE_WHITE
            self.game_active = True
            self.thinking = False
            self._ai_budget = {9: 1.0, 13: 1.5, 19: 1.5}[size]
            self._refresh_board()
            self.status_label.setText("新开局：你是黑棋（先手），轮到你落子。")
            self._refresh_controls()
            self._emit_event("game_started")

        def undo(self) -> None:
            if not self.game_active or self.thinking:
                return
            if self.engine.move_count == 0:
                return
            self.engine.undo()  # 撤销对手最后一步
            if self.engine.move_count:
                self.engine.undo()  # 撤销自己最后一步
            self._refresh_board()
            self.status_label.setText("已悔棋，轮到你落子。")
            self._refresh_controls()

        def resign(self) -> None:
            if not self.game_active:
                return
            self.game_active = False
            self._stop_ai_thread()
            self.status_label.setText("你认输了。")
            self._emit_event("resigned")
            self._refresh_controls()

        def close_game(self) -> None:
            """手动结束：销毁对局、收起面板，并通知前端清空待发点评。"""
            self.game_active = False
            self._stop_ai_thread()
            if self.on_manual_end is not None:
                try:
                    self.on_manual_end()
                except Exception:
                    pass
            self.hide()

        def record_text(self) -> str:
            return self.engine.record_text()

        # ---------- 内部流程 ----------

        def _refresh_board(self) -> None:
            last_move = self.engine.move_stack[-1][:2] if self.engine.move_stack else None
            self.board_widget.set_board_state(self.engine.board, last_move, None)
            self.title_label.setText(f"五子棋 · {self.engine.size}路")

        def _refresh_controls(self) -> None:
            can_act = self.game_active and not self.thinking
            self.new_btn.setEnabled(True)
            self.undo_btn.setEnabled(can_act and self.engine.move_count > 0)
            self.resign_btn.setEnabled(can_act)
            self.close_btn.setEnabled(True)
            self.size_combo.setEnabled(not self.game_active)

        def _emit_event(self, event_type: str, **extra: object) -> None:
            if self.on_game_event is None:
                return
            event = build_game_event(
                event_type,
                self.engine.size,
                record_text=self.engine.record_text(),
                **extra,
            )
            try:
                self.on_game_event(event)
            except Exception:
                pass

        def _on_user_move(self, r: int, c: int) -> None:
            if not self.game_active or self.thinking:
                return
            if not self.engine.place(r, c, self.user_player):
                return
            self._refresh_board()
            if self._finish_move_if_needed(r, c, user_turn=True):
                return
            threat = detect_threat(self.engine.board, r, c, self.user_player)
            if threat:
                self._emit_event(
                    "threat",
                    coord=self.engine.coord_name(r, c),
                    threat=threat,
                    player="user",
                )
            self._start_ai_move()

        def _start_ai_move(self) -> None:
            if not self.game_active or self.thinking:
                return
            self.thinking = True
            self.status_label.setText("对手思考中…")
            self._refresh_controls()
            snapshot = [row[:] for row in self.engine.board]
            thread = _AIThinkThread(snapshot, self.ai_player, self._ai_budget, parent=self)
            thread.move_ready.connect(self._on_ai_move_ready)  # noqa
            self._ai_thread = thread
            thread.start()

        def _on_ai_move_ready(self, r: int, c: int) -> None:
            thread = self.sender()
            if not self.game_active:
                return
            if self._ai_thread is not None and thread is not None and self._ai_thread is not thread:
                return
            self.thinking = False
            if not self.engine.place(r, c, self.ai_player):
                self.status_label.setText("轮到你落子。")
                self._refresh_controls()
                return
            self._refresh_board()
            if self._finish_move_if_needed(r, c, user_turn=False):
                return
            threat = detect_threat(self.engine.board, r, c, self.ai_player)
            if threat:
                self._emit_event(
                    "threat",
                    coord=self.engine.coord_name(r, c),
                    threat=threat,
                    player="ai",
                )
            self.status_label.setText("轮到你落子。")
            self._refresh_controls()

        def _finish_move_if_needed(self, r: int, c: int, user_turn: bool) -> bool:
            """落子后统一处理胜负/和棋；返回 True 表示对局已结束。"""
            result = self.engine.check_five(r, c)
            if result is not None:
                self.game_active = False
                player, line = result
                self.board_widget.set_board_state(self.engine.board, (r, c), line)
                if user_turn:
                    self.status_label.setText("你赢了！")
                    self._emit_event(
                        "user_win",
                        coord=self.engine.coord_name(r, c),
                        winner="user",
                        line=[list(p) for p in line],
                    )
                else:
                    self.status_label.setText("对手获胜！")
                    self._emit_event(
                        "ai_win",
                        coord=self.engine.coord_name(r, c),
                        winner="ai",
                        line=[list(p) for p in line],
                    )
                self._refresh_controls()
                return True
            if self.engine.is_full():
                self.game_active = False
                self.status_label.setText("和棋")
                self._emit_event("draw")
                self._refresh_controls()
                return True
            return False

        def _stop_ai_thread(self) -> None:
            self.thinking = False
            thread = self._ai_thread
            self._ai_thread = None
            if thread is not None and thread.isRunning():
                thread.requestInterruption()
