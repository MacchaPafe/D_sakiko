"""Fluent 配置中的 ChatGPT 账户管理，不向界面暴露访问令牌。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from PyQt5.QtCore import QThread, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action, BodyLabel, CaptionLabel, CardWidget, ComboBox, FluentIcon,
    IndeterminateProgressRing, InfoBadge, InfoBar, InfoBarIcon,
    InfoBarPosition, MessageBox, MessageBoxBase, PrimaryPushButton,
    PushButton, RadioButton, RoundMenu, SettingCard, StrongBodyLabel,
    SubtitleLabel, ToolTipFilter, TransparentPushButton,
    TransparentToolButton,
)

from llm.chatgpt.session import ChatGPTSession, USAGE_URL
from llm.chatgpt.storage import Account
from llm.errors import LLMError
from llm.types import CancellationToken
from qconfig import d_sakiko_config
from ui_qt.threads.update_config_thread import notify_config_reload


class AccountTask(QThread):
    """后台执行可取消的认证或目录请求，GUI 线程只接收显示数据。"""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    login_url = pyqtSignal(str)

    def __init__(self, operation: Callable[[], Awaitable[object]], cancel: CancellationToken,
                 parent: QWidget) -> None:
        """绑定一次独立操作和取消信号。"""
        super().__init__(parent)
        self.operation = operation
        self.cancel = cancel

    def run(self) -> None:
        """取消时关闭异步 HTTP 和回调监听，避免遗留后台线程。"""
        async def execute() -> object:
            """等待任务时观察取消，不向主线程阻塞等待网络。"""
            task = asyncio.ensure_future(self.operation())
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=0.05)
                    if self.cancel.cancelled:
                        raise LLMError("cancelled", "操作已取消。")
                return task.result()
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        try:
            result = asyncio.run(execute())
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)


class ChatGPTComplianceDialog(MessageBoxBase):
    """向用户说明 ChatGPT 订阅官方 OAuth 接入原理与合规性。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.title_label = SubtitleLabel("ChatGPT 订阅接入：安全与合规说明", self.widget)
        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.setSpacing(12)

        sections = (
            (
                "1. 官方 OAuth 授权协议，非反代",
                "数字小祥采用 OpenAI 官方提供的 Sign in with ChatGPT 标准协议接入。该协议由 OpenAI 开放给所有开源 Agent 使用，是正常的接入流程。",
            ),
            (
                "2. 可以随时退出登录",
                "您可以随时在账户卡片上退出登录，或前往 ChatGPT 官网的「设置 → 使用情况和计费」页面查看额度消耗并随时取消授权。",
            ),
        )
        for heading, content in sections:
            item_layout = QVBoxLayout()
            item_layout.setSpacing(4)
            heading_label = StrongBodyLabel(heading, self)
            body_label = BodyLabel(content, self)
            body_label.setWordWrap(True)
            body_label.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))
            item_layout.addWidget(heading_label)
            item_layout.addWidget(body_label)
            self.viewLayout.addLayout(item_layout)

        self.widget.setMinimumWidth(460)
        self.yesButton.setText("知道了")
        self.cancelButton.setText("管理用量...")
        self.cancelButton.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(USAGE_URL)))


