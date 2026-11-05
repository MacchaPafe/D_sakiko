"""无需加载 TTS/GPU 的 Qt 额度入口与设置回归。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import QObject, pyqtSignal, Qt
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget

from chat.api_usage import ApiTarget, ApiUsageConfig, ApiUsageService, UsageEntry, UsageSecretStore, UsageSnapshot
from ui_main.components.api_usage import ApiUsageController, ApiUsageButton, snapshot_text
from ui.components.api_usage_settings import ApiUsageSettingsWidget


class Item(QObject):
    valueChanged = pyqtSignal(object)
    def __init__(self, value):
        super().__init__()
        self.value = value


class Config:
    def __init__(self):
        for name, value in {"use_default_deepseek_api": False, "enable_custom_llm_api_provider": False,
            "llm_api_provider": "deepseek", "llm_api_key": {"deepseek": "chat-key"},
            "llm_api_base_url": {}, "custom_llm_api_url": "", "custom_llm_api_key": "",
            "api_usage_configs": {}}.items():
            setattr(self, name, Item(value))
        self.saves = 0
    def set(self, item, value):
        self.saves += 1
        item.value = value
        item.valueChanged.emit(value)


class Backend:
    def __init__(self): self.values = {}
    def set_password(self, namespace, ref, value): self.values[ref] = value
    def get_password(self, namespace, ref): return self.values.get(ref)
    def delete_password(self, namespace, ref): self.values.pop(ref, None)


@pytest.fixture(scope="session")
def app():
    app = QApplication.instance() or QApplication([])
    # Windows offscreen 插件不枚举系统字体；显式加载以供截图验收。
    if not QFontDatabase().families() and os.path.isfile("C:/Windows/Fonts/msyh.ttc"):
        font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0], 10))
    return app


@pytest.fixture
def controller(app):
    config = Config()
    target = ApiTarget("deepseek", "https://api.deepseek.com", "chat-key")
    config.api_usage_configs.value[target.config_key] = ApiUsageConfig(template="deepseek", enabled=False).to_dict()
    service = ApiUsageService(secrets=UsageSecretStore(Backend()),
        runner=lambda *args: {"data": {"remaining": 8, "unit": "CNY"}})
    controller = ApiUsageController(config, service=service)
    QTest.qWait(20)
    try:
        yield controller
    finally:
        controller.close()


def wait(predicate):
    for _ in range(200):
        if predicate(): return
        QTest.qWait(10)
    assert predicate()


def test_disabled_no_network_and_default_five_minutes(controller):
    assert controller.snapshot.status == "disabled"
    assert not controller.timer.isActive()
    config = ApiUsageConfig(enabled=True, template="deepseek")
    controller.save(controller.target, config, {})
    wait(lambda: controller.snapshot.status == "success")
    assert controller.timer.isActive()
    assert controller.timer.interval() == 300000


def test_test_query_does_not_save_or_switch_api(controller):
    target = ApiTarget("custom", "https://draft.example/v1", "draft-key")
    widget = ApiUsageSettingsWidget(lambda: target, controller=controller)
    widget.template.setCurrentIndex(widget.template.findData("generic"))
    before = controller.config.saves
    widget.test_query()
    wait(lambda: "剩余：8" in widget.result.text())
    assert controller.config.saves == before
    assert controller.target.provider_id == "deepseek"
    assert controller.snapshot.status == "disabled"
    widget.close()


def test_save_restart_and_secret_reference(controller):
    widget = ApiUsageSettingsWidget(lambda: controller.target, controller=controller)
    widget.enabled.setChecked(True)
    widget.key.setText("query-private-key")
    widget.interval.setValue(0)
    assert widget.save()
    wait(lambda: controller.snapshot.status == "success")
    saved = controller.config.api_usage_configs.value[controller.target.config_key]
    assert "query-private-key" not in str(saved)
    assert controller.service.secrets.get(saved["api_key_ref"]) == "query-private-key"
    assert not controller.timer.isActive()
    restored = ApiUsageSettingsWidget(lambda: controller.target, controller=controller)
    assert restored.key.text() == "query-private-key" and restored.enabled.isChecked()
    assert restored.interval.value() == 0
    widget.close()
    restored.close()


def test_credential_edit_invalidates_old_results(controller):
    controller.save(controller.target, ApiUsageConfig(enabled=True, template="deepseek"), {})
    wait(lambda: controller.snapshot.status == "success")
    controller.config.set(controller.config.llm_api_key, {"deepseek": "other-chat-key"})
    wait(lambda: controller.target.api_key == "other-chat-key")
    assert controller.snapshot.status in {"loading", "success"}


def test_button_popup_zero_unknown_stale_and_plain_text(controller):
    parent = QWidget()
    parent.resize(420, 220)
    button = ApiUsageButton(parent, controller=controller)
    controller.snapshot = UsageSnapshot("error", entries=(UsageEntry(remaining=0, unit="CNY", extra="<b>not HTML</b>"),),
        stale=True, error="查询失败", checked_at=1, last_success_at=1)
    button.update_snapshot(controller.snapshot)
    button.show_details()
    assert button._balance_view.cards[0].amount.text() == "0.00"
    assert "上次成功" in button._balance_view.updated.text()
    assert button._balance_view.cards[0].amount.textFormat() == Qt.PlainText
    from PyQt5.QtWidgets import QLabel
    assert any(label.text() == "<b>not HTML</b>" and label.textFormat() == Qt.PlainText
               for label in button._balance_view.findChildren(QLabel))
    unknown = snapshot_text(UsageSnapshot("success", entries=(UsageEntry(),)))
    assert "未提供" in unknown and "剩余：0" not in unknown
    parent.show()
    QTest.qWait(20)
    assert button.width() <= 150
    parent.close()


def test_inactive_provider_profile_does_not_refresh_current(controller):
    other = ApiTarget("custom", "https://relay.example", "other-key")
    controller.save(other, ApiUsageConfig(enabled=True, template="generic"), {})
    QTest.qWait(80)
    assert controller.target.provider_id == "deepseek"
    assert controller.snapshot.status == "disabled"


def test_shared_api_controls_disabled(controller):
    target = ApiTarget("deepseek_up", "", shared=True)
    widget = ApiUsageSettingsWidget(lambda: target, controller=controller)
    assert not widget.enabled.isEnabled() and not widget.test_button.isEnabled()
    assert "共用" in widget.result.text()
    widget.close()


def test_settings_invalid_script_prevents_save(controller):
    target = ApiTarget("custom", "https://relay.example", "other-key")
    widget = ApiUsageSettingsWidget(lambda: target, controller=controller)
    widget.enabled.setChecked(True)
    widget.template.setCurrentIndex(widget.template.findData("custom"))
    assert not widget.save()
    assert controller.config.saves == 0
    widget.close()


def test_close_stops_polling(controller):
    controller.save(controller.target, ApiUsageConfig(enabled=True, template="deepseek"), {})
    wait(lambda: controller.timer.isActive())
    controller.close()
    assert not controller.timer.isActive()


def test_tool_view_publishes_and_unbinds_on_close(controller):
    """控制器发布只读视图；关闭后聊天工具读到 unavailable，旧数据不再可见。"""
    from chat.api_usage_tool import read_api_usage, unbind_all_readers

    controller.save(controller.target, ApiUsageConfig(enabled=True, template="deepseek"), {})
    wait(lambda: controller.snapshot.status == "success")
    published = read_api_usage()
    assert published["entries"] and published["entries"][0]["remaining"] == 8
    token = controller._reader_token
    controller.close()
    assert read_api_usage()["status"] == "unavailable"
    unbind_all_readers()
    assert token is not None


def test_tool_view_invalidates_before_merged_reload(controller):
    """Qt 30 毫秒合并重载尚未执行时，工具也不得读到旧账户的数据。"""
    from chat.api_usage_tool import read_api_usage

    controller.save(controller.target, ApiUsageConfig(enabled=True, template="deepseek"), {})
    wait(lambda: controller.snapshot.status == "success")
    assert read_api_usage()["entries"][0]["remaining"] == 8
    controller.config.set(controller.config.llm_api_key, {"deepseek": "switched-chat-key"})
    immediate = read_api_usage()
    assert immediate["entries"] == [] and immediate["status"] == "loading"
    wait(lambda: controller.snapshot.status == "success" and read_api_usage()["entries"])
    assert read_api_usage()["entries"][0]["remaining"] == 8


def test_dedicated_key_replacement_refreshes_same_profile(controller):
    config = ApiUsageConfig(enabled=True, template="deepseek", auto_interval=0)
    controller.save(controller.target, config, {"api_key": "first-private-key"})
    wait(lambda: controller.snapshot.status == "success")
    previous = controller._revision
    controller.save(controller.target, config, {"api_key": "second-private-key"})
    wait(lambda: controller._revision != previous and controller.snapshot.status == "success")
    assert controller.usage_config.revision == 2
    assert "private-key" not in controller._revision


def test_configuration_process_does_not_start_second_poller(app):
    config = Config()
    target = ApiTarget("deepseek", "https://api.deepseek.com", "chat-key")
    config.api_usage_configs.value[target.config_key] = ApiUsageConfig(enabled=True, template="deepseek").to_dict()
    calls = []
    controller = ApiUsageController(config, polling=False,
        service=ApiUsageService(runner=lambda *args: calls.append(args)))
    QTest.qWait(50)
    assert not calls and not controller.timer.isActive()
    controller.close()


def test_invalid_saved_config_is_reported_without_ui_exception(controller):
    controller.config.set(controller.config.api_usage_configs,
        {controller.target.config_key: {"enabled": True, "timeout": "broken"}})
    wait(lambda: controller.snapshot.status == "error")
    assert "配置" in controller.snapshot.error
    widget = ApiUsageSettingsWidget(lambda: controller.target, controller=controller)
    assert widget.timeout.value() == 10
    widget.close()


def test_narrow_button_stays_compact_after_refresh(controller):
    parent = QWidget()
    parent.resize(420, 200)
    button = ApiUsageButton(parent, controller=controller)
    button.update_snapshot(UsageSnapshot("success", entries=(UsageEntry(remaining=1234, unit="CNY"),)))
    assert button.text() == "额度"
    parent.close()


def test_draft_edit_discards_old_test_preview(controller):
    widget = ApiUsageSettingsWidget(lambda: controller.target, controller=controller)
    revision = widget._test_revision
    widget.key.setText("edited-key")
    before = widget.result.text()
    widget._accept_test((revision, controller.target, UsageSnapshot("success", entries=(UsageEntry(remaining=99),))))
    assert widget.result.text() == before
    widget.close()


def test_real_api_settings_component_draft_bridge(controller, app, monkeypatch):
    pytest.importorskip("qfluentwidgets")
    pytest.importorskip("litellm")
    from ui.components.llm_api_area import LLMAPIArea
    monkeypatch.setattr(app, "_api_usage_controller", controller, raising=False)
    area = LLMAPIArea(None)
    try:
        area.llm_provider_combobox.setCurrentIndex(area.llm_provider_combobox.findData("custom"))
        area.custom_url_input.setText("https://draft.example/v1")
        area.custom_key_input.setText("draft-only-key")
        area.custom_url_input.editingFinished.emit()
        widget = area.api_usage_settings
        widget.template.setCurrentIndex(widget.template.findData("generic"))
        assert area._usage_draft_target() == ApiTarget("custom", "https://draft.example/v1", "draft-only-key")
        widget.test_query()
        wait(lambda: "剩余：8" in widget.result.text())
        assert controller.target.provider_id == "deepseek" and controller.config.saves == 0
        area.resize(760, 880)
        area.show()
        QTest.qWait(30)
        path = os.environ.get("API_USAGE_SCREENSHOT_PATH")
        if path:
            assert widget.grab().save(path)
        area.resize(440, 880)
        area.generation_card.set_expanded(True)
        QTest.qWait(30)
        assert area.horizontalScrollBar().maximum() == 0
    finally:
        area.close()
        area.deleteLater()
        QTest.qWait(10)


@pytest.mark.parametrize("width", [440, 620, 900])
def test_expanded_generation_labels_fit_after_resize(
    controller: ApiUsageController, app: QApplication,
    monkeypatch: pytest.MonkeyPatch, width: int,
) -> None:
    """反复展开和改变宽度后，标题与说明获得完整的换行高度。"""
    from ui.components.llm_api_area import LLMAPIArea
    monkeypatch.setattr(app, "_api_usage_controller", controller, raising=False)
    area = LLMAPIArea(None)
    try:
        area.show()
        cards = (area.display_unformatted_message_card, area.llm_temperature_card,
                 area.llm_top_p_card, *area.timeout_cards)
        for current_width in (width, 440, width):
            area.generation_card.set_expanded(False)
            area.resize(current_width, 880)
            QTest.qWait(20)
            area.generation_card.set_expanded(True)
            QTest.qWait(30)
            assert area.horizontalScrollBar().maximum() == 0
            for card in cards:
                title, content = card.titleLabel, card.contentLabel
                for label in (title, content):
                    assert label.height() >= label.heightForWidth(label.width()), label.text()
                    assert card.rect().contains(label.geometry()), label.text()
                assert title.geometry().bottom() < content.geometry().top()
    finally:
        area.close()
        area.deleteLater()
        QTest.qWait(10)


def test_official_defaults_preserve_explicit_opt_out(app: QApplication) -> None:
    """首次使用官方账户自动查询，历史关闭选择和中转地址保持原意。"""
    target = ApiTarget("deepseek", "https://api.deepseek.com", "synthetic")
    assert ApiUsageConfig.from_dict(None, target).enabled
    assert not ApiUsageConfig.from_dict({"enabled": False}, target).enabled
    assert not ApiUsageConfig.from_dict(None, ApiTarget("custom", "https://relay.example")).enabled
    assert not ApiUsageConfig.from_dict(None, ApiTarget("deepseek_up", "", shared=True)).enabled
    config = Config()
    calls: list[dict] = []

    def runner(payload: dict, *_args: object) -> dict:
        """记录默认自动查询，不连接外部服务。"""
        calls.append(payload)
        return {"data": {"remaining": 7}}

    instance = ApiUsageController(config, service=ApiUsageService(runner=runner))
    try:
        wait(lambda: instance.snapshot.status == "success")
        assert len(calls) == 1 and instance.timer.isActive()
    finally:
        instance.close()


def test_manual_open_queries_without_enabling_polling(controller: ApiUsageController) -> None:
    """关闭自动查询时，主界面一次点击就能取得余额，且不修改配置。"""
    button = ApiUsageButton(controller=controller)
    saves = controller.config.saves
    try:
        button.show_details()
        wait(lambda: controller.snapshot.status == "success")
        assert controller.config.saves == saves
        assert not controller.usage_config.enabled
        assert not controller.timer.isActive()
        assert button._balance_view.cards[0].amount.text() == "8.00"
        assert button._refresh.isEnabled()
    finally:
        button._dialog.close()
        button.close()


def test_required_credentials_visible_without_advanced(controller: ApiUsageController) -> None:
    """普通账户只显示常用项，管理密钥和 New API 必填项不藏进高级区。"""
    from qfluentwidgets import HeaderCardWidget
    widget = ApiUsageSettingsWidget(lambda: controller.target, controller=controller)
    try:
        assert isinstance(widget, HeaderCardWidget)
        assert widget.advanced.isHidden()
        widget.template.setCurrentIndex(widget.template.findData("openrouter_account"))
        assert not widget.required_fields.isHidden()
        assert widget.required_layout.indexOf(widget.key_field) >= 0
        widget.template.setCurrentIndex(widget.template.findData("new_api"))
        assert widget.required_layout.indexOf(widget.access_field) >= 0
        assert widget.required_layout.indexOf(widget.user_field) >= 0
        widget.template.setCurrentIndex(widget.template.findData("custom"))
        assert not widget.script_field.isHidden()
        widget.template.setCurrentIndex(widget.template.findData("deepseek"))
        assert widget.script_field.isHidden()
        assert widget.required_fields.isHidden()
        widget.key.setText("synthetic-override")
        assert "覆盖" in widget.connection_hint.text()
    finally:
        widget.close()


def test_outer_chat_key_edit_invalidates_draft_preview(
    controller: ApiUsageController, app: QApplication, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """修改外层聊天密钥后，旧草稿回调不会冒充新账户的查询结果。"""
    from ui.components.llm_api_area import LLMAPIArea
    monkeypatch.setattr(app, "_api_usage_controller", controller, raising=False)
    area = LLMAPIArea(None)
    try:
        area.llm_provider_combobox.setCurrentIndex(area.llm_provider_combobox.findData("deepseek"))
        area.standard_key_input.setText("synthetic-old-key")
        widget = area.api_usage_settings
        revision = widget._test_revision
        target = area._usage_draft_target()
        area.standard_key_input.setText("synthetic-new-key")
        widget._accept_test((revision, target, UsageSnapshot("success", entries=(UsageEntry(remaining=99),))))
        assert widget._test_revision != revision
        assert not widget.result.text()
    finally:
        area.close()
        area.deleteLater()
        QTest.qWait(10)


def test_balance_cards_keep_currencies_bonus_and_unknown_distinct(app: QApplication) -> None:
    """多币种独立展示，零赠金隐藏，缺失余额不显示为零。"""
    from ui_main.components.api_usage_balance import UsageBalanceView
    view = UsageBalanceView()
    try:
        view.set_snapshot(UsageSnapshot("success", entries=(
            UsageEntry(remaining=128.56, unit="CNY", grantedBalance=18.56, toppedUpBalance=110),
            UsageEntry(remaining=0, unit="USD", grantedBalance=0),
            UsageEntry(remaining=None, unit="USD", planName="密钥额度"),
        )))
        assert len(view.cards) == 3
        assert view.cards[0].amount.text() == "128.56"
        assert view.cards[0].bonus.text() == "含赠金 18.56 CNY"
        assert view.cards[1].amount.text() == "0.00" and view.cards[1].bonus.isHidden()
        assert view.cards[2].amount.text() == "—"
        assert view.updated.isHidden()
    finally:
        view.close()
