import os
from typing import Optional
from PyQt5.QtMultimedia import QMediaPlayer, QMediaPlaylist, QMediaContent
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import QTimer, Qt, QUrl
from PyQt5.QtGui import QIcon

from character import CharacterAttributes,  EMOTION_REFERENCE_KEYS, ref_audio_language_list
from ui_constants import dialogWindowDefaultCss

from log import get_logger

logger = get_logger(__name__)

class ChangeReferenceAudioWindow(QDialog):
    EMOTION_LABELS = {
        "happiness": "happiness（开心）",
        "sadness": "sadness（难过）",
        "anger": "anger（生气）",
        "disgust": "disgust（反感）",
        "like": "like（喜欢）",
        "surprise": "surprise（惊讶）",
        "fear": "fear（害怕）",
    }

    def __init__(self, current_character: CharacterAttributes, audio_gen_module, desktop_size):
        super().__init__()
        self.current_character = current_character
        self.audio_gen_module = audio_gen_module
        self._initializing = True
        self.audio_player = QMediaPlayer()
        self.audio_playlist = QMediaPlaylist()
        self.audio_refs: dict[str, str] = {}
        self.text_refs: dict[str, str] = {}
        self.sakiko_refs: dict[str, dict[str, str]] = {}
        self.current_key: str = "black" if self.current_character.is_sakiko else "happiness"
        self.audio_files: list[str] = []

        self.setWindowTitle("配置当前角色参考音频")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(int(desktop_size.width() * 0.62), int(desktop_size.height() * 0.62))
        self._load_config_from_disk()
        self._build_ui()
        self._refresh_mode_list(self.current_key)
        self._refresh_library()
        self._load_current_mode_to_editor()
        self._initializing = False

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.setObjectName("referenceAudioDialog")

        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel(f"角色：{self.current_character.character_name}", self))
        top_layout.addStretch(1)
        top_layout.addWidget(QLabel("参考音频语言", self))
        self.language_combo = QComboBox(self)
        self.language_combo.setObjectName("referenceLanguageCombo")
        self.language_combo.addItems(ref_audio_language_list)
        if self.current_character.gptsovits_ref_audio_lan in ref_audio_language_list:
            self.language_combo.setCurrentText(self.current_character.gptsovits_ref_audio_lan)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        top_layout.addWidget(self.language_combo)
        layout.addLayout(top_layout)

        body_layout = QHBoxLayout()
        mode_group = QGroupBox("所有emotion标签" if not self.current_character.is_sakiko else "所有状态", self)
        mode_layout = QVBoxLayout(mode_group)
        self.mode_list = QListWidget(self)
        self.mode_list.setObjectName("referenceModeList")
        self.mode_list.setMinimumWidth(170)
        self.mode_list.setMaximumWidth(220)
        self.mode_list.currentItemChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self.mode_list)
        body_layout.addWidget(mode_group)

        center_group = QGroupBox("当前emotion的配置" if not self.current_character.is_sakiko else "当前状态的配置", self)
        center_group.setObjectName("referenceCenterGroup")
        center_layout = QVBoxLayout(center_group)
        self.current_mode_label = QLabel("", self)
        self.current_audio_label = QLabel("", self)
        self.current_audio_label.setWordWrap(True)
        play_current_btn = QPushButton("播放当前音频", self)
        play_current_btn.clicked.connect(self.play_current_ref_audio)
        self.ref_text_edit = QPlainTextEdit(self)
        self.ref_text_edit.setObjectName("referenceTextEdit")
        self.ref_text_edit.setMinimumHeight(160)
        confirm_text_btn = QPushButton("确认修改", self)
        confirm_text_btn.clicked.connect(self.confirm_text_change)
        apply_layout = QHBoxLayout()
        apply_all_btn = QPushButton("将本配置应用到全部emotion", self)
        apply_all_btn.clicked.connect(self.apply_current_to_all)
        help_label = QLabel("?", self)
        help_label.setObjectName("referenceHelpLabel")
        help_label.setToolTip("把当前情绪/状态的参考音频和参考文本复制到全部项。适合你只想使用同一套参考音频的情况。")
        apply_layout.addWidget(apply_all_btn)
        apply_layout.addWidget(help_label)
        apply_layout.addStretch(1)
        center_layout.addWidget(self.current_mode_label)
        center_layout.addWidget(self.current_audio_label)
        center_layout.addWidget(play_current_btn)
        center_layout.addWidget(QLabel("参考文本", self))
        center_layout.addWidget(self.ref_text_edit)
        center_layout.addWidget(confirm_text_btn)
        center_layout.addLayout(apply_layout)
        body_layout.addWidget(center_group, 2)

        library_group = QGroupBox("候选音频素材库", self)
        library_group.setObjectName("referenceLibraryGroup")
        library_layout = QVBoxLayout(library_group)
        self.search_input = QLineEdit(self)
        self.search_input.setObjectName("referenceSearchInput")
        self.search_input.setPlaceholderText("按文件名搜索")
        self.search_input.textChanged.connect(self._refresh_library)
        self.library_container = QWidget(self)
        self.library_layout = QVBoxLayout(self.library_container)
        self.library_layout.setContentsMargins(0, 0, 0, 0)
        self.library_layout.addStretch(1)
        scroll_area = QScrollArea(self)
        scroll_area.setObjectName("referenceLibraryScroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.library_container)
        library_layout.addWidget(self.search_input)
        library_layout.addWidget(scroll_area)
        body_layout.addWidget(library_group, 2)
        layout.addLayout(body_layout)

        self.toast_label = QLabel("", self)
        self.toast_label.setStyleSheet("color: #2E7D32;")
        layout.addWidget(self.toast_label)
        self._apply_dialog_styles()

    def _mode_entries(self) -> list[tuple[str, str]]:
        if self.current_character.is_sakiko:
            return [("black", "黑祥"), ("white", "白祥")]
        return [(key, self.EMOTION_LABELS.get(key, key)) for key in EMOTION_REFERENCE_KEYS]

    def _load_config_from_disk(self) -> None:
        if self.current_character.is_sakiko:
            self.sakiko_refs = {
                "black": {
                    "audio": self._read_sakiko_audio_path("black"),
                    "text": self._read_text_file(self.audio_gen_module.ref_text_file_black_sakiko),
                },
                "white": {
                    "audio": self._read_sakiko_audio_path("white"),
                    "text": self._read_text_file(self.audio_gen_module.ref_text_file_white_sakiko),
                },
            }
        else:
            self.current_character.reload_emotion_reference_config_from_disk(migrate_if_missing=True)
            self.audio_refs = self.current_character._emotion_dict(self.current_character.gptsovits_ref_audio)
            self.text_refs = self.current_character._emotion_dict(self.current_character.gptsovits_ref_audio_text)
            for key in EMOTION_REFERENCE_KEYS:
                self.audio_refs.setdefault(key, "")
                self.text_refs.setdefault(key, "")
        self._scan_audio_files()

    def _scan_audio_files(self) -> None:
        ref_dir = os.path.join("../reference_audio", self.current_character.character_folder_name)
        self.audio_files = []
        try:
            for file in sorted(os.listdir(ref_dir)):
                if file.lower().endswith((".wav", ".mp3")):
                    self.audio_files.append(os.path.join(ref_dir, file))
        except Exception:
            logger.exception("扫描参考音频目录失败：%s", ref_dir)

    def _apply_dialog_styles(self) -> None:
        self.setStyleSheet(
            dialogWindowDefaultCss
            + """
            QDialog#referenceAudioDialog {
                background: #F4F7FB;
            }
            QGroupBox {
                border: 1px solid #D6E0EE;
                border-radius: 6px;
                margin-top: 14px;
                padding: 9px 7px 7px 7px;
                color: #5C82BD;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QListWidget#referenceModeList {
                background: #FFFFFF;
                border: 1px solid #D6E0EE;
                border-radius: 5px;
                padding: 4px;
                outline: none;
            }
            QListWidget#referenceModeList::item {
                min-height: 24px;
                padding: 3px 6px;
                border-radius: 4px;
            }
            QListWidget#referenceModeList::item:selected {
                background: #DCE8F8;
                color: #315F9D;
            }
            QComboBox#referenceLanguageCombo,
            QLineEdit#referenceSearchInput,
            QPlainTextEdit#referenceTextEdit {
                background: #FFFFFF;
                border: 1px solid #C8D5E6;
                border-radius: 5px;
                padding: 5px 7px;
                color: #3F6EA8;
                selection-background-color: #BFD4F2;
            }
            QComboBox#referenceLanguageCombo {
                min-width: 130px;
                padding-right: 24px;
            }
            QComboBox#referenceLanguageCombo::drop-down {
                width: 24px;
                border-left: 1px solid #C8D5E6;
                border-top-right-radius: 5px;
                border-bottom-right-radius: 5px;
                background: #EEF4FB;
            }
            QComboBox#referenceLanguageCombo QAbstractItemView {
                background: #FFFFFF;
                border: 1px solid #C8D5E6;
                selection-background-color: #DCE8F8;
                color: #3F6EA8;
                outline: none;
            }
            QScrollArea#referenceLibraryScroll {
                background: #FFFFFF;
                border: 1px solid #D6E0EE;
                border-radius: 5px;
            }
            QWidget#referenceAudioCard {
                background: #FFFFFF;
                border: none;
            }
            QLabel#referenceAudioFileName {
                color: #4F7DB7;
            }
            QFrame#referenceAudioSeparator {
                color: #E2EAF4;
                background: #E2EAF4;
                max-height: 1px;
            }
            QPushButton, QToolButton {
                border: 1px solid #C8D5E6;
                border-radius: 5px;
                padding: 5px 10px;
                background: #FFFFFF;
                color: #4D79B2;
            }
            QPushButton:hover, QToolButton:hover {
                background: #EEF5FF;
                border-color: #9EBBE0;
            }
            QLabel#referenceHelpLabel {
                color: #5C82BD;
                padding: 0 6px;
                font-weight: 600;
            }
            QScrollBar:vertical,
            QScrollBar:horizontal {
                background: #EEF4FB;
                border: 1px solid #D6E0EE;
                border-radius: 4px;
                margin: 0;
            }
            QScrollBar:vertical {
                width: 11px;
            }
            QScrollBar:horizontal {
                height: 11px;
            }
            QScrollBar::handle:vertical,
            QScrollBar::handle:horizontal {
                background: #B8CCE8;
                border-radius: 4px;
                min-height: 24px;
                min-width: 24px;
            }
            QScrollBar::handle:vertical:hover,
            QScrollBar::handle:horizontal:hover {
                background: #95B5DD;
            }
            QScrollBar::add-line,
            QScrollBar::sub-line {
                width: 0;
                height: 0;
                border: none;
                background: transparent;
            }
            """
        )

    def _read_sakiko_audio_path(self, key: str) -> str:
        file_name = "default_ref_audio_black.txt" if key == "black" else "default_ref_audio_white.txt"
        fallback = (
            self.audio_gen_module.ref_audio_file_black_sakiko
            if key == "black"
            else self.audio_gen_module.ref_audio_file_white_sakiko
        )
        path_file = os.path.join("../reference_audio", "sakiko", file_name)
        if os.path.exists(path_file):
            try:
                with open(path_file, "r", encoding="utf-8") as file:
                    candidate = file.read().strip()
                if candidate:
                    return candidate
            except Exception:
                logger.exception("读取祥子参考音频路径失败：%s", path_file)
        return fallback

    @staticmethod
    def _read_text_file(path: Optional[str]) -> str:
        if not path or not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as file:
                return file.read().strip()
        except Exception:
            logger.exception("读取参考文本失败：%s", path)
            return ""

    @staticmethod
    def _write_text_file(path: str, text: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as file:
                file.write(text)
            return True
        except Exception:
            logger.exception("写入参考文本失败：%s", path)
            return False

    def _current_audio(self) -> str:
        if self.current_character.is_sakiko:
            return self.sakiko_refs.get(self.current_key, {}).get("audio", "")
        return self.audio_refs.get(self.current_key, "")

    def _current_text(self) -> str:
        if self.current_character.is_sakiko:
            return self.sakiko_refs.get(self.current_key, {}).get("text", "")
        return self.text_refs.get(self.current_key, "")

    def _set_current_audio(self, path: str) -> bool:
        if self.current_character.is_sakiko:
            self.sakiko_refs.setdefault(self.current_key, {})["audio"] = path
            return self._save_sakiko_key(self.current_key)
        self.audio_refs[self.current_key] = path
        return self._save_non_sakiko()

    def _set_current_text(self, text: str) -> bool:
        if self.current_character.is_sakiko:
            self.sakiko_refs.setdefault(self.current_key, {})["text"] = text
            return self._save_sakiko_key(self.current_key)
        self.text_refs[self.current_key] = text
        return self._save_non_sakiko()

    def _save_non_sakiko(self) -> bool:
        ok = self.current_character.save_emotion_reference_config(self.audio_refs, self.text_refs)
        if ok:
            self.audio_refs = self.current_character._emotion_dict(self.current_character.gptsovits_ref_audio)
            self.text_refs = self.current_character._emotion_dict(self.current_character.gptsovits_ref_audio_text)
        return ok

    def _save_sakiko_key(self, key: str) -> bool:
        refs = self.sakiko_refs.get(key, {})
        audio = refs.get("audio", "")
        text = refs.get("text", "")
        if key == "black":
            audio_path_file = "../reference_audio/sakiko/default_ref_audio_black.txt"
            text_path = self.audio_gen_module.ref_text_file_black_sakiko
            self.audio_gen_module.ref_audio_file_black_sakiko = audio
        else:
            audio_path_file = "../reference_audio/sakiko/default_ref_audio_white.txt"
            text_path = self.audio_gen_module.ref_text_file_white_sakiko
            self.audio_gen_module.ref_audio_file_white_sakiko = audio
        audio_ok = self._write_text_file(audio_path_file, audio)
        text_ok = self._write_text_file(text_path, text)
        return audio_ok and text_ok

    def _refresh_mode_list(self, select_key: Optional[str] = None) -> None:
        select_key = select_key or self.current_key
        self.mode_list.blockSignals(True)
        self.mode_list.clear()
        selected_row = 0
        for row, (key, label) in enumerate(self._mode_entries()):
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            self.mode_list.addItem(item)
            if key == select_key:
                selected_row = row
        self.mode_list.setCurrentRow(selected_row)
        self.mode_list.blockSignals(False)

    def _is_mode_valid(self, key: str) -> bool:
        if self.current_character.is_sakiko:
            refs = self.sakiko_refs.get(key, {})
            return bool(refs.get("text", "").strip()) and os.path.exists(refs.get("audio", ""))
        return bool(self.text_refs.get(key, "").strip()) and os.path.exists(self.audio_refs.get(key, ""))

    def _on_mode_changed(self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem] = None) -> None:
        if current is None:
            return
        key = current.data(Qt.UserRole)
        if isinstance(key, str):
            self.current_key = key
            self._load_current_mode_to_editor()

    def _load_current_mode_to_editor(self) -> None:
        label = dict(self._mode_entries()).get(self.current_key, self.current_key)
        audio_path = self._current_audio()
        self.current_mode_label.setText(f"当前项：{label}")
        self.current_audio_label.setText(f"当前音频：{os.path.basename(audio_path) if audio_path else '无'}")
        self.current_audio_label.setToolTip(audio_path)
        self.ref_text_edit.setPlainText(self._current_text())

    def _refresh_library(self, *_args) -> None:
        while self.library_layout.count():
            item = self.library_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        keyword = self.search_input.text().strip().lower() if hasattr(self, "search_input") else ""
        for path in self.audio_files:
            if keyword and keyword not in os.path.basename(path).lower():
                continue
            card = QWidget(self)
            card.setObjectName("referenceAudioCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 7, 8, 7)
            card_layout.setSpacing(6)

            name_label = QLabel(os.path.basename(path), card)
            name_label.setObjectName("referenceAudioFileName")
            name_label.setToolTip(path)
            name_label.setWordWrap(True)

            action_layout = QHBoxLayout()
            play_btn = QToolButton(card)
            play_btn.setIcon(QIcon("./icons/play.svg"))
            play_btn.clicked.connect(lambda _checked=False, p=path: self.play_ref_audio(p))
            use_btn = QPushButton("使用", card)
            use_btn.clicked.connect(lambda _checked=False, p=path: self.use_audio_for_current(p))
            action_layout.addWidget(play_btn)
            action_layout.addWidget(use_btn)
            action_layout.addStretch(1)

            card_layout.addWidget(name_label)
            card_layout.addLayout(action_layout)
            self.library_layout.addWidget(card)

            separator = QFrame(self)
            separator.setFrameShape(QFrame.HLine)
            separator.setObjectName("referenceAudioSeparator")
            self.library_layout.addWidget(separator)
        self.library_layout.addStretch(1)

    def _on_language_changed(self, *_args) -> None:
        if self._initializing:
            return
        language = self.language_combo.currentText()
        if self.current_character.write_reference_audio_language(language):
            self._show_toast("参考音频语言已更新")
        else:
            self._show_toast("参考音频语言保存失败", success=False)

    def play_current_ref_audio(self) -> None:
        audio_path = self._current_audio()
        if audio_path:
            self.play_ref_audio(audio_path)

    def play_ref_audio(self, ref_audio_path: str) -> None:
        audio_path = os.path.abspath(ref_audio_path)
        url = QUrl.fromLocalFile(audio_path)
        self.audio_playlist.clear()
        self.audio_playlist.addMedia(QMediaContent(url))
        self.audio_playlist.setCurrentIndex(0)
        self.audio_player.setPlaylist(self.audio_playlist)
        self.audio_player.play()

    def use_audio_for_current(self, path: str) -> None:
        if self._set_current_audio(path):
            self._refresh_mode_list(self.current_key)
            self._load_current_mode_to_editor()
            self._refresh_library()
            self._show_toast(f"已更新 {dict(self._mode_entries()).get(self.current_key, self.current_key)} 参考音频")
        else:
            self._show_toast("参考音频保存失败", success=False)

    def confirm_text_change(self) -> None:
        text = self.ref_text_edit.toPlainText().strip()
        if not text:
            self._show_toast("参考文本不能为空", success=False)
            return
        if self._set_current_text(text):
            self._refresh_mode_list(self.current_key)
            self._refresh_library()
            self._show_toast("参考文本已保存")
        else:
            self._show_toast("参考文本保存失败", success=False)

    def apply_current_to_all(self) -> None:
        audio = self._current_audio()
        text = self.ref_text_edit.toPlainText().strip()
        if not audio or not text:
            self._show_toast("当前音频或参考文本为空，无法应用到全部", success=False)
            return
        if self.current_character.is_sakiko:
            for key in ("black", "white"):
                self.sakiko_refs.setdefault(key, {})["audio"] = audio
                self.sakiko_refs.setdefault(key, {})["text"] = text
            ok = self._save_sakiko_key("black") and self._save_sakiko_key("white")
        else:
            for key in EMOTION_REFERENCE_KEYS:
                self.audio_refs[key] = audio
                self.text_refs[key] = text
            ok = self._save_non_sakiko()
        if ok:
            self._refresh_mode_list(self.current_key)
            self._load_current_mode_to_editor()
            self._refresh_library()
            self._show_toast("已应用到全部")
        else:
            self._show_toast("应用到全部失败", success=False)

    def _show_toast(self, message: str, success: bool = True) -> None:
        self.toast_label.setStyleSheet("color: #2E7D32;" if success else "color: #B00020;")
        self.toast_label.setText(message)
        QTimer.singleShot(2200, self.toast_label.clear)
