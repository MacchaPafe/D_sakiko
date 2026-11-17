from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from live2d_support.dev_viewer import (
    ModelResources, MotionEntry, PreviewController, PreviewSelection, PreviewState, ViewerViewport,
    fit_viewer_window, read_resources,
)


class ViewerScalingTest(unittest.TestCase):
    """验证低分辨率适配及缩放后的输入与渲染坐标一致。"""

    def test_initial_window_fits_small_desktop_with_chrome_margin(self) -> None:
        """在 1200×800 屏幕上为标题栏和任务栏留出空间。"""
        width, height = fit_viewer_window((1200, 800), (1220, 900))
        self.assertLessEqual(width, 1140)
        self.assertLessEqual(height, 690)
        self.assertAlmostEqual(width / height, 1220 / 900, places=2)
        self.assertEqual(fit_viewer_window((1920, 1080), (1220, 900), 0.75), (915, 675))

    def test_scaled_bottom_buttons_keep_correct_hit_coordinates(self) -> None:
        """小窗口中底部播放和重置按钮仍映射到原来的逻辑位置。"""
        for size in ((915, 675), (1100, 690), (600, 720)):
            viewport = ViewerViewport(size, (1220, 900))
            x, y, width, height = viewport.content
            for logical in ((810, 840), (1105, 840), (800, 550)):
                screen = (round(x + logical[0] * width / 1220), round(y + logical[1] * height / 900))
                mapped = viewport.to_logical(screen)
                self.assertIsNotNone(mapped)
                assert mapped is not None
                self.assertLessEqual(abs(mapped[0] - logical[0]), 2)
                self.assertLessEqual(abs(mapped[1] - logical[1]), 2)
                self.assertLess(screen[1], size[1])

    def test_letterbox_is_not_clickable_and_gl_uses_bottom_origin(self) -> None:
        """宽屏留白不触发侧栏，OpenGL 的 Y 原点与鼠标坐标正确转换。"""
        viewport = ViewerViewport((1200, 600), (1220, 900))
        x, y, width, height = viewport.content
        self.assertGreater(x, 0)
        self.assertIsNone(viewport.to_logical((0, 100)))
        self.assertIsNone(viewport.to_logical((x + width, 100)))
        self.assertEqual(viewport.gl_rect, (x, 600 - y - height, width, height))
        model = viewport.model_rect(760)
        self.assertEqual(model[2], round(width * 760 / 1220))
        self.assertEqual(model[3], height)


