"""轮播画面回归：遮挡、补位、循环落位及缩放时的视觉连续性。"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QEvent, QPointF, Qt
from PyQt5.QtGui import QColor, QImage, QPixmap, QMouseEvent
from PyQt5.QtWidgets import QApplication

from live2d_download.hosted.catalog import BANDS
from live2d_download.hosted.ui import CharacterCarousel


class CarouselRenderingTests(unittest.TestCase):
    """用真实离屏绘制检查动画边界，而不访问网络。"""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        """复用 Qt 应用。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """准备七位角色，以便区分可见立绘与隐藏补位。"""
        self.carousel = CharacterCarousel()
        self.carousel.resize(1000, 650)
        self.members = BANDS[0][2] + BANDS[1][2][:2]
        for index, character in enumerate(self.members):
            picture = QPixmap(60, 100)
            picture.fill(QColor.fromHsv(index * 45, 180, 220))
            self.carousel.pictures[character] = picture
        self.set_members(7)
        self.carousel.show()
        self.app.processEvents()
        self.carousel.arrow_hover_timer.stop()
        for layer in self.carousel.arrow_layers:
            layer.hide()

    def tearDown(self) -> None:
        """清理动画与测试窗口。"""
        self.carousel.slide.stop()
        for animation in (*self.carousel.fades.values(), *self.carousel.arrow_fades):
            animation.stop()
        self.carousel.close()
        self.carousel.deleteLater()
        self.app.processEvents()

    def set_members(self, count: int) -> None:
        """切换测试分组并完成首次展示淡入。"""
        self.carousel.set_members(self.members[:count])
        for fade in self.carousel.fades.values():
            fade.setCurrentTime(fade.duration())

    def frame(self) -> QImage:
        """获取包含透明度和裁剪结果的实际画面。"""
        return self.carousel.grab().toImage()

    def mouse(self, event_type: QEvent.Type, x: int) -> None:
        """向真实控件投递鼠标事件，覆盖按下、跟手与松手流程。"""
        button = Qt.NoButton if event_type == QEvent.MouseMove else Qt.LeftButton
        buttons = Qt.NoButton if event_type == QEvent.MouseButtonRelease else Qt.LeftButton
        event = QMouseEvent(event_type, QPointF(x, self.carousel.height()/2), button, buttons, Qt.NoModifier)
        self.app.sendEvent(self.carousel, event)

    def finish_slide(self) -> None:
        """同步完成已启动的吸附动画。"""
        if self.carousel.slide.state() != self.carousel.slide.Stopped:
            self.carousel.slide.setCurrentTime(self.carousel.slide.duration())

    def test_drag_multiple_turns_then_snap_without_intermediate_selection(self) -> None:
        """双向拖动两圈以上仍按固定比例跟手，只在最终吸附时提交一次选择。"""
        selected: list[str] = []
        self.carousel.selected.connect(selected.append)
        for direction in (-1, 1):
            with self.subTest(direction=direction):
                self.set_members(7)
                selected.clear()
                start = self.carousel.width() // 2
                self.mouse(QEvent.MouseButtonPress, start)
                step = self.carousel.card_step()
                for slots in (.25, 1.25, 7.25, 16.75):
                    finish = start + round(direction * step * slots)
                    self.mouse(QEvent.MouseMove, finish)
                    self.assertAlmostEqual(self.carousel.drag_offset / step, (finish - start) / step)
                    self.assertEqual(self.carousel.index, 0)
                    self.assertFalse(selected)
                    self.assertLessEqual(len(self.carousel.card_offsets()), 7)
                    self.assertGreaterEqual(len(self.carousel.card_layers()), 5)
                    self.frame()
                    self.assertLessEqual(len(self.carousel._card_images), 7)
                self.mouse(QEvent.MouseButtonRelease, finish)
                self.assertFalse(selected)
                self.finish_slide()
                expected = (-direction * 17) % 7
                self.assertEqual(self.carousel.index, expected)
                self.assertEqual(selected, [self.members[expected]])
                self.assertEqual(self.carousel.drag_offset, 0.0)

    def test_whole_turn_matches_original_frame_and_settles_immediately(self) -> None:
        """整圈拖动回到原角色，亚像素误差无需再等待吸附动画。"""
        before = self.frame()
        start = self.carousel.width() // 2
        self.mouse(QEvent.MouseButtonPress, start)
        finish = start - round(len(self.members) * self.carousel.card_step())
        self.mouse(QEvent.MouseMove, finish)
        self.mouse(QEvent.MouseButtonRelease, finish)
        self.assertEqual(self.carousel.slide.state(), self.carousel.slide.Stopped)
        self.assertEqual(self.carousel.index, 0)
        self.assertEqual(self.frame(), before)

    def test_reversing_drag_uses_final_nearest_role_and_short_remaining_animation(self) -> None:
        """长拖后反向回到另一侧时，以最终位置就近吸附且仅补齐剩余距离。"""
        start = self.carousel.width() // 2
        self.mouse(QEvent.MouseButtonPress, start)
        step = self.carousel.card_step()
        self.mouse(QEvent.MouseMove, start + round(step * 9.7))
        finish = start - round(step * 2.9)
        self.mouse(QEvent.MouseMove, finish)
        remaining = abs((finish - start) / step + 3)
        self.mouse(QEvent.MouseButtonRelease, finish)
        self.assertEqual(self.carousel.slide.duration(), round(320 * remaining))
        self.assertLess(self.carousel.slide.duration(), 40)
        self.finish_slide()
        self.assertEqual(self.carousel.index, 3)
        self.carousel.shift(1)
        self.assertEqual(self.carousel.slide.duration(), 320)
        self.finish_slide()
        self.assertEqual(self.carousel.index, 4)

    def test_short_side_drag_snaps_back_but_side_click_moves_one_role(self) -> None:
        """在侧卡上拖动不足半槽位应回弹，只有真正的点击才触发单步切换。"""
        start = 40
        finish = start + round(self.carousel.card_step() * .3)
        self.mouse(QEvent.MouseButtonPress, start)
        self.mouse(QEvent.MouseMove, finish)
        self.mouse(QEvent.MouseButtonRelease, finish)
        self.finish_slide()
        self.assertEqual(self.carousel.index, 0)
        self.mouse(QEvent.MouseButtonPress, start)
        self.mouse(QEvent.MouseButtonRelease, start)
        self.finish_slide()
        self.assertEqual(self.carousel.index, 6)

    def test_long_drag_recycles_portraits_without_committing_selection(self) -> None:
        """大分组拖动时预取新窗口，继续移动同一槽位不重复请求。"""
        members = self.members + BANDS[1][2][2:]
        self.carousel.set_members(members)
        self.carousel.pictures.clear()
        requested: list[str] = []
        selected: list[str] = []
        self.carousel.requested.connect(requested.append)
        self.carousel.selected.connect(selected.append)
        start = self.carousel.width() // 2
        self.mouse(QEvent.MouseButtonPress, start)
        self.mouse(QEvent.MouseMove, start - round(self.carousel.card_step() * 4.2))
        expected = {members[i] for i in range(1, 8)}
        self.assertEqual(set(requested), expected)
        self.assertEqual(self.carousel.visible_characters, expected)
        self.assertFalse(selected)
        requested.clear()
        self.mouse(QEvent.MouseMove, start - round(self.carousel.card_step() * 4.3))
        self.assertFalse(requested)
        self.carousel.set_members(self.members[:3])
        self.mouse(QEvent.MouseButtonRelease, start)
        self.assertEqual(self.carousel.index, 0)
        self.assertEqual(self.carousel.drag_offset, 0.0)

    def test_loop_boundary_keeps_same_picture_for_large_offsets(self) -> None:
        """跨越多圈时的同一相位保持同一画面，补位窗口重排不造成跳变。"""
        for count in (2, 3, 4, 5, 7):
            self.set_members(count)
            for phase in (-.75, -.25, 0.0, .25, .75):
                self.carousel._slide(phase)
                before = self.frame()
                for turns in (-3, 3):
                    with self.subTest(count=count, phase=phase, turns=turns):
                        self.carousel._slide(turns * count + phase)
                        self.assertEqual(self.frame(), before)

    def test_hidden_portraits_do_not_show_through_translucent_cards(self) -> None:
        """改变隐藏立绘不应改变静止画面的任何像素。"""
        before = self.frame()
        for offset in (-3, 3):
            picture = QPixmap(60, 100)
            picture.fill(QColor("magenta"))
            self.carousel.pictures[self.members[offset]] = picture
        self.assertEqual(self.frame(), before)
        self.assertEqual({delta for delta, _ in self.carousel.card_layers()}, {-2, -1, 0, 1, 2})

    def test_incoming_card_is_visible_during_motion_in_both_directions(self) -> None:
        """补位在移动阶段逐步露出，左右拖动共用同一绘制路径。"""
        for direction in (-1, 1):
            with self.subTest(direction=direction):
                self.carousel.drag_offset = -direction * self.carousel.card_step() * .25
                visible = {delta for delta, _ in self.carousel.card_layers()}
                self.assertIn(direction * 3, visible)
                before = self.frame()
                picture = QPixmap(60, 100)
                picture.fill(QColor("cyan"))
                self.carousel.pictures[self.members[direction * 3]] = picture
                self.assertNotEqual(self.frame(), before)

    def test_settling_does_not_change_pixels_including_wraparound(self) -> None:
        """提交索引前后的画面完全相同，覆盖小分组与首尾循环。"""
        for count in (2, 3, 4, 5, 7):
            for direction in (-1, 1):
                with self.subTest(count=count, direction=direction):
                    self.set_members(count)
                    self.carousel.index = count - 1 if direction > 0 else 0
                    self.carousel.slide_target = direction
                    self.carousel._slide(float(-direction))
                    before = self.frame()
                    self.carousel._settled()
                    self.assertEqual(self.frame(), before)
                    self.assertTrue(all(alpha == 1 for alpha in self.carousel.alphas.values()))

    def test_visible_count_and_hidden_endpoints_survive_resizing(self) -> None:
        """不同宽高下可见角色不重复，外侧卡片仍能露出且补位完全隐藏。"""
        for size in ((680, 800), (1000, 650), (1500, 550)):
            self.carousel.resize(*size)
            for count in range(1, 8):
                with self.subTest(size=size, count=count):
                    self.set_members(count)
                    layers = self.carousel.card_layers()
                    characters = {self.carousel.members[delta % count] for delta, _ in layers}
                    self.assertEqual(len(layers), min(5, count))
                    self.assertEqual(len(characters), len(layers))

    def test_resize_during_slide_preserves_progress_and_final_frame(self) -> None:
        """动画中途缩放不改变逻辑进度，最终落位也不跳变。"""
        self.carousel.shift(1)
        self.carousel.slide.pause()
        self.carousel.slide.setCurrentTime(120)
        position = self.carousel.drag_offset / self.carousel.card_step()
        self.carousel.resize(720, 780)
        self.assertAlmostEqual(self.carousel.drag_offset / self.carousel.card_step(), position)
        self.carousel.slide.stop()
        self.carousel._slide(-1.0)
        before = self.frame()
        self.carousel._settled()
        self.assertEqual(self.frame(), before)

    def test_center_overlap_has_no_abrupt_order_change(self) -> None:
        """中心两卡跨过中点时不应整块交换遮挡颜色。"""
        frames: list[bytes] = []
        for progress in (.4999, .5001):
            self.carousel._slide(-progress)
            frame = self.frame().convertToFormat(QImage.Format_RGBA8888)
            frames.append(frame.bits().asstring(frame.byteCount()))
        difference = sum(abs(a - b) for a, b in zip(*frames)) / len(frames[0])
        self.assertLess(difference, .5)

    def test_cached_incoming_portrait_does_not_restart_fade(self) -> None:
        """尚未进入旧预取窗口的缓存角色在成为隐藏补位时不重新淡入。"""
        self.members += BANDS[1][2][2:]
        for character in self.members[7:]:
            picture = QPixmap(60, 100)
            picture.fill(QColor("yellow"))
            self.carousel.pictures[character] = picture
        self.carousel.set_members(self.members)
        for fade in self.carousel.fades.values():
            fade.setCurrentTime(fade.duration())
        self.carousel.slide_target = 1
        self.carousel._slide(-1.0)
        self.carousel._settled()
        incoming = self.members[4]
        self.assertEqual(self.carousel.alphas.get(incoming, 1.0), 1.0)
        self.assertEqual(self.carousel.alphas.get(self.members[3], 1.0), 1.0)
