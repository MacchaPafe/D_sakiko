"""反馈详情的只读分区展示，不解释上传的 HTML 或外部资源。"""

from __future__ import annotations

import html
import json
from typing import cast

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QStackedWidget,
    QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from feedback.protocol import Json, validate_payload

RATINGS = {"up": "赞", "down": "踩", "none": "未评价"}


def plain_view() -> QPlainTextEdit:
    """创建可选中和复制的纯文本视图。"""
    editor = QPlainTextEdit()
    editor.setReadOnly(True)
    return editor


def plain_label(text: str = "") -> QLabel:
    """任何来自反馈的标签都显式使用纯文本。"""
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


class FeedbackDetailView(QWidget):
    """评价、评论、消息、诊断及默认折叠的技术信息。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.target: int | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.heading = plain_label("请选择一条反馈")
        self.summary = plain_label()
        layout.addWidget(self.heading)
        layout.addWidget(self.summary)
        layout.addWidget(plain_label("评论"))
        self.comment = plain_view()
        self.comment.setMaximumHeight(100)
        layout.addWidget(self.comment)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        conversation = QWidget()
        messages_layout = QVBoxLayout(conversation)
        bar = QHBoxLayout()
        self.locate = QPushButton("定位反馈消息")
        self.locate.clicked.connect(self._locate)
        bar.addWidget(self.locate)
        self.raw_toggle = QPushButton("查看 JSON")
        self.raw_toggle.setCheckable(True)
        self.raw_toggle.toggled.connect(self._toggle_raw)
        bar.addWidget(self.raw_toggle)
        bar.addStretch()
        messages_layout.addLayout(bar)
        self.messages = QTextBrowser()
        self.messages.setOpenLinks(False)
        self.messages.setOpenExternalLinks(False)
        self.raw = plain_view()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.messages)
        self.stack.addWidget(self.raw)
        messages_layout.addWidget(self.stack, 1)
        self.tabs.addTab(conversation, "对话")
        self.diagnostics = plain_view()
        self.tabs.addTab(self.diagnostics, "世界书诊断")
        self.prompts = plain_view()
        self.tabs.addTab(self.prompts, "提示词快照")
        self.metadata_toggle = QToolButton()
        self.metadata_toggle.setText("技术信息")
        self.metadata_toggle.setCheckable(True)
        self.metadata_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.metadata_toggle.setArrowType(Qt.RightArrow)
        self.metadata = plain_view()
        self.metadata.setMaximumHeight(130)
        self.metadata.hide()
        self.metadata_toggle.toggled.connect(self._toggle_metadata)
        layout.addWidget(self.metadata_toggle)
        layout.addWidget(self.metadata)
        self.clear()

    def _toggle_raw(self, checked: bool) -> None:
        self.stack.setCurrentIndex(1 if checked else 0)
        self.raw_toggle.setText("查看对话" if checked else "查看 JSON")

    def _toggle_metadata(self, checked: bool) -> None:
        self.metadata.setVisible(checked)
        self.metadata_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

    def _locate(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.raw_toggle.setChecked(False)
        if self.target is not None:
            self.messages.scrollToAnchor(f"message-{self.target}")

    def clear(self) -> None:
        self.target = None
        self.heading.setText("请选择一条反馈")
        self.summary.clear()
        for editor in (self.comment, self.messages, self.raw, self.diagnostics, self.prompts, self.metadata):
            editor.clear()
        self.locate.setEnabled(False)
        self.raw_toggle.setChecked(False)
        self.raw_toggle.setEnabled(False)
        self.metadata_toggle.setChecked(False)
        self.tabs.setCurrentIndex(0)

    def show_detail(self, detail: dict[str, Json]) -> None:
        """只渲染通过 v2 校验的内容；所有上传文本先转义。"""
        payload = cast(dict[str, Json], detail["payload"])
        validate_payload(payload)
        self.clear()
        conversation = cast(dict[str, Json] | None, payload["conversation"])
        self.target = int(payload["target"]) if payload["target"] is not None else None
        self.heading.setText(str(conversation["name"]) if conversation else "意见建议")
        target_text = f"针对第 {self.target + 1} 条消息" if self.target is not None else ("针对整段对话" if conversation else "未附带对话")
        self.summary.setText(f"{RATINGS[str(payload['rating'])]} · {'已处理' if detail['processed'] else '未处理'} · {target_text}")
        self.comment.setPlainText(str(payload["comment"]) or "未填写评论。")
        self.locate.setEnabled(self.target is not None)
        self.raw_toggle.setEnabled(conversation is not None)
        if conversation is None:
            self.messages.setPlainText("此反馈未附带对话。")
            self.raw.setPlainText("此反馈未附带对话。")
            self.prompts.setPlainText("此反馈未附带提示词。")
        else:
            rows = []
            for index, message in enumerate(cast(list[dict[str, Json]], conversation["messages"])):
                speaker = "用户" if message["role"] == "user" else str(message["character_name"])
                target = "【反馈目标】 " if index == self.target else ""
                # QTextBrowser 只接收固定标记和转义后的文字，无法注入图片或链接。
                title = html.escape(f"{target}{index + 1} · {speaker}")
                text = html.escape(str(message["text"])).replace("\n", "<br>")
                translation = html.escape(str(message["translation"])).replace("\n", "<br>")
                emotion = html.escape(str(message["emotion"]))
                extra = f"<br>翻译：{translation}" if translation else ""
                if message["role"] == "assistant":
                    extra += f"<br>情绪：{emotion}"
                rows.append(f'<a name="message-{index}"></a><p><b>{title}</b><br>{text}{extra}</p>')
            if rows:
                self.messages.setHtml("".join(rows))
            else:
                self.messages.setPlainText("此对话尚无消息。")
            self.raw.setPlainText(json.dumps(conversation, ensure_ascii=False, indent=2))
            context = cast(dict[str, Json], payload["prompt_context"])
            self.prompts.setPlainText("基础提示词（导入时保留）\n\n" + str(context["base_system_prompt"])
                                     + "\n\n运行时指令（仅供诊断，导入时不包含）\n\n" + str(context["runtime_system_prompt"]))
        diagnostics = payload["worldbook_diagnostics"]
        if not conversation:
            diagnostic_text = "此反馈未附带世界书诊断。"
        elif not payload["worldbook_enabled"]:
            diagnostic_text = "此对话未启用世界书。"
        elif not diagnostics:
            diagnostic_text = "已启用世界书，但没有诊断记录。"
        else:
            diagnostic_text = json.dumps(diagnostics, ensure_ascii=False, indent=2)
        self.diagnostics.setPlainText(diagnostic_text)
        metadata = {k: v for k, v in detail.items() if k != "payload"}
        metadata.update({k: v for k, v in payload.items() if k not in
                         {"conversation", "comment", "rating", "worldbook_diagnostics", "prompt_context"}})
        if isinstance(payload["prompt_context"], dict):
            metadata["prompt_context"] = {k: v for k, v in payload["prompt_context"].items() if k in {"source", "rendered_at"}}
        self.metadata.setPlainText(json.dumps(metadata, ensure_ascii=False, indent=2))
