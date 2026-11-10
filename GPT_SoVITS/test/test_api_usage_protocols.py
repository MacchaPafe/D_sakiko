"""自定义 Completion 与 Responses 额度连接、配置和快照隔离回归。"""
from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from qfluentwidgets import qconfig as fluent_config

from chat.api_usage import (
    ApiTarget, ApiUsageConfig, ApiUsageService, UsageEntry, UsageSecretStore, UsageSnapshot, resolve_target,
)
from chat.api_usage_tool import build_result
from qconfig import d_sakiko_config
from test.test_api_usage_ui import Backend, Config, Item, app, wait
from ui_fluent_widget.components.llm_api_area import LLMAPIArea
from ui_qt.components.api_usage import ApiUsageController


class ProtocolConfig(Config):
    """以同一站点的不同聊天密钥模拟两个协议，不访问真实配置。"""

    def __init__(self) -> None:
        """显式保存两个协议字段与独立的关闭自动查询偏好。"""
        super().__init__()
        self.enable_custom_llm_api_provider.value = True
        self.custom_llm_api_url.value = "https://relay.example/v1"
        self.custom_llm_api_key.value = "synthetic-completion"
        self.custom_llm_api_protocol = Item("chat-completions")
        self.custom_responses_url = Item("https://relay.example/v1")
        self.custom_responses_key = Item("synthetic-responses")
        self.api_usage_configs.value = {
            ApiTarget("custom", self.custom_llm_api_url.value).config_key:
                ApiUsageConfig(template="generic", auto_interval=3).to_dict(),
            ApiTarget("custom_responses", self.custom_responses_url.value).config_key:
                ApiUsageConfig(template="generic", auto_interval=7).to_dict(),
        }


@pytest.fixture
def protocol_controller(app: QApplication) -> Iterator[ApiUsageController]:
    """使用合成响应与内存凭据库创建真实额度控制器。"""
    service = ApiUsageService(
        secrets=UsageSecretStore(Backend()),
        runner=Mock(return_value={"data": {"remaining": 8, "unit": "USD"}}),
    )
    instance = ApiUsageController(ProtocolConfig(), service=service)
    QTest.qWait(20)
    try:
        yield instance
    finally:
        instance.close()


@pytest.mark.parametrize("protocol,provider,key", [
    ("chat-completions", "custom", "synthetic-completion"),
    ("responses", "custom_responses", "synthetic-responses"),
])
def test_resolve_current_protocol_credentials(protocol: str, provider: str, key: str) -> None:
    """运行时读取当前协议自己的地址和密钥，并保留 Completion 的旧配置键。"""
    config = ProtocolConfig()
    config.custom_llm_api_protocol.value = protocol
    config.custom_responses_url.value = "https://responses.example/v1/"
    target = resolve_target(config)
    expected_base = "https://responses.example/v1" if protocol == "responses" else "https://relay.example/v1"
    assert target == ApiTarget(provider, expected_base, key)
    if protocol == "chat-completions":
        assert target.config_key == ApiTarget("custom", expected_base).config_key


def test_legacy_configuration_without_protocol_remains_completion() -> None:
    """没有协议字段的旧配置继续使用原来的自定义 API 查询配置。"""
    config = Config()
    config.enable_custom_llm_api_provider.value = True
    config.custom_llm_api_url.value = "https://legacy.example/v1"
    config.custom_llm_api_key.value = "synthetic-legacy"
    assert resolve_target(config) == ApiTarget("custom", "https://legacy.example/v1", "synthetic-legacy")


def test_protocol_profiles_and_secret_references_are_independent(
    protocol_controller: ApiUsageController,
) -> None:
    """相同站点分别保存查询参数和凭据，修改 Responses 不覆盖 Completion。"""
    controller = protocol_controller
    completion = controller.target
    responses = ApiTarget("custom_responses", completion.base_url, "synthetic-responses")
    assert completion.config_key != responses.config_key
    assert controller.save(completion, ApiUsageConfig(template="generic", auto_interval=3),
                           {"api_key": "synthetic-completion-override"})
    original = controller.read_config(completion).to_dict()
    assert controller.save(responses, ApiUsageConfig(template="generic", auto_interval=7),
                           {"api_key": "synthetic-responses-override"})
    assert controller.save(responses, ApiUsageConfig(template="generic", auto_interval=11),
                           {"api_key": "synthetic-responses-rotated"})
    assert controller.read_config(completion).to_dict() == original
    completion_ref = controller.read_config(completion).api_key_ref
    responses_ref = controller.read_config(responses).api_key_ref
    assert completion_ref != responses_ref
    assert controller.service.secrets.get(completion_ref) == "synthetic-completion-override"
    assert controller.service.secrets.get(responses_ref) == "synthetic-responses-rotated"
    # 新控制器从同一份已保存配置及凭据库重新加载，不依赖设置卡片的临时状态。
    restored = ApiUsageController(controller.config, polling=False,
        service=ApiUsageService(secrets=controller.service.secrets))
    try:
        assert restored.read_config(completion).auto_interval == 3
        assert restored.read_config(responses).auto_interval == 11
    finally:
        restored.close()


