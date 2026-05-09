from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Literal

from PyQt5.QtCore import QAbstractListModel, QModelIndex, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QListView,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
)

from chat.chat import Chat
from ui_main.components.character_avatar import build_initial_avatar


ChatSidebarMode = Literal["flat", "folded"]
ChatSidebarRowType = Literal["character_header", "chat_flat", "chat_child"]


class ChatSidebarRole(IntEnum):
    """
    定义聊天侧栏模型的自定义数据角色。
    """
    ROW_TYPE = int(Qt.UserRole) + 1
    CHAT_ID = int(Qt.UserRole) + 2
    CHARACTER_NAME = int(Qt.UserRole) + 3
    CHAT_TITLE = int(Qt.UserRole) + 4
    PREVIEW_TEXT = int(Qt.UserRole) + 5
    EXPANDED = int(Qt.UserRole) + 6
    ACTIVE = int(Qt.UserRole) + 7


@dataclass(frozen=True)
class ChatSidebarRow:
    """
    表示聊天侧栏中当前可见的一行。
    """
    row_type: ChatSidebarRowType
    chat_id: str | None
    character_name: str
    chat_title: str
    preview_text: str
    avatar_color: str
    expanded: bool
    active: bool


class ChatSidebarModel(QAbstractListModel):
    """
    为聊天侧栏提供平铺和折叠两种模式的扁平列表模型。
    """
    order_changed = pyqtSignal(list)
    expanded_changed = pyqtSignal(list)

    def __init__(self, parent: object | None = None) -> None:
        """
        初始化聊天侧栏模型。
        """
        super().__init__(parent)
        self._chats: list[Chat] = []
        self._rows: list[ChatSidebarRow] = []
        self._mode: ChatSidebarMode = "flat"
        self._current_chat_id = ""
        self._expanded_character_names: set[str] = set()
        self._character_theme_colors: dict[str, str] = {}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """
        返回当前可见行数量。
        """
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object | None:
        """
        返回指定行和角色的数据。
        """
        row = self.row_at(index)
        if row is None:
            return None
        if role == Qt.DisplayRole:
            return row.chat_title or row.preview_text or row.character_name
        if role == int(ChatSidebarRole.ROW_TYPE):
            return row.row_type
        if role == int(ChatSidebarRole.CHAT_ID):
            return row.chat_id
        if role == int(ChatSidebarRole.CHARACTER_NAME):
            return row.character_name
        if role == int(ChatSidebarRole.CHAT_TITLE):
            return row.chat_title
        if role == int(ChatSidebarRole.PREVIEW_TEXT):
            return row.preview_text
        if role == int(ChatSidebarRole.EXPANDED):
            return row.expanded
        if role == int(ChatSidebarRole.ACTIVE):
            return row.active
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        """
        返回侧栏行的交互标记。
        """
        if not index.isValid():
            if self._mode == "flat":
                return Qt.ItemIsDropEnabled
            return Qt.NoItemFlags
        default_flags = super().flags(index)
        row = self.row_at(index)
        if row is None:
            return default_flags
        if self._mode == "flat" and row.row_type == "chat_flat":
            return default_flags | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled
        return default_flags

    def supportedDropActions(self) -> Qt.DropActions:
        """
        返回模型支持的拖拽动作。
        """
        return Qt.MoveAction

    def supportedDragActions(self) -> Qt.DropActions:
        """
        返回模型支持的拖动动作。
        """
        return Qt.MoveAction

    def moveRows(
        self,
        source_parent: QModelIndex,
        source_row: int,
        count: int,
        destination_parent: QModelIndex,
        destination_child: int,
    ) -> bool:
        """
        在平铺模式下移动对话行并发出新的排序。
        """
        if source_parent.isValid() or destination_parent.isValid():
            return False
        if self._mode != "flat" or count != 1:
            return False
        if not (0 <= source_row < len(self._chats)):
            return False
        if not (0 <= destination_child <= len(self._chats)):
            return False
        if destination_child == source_row or destination_child == source_row + 1:
            return False

        adjusted_destination = destination_child
        if destination_child > source_row:
            adjusted_destination -= 1

        self.beginMoveRows(QModelIndex(), source_row, source_row, QModelIndex(), destination_child)
        chat = self._chats.pop(source_row)
        self._chats.insert(adjusted_destination, chat)
        row = self._rows.pop(source_row)
        self._rows.insert(adjusted_destination, row)
        self.endMoveRows()
        self.order_changed.emit([one_chat.chat_id for one_chat in self._chats])
        return True

    def set_chats(self, chats: list[Chat], current_chat_id: str) -> None:
        """
        设置侧栏需要展示的对话数据。
        """
        self.beginResetModel()
        self._chats = list(chats)
        self._current_chat_id = current_chat_id
        self._rebuild_rows(reset_model=False)
        self.endResetModel()

    def set_mode(self, mode: ChatSidebarMode) -> None:
        """
        切换侧栏展示模式。
        """
        if mode == self._mode:
            return
        self.beginResetModel()
        self._mode = mode
        self._rebuild_rows(reset_model=False)
        self.endResetModel()

    def mode(self) -> ChatSidebarMode:
        """
        返回当前侧栏展示模式。
        """
        return self._mode

    def set_expanded_character_names(self, character_names: list[str]) -> None:
        """
        设置折叠模式下展开的角色名称。
        """
        self.beginResetModel()
        self._expanded_character_names = set(character_names)
        self._rebuild_rows(reset_model=False)
        self.endResetModel()

    def set_character_theme_colors(self, character_theme_colors: dict[str, str]) -> None:
        """
        设置角色名称到主题色的映射。
        """
        self.beginResetModel()
        self._character_theme_colors = dict(character_theme_colors)
        self._rebuild_rows(reset_model=False)
        self.endResetModel()

    def expanded_character_names(self) -> list[str]:
        """
        返回当前展开的角色名称列表。
        """
        return sorted(self._expanded_character_names)

    def toggle_character(self, character_name: str) -> None:
        """
        展开或收起指定角色分组。
        """
        if character_name in self._expanded_character_names:
            self._expanded_character_names.remove(character_name)
        else:
            self._expanded_character_names.add(character_name)
        self.beginResetModel()
        self._rebuild_rows(reset_model=False)
        self.endResetModel()
        self.expanded_changed.emit(self.expanded_character_names())

    def row_at(self, index: QModelIndex) -> ChatSidebarRow | None:
        """
        根据索引获取侧栏行对象。
        """
        if not index.isValid():
            return None
        row_number = index.row()
        if not (0 <= row_number < len(self._rows)):
            return None
        return self._rows[row_number]

    def row_at_position(self, row_number: int) -> ChatSidebarRow | None:
        """
        根据行号获取侧栏行对象。
        """
        if not (0 <= row_number < len(self._rows)):
            return None
        return self._rows[row_number]

    def _rebuild_rows(self, reset_model: bool) -> None:
        """
        根据当前模式重建可见行。
        """
        if reset_model:
            self.beginResetModel()
        if self._mode == "flat":
            self._rows = [self._build_flat_row(chat) for chat in self._chats]
        else:
            self._rows = self._build_folded_rows()
        if reset_model:
            self.endResetModel()

    def _build_flat_row(self, chat: Chat) -> ChatSidebarRow:
        """
        构建平铺模式下的对话行。
        """
        character_name = self._chat_character_name(chat)
        return ChatSidebarRow(
            row_type="chat_flat",
            chat_id=chat.chat_id,
            character_name=character_name,
            chat_title=chat.name.strip() or "未命名对话",
            preview_text=self._preview_text(chat),
            avatar_color=self._avatar_color(character_name),
            expanded=False,
            active=chat.chat_id == self._current_chat_id,
        )

    def _build_folded_rows(self) -> list[ChatSidebarRow]:
        """
        构建折叠模式下的角色分组行和子对话行。
        """
        grouped_chat_ids: dict[str, list[Chat]] = {}
        character_order: list[str] = []
        for chat in self._chats:
            character_name = self._chat_character_name(chat)
            if character_name not in grouped_chat_ids:
                grouped_chat_ids[character_name] = []
                character_order.append(character_name)
            grouped_chat_ids[character_name].append(chat)

        rows: list[ChatSidebarRow] = []
        for character_name in character_order:
            chats = grouped_chat_ids[character_name]
            expanded = character_name in self._expanded_character_names
            active = any(chat.chat_id == self._current_chat_id for chat in chats)
            rows.append(
                ChatSidebarRow(
                    row_type="character_header",
                    chat_id=None,
                    character_name=character_name,
                    chat_title=character_name,
                    preview_text="",
                    avatar_color=self._avatar_color(character_name),
                    expanded=expanded,
                    active=active,
                )
            )
            if expanded:
                rows.extend(self._build_child_row(chat) for chat in chats)
        return rows

    def _build_child_row(self, chat: Chat) -> ChatSidebarRow:
        """
        构建折叠模式下的子对话行。
        """
        character_name = self._chat_character_name(chat)
        return ChatSidebarRow(
            row_type="chat_child",
            chat_id=chat.chat_id,
            character_name=character_name,
            chat_title=chat.name.strip() or "未命名对话",
            preview_text=self._preview_text(chat),
            avatar_color=self._avatar_color(character_name),
            expanded=False,
            active=chat.chat_id == self._current_chat_id,
        )

    def _chat_character_name(self, chat: Chat) -> str:
        """
        获取对话绑定角色名称，无法确定时返回兜底名称。
        """
        return chat.get_character_name() or "未知角色"

    def _avatar_color(self, character_name: str) -> str:
        """
        获取指定角色用于头像绘制的主题色。
        """
        return self._character_theme_colors.get(character_name, "")

    def _preview_text(self, chat: Chat) -> str:
        """
        获取对话最后一条消息预览。
        """
        if not chat.message_list:
            return "暂无消息"
        last_message = chat.message_list[-1]
        text = (last_message.translation or last_message.text).strip()
        return " ".join(text.split()) or "暂无消息"


