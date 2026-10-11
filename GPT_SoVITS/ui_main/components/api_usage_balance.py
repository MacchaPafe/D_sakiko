"""使用原生 Qt 展示账户额度，金额、赠金和查询状态各有独立层级。"""
from __future__ import annotations

from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from chat.api_usage import UsageEntry, UsageSnapshot
from ui_main.theme import DEFAULT_CHARACTER_THEME_SEED, ThemePalette, derive_theme_palette


def balance_label(text: str, role: str, parent: QWidget) -> QLabel:
    """创建始终按纯文本渲染的余额标签，避免服务商文本被解释为 HTML。"""
    label = QLabel(text, parent)
    label.setProperty("usageRole", role)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


def format_amount(value: float | None, unit: str | None = None) -> str:
    """保留零值与小额余额，货币金额至少显示两位小数。"""
    if value is None:
        return "—"
    text = f"{value:,.8f}".rstrip("0").rstrip(".")
    if value and float(f"{value:.8f}") == 0:
        return f"{value:.3g}"
    if unit in {"CNY", "USD"}:
        if "." not in text:
            text += ".00"
        elif len(text.rsplit(".", 1)[1]) == 1:
            text += "0"
    return text


class UsageBalanceCard(QFrame):
    """单个币种独立成卡，以余额为主、充值与赠金为辅。"""

    def __init__(self, entry: UsageEntry, parent: QWidget | None = None) -> None:
        """建立金额及明细层级，不通过解析说明文字获取赠金。"""
        super().__init__(parent)
        self.setObjectName("usageBalanceCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        layout.addWidget(balance_label(entry.planName or "可用额度", "caption", self))
        amount_row = QHBoxLayout()
        amount_row.setSpacing(10)
        self.amount = balance_label(format_amount(entry.remaining, entry.unit), "amount", self)
        font = QFont(self.font())
        font.setPointSize(30 if len(self.amount.text()) <= 12 else 22)
        font.setWeight(QFont.DemiBold)
        self.amount.setFont(font)
        self.amount.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        amount_row.addWidget(self.amount, 1)
        self.unit = balance_label(entry.unit or "", "unit", self)
        amount_row.addWidget(self.unit, 0, Qt.AlignBottom)
        layout.addLayout(amount_row)
        if entry.remaining is None:
            message = "未设置密钥限额" if entry.planName == "密钥额度" and entry.total is None else "服务商未提供剩余额度"
            layout.addWidget(balance_label(message, "secondary", self))

        self.bonus = balance_label("", "bonus", self)
        if entry.grantedBalance is not None and entry.grantedBalance > 0:
            self.bonus.setText(f"含赠金 {format_amount(entry.grantedBalance, entry.unit)} {entry.unit or ''}".rstrip())
        self.bonus.setVisible(bool(self.bonus.text()))
        layout.addWidget(self.bonus)
        details: list[str] = []
        for value, title in ((entry.toppedUpBalance, "充值余额"), (entry.used, "已用"), (entry.total, "总额度")):
            if value is not None:
                details.append(f"{title}  {format_amount(value, entry.unit)} {entry.unit or ''}".rstrip())
        if details:
            layout.addWidget(balance_label("   ·   ".join(details), "secondary", self))
        if entry.extra:
            layout.addWidget(balance_label(entry.extra, "secondary", self))
        if not entry.isValid:
            layout.addWidget(balance_label(entry.invalidMessage or "账户当前不可用", "notice", self))


class UsageBalanceView(QWidget):
    """按币种排列余额卡片，并单独显示加载、过期、错误与更新时间。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建可复用的纯 Qt 余额视图。"""
        super().__init__(parent)
        self.setObjectName("usageBalanceView")
        self.cards: list[UsageBalanceCard] = []
        self._entries: tuple[UsageEntry, ...] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.status = balance_label("", "notice", self)
        layout.addWidget(self.status)
        self.cards_layout = QVBoxLayout()
        self.cards_layout.setSpacing(12)
        layout.addLayout(self.cards_layout)
        self.empty = balance_label("正在获取账户额度…", "secondary", self)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(100)
        layout.addWidget(self.empty)
        self.updated = balance_label("", "timestamp", self)
        layout.addWidget(self.updated)
        layout.addStretch(1)
        self.set_theme_palette(derive_theme_palette(DEFAULT_CHARACTER_THEME_SEED))

    def set_theme_palette(self, palette: ThemePalette) -> None:
        """跟随聊天角色主题，确保错误和辅助信息仍有足够对比度。"""
        self.setStyleSheet(f"""
            QWidget#usageBalanceView {{ background: transparent; }}
            QFrame#usageBalanceCard {{ background: {palette.surface}; border: 1px solid {palette.border_subtle}; border-radius: 14px; }}
            QLabel {{ background: transparent; border: none; color: {palette.text_primary}; padding: 0; }}
            QLabel[usageRole="amount"] {{ color: {palette.text_accent}; }}
            QLabel[usageRole="caption"], QLabel[usageRole="secondary"], QLabel[usageRole="unit"] {{ color: {palette.text_secondary}; font-size: 13px; }}
            QLabel[usageRole="bonus"] {{ color: {palette.text_accent}; font-size: 14px; }}
            QLabel[usageRole="timestamp"] {{ color: {palette.text_secondary}; font-size: 11px; }}
            QLabel[usageRole="notice"] {{ color: {palette.text_primary}; font-size: 13px; }}
        """)

    def set_snapshot(self, snapshot: UsageSnapshot) -> None:
        """保留刷新中的余额，失败时明确标注旧数据而不伪造零值。"""
        if snapshot.entries != self._entries:
            while self.cards_layout.count():
                item = self.cards_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
            self.cards = []
            for entry in snapshot.entries:
                card = UsageBalanceCard(entry, self)
                self.cards.append(card)
                self.cards_layout.addWidget(card)
            self._entries = snapshot.entries
        messages: list[str] = []
        if snapshot.status == "loading":
            messages.append("正在刷新…")
        if snapshot.stale:
            messages.append("刷新未成功，当前显示上次查询的余额。")
        if snapshot.error and snapshot.status != "disabled":
            messages.append(snapshot.error)
        self.status.setText("\n".join(messages))
        self.status.setVisible(bool(messages))
        self.empty.setVisible(not snapshot.entries)
        if snapshot.status == "loading":
            self.empty.setText("正在获取账户额度…")
        elif snapshot.status == "disabled":
            self.empty.setText("点击刷新，查询当前账户额度。")
        else:
            self.empty.setText("暂未取得额度数据")
        self.updated.setVisible(snapshot.last_success_at is not None)
        if snapshot.last_success_at is not None:
            timestamp = datetime.fromtimestamp(snapshot.last_success_at)
            self.updated.setText(f"更新于 {timestamp:%H:%M} · " + ("上次成功结果" if snapshot.stale else "服务商返回的账户数据"))
            self.updated.setToolTip(f"最后成功查询：{timestamp:%Y-%m-%d %H:%M:%S}")
