"""额度查询的桌面控制器、设置表单和聊天入口。"""
from __future__ import annotations

import dataclasses
import hashlib
from datetime import datetime
import json
from typing import Callable

from PyQt5.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from chat.api_usage import (
    ApiTarget, ApiUsageConfig, ApiUsageService, TEMPLATE_NAMES, UsageError,
    UsageSnapshot, default_template, normalize_base, resolve_target,
)


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
        for field, title in (("remaining", "剩余"), ("used", "已使用"), ("total", "总额度")):
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
        QTimer.singleShot(0, self.reload)

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
            self.snapshotChanged.emit(self.snapshot)
            return
        revision = hashlib.sha256(json.dumps([target.config_key, target.api_key, target.shared,
            usage.to_dict()], sort_keys=True).encode()).hexdigest()
        if revision == self._revision:
            return
        self._revision = revision
        self.timer.stop()
        self.service.cancel()
        self.target, self.usage_config = target, usage
        self.snapshot = UsageSnapshot(error="尚未查询。")
        self.snapshotChanged.emit(self.snapshot)
        self.refresh()

    def refresh(self) -> None:
        if self._closed or not self.polling:
            return
        revision = self._revision

        def deliver(snapshot):
            if not self._closed:
                self._delivered.emit((revision, snapshot))

        self.service.query(self.target, self.usage_config, deliver)

    def _accept(self, value) -> None:
        revision, snapshot = value
        if self._closed or revision != self._revision:
            return
        self.snapshot = snapshot
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

    def close(self) -> None:
        self._closed = True
        self.timer.stop()
        self.reload_timer.stop()
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


