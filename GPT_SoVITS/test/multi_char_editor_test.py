"""小剧场编辑器的离屏交互测试，不连接真实存档或 Live2D 进程。"""
import os
import sys
from copy import deepcopy
from pathlib import Path
from queue import Queue
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt5.QtWidgets import QApplication, QDialog, QMenu, QPushButton, QSlider, QVBoxLayout, QWidget
from PyQt5.QtCore import Qt, QPoint, QPointF, QMimeData
from PyQt5.QtGui import QDropEvent
from chat.chat import Chat, Message
from character import CharacterAttributes
from emotion_enum import EmotionEnum
from live2d_support.theater_cast import TheaterCast
from ui_small_theater.multi_char_editor import MultiCharEditor
from ui_small_theater.multi_char_insert_dialog import CastButtons
from ui_small_theater.multi_char_message_edit import delete_saved_message, insert_saved_message, save_cast
from ui_small_theater.multi_char_timeline import MIME


class Owner(QWidget):
    """隔离原控制台的存档、BGM 和消息队列依赖。"""

    def __init__(self):
        """初始化本地队列和无音源播放器。"""
        super().__init__()
        # 与真实 ViewerGUI 一致：旧属性遮蔽了 QWidget.screen 方法。
        self.screen = QApplication.primaryScreen().geometry()
        self.to_live2d_change_character_queue = Queue()
        self.bgm_player = Mock(isMuted=Mock(return_value=False))
        self._current_bgm_path = None
        self.performance_editor = None
        self.saved = 0
        self.fail = False
        self.refresh_chat_display = Mock()
        self.refresh_chat_list = Mock()
        self.regenerate_audio = Mock(return_value=True)
        self.performance_editor_finished = Mock()

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
    chat = Chat(message_list=[message])
    slots = [{"character_name": name, "model_json_path": None} for name in ("A", "B")]
    session = MultiCharEditor(owner, chat, message, slots)
    owner.performance_editor = session
    app.processEvents()
    yield session
    session.finish()
    owner.close()
    app.processEvents()


def test_cast_controls_save_only_contiguous_rows_and_keep_speaker_audio_and_tracks(app):
    """阵容草稿、即时互换和说话人各自提交，角色事件仍归原身份。"""
    owner = Owner()
    owner.setGeometry(350, 100, 400, 500)
    messages = [Message.from_dict({"character_name": "A", "text": "测试台词", "muted": True,
                                  "audio_path": "original.wav", "speaker_id": "a"}) for _ in range(4)]
    chat = Chat(message_list=messages)
    characters = [SimpleNamespace(character_folder_name=name.lower(), character_name=name, live2d_json=None)
                  for name in ("A", "B", "C")]
    roster = TheaterCast(chat, ["A", "B"], characters, "screen")
    assert roster.add("c", owner._save_chat)
    messages[3].scene_before = {"cast": ["c", "a"]}
    session = MultiCharEditor(owner, chat, messages[1], roster.payload(roster.opening)["slots"], roster=roster)
    owner.performance_editor = session
    try:
        session.draft["characters"]["A"]["motions"].append({"at_ms": 1, "resource": {"logical_id": "pose"}})
        session.cast_buttons.buttons["a"].click()
        session.cast_buttons.buttons["c"].click()
        assert not session.cast_swap.isEnabled()
        assert roster.casts()[1] == ("a", "b")
        assert session.commit_cast()
        assert roster.casts() == [("a", "b"), ("c", "b"), ("c", "b"), ("c", "a")]
        assert session.draft["characters"]["A"]["motions"] and "A" not in session.names
        session.swap_cast()
        assert roster.casts() == [("a", "b"), ("b", "c"), ("b", "c"), ("c", "a")]
        session.receive({"type": "multi_char_transition", "playing": False, "session": session.session_id,
                         "sequence": session.sequence})
        assert session.names == ["B", "C"]
        session.speaker_label.click()
        drawer = session.resource_window.findChild(QMenu)
        buttons = drawer.findChildren(QPushButton)
        assert [button.text() for button in buttons] == ["B", "C"]
        assert [action.text() for action in drawer.actions() if action.text()] == ["无说话人"]
        buttons[0].click()
        assert (session.message.speaker_id, session.message.audio_path, session.message.muted) == (
            "b", "original.wav", True)
        from multi_char_main import ViewerGUI
        renamed = SimpleNamespace(character_folder_name="b", character_name="B 的新名称")
        assert ViewerGUI._voice_character(SimpleNamespace(character_list=[renamed]), session.message) is renamed
        assert session.draft["characters"]["A"]["motions"]
        owner.regenerate_audio.assert_not_called()
        session.cast_buttons.buttons["b"].click()
        session.cast_buttons.buttons["c"].click()
        assert session.commit_cast() and session.view.rows == []
        assert session.view.row_at(session.view.top) is None
        session.speaker_label.click()
        drawer = QApplication.activePopupWidget()
        assert drawer.objectName() == "multiCharSpeakerMenu"
        assert [action.text() for action in drawer.actions()] == ["无说话人"]
        drawer.actions()[0].trigger()
        assert (session.message.speaker_id, session.message.character_name) == ("", "")
        assert (session.message.audio_path, session.message.muted) == ("original.wav", True)
    finally:
        session.finish()
        owner.close()
        app.processEvents()