class DevViewerTest(unittest.TestCase):
    """验证选择与演出隔离、方向筛选及模型替换时的资源生命周期。"""

    def setUp(self) -> None:
        """准备不依赖 OpenGL 的模型资源和可记录调用的运行时。"""
        self.center = MotionEntry('Default', 2, Path('/models/wave_C.motion3.json'), 2.0)
        self.left = MotionEntry('Default', 3, Path('/models/wave_L.motion3.json'), 2.0)
        self.right = MotionEntry('Default', 4, Path('/models/wave_R.motion3.json'), 2.0)
        self.other = MotionEntry('Idle', 0, Path('/models/idle.motion3.json'), 3.0)
        self.resources = ModelResources(Path('/models/demo.model3.json'), (self.center, self.left, self.right, self.other), (), ())
        self.native = Mock(spec=['StopAllMotions', 'ResetExpressions', 'ResetAllParameters', 'ResetPose'])
        self.model = Mock(version='v3', model=self.native)
        self.model.StartMotion.return_value = True
        self.model.SetExpression.return_value = True
        self.controller = PreviewController(Mock(return_value=self.model), (760, 900))
        self.controller.model = self.model
        self.controller.resources = self.resources

    def test_selection_and_filter_do_not_start_or_replace_playback(self) -> None:
        """修改候选与筛选不触发运行时，也不改变当前播放快照。"""
        state = self.controller.state
        original = PreviewSelection(self.center, 'smile')
        state.playing = original
        state.selected = PreviewSelection(self.left, 'sad')
        self.assertEqual(state.visible_motions(self.resources), (self.center,))
        state.direction = 'R'
        self.assertEqual(state.visible_motions(self.resources), (self.right,))
        self.assertEqual(state.selected.motion, self.left)
        self.assertIs(state.playing, original)
        self.model.StartMotion.assert_not_called()
        self.model.SetExpression.assert_not_called()
        state.direction = 'all'
        self.assertEqual(state.visible_motions(self.resources), self.resources.motions)

    def test_direction_fallback_preserves_real_group_identity(self) -> None:
        """Default 组按文件名筛选，普通文件可回退到组名方向。"""
        self.assertEqual(self.center.direction, 'C')
        fallback = MotionEntry('Greeting_L', 0, Path('/models/wave.motion3.json'), 1.0)
        self.assertEqual(fallback.direction, 'L')
        self.assertIsNone(self.other.direction)

    def test_play_resets_then_starts_motion_and_expression_without_update(self) -> None:
        """组合在同一轮调用中启动，关闭自动表情并在重播前清除历史状态。"""
        selection = PreviewSelection(self.center, 'smile')
        self.controller.state.selected = selection
        recorder = Mock()
        recorder.attach_mock(self.native, 'native')
        recorder.attach_mock(self.model.StartMotion, 'motion')
        recorder.attach_mock(self.model.SetExpression, 'expression')
        expected = [call.native.StopAllMotions(), call.native.ResetExpressions(), call.native.ResetAllParameters(), call.native.ResetPose(),
                    call.motion('Default', 2, priority=3, auto_expression=False), call.expression('smile')]
        self.assertTrue(self.controller.play())
        self.assertEqual(recorder.mock_calls, expected)
        self.assertIs(self.controller.state.playing, selection)
        recorder.reset_mock()
        self.assertTrue(self.controller.play())
        self.assertEqual(recorder.mock_calls, expected)
        self.model.Update.assert_not_called()

    def test_empty_expression_clears_previous_expression_and_empty_pair_is_noop(self) -> None:
        """无表情会清除旧表情，而两项都未选择时不打断当前演出。"""
        self.controller.state.playing = PreviewSelection(self.left, 'sad')
        self.controller.state.selected = PreviewSelection(self.center, None)
        self.controller.play()
        self.native.ResetExpressions.assert_called_once_with()
        self.model.SetExpression.assert_not_called()
        previous = self.controller.state.playing
        self.controller.state.selected = PreviewSelection()
        self.assertFalse(self.controller.play())
        self.assertIs(self.controller.state.playing, previous)
        self.native.ResetExpressions.assert_called_once_with()

    def test_expression_only_and_failed_expression_leave_no_partial_pair(self) -> None:
        """仅表情可播放，组合中表情失败则清除已启动的动作。"""
        self.controller.state.selected = PreviewSelection(None, 'smile')
        self.assertTrue(self.controller.play())
        self.model.StartMotion.assert_not_called()
        self.controller.state.selected = PreviewSelection(self.center, 'broken')
        self.model.SetExpression.return_value = False
        with self.assertRaisesRegex(RuntimeError, '表情启动失败'):
            self.controller.play()
        self.assertIsNone(self.controller.state.playing)
        self.assertEqual(self.native.StopAllMotions.call_count, 3)

    def test_failed_model_setup_keeps_previous_and_disposes_candidate(self) -> None:
        """候选模型准备失败时，旧模型与选择保持可用。"""
        candidate = Mock(version='v3')
        candidate.Resize.side_effect = RuntimeError('resize failed')
        self.controller.factory = Mock(return_value=candidate)
        self.controller.state.selected = PreviewSelection(self.center)
        with patch('live2d_support.dev_viewer.read_resources', return_value=self.resources):
            with self.assertRaisesRegex(RuntimeError, 'resize failed'):
                self.controller.load(self.resources.path)
        candidate.dispose.assert_called_once_with()
        self.model.dispose.assert_not_called()
        self.assertIs(self.controller.model, self.model)
        self.assertEqual(self.controller.state.selected.motion, self.center)

    def test_switch_clears_selection_and_reset_preserves_it(self) -> None:
        """模型切换清空旧选择，完整重置则恢复初始状态并保留有效选择。"""
        candidate = Mock(version='v3')
        self.controller.factory = Mock(return_value=candidate)
        self.controller.state.selected = PreviewSelection(self.center)
        self.controller.state.direction = 'L'
        with patch('live2d_support.dev_viewer.read_resources', return_value=self.resources):
            self.controller.reset()
        self.assertEqual(self.controller.state.selected.motion, self.center)
        self.assertIsNone(self.controller.state.playing)
        self.model.dispose.assert_called_once_with()
        second = Mock(version='v3')
        self.controller.factory = Mock(return_value=second)
        with patch('live2d_support.dev_viewer.read_resources', return_value=self.resources):
            self.controller.load(self.resources.path)
        self.assertFalse(self.controller.state.selected.playable)
        self.assertEqual(self.controller.state.direction, 'C')
        candidate.dispose.assert_called_once_with()

    def test_resources_accept_empty_model_and_reject_missing_required_files(self) -> None:
        """无动作表情的模型仍可查看，V2 与缺失模型主体在调用原生代码前拒绝。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / 'sample.model3.json'
            data = {'Version': 3, 'FileReferences': {'Moc': 'sample.moc3', 'Textures': ['texture.png']}}
            model.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '必需文件不存在'):
                read_resources(model)
            (root / 'sample.moc3').touch()
            (root / 'texture.png').touch()
            resources = read_resources(model)
            self.assertEqual(resources.motions, ())
            self.assertEqual(resources.expressions, ())
            self.assertFalse(PreviewState().mouth)
            data['Version'] = 2
            model.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Version 3'):
                read_resources(model)
            with self.assertRaisesRegex(ValueError, '仅支持'):
                read_resources(root / 'sample.model.json')


if __name__ == '__main__':
    unittest.main()
