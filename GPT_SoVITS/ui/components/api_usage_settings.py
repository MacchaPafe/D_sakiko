"""Fluent 风格的额度配置；默认复用聊天连接，高级覆盖项按需展开。"""
from __future__ import annotations

from typing import Callable

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, ComboBox, DoubleSpinBox, FluentIcon, HeaderCardWidget,
    LineEdit, PasswordLineEdit, PlainTextEdit, PrimaryPushButton, SpinBox,
    SwitchButton, TransparentPushButton,
)

from chat.api_usage import (
    ApiTarget, ApiUsageConfig, TEMPLATE_NAMES, UsageError, UsageSnapshot,
    default_template, normalize_base,
)
from ui_main.components.api_usage import (
    ApiUsageController, confirm_origin, get_api_usage_controller, snapshot_text,
)


def form_field(title: str, control: QWidget, parent: QWidget) -> QWidget:
    """在输入框上方提供常驻标签，窄窗口中不挤压输入区域。"""
    field = QWidget(parent)
    layout = QVBoxLayout(field)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    label = BodyLabel(title, field)
    label.setBuddy(control)
    layout.addWidget(label)
    layout.addWidget(control)
    return field


class ApiUsageSettingsWidget(HeaderCardWidget):
    """独立维护查询草稿，测试结果不会修改聊天 API 配置。"""

    _testResult = pyqtSignal(object)

    def __init__(self, target_getter: Callable[[], ApiTarget], parent: QWidget | None = None,
                 controller: ApiUsageController | None = None) -> None:
        """创建主配置、必要凭据和默认折叠的高级配置。"""
        super().__init__(parent)
        self.setTitle("账户额度")
        self.setObjectName("apiUsageSettings")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.viewLayout.setContentsMargins(24, 18, 24, 20)
        self.controller = controller or get_api_usage_controller(polling=False)
        self.target_getter = target_getter
        self._loaded_key = ""
        self._approved: tuple[str, ...] = ()
        self._test_revision = 0
        self.enabled = SwitchButton(self)
        self.enabled.setOnText("自动查询")
        self.enabled.setOffText("仅手动查询")
        self.enabled.setToolTip("自动查询默认每 5 分钟刷新；关闭后仍可从聊天窗口手动查询。")
        self.enabled.setObjectName("apiUsageEnabled")
        self.headerLayout.addWidget(self.enabled, 0, Qt.AlignRight)
        body = QVBoxLayout()
        body.setSpacing(16)
        self.viewLayout.addLayout(body)
        self.connection_hint = CaptionLabel("默认使用当前 API 的地址与密钥，无需重复填写。", self)
        self.connection_hint.setWordWrap(True)
        body.addWidget(self.connection_hint)
        self.template = ComboBox(self)
        for key, name in TEMPLATE_NAMES.items():
            self.template.addItem(name, userData=key)
        body.addWidget(form_field("查询方式", self.template, self))

        self.required_fields = QWidget(self)
        self.required_layout = QVBoxLayout(self.required_fields)
        self.required_layout.setContentsMargins(0, 0, 0, 0)
        self.required_layout.setSpacing(12)
        body.addWidget(self.required_fields)
        self.key = PasswordLineEdit(self)
        self.key.setPlaceholderText("留空时使用当前聊天 API Key")
        self.key_field = form_field("查询专用 API Key", self.key, self)
        self.access = PasswordLineEdit(self)
        self.access.setPlaceholderText("New API 账户的 Access Token")
        self.access_field = form_field("Access Token", self.access, self)
        self.user_id = LineEdit(self)
        self.user_id.setPlaceholderText("账户 User ID")
        self.user_field = form_field("User ID", self.user_id, self)

        self.script = PlainTextEdit(self)
        self.script.setObjectName("apiUsageScript")
        self.script.setPlaceholderText("粘贴 CC Switch 查询脚本：({ request: {...}, extractor: function(response) {...} })")
        self.script.setMinimumHeight(150)
        self.script.setMaximumHeight(210)
        self.script_field = form_field("自定义查询脚本", self.script, self)
        body.addWidget(self.script_field)

        self.advanced_toggle = TransparentPushButton(FluentIcon.CARE_DOWN_SOLID, "高级查询设置", self)
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setAccessibleName("展开高级查询设置")
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        body.addWidget(self.advanced_toggle, 0, Qt.AlignLeft)
        self.advanced = QWidget(self)
        self.advanced_layout = QVBoxLayout(self.advanced)
        self.advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced_layout.setSpacing(12)
        self.base = LineEdit(self)
        self.base.setPlaceholderText("留空时使用当前 API 地址")
        self.advanced_layout.addWidget(form_field("覆盖查询地址", self.base, self))
        self.timeout = SpinBox(self)
        self.timeout.setRange(2, 30)
        self.timeout.setSuffix(" 秒")
        self.interval = SpinBox(self)
        self.interval.setRange(0, 1440)
        self.interval.setSuffix(" 分钟")
        self.interval.setSpecialValueText("不定时刷新")
        timing = QHBoxLayout()
        timing.setSpacing(16)
        timing.addWidget(form_field("刷新间隔", self.interval, self))
        timing.addWidget(form_field("请求超时", self.timeout, self))
        self.advanced_layout.addLayout(timing)
        self.scale = DoubleSpinBox(self)
        self.scale.setRange(0.000001, 1e12)
        self.scale.setDecimals(6)
        self.scale_field = form_field("每单位对应的配额", self.scale, self)
        self.unit = LineEdit(self)
        self.unit.setPlaceholderText("例如 USD")
        self.unit_field = form_field("额度单位", self.unit, self)
        self.advanced_layout.addWidget(self.scale_field)
        self.advanced_layout.addWidget(self.unit_field)
        body.addWidget(self.advanced)
        self.advanced.hide()

        actions = QHBoxLayout()
        self.test_button = PrimaryPushButton(FluentIcon.SYNC, "测试查询", self)
        self.test_button.setObjectName("apiUsageTest")
        self.test_button.clicked.connect(self.test_query)
        actions.addWidget(self.test_button)
        actions.addWidget(CaptionLabel("使用当前表单，不保存配置", self))
        actions.addStretch(1)
        body.addLayout(actions)
        self.result = BodyLabel(self)
        self.result.setTextFormat(Qt.PlainText)
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result.hide()
        body.addWidget(self.result)
        self.template.currentIndexChanged.connect(self._template_changed)
        self._testResult.connect(self._accept_test)
        self.load_target(force=True)
        self.enabled.checkedChanged.connect(self._invalidate_test)
        self.template.currentIndexChanged.connect(self._invalidate_test)
        for control in (self.base, self.key, self.access, self.user_id, self.unit, self.script):
            control.textChanged.connect(self._invalidate_test)
        for control in (self.base, self.key):
            control.textChanged.connect(self._update_connection_hint)
        for control in (self.timeout, self.interval, self.scale):
            control.valueChanged.connect(self._invalidate_test)

    def _toggle_advanced(self, expanded: bool) -> None:
        """按需展开覆盖项，保留正在编辑的值。"""
        self.advanced.setVisible(expanded)
        self.advanced_toggle.setIcon(FluentIcon.CARE_UP_SOLID if expanded else FluentIcon.CARE_DOWN_SOLID)
        self.advanced_toggle.setText("收起高级设置" if expanded else "高级查询设置")
        self.advanced_toggle.setAccessibleName(self.advanced_toggle.text())

    def _invalidate_test(self, *_args: object) -> None:
        """草稿变化后使在途结果失效，同时清理旧账户预览。"""
        self._test_revision += 1
        self.test_button.setEnabled(not self.target_getter().shared)
        self._set_result("")

    def _set_result(self, text: str) -> None:
        """仅在测试或保存有反馈时占用结果区域。"""
        self.result.setText(text)
        self.result.setVisible(bool(text))

    def target_edited(self, *_args: object) -> None:
        """将外层聊天地址与密钥的变化纳入草稿失效边界。"""
        self._invalidate_test()

    def _update_connection_hint(self, *_args: object) -> None:
        """即使覆盖项被收起，也让用户知道查询是否复用当前连接。"""
        template = self.template.currentData()
        if self.target_getter().shared:
            text = "作者共用 API 不展示共用账户额度。"
        elif template == "openrouter_account":
            text = "账户余额需要 OpenRouter 管理密钥，请在下方单独填写。"
        elif template == "new_api":
            text = "使用当前站点地址；账户查询需要 Access Token 和 User ID。"
        elif self.base.text().strip() or self.key.text():
            text = "已设置查询地址或密钥覆盖，可在高级查询设置中查看。"
        else:
            text = ""
        self.connection_hint.setText(text)
        self.connection_hint.setVisible(bool(text))

    def load_target(self, _value: object = None, *, force: bool = False) -> None:
        """切换服务商配置，必要凭据始终显示，高级覆盖项默认收起。"""
        target = self.target_getter()
        if not force and target.config_key == self._loaded_key:
            return
        self._test_revision += 1
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
        self.advanced_toggle.setChecked(False)
        self._template_changed()
        self.test_button.setEnabled(not target.shared)
        self._update_connection_hint()
        self._set_result("作者共用 API 不展示共用账户额度。" if target.shared else "")

    def _template_changed(self, _value: object = None) -> None:
        """只显示所选查询方式必需的字段，普通模板无需手填凭据。"""
        template = self.template.currentData()
        is_account = template == "openrouter_account"
        is_new = template == "new_api"
        is_custom = template == "custom"
        # 同一个输入控件在必要字段与覆盖区间移动，避免两份密钥草稿相互覆盖。
        for field, required in ((self.key_field, is_account), (self.access_field, is_new), (self.user_field, is_new)):
            self.required_layout.removeWidget(field)
            self.advanced_layout.removeWidget(field)
            destination = self.required_layout if required else self.advanced_layout
            if required:
                destination.addWidget(field)
            else:
                destination.insertWidget(1, field)
            field.setVisible(required or is_custom or field is self.key_field)
        self.key.setPlaceholderText("填写 OpenRouter 管理密钥" if is_account else "留空时使用当前聊天 API Key")
        self.required_fields.setVisible(is_account or is_new)
        self.script_field.setVisible(is_custom)
        self.scale_field.setVisible(is_new)
        self.unit_field.setVisible(template in {"new_api", "generic", "custom"})
        self._update_connection_hint()

    def draft(self) -> ApiUsageConfig:
        """读取并校验当前表单，保持未保存的聊天配置不变。"""
        config = ApiUsageConfig(enabled=self.enabled.isChecked(), template=self.template.currentData(),
            code=self.script.toPlainText(), base_url=normalize_base(self.base.text()),
            user_id=self.user_id.text().strip(), timeout=self.timeout.value(),
            auto_interval=self.interval.value(), quota_scale=self.scale.value(), unit=self.unit.text().strip(),
            approved_origins=self._approved)
        config.validate()
        return config

    def overrides(self) -> dict[str, str]:
        """未指定专用密钥时沿用当前聊天表单中的密钥。"""
        return {"api_key": self.key.text() or self.target_getter().api_key, "access_token": self.access.text()}

    def validate_draft(self) -> bool:
        """将可纠正的表单错误展示在测试按钮下方。"""
        try:
            self.draft()
            return True
        except (UsageError, ValueError, TypeError) as error:
            self._set_result(str(error))
            return False

    def save(self) -> bool:
        """保存查询配置，并提示凭据是否仅在当前进程有效。"""
        if not self.validate_draft():
            return False
        persisted = self.controller.save(self.target_getter(), self.draft(),
            {"api_key": self.key.text(), "access_token": self.access.text()})
        self._set_result("额度查询配置已保存。" if persisted else
            "配置已保存；系统凭据库未启用，专用凭据仅保留到本次程序退出。")
        return True

    def test_query(self) -> None:
        """使用当前草稿测试，不保存配置或开启自动刷新。"""
        if not self.validate_draft():
            return
        revision = self._test_revision
        target = self.target_getter()
        config = self.draft()
        if config.template == "openrouter_account" and not self.key.text():
            self._set_result("请填写查询专用的 OpenRouter 管理密钥。")
            return
        self.test_button.setEnabled(False)

        def deliver(snapshot: UsageSnapshot) -> None:
            """通过 Qt 信号将后台结果交付给对应草稿。"""
            try:
                self._testResult.emit((revision, target, snapshot))
            except RuntimeError:
                pass  # 设置窗口已释放，不再交付后台结果。

        if not self.controller.service.test(target, config, deliver, self.overrides()):
            self.test_button.setEnabled(True)

    def _accept_test(self, value: tuple[int, ApiTarget, UsageSnapshot]) -> None:
        """仅展示当前草稿和当前账户的结果，并按需请求跨域确认。"""
        revision, target, snapshot = value
        if revision != self._test_revision or target != self.target_getter():
            return
        self._set_result(snapshot_text(snapshot))
        self.test_button.setEnabled(snapshot.status != "loading")
        if snapshot.status == "needs_confirmation" and confirm_origin(self, snapshot.confirmation_origin):
            self._approved = tuple(dict.fromkeys((*self._approved, snapshot.confirmation_origin)))
            self.test_query()
