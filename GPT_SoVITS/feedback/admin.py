"""开发者反馈面板：Access 认证、纯文本查看和受管理的导出。"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import requests
from platformdirs import user_data_path
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtWidgets import QComboBox, QDialog, QHBoxLayout, QListWidget, QMenu, QMessageBox, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget

from chat.chat import Chat, ChatManager
from feedback.dialogs import NetworkJob
from feedback.importing import find_imported_chat, source_of, source_status_text
from feedback.viewer import FeedbackDetailView, RATINGS, plain_label
from feedback.protocol import Json, MAX_BODY, export_backup, validate_payload
from ui_main.theme import ThemePalette, build_dialog_theme_stylesheet

CallableOperation = Callable[[], object]
CallableResult = Callable[[object], None]


class AdminClient:
    """仅从开发者本机读取限权 Access service token，不使用部署令牌。"""

    def __init__(self) -> None:
        """检查独立管理地址和本机访问配置。"""
        self.endpoint = os.environ.get("DSAKIKO_FEEDBACK_ADMIN_URL", "").rstrip("/")
        parsed = urlsplit(self.endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("请配置 HTTPS 管理服务地址。")
        self.headers = {"CF-Access-Client-Id": os.environ.get("DSAKIKO_FEEDBACK_ACCESS_ID", ""),
                        "CF-Access-Client-Secret": os.environ.get("DSAKIKO_FEEDBACK_ACCESS_SECRET", "")}
        if not all(self.headers.values()):
            raise ValueError("请在开发者本机配置 Access service token；不能使用 Cloudflare 部署令牌。")

    def request(self, method: str, suffix: str = "", body: dict[str, Json] | None = None) -> dict[str, Json]:
        """有界读取经过 Access 认证的管理响应，不跟随登录重定向。"""
        try:
            with requests.request(method, self.endpoint + "/v1/admin/feedback" + suffix, headers=self.headers,
                                  json=body, timeout=(10, 30), stream=True, allow_redirects=False) as response:
                if response.status_code == 404:
                    return {"missing": True}
                if response.status_code != 200:
                    raise ValueError("管理服务请求失败，请检查 Access 权限和服务状态。")
                content = bytearray()
                for chunk in response.iter_content(8192):
                    content.extend(chunk)
                    if len(content) > MAX_BODY * 2:
                        raise ValueError("管理响应超过大小限制。")
                value: object = json.loads(content)
                if not isinstance(value, dict):
                    raise ValueError("管理服务返回了无效响应。")
                return value
        except (requests.RequestException, json.JSONDecodeError) as exc:
            raise ValueError("无法连接反馈管理服务。") from exc


class ManagedExports:
    """集中保存开发者导出，并在面板打开时同步过期和撤回清理。"""

    def __init__(self) -> None:
        """使用独立目录，文件名不来自上传文本。"""
        self.root = user_data_path("D_sakiko", appauthor=False) / "feedback" / "admin_exports"
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def remove(self, feedback_id: str) -> None:
        """只删除本工具管理的对应反馈副本。"""
        if not all(c in "0123456789abcdef-" for c in feedback_id) or len(feedback_id) != 36:
            raise ValueError("反馈编号无效。")
        for suffix in (".json", ".dsakiko-chat.zip", ".meta"):
            (self.root / (feedback_id + suffix)).unlink(missing_ok=True)

    def sync(self, client: AdminClient) -> None:
        """检查本工具导出的副本，删除已到期或云端已撤回的内容。"""
        for path in self.root.glob("*.meta"):
            metadata: dict[str, Json] = json.loads(path.read_text("utf-8"))
            if metadata.get("endpoint") != client.endpoint:
                continue
            if int(str(metadata["expires_at"])) <= time.time() or client.request("GET", "/" + path.stem).get("missing"):
                self.remove(path.stem)

    def save(self, detail: dict[str, Json], endpoint: str, backup: bool) -> Path:
        """写入可追踪的反馈 JSON 或文本备份，保留服务端到期时间。"""
        feedback_id = str(detail["feedback_id"])
        if len(feedback_id) != 36 or not all(c in "0123456789abcdef-" for c in feedback_id):
            raise ValueError("反馈编号无效。")
        if int(str(detail["expires_at"])) <= time.time():
            raise ValueError("反馈已到期。")
        payload = cast(dict[str, Json], detail["payload"])
        validate_payload(payload)
        # 先写索引，即使导出中断也可清理剩余文件。
        (self.root / (feedback_id + ".meta")).write_text(json.dumps({"expires_at": detail["expires_at"], "endpoint": endpoint}), "utf-8")
        path = self.root / (feedback_id + (".dsakiko-chat.zip" if backup else ".json"))
        if backup:
            export_backup(payload, path)
        else:
            path.write_text(json.dumps(detail, ensure_ascii=False, indent=2), "utf-8")
        path.chmod(0o600)
        return path


class FeedbackAdminDialog(QDialog):
    """开发者同进程管理面板，网络操作与本机对话变更分开执行。"""

    def __init__(self, palette: ThemePalette, parent: QWidget | None = None, *,
                 chat_manager: ChatManager | None = None,
                 import_chat: Callable[[dict[str, Json], str], str | None] | None = None,
                 open_chat: Callable[[str], bool] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("反馈管理")
        self.resize(1050, 760)
        self.setStyleSheet(build_dialog_theme_stylesheet(palette))
        self.manager = chat_manager
        self.import_chat_callback = import_chat
        self.open_chat_callback = open_chat
        self.endpoint = os.environ.get("DSAKIKO_FEEDBACK_ADMIN_URL", "").rstrip("/")
        self.job: NetworkJob | None = None
        self.detail: dict[str, Json] | None = None
        self.cursor: str | None = None
        self.items: list[dict[str, Json]] = []
        self.exports = ManagedExports()
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItems(["全部", "未处理", "已处理"])
        self.rating = QComboBox()
        self.rating.addItems(["全部评价", "赞", "踩", "不评价"])
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self._refresh)
        self.next_button = QPushButton("下一页")
        self.next_button.clicked.connect(self._next_page)
        for widget in (self.filter, self.rating, self.refresh_button, self.next_button):
            bar.addWidget(widget)
        bar.addStretch()
        layout.addLayout(bar)
        splitter = QSplitter(Qt.Horizontal)
        self.lists = QTabWidget()
        self.list = QListWidget()
        self.local_list = QListWidget()
        for listing in (self.list, self.local_list):
            listing.setWordWrap(True)
            listing.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.itemSelectionChanged.connect(self._selected)
        self.local_list.itemSelectionChanged.connect(self._local_selected)
        self.lists.addTab(self.list, "云端反馈")
        self.lists.addTab(self.local_list, "已导入对话")
        self.lists.currentChanged.connect(self._list_tab_changed)
        splitter.addWidget(self.lists)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.viewer = FeedbackDetailView()
        right_layout.addWidget(self.viewer, 1)
        self.source_status = plain_label()
        right_layout.addWidget(self.source_status)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 740])
        layout.addWidget(splitter, 1)
        actions = QHBoxLayout()
        self.import_button = QPushButton("导入为新对话")
        self.import_button.clicked.connect(self._import)
        self.mark_button = QPushButton("标为已处理")
        self.mark_button.clicked.connect(self._mark)
        self.more_button = QPushButton("更多操作")
        menu = QMenu(self)
        self.export_json_action = menu.addAction("导出反馈 JSON", lambda: self._export(False))
        self.export_backup_action = menu.addAction("导出对话 ZIP", lambda: self._export(True))
        menu.addSeparator()
        self.delete_action = menu.addAction("删除云端反馈…", self._delete)
        self.more_button.setMenu(menu)
        for widget in (self.import_button, self.mark_button, self.more_button):
            actions.addWidget(widget)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = plain_label("本地导入的对话独立保留；刷新时检查原始反馈状态。")
        layout.addWidget(self.status)
        self.filter.currentIndexChanged.connect(self._refresh)
        self.rating.currentIndexChanged.connect(self._refresh)
        self._refresh_local_list()
        self._update_actions()
        self._refresh()

    def _local_chat(self) -> Chat | None:
        item = self.local_list.currentItem()
        return self.manager.get_chat_by_id(item.data(Qt.UserRole)) if self.manager and item else None

    def _existing_chat(self) -> Chat | None:
        if self.lists.currentIndex() == 1:
            return self._local_chat()
        if self.manager and self.detail:
            return find_imported_chat(self.manager, self.endpoint, str(self.detail["feedback_id"]))
        return None

    def _update_actions(self) -> None:
        idle = self.job is None
        selected = self.detail is not None and self.lists.currentIndex() == 0
        existing = self._existing_chat()
        conversation = self.detail and cast(dict[str, Json], self.detail["payload"])["conversation"]
        self.import_button.setText("打开已导入对话" if existing else "导入为新对话")
        self.import_button.setEnabled(idle and bool((existing and self.open_chat_callback) or
                                                     (selected and conversation and self.import_chat_callback)))
        self.mark_button.setEnabled(idle and selected)
        self.mark_button.setText("标为未处理" if self.detail and self.detail["processed"] else "标为已处理")
        self.more_button.setEnabled(idle and selected)
        self.export_backup_action.setEnabled(bool(conversation))
        for widget in (self.filter, self.rating, self.refresh_button, self.lists):
            widget.setEnabled(idle)
        self.next_button.setEnabled(idle and bool(self.cursor) and self.lists.currentIndex() == 0)

    def _start(self, operation: CallableOperation, complete: CallableResult) -> None:
        if self.job is not None:
            return
        self.job = NetworkJob(operation, self)
        self.job.finished.connect(lambda: self._finished(complete))
        self._update_actions()
        self.job.start()

    def _finished(self, complete: CallableResult) -> None:
        assert self.job is not None
        job = self.job
        self.job = None
        try:
            if job.error:
                self.status.setText(job.error)
            else:
                complete(job.result)
        except ValueError as exc:
            self.status.setText(str(exc))
        except (KeyError, TypeError):
            self.status.setText("管理响应不符合当前格式，请更新程序。")
        finally:
            self._update_actions()
            job.deleteLater()

    def _refresh(self) -> None:
        self._load_page(None, sync=True)

    def _next_page(self) -> None:
        if self.cursor:
            self._load_page(self.cursor)

    def _source_snapshots(self) -> dict[str, dict[str, object]]:
        """在主线程冻结来源元数据，后台不读取或修改 ChatManager。"""
        sources = {}
        if self.manager:
            for chat in self.manager.single_character_chats():
                source = source_of(chat)
                if source and source.get("endpoint") == self.endpoint:
                    sources[str(source["feedback_id"])] = dict(source)
        return sources

    @staticmethod
    def _check_sources(client: AdminClient, sources: dict[str, dict[str, object]]) -> dict[str, str]:
        statuses = {}
        for feedback_id, source in sources.items():
            if int(source["expires_at"]) <= time.time() or source.get("status") == "unavailable":
                statuses[feedback_id] = "unavailable"
                continue
            try:
                statuses[feedback_id] = "unavailable" if client.request("GET", "/" + feedback_id).get("missing") else "available"
            except (ValueError, requests.RequestException):
                statuses[feedback_id] = "unknown"
        return statuses

    def _apply_source_statuses(self, statuses: dict[str, str]) -> None:
        if not self.manager or not statuses:
            return
        now = int(time.time())
        for chat in self.manager.single_character_chats():
            source = source_of(chat)
            if source and source.get("endpoint") == self.endpoint and source.get("feedback_id") in statuses:
                source["status"] = statuses[str(source["feedback_id"])]
                source["checked_at"] = now
                if source["status"] == "available":
                    source["last_available_at"] = now
        try:
            self.manager.save()
        except Exception:
            self.status.setText("来源状态已更新，但未能保存到本机；请检查数据目录权限后重试。")
        self._refresh_local_list()

    def _load_page(self, cursor: str | None, sync: bool = False) -> None:
        if self.job is not None:
            return
        params = []
        if self.filter.currentIndex():
            params.append("processed=" + ("0" if self.filter.currentIndex() == 1 else "1"))
        if self.rating.currentIndex():
            params.append("rating=" + ("up", "down", "none")[self.rating.currentIndex() - 1])
        if cursor:
            params.append("cursor=" + cursor)
        sources = self._source_snapshots() if sync else {}

        def operation() -> object:
            statuses = {key: "unknown" for key in sources}
            notice = ""
            try:
                client = AdminClient()
                statuses = self._check_sources(client, sources)
                if sync:
                    try:
                        self.exports.sync(client)
                    except (ValueError, OSError):
                        notice = "部分导出副本未能同步，请稍后重试。"
                page = client.request("GET", "?" + "&".join(params))
                return page, statuses, notice
            except ValueError as exc:
                return None, statuses, str(exc)

        def complete(result: object) -> None:
            page, statuses, notice = result
            if page is not None:
                self._show_list(page)
            self.status.setText(notice or f"本页 {len(self.items)} 条。" + ("还有下一页。" if self.cursor else ""))
            self._apply_source_statuses(statuses)

        self._start(operation, complete)

    def _show_list(self, result: object) -> None:
        data = cast(dict[str, Json], result)
        self.items = cast(list[dict[str, Json]], data["items"])
        self.cursor = cast(str | None, data["cursor"])
        self.detail = None
        self.viewer.clear()
        self.source_status.clear()
        self.list.clear()
        for item in self.items:
            date = time.strftime("%m-%d %H:%M", time.localtime(int(str(item["created_at"]))))
            self.list.addItem(f"{RATINGS[str(item['rating'])]} · {'已处理' if item['processed'] else '未处理'} · "
                              f"{'对话反馈' if item['has_conversation'] else '意见建议'}\n{date} · #{str(item['feedback_id'])[:8]}")
        if self.lists.currentIndex() == 1:
            self._local_selected()

    def _selected(self) -> None:
        index = self.list.currentRow()
        if self.lists.currentIndex() != 0 or not 0 <= index < len(self.items) or self.job is not None:
            return
        feedback_id = str(self.items[index]["feedback_id"])
        self.detail = None
        self.viewer.clear()
        self.source_status.clear()
        self._start(lambda: AdminClient().request("GET", "/" + feedback_id), self._show_detail)

    def _show_detail(self, result: object) -> None:
        detail = cast(dict[str, Json], result)
        if detail.get("missing"):
            self.status.setText("反馈已撤回、删除或到期。")
            return
        self.viewer.show_detail(detail)
        self.detail = detail
        existing = self._existing_chat()
        if existing:
            self.source_status.setText(source_status_text(source_of(existing)))
        self.status.setText("本地导入的对话独立保留，不随云端记录删除。")
        self._update_actions()

    def _refresh_local_list(self) -> None:
        selected = self._local_chat()
        selected_id = selected.chat_id if selected else None
        self.local_list.blockSignals(True)
        self.local_list.clear()
        if self.manager:
            for chat in self.manager.single_character_chats():
                source = source_of(chat)
                if not source or source.get("endpoint") != self.endpoint:
                    continue
                self.local_list.addItem(chat.name + "\n" + source_status_text(source))
                item = self.local_list.item(self.local_list.count() - 1)
                item.setData(Qt.UserRole, chat.chat_id)
                if chat.chat_id == selected_id:
                    self.local_list.setCurrentItem(item)
        self.local_list.blockSignals(False)
        if self.lists.currentIndex() == 1:
            self._local_selected()

    def _list_tab_changed(self) -> None:
        self.detail = None
        self.viewer.clear()
        self.source_status.clear()
        if self.lists.currentIndex() == 1:
            self._local_selected()
        else:
            self._selected()
        self._update_actions()

    def _local_selected(self) -> None:
        if self.lists.currentIndex() != 1:
            return
        self.viewer.clear()
        self.source_status.clear()
        chat = self._local_chat()
        if chat:
            self.viewer.heading.setText(chat.name)
            self.viewer.summary.setText("本地导入对话 · 点击下方按钮打开")
            self.source_status.setText(source_status_text(source_of(chat)))
        self._update_actions()

    def _mark(self) -> None:
        if self.detail is not None:
            feedback_id = str(self.detail["feedback_id"])
            processed = not bool(self.detail["processed"])
            self._start(lambda: AdminClient().request("PATCH", "/" + feedback_id, {"processed": processed}), lambda _: self._refresh())

    def _delete(self) -> None:
        if self.detail is None or QMessageBox.question(self, "删除云端反馈", "删除这条云端反馈及受管理的导出文件？已导入的本地对话会保留。") != QMessageBox.Yes:
            return
        feedback_id = str(self.detail["feedback_id"])

        def operation() -> object:
            result = AdminClient().request("DELETE", "/" + feedback_id)
            self.exports.remove(feedback_id)
            return result

        def complete(_result: object) -> None:
            self._apply_source_statuses({feedback_id: "unavailable"})
            self._refresh()

        self._start(operation, complete)

    def _import(self) -> None:
        existing = self._existing_chat()
        if existing:
            if self.open_chat_callback and self.open_chat_callback(existing.chat_id):
                self.accept()
            return
        if self.detail is None or self.import_chat_callback is None:
            return
        feedback_id = str(self.detail["feedback_id"])

        def complete(result: object) -> None:
            detail = cast(dict[str, Json], result)
            if detail.get("missing"):
                self._apply_source_statuses({feedback_id: "unavailable"})
                self.detail = None
                self.viewer.clear()
                self.status.setText("反馈已撤回、删除或到期，不能导入。")
                return
            self._show_detail(detail)
            chat_id = self.import_chat_callback(detail, self.endpoint)
            if chat_id:
                self._refresh_local_list()
                self.status.setText("已导入为独立对话，点击“打开已导入对话”继续。")
                self._update_actions()

        self._start(lambda: AdminClient().request("GET", "/" + feedback_id), complete)

    def _export(self, backup: bool) -> None:
        if self.detail is None:
            return
        feedback_id = str(self.detail["feedback_id"])

        def operation() -> object:
            client = AdminClient()
            detail = client.request("GET", "/" + feedback_id)
            if detail.get("missing"):
                self.exports.remove(feedback_id)
                raise ValueError("反馈已撤回或到期，不能导出。")
            return self.exports.save(detail, client.endpoint, backup)

        self._start(operation, lambda path: self.status.setText(f"已导出：{path}"))

    def reject(self) -> None:
        if self.job is None:
            self.detail = None
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.job is not None:
            event.ignore()
        else:
            self.detail = None
            super().closeEvent(event)
