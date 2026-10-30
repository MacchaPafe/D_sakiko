"""小剧场编辑器的离屏交互测试，不连接真实存档或 Live2D 进程。"""
import os
import sys
from copy import deepcopy
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt, QPointF, QMimeData
from PyQt5.QtGui import QDropEvent
from chat.chat import Message
from ui.components.multi_char_editor import MultiCharEditor
from ui.components.multi_char_timeline import MIME


class Owner(QWidget):
    """隔离原控制台的存档、BGM 和消息队列依赖。"""

    def __init__(self):
        """初始化本地队列和无音源播放器。"""
        super().__init__()
        # 与真实 ViewerGUI 一致：旧属性遮蔽了 QWidget.screen 方法。
        self.screen = QApplication.primaryScreen().geometry()
        self.to_live2d_change_character_queue = Queue()
        self.bgm_player = Mock()
        self.performance_editor = None
        self.saved = 0
        self.fail = False
        self.refresh_chat_display = Mock()
        self.refresh_chat_list = Mock()
        self.regenerate_audio = Mock(return_value=True)

    def _save_chat(self):
        """记录保存调用，不写入任何用户文件。"""
        self.saved += 1
        return not self.fail


@pytest.fixture(scope="module")
def app():
    """共用一个离屏 Qt 应用，避免窗口生命周期冲突。"""
    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor(app):
    """创建无模型、正常无声的双人编排，进入即保存。"""
    owner = Owner()
    owner.setGeometry(350, 100, 400, 500)
    message = Message.from_dict({"character_name": "A", "text": "测试台词", "emotion": "normal"})
    chat = SimpleNamespace(message_list=[message])
    slots = [{"character_name": name, "model_json_path": None} for name in ("A", "B")]
    session = MultiCharEditor(owner, chat, message, slots)
    owner.performance_editor = session
    app.processEvents()
    yield session
    session.finish()
    owner.close()
    app.processEvents()


def test_enter_discard_and_window_restore(editor):
    """首次进入持久保存，放弃草稿仍保留基线并关闭两个窗口。"""
    assert editor.owner.saved == 1
    assert len(editor.view.rows) == 4
    assert editor.timeline_window.windowFlags() & Qt.FramelessWindowHint
    assert not editor.owner.isEnabled()
    editor.draft["characters"]["A"]["motions"].append({"at_ms": 0, "resource": {"file_name": "missing"}})
    editor.discard()
    assert editor.message.performance_timeline["characters"]["A"]["motions"] == []
    assert editor.owner.isEnabled()
    assert not editor.timeline_window.isVisible()
    assert not editor.resource_window.isVisible()


def test_editor_fonts_do_not_inherit_large_point_size(editor):
    """轨道标签和控件使用同一像素字体，避免全局点字号挤坏轨道。"""
    from PyQt5.QtWidgets import QGraphicsTextItem
    expected = editor.timeline_window.font().pixelSize()
    assert 14 <= expected <= 36
    labels = [item for item in editor.view.scene().items() if isinstance(item, QGraphicsTextItem)]
    assert labels
    assert all(item.font().pixelSize() == expected for item in labels)


def test_tracks_fill_available_height(editor, app):
    """大窗口四轨均分可用高度，避免底部留下整片空白。"""
    editor.layout.timer.stop()
    editor.timeline_window.resize(1600, 650)
    app.processEvents()
    view = editor.view
    assert abs(view.sceneRect().height() - view.viewport().height()) <= 8


def test_editor_does_not_change_bgm_state(editor):
    """进入与退出编辑不暂停、停止或重启主界面 BGM。"""
    editor.finish()
    assert editor.owner.bgm_player.mock_calls == []


