"""额度查询的桌面控制器和原生 Qt 聊天入口。"""
from __future__ import annotations

import dataclasses
from datetime import datetime
import json
import threading
from typing import Callable

from PyQt5.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QResizeEvent
from PyQt5.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from chat.api_usage import (
    ApiTarget, ApiUsageConfig, ApiUsageService, UsageError, UsageSnapshot, resolve_target,
)
from chat.api_usage_tool import UsageSnapshotView, bind_reader, config_fingerprint, unbind_reader
from ui_main.components.api_usage_balance import UsageBalanceView
from ui_main.theme import DEFAULT_CHARACTER_THEME_SEED, ThemePalette, build_dialog_theme_stylesheet, derive_theme_palette


def snapshot_text(snapshot: UsageSnapshot) -> str:
    """以纯文本显示额度，空值和零余额保持可辨认。"""
    lines = []
    if snapshot.status == "loading":
        lines.append("正在查询…")
    if snapshot.stale:
        lines.append("以下为上次成功查询的数据，当前刷新未成功。")
    if snapshot.error:
        lines.append(snapshot.error)
    for entry in snapshot.entries:
        lines.append(entry.planName or "额度明细")
        if not entry.isValid:
            lines.append(entry.invalidMessage or "账户当前不可用")
        for field, title in (("remaining", "剩余"), ("used", "已使用"), ("total", "总额度"),
                             ("toppedUpBalance", "充值余额"), ("grantedBalance", "赠金")):
            value = getattr(entry, field)
            if value is not None:
                lines.append(f"{title}：{value:g} {entry.unit or ''}".rstrip())
        if entry.remaining is None:
            lines.append("服务商未提供剩余额度数值。")
        if entry.extra:
            lines.append(entry.extra)
    for value, title in ((snapshot.checked_at, "最后查询"), (snapshot.last_success_at, "最后成功")):
        if value is not None:
            lines.append(f"{title}：{datetime.fromtimestamp(value):%m-%d %H:%M:%S}")
    return "\n".join(lines) or "尚未查询。"


