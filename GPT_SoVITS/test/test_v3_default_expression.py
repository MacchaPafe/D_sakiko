"""验证默认表情的资源回退、待机保持及对话演出切换。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, call, patch

import pytest

from live2d_support.runtime_adapter import Live2DModelAdapter, Live2DVersion


def create_model(root: Path, expressions: list[tuple[str, str]], *, missing: tuple[str, ...] = (), version: Live2DVersion = "v3") -> tuple[Live2DModelAdapter, Mock]:
    """经过真实 adapter 加载入口，只替换 OpenGL 和原生模型。"""
    for _, file in expressions:
        if file not in missing:
            target = root / file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{"Parameters": []}', encoding="utf-8")
    motion = "mtn_kime01_C.motion3.json"
    (root / motion).write_text('{"Meta": {"Loop": false, "Duration": 1}, "Curves": []}', encoding="utf-8")
    groups = {group: [{"File": motion}] for group in (
        "IDLE", "IDLE_C", "IDLE_L", "IDLE_R", "idle_motion", "idle_motion_C",
        "change_character", "change_character_C", "happiness")}
    path = root / ("sample.model3.json" if version == "v3" else "sample.model.json")
    document = {"Version": 3, "FileReferences": {
        "Expressions": [{"Name": name, "File": file} for name, file in expressions], "Motions": groups}}
    if version == "v2":
        document = {"model": "sample.moc", "expressions": [{"name": name, "file": file} for name, file in expressions]}
    path.write_text(json.dumps(document), encoding="utf-8")
    native = Mock()
    native.GetParamIds.return_value = []
    native.IsMotionFinished.return_value = True
    with patch("live2d_support.runtime_adapter.load_live2d_runtime", return_value=Mock(Model=Mock(return_value=native))), \
            patch("live2d_support.runtime_adapter.glUseProgram"):
        adapter = Live2DModelAdapter.create(str(path))
    return adapter, native


@pytest.mark.parametrize("missing, expected", [
    ((), "smile_alias"),
    (("faces/exp_smile01.exp3.json",), "idle_alias"),
    (("faces/exp_smile01.exp3.json", "exp_idle01.exp3.json"), None),
])
def test_default_uses_existing_files_in_priority_order(tmp_path, missing, expected):
    adapter, native = create_model(tmp_path, [
        ("smile_alias", "faces/exp_smile01.exp3.json"),
        ("idle_alias", "exp_idle01.exp3.json"),
        ("exp_idle02", "exp_idle02.exp3.json"),
    ], missing=missing)
    assert adapter.default_expression_id == expected
    assert native.SetExpression.call_args_list == ([call(expected)] if expected else [])
    # 宿主加载后的 idle 和换人动作不能再覆盖优先选择，也不额外选择 idle02。
    adapter.SetSemanticExpression("idle")
    adapter.StartRandomMotion("change_character", position="C")
    adapter.StartRandomMotion("IDLE", position="C")
    assert native.SetExpression.call_args_list == ([call(expected)] if expected else [])


def test_import_hash_and_registered_id_are_supported(tmp_path):
    adapter, native = create_model(tmp_path, [("happy", "94123086_exp_smile01.exp3.json")])
    assert adapter.default_expression_id == "happy"
    native.SetExpression.assert_called_once_with("happy")
    native.LoadExtraExpression.assert_not_called()


def test_unregistered_default_is_loaded_without_editing_model(tmp_path):
    (tmp_path / "exp_smile01.exp3.json").write_text('{"Parameters": []}', encoding="utf-8")
    adapter, native = create_model(tmp_path, [("idle", "exp_idle01.exp3.json")])
    native.LoadExtraExpression.assert_called_once_with(
        adapter.default_expression_id, str(tmp_path / "exp_smile01.exp3.json"))
    native.SetExpression.assert_called_once_with(adapter.default_expression_id)
    document = json.loads(Path(adapter.model_json_path).read_text(encoding="utf-8"))
    assert document["FileReferences"]["Expressions"] == [{"Name": "idle", "File": "exp_idle01.exp3.json"}]


@pytest.mark.parametrize("group", ["IDLE", "IDLE_C", "IDLE_L", "IDLE_R", "idle_motion", "idle_motion_C"])
def test_idle_restores_default_after_explicit_performance(tmp_path, group):
    adapter, native = create_model(tmp_path, [
        ("exp_smile01", "exp_smile01.exp3.json"),
        ("exp_kime01", "exp_kime01.exp3.json"),
        ("exp_sad01", "exp_sad01.exp3.json"),
    ])
    assert adapter.apply_performance({"expression": "exp_sad01"}, "sadness", context="reply")
    native.SetExpression.assert_called_with("exp_sad01")
    assert adapter.StartMotion(group, 0, 3)
    native.SetExpression.assert_called_with("exp_smile01")
    assert native.SetExpression.call_args_list == [call("exp_smile01"), call("exp_sad01"), call("exp_smile01")]
    # 连续待机不重启表情淡入，动作仍然正常执行。
    adapter.StartMotion(group, 0, 3)
    assert native.SetExpression.call_count == 3
    assert native.StartMotion.call_count >= 2


def test_other_motion_groups_still_select_expressions(tmp_path):
    adapter, native = create_model(tmp_path, [
        ("exp_smile01", "exp_smile01.exp3.json"), ("exp_kime01", "exp_kime01.exp3.json")])
    adapter.StartMotion("happiness", 0, 3)
    native.SetExpression.assert_called_with("exp_kime01")


def test_default_can_be_restored_after_editor_resets_native_expressions(tmp_path):
    from live2d_support.expression_preview import ExpressionPreviewSession

    adapter, native = create_model(tmp_path, [("smile", "exp_smile01.exp3.json")])
    session = ExpressionPreviewSession()
    session.model = adapter
    session.reset()
    assert adapter.SetSemanticExpression("idle")
    assert native.SetExpression.call_args_list == [call("smile"), call("smile")]


def test_default_eye_expression_is_not_overwritten_after_motion_finishes(tmp_path: Path) -> None:
    adapter, native = create_model(tmp_path, [("smile", "exp_smile01.exp3.json")])
    adapter.set_auto_blink_enable(True)
    adapter.StartMotion("IDLE", 0, 3)
    adapter.update()
    native.Update.assert_called_once()
    native.SetAutoBlink.assert_called_with(True)
    native.UpdateBlink.assert_not_called()


def test_expression_failure_falls_back_without_failing_model_load(tmp_path):
    original = Live2DModelAdapter.set_expression_if_supported

    def set_expression(adapter, name):
        return False if name == "smile" else original(adapter, name)

    with patch.object(Live2DModelAdapter, "set_expression_if_supported", set_expression):
        adapter, native = create_model(tmp_path, [
            ("smile", "exp_smile01.exp3.json"), ("idle", "exp_idle01.exp3.json")])
    assert adapter.default_expression_id == "idle"
    native.SetExpression.assert_called_once_with("idle")


def test_v2_keeps_existing_initialization(tmp_path):
    adapter, native = create_model(tmp_path, [("idle", "exp_smile01.exp3.json")], version="v2")
    native.SetExpression.assert_not_called()
    assert adapter.SetSemanticExpression("idle")
    native.SetExpression.assert_called_once_with("idle")
