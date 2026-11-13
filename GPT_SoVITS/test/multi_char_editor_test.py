"""小剧场编辑器的离屏交互测试，不连接真实存档或 Live2D 进程。"""
import os
import sys
from copy import deepcopy
from pathlib import Path
from queue import Queue
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt, QPointF, QMimeData
from PyQt5.QtGui import QDropEvent
from chat.chat import Chat, Message
from ui_small_theater.multi_char_editor import MultiCharEditor
from ui_small_theater.multi_char_timeline import MIME


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