class ApiUsageController(QObject):
    """Qt 仅调度轮询；网络与脚本均在额度服务后台执行。"""
    snapshotChanged = pyqtSignal(object)
    _delivered = pyqtSignal(object)

    def __init__(self, config: object, parent=None, service: ApiUsageService | None = None,
                 *, polling: bool = True) -> None:
        super().__init__(parent)
        self.config = config
        self.service = service or ApiUsageService()
        self.target = resolve_target(config)
        self.usage_config = ApiUsageConfig()
        self.snapshot = UsageSnapshot()
        self._revision = ""
        self._closed = False
        self._view_lock = threading.RLock()
        self._view = UsageSnapshotView(fingerprint="", provider=self.target.name)
        self._reader_token = None
        self.polling = polling
        self._delivered.connect(self._accept)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)
        self.reload_timer = QTimer(self)
        self.reload_timer.setSingleShot(True)
        self.reload_timer.timeout.connect(self.reload)
        for name in ("use_default_deepseek_api", "enable_custom_llm_api_provider", "llm_api_provider",
                     "llm_api_base_url", "llm_api_key", "custom_llm_api_url", "custom_llm_api_key",
                     "api_usage_configs"):
            item = getattr(config, name)
            item.valueChanged.connect(self.schedule_reload)
        self._publish()
        self._reader_token = bind_reader(self._tool_view)
        QTimer.singleShot(0, self.reload)

    def _publish(self) -> None:
        """发布一个全新的不可变视图；工具线程与 UI 线程都只读取这一份引用。"""
        view = UsageSnapshotView(fingerprint=self._revision, provider=self.target.name,
                                 snapshot=self.snapshot, enabled=self.usage_config.enabled,
                                 shared=self.target.shared)
        with self._view_lock:
            self._view = view

    def _tool_view(self) -> UsageSnapshotView:
        """给聊天工具线程的只读视图；不操作 Qt，配置一变化立即失效。"""
        if self._closed:
            # 已关闭与“配置变化”必须分开：退出语义是未就绪，不是正在刷新。
            return None
        try:
            target = resolve_target(self.config)
            usage = self.read_config(target)
            usage.validate()
            fingerprint = config_fingerprint(target, usage)
        except Exception:
            with self._view_lock:
                provider = self._view.provider
            return UsageSnapshotView(fingerprint="", provider=provider, snapshot=UsageSnapshot(
                status="error", error="当前额度配置无法读取，请检查 API 设置。"))
        with self._view_lock:
            view = self._view
            closed = self._closed
        if closed:
            # 关闭可能发生在配置读取窗口内；返回前再确认一次，退出语义必须是未就绪。
            return None
        if fingerprint != view.fingerprint:
            return UsageSnapshotView(fingerprint=fingerprint, provider=target.name, pending=True,
                snapshot=UsageSnapshot(status="loading", error="配置已变化，正在重新读取额度。"))
        return view

    def schedule_reload(self, _value=None) -> None:
        if not self._closed:
            self.reload_timer.start(30)

    def read_config(self, target: ApiTarget) -> ApiUsageConfig:
        values = self.config.api_usage_configs.value or {}
        if not isinstance(values, dict):
            raise UsageError("额度查询配置格式错误，请重新保存配置。")
        return ApiUsageConfig.from_dict(values.get(target.config_key), target)

    def reload(self) -> None:
        if self._closed:
            return
        target = resolve_target(self.config)
        try:
            usage = self.read_config(target)
            usage.validate()
        except (UsageError, ValueError, TypeError):
            self.timer.stop()
            self.service.cancel()
            self._revision = "invalid"
            self.snapshot = UsageSnapshot("error", error="额度查询配置格式错误，请重新保存配置。")
            self._publish()
            self.snapshotChanged.emit(self.snapshot)
            return
        revision = config_fingerprint(target, usage)
        if revision == self._revision:
            return
        self._revision = revision
        self.timer.stop()
        self.service.cancel()
        self.target, self.usage_config = target, usage
        self.snapshot = UsageSnapshot(error="尚未查询。")
        self._publish()
        self.snapshotChanged.emit(self.snapshot)
        self.refresh()

    def refresh(self, *, manual: bool = False) -> None:
        """自动查询遵循开关，用户主动查询无需先开启自动刷新。"""
        if self._closed or not self.polling:
            return
        revision = self._revision

        def deliver(snapshot: UsageSnapshot) -> None:
            """将结果投递给启动查询时的配置版本。"""
            if not self._closed:
                self._delivered.emit((revision, snapshot))

        usage = dataclasses.replace(self.usage_config, enabled=True) if manual else self.usage_config
        self.service.query(self.target, usage, deliver)

    def refresh_manual(self) -> None:
        """只刷新当前余额，不改变自动查询偏好。"""
        self.refresh(manual=True)

    def _accept(self, value) -> None:
        revision, snapshot = value
        if self._closed or revision != self._revision:
            return
        self.snapshot = snapshot
        self._publish()
        self.snapshotChanged.emit(snapshot)
        self.timer.stop()
        if (snapshot.status not in {"loading", "needs_confirmation", "disabled"}
                and self.usage_config.enabled and self.usage_config.auto_interval):
            self.timer.start(self.usage_config.auto_interval * 60_000)

    def save(self, target: ApiTarget, config: ApiUsageConfig, overrides: dict[str, str]) -> bool:
        """只持久化专用凭据引用；返回凭据是否已存入系统凭据库。"""
        config.validate()
        values = config.to_dict()
        persisted = True
        for field in ("api_key", "access_token"):
            reference = target.config_key + ":" + field
            value = overrides.get(field, "")
            persisted = self.service.secrets.put(reference, value) and persisted
            values[field + "_ref"] = reference if value else ""
        saved = self.config.api_usage_configs.value
        profiles = dict(saved) if isinstance(saved, dict) else {}
        # 专用凭据使用固定引用，版本号让跨进程配置重载仍能察觉凭据更新。
        try:
            values["revision"] = self.read_config(target).revision + 1
        except (UsageError, ValueError, TypeError):
            values["revision"] = 1
        profiles[target.config_key] = values
        self.config.set(self.config.api_usage_configs, profiles)
        self.schedule_reload()
        return persisted

    def approve_current(self, parent: QWidget) -> None:
        target_origin = self.snapshot.confirmation_origin
        if not target_origin:
            return
        if confirm_origin(parent, target_origin):
            profiles = dict(self.config.api_usage_configs.value or {})
            approved = tuple(dict.fromkeys((*self.usage_config.approved_origins, target_origin)))
            profiles[self.target.config_key] = dataclasses.replace(
                self.usage_config, approved_origins=approved).to_dict()
            self.config.set(self.config.api_usage_configs, profiles)
            self.reload()
            if not self.usage_config.enabled:
                self.refresh_manual()

    def close(self) -> None:
        self._closed = True
        self.timer.stop()
        self.reload_timer.stop()
        unbind_reader(self._reader_token)
        self._reader_token = None
        self.service.close()