class ChatSidebarDelegate(QStyledItemDelegate):
    """
    自绘聊天侧栏中的角色行和对话行。
    """

    def __init__(self, parent: object | None = None) -> None:
        """
        初始化聊天侧栏委托。
        """
        super().__init__(parent)
        self._text_color = QColor("#334155")
        self._secondary_text_color = QColor("#748294")
        self._border_color = QColor("#DCE8F0")
        self._child_border_color = QColor("#E9F1F6")
        self._hover_color = QColor("#F7FAFC")
        self._active_color = QColor("#EEF6FC")
        self._active_mark_color = QColor("#7799CC")

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """
        返回不同侧栏行的推荐尺寸。
        """
        row_type = self._row_type(index)
        if row_type == "chat_child":
            return QSize(option.rect.width(), 46)
        if row_type == "character_header":
            return QSize(option.rect.width(), 62)
        return QSize(option.rect.width(), 64)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """
        绘制侧栏行。
        """
        row = self._row(index)
        if row is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        if row.row_type == "character_header":
            self._paint_character_row(painter, option, row)
        elif row.row_type == "chat_flat":
            self._paint_flat_chat_row(painter, option, row)
        else:
            self._paint_child_chat_row(painter, option, row)
        painter.restore()

    def _paint_character_row(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        row: ChatSidebarRow,
    ) -> None:
        """
        绘制折叠模式中的角色标题行。
        """
        rect = option.rect.adjusted(2, 4, -2, -4)
        self._paint_background(painter, rect, row.active, option)
        avatar_size = min(44, rect.height() - 12)
        avatar_rect = QRect(rect.left() + 10, rect.top() + (rect.height() - avatar_size) // 2, avatar_size, avatar_size)
        self._paint_avatar(painter, avatar_rect, row.character_name, row.avatar_color)

        arrow_rect = QRect(rect.right() - 32, rect.top(), 26, rect.height())
        text_rect = QRect(avatar_rect.right() + 10, rect.top(), arrow_rect.left() - avatar_rect.right() - 16, rect.height())
        self._paint_single_line_text(painter, text_rect, row.character_name, 16, True, self._text_color, Qt.AlignVCenter)
        arrow = "⌃" if row.expanded else "⌄"
        self._paint_single_line_text(painter, arrow_rect, arrow, 18, True, self._secondary_text_color, Qt.AlignCenter)

    def _paint_flat_chat_row(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        row: ChatSidebarRow,
    ) -> None:
        """
        绘制平铺模式中的对话行。
        """
        rect = option.rect.adjusted(2, 4, -2, -4)
        self._paint_background(painter, rect, row.active, option)
        avatar_size = min(42, rect.height() - 12)
        avatar_rect = QRect(rect.left() + 10, rect.top() + (rect.height() - avatar_size) // 2, avatar_size, avatar_size)
        self._paint_avatar(painter, avatar_rect, row.character_name, row.avatar_color)

        text_left = avatar_rect.right() + 12
        text_width = max(10, rect.right() - text_left - 10)
        title_rect = QRect(text_left, rect.top() + 9, text_width, 20)
        preview_rect = QRect(text_left, title_rect.bottom() + 3, text_width, 20)
        self._paint_single_line_text(painter, title_rect, row.chat_title, 14, True, self._text_color, Qt.AlignVCenter)
        self._paint_single_line_text(painter, preview_rect, row.preview_text, 13, False, self._secondary_text_color, Qt.AlignVCenter)

    def _paint_child_chat_row(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        row: ChatSidebarRow,
    ) -> None:
        """
        绘制折叠模式中的子对话行。
        """
        rect = option.rect.adjusted(30, 4, -2, -4)
        self._paint_background(painter, rect, row.active, option, light=True)
        text_rect = rect.adjusted(14, 0, -12, 0)
        self._paint_single_line_text(painter, text_rect, row.preview_text, 13, False, self._secondary_text_color, Qt.AlignVCenter)

    def _paint_background(
        self,
        painter: QPainter,
        rect: QRect,
        active: bool,
        option: QStyleOptionViewItem,
        light: bool = False,
    ) -> None:
        """
        绘制行背景和当前会话强调条。
        """
        if active:
            background = self._active_color
        elif option.state & QStyle.State_MouseOver:
            background = self._hover_color
        elif light:
            background = QColor("#FBFDFE")
        else:
            background = QColor("#FFFFFF")

        border_color = self._child_border_color if light else self._border_color
        painter.setPen(QPen(border_color, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 7, 7)
        if active:
            mark_rect = QRect(rect.left(), rect.top() + 8, 3, rect.height() - 16)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._active_mark_color)
            painter.drawRoundedRect(mark_rect, 2, 2)

    def _paint_avatar(self, painter: QPainter, rect: QRect, character_name: str, avatar_color: str) -> None:
        """
        绘制角色首字符头像。
        """
        device = painter.device()
        device_pixel_ratio = float(device.devicePixelRatioF()) if hasattr(device, "devicePixelRatioF") else 1.0
        avatar = build_initial_avatar(character_name, rect.width(), device_pixel_ratio, avatar_color)
        painter.drawPixmap(rect, avatar)

    def _paint_single_line_text(
        self,
        painter: QPainter,
        rect: QRect,
        text: str,
        pixel_size: int,
        bold: bool,
        color: QColor,
        alignment: Qt.Alignment,
    ) -> None:
        """
        绘制单行省略文本。
        """
        font = QFont(painter.font())
        font.setPixelSize(pixel_size)
        font.setBold(bold)
        painter.setFont(font)
        painter.setPen(color)
        metrics = QFontMetrics(font)
        elided_text = metrics.elidedText(text, Qt.ElideRight, max(0, rect.width()))
        painter.drawText(rect, int(alignment), elided_text)

    def _row(self, index: QModelIndex) -> ChatSidebarRow | None:
        """
        从模型索引中读取侧栏行对象。
        """
        model = index.model()
        if not isinstance(model, ChatSidebarModel):
            return None
        return model.row_at(index)

    def _row_type(self, index: QModelIndex) -> str:
        """
        从模型索引中读取侧栏行类型。
        """
        row = self._row(index)
        if row is None:
            return ""
        return row.row_type


class ChatSidebarView(QListView):
    """
    封装聊天侧栏列表视图的点击、右键和拖拽行为。
    """
    chat_selected = pyqtSignal(str)
    chat_menu_requested = pyqtSignal(str, QPoint)
    chat_order_changed = pyqtSignal(list)
    expanded_characters_changed = pyqtSignal(list)

    def __init__(self, parent: object | None = None) -> None:
        """
        初始化聊天侧栏列表视图。
        """
        super().__init__(parent)
        self._model = ChatSidebarModel(self)
        self.setModel(self._model)
        self.setItemDelegate(ChatSidebarDelegate(self))
        self.setMouseTracking(True)
        self.setSpacing(2)
        self.setUniformItemSizes(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.clicked.connect(self._handle_clicked)
        self.customContextMenuRequested.connect(self._handle_context_menu_requested)
        self._model.order_changed.connect(self.chat_order_changed)
        self._model.expanded_changed.connect(self.expanded_characters_changed)
        self.setStyleSheet(
            """
            QListView {
                background: transparent;
                border: none;
                outline: none;
            }
            """
        )

    def set_chats(self, chats: list[Chat], current_chat_id: str) -> None:
        """
        设置侧栏展示的对话数据。
        """
        self._model.set_chats(chats, current_chat_id)
        self._select_current_chat_row(current_chat_id)

    def set_mode(self, mode: ChatSidebarMode) -> None:
        """
        切换侧栏展示模式。
        """
        self._model.set_mode(mode)
        self.setDragEnabled(mode == "flat")
        self.setAcceptDrops(mode == "flat")

    def mode(self) -> ChatSidebarMode:
        """
        返回当前侧栏展示模式。
        """
        return self._model.mode()

    def set_expanded_character_names(self, character_names: list[str]) -> None:
        """
        设置折叠模式下展开的角色名称。
        """
        self._model.set_expanded_character_names(character_names)

    def set_character_theme_colors(self, character_theme_colors: dict[str, str]) -> None:
        """
        设置角色名称到主题色的映射。
        """
        self._model.set_character_theme_colors(character_theme_colors)

    def expanded_character_names(self) -> list[str]:
        """
        返回当前展开的角色名称。
        """
        return self._model.expanded_character_names()

    def _handle_clicked(self, index: QModelIndex) -> None:
        """
        处理侧栏行点击。
        """
        row = self._model.row_at(index)
        if row is None:
            return
        if row.row_type == "character_header":
            self._model.toggle_character(row.character_name)
        elif row.chat_id is not None:
            self.chat_selected.emit(row.chat_id)

    def _handle_context_menu_requested(self, pos: QPoint) -> None:
        """
        处理侧栏右键菜单请求。
        """
        index = self.indexAt(pos)
        row = self._model.row_at(index)
        if row is None or row.chat_id is None:
            return
        self.chat_menu_requested.emit(row.chat_id, self.mapToGlobal(pos))

    def _select_current_chat_row(self, current_chat_id: str) -> None:
        """
        根据当前对话 ID 更新列表选中行。
        """
        for row_number in range(self._model.rowCount()):
            row = self._model.row_at_position(row_number)
            if row is not None and row.chat_id == current_chat_id:
                self.setCurrentIndex(self._model.index(row_number, 0))
                return
        self.clearSelection()