def test_resources_use_two_columns_and_one_scrollbar(editor, app):
    """几十条同类型资源均分行内两列，只允许最右侧的纵向滚动。"""
    from PyQt5.QtWidgets import QListWidgetItem
    for index in range(50):
        editor.list.addItem(QListWidgetItem(f"motion_{index}"))
    editor.list.update_columns()
    app.processEvents()
    first = editor.list.visualItemRect(editor.list.item(0))
    second = editor.list.visualItemRect(editor.list.item(1))
    third = editor.list.visualItemRect(editor.list.item(2))
    assert first.top() == second.top()
    assert second.left() > first.right()
    assert third.top() > first.top()
    assert editor.list.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    assert editor.list.verticalScrollBar().maximum() > 0
    assert editor.drag_hint.isHidden()
    editor.view.dragging.emit(True)
    assert not editor.drag_hint.isHidden()
    editor.view.dragging.emit(False)
    assert editor.drag_hint.isHidden()


@pytest.mark.parametrize("column,kind,row", [(0, "motions", 0), (1, "expressions", 1)])
def test_resource_mouse_drag_reaches_timeline(editor, app, monkeypatch, column, kind, row):
    """实际鼠标拖动必须触发 QDrag，两列动作/表情都能携带资源落入时间轴。"""
    from PyQt5.QtCore import QEvent, QPoint
    from PyQt5.QtGui import QMouseEvent, QDragEnterEvent
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QListWidgetItem, QListView
    import ui.components.multi_char_editor as module
    editor.resources["A"].version = "v3"
    editor.list.clear()
    for index in range(2):
        item = QListWidgetItem(f"resource_{index}")
        item.setData(Qt.UserRole, {"character": "A", "kind": kind,
                                  "resource": {"file_name": f"resource_{index}"}})
        editor.list.addItem(item)
    editor.list.update_columns()
    QTest.qWait(30)
    assert editor.list.dragEnabled()
    assert editor.list.dragDropMode() == QListView.DragOnly
    drag = Mock()

    def drop(actions):
        """模拟系统传递同一个 MIME 到真实时间轴，校验落下产生的事件。"""
        assert not editor.drag_hint.isHidden()
        mime = drag.setMimeData.call_args.args[0]
        point = QPoint(round(editor.view.x_at(100)), round(editor.view.top + row * editor.view.row_height + 15))
        enter = QDragEnterEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        editor.view.dragEnterEvent(enter)
        assert enter.isAccepted()
        event = QDropEvent(QPointF(point), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        editor.view.dropEvent(event)
        assert event.isAccepted()
        return Qt.CopyAction

    drag.exec_.side_effect = drop
    monkeypatch.setattr(module, "QDrag", Mock(return_value=drag))
    point = editor.list.visualItemRect(editor.list.item(column)).center()
    QTest.mousePress(editor.list.viewport(), Qt.LeftButton, pos=point)
    for distance in (2, QApplication.startDragDistance() + 5, QApplication.startDragDistance() + 20):
        moved = point + QPoint(distance, 0)
        event = QMouseEvent(QEvent.MouseMove, QPointF(moved), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(editor.list.viewport(), event)
        if drag.exec_.called:
            break
    QTest.mouseRelease(editor.list.viewport(), Qt.LeftButton, pos=point)
    drag.exec_.assert_called_once()
    assert editor.draft["characters"]["A"][kind][0]["resource"]["file_name"] == f"resource_{column}"
    assert editor.drag_hint.isHidden()


def test_two_columns_stay_stable_across_scrollbar_visibility(editor, app):
    """有、无滚动条反复切换后，连续事件循环始终两列且不再重排闪烁。"""
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QListWidgetItem
    from ui.components.multi_char_editor import ResourceList
    resources = ResourceList(editor)
    resources.setObjectName("multiCharResourceList")
    resources.setStyleSheet(editor.resource_window.styleSheet())
    resources.setFont(editor.resource_window.font())
    for index in range(20):
        resources.addItem(QListWidgetItem(f"motion_{index}"))
    resources.show()
    try:
        row_height = round(resources.fontMetrics().height() * 1.8)
        # 两列能放下、单列放不下，是原来显隐循环的触发区间。
        for rows, scrolling in [(14, False), (6, True), (14, False)]:
            resources.resize(600, row_height * rows)
            QTest.qWait(60)
            snapshots = []
            for _ in range(12):
                QTest.qWait(15)
                first = resources.visualItemRect(resources.item(0))
                second = resources.visualItemRect(resources.item(1))
                assert first.top() == second.top()
                assert second.left() > first.right()
                assert resources.verticalScrollBar().isVisible() == scrolling
                snapshots.append((resources.gridSize().width(), resources.verticalScrollBar().maximum(),
                                  first.getRect(), second.getRect()))
            assert len(set(snapshots)) == 1
            assert not resources.column_timer.isActive()
    finally:
        resources.close()
        resources.deleteLater()


def test_switch_initializes_new_message_and_ignores_old_feedback(editor):
    """原位切句初始化目标、清空波形选择，并拒收旧句预览回执。"""
    original = editor.message
    second = Message.from_dict({"character_name": "B", "text": "下一句", "emotion": "normal"})
    editor.chat.message_list.append(second)
    editor.update_navigation()
    assert not editor.previous.isEnabled() and editor.next.isEnabled()
    old_session = editor.session_id
    windows = (editor.resource_window, editor.timeline_window)
    editor.view.waveform = [1.0]
    editor.switch_message(1)
    assert editor.message is second
    assert second.performance_timeline is not None
    assert editor.view.waveform == []
    assert windows == (editor.resource_window, editor.timeline_window)
    assert editor.previous.isEnabled() and not editor.next.isEnabled()
    editor.receive({"session": old_session, "position": 300, "playing": True})
    assert editor.position == 0 and not editor.playing
    editor.switch_message(-1)
    assert editor.message is original


def test_text_save_is_independent_of_timeline_discard(editor, monkeypatch):
    """保存文本更新无声时长并裁剪越界，随后放弃时间轴不会撤回文本。"""
    from PyQt5.QtWidgets import QMessageBox
    question = Mock(return_value=QMessageBox.No)
    monkeypatch.setattr(QMessageBox, "question", question)
    event = {"at_ms": 800, "resource": {"file_name": "missing"}}
    editor.message.performance_timeline["characters"]["B"]["motions"].append(deepcopy(event))
    editor.draft["characters"]["B"]["motions"].append(event)
    editor.toggle_text_edit()
    assert editor.text_button.text() == "保存文本"
    editor.original_input.setPlainText("短")
    editor.translation_input.setPlainText("新翻译")
    assert editor.save_text()
    question.assert_called_once()
    assert editor.span.duration_ms == 600
    assert editor.draft["characters"]["B"]["motions"] == []
    assert editor.message.performance_timeline["characters"]["B"]["motions"] == []
    assert editor.text_button.text() == "编辑文本"
    editor.discard()
    assert editor.message.text == "短"
    assert editor.message.translation == "新翻译"


def test_translation_only_save_does_not_prompt_audio(editor, monkeypatch):
    """仅改翻译不询问合成，也不丢失未保存的编排事件。"""
    from PyQt5.QtWidgets import QMessageBox
    question = Mock()
    monkeypatch.setattr(QMessageBox, "question", question)
    editor.draft["characters"]["A"]["motions"].append({"at_ms": 20, "resource": {"file_name": "missing"}})
    editor.toggle_text_edit()
    editor.translation_input.setPlainText("只改翻译")
    assert editor.save_text()
    question.assert_not_called()
    assert editor.draft["characters"]["A"]["motions"]
    assert not editor.message.performance_timeline["characters"]["A"]["motions"]


def test_text_save_keeps_existing_audio_duration(editor, tmp_path, monkeypatch):
    """已有音频时修改原文且拒绝合成，仍以旧音频长度作为时间轴范围。"""
    import soundfile as sf
    from PyQt5.QtWidgets import QMessageBox
    path = tmp_path / "old.wav"
    sf.write(str(path), [0.0] * 16000, 8000)
    editor.message.audio_path = str(path)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    editor.toggle_text_edit()
    editor.original_input.setPlainText("短")
    assert editor.save_text()
    assert editor.span.duration_ms == 2000
    assert editor.message.audio_path == str(path)
    editor.owner.regenerate_audio.assert_not_called()


def test_failed_text_save_keeps_editable_draft(editor):
    """写盘失败回滚消息并保留输入草稿，不触发合成。"""
    original = editor.message.text
    editor.toggle_text_edit()
    editor.original_input.setPlainText("未能保存")
    editor.owner.fail = True
    assert not editor.save_text()
    assert editor.message.text == original
    assert editor.text_editing
    assert editor.original_input.toPlainText() == "未能保存"
    editor.owner.regenerate_audio.assert_not_called()


def test_synthesis_locks_navigation_and_exit_then_unlocks_on_failure(editor):
    """音频合成期间不可切句或关闭，失败后旧数据不变并恢复按钮。"""
    editor.chat.message_list.append(Message.from_dict({"character_name": "B", "text": "下一句"}))
    original = editor.message
    editor.regenerate_audio()
    assert editor.audio_busy
    assert not editor.next.isEnabled()
    editor.switch_message(1)
    editor.save()
    editor.discard()
    editor.request_close()
    assert editor.message is original and not editor.closed
    callback = editor.owner.regenerate_audio.call_args.kwargs["on_complete"]
    callback(False, "合成失败")
    assert not editor.audio_busy
    assert editor.next.isEnabled()
    assert editor.message.audio_path == original.audio_path


def test_synthesis_refreshes_both_baseline_and_draft(editor, tmp_path):
    """新音频即时生效，裁剪独立草稿后仍保留有效的未保存动作。"""
    import soundfile as sf
    path = tmp_path / "new.wav"
    sf.write(str(path), [0.0] * 4000, 8000)
    editor.draft["characters"]["A"]["motions"] = [
        {"at_ms": at, "resource": {"file_name": "missing"}} for at in (100, 700)]
    editor.regenerate_audio()
    editor.message.audio_path = str(path)
    editor.audio_regenerated(True, "生成完毕")
    assert editor.span.duration_ms == 500
    assert len(editor.draft["characters"]["A"]["motions"]) == 1
    assert not editor.baseline["characters"]["A"]["motions"]
    editor.discard()
    assert editor.message.audio_path == str(path)


def test_unsaved_text_cancel_blocks_navigation(editor, monkeypatch):
    """文本草稿选择取消后留在原句，不静默丢弃输入。"""
    from PyQt5.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Cancel)
    editor.chat.message_list.append(Message.from_dict({"character_name": "B", "text": "下一句"}))
    original = editor.message
    editor.toggle_text_edit()
    editor.original_input.setPlainText("未保存")
    editor.switch_message(1)
    assert editor.message is original
    assert editor.original_input.toPlainText() == "未保存"


@pytest.mark.parametrize("choice,fail,switched,saved", [
    ("取消", False, False, False),
    ("放弃并切换", False, True, False),
    ("保存并切换", False, True, True),
    ("保存并切换", True, False, False),
])
def test_switch_draft_choices(editor, monkeypatch, choice, fail, switched, saved):
    """切句保存、放弃、取消及写盘失败均不丢失原句草稿或误切消息。"""
    from PyQt5.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "exec_", lambda dialog: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton",
                        lambda dialog: next(button for button in dialog.buttons() if button.text() == choice))
    original = editor.message
    second = Message.from_dict({"character_name": "B", "text": "下一句", "emotion": "normal"})
    editor.chat.message_list.append(second)
    editor.draft["characters"]["A"]["motions"].append({"at_ms": 0, "resource": {"file_name": "missing"}})
    editor.owner.fail = fail
    editor.switch_message(1)
    assert (editor.message is second) == switched
    assert bool(original.performance_timeline["characters"]["A"]["motions"]) == saved
    if not switched:
        assert editor.draft["characters"]["A"]["motions"]