class ApiUsageSettingsWidget(QGroupBox):
    """API 设置页内的额度查询草稿；测试不会改动聊天配置。"""
    _testResult = pyqtSignal(object)

    def __init__(self, target_getter: Callable[[], ApiTarget], parent=None,
                 controller: ApiUsageController | None = None) -> None:
        super().__init__("额度查询", parent)
        self.controller = controller or get_api_usage_controller(polling=False)
        self.target_getter = target_getter
        self._loaded_key = ""
        self._approved: tuple[str, ...] = ()
        self._test_revision = 0
        self.setObjectName("apiUsageSettings")
        form = QFormLayout(self)
        self.enabled = QCheckBox("启用当前 API 的额度查询")
        self.enabled.setObjectName("apiUsageEnabled")
        self.template = QComboBox()
        for key, name in TEMPLATE_NAMES.items():
            self.template.addItem(name, key)
        self.base = QLineEdit()
        self.base.setPlaceholderText("留空使用当前 API 地址")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("留空使用当前 API Key；账户余额模板可填写管理密钥")
        self.access = QLineEdit()
        self.access.setEchoMode(QLineEdit.Password)
        self.user_id = QLineEdit()
        self.timeout = QSpinBox()
        self.timeout.setRange(2, 30)
        self.timeout.setSuffix(" 秒")
        self.interval = QSpinBox()
        self.interval.setRange(0, 1440)
        self.interval.setSuffix(" 分钟（0 为只手动刷新）")
        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.000001, 1e12)
        self.scale.setDecimals(6)
        self.unit = QLineEdit()
        self.script = QPlainTextEdit()
        self.script.setObjectName("apiUsageScript")
        self.script.setPlaceholderText("直接粘贴 CC Switch 的 ({ request: {...}, extractor: function(response) {...} }) 脚本")
        self.script.setMinimumHeight(140)
        self.script.setMaximumHeight(220)
        self.test_button = QPushButton("测试查询（不保存）")
        self.test_button.setObjectName("apiUsageTest")
        self.test_button.clicked.connect(self.test_query)
        self.result = QLabel()
        self.result.setTextFormat(Qt.PlainText)
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for title, widget in (("", self.enabled), ("查询模板", self.template), ("查询基址", self.base),
            ("查询专用 Key", self.key), ("Access Token", self.access), ("User ID", self.user_id),
            ("请求超时", self.timeout), ("自动刷新", self.interval), ("New API 换算比例", self.scale),
            ("额度单位", self.unit), ("CC Switch 脚本", self.script), ("", self.test_button), ("", self.result)):
            form.addRow(title, widget)
        self.template.currentIndexChanged.connect(self._template_changed)
        self._testResult.connect(self._accept_test)
        self.load_target(force=True)
        for widget in (self.enabled, self.template, self.base, self.key, self.access, self.user_id,
                       self.timeout, self.interval, self.scale, self.unit, self.script):
            signal = (widget.toggled if isinstance(widget, QCheckBox) else
                      widget.currentIndexChanged if isinstance(widget, QComboBox) else
                      widget.valueChanged if isinstance(widget, (QSpinBox, QDoubleSpinBox)) else
                      widget.textChanged)
            signal.connect(self._invalidate_test)

    def _invalidate_test(self, *_args) -> None:
        self._test_revision += 1
        self.test_button.setEnabled(not self.target_getter().shared)

    def load_target(self, _value=None, *, force: bool = False) -> None:
        target = self.target_getter()
        if not force and target.config_key == self._loaded_key:
            return
        self._test_revision += 1
        self.test_button.setEnabled(not target.shared)
        self._loaded_key = target.config_key
        try:
            config = self.controller.read_config(target)
            config.validate()
        except (UsageError, ValueError, TypeError):
            config = ApiUsageConfig(template=default_template(target))
        self._approved = config.approved_origins
        self.enabled.setChecked(config.enabled and not target.shared)
        self.enabled.setEnabled(not target.shared)
        self.template.setCurrentIndex(max(0, self.template.findData(config.template)))
        self.base.setText(config.base_url)
        for widget, reference in ((self.key, config.api_key_ref), (self.access, config.access_token_ref)):
            try:
                widget.setText(self.controller.service.secrets.get(reference))
            except UsageError:
                widget.clear()
        self.user_id.setText(config.user_id)
        self.timeout.setValue(config.timeout)
        self.interval.setValue(config.auto_interval)
        self.scale.setValue(config.quota_scale)
        self.unit.setText(config.unit)
        self.script.setPlainText(config.code)
        self.result.setText("作者共用 API 不展示共用账户额度。" if target.shared else "启用并保存后查询；只刷新当前正在使用的 API。")
        self._template_changed()

    def _template_changed(self, _value=None) -> None:
        self.script.setEnabled(self.template.currentData() == "custom")
        is_new = self.template.currentData() == "new_api"
        for widget in (self.access, self.user_id, self.scale):
            widget.setEnabled(is_new or self.template.currentData() == "custom")

    def draft(self) -> ApiUsageConfig:
        config = ApiUsageConfig(enabled=self.enabled.isChecked(), template=self.template.currentData(),
            code=self.script.toPlainText(), base_url=normalize_base(self.base.text()),
            user_id=self.user_id.text().strip(), timeout=self.timeout.value(),
            auto_interval=self.interval.value(), quota_scale=self.scale.value(), unit=self.unit.text().strip(),
            approved_origins=self._approved)
        config.validate()
        return config

    def overrides(self) -> dict[str, str]:
        return {"api_key": self.key.text() or self.target_getter().api_key, "access_token": self.access.text()}

    def validate_draft(self) -> bool:
        try:
            self.draft()
            return True
        except (UsageError, ValueError, TypeError) as error:
            self.result.setText(str(error))
            return False

    def save(self) -> bool:
        if not self.validate_draft():
            return False
        persisted = self.controller.save(self.target_getter(), self.draft(),
            {"api_key": self.key.text(), "access_token": self.access.text()})
        self.result.setText("额度查询配置已保存。" if persisted else
            "配置已保存；系统凭据库未启用，专用凭据仅保留到本次程序退出。")
        return True

    def test_query(self) -> None:
        if not self.validate_draft():
            return
        revision = self._test_revision
        config = self.draft()
        # 即使草稿未启用，也可以测试；账户余额测试不把普通聊天密钥当成管理密钥。
        overrides = self.overrides()
        if config.template == "openrouter_account" and not self.key.text():
            self.result.setText("请填写查询专用的 OpenRouter 管理密钥。")
            return
        self.test_button.setEnabled(False)

        def deliver(snapshot):
            try:
                self._testResult.emit((revision, snapshot))
            except RuntimeError:
                pass  # 设置窗口已经关闭，草稿结果不再交付。

        if not self.controller.service.test(self.target_getter(), config, deliver, overrides):
            self.test_button.setEnabled(True)

    def _accept_test(self, value) -> None:
        revision, snapshot = value
        if revision != self._test_revision:
            return
        self.result.setText(snapshot_text(snapshot))
        self.test_button.setEnabled(snapshot.status != "loading")
        if snapshot.status == "needs_confirmation" and confirm_origin(self, snapshot.confirmation_origin):
            self._approved = tuple(dict.fromkeys((*self._approved, snapshot.confirmation_origin)))
            self.test_query()