def test_cast_insert_delete_and_save_failure_keep_independent_row_snapshots():
    """插在段落中间可以接替出场，删除段首和失败保存都保留后续阵容。"""
    lines = [Message.from_dict({"text": "相同内容"}) for _ in range(4)]
    lines[-1].scene_before = {"cast": ["c", "a"], "background": "tail.png"}
    chat = Chat(message_list=lines)
    characters = [SimpleNamespace(character_folder_name=name.lower(), character_name=name)
                  for name in ("A", "B", "C")]
    roster = TheaterCast(chat, ["A", "B"], characters, "screen")
    before = deepcopy(chat.to_dict())
    assert not save_cast(roster, 1, ("c", "b"), lambda: False)
    assert chat.to_dict() == before
    inserted = Message.from_dict({"text": "新句", "character_name": "C", "speaker_id": "c"})
    assert insert_saved_message(chat, lines[0], inserted, True, lambda: True,
                                roster=roster, cast=("c", "b"))
    assert roster.casts() == [("a", "b"), ("c", "b"), ("c", "b"), ("c", "b"), ("c", "a")]
    assert delete_saved_message(chat, inserted, lambda: True, roster=roster)
    assert roster.casts() == [("a", "b"), ("c", "b"), ("c", "b"), ("c", "a")]
    assert lines[-1].scene_before["background"] == "tail.png"


def test_role_context_menu_removes_availability_without_changing_saved_cast(app, monkeypatch):
    """右键移除只影响名单，已出场角色保留在该句并可重新加入名单。"""
    message = Message.from_dict({"character_name": "C", "speaker_id": "c", "text": "原台词",
                                 "scene_before": {"cast": ["c", "b"]}})
    chat = Chat(message_list=[message])
    characters = [SimpleNamespace(character_folder_name=name.lower(), character_name=name)
                  for name in ("A", "B", "C", "D")]
    roster = TheaterCast(chat, ["A", "B"], characters, "screen")
    save = Mock(return_value=True)
    assert roster.add("c", save) and roster.add("d", save)
    before = deepcopy(message.as_dict())
    widget = CastButtons(roster, ("c", "b"), save)
    actions = []

    def choose(menu, _):
        action = menu.actions()[0]
        actions.append((action.text(), action.isEnabled()))
        return action if action.isEnabled() else None

    monkeypatch.setattr(QMenu, "exec_", choose)
    try:
        widget.buttons["a"].customContextMenuRequested.emit(QPoint())
        assert actions[-1] == ("从剧本可用角色中移除", False)

        save.return_value = False
        widget.buttons["d"].customContextMenuRequested.emit(QPoint())
        assert actions[-1] == ("从剧本可用角色中移除", True)
        assert "d" in roster.role_ids() and "d" in widget.buttons

        save.return_value = True
        widget.buttons["d"].customContextMenuRequested.emit(QPoint())
        assert "d" not in roster.role_ids() and "d" not in widget.buttons
        widget.buttons["c"].customContextMenuRequested.emit(QPoint())
        assert roster.role_ids() == ["a", "b"]
        assert widget.buttons["c"].isChecked()
        assert roster.casts() == [("c", "b")] and message.as_dict() == before

        # 「＋」仍能重新加入当前句保留的出场角色。
        widget.add_button.click()
        assert actions[-1] == ("C", True)
        assert roster.role_ids() == ["a", "b", "c"]
        assert widget.buttons["c"].isChecked() and message.as_dict() == before
    finally:
        widget.close()
        app.processEvents()


def test_adding_role_to_full_row_keeps_new_pill_and_plus_visible(app, monkeypatch):
    """可见角色行已铺满时，追加胶囊后容器应扩展到第二行。"""
    characters = [SimpleNamespace(character_folder_name=role, character_name=f"Actor {role.upper()}")
                  for role in ("a", "b", "c", "d", "e")]
    roster = TheaterCast(Chat(), ["Actor A", "Actor B"], characters, "screen")
    save = Mock(return_value=True)
    assert roster.add("c", save) and roster.add("d", save)
    window = QWidget()
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    widget = CastButtons(roster, roster.opening, save, window)
    layout.addWidget(widget)
    window.show()
    try:
        app.processEvents()
        first_row = [*widget.buttons.values(), widget.add_button]
        width = sum(button.sizeHint().width() for button in first_row)
        window.setFixedWidth(width + widget.flow._h_spacing * (len(first_row) - 1))
        app.processEvents()
        assert widget.add_button.y() == first_row[0].y()
        original_height = widget.height()

        monkeypatch.setattr(QMenu, "exec_", lambda menu, _: menu.actions()[0])
        widget.add_button.click()
        app.processEvents()

        assert widget.height() > original_height
        for button in (widget.buttons["e"], widget.add_button):
            assert button.isVisible()
            assert widget.rect().contains(button.geometry())
    finally:
        window.close()
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