class AccountCard(CardWidget):
    """单个 ChatGPT 账户卡片，展示授权状态并提供刷新、退出及更多菜单。"""

    selected = pyqtSignal(str)
    refresh_requested = pyqtSignal(str)
    logout_requested = pyqtSignal(str)
    relogin_requested = pyqtSignal(str)

    def __init__(self, account: Account, *, is_selected: bool, is_active: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.account = account
        self.setFixedHeight(68)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 12, 10)
        layout.setSpacing(12)

        self.radio_button = RadioButton(self)
        self.radio_button.setChecked(is_selected)
        self.radio_button.setFixedWidth(24)
        self.radio_button.clicked.connect(lambda: self.selected.emit(self.account.id))
        layout.addWidget(self.radio_button)

        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        self.title_label = StrongBodyLabel(account.email or "未完成的登录", self)
        title_row.addWidget(self.title_label)

        if is_active and account.sharing:
            self.status_badge = InfoBadge.success("当前使用", parent=self)
        elif account.sharing:
            self.status_badge = InfoBadge.attension("已授权", parent=self)
        else:
            self.status_badge = InfoBadge.warning("需重新登录", parent=self)
        title_row.addWidget(self.status_badge)
        title_row.addStretch(1)
        info_layout.addLayout(title_row)

        status_text = "已登录并授权订阅额度" if account.sharing else "尚未完成登录或未授权订阅额度"
        self.subtitle_label = CaptionLabel(f"ID: {account.id[:6]} · {status_text}", self)
        self.subtitle_label.setTextColor(QColor(115, 115, 115), QColor(160, 160, 160))
        info_layout.addWidget(self.subtitle_label)

        layout.addLayout(info_layout, stretch=1)

        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(4)

        self.refresh_button = TransparentToolButton(FluentIcon.SYNC, self)
        self.refresh_button.setToolTip("刷新模型列表")
        self.refresh_button.installEventFilter(ToolTipFilter(self.refresh_button))
        self.refresh_button.setEnabled(account.sharing)
        self.refresh_button.clicked.connect(lambda: self.refresh_requested.emit(self.account.id))

        self.logout_button = TransparentToolButton(FluentIcon.POWER_BUTTON, self)
        self.logout_button.setToolTip("退出登录")
        self.logout_button.installEventFilter(ToolTipFilter(self.logout_button))
        self.logout_button.setEnabled(account.status == "connected" or bool(account.credential_store))
        self.logout_button.clicked.connect(self._confirm_logout)

        self.more_button = TransparentToolButton(FluentIcon.MORE, self)
        self.more_button.setToolTip("更多操作")
        self.more_button.installEventFilter(ToolTipFilter(self.more_button))
        self.more_button.clicked.connect(self.show_more_menu)

        self.relogin_action = Action(FluentIcon.SYNC, "重新登录", self)
        self.relogin_action.triggered.connect(lambda: self.relogin_requested.emit(self.account.id))
        self.usage_action = Action(FluentIcon.LINK, "管理 ChatGPT 用量", self)
        self.usage_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(USAGE_URL)))

        for button in (self.refresh_button, self.logout_button, self.more_button):
            actions_layout.addWidget(button)
        layout.addLayout(actions_layout)

        self.clicked.connect(lambda: self.selected.emit(self.account.id))

    def _confirm_logout(self) -> None:
        """确认当前卡片的账户后才发出退出登录请求。"""
        account_name = self.account.email or f"账户 {self.account.id[:6]}"
        dialog = MessageBox(
            "确认退出登录？",
            f"退出 {account_name} 后，需要重新登录才能继续使用此账户的 ChatGPT 订阅。",
            self.window(),
        )
        dialog.contentLabel.setTextFormat(Qt.PlainText)
        dialog.yesButton.setText("退出登录")
        dialog.cancelButton.setText("取消")
        dialog.cancelButton.setDefault(True)
        dialog.cancelButton.setFocus()
        confirmed = dialog.exec_()
        dialog.deleteLater()
        if confirmed:
            self.logout_requested.emit(self.account.id)

    def show_more_menu(self) -> None:
        """弹出该账户的更多操作菜单（重新登录、用量管理）。"""
        menu = RoundMenu(parent=self)
        menu.addAction(self.relogin_action)
        menu.addAction(self.usage_action)
        pos = self.more_button.mapToGlobal(self.more_button.rect().bottomLeft())
        menu.exec(pos)


