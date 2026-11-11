"""PR #29 合并时新增配置页面的离线集成检查。"""
from __future__ import annotations

import pytest
from unittest.mock import Mock
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel
from qfluentwidgets import qconfig as fluent_config

from chat.api_usage import ApiTarget, ApiUsageConfig, ApiUsageService, CHATGPT_USAGE_NOTICE, UsageEntry, UsageSnapshot
from chat.api_usage_tool import build_result
from qconfig import d_sakiko_config
from test.test_api_usage_ui import app, controller
from ui_fluent_widget.components.chatgpt_account_area import ChatGPTAccountArea
from ui_fluent_widget.components.llm_api_area import LLMAPIArea
from ui_qt.components.api_usage import ApiUsageButton, ApiUsageController
from ui_fluent_widget.components.api_usage_settings import ApiUsageSettingsWidget


def test_start_with_chatgpt_selected(
    app: QApplication, controller: ApiUsageController, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """启动时已选订阅账户，卡片初始化完成后才能禁用生成参数。"""
    monkeypatch.setattr(app, "_api_usage_controller", controller, raising=False)
    monkeypatch.setattr(ChatGPTAccountArea, "load", lambda self: None)
    monkeypatch.setattr(d_sakiko_config, "set", Mock())
    monkeypatch.setattr(fluent_config, "set", Mock())
    monkeypatch.setattr(d_sakiko_config.use_default_deepseek_api, "value", False)
    monkeypatch.setattr(d_sakiko_config.enable_custom_llm_api_provider, "value", False)
    monkeypatch.setattr(d_sakiko_config.llm_api_provider, "value", "chatgpt")
    area = LLMAPIArea(None)
    try:
        assert area.llm_stack.currentWidget() is area.chatgpt_area
        assert not area.llm_temperature_card.isEnabled()
        assert not area.llm_top_p_card.isEnabled()
        assert area.api_usage_settings is not None
    finally:
        area.close()
        area.deleteLater()
        QTest.qWait(10)


@pytest.mark.parametrize("provider", ["custom", "custom_responses"])
def test_custom_context_control_survives_narrow_layout(
    app: QApplication, controller: ApiUsageController, monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    """两个协议的上下文上限控件均保留，窄窗口没有水平溢出。"""
    monkeypatch.setattr(app, "_api_usage_controller", controller, raising=False)
    area = LLMAPIArea(None)
    try:
        area.llm_provider_combobox.setCurrentIndex(area.llm_provider_combobox.findData(provider))
        area.custom_context_limit.setValue(128)
        assert area.custom_context_unit.currentData() == 1000
        assert area.custom_context_limit.value() == 128
        area.resize(440, 880)
        area.show()
        QTest.qWait(30)
        assert area.horizontalScrollBar().maximum() == 0
        assert area.custom_context_limit.isVisible()
    finally:
        area.close()
        area.deleteLater()
        QTest.qWait(10)


def test_subscription_page_can_grow_after_selection(
    app: QApplication, controller: ApiUsageController, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """订阅页面动态增加账户卡片后，外层容器继续容纳新增内容。"""
    monkeypatch.setattr(app, "_api_usage_controller", controller, raising=False)
    monkeypatch.setattr(ChatGPTAccountArea, "load", lambda self: None)
    area = LLMAPIArea(None)
    try:
        area.llm_provider_combobox.setCurrentIndex(area.llm_provider_combobox.findData("chatgpt"))
        area.resize(760, 880)
        area.show()
        QTest.qWait(30)
        card = QLabel("模拟新增账户卡片", area.chatgpt_area.cards_container)
        card.setFixedHeight(1000)
        area.chatgpt_area.cards_layout.addWidget(card)
        area.chatgpt_area.cards_container.show()
        area.chatgpt_area._sync_geometry()
        QTest.qWait(30)
        assert area.llm_stack.height() >= area.chatgpt_area.minimumSizeHint().height()
    finally:
        area.close()
        area.deleteLater()
        QTest.qWait(10)


@pytest.mark.parametrize("draft_test", [False, True])
def test_subscription_never_runs_balance_scripts(draft_test: bool) -> None:
    """即使保存了启用配置或强制测试，订阅目标也不能运行余额脚本。"""
    runner = Mock(side_effect=AssertionError("订阅不应发起查询"))
    service = ApiUsageService(runner=runner)
    snapshots: list[UsageSnapshot] = []
    target = ApiTarget("chatgpt", "https://ignored.example", "synthetic")
    config = ApiUsageConfig(enabled=True, template="generic")
    try:
        assert not service.query(target, config, snapshots.append, test=draft_test)
        runner.assert_not_called()
        assert snapshots == [UsageSnapshot("unavailable", error=CHATGPT_USAGE_NOTICE)]
        assert not ApiUsageConfig.from_dict({"enabled": True}, target).enabled
    finally:
        service.close()


def test_switch_to_subscription_hides_previous_api_balance_immediately(
    controller: ApiUsageController,
) -> None:
    """配置切换早于 Qt 重载时，工具和手动刷新均不能复用旧账户余额。"""
    previous = UsageSnapshot("success", entries=(UsageEntry(remaining=99, unit="USD"),))
    controller.snapshot = previous
    controller._publish()
    old_revision = controller._revision
    controller.config.llm_api_provider.value = "chatgpt"
    controller.config.api_usage_configs.value = ["malformed-legacy-profile"]
    result = build_result(controller._tool_view())
    assert result["status"] == "unavailable"
    assert result["entries"] == []
    assert "订阅" in result["message"]
    runner = Mock(side_effect=AssertionError("订阅不应发起查询"))
    controller.service.runner = runner
    controller.refresh_manual()
    QTest.qWait(20)
    controller._accept((old_revision, previous))
    assert controller.snapshot.status == "unavailable"
    assert not controller.snapshot.entries
    assert not controller.timer.isActive()
    runner.assert_not_called()


def test_subscription_usage_dialog_links_to_official_page(
    controller: ApiUsageController, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """额度页提供官网入口和问号说明，并在切回普通 API 后恢复刷新按钮。"""
    controller.config.llm_api_provider.value = "chatgpt"
    controller.reload()
    open_url = Mock(return_value=True)
    monkeypatch.setattr("ui_qt.components.api_usage.QDesktopServices.openUrl", open_url)
    button = ApiUsageButton(controller=controller)
    try:
        button.show_details()
        assert not button._subscription_help.isHidden()
        assert button._subscription_help.toolTip() == CHATGPT_USAGE_NOTICE
        assert button._refresh.isHidden()
        assert not button._balance_view.cards
        button._manage_usage.click()
        assert open_url.call_count == 1
        assert open_url.call_args.args[0].toString() == "https://chatgpt.com/settings/usage"
        controller.config.llm_api_provider.value = "deepseek"
        controller.reload()
        assert button._manage_usage.isHidden()
        assert button._subscription_help.isHidden()
        assert not button._refresh.isHidden()
    finally:
        button._dialog.close()
        button.close()


def test_subscription_settings_skip_balance_configuration(
    controller: ApiUsageController, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """订阅设置只保留跳转入口，不读取或保存专用额度凭据。"""
    controller.config.llm_api_provider.value = "chatgpt"
    controller.reload()
    save = Mock(side_effect=AssertionError("订阅不保存额度配置"))
    monkeypatch.setattr(controller, "save", save)
    widget = ApiUsageSettingsWidget(lambda: controller.target, controller=controller)
    try:
        assert widget.query_body.isHidden()
        assert widget.query_body.isAncestorOf(widget.key_field)
        assert widget.query_body.isAncestorOf(widget.access_field)
        assert widget.query_body.isAncestorOf(widget.user_field)
        assert widget.enabled.isHidden()
        assert not widget.subscription_info.isHidden()
        assert widget.subscription_help.toolTip() == CHATGPT_USAGE_NOTICE
        assert widget.save()
        widget.test_query()
        save.assert_not_called()
        controller.config.llm_api_provider.value = "deepseek"
        controller.reload()
        widget.load_target()
        assert not widget.query_body.isHidden()
        assert widget.subscription_info.isHidden()
        assert not widget.enabled.isHidden()
    finally:
        widget.close()