def test_editor_exit_restores_stage_before_rebuilding_formal_script(editor):
    """退出先发预览结束命令并恢复布局，再通知主控重建最新正式剧本。"""
    queue = editor.owner.to_live2d_change_character_queue
    while not queue.empty():
        queue.get_nowait()
    events = []
    original_restore = editor.layout.restore

    def restore():
        """记录恢复步骤，仍执行真实窗口恢复。"""
        events.append("restore")
        original_restore()

    def finished(chat):
        """验证回调发生在预览退出命令之后，且传递当前剧本对象。"""
        assert chat is editor.chat
        commands = []
        while not queue.empty():
            commands.append(queue.get_nowait())
        assert any(command.get("action") == "exit" for command in commands)
        assert editor.owner.performance_editor is None
        events.append("reload")

    editor.layout.restore = restore
    editor.owner.performance_editor_finished.side_effect = finished
    editor.finish()
    editor.finish()
    assert events == ["restore", "reload"]
    editor.owner.performance_editor_finished.assert_called_once_with(editor.chat)


def test_cancel_close_keeps_formal_script_unchanged(editor, monkeypatch):
    """用户取消关闭时保持编排，不重置正式播放起点。"""
    from PyQt5.QtWidgets import QMessageBox
    editor.draft["characters"]["A"]["motions"].append({
        "at_ms": 0, "resource": {"file_name": "changed.motion3.json"},
    })
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Cancel)
    editor.request_close()
    assert not editor.closed
    assert editor.owner.performance_editor is editor
    editor.owner.performance_editor_finished.assert_not_called()


