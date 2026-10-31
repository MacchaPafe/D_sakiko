"""小剧场新增台词表单：只收集消息字段，不请求 LLM 或合成音频。"""
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QPlainTextEdit, QVBoxLayout,
)
from ui.components.multi_char_icons import set_button_icon


class MultiCharInsertDialog(QDialog):
    """允许插入角色台词或不绑定角色的静音文本。"""

    def __init__(self, names, current_character, parent=None):
        """沿用编排器主题，初始化角色、原文、翻译和静音控件。"""
        super().__init__(parent)
        self.setObjectName("multiCharInsertDialog")
        self.setWindowTitle("添加台词")
        self.setFont(parent.font())
        self.setStyleSheet(parent.styleSheet())
        self.resize(round(self.fontMetrics().height() * 25), 0)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.speaker = QComboBox()
        self.speaker.setObjectName("multiCharInsertSpeaker")
        for name in names:
            self.speaker.addItem(name, name)
        self.speaker.addItem("无", "")
        index = self.speaker.findData(current_character)
        self.speaker.setCurrentIndex(max(0, index))
        form.addRow("说话角色", self.speaker)
        self.original = QPlainTextEdit()
        self.original.setObjectName("multiCharOriginalInput")
        self.original.setPlaceholderText("填写原文（必填）")
        self.translation = QPlainTextEdit()
        self.translation.setObjectName("multiCharTranslationInput")
        self.translation.setPlaceholderText("翻译（可选）")
        for field in (self.original, self.translation):
            field.setFixedHeight(round(self.fontMetrics().height() * 4))
        form.addRow("原文", self.original)
        form.addRow("翻译", self.translation)
        self.muted = QCheckBox("本句静音")
        set_button_icon(self.muted, "mute", self)
        self.muted.setObjectName("multiCharMuted")
        self.reset_mute_default()
        form.addRow("", self.muted)
        root.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.confirm = self.buttons.button(QDialogButtonBox.Ok)
        self.confirm.setText("添加")
        set_button_icon(self.confirm, "add_white", self)
        self.confirm.setObjectName("multiCharSave")
        self.confirm.setEnabled(False)
        cancel = self.buttons.button(QDialogButtonBox.Cancel)
        cancel.setText("取消")
        set_button_icon(cancel, "discard", self)
        cancel.setProperty("discardAction", True)
        cancel.setObjectName("multiCharControl")
        root.addWidget(self.buttons)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.original.textChanged.connect(self.validate_text)
        self.speaker.currentIndexChanged.connect(self.reset_mute_default)
        self.original.setFocus()

    def reset_mute_default(self, *_):
        """切换角色时使用对应默认值，无角色默认静音而角色台词默认有声。"""
        self.muted.setChecked(not bool(self.speaker.currentData()))

    def validate_text(self):
        """原文只有空白时禁止确认，避免插入没有内容的占位消息。"""
        self.confirm.setEnabled(bool(self.original.toPlainText().strip()))

    def message_data(self):
        """返回普通消息的数据，不生成额外的旁白标志或伪角色。"""
        return {
            "character_name": self.speaker.currentData(),
            "text": self.original.toPlainText(),
            "translation": self.translation.toPlainText(),
            "muted": self.muted.isChecked(),
            "emotion": "normal",
            "audio_path": "",
        }
