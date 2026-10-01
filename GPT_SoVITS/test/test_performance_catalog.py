"""验证独立演出目录、兼容解析、编辑和原生状态提交。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live2d_support.performance_catalog import (
    MotionAsset, PerformanceCatalog, load_performance_catalog, performance_config_path,
    projected_model_document, read_config, save_config, shared_config_path,
    cubism_motion_json,
)
from live2d_support.performance_policy import PerformanceState, resolve_performance
from live2d_support.model_normalizer import normalize_model3_for_project
from live2d_support.model_importer import import_live2d_model
from live2d_support.runtime_adapter import Live2DModelAdapter
from performance_types import PerformanceSelection

FIXTURES = json.loads((Path(__file__).parent / "fixtures/performance_cases.json").read_text())


@pytest.mark.parametrize("case", FIXTURES["cases"], ids=lambda item: item["name"])
def test_shared_policy_cases(case: dict[str, object]) -> None:
    """Python 和浏览器消费相同的行为样例。"""
    source = FIXTURES["catalog"]
    catalog = PerformanceCatalog(Path("model.model3.json"), str(case.get("version", "v3")))
    catalog.motions = {key: {direction: MotionAsset(file, {"File": file}) for direction, file in variants.items()}
                       for key, variants in source["motions"].items()}
    catalog.expressions, catalog.groups = source["expressions"], source["groups"]
    plan = resolve_performance(catalog, PerformanceSelection.from_value(case["selection"]),
                               str(case["emotion"]), str(case.get("direction", "C")),
                               PerformanceState(**case.get("current", {})))
    actual = {"legacy_group": plan.legacy_group} if plan.legacy_group else {
        "motion_file": plan.state.motion_file, "expression": plan.state.expression,
        "change_motion": plan.change_motion, "change_expression": plan.change_expression}
    assert actual == case["expected"]


def make_model(root: Path) -> Path:
    """创建包含循环动作和两种表情的自包含测试模型。"""
    root.mkdir(parents=True, exist_ok=True)
    for file in ("mtn_nod01_C.motion3.json", "mtn_nod01_L.motion3.json", "mtn_other_C.motion3.json"):
        (root / file).write_text(json.dumps({"Meta": {"Loop": True, "Duration": 1}, "Curves": []}))
    for file in ("exp_smile01", "exp_sad01"):
        (root / (file + ".exp3.json")).write_text('{"Parameters": []}')
    path = root / "sample.model3.json"
    save_config(path, {"FileReferences": {"Motions": {"happiness": [{"File": "mtn_nod01_C.motion3.json"}],
                        "happiness_L": [{"File": "mtn_nod01_L.motion3.json"}], "unknown": [{"File": "mtn_other_C.motion3.json"}]},
                        "Expressions": [{"Name": name, "File": name + ".exp3.json"} for name in ("exp_smile01", "exp_sad01")]}})
    return path


def test_catalog_inheritance_presets_and_projection(tmp_path: Path) -> None:
    """方向只存在于运行时，覆盖优先且损坏预设不进入提示词。"""
    path = make_model(tmp_path / "tomori/live2D_model")
    save_config(shared_config_path(path), {"series": {"default": {"motions": {"mtn_nod01": "点头回应"}}}})
    save_config(performance_config_path(path), {"expressions": {"exp_smile01": "闭眼微笑"}, "presets": [
        {"id": "valid", "motion": "mtn_nod01", "expression": "exp_smile01"},
        {"id": "bad", "motion": [], "expression": "missing"}]})
    catalog = load_performance_catalog(path)
    assert len(catalog.motions) == 2
    assert catalog.motion("mtn_nod01", "L").file.endswith("_L.motion3.json")
    assert catalog.descriptions["motions"]["mtn_nod01"] == "点头回应"
    assert catalog.sources["motions"]["mtn_nod01"] == "shared"
    assert catalog.descriptions["expressions"]["exp_smile01"] == "闭眼微笑"
    prompt = json.dumps(catalog.prompt_projection())
    assert "_C" not in prompt and ".motion3.json" not in prompt
    assert len(catalog.presets) == 2 and len(catalog.valid_presets()) == 1
    original = path.read_bytes()
    assert "__dsakiko_performance__" in projected_model_document(path)["FileReferences"]["Motions"]
    assert path.read_bytes() == original


@pytest.mark.parametrize("model_name", ["sample", "adv_live2d_tomori_001_casual_spring_01"])
def test_catalog_without_guidance_uses_name_labels(tmp_path: Path, model_name: str) -> None:
    """缺少随模型携带的指导时，仅使用名称标签，不按角色名注入描述。"""
    path = make_model(tmp_path).rename(tmp_path / f"{model_name}.model3.json")
    catalog = load_performance_catalog(path)
    assert catalog.series == "default"
    assert catalog.descriptions["motions"] == {"mtn_nod01": "点头"}
    assert catalog.descriptions["expressions"] == {"exp_sad01": "悲伤", "exp_smile01": "微笑"}
    assert all(source == "name" for values in catalog.sources.values() for source in values.values())
    assert "mtn_other" in catalog.motions
    assert "mtn_other" not in catalog.prompt_projection()["motions"]


def test_tomori_model_guidance_and_explicit_shared_series(tmp_path: Path) -> None:
    """燈模型自身的指导优先，未覆盖项按显式系列继承共享描述。"""
    path = make_model(tmp_path).rename(tmp_path / "adv_live2d_tomori_001_casual_spring_01.model3.json")
    save_config(shared_config_path(path), {"series": {
        "ournote_tomori_001": {"motions": {"mtn_nod01": "共享点头"},
                              "expressions": {"exp_sad01": "共享的悲伤表情"}},
        "default": {"expressions": {"exp_sad01": "另一系列的描述"}},
    }})
    save_config(performance_config_path(path), {
        "series": "ournote_tomori_001", "motions": {"mtn_nod01": "模型包中的点头说明"},
        "expressions": {"exp_smile01": "模型包中的微笑说明"},
    })
    catalog = load_performance_catalog(path)
    assert catalog.series == "ournote_tomori_001"
    assert catalog.descriptions["motions"]["mtn_nod01"] == "模型包中的点头说明"
    assert catalog.sources["motions"]["mtn_nod01"] == "model"
    assert catalog.inherited_descriptions["motions"]["mtn_nod01"] == "共享点头"
    assert catalog.descriptions["expressions"]["exp_sad01"] == "共享的悲伤表情"
    assert catalog.sources["expressions"]["exp_sad01"] == "shared"
    assert catalog.descriptions["expressions"]["exp_smile01"] == "模型包中的微笑说明"


def test_normalization_upgrade_keeps_user_groups_and_assets(tmp_path: Path) -> None:
    """旧模型升级索引保留用户分组，未知动作仍可独立选择。"""
    path = make_model(tmp_path)
    normalize_model3_for_project(str(path))
    data = read_config(path)
    data["DSakiko"]["NormalizedModel3Version"] = 1
    data["FileReferences"]["Motions"]["happiness"] = []
    save_config(path, data)
    assert normalize_model3_for_project(str(path))
    upgraded = read_config(path)
    assert upgraded["FileReferences"]["Motions"]["happiness"] == []
    assert len(upgraded["DSakiko"]["MotionAssets"]) == 3
    assert "mtn_other" in load_performance_catalog(path).motions
    assert not normalize_model3_for_project(str(path))


def test_native_dedup_single_play_and_stale_callback(tmp_path: Path) -> None:
    """循环动作派生为单次；换表情不重播；取消后的回调不生效。"""
    path = make_model(tmp_path)
    native = Mock()
    loaded_data: list[dict[str, object]] = []

    def load_extra(group: str, file: str) -> int:
        """读取临时文件内容，模拟运行时同步加载。"""
        loaded_data.append(json.loads(Path(file).read_text()))
        return len(loaded_data) - 1

    native.LoadExtraMotion.side_effect = load_extra
    adapter = Live2DModelAdapter(str(path), "v3", ModuleType("fake"), native, frozenset(), {},
                                frozenset({"exp_smile01", "exp_sad01"}), frozenset(), {})
    started, finished = Mock(), Mock()
    adapter.apply_performance({"motion": "mtn_nod01", "expression": "exp_smile01"}, "like", context="turn", on_start=started, on_finish=finished)
    start_callback = native.StartMotion.call_args.args[-2]
    callback = native.StartMotion.call_args.args[-1]
    adapter.apply_performance({"motion": "mtn_nod01", "expression": "exp_sad01"}, "like", context="turn", on_finish=finished)
    assert native.StartMotion.call_count == 1
    assert native.SetExpression.call_count == 2
    assert loaded_data[0]["Meta"]["Loop"] is False
    assert json.loads((tmp_path / "mtn_nod01_C.motion3.json").read_text())["Meta"]["Loop"] is True
    adapter.reset_performance()
    start_callback()
    callback()
    started.assert_not_called()
    finished.assert_not_called()


def test_editor_saves_two_presets_for_same_motion(tmp_path: Path) -> None:
    """编辑器允许同动作的多个表情组合，配置无需重载渲染器。"""
    from PyQt5.QtWidgets import QApplication
    from ui.components.live2d_performance_editor import Live2DPerformanceEditor, PerformancePresetDialog

    app = QApplication.instance() or QApplication([])
    path = make_model(tmp_path)
    sent: list[dict[str, object]] = []
    editor = Live2DPerformanceEditor(sent.append)
    editor.load_model(path)
    editor.select_resource("motions", "mtn_nod01")
    for index, expression in enumerate(("exp_smile01", "exp_sad01")):
        editor.select_resource("expressions", expression)
        dialog = PerformancePresetDialog(editor, new=True)
        dialog.name.setText(f"组合 {index}")
        assert dialog.save_preset()
        dialog.close()
    assert len(read_config(performance_config_path(path))["presets"]) == 2
    editor.preview(True)
    assert sent[-1]["motion"] == "mtn_nod01"
    editor.close()
    app.processEvents()


def test_cubism_serialization_preserves_numbers_and_strings() -> None:
    """原生解析器要求十进制和末尾换行，字符串中的指数文本保持不变。"""
    data = {"Meta": {"Loop": False}, "Curves": [{"Segments": [0, 0.000001, -0.000001]}], "UserData": "1e-06"}
    serialized = cubism_motion_json(data)
    assert "0.000001" in serialized
    assert serialized.endswith("\n")
    assert json.loads(serialized) == data


def test_failed_native_start_does_not_suppress_retry(tmp_path: Path) -> None:
    """运行时静默拒绝动作时，表情仍可成功且后续段落重新尝试动作。"""
    path = make_model(tmp_path)
    native = Mock()
    native.LoadExtraMotion.return_value = 0
    native.IsMotionFinished.return_value = True
    adapter = Live2DModelAdapter(str(path), "v3", ModuleType("fake"), native, frozenset(), {},
                                frozenset({"exp_smile01"}), frozenset(), {})
    selection = {"motion": "mtn_nod01", "expression": "exp_smile01"}
    adapter.apply_performance(selection, "like", context="turn")
    assert adapter.performance_state.motion_file is None
    assert adapter.performance_state.expression == "exp_smile01"
    native.IsMotionFinished.return_value = False
    adapter.apply_performance(selection, "like", context="turn")
    assert native.StartMotion.call_count == 2
    assert adapter.performance_state.motion_file is not None


def test_import_preserves_colliding_assets_and_relocates_bindings(tmp_path: Path) -> None:
    """同名的不同动作导入后仍独立，覆盖绑定、预设与原文件均保留。"""
    source = make_model(tmp_path / "source")
    data = read_config(source)
    contents = [json.dumps({"Meta": {"Loop": True, "Duration": i + 1}, "Curves": []}) for i in range(2)]
    for name, content in zip(("a", "b"), contents):
        (source.parent / name).mkdir()
        file = f"{name}/mtn_custom_C.motion3.json"
        (source.parent / file).write_text(content)
        data["FileReferences"]["Motions"][name] = [{"File": file}]
    save_config(source, data)
    save_config(performance_config_path(source), {"bindings": {"b/mtn_custom_C.motion3.json": "custom_b"},
                "motions": {"custom_b": "抬手"}, "presets": [{"id": "p", "motion": "custom_b", "expression": "exp_smile01"}]})
    original = source.read_bytes()
    live2d_root = tmp_path / "live2d_related"
    (live2d_root / "tomori").mkdir(parents=True)
    result = import_live2d_model(str(source), "tomori", str(live2d_root))
    catalog = load_performance_catalog(result.model_json_path)
    custom = {key: variants for key, variants in catalog.motions.items() if "custom" in key}
    assert len(custom) == 2
    assert {catalog.model_path.parent.joinpath(variants["C"].file).read_text() for variants in custom.values()} == set(contents)
    assert catalog.descriptions["motions"]["custom_b"] == "抬手"
    assert len(catalog.valid_presets()) == 1
    assert all((catalog.model_path.parent / file).is_file() for file in catalog.config["bindings"])
    assert source.read_bytes() == original
    assert (source.parent / "b/mtn_custom_C.motion3.json").read_text() == contents[1]


def test_import_preserves_shared_only_description(tmp_path: Path) -> None:
    """没有 sidecar 时也导出共享描述，且不覆盖目标角色的共享文件。"""
    source = make_model(tmp_path / "source")
    save_config(shared_config_path(source), {"series": {"default": {"motions": {"mtn_nod01": "轻轻点头"}}}})
    root = tmp_path / "live2d_related"
    destination_shared = root / "tomori/performance.shared.json"
    save_config(destination_shared, {"series": {"default": {"motions": {"mtn_nod01": "旧描述"}}}})
    original = destination_shared.read_bytes()
    result = import_live2d_model(str(source), "tomori", str(root))
    assert load_performance_catalog(result.model_json_path).descriptions["motions"]["mtn_nod01"] == "轻轻点头"
    assert destination_shared.read_bytes() == original


def test_changed_asset_requires_description_review(tmp_path: Path) -> None:
    """内容变化只触发复核提示，不改变资源 ID 和用户描述。"""
    path = make_model(tmp_path)
    catalog = load_performance_catalog(path)
    save_config(performance_config_path(path), {"motions": {"mtn_nod01": "点头"}, "fingerprints": {
        "motions": {"mtn_nod01": catalog.resource_fingerprint("motions", "mtn_nod01")}}})
    catalog = load_performance_catalog(path)
    assert not catalog.description_needs_review("motions", "mtn_nod01")
    (tmp_path / catalog.motion("mtn_nod01").file).write_text('{"Meta":{"Duration":10},"Curves":[]}')
    assert catalog.description_needs_review("motions", "mtn_nod01")
    assert catalog.descriptions["motions"]["mtn_nod01"] == "点头"


def test_native_segment_gaps_hold_until_whole_turn_finishes(tmp_path: Path) -> None:
    """共享宿主按段换表情，等生成与播放都结束后才恢复待机。"""
    from runtime.single_character_performance import SingleCharacterPerformance

    path = make_model(tmp_path)
    native = Mock()
    native.LoadExtraMotion.return_value = 0
    native.IsMotionFinished.return_value = False
    native.StartMotion.side_effect = lambda group, index, priority, started, finished: started() if started else None
    adapter = Live2DModelAdapter(str(path), "v3", ModuleType("fake"), native, frozenset(), {},
                                frozenset({"exp_smile01", "exp_sad01"}), frozenset(), {})
    player = SingleCharacterPerformance()
    events: list[dict[str, object]] = []
    player.on_event = events.append
    segment: dict[str, object] = {"type": "play_segment", "chat_id": "chat", "turn_id": "turn", "segment_id": 1,
                                 "text": "你好", "emotion": "like", "audio_path": "NO_AUDIO",
                                 "performance": {"motion": "mtn_nod01", "expression": "exp_smile01"}}
    with (patch("runtime.single_character_performance.time.time", return_value=100.0) as wall,
          patch("runtime.single_character_performance.time.monotonic", return_value=100.0) as timer,
          patch.object(player, "audio_busy", return_value=False),
          patch.object(adapter, "StartRandomMotion", return_value=True) as idle):
        player.last_idle = 100
        player.command({"type": "thinking"}, adapter)
        idle.reset_mock()
        player.command(segment, adapter)
        player.update_playback(adapter)
        assert player.audio_started and player.independent_performance
        timer.return_value = wall.return_value = 107
        native.StartMotion.call_args.args[-1]()
        player.update_playback(adapter)
        assert [event["type"] for event in events] == ["playback_started", "playback_complete"]
        timer.return_value = wall.return_value = 125
        player.update_playback(adapter)
        idle.assert_not_called()
        segment = dict(segment, segment_id=2, performance={"motion": "mtn_nod01", "expression": "exp_sad01"})
        player.command(segment, adapter)
        player.update_playback(adapter)
        assert native.StartMotion.call_count == 2
        assert native.SetExpression.call_count == 2
        player.command({"type": "generation_finished"}, adapter)
        timer.return_value = wall.return_value = 132
        native.StartMotion.call_args.args[-1]()
        player.update_playback(adapter)
        idle.assert_not_called()
        timer.return_value = wall.return_value = 135
        player.update_playback(adapter)
        idle.assert_called_once()
        assert adapter.performance_state is None


def test_v3_audio_starts_even_if_both_visual_channels_fail(tmp_path: Path) -> None:
    """视觉失败不等待新动作回调，也不阻塞本段语音。"""
    from runtime.single_character_performance import SingleCharacterPerformance

    model = Mock(version="v3")
    model.apply_performance.return_value = False
    player = SingleCharacterPerformance()
    with (patch.object(player, "audio_busy", return_value=True),
          patch.object(player, "onStartCallback_emotion_version") as audio,
          patch.object(player.wavHandler, "Update", return_value=False)):
        player.command({"type": "play_segment", "chat_id": "c", "turn_id": "t", "audio_path": "voice.wav"}, model)
        player.update_playback(model)
        audio.assert_called_once_with("voice.wav")
        assert player.busy


def test_web_asset_projection_is_non_destructive_and_path_checked(tmp_path: Path) -> None:
    """浏览器获得单次动作视图，磁盘模型与 Loop 设置不被改写。"""
    from dsakiko_webui.backend.assets import AssetRegistry

    path = make_model(tmp_path / "tomori")
    original = path.read_bytes()
    with patch("dsakiko_webui.backend.assets.LIVE2D_ROOT", tmp_path):
        assets = AssetRegistry()
        url = assets.register_live2d_model(path)
        model_id = url.split("/")[-2]
        projected = assets.live2d_document(model_id, path.name)
        motion = projected["FileReferences"]["Motions"]["__dsakiko_performance__"][0]["File"]
        assert motion.endswith("?single=1")
        assert assets.live2d_document(model_id, motion.removesuffix("?single=1"), True)["Meta"]["Loop"] is False
        assert read_config(path.parent / motion.removesuffix("?single=1"))["Meta"]["Loop"] is True
        assert assets.live2d_document(model_id, "../../outside.model3.json") is None
    assert path.read_bytes() == original
