from __future__ import annotations

import os
import sys
import unittest
from types import ModuleType
from unittest.mock import patch

script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from live2d_support.expression_policy import (
    normalized_name_tokens,
    select_expression_for_motion,
    select_supported_expression,
    semantic_expression_candidates,
)
from live2d_support.motion_capabilities import motion_capabilities_from_motion_files_by_group
from live2d_support.motion_selection import resolve_positioned_motion_group, select_random_motion
from live2d_support.runtime_adapter import Live2DModelAdapter


class FakeExtraMotionModel:
    """测试用 Live2D V3 模型，记录外部动作加载和播放调用。"""

    def __init__(self) -> None:
        """初始化测试模型的调用记录。"""
        self.load_calls: list[tuple[str, str]] = []
        self.start_calls: list[tuple[str, int, int, object | None, object | None]] = []
        self.motion_counts_by_group: dict[str, int] = {}

    def LoadExtraMotion(self, group: str, motion_json_path: str) -> int:
        """模拟把外部动作追加加载到指定动作组。"""
        motion_index = self.motion_counts_by_group.get(group, 0)
        self.motion_counts_by_group[group] = motion_index + 1
        self.load_calls.append((group, motion_json_path))
        return motion_index

    def StartMotion(
            self,
            group: str,
            motion_index: int,
            priority: int,
            on_start: object | None = None,
            on_finish: object | None = None,
    ) -> None:
        """记录指定动作组和 index 的播放请求。"""
        self.start_calls.append((group, motion_index, priority, on_start, on_finish))


def create_v3_preview_adapter(model: FakeExtraMotionModel) -> Live2DModelAdapter:
    """创建只用于外部动作预览测试的 V3 adapter。"""
    return Live2DModelAdapter(
        model_json_path="model.model3.json",
        version="v3",
        runtime=ModuleType("fake_live2d_v3"),
        model=model,
        motion_groups=frozenset(),
        motion_files_by_group={},
        expression_ids=frozenset(),
        parameter_ids=frozenset(),
        preview_motion_indices_by_path={},
    )


class Live2DExpressionPolicyTestCase(unittest.TestCase):
    """测试 Live2D 表情策略。"""

    def test_normalized_name_tokens_removes_numeric_suffix(self) -> None:
        """验证动作文件名 token 会去除数字后缀。"""
        tokens = normalized_name_tokens("motions/mtn_smile01_C.motion3.json")

        self.assertIn("smile", tokens)
        self.assertIn("c", tokens)

    def test_select_supported_expression_keeps_candidate_order(self) -> None:
        """验证候选表按优先级选择第一个受支持表情。"""
        expression_id = select_supported_expression(
            ("exp_missing", "exp_smile02", "exp_smile01"),
            {"exp_smile01", "exp_smile02"},
        )

        self.assertEqual(expression_id, "exp_smile02")

    def test_semantic_expression_candidates_resolves_known_key(self) -> None:
        """验证语义表情名会解析到候选表。"""
        candidates = semantic_expression_candidates(" Happiness ")

        self.assertIsNotNone(candidates)
        self.assertEqual(candidates[0], "exp_smile02")

    def test_select_expression_for_motion_prefers_motion_file_token(self) -> None:
        """验证动作文件名 token 优先于动作组语义。"""
        expression_id = select_expression_for_motion(
            "idle",
            "mtn_smile01.motion3.json",
            {"exp_idle01", "exp_smile01"},
        )

        self.assertEqual(expression_id, "exp_smile01")

    def test_select_expression_for_motion_falls_back_to_idle(self) -> None:
        """验证没有动作命中特征时回退到 idle 表情。"""
        expression_id = select_expression_for_motion(
            "unknown_group",
            "motion.motion3.json",
            {"exp_idle01"},
        )

        self.assertEqual(expression_id, "exp_idle01")


class Live2DMotionSelectionTestCase(unittest.TestCase):
    """测试 Live2D 动作选择策略。"""

    def test_resolve_positioned_motion_group_prefers_position_group(self) -> None:
        """验证存在位置动作组时优先选择位置组。"""
        group_name = resolve_positioned_motion_group(
            "happiness",
            "L",
            {"happiness", "happiness_L"},
        )

        self.assertEqual(group_name, "happiness_L")

    def test_resolve_positioned_motion_group_falls_back_to_base_group(self) -> None:
        """验证缺少位置动作组时回退基础组。"""
        group_name = resolve_positioned_motion_group(
            "happiness",
            "R",
            {"happiness", "happiness_L"},
        )

        self.assertEqual(group_name, "happiness")

    def test_select_random_motion_uses_positioned_group(self) -> None:
        """验证指定动作组会在位置组中选择动作。"""
        selected_motion = select_random_motion(
            "happiness",
            "L",
            {"happiness", "happiness_L"},
            {"happiness": ("base.motion3.json",), "happiness_L": ("left.motion3.json",)},
        )

        self.assertEqual(selected_motion, ("happiness_L", 0))

    def test_select_random_motion_can_pick_from_all_non_empty_groups(self) -> None:
        """验证未指定动作组时会从非空动作组选择动作。"""
        with patch("live2d_support.motion_selection.random.choice", return_value="idle"):
            selected_motion = select_random_motion(
                None,
                None,
                {"idle", "empty"},
                {"idle": ("idle.motion3.json",), "empty": ()},
            )

        self.assertEqual(selected_motion, ("idle", 0))