def confirm_origin(parent: QWidget, target_origin: str) -> bool:
    return QMessageBox.question(parent, "确认额度查询地址",
        f"此查询将向以下地址发送配置的查询凭据：\n{target_origin}\n\n确认后允许此配置的后台刷新使用该地址。",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


def get_api_usage_controller(*, polling: bool = True) -> ApiUsageController:
    """整个桌面进程共用一个额度查询实例，不创建第二条聊天通道。"""
    app = QApplication.instance()
    controller = getattr(app, "_api_usage_controller", None)
    if controller is None:
        from qconfig import d_sakiko_config
        controller = ApiUsageController(d_sakiko_config, app, polling=polling)
        app._api_usage_controller = controller
        app.aboutToQuit.connect(controller.close)
    elif polling and not controller.polling:
        controller.polling = True
        controller.refresh()
    return controller


class ApiUsageButton(QToolButton):
    """紧凑额度入口；窄窗口只显示“额度”，详细信息在弹窗内。"""
    settingsRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, *, height: int = 30,
                 controller: ApiUsageController | None = None) -> None:
        """创建与上下文 Token 指示器独立的余额入口。"""
        super().__init__(parent)
        self.controller = controller or get_api_usage_controller()
        self.setObjectName("apiUsageButton")
        self.setAccessibleName("当前 API 额度")
        self.setFixedHeight(height)
        self.setMaximumWidth(150)
        self._dialog: QDialog | None = None
        self._palette = derive_theme_palette(DEFAULT_CHARACTER_THEME_SEED)
        self.clicked.connect(self.show_details)
        self.controller.snapshotChanged.connect(self.update_snapshot)
        self.update_snapshot(self.controller.snapshot)

    def set_theme_palette(self, palette: ThemePalette) -> None:
        """将角色主题同步到入口和已打开的余额弹窗。"""
        self._palette = palette
        self.setStyleSheet(f"QToolButton {{ color:{palette.text_accent}; background:{palette.surface_tint};"
                          f"border:0; border-radius:8px; padding:0 7px; }}"
                          f"QToolButton:hover {{ background:{palette.surface_selected}; }}")
        if self._dialog is not None:
            self._dialog.setStyleSheet(build_dialog_theme_stylesheet(palette))
            self._balance_view.set_theme_palette(palette)

    def update_snapshot(self, snapshot: UsageSnapshot) -> None:
        """更新紧凑摘要与按币种组织的明细，旧数据状态保持可见。"""
        text = self.controller.target.name + " 额度"
        if len(snapshot.entries) == 1 and snapshot.entries[0].remaining is not None:
            entry = snapshot.entries[0]
            text = f"{self.controller.target.name} {entry.remaining:g} {entry.unit or ''}"
        if self.parentWidget() is not None and self.parentWidget().width() < 580:
            text = "额度"
        self.setText(self.fontMetrics().elidedText(text, Qt.ElideRight, 136))
        self.setToolTip(self.controller.target.name + "\n" + snapshot_text(snapshot))
        if self._dialog is not None:
            self._provider.setText(self.controller.target.name)
            self._balance_view.set_snapshot(snapshot)
            if self.controller.target.shared:
                self._balance_view.empty.setText("作者共用 API 不展示共用账户额度。\n请在 API 设置中配置自己的账户。")
            self._refresh.setEnabled(snapshot.status != "loading" and not self.controller.target.shared)
            self._refresh.setText("正在刷新…" if snapshot.status == "loading" else "刷新余额")
            self._confirm.setVisible(snapshot.status == "needs_confirmation")

    def resizeEvent(self, event: QResizeEvent) -> None:
        """入口尺寸变化后重新计算摘要长度。"""
        super().resizeEvent(event)
        self.update_snapshot(self.controller.snapshot)

    def _approve_origin(self) -> None:
        """在当前余额弹窗中确认额外的查询目标。"""
        if self._dialog is not None:
            self.controller.approve_current(self._dialog)

    def show_details(self) -> None:
        """展开余额卡片，首次手动查看时直接查询当前账户。"""
        if self._dialog is None:
            self._dialog = QDialog(self)
            self._dialog.setWindowTitle("当前 API 额度")
            self._dialog.resize(430, 400)
            self._dialog.setMinimumWidth(340)
            layout = QVBoxLayout(self._dialog)
            layout.setContentsMargins(24, 22, 24, 20)
            layout.setSpacing(16)
            self._provider = QLabel(self._dialog)
            self._provider.setTextFormat(Qt.PlainText)
            font = self._provider.font()
            font.setPointSize(17)
            font.setBold(True)
            self._provider.setFont(font)
            layout.addWidget(self._provider)
            scroll = QScrollArea(self._dialog)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            self._balance_view = UsageBalanceView(scroll)
            scroll.setWidget(self._balance_view)
            layout.addWidget(scroll, 1)
            row = QHBoxLayout()
            self._settings = QPushButton("API 设置", self._dialog)
            self._settings.clicked.connect(self.settingsRequested)
            row.addWidget(self._settings)
            row.addStretch(1)
            self._refresh = QPushButton("刷新余额")
            self._refresh.clicked.connect(self.controller.refresh_manual)
            self._confirm = QPushButton("确认查询地址")
            self._confirm.clicked.connect(self._approve_origin)
            row.addWidget(self._confirm)
            row.addWidget(self._refresh)
            layout.addLayout(row)
            self.set_theme_palette(self._palette)
        self.update_snapshot(self.controller.snapshot)
        self._dialog.show()
        self._dialog.raise_()
        snapshot = self.controller.snapshot
        if not self.controller.target.shared and snapshot.status != "loading" and not snapshot.entries:
            self.controller.refresh_manual()