@pytest.mark.parametrize("field,value", [
    ("custom_llm_api_protocol", "chat-completions"),
    ("custom_responses_url", "https://new-responses.example/v1"),
    ("custom_responses_key", "synthetic-responses-new"),
])
def test_protocol_changes_invalidate_snapshot_and_reload(
    protocol_controller: ApiUsageController, field: str, value: str,
) -> None:
    """协议、Responses 地址或密钥变化立即隔离工具快照，随后自动重载控制器。"""
    controller = protocol_controller
    controller.config.custom_llm_api_protocol.value = "responses"
    controller.reload()
    previous = UsageSnapshot("success", entries=(UsageEntry(remaining=99, unit="USD"),))
    controller.snapshot = previous
    controller._publish()
    old_revision = controller._revision
    controller.config.set(getattr(controller.config, field), value)
    result = build_result(controller._tool_view())
    assert result["status"] == "loading" and result["entries"] == []
    controller._accept((old_revision, previous))
    wait(lambda: controller._revision != old_revision)
    assert controller.target == resolve_target(controller.config)
    assert not controller.snapshot.entries
    controller._accept((old_revision, previous))
    assert not controller.snapshot.entries
    controller.service.runner.assert_not_called()


def test_manual_refresh_before_reload_uses_current_responses_connection(
    protocol_controller: ApiUsageController,
) -> None:
    """切换协议后立即刷新，也只向 Responses 当前地址传递对应密钥。"""
    controller = protocol_controller
    controller.config.custom_responses_url.value = "https://responses.example/v1"
    target = ApiTarget("custom_responses", "https://responses.example/v1", "synthetic-responses")
    controller.config.api_usage_configs.value[target.config_key] = ApiUsageConfig(template="generic").to_dict()
    controller.config.set(controller.config.custom_llm_api_protocol, "responses")
    controller.refresh_manual()
    wait(lambda: controller.snapshot.status == "success")
    controller.service.runner.assert_called_once()
    payload = controller.service.runner.call_args.args[0]
    assert payload["variables"]["baseUrl"] == "https://responses.example/v1"
    assert payload["variables"]["apiKey"] == "synthetic-responses"
    assert not controller.usage_config.enabled
    assert not controller.timer.isActive()


def test_same_endpoint_and_key_do_not_share_protocol_cache(
    protocol_controller: ApiUsageController,
) -> None:
    """即使两个协议使用同一地址和密钥，新协议加载中也不展示另一协议缓存。"""
    controller = protocol_controller
    controller.config.custom_responses_key.value = controller.config.custom_llm_api_key.value
    controller.refresh_manual()
    wait(lambda: controller.snapshot.status == "success")
    received: list[UsageSnapshot] = []
    controller.snapshotChanged.connect(received.append)
    controller.config.set(controller.config.custom_llm_api_protocol, "responses")
    controller.refresh_manual()
    wait(lambda: controller.snapshot.status == "success" and controller.target.provider_id == "custom_responses")
    assert any(snapshot.status == "loading" for snapshot in received)
    assert all(not snapshot.entries for snapshot in received if snapshot.status != "success")
    assert controller.service.runner.call_count == 2


def test_settings_switch_protocol_profiles_and_query_current_draft(
    app: QApplication, protocol_controller: ApiUsageController, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """设置页按协议恢复额度草稿，Responses 测试查询使用可见表单而非隐藏标准密钥。"""
    controller = protocol_controller
    monkeypatch.setattr(app, "_api_usage_controller", controller, raising=False)
    monkeypatch.setattr(d_sakiko_config, "set", Mock())
    monkeypatch.setattr(fluent_config, "set", Mock())
    for name, value in {
        "use_default_deepseek_api": True,
        "custom_llm_api_url": "https://relay.example/v1",
        "custom_llm_api_key": "synthetic-completion",
        "custom_responses_url": "https://relay.example/v1",
        "custom_responses_key": "synthetic-responses",
    }.items():
        monkeypatch.setattr(getattr(d_sakiko_config, name), "value", value)
    area = LLMAPIArea(None)
    try:
        area.standard_key_input.setText("synthetic-unrelated-standard")
        area.llm_provider_combobox.setCurrentIndex(area.llm_provider_combobox.findData("custom"))
        widget = area.api_usage_settings
        assert widget.interval.value() == 3
        old_revision = widget._test_revision
        old_target = area._usage_draft_target()
        area.llm_provider_combobox.setCurrentIndex(area.llm_provider_combobox.findData("custom_responses"))
        target = area._usage_draft_target()
        assert target == ApiTarget("custom_responses", "https://relay.example/v1", "synthetic-responses")
        assert widget.interval.value() == 7
        widget._accept_test((old_revision, old_target, UsageSnapshot("success", entries=(UsageEntry(remaining=99),))))
        assert not widget.result.text()
        widget.interval.setValue(11)
        assert widget.save()
        widget.test_query()
        wait(lambda: "剩余：8" in widget.result.text())
        payload = controller.service.runner.call_args.args[0]
        assert payload["variables"]["apiKey"] == "synthetic-responses"
        assert payload["variables"]["baseUrl"] == target.base_url
        assert controller.target.provider_id == "custom"
        area.llm_provider_combobox.setCurrentIndex(area.llm_provider_combobox.findData("custom"))
        assert widget.interval.value() == 3
        area.llm_provider_combobox.setCurrentIndex(area.llm_provider_combobox.findData("custom_responses"))
        assert widget.interval.value() == 11
    finally:
        area.close()
        area.deleteLater()
        QTest.qWait(10)
