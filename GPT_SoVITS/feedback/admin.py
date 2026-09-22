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
from PyQt5.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from feedback.dialogs import NetworkJob
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
    """开发者同进程管理面板，不加载角色资产或执行上传内容。"""

    def __init__(self, palette: ThemePalette, parent: QWidget | None = None) -> None:
        """创建筛选、分页、纯文本详情及管理操作。"""
        super().__init__(parent)
        self.setWindowTitle("反馈管理")
        self.resize(900, 650)
        self.setStyleSheet(build_dialog_theme_stylesheet(palette))
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
        bar.addWidget(self.filter)
        bar.addWidget(self.rating)
        for label, callback in (("刷新", self._refresh), ("下一页", self._next_page)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            bar.addWidget(button)
        layout.addLayout(bar)
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._selected)
        layout.addWidget(self.list, 1)
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        layout.addWidget(self.viewer, 3)
        actions = QHBoxLayout()
        for label, callback in (("标为已处理", lambda: self._mark(True)), ("标为未处理", lambda: self._mark(False)),
                                ("导出反馈 JSON", lambda: self._export(False)), ("导出文本对话备份", lambda: self._export(True)), ("删除", self._delete)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QLabel("查看不会加载角色、附件或外部链接。导出位于专用目录，打开面板时清理到期和已撤回副本；手动复制的文件需另行删除。")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._refresh()

    def _start(self, operation: CallableOperation, complete: CallableResult) -> None:
        """串行执行后台操作，保持 UI 可响应。"""
        if self.job is not None:
            return
        self.list.setEnabled(False)
        self.job = NetworkJob(operation, self)
        self.job.finished.connect(lambda: self._finished(complete))
        self.job.start()

    def _finished(self, complete: CallableResult) -> None:
        """处理后台结果，错误不泄漏凭据和响应原文。"""
        assert self.job is not None
        job = self.job
        self.job = None
        self.list.setEnabled(True)
        if job.error:
            self.status.setText(job.error)
        else:
            try:
                complete(job.result)
            except (ValueError, KeyError, TypeError):
                self.status.setText("管理响应不符合当前格式，请更新程序。")
        job.deleteLater()

    def _refresh(self) -> None:
        """重新加载第一页并同步受管理副本。"""
        self._load_page(None, sync=True)

    def _next_page(self) -> None:
        """使用服务端游标加载下一页。"""
        if self.cursor:
            self._load_page(self.cursor)

    def _load_page(self, cursor: str | None, sync: bool = False) -> None:
        """冻结筛选条件后查询，不把正文放进列表请求。"""
        params = []
        if self.filter.currentIndex():
            params.append("processed=" + ("0" if self.filter.currentIndex() == 1 else "1"))
        if self.rating.currentIndex():
            params.append("rating=" + ("up", "down", "none")[self.rating.currentIndex() - 1])
        if cursor:
            params.append("cursor=" + cursor)

        def operation() -> object:
            """执行受管理副本同步和列表读取。"""
            client = AdminClient()
            if sync:
                self.exports.sync(client)
            return client.request("GET", "?" + "&".join(params))

        self._start(operation, self._show_list)

    def _show_list(self, result: object) -> None:
        """填充纯文本列表并清除上一份详情。"""
        data = cast(dict[str, Json], result)
        self.items = cast(list[dict[str, Json]], data["items"])
        self.cursor = cast(str | None, data["cursor"])
        self.detail = None
        self.viewer.clear()
        self.list.clear()
        for item in self.items:
            self.list.addItem(f"{item['feedback_id']} · {item['rating']} · {'已处理' if item['processed'] else '未处理'}")
        self.status.setText(f"本页 {len(self.items)} 条。" + ("还有下一页。" if self.cursor else ""))

    def _selected(self) -> None:
        """按编号读取单份反馈，避免把整个数据库正文下载到内存。"""
        index = self.list.currentRow()
        if not 0 <= index < len(self.items) or self.job is not None:
            return
        feedback_id = str(self.items[index]["feedback_id"])
        self.detail = None
        self.viewer.clear()
        self._start(lambda: AdminClient().request("GET", "/" + feedback_id), self._show_detail)

    def _show_detail(self, result: object) -> None:
        """纯文本显示完整反馈，不解释 HTML、模板或资源路径。"""
        detail = cast(dict[str, Json], result)
        if detail.get("missing"):
            self.status.setText("反馈已撤回或到期。")
            return
        validate_payload(cast(dict[str, Json], detail["payload"]))
        self.detail = detail
        self.viewer.setPlainText(json.dumps(detail["payload"], ensure_ascii=False, indent=2))

    def _mark(self, processed: bool) -> None:
        """只修改工作流状态，不改写用户正文。"""
        if self.detail is not None:
            feedback_id = str(self.detail["feedback_id"])
            self._start(lambda: AdminClient().request("PATCH", "/" + feedback_id, {"processed": processed}), lambda _result: self._refresh())

    def _delete(self) -> None:
        """删除云端内容以及本工具管理的本机副本。"""
        if self.detail is None:
            return
        feedback_id = str(self.detail["feedback_id"])

        def operation() -> object:
            """服务器确认删除后清理受管理导出。"""
            result = AdminClient().request("DELETE", "/" + feedback_id)
            self.exports.remove(feedback_id)
            return result

        self._start(operation, lambda _result: self._refresh())

    def _export(self, backup: bool) -> None:
        """导出前重新读取，避免导出已撤回的内存副本。"""
        if self.detail is None:
            return
        feedback_id = str(self.detail["feedback_id"])

        def operation() -> object:
            """确认反馈仍有效后写入专用目录。"""
            client = AdminClient()
            detail = client.request("GET", "/" + feedback_id)
            if detail.get("missing"):
                self.exports.remove(feedback_id)
                raise ValueError("反馈已撤回或到期，不能导出。")
            return self.exports.save(detail, client.endpoint, backup)

        self._start(operation, lambda path: self.status.setText(f"已导出：{path}"))

    def reject(self) -> None:
        """后台操作完成后允许关闭。"""
        if self.job is None:
            self.detail = None
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """避免关闭弹窗时销毁后台线程。"""
        if self.job is not None:
            event.ignore()
        else:
            self.detail = None
            super().closeEvent(event)
