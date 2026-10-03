"""超时模式、请求参数传递与设置控件回归。"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from llm_request_settings import request_timeout


def config():
    return SimpleNamespace(**{key: SimpleNamespace(value=value) for key, value in {
        'llm_request_timeout_seconds': 71,
        'theater_request_timeout_seconds': 137,
        'use_default_deepseek_api': True,
        'llm_temperature': 1.0,
        'llm_top_p': 1.0,
    }.items()})


def test_modes_and_old_configuration():
    cfg = config()
    assert request_timeout(cfg) == 71
    assert request_timeout(cfg, theater=True) == 137
    assert request_timeout(object()) == 30
    assert request_timeout(object(), theater=True) == 100


@pytest.mark.parametrize('value', [0, -1, True, '120', None, float('nan'), 86401])
def test_invalid_values_fall_back(value):
    from qconfig import RequestTimeoutValidator
    validator = RequestTimeoutValidator(100)
    assert not validator.validate(value)
    assert validator.correct(value) == 100


def test_timeout_reaches_tools_plain_json_retry_and_summary_requests():
    import dp_local2
    subject = dp_local2.DSLocalAndVoiceGen.__new__(dp_local2.DSLocalAndVoiceGen)
    subject.d_sakiko_config = config()
    subject.model = 'test-key'
    subject._turn_request_timeout = request_timeout(subject.d_sakiko_config)
    subject._current_deepseek_file_service = lambda: None
    captured = []

    def execute(provider, kwargs):
        captured.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))])

    subject._execute_completion_with_provider_policy = execute
    reasoning = {'_reasoning_snapshot_locked': True}
    subject._prepare_runtime_messages = lambda messages: messages
    subject._run_plain_completion_turn([], reasoning)
    subject._supports_json_object_response_format_for_current_config = lambda: False
    subject._run_json_completion_with_empty_retry([], reasoning)
    subject._completion_with_current_config('rolling-summary', [], **reasoning)
    subject._completion_with_current_config('tool-runtime', [], tools=[], **reasoning)
    assert [kwargs['timeout'] for kwargs in captured] == [71] * 4
    subject._completion_with_current_config('explicit', [], timeout=222, **reasoning)
    assert captured[-1]['timeout'] == 222


@pytest.fixture(scope='module')
def qapp():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_numeric_card_saves_and_tracks_configuration(monkeypatch, qapp):
    from qfluentwidgets import ConfigItem
    from ui.custom_widgets import request_timeout_setting_card as module
    item = ConfigItem('test', 'seconds', 30)
    writer = Mock(side_effect=lambda target, value: setattr(target, 'value', value))
    monkeypatch.setattr(module, 'd_sakiko_config', SimpleNamespace(set=writer))
    card = module.RequestTimeoutSettingCard(item, '大模型请求超时', '说明')
    card.spin_box.setValue(120)
    writer.assert_called_once_with(item, 120)
    item.value = 240
    assert card.spin_box.value() == 240
    assert writer.call_count == 1
    card.resize(540, 90)
    card.show()
    qapp.processEvents()
    assert card.spin_box.geometry().right() < card.width()
    assert card.contentLabel.height() >= card.contentLabel.heightForWidth(card.contentLabel.width())
    card.close()
    card.deleteLater()


@pytest.mark.parametrize('provider_mode', ['default', 'custom', 'standard', 'standard_base'])
def test_theater_all_provider_branches_use_independent_timeout(monkeypatch, provider_mode):
    from queue import Queue
    import litellm
    import dp_local_multi_char as module
    cfg = config()
    cfg.use_default_deepseek_api.value = provider_mode == 'default'
    cfg.enable_custom_llm_api_provider = SimpleNamespace(value=provider_mode == 'custom')
    for name, value in {
        'custom_llm_api_model': 'openai/test', 'custom_llm_api_key': 'test-key',
        'custom_llm_api_url': 'https://invalid.test/v1', 'llm_api_provider': 'openai',
        'llm_api_model': {'openai': 'test'}, 'llm_api_key': {'openai': 'test-key'},
        'llm_api_base_url': {'openai': 'https://invalid.test/v1'} if provider_mode == 'standard_base' else {},
    }.items():
        setattr(cfg, name, SimpleNamespace(value=value))
    cfg.reload_from_disk = Mock()
    monkeypatch.setattr(module, 'd_sakiko_config', cfg)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    captured = []

    def completion(**kwargs):
        captured.append(kwargs)
        raise SystemExit('request captured without network')

    monkeypatch.setattr(litellm, 'completion', completion)
    subject = module.DSLocalAndVoiceGen.__new__(module.DSLocalAndVoiceGen)
    subject.model = 'test-key'
    subject.base_prompt = 'test'
    subject.character_list = [SimpleNamespace(character_name=name, character_description='test') for name in ['甲', '乙']]
    commands = Queue()
    commands.put({'char_index': [0, 1], 'user_input': {}})
    with pytest.raises(SystemExit, match='request captured'):
        subject.text_generator(Queue(), commands, Queue())
    assert captured[0]['timeout'] == 137
    cfg.reload_from_disk.assert_called_once()