def test_empty_save_and_failure(editor):
    """保存失败不关闭窗口、不污染原消息，重试成功后提交空编排。"""
    editor.draft["characters"]["B"]["motions"].append({"at_ms": 1, "resource": {"file_name": "missing"}})
    editor.owner.fail = True
    editor.save()
    assert not editor.closed
    assert editor.message.performance_timeline["characters"]["B"]["motions"] == []
    editor.owner.fail = False
    editor.draft["characters"]["B"]["motions"].clear()
    editor.save()
    assert editor.closed


def test_cross_window_drop_role_validation(editor):
    """新资源只可拖入自己的角色及类型，空模型不妨碍保留资源引用。"""
    import json
    payload = {"session": editor.session_id, "character": "B", "kind": "motions",
               "resource": {"logical_id": "nod", "file_name": "nod.motion3.json"}}
    mime = QMimeData()
    mime.setData(MIME, json.dumps(payload).encode())
    x = editor.view.x_at(100)
    wrong_y = editor.view.top + 15
    event = QDropEvent(QPointF(x, wrong_y), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    editor.view.dropEvent(event)
    assert editor.draft["characters"]["A"]["motions"] == []
    correct_y = editor.view.top + 2 * editor.view.row_height + 15
    event = QDropEvent(QPointF(x, correct_y), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    editor.view.dropEvent(event)
    assert len(editor.draft["characters"]["B"]["motions"]) == 1
    assert editor.draft["characters"]["B"]["motions"][0]["color"].startswith("#")


def test_workspace_uses_available_area_and_moves_only_needed():
    """布局优先上移且底部不压住任务栏，小屏才缩小上方。"""
    from PyQt5.QtCore import QRect
    from ui.components.multi_char_windows import workspace_rects
    stage = QRect(100, 160, 800, 700)
    control = QRect(900, 160, 600, 700)
    available = QRect(0, 0, 1920, 1040)
    left, right, bottom = workspace_rects(stage, control, available, 300)
    assert left.height() == 700
    assert right.top() == left.top() == 40
    assert left.right() + 1 == right.left()
    assert bottom.left() == left.left()
    assert bottom.right() == right.right()
    assert bottom.top() == right.bottom() + 1
    assert available.contains(bottom)
    small = QRect(0, 0, 1280, 680)
    left, right, bottom = workspace_rects(stage, control, small, 300)
    assert left.height() < 700
    assert small.contains(left) and small.contains(right) and small.contains(bottom)


def test_other_character_track_remains_editable(editor):
    """资源标签选择 A 不会阻止 B 轨的移动及精确输入。"""
    event = {"at_ms": 100, "resource": {"file_name": "missing"}, "color": "#0077DD"}
    editor.draft["characters"]["B"]["motions"].append(event)
    editor.characters.setCurrentIndex(0)
    editor.select_event((2, event))
    editor.change_time(0.2)
    assert event["at_ms"] == 200
    assert editor.characters.currentIndex() == 0


def test_snap_endpoints_and_alt(editor):
    """五类吸附共享原始时长，Alt 可关闭，负数和同点冲突拒绝。"""
    from unittest.mock import Mock
    view = editor.view
    resources = editor.resources['A']
    resources.duration = Mock(return_value=100)
    existing = {"at_ms": 400, "resource": {"file_name": "existing"}}
    editor.draft['characters']['A']['motions'] = [existing]
    new = {"resource": {"file_name": "new"}}
    view.playhead = 200
    for raw, expected in [(1, 0), (201, 200), (301, 300), (501, 500), (801, 800)]:
        at, valid = view.candidate(0, new, raw, Qt.NoModifier)
        assert (at, valid) == (expected, True)
    assert view.candidate(0, new, 201, Qt.AltModifier)[0] == 201
    assert not view.candidate(0, new, 400, Qt.AltModifier)[1]
    assert not view.candidate(0, new, -100, Qt.AltModifier)[1]