class ChatGPTAccountArea(QWidget):
    """账户与全局模型分开选择；连接立即保存，选模自动启用。"""

    activated = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, session: ChatGPTSession | None = None) -> None:
        """构建 Fluent 控件，尚未打开页面时不访问凭据库或网络。"""
        super().__init__(parent)
        self._session = session
        self._task: AccountTask | None = None
        self._login_url = ""
        self._selected_id = ""
        self._accounts: list[Account] = []
        self._models: list[dict[str, object]] = []
        self._after_task: Callable[[], None] | None = None
        self._status_bar: InfoBar | None = None
        self.account_cards: dict[str, AccountCard] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 4)
        layout.setSpacing(10)

        # 1. 顶部标题与操作栏
        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 0, 0)
        header.setSpacing(8)
        self.header_label = StrongBodyLabel("ChatGPT 账户", self)
        self.compliance_button = TransparentPushButton(FluentIcon.INFO, "安全与合规说明", self)
        self.compliance_button.setToolTip("了解 ChatGPT 订阅直连原理与账号安全说明")
        self.compliance_button.installEventFilter(ToolTipFilter(self.compliance_button))

        self.usage_button = TransparentPushButton(FluentIcon.LINK, "管理用量", self)
        self.usage_button.setToolTip("在浏览器中打开 ChatGPT 官网管理订阅用量与授权")
        self.usage_button.installEventFilter(ToolTipFilter(self.usage_button))

        self.add_button = PrimaryPushButton(FluentIcon.ADD, "添加账户", self)
        self.add_button.setToolTip("在浏览器中登录并绑定新的 ChatGPT 账户")
        self.add_button.installEventFilter(ToolTipFilter(self.add_button))

        header.addWidget(self.header_label)
        header.addWidget(self.compliance_button)
        header.addStretch(1)
        header.addWidget(self.usage_button)
        header.addWidget(self.add_button)
        layout.addLayout(header)

        # 2. 空状态引导卡片（尚无任何账户时展示）
        self.empty_card = CardWidget(self)
        empty_layout = QHBoxLayout(self.empty_card)
        empty_layout.setContentsMargins(20, 14, 20, 14)
        empty_layout.setSpacing(12)
        empty_text_layout = QVBoxLayout()
        empty_text_layout.setSpacing(2)
        empty_title = StrongBodyLabel("尚未绑定 ChatGPT 账户", self.empty_card)
        empty_subtitle = CaptionLabel("登录后即可使用您的 ChatGPT 订阅额度进行对话", self.empty_card)
        empty_subtitle.setTextColor(QColor(115, 115, 115), QColor(160, 160, 160))
        empty_text_layout.addWidget(empty_title)
        empty_text_layout.addWidget(empty_subtitle)
        self.empty_login_button = PrimaryPushButton("Continue with ChatGPT", self.empty_card)
        empty_layout.addLayout(empty_text_layout, stretch=1)
        empty_layout.addWidget(self.empty_login_button)
        layout.addWidget(self.empty_card)

        # 3. 账户卡片列表容器
        self.cards_container = QWidget(self)
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        layout.addWidget(self.cards_container)

        # 4. 登录等待中动态备份条（平时隐藏，仅在等待浏览器回调时展示）
        self.login_banner = CardWidget(self)
        banner_layout = QHBoxLayout(self.login_banner)
        banner_layout.setContentsMargins(16, 10, 16, 10)
        banner_layout.setSpacing(12)
        self.progress_ring = IndeterminateProgressRing(self.login_banner)
        self.progress_ring.setFixedSize(20, 20)
        self.progress_ring.setStrokeWidth(3)
        banner_text_layout = QVBoxLayout()
        banner_text_layout.setSpacing(2)
        self.banner_title = BodyLabel("正在等待浏览器完成登录授权…", self.login_banner)
        self.banner_hint = CaptionLabel("若浏览器未自动打开，可复制登录链接手动访问", self.login_banner)
        self.banner_hint.setTextColor(QColor(115, 115, 115), QColor(160, 160, 160))
        banner_text_layout.addWidget(self.banner_title)
        banner_text_layout.addWidget(self.banner_hint)
        self.copy_button = PushButton(FluentIcon.COPY, "复制登录链接", self.login_banner)
        self.copy_button.setEnabled(False)
        self.cancel_button = PushButton("取消", self.login_banner)
        self.cancel_button.setEnabled(False)
        banner_layout.addWidget(self.progress_ring)
        banner_layout.addLayout(banner_text_layout, stretch=1)
        banner_layout.addWidget(self.copy_button)
        banner_layout.addWidget(self.cancel_button)
        self.login_banner.hide()
        layout.addWidget(self.login_banner)

        # 5. 全局模型选择卡片（与下方 SettingCard 保持统一的卡片视觉风格）
        self.model_card = SettingCard(
            FluentIcon.ROBOT,
            "全局 ChatGPT 模型",
            "选择后立即生效，切换账户时自动沿用",
            self,
        )
        self.model_combo = ComboBox(self.model_card)
        self.model_combo.setMinimumWidth(220)
        self.model_card.hBoxLayout.addWidget(self.model_combo, 0, Qt.AlignRight)
        self.model_card.hBoxLayout.addSpacing(16)
        layout.addWidget(self.model_card)

        self.compliance_button.clicked.connect(self.show_compliance_dialog)
        self.add_button.clicked.connect(lambda: self.sign_in(None))
        self.empty_login_button.clicked.connect(lambda: self.sign_in(None))
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self._login_url))
        self.cancel_button.clicked.connect(self.cancel)
        self.usage_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(USAGE_URL)))
        self.model_combo.currentIndexChanged.connect(lambda index: self._guard(lambda: self._model_changed(index)))

    def show_compliance_dialog(self) -> None:
        """弹出技术原理与安全合规说明对话框。"""
        dialog = ChatGPTComplianceDialog(self.window())
        dialog.exec_()

    def _sync_geometry(self) -> None:
        """卡片数量或等待条显隐变化后通知外层自适应堆栈容器更新高度。"""
        self.cards_container.updateGeometry()
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None:
            parent.updateGeometry()

    def show_status(self, message: str, icon: InfoBarIcon = InfoBarIcon.INFORMATION,
                    *, duration: int | None = None) -> None:
        """用窗口右上角的 Fluent 提示替换旧状态，错误和警告保留至关闭。"""
        if self._status_bar is not None:
            self._status_bar.close()
        if duration is None:
            duration = -1 if icon in {InfoBarIcon.ERROR, InfoBarIcon.WARNING} else 5000
        self._status_bar = InfoBar.new(
            icon=icon, title="ChatGPT", content=message, orient=Qt.Vertical,
            isClosable=True, duration=duration, position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )
        self._status_bar.closedSignal.connect(self._clear_status_bar)

    def _clear_status_bar(self) -> None:
        """提示手动关闭或自动消失后清除引用，避免访问已销毁控件。"""
        self._status_bar = None

    def _guard(self, operation: Callable[[], None]) -> None:
        """槽函数的磁盘错误只显示失败，不能使 Qt 进程异常终止。"""
        try:
            operation()
        except Exception as exc:
            self.show_status(str(exc), InfoBarIcon.ERROR)

    @property
    def session(self) -> ChatGPTSession:
        """仅在用户打开订阅页面时初始化账户存储。"""
        if self._session is None:
            self._session = ChatGPTSession()
        return self._session

    def selected_id(self) -> str:
        """取得当前选择的注册身份，不用显示邮箱代替。"""
        return self._selected_id

    def _rebuild_cards(self) -> None:
        """根据账户列表重建卡片并同步选中及活动状态。"""
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.account_cards.clear()

        has_accounts = bool(self._accounts)
        self.empty_card.setVisible(not has_accounts)
        self.cards_container.setVisible(has_accounts)
        self.add_button.setVisible(has_accounts)

        active_id = str(d_sakiko_config.chatgpt_account_id.value or "")
        busy = self.is_busy()
        for account in self._accounts:
            card = AccountCard(
                account,
                is_selected=(account.id == self._selected_id),
                is_active=(account.id == active_id),
                parent=self.cards_container,
            )
            card.setEnabled(not busy)
            card.selected.connect(lambda account_id: self._guard(lambda: self.select_account(account_id)))
            card.refresh_requested.connect(lambda account_id: self._guard(lambda: self.refresh_models(account_id)))
            card.logout_requested.connect(lambda account_id: self._guard(lambda: self.sign_out(account_id)))
            card.relogin_requested.connect(lambda account_id: self._guard(lambda: self.sign_in(account_id)))
            self.cards_layout.addWidget(card)
            self.account_cards[account.id] = card

        self._sync_geometry()

    def load(self, selected: str = "") -> None:
        """刷新无秘密账户列表，保留当前或全局选中的连接。"""
        try:
            self._accounts = self.session.store.list_accounts()
        except Exception as exc:
            self.show_status(str(exc), InfoBarIcon.ERROR)
            return
        wanted = selected or self._selected_id or str(d_sakiko_config.chatgpt_account_id.value)
        known_ids = {account.id for account in self._accounts}
        if wanted in known_ids:
            self._selected_id = wanted
        elif self._accounts:
            self._selected_id = self._accounts[0].id
        else:
            self._selected_id = ""
        self._rebuild_cards()
        self._fill_models(self.session.cached_models(self._selected_id) if self._selected_id else [])

    def select_account(self, account_id: str) -> None:
        """在卡片列表中选择指定账户，已授权账户立即成为当前生效连接。"""
        if account_id and not any(acc.id == account_id for acc in self._accounts):
            return
        self._selected_id = account_id
        self._account_changed()
        self._rebuild_cards()

    def _busy(self, value: bool, *, is_login: bool = False) -> None:
        """进行网络操作时锁定账户编辑，登录期间展示复制链接与取消备份条。"""
        for widget in (self.model_combo, self.add_button, self.empty_login_button):
            widget.setEnabled(not value)
        for card in self.account_cards.values():
            card.setEnabled(not value)
        self.cancel_button.setEnabled(value)
        if value and is_login:
            self.login_banner.show()
        elif not value:
            self.login_banner.hide()
        self._sync_geometry()

    def _start(self, operation: Callable[[], Awaitable[object]], handler: Callable[[object], None],
               *, token: CancellationToken | None = None, is_login: bool = False) -> None:
        """一次只运行一个账户操作，结果交回主线程处理。"""
        if self._task is not None:
            return
        task = AccountTask(operation, token or CancellationToken(), self)
        self._task = task
        self._busy(True, is_login=is_login)
        task.login_url.connect(self._set_login_url)
        task.succeeded.connect(lambda value: self._guard(lambda: handler(value)))
        task.failed.connect(lambda message: self.show_status(message, InfoBarIcon.ERROR))
        task.finished.connect(self._finished)
        task.finished.connect(task.deleteLater)
        task.start()

    def _finished(self) -> None:
        """清理按钮和短期授权网址，再执行需要接续的目录加载。"""
        self._task = None
        self._busy(False)
        self._login_url = ""
        self.copy_button.setEnabled(False)
        followup, self._after_task = self._after_task, None
        if followup is not None:
            followup()

    def _set_login_url(self, value: str) -> None:
        """只在内存保留可复制的授权链接，不展示或记录其中的身份提示。"""
        self._login_url = value
        self.copy_button.setEnabled(True)

    def sign_in(self, account_id: str | None = None) -> None:
        """添加或重登注册，成功后立即选择新连接并沿用全局模型。"""
        if self._task is not None:
            return
        token = CancellationToken()
        self.show_status("请在系统浏览器中登录；若未弹出浏览器，可点击下方“复制登录链接”。", duration=-1)

        async def operation() -> object:
            """通过线程信号提供浏览器链接，不传递令牌。"""
            return await self.session.sign_in(account_id or None, cancel=token,
                on_url=lambda url: self._task.login_url.emit(url) if self._task else None)

        self._start(operation, self._signed_in, token=token, is_login=True)

    def _signed_in(self, value: object) -> None:
        """登录状态已经落盘；独立提交账户选择，不保存其他页面草稿。"""
        if not isinstance(value, Account):
            return
        d_sakiko_config.set(d_sakiko_config.chatgpt_account_id, value.id)
        self.load(value.id)
        if not value.sharing:
            self.show_status("已登录，但没有授权使用 ChatGPT 订阅额度，请重新登录并授权。", InfoBarIcon.WARNING)
            return
        model = str(d_sakiko_config.chatgpt_model.value or "")
        if model:
            self._commit(value.id, model)
            self._rebuild_cards()
        else:
            self.show_status("登录已保存。请选择模型以启用 ChatGPT。", InfoBarIcon.SUCCESS)
        self._after_task = self.refresh_models
        if not d_sakiko_config.chatgpt_welcome_seen.value:
            dialog = MessageBox("已授权使用 ChatGPT 订阅", "选择模型后，数字小祥将使用你的 ChatGPT 订阅额度。可随时打开“管理用量”调整限制。", self.window())
            dialog.yesButton.setText("知道了")
            dialog.cancelButton.hide()
            d_sakiko_config.set(d_sakiko_config.chatgpt_welcome_seen, True)
            dialog.exec_()

    def _commit(self, identifier: str, model: str) -> None:
        """原子保存订阅连接与全局模型，其他配置仍按原方式保存。"""
        with d_sakiko_config as config:
            config.set(config.chatgpt_account_id, identifier)
            config.set(config.chatgpt_model, model)
            config.set(config.use_default_deepseek_api, False)
            config.set(config.enable_custom_llm_api_provider, False)
            config.set(config.llm_api_provider, "chatgpt")
        notify_config_reload()
        self.activated.emit()

    def _account_changed(self) -> None:
        """手动切换账户后保留全局模型，并刷新此账户的模型目录。"""
        identifier = self.selected_id()
        if not identifier:
            self._fill_models([])
            return
        account = self.session.store.get(identifier)
        model = str(d_sakiko_config.chatgpt_model.value or "")
        self._fill_models(self.session.cached_models(identifier))
        if account.sharing:
            if model:
                self._commit(identifier, model)
            else:
                d_sakiko_config.set(d_sakiko_config.chatgpt_account_id, identifier)
            self.refresh_models(identifier)
        else:
            self.show_status("该账户尚未登录或授权，请在卡片右侧菜单中选择“重新登录”。", InfoBarIcon.INFORMATION)

    def _fill_models(self, models: list[dict[str, object]]) -> None:
        """模型目录变化不重置全局选择，不把加载触发的信号当用户操作。"""
        self._models = models
        selected = str(d_sakiko_config.chatgpt_model.value or "")
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItem("请选择模型", userData="")
        for model in models:
            self.model_combo.addItem(str(model.get("display_name") or model["slug"]), userData=model["slug"])
        index = self.model_combo.findData(selected)
        if selected and index < 0:
            self.model_combo.addItem(selected + "（当前目录中不可用）", userData=selected)
            index = self.model_combo.count() - 1
        self.model_combo.setCurrentIndex(max(0, index))
        self.model_combo.blockSignals(False)

    def refresh_models(self, account_id: str | None = None) -> None:
        """在后台请求指定或当前账户目录，失败保留缓存和全局模型。"""
        identifier = account_id or self.selected_id()
        if not identifier:
            return
        if account_id and account_id != self._selected_id:
            self._selected_id = account_id
            self._rebuild_cards()

        async def operation() -> object:
            """仅查询所选连接的模型列表。"""
            return await self.session.models(identifier)

        def finished(value: object) -> None:
            """目录成功后显示可用性，不自动选择其他模型。"""
            if isinstance(value, list) and identifier == self.selected_id():
                self._fill_models(value)
                selected = str(d_sakiko_config.chatgpt_model.value or "")
                available = any(item.get("slug") == selected for item in self._models)
                if available or not selected:
                    self.show_status("模型目录已更新。", InfoBarIcon.SUCCESS)
                else:
                    self.show_status("当前账户不支持所选模型，请手动调整。", InfoBarIcon.WARNING)
        self._start(operation, finished)

    def _model_changed(self, index: int) -> None:
        """用户实际选择受支持模型时立即启用，无需再点保存配置。"""
        model = str(self.model_combo.currentData() or "")
        identifier = self.selected_id()
        if identifier and model and any(item.get("slug") == model for item in self._models):
            if self.session.store.get(identifier).sharing:
                self._commit(identifier, model)
                self._rebuild_cards()

    def sign_out(self, account_id: str | None = None) -> None:
        """退出会话但保留注册，账户仍有生成租约时由认证模块拒绝。"""
        identifier = account_id or self.selected_id()
        if not identifier:
            return

        async def operation() -> object:
            """尝试撤销会话并清理此注册的本地凭据。"""
            return await self.session.sign_out(identifier)

        def finished(value: object) -> None:
            """退出不自动切换其他账户或提供商。"""
            if d_sakiko_config.chatgpt_account_id.value == identifier:
                d_sakiko_config.set(d_sakiko_config.chatgpt_account_id, "")
                notify_config_reload()
            self.load(identifier)
            self.show_status(
                str(value) if value else "已退出登录，下次可通过卡片右侧菜单重新登录。",
                InfoBarIcon.WARNING if value else InfoBarIcon.SUCCESS,
            )
        self._start(operation, finished)

    def cancel(self) -> None:
        """请求关闭当前认证操作，已保存的注册关系仍保留。"""
        if self._task is not None:
            self._task.cancel.cancel()

    def is_busy(self) -> bool:
        """配置窗口等待后台连接清理后才能销毁线程对象。"""
        return self._task is not None