class Live2DMotionCapabilitiesTestCase(unittest.TestCase):
    """测试 Live2D 方向动作能力查询。"""

    def test_capabilities_use_non_empty_standard_position_groups(self) -> None:
        """验证方向能力只来自非空的项目标准方向变体组。"""
        capabilities = motion_capabilities_from_motion_files_by_group({
            "happiness": ("base.motion3.json",),
            "happiness_L": ("left.motion3.json",),
            "happiness_R": (),
            "custom_L": ("custom_left.motion3.json",),
        })

        self.assertTrue(capabilities.supports_position("L"))
        self.assertFalse(capabilities.supports_position("R"))
        self.assertTrue(capabilities.supports_group_position("happiness", "L"))
        self.assertFalse(capabilities.supports_group_position("happiness", "R"))

    def test_adapter_capabilities_ignore_preview_group(self) -> None:
        """验证 adapter 能力查询不受动作编辑器预览组影响。"""
        adapter = Live2DModelAdapter(
            model_json_path="model.model3.json",
            version="v3",
            runtime=ModuleType("fake_live2d_v3"),
            model=FakeExtraMotionModel(),
            motion_groups=frozenset({"happiness", Live2DModelAdapter.PREVIEW_MOTION_GROUP}),
            motion_files_by_group={
                "happiness": ("base.motion3.json",),
                Live2DModelAdapter.PREVIEW_MOTION_GROUP: ("preview_L.motion3.json",),
            },
            expression_ids=frozenset(),
            parameter_ids=frozenset(),
            preview_motion_indices_by_path={},
        )

        self.assertFalse(adapter.supports_positioned_motion("L"))


class Live2DPreviewMotionTestCase(unittest.TestCase):
    """测试 Live2D 外部动作预览。"""

    def test_start_motion_file_reuses_loaded_v3_extra_motion(self) -> None:
        """验证同一个 V3 外部动作文件不会被重复追加加载。"""
        model = FakeExtraMotionModel()
        adapter = create_v3_preview_adapter(model)
        motion_path = "mtn_smile01.motion3.json"

        self.assertTrue(adapter.start_motion_file(motion_path))
        self.assertTrue(adapter.start_motion_file(motion_path))

        self.assertEqual(model.load_calls, [(Live2DModelAdapter.PREVIEW_MOTION_GROUP, motion_path)])
        self.assertEqual(
            model.start_calls,
            [
                (Live2DModelAdapter.PREVIEW_MOTION_GROUP, 0, 3, None, None),
                (Live2DModelAdapter.PREVIEW_MOTION_GROUP, 0, 3, None, None),
            ],
        )
        self.assertEqual(
            adapter.motion_files_by_group[Live2DModelAdapter.PREVIEW_MOTION_GROUP],
            (motion_path,),
        )

    def test_start_motion_file_appends_different_v3_extra_motions(self) -> None:
        """验证不同 V3 外部动作文件会追加到统一预览动作组。"""
        model = FakeExtraMotionModel()
        adapter = create_v3_preview_adapter(model)
        first_motion_path = "mtn_smile01.motion3.json"
        second_motion_path = "mtn_angry01.motion3.json"

        self.assertTrue(adapter.start_motion_file(first_motion_path))
        self.assertTrue(adapter.start_motion_file(second_motion_path))

        self.assertEqual(
            model.load_calls,
            [
                (Live2DModelAdapter.PREVIEW_MOTION_GROUP, first_motion_path),
                (Live2DModelAdapter.PREVIEW_MOTION_GROUP, second_motion_path),
            ],
        )
        self.assertEqual(
            model.start_calls,
            [
                (Live2DModelAdapter.PREVIEW_MOTION_GROUP, 0, 3, None, None),
                (Live2DModelAdapter.PREVIEW_MOTION_GROUP, 1, 3, None, None),
            ],
        )
        self.assertEqual(
            adapter.motion_files_by_group[Live2DModelAdapter.PREVIEW_MOTION_GROUP],
            (first_motion_path, second_motion_path),
        )


if __name__ == "__main__":
    unittest.main()
