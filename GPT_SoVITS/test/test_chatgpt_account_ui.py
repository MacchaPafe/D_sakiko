"""账户与全局模型自动保存，以及主程序一次提示的离屏交互验证。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt5.QtWidgets import QApplication, QWidget

from llm.chatgpt.session import ChatGPTSession
from llm.chatgpt.storage import AccountStore, write_private_json
from test.test_chatgpt_subscription import MemoryVault, account
from ui_fluent_widget.components import chatgpt_account_area as module


class Settings:
    """只在内存记录配置提交，避免测试修改用户真实配置。"""

    def __init__(self, model: str = 'model-M') -> None:
        """建立与配置页使用字段一致的替身。"""
        for name, value in {'chatgpt_account_id': '', 'chatgpt_model': model, 'use_default_deepseek_api': True,
                            'enable_custom_llm_api_provider': False, 'llm_api_provider': 'deepseek',
                            'chatgpt_welcome_seen': True, 'chatgpt_announcement_seen': False}.items():
            setattr(self, name, SimpleNamespace(value=value))
        self.writes: list[object] = []

    def set(self, item: object, value: object) -> None:
        """记录已提交值。"""
        setattr(item, 'value', value)
        self.writes.append(value)

    def __enter__(self) -> Settings:
        """模拟配置事务。"""
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """内存测试不访问磁盘。"""
        return None


@pytest.fixture(scope='module')
def app() -> QApplication:
    """共用一个离屏 Qt 应用。"""
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('first_model', ['', 'model-M'])
def test_login_saved_immediately_and_model_is_global(app: QApplication, tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch, first_model: str) -> None:
    """新登录自动成为活动账户，首次选模立即启用，切换账户不重置模型。"""
    settings = Settings(first_model)
    monkeypatch.setattr(module, 'd_sakiko_config', settings)
    monkeypatch.setattr(module, 'notify_config_reload', Mock(return_value=True))
    store = AccountStore(tmp_path, MemoryVault())
    first, second = account(store), account(store)
    models = [{'slug': 'model-M', 'display_name': '模型 M'}, {'slug': 'model-N', 'display_name': '模型 N'}]
    for value in (first, second):
        write_private_json(tmp_path / 'models' / (value.id + '.json'), {'models': models})
    panel = module.ChatGPTAccountArea(session=ChatGPTSession(store))
    monkeypatch.setattr(panel, 'refresh_models', lambda *args: None)
    panel._signed_in(first)
    assert settings.chatgpt_account_id.value == first.id
    if not first_model:
        assert settings.use_default_deepseek_api.value is True
        panel.model_combo.setCurrentIndex(panel.model_combo.findData('model-M'))
    assert settings.llm_api_provider.value == 'chatgpt'
    panel._signed_in(second)
    assert settings.chatgpt_account_id.value == second.id
    assert settings.chatgpt_model.value == 'model-M'
    panel.model_combo.setCurrentIndex(panel.model_combo.findData('model-N'))
    panel.account_cards[first.id].radio_button.click()
    assert settings.chatgpt_account_id.value == first.id
    assert settings.chatgpt_model.value == 'model-N'
    assert type(panel.model_combo).__module__.startswith('qfluentwidgets')
    panel.close()


def test_standard_qt_announcement_shown_once(app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    """主程序使用标准 Qt，首次实际展示后即记录，不因未点击而重复弹出。"""
    from ui_qt.components import chatgpt_announcement as announcement
    settings = Settings()
    monkeypatch.setattr(announcement, 'd_sakiko_config', settings)
    owner = QWidget()
    banner = announcement.ChatGPTAnnouncement(owner)
    assert settings.chatgpt_announcement_seen.value is False
    owner.show()
    app.processEvents()
    assert settings.chatgpt_announcement_seen.value is True
    another = announcement.ChatGPTAnnouncement(owner)
    assert another.isHidden()
    owner.close()


def test_account_cards_relogin_and_add_new_account(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """卡片更多菜单支持重登已有注册，待登录账户不提前替换活动账户，顶部按钮添加新账户。"""
    settings = Settings()
    monkeypatch.setattr(module, 'd_sakiko_config', settings)
    monkeypatch.setattr(module, 'notify_config_reload', Mock(return_value=True))
    store = AccountStore(tmp_path, MemoryVault())
    active, saved = account(store), account(store)
    store.clear_tokens(saved)
    settings.chatgpt_account_id.value = active.id
    panel = module.ChatGPTAccountArea(session=ChatGPTSession(store))
    monkeypatch.setattr(panel, 'refresh_models', lambda *args: None)
    login = Mock()
    monkeypatch.setattr(panel, 'sign_in', login)
    panel.load(active.id)
    assert set(panel.account_cards) == {active.id, saved.id}
    panel.select_account(saved.id)
    assert settings.chatgpt_account_id.value == active.id
    panel.account_cards[saved.id].relogin_action.trigger()
    login.assert_called_once_with(saved.id)
    panel.add_button.click()
    assert login.call_args.args == (None,)
    assert settings.chatgpt_account_id.value == active.id
    panel.close()


def test_login_banner_visibility_and_compliance_dialog(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """备份登录链接与取消按钮平时隐藏，仅在登录等待期间展示；合规说明弹窗正常构建。"""
    settings = Settings()
    monkeypatch.setattr(module, 'd_sakiko_config', settings)
    store = AccountStore(tmp_path, MemoryVault())
    panel = module.ChatGPTAccountArea(session=ChatGPTSession(store))
    panel.load()
    assert panel.login_banner.isHidden()
    assert not panel.empty_card.isHidden()
    panel._busy(True, is_login=True)
    assert not panel.login_banner.isHidden()
    panel._set_login_url('https://auth.openai.com/authorize?test=1')
    assert panel.copy_button.isEnabled()
    panel._busy(False)
    assert panel.login_banner.isHidden()

    dialog = module.ChatGPTComplianceDialog(panel)
    assert '安全与合规说明' in dialog.title_label.text()
    dialog.close()
    panel.close()