class ApiUsageButton(QToolButton):
    """紧凑额度入口；窄窗口只显示“额度”，详细信息在弹窗内。"""
    def __init__(self, parent=None, *, height: int = 30, controller: ApiUsageController | None = None) -> None:
        super().__init__(parent)
        self.controller = controller or get_api_usage_controller()
        self.setObjectName("apiUsageButton")
        self.setAccessibleName("当前 API 额度")
        self.setFixedHeight(height)
        self.setMaximumWidth(150)
        self._dialog = None
        self.clicked.connect(self.show_details)
        self.controller.snapshotChanged.connect(self.update_snapshot)
        self.update_snapshot(self.controller.snapshot)

    def set_theme_palette(self, palette) -> None:
        self.setStyleSheet(f"QToolButton {{ color:{palette.text_accent}; background:{palette.surface_tint};"
                          f"border:0; border-radius:8px; padding:0 7px; }}"
                          f"QToolButton:hover {{ background:{palette.surface_selected}; }}")

    def update_snapshot(self, snapshot: UsageSnapshot) -> None:
        text = self.controller.target.name + " 额度"
        if len(snapshot.entries) == 1 and snapshot.entries[0].remaining is not None:
            entry = snapshot.entries[0]
            text = f"{self.controller.target.name} {entry.remaining:g} {entry.unit or ''}"
        if self.parentWidget() is not None and self.parentWidget().width() < 580:
            text = "额度"
        self.setText(self.fontMetrics().elidedText(text, Qt.ElideRight, 136))
        self.setToolTip(self.controller.target.name + "\n" + snapshot_text(snapshot))
        if self._dialog is not None:
            self._details.setText(self.controller.target.name + "\n\n" + snapshot_text(snapshot))
            self._refresh.setEnabled(snapshot.status != "loading" and self.controller.usage_config.enabled)
            self._confirm.setVisible(snapshot.status == "needs_confirmation")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        parent = self.parentWidget()
        if parent is not None and parent.width() < 580:
            self.setText("额度")

    def show_details(self) -> None:
        if self._dialog is None:
            self._dialog = QDialog(self)
            self._dialog.setWindowTitle("当前 API 额度")
            self._dialog.resize(360, 280)
            layout = QVBoxLayout(self._dialog)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            self._details = QLabel()
            self._details.setTextFormat(Qt.PlainText)
            self._details.setWordWrap(True)
            self._details.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._details.setAlignment(Qt.AlignTop)
            scroll.setWidget(self._details)
            layout.addWidget(scroll)
            hint = QLabel("配置入口：设置 → 大模型 API → 额度查询")
            hint.setWordWrap(True)
            layout.addWidget(hint)
            row = QHBoxLayout()
            self._refresh = QPushButton("刷新")
            self._refresh.clicked.connect(self.controller.refresh)
            self._confirm = QPushButton("确认查询地址")
            self._confirm.clicked.connect(lambda: self.controller.approve_current(self._dialog))
            row.addWidget(self._refresh)
            row.addWidget(self._confirm)
            layout.addLayout(row)
        self.update_snapshot(self.controller.snapshot)
        self._dialog.show()
        self._dialog.raise_()