@pytest.mark.parametrize("release_action", ["delete", "escape", "old_track_corner"])
def test_drag_delete_uses_bottom_toolbar_without_moving_controls(editor, app, release_action):
    """缩放横滚后只在底栏投放删除，取消或旧轨道热区不误删且两端控件不移动。"""
    from PyQt5.QtCore import QEvent, QPoint
    from PyQt5.QtGui import QKeyEvent, QMouseEvent

    editor.layout.timer.stop()
    editor.timeline_window.setGeometry(20, 20, 1600, 400)
    view = editor.view
    event = {"at_ms": 200, "resource": {"file_name": "missing"}}
    events = editor.draft["characters"]["A"]["motions"]
    events.append(event)
    view.zoom = 2.0
    view.rebuild()
    app.processEvents()
    view.horizontalScrollBar().setValue(100)
    assert view.horizontalScrollBar().value() > 0
    viewport = view.viewport()
    target = view.trash_target

    def send_mouse(event_type, global_position, button, buttons):
        """用真实屏幕与视口坐标发送鼠标事件，模拟跨控件拖动时的鼠标抓取。"""
        local_position = viewport.mapFromGlobal(global_position)
        mouse = QMouseEvent(event_type, QPointF(local_position), QPointF(global_position),
                            button, buttons, Qt.NoModifier)
        QApplication.sendEvent(viewport, mouse)
        app.processEvents()

    def control_positions():
        """记录底栏两端按钮的屏幕位置和尺寸以检查拖动提示不挤动布局。"""
        return [(button.mapToGlobal(QPoint()), button.size())
                for button in (editor.play, editor.discard_button, editor.save_button)]

    baseline = control_positions()
    assert not target.isVisible()
    block = next(block for block in view.blocks if block.event is event)
    start = viewport.mapToGlobal(view.mapFromScene(block.sceneBoundingRect().center()))
    send_mouse(QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    send_mouse(QEvent.MouseMove, start + QPoint(QApplication.startDragDistance() + 5, 0),
               Qt.NoButton, Qt.LeftButton)
    assert view.drag and target.isVisible()
    assert control_positions() == baseline
    assert target.mapToGlobal(QPoint()).y() > viewport.mapToGlobal(viewport.rect().bottomLeft()).y()

    if release_action == "old_track_corner":
        unit = editor.timeline_window.font().pixelSize()
        destination = viewport.mapToGlobal(viewport.rect().bottomRight() - QPoint(unit * 3, unit))
    else:
        destination = target.mapToGlobal(target.rect().center())
    send_mouse(QEvent.MouseMove, destination, Qt.NoButton, Qt.LeftButton)
    assert view.trash_hover == (release_action != "old_track_corner")
    if release_action == "escape":
        QApplication.sendEvent(view, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        app.processEvents()
        assert not target.isVisible()
        assert not view.trash_hover
    send_mouse(QEvent.MouseButtonRelease, destination, Qt.LeftButton, Qt.NoButton)
    assert bool(events) == (release_action != "delete")
    if events:
        assert events[0]["at_ms"] == 200
    assert not target.isVisible()
    assert not view.trash_hover
    assert control_positions() == baseline


def test_resources_use_two_columns_and_one_scrollbar(editor, app):
    """资源保持两列单滚动条，拖动提示覆盖列表底边且不挤占或拦截资源。"""
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
    assert editor.drag_hint.parentWidget() is editor.list.viewport()
    assert editor.drag_hint.testAttribute(Qt.WA_TransparentForMouseEvents)
    assert editor.resource_window.layout().indexOf(editor.drag_hint) == -1
    viewport_size = editor.list.viewport().size()
    assert editor.drag_hint.isHidden()
    editor.view.dragging.emit(True)
    app.processEvents()
    assert not editor.drag_hint.isHidden()
    assert editor.list.viewport().size() == viewport_size
    assert editor.drag_hint.geometry().bottom() == editor.list.viewport().height() - 1
    editor.view.dragging.emit(False)
    assert editor.drag_hint.isHidden()


@pytest.mark.parametrize("column,kind,row", [(0, "motions", 0), (1, "expressions", 1)])
def test_resource_mouse_drag_reaches_timeline(editor, app, monkeypatch, column, kind, row):
    """实际鼠标拖动必须触发 QDrag，两列动作/表情都能携带资源落入时间轴。"""
    from PyQt5.QtCore import QEvent, QPoint
    from PyQt5.QtGui import QMouseEvent, QDragEnterEvent
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QListWidgetItem, QListView
    import ui_small_theater.multi_char_editor as module
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
    from ui_small_theater.multi_char_editor import ResourceList
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
    assert editor.message_count.text() == "1/2"
    old_session = editor.session_id
    windows = (editor.resource_window, editor.timeline_window)
    editor.view.waveform = [1.0]
    editor.switch_message(1)
    assert editor.message is second
    assert second.performance_timeline is not None
    assert editor.view.waveform == []
    assert windows == (editor.resource_window, editor.timeline_window)
    assert editor.previous.isEnabled() and not editor.next.isEnabled()
    assert editor.message_count.text() == "2/2"
    editor.receive({"session": old_session, "position": 300, "playing": True})
    assert editor.position == 0 and not editor.playing
    editor.switch_message(-1)
    assert editor.message is original
    assert editor.message_count.text() == "1/2"


@pytest.mark.parametrize("index", [0, 1, 2])
def test_delete_selects_next_or_previous_and_rejects_old_preview(editor, monkeypatch, index):
    """删除首中末句均只保存一次，停旧预览并选正确邻句，旧回执不能恢复已删句。"""
    confirm = Mock(return_value=True)
    monkeypatch.setattr("ui_small_theater.multi_char_editor.confirm_message_deletion", confirm)
    messages = [editor.message] + [Message.from_dict({"character_name": "B", "text": "同文台词"})
                                   for _ in range(2)]
    for message in messages:
        message.performance_timeline = deepcopy(editor.baseline)
    editor.chat.message_list[:] = messages
    editor.show_message(messages[index])
    deleted = editor.message
    target = messages[index + 1] if index < 2 else messages[index - 1]
    old_session = editor.session_id
    saved = editor.owner.saved
    queue = editor.owner.to_live2d_change_character_queue
    while not queue.empty():
        queue.get_nowait()

    def refresh():
        assert editor.message is target
        assert any(message is editor.message for message in editor.chat.message_list)

    editor.owner.refresh_chat_display.side_effect = refresh
    editor.delete_message()
    confirm.assert_called_once_with(editor.resource_window, deleted, discard_drafts=True)
    assert editor.message is target and all(message is not deleted for message in editor.chat.message_list)
    assert editor.owner.saved == saved + 1
    assert editor.message_count.text() == ("1/2" if index == 0 else "2/2")
    assert editor.session_id != old_session
    editor.receive({"session": old_session, "position": 999, "playing": True})
    assert editor.position == 0 and not editor.playing
    commands = []
    while not queue.empty():
        commands.append(queue.get_nowait())
    assert [command["action"] for command in commands] == ["stop", "enter"]
    assert commands[0]["scene_context"]["index"] == index
    assert len(commands[0]["scene_context"]["messages"]) == 3
    assert len(commands[1]["scene_context"]["messages"]) == 2
    editor.owner.refresh_chat_display.assert_called_once()
    editor.owner.refresh_chat_list.assert_called_once()


def test_delete_prepares_next_timeline_in_same_save_and_recomputes_inherited_scene(editor, monkeypatch):
    """下一句首次初始化与删除一并落盘；被删场景不迁移，新首句跳过原黑场。"""
    from live2d_support.multi_char_scene import SceneTiming, resolve_scene, transition_from_total
    monkeypatch.setattr("ui_small_theater.multi_char_editor.confirm_message_deletion", lambda *args, **kwargs: True)
    editor.message.scene_before = {"background": "deleted.png", "visible_characters": []}
    next_message = Message.from_dict({"character_name": "B", "text": "下一句",
                                      "scene_before": {"transition": transition_from_total()}})
    original_scene = deepcopy(next_message.scene_before)
    editor.chat.message_list.append(next_message)
    saves = []

    def save():
        saves.append([message.as_dict() for message in editor.chat.message_list])
        return True

    editor.owner._save_chat = save
    editor.delete_message()
    assert len(saves) == 1 and len(saves[0]) == 1
    assert saves[0][0]["performance_timeline"] is not None
    assert next_message.scene_before == original_scene
    assert resolve_scene(editor.chat.message_list, 0, editor.names) == {
        "visible_characters": editor.names, "background": "",
    }
    assert SceneTiming.from_scene(next_message.scene_before, first=True).duration_ms == 0
    assert not editor.black_check.isEnabled()


def test_delete_only_sentence_discards_drafts_and_finishes_empty_chat(editor, monkeypatch, tmp_path):
    """删最后一条不保存草稿、不删除音频文件，关闭编排并让主控装载空剧本。"""
    monkeypatch.setattr("ui_small_theater.multi_char_editor.confirm_message_deletion", lambda *args, **kwargs: True)
    old = editor.message
    audio = tmp_path / "keep.wav"
    audio.write_bytes(b"keep")
    old.audio_path = str(audio)
    editor.toggle_text_edit()
    editor.original_input.setPlainText("未保存文本")
    editor.draft["characters"]["A"]["motions"].append({"at_ms": 0, "resource": {"file_name": "draft"}})
    editor.delete_message()
    assert editor.chat.message_list == []
    assert editor.closed and editor.owner.isEnabled()
    assert old.text == "测试台词" and not old.performance_timeline["characters"]["A"]["motions"]
    assert audio.read_bytes() == b"keep"
    editor.owner.performance_editor_finished.assert_called_once_with(editor.chat)


def test_cancel_delete_preserves_text_timeline_and_preview(editor, monkeypatch):
    """取消唯一确认时草稿和正在预览的位置全部保留，不触发切句或保存提示。"""
    confirm = Mock(return_value=False)
    monkeypatch.setattr("ui_small_theater.multi_char_editor.confirm_message_deletion", confirm)
    editor.toggle_text_edit()
    editor.original_input.setPlainText("未保存文本")
    editor.draft["characters"]["A"]["motions"].append({"at_ms": 10, "resource": {"file_name": "draft"}})
    editor.position = 200
    editor.playing = True
    saved = editor.owner.saved
    editor.delete_message()
    assert editor.position == 200 and editor.playing and not editor.closed
    assert editor.original_input.toPlainText() == "未保存文本"
    assert editor.draft["characters"]["A"]["motions"]
    assert editor.owner.saved == saved
    assert len(editor.chat.message_list) == 1
    confirm.assert_called_once()


@pytest.mark.parametrize("raises", [False, True])
def test_delete_failure_restores_neighbor_initialization_and_keeps_drafts(editor, monkeypatch, raises):
    """删除存档失败回滚邻句首次编排和消息列表，仍可继续当前句文本及时间轴草稿。"""
    from PyQt5.QtWidgets import QMessageBox
    monkeypatch.setattr("ui_small_theater.multi_char_editor.confirm_message_deletion", lambda *args, **kwargs: True)
    monkeypatch.setattr(QMessageBox, "warning", Mock())
    old = editor.message
    next_message = Message.from_dict({"character_name": "B", "text": "下一句"})
    editor.chat.message_list.append(next_message)
    original_meta = editor.chat.meta
    editor.toggle_text_edit()
    editor.original_input.setPlainText("未保存文本")
    editor.draft["characters"]["A"]["motions"].append({"at_ms": 10, "resource": {"file_name": "draft"}})
    save = Mock(return_value=False, side_effect=OSError("存档失败") if raises else None)
    editor.owner._save_chat = save
    editor.delete_message()
    assert editor.message is old and editor.chat.message_list[0] is old
    assert len(editor.chat.message_list) == 2 and editor.chat.message_list[1] is next_message
    assert editor.chat.meta is original_meta
    assert next_message.performance_timeline is None
    assert editor.text_editing and editor.original_input.toPlainText() == "未保存文本"
    assert editor.draft["characters"]["A"]["motions"] and not editor.closed
    editor.owner.refresh_chat_display.assert_not_called()
    save.assert_called_once()


def test_delete_rejects_corrupt_neighbor_before_saving_or_stopping(editor, monkeypatch):
    """目标句轨道损坏时提前拒绝，原句及预览均不动，避免删除后才发现无法切句。"""
    from PyQt5.QtWidgets import QMessageBox
    monkeypatch.setattr("ui_small_theater.multi_char_editor.confirm_message_deletion", lambda *args, **kwargs: True)
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    next_message = Message.from_dict({"character_name": "B", "text": "损坏轨道"})
    next_message.performance_timeline = "broken"
    editor.chat.message_list.append(next_message)
    old = editor.message
    editor.playing = True
    save = Mock()
    editor.owner._save_chat = save
    editor.delete_message()
    assert editor.message is old and editor.playing
    assert len(editor.chat.message_list) == 2 and next_message.performance_timeline == "broken"
    save.assert_not_called()
    warning.assert_called_once()


def test_audio_busy_disables_and_blocks_sentence_deletion(editor, monkeypatch):
    """生成音频时隐藏或直接调用入口都不能删除，解除忙碌恢复按钮。"""
    confirm = Mock()
    monkeypatch.setattr("ui_small_theater.multi_char_editor.confirm_message_deletion", confirm)
    editor.set_audio_busy(True)
    assert not editor.delete_message_button.isEnabled()
    editor.delete_message()
    confirm.assert_not_called()
    assert len(editor.chat.message_list) == 1
    editor.set_audio_busy(False)
    assert editor.delete_message_button.isEnabled()


@pytest.mark.parametrize("change", ["close", "busy", "switch", "remove"])
def test_delete_rechecks_target_after_confirmation_events(editor, monkeypatch, change):
    """确认窗口的嵌套事件若关闭、切句或改变目标，不继续删除过期目标。"""
    old = editor.message
    next_message = Message.from_dict({"character_name": "B", "text": "下一句"})
    next_message.performance_timeline = deepcopy(editor.baseline)
    editor.chat.message_list.append(next_message)
    save = Mock(return_value=True)
    editor.owner._save_chat = save

    def confirm(*args, **kwargs):
        if change == "close":
            editor.finish()
        elif change == "busy":
            editor.set_audio_busy(True)
        elif change == "switch":
            editor.show_message(next_message)
        else:
            editor.chat.delete_message_at(0)
        return True

    monkeypatch.setattr("ui_small_theater.multi_char_editor.confirm_message_deletion", confirm)
    editor.delete_message()
    save.assert_not_called()
    assert any(message is next_message for message in editor.chat.message_list)
    assert any(message is old for message in editor.chat.message_list) == (change != "remove")
    if change == "busy":
        editor.set_audio_busy(False)


def test_text_save_is_independent_of_timeline_discard(editor, monkeypatch):
    """保存文本更新无声时长并裁剪越界，随后放弃时间轴不会撤回文本。"""
    from PyQt5.QtWidgets import QMessageBox
    question = Mock(return_value=QMessageBox.No)
    monkeypatch.setattr(QMessageBox, "question", question)
    event = {"at_ms": 800, "resource": {"file_name": "missing"}}
    editor.message.performance_timeline["characters"]["B"]["motions"].append(deepcopy(event))
    editor.draft["characters"]["B"]["motions"].append(event)
    readonly_parent = editor.text_button.parentWidget()
    assert editor.text_edit_actions.isHidden()
    editor.toggle_text_edit()
    assert editor.text_button.text() == "保存文本"
    assert editor.text_button.parentWidget() is editor.text_edit_actions
    assert editor.discard_text_button.parentWidget() is editor.text_edit_actions
    assert editor.text_edit_actions.isVisible()
    assert editor.original_input.isVisible() and editor.translation_input.isVisible()
    assert editor.dialogue_label.isHidden()
    editor.original_input.setPlainText("短")
    editor.translation_input.setPlainText("新翻译")
    assert editor.save_text()
    question.assert_called_once()
    assert editor.span.duration_ms == 720
    assert editor.draft["characters"]["B"]["motions"] == []
    assert editor.message.performance_timeline["characters"]["B"]["motions"] == []
    assert editor.text_button.text() == "编辑文本"
    assert editor.text_button.parentWidget() is readonly_parent
    assert editor.text_edit_actions.isHidden()
    editor.toggle_text_edit()
    editor.original_input.setPlainText("本次放弃的文本")
    editor.discard_text_edit()
    assert editor.message.text == "短"
    assert editor.original_input.toPlainText() == "短"
    assert editor.text_button.parentWidget() is readonly_parent
    assert editor.text_edit_actions.isHidden()
    assert editor.dialogue_label.isVisible()
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


def test_audio_pill_routes_settings_and_regeneration_separately(editor, monkeypatch):
    """设置区按本句稳定身份打开参考音频窗口，文字区仍执行合成。"""
    import multi_char_main
    owner = editor.owner
    voice = SimpleNamespace(character_folder_name="actor-a", character_name="A 的新名称", is_sakiko=False)
    owner.character_list = [voice]
    owner.audio_gen_module = object()
    owner._voice_character = MethodType(multi_char_main.ViewerGUI._voice_character, owner)
    owner.configure_sentence_reference_audio = MethodType(
        multi_char_main.ViewerGUI.configure_sentence_reference_audio, owner
    )
    editor.message.set_speaker("A", "actor-a")
    dialog = Mock()
    factory = Mock(return_value=dialog)
    monkeypatch.setattr(multi_char_main, "ChangeReferenceAudioWindow", factory)

    editor.reference_settings_button.click()
    assert factory.call_args.args[:2] == (voice, owner.audio_gen_module)
    assert factory.call_args.kwargs["parent"] is editor.resource_window
    dialog.exec_.assert_called_once_with()
    owner.regenerate_audio.assert_not_called()

    editor.regenerate_button.click()
    owner.regenerate_audio.assert_called_once()
    factory.assert_called_once()
    assert not editor.reference_settings_button.isEnabled()
    editor.audio_regenerated(False, "测试结束")


def test_emotion_menu_saves_only_current_emotion_and_follows_the_speaker(editor, tmp_path):
    voice = CharacterAttributes()
    voice.character_folder_name = "a"
    reference = tmp_path / "default.wav"
    reference.touch()
    voice.gptsovits_ref_audio = {"happiness": str(reference)}
    voice.gptsovits_ref_audio_text = {"happiness": "参考文本"}
    sakiko = CharacterAttributes()
    sakiko.character_folder_name = "sakiko"
    editor.owner._voice_character = lambda message: {"a": voice, "sakiko": sakiko}.get(message.speaker_id)
    message = editor.message
    message.set_speaker("A", "a")
    message.audio_path = "original.wav"
    message.muted = True
    saved_timeline = deepcopy(message.performance_timeline)
    draft = deepcopy(editor.draft)
    editor.update_audio_actions()
    assert not editor.emotion_button.icon().isNull()
    editor.emotion_button.click()
    menu = QApplication.activePopupWidget()
    actions = menu.actions()
    assert len(actions) == 7 and actions[0].text() == "开心" and actions[0].isChecked()
    assert actions[2].text() == "生气（沿用默认）"
    actions[2].trigger()
    menu.close()
    assert message.emotion is EmotionEnum.ANGER and message.as_dict()["emotion"] == "anger"
    assert editor.emotion_button.text() == "生气"
    assert (message.audio_path, message.muted) == ("original.wav", True)
    assert message.performance_timeline == saved_timeline and editor.draft == draft
    editor.owner.regenerate_audio.assert_not_called()

    editor.owner.fail = True
    editor.change_emotion(message, voice, EmotionEnum.FEAR)
    assert message.emotion is EmotionEnum.ANGER and editor.emotion_button.text() == "生气"
    editor.owner.fail = False
    message.set_speaker("祥子", "sakiko")
    editor.sync_scene_controls()
    assert editor.emotion_button.isHidden() and editor.regenerate_button.isEnabled()
    message.set_speaker("A", "a")
    editor.sync_scene_controls()
    assert not editor.emotion_button.isHidden() and editor.emotion_button.text() == "生气"
    message.set_speaker("", "")
    editor.sync_scene_controls()
    assert not editor.regenerate_pill.isEnabled()


def test_settings_opens_from_editor_and_follows_saved_cast(app, monkeypatch, tmp_path):
    import multi_char_main
    from multi_char_main import ViewerGUI

    owner = Owner()
    owner_font = owner.font()
    owner_font.setPixelSize(42)
    owner.setFont(owner_font)
    messages = [Message.from_dict({"character_name": "A", "text": "台词", "speaker_id": "a"}) for _ in range(2)]
    chat = Chat(message_list=messages)
    characters = [SimpleNamespace(character_folder_name=name.lower(), character_name=name, live2d_json=None)
                  for name in ("A", "B", "C")]
    roster = TheaterCast(chat, ["A", "B"], characters, "screen")
    assert roster.add("c", owner._save_chat)
    owner.current_chat = chat
    owner.character_list = characters
    owner.settings_dialog = None
    owner.audio_gen_module = SimpleNamespace(speed=1, pause_second=0.5)
    owner._bgm_volume = 50
    owner.get_current_l2d_fps = lambda: 60
    owner.motion_facing_mode_button_text = lambda: "对话朝向：正常"
    owner.is_motion_facing_mode_available = lambda: False
    owner._chat_character_names = lambda chat: ["A", "B"]
    owner.create_mask_menu = lambda: QMenu(owner)
    owner.switch_l2d_fps = Mock()
    owner.convert_sakiko_state = Mock()
    owner.set_bgm = Mock()
    owner.set_bgm_volume = Mock()
    owner.play_bgm_btn = QPushButton(owner)
    owner.bgm_controls_available = lambda: False
    owner._prepare_live2d_model_for_switch = Mock(return_value=True)
    owner.message_queue = Queue()
    for name in ("open_settings_dialog", "_on_settings_dialog_finished", "update_bgm_controls", "theater_cast",
                 "_on_live2d_model_changed", "_theater_opening_payload"):
        setattr(owner, name, MethodType(getattr(ViewerGUI, name), owner))
    config = SimpleNamespace(multi_char_subtitle_mode=SimpleNamespace(value="both"),
                             multi_char_motion_facing_mode=SimpleNamespace(value="screen"), set=Mock())
    monkeypatch.setattr(multi_char_main, "d_sakiko_config", config)
    monkeypatch.setattr(multi_char_main.character, "GetCharacterAttributes",
                        lambda: SimpleNamespace(character_class_list=characters))
    owner.open_settings_dialog()
    main_dialog = owner.settings_dialog
    main_font_pixels = main_dialog.label_model_move_info.font().pixelSize()
    main_subtitle_size = main_dialog.subtitle_modes.sizeHint()
    main_dialog.close()
    session = MultiCharEditor(owner, chat, messages[0], roster.payload(roster.opening)["slots"], roster=roster)
    owner.performance_editor = session
    try:
        session.settings_button.click()
        dialog = owner.settings_dialog
        assert not owner.isEnabled() and dialog.isEnabled() and dialog.isVisible()
        assert dialog.parentWidget() is session.resource_window
        assert main_font_pixels == dialog.label_model_move_info.font().pixelSize() == session.resource_window.font().pixelSize()
        assert main_subtitle_size == dialog.subtitle_modes.sizeHint()
        session.settings_button.click()
        assert owner.settings_dialog is dialog
        assert list(dialog.voice_cards) == ["a", "b"]
        dialog.voice_cards["b"].findChild(QSlider).setValue(121)
        assert owner._theater_voice_options["b"]["speed_factor"] == 1.21
        owner.chat_manager = SimpleNamespace(save=Mock(side_effect=OSError("测试写入失败")))
        warning = Mock()
        monkeypatch.setattr(multi_char_main.QMessageBox, "warning", warning)
        assert not ViewerGUI._save_chat(owner)
        assert warning.call_args.args[0] is session.resource_window

        path = str(tmp_path / "new.model3.json")

        def model_window(role, apply, *, form):
            assert role == "a" and form is None
            child = QDialog()
            monkeypatch.setattr(child, "exec_", lambda: apply(SimpleNamespace(model_json_path=path, is_default=False)))
            return child

        monkeypatch.setattr(multi_char_main, "ChangeL2DModelWindow", model_window)
        session.draft["characters"]["A"]["motions"].append({"at_ms": 1, "resource": {"logical_id": "kept"}})
        dialog.model_buttons["a"].click()
        assert chat.meta.live2d_models == {"A": path}
        assert session.draft["characters"]["A"]["motions"]
        assert session.preview_opening_slots["slots"][0]["model_json_path"] == path
        assert str(session.resources["A"].path) == path

        session.show_message(messages[1])
        session.cast_buttons.buttons["a"].click()
        session.cast_buttons.buttons["c"].click()
        assert list(dialog.model_buttons) == ["a", "b"]
        assert session.commit_cast()
        assert list(dialog.voice_cards) == ["c", "b"]
        session.swap_cast()
        assert list(dialog.model_buttons) == ["b", "c"]
        assert dialog.voice_cards["b"].findChild(QSlider).value() == 121
        session.finish_swap()
        session.cast_buttons.buttons["c"].click()
        assert session.commit_cast()
        assert list(dialog.model_buttons) == list(dialog.voice_cards) == ["b"]
        session.cast_buttons.buttons["b"].click()
        assert session.commit_cast()
        assert dialog.live2d_group.isHidden() and dialog.voice_group.isHidden()
        dialog.subtitle_buttons["translation"].click()
        assert config.set.call_args.args == (config.multi_char_subtitle_mode, "translation")
        assert {"type": "set_subtitle_mode", "mode": "translation"} in owner.to_live2d_change_character_queue.queue
        session.finish()
        assert owner.settings_dialog is None and not dialog.isVisible()
    finally:
        session.finish()
        owner.close()
        app.processEvents()


def test_synthesis_locks_navigation_and_exit_then_unlocks_on_failure(editor):
    """音频合成期间不可切句或关闭，失败后旧数据不变并恢复按钮。"""
    editor.chat.message_list.append(Message.from_dict({"character_name": "B", "text": "下一句"}))
    original = editor.message
    editor.regenerate_audio()
    assert editor.audio_busy
    assert not editor.next.isEnabled()
    assert not editor.previous.isEnabled() and not editor.add_message_button.isEnabled()
    assert editor.message_count.text() == "1/2"
    editor.switch_message(1)
    editor.save()
    editor.discard()
    editor.request_close()
    assert editor.message is original and not editor.closed
    callback = editor.owner.regenerate_audio.call_args.kwargs["on_complete"]
    callback(False, "合成失败")
    assert not editor.audio_busy
    assert editor.next.isEnabled()
    assert editor.add_message_button.isEnabled()
    assert editor.message_count.text() == "1/2"
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
    editor.owner.performance_editor_finished.assert_not_called()
    assert editor.message.performance_timeline["characters"]["B"]["motions"] == []
    editor.owner.fail = False
    editor.draft["characters"]["B"]["motions"].clear()
    editor.save()
    assert editor.closed
    editor.owner.performance_editor_finished.assert_called_once_with(editor.chat)


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
    from ui_small_theater.multi_char_windows import workspace_rects
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
    for raw, expected in [(1, 0), (201, 200), (301, 300), (501, 500),
                          (editor.span.duration_ms - 99, editor.span.duration_ms - 100)]:
        at, valid = view.candidate(0, new, raw, Qt.NoModifier)
        assert (at, valid) == (expected, True)
    assert view.candidate(0, new, 201, Qt.AltModifier)[0] == 201
    assert not view.candidate(0, new, 400, Qt.AltModifier)[1]
    assert not view.candidate(0, new, -100, Qt.AltModifier)[1]


def test_capsule_controls_allow_empty_text_and_keep_settings_when_disabled(editor):
    """首句标题可留空、滑动只改停留时长，关闭后仍保留内容供再次启用。"""
    assert editor.message_index() == 0
    editor.capsule_check.setChecked(True)
    assert editor.message.scene_before["capsule"] == {"enabled": True, "text": "", "hold_ms": 2500}
    assert editor.transition_preview.isEnabled()
    assert not editor.black_check.isEnabled()
    editor.capsule_text.setText("很长的标题" * 20)
    editor.capsule_save.click()
    assert editor.message.scene_before["capsule"]["text"] == "很长的标题" * 20
    editor.capsule_duration.setValue(5000)
    assert editor.message.scene_before["capsule"]["hold_ms"] == 5000
    editor.preview_transition()
    assert editor.transition_playing
    editor.capsule_check.setChecked(False)
    assert editor.message.scene_before["capsule"] == {
        "enabled": False, "text": "很长的标题" * 20, "hold_ms": 5000}
    editor.capsule_check.setChecked(True)
    assert editor.capsule_text.text() == "很长的标题" * 20
    assert editor.capsule_duration.minimum() == 500 and editor.capsule_duration.maximum() == 5000


@pytest.mark.parametrize("focus", ["resource_window", "timeline_window", "play", "black_check"])
def test_space_in_editor_windows_calls_timeline_play_slot(editor, monkeypatch, app, focus):
    from PyQt5.QtTest import QTest
    toggle = Mock(wraps=editor.toggle_play)
    monkeypatch.setattr(editor, "toggle_play", toggle)
    widget = getattr(editor, focus)
    widget.setFocusPolicy(Qt.StrongFocus)
    widget.window().activateWindow()
    widget.setFocus()
    app.processEvents()
    QTest.keyClick(widget, Qt.Key_Space)
    toggle.assert_called_once_with()
    assert editor.playing


def test_space_typing_and_stage_request_use_correct_editor_behavior(editor, monkeypatch, app):
    from PyQt5.QtTest import QTest
    toggle = Mock(wraps=editor.toggle_play)
    monkeypatch.setattr(editor, "toggle_play", toggle)
    editor.resource_window.activateWindow()
    editor.search.setFocus()
    editor.search.setText("left")
    app.processEvents()
    QTest.keyClick(editor.search, Qt.Key_Space)
    assert editor.search.text() == "left "
    toggle.assert_not_called()
    editor.receive({"type": "multi_char_preview", "session": editor.session_id, "action": "toggle_play"})
    toggle.assert_called_once_with()
    assert editor.playing
