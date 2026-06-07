from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QCloseEvent, QIcon
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer, QMediaPlaylist
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from log import get_logger
from qconfig import d_sakiko_config
from ui_constants import dialogWindowDefaultCss

logger = get_logger(__name__)

BGM_DIRECTORY = (
    Path(__file__).resolve().parents[3]
    / "reference_audio"
    / "small_theater_bgm"
)
SUPPORTED_BGM_SUFFIXES = {".mp3", ".wav"}


class CustomBGMDialog(QDialog):
    """管理、试听并选择小剧场背景音乐。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("更改小剧场背景音乐")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self.audio_player = QMediaPlayer(self)
        self.audio_playlist = QMediaPlaylist(self)
        self.audio_player.setPlaylist(self.audio_playlist)
        self.audio_player.error.connect(self._handle_audio_error)

        layout = QVBoxLayout()
        current_bgm_layout = QHBoxLayout()
        current_bgm_name = Path(
            d_sakiko_config.multi_char_background_music_path.value
        ).name

        self.current_bgm_label = QLabel(f"当前背景音乐: {current_bgm_name}")
        self.play_current_bgm_button = QToolButton()
        self.play_current_bgm_button.setIcon(QIcon("./icons/play.svg"))
        self.play_current_bgm_button.clicked.connect(self.toggle_current_bgm)

        current_bgm_layout.addWidget(self.current_bgm_label)
        current_bgm_layout.addWidget(self.play_current_bgm_button)
        layout.addLayout(current_bgm_layout)

        self.error_label = QLabel("", self)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color:red;")
        self.error_label.setVisible(False)

        select_new_bgm_group = QGroupBox("选择新的背景音乐:")
        self.select_new_bgm_layout = QVBoxLayout()
        select_new_bgm_group.setLayout(self.select_new_bgm_layout)
        layout.addWidget(select_new_bgm_group)

        control_layout = QHBoxLayout()
        add_bgm_button = QPushButton("添加背景音乐", self)
        add_bgm_button.clicked.connect(self.ask_for_bgm_path)
        refresh_bgm_button = QPushButton("刷新背景音乐", self)
        refresh_bgm_button.clicked.connect(self.refresh_bgm_display)
        control_layout.addWidget(add_bgm_button)
        control_layout.addWidget(refresh_bgm_button)
        layout.addLayout(control_layout)
        layout.addWidget(self.error_label)

        self.setLayout(layout)
        self.setMinimumHeight(300)
        self.setStyleSheet(dialogWindowDefaultCss)
        self.refresh_bgm_display()

    def _remove_all_widgets(self, layout: QLayout) -> None:
        """递归移除布局中的全部控件和子布局。"""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._remove_all_widgets(child_layout)

    def refresh_bgm_display(self) -> None:
        """重新扫描背景音乐目录并刷新可选项。"""
        self.set_error_message(None)
        try:
            all_bgm_files = sorted(
                (
                    path
                    for path in BGM_DIRECTORY.iterdir()
                    if path.is_file()
                    and path.suffix.lower() in SUPPORTED_BGM_SUFFIXES
                ),
                key=lambda path: path.name.casefold(),
            )
        except OSError:
            logger.exception("背景音乐文件夹读取失败：%s", BGM_DIRECTORY)
            self.set_error_message("背景音乐文件夹读取失败")
            all_bgm_files = []

        self._remove_all_widgets(self.select_new_bgm_layout)
        current_bgm_path = Path(
            d_sakiko_config.multi_char_background_music_path.value
        ).resolve()

        for bgm_path in all_bgm_files:
            single_bgm_layout = QHBoxLayout()
            name_label = QLabel(bgm_path.name)

            play_btn = QToolButton()
            play_btn.setIcon(QIcon("./icons/play.svg"))
            play_btn.clicked.connect(
                lambda checked=False, path=bgm_path: self.toggle_bgm(path)
            )

            select_btn = QToolButton()
            select_btn.setText("选择")
            select_btn.clicked.connect(
                lambda checked=False, path=bgm_path: self.replace_bgm(path)
            )

            delete_btn = QToolButton()
            delete_btn.setText("删除")
            delete_btn.setEnabled(bgm_path.resolve() != current_bgm_path)
            delete_btn.clicked.connect(
                lambda checked=False, path=bgm_path: self.delete_bgm(path)
            )

            single_bgm_layout.addWidget(name_label)
            single_bgm_layout.addWidget(play_btn)
            single_bgm_layout.addWidget(select_btn)
            single_bgm_layout.addWidget(delete_btn)
            self.select_new_bgm_layout.addLayout(single_bgm_layout)

    def set_error_message(self, message: str | None) -> None:
        """显示或清除对话框中的错误信息。"""
        self.error_label.setText(message or "")
        self.error_label.setVisible(bool(message))

    def _handle_audio_error(self, error: QMediaPlayer.Error) -> None:
        """显示试听播放器报告的媒体错误。"""
        message = self.audio_player.errorString() or f"错误代码 {error}"
        logger.error("背景音乐试听失败：%s", message)
        self.set_error_message(f"背景音乐试听失败：{message}")

    def toggle_current_bgm(self) -> None:
        """播放或停止当前选中的背景音乐。"""
        self.toggle_bgm(
            Path(d_sakiko_config.multi_char_background_music_path.value)
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭窗口时停止试听。"""
        self.audio_player.stop()
        super().closeEvent(event)

    def toggle_bgm(self, bgm_path: Path) -> None:
        """试听指定背景音乐，再次点击同一首音乐时停止。"""
        absolute_path = bgm_path.resolve()
        current_path = Path(
            self.audio_playlist.currentMedia().canonicalUrl().toLocalFile()
        )
        if self.audio_player.state() == QMediaPlayer.PlayingState:
            self.audio_player.stop()
            if current_path == absolute_path:
                return

        if not absolute_path.is_file():
            self.set_error_message(f"背景音乐文件不存在：{absolute_path.name}")
            return

        self.set_error_message(None)
        self.audio_playlist.clear()
        url = QUrl.fromLocalFile(str(absolute_path))
        if not self.audio_playlist.addMedia(QMediaContent(url)):
            self.set_error_message(
                self.audio_playlist.errorString() or "无法载入背景音乐"
            )
            return
        self.audio_playlist.setCurrentIndex(0)
        self.audio_player.play()

    def replace_bgm(self, new_bgm_file: Path) -> None:
        """将指定文件设为当前小剧场背景音乐。"""
        absolute_path = new_bgm_file.resolve()
        if (
            not absolute_path.is_file()
            or absolute_path.suffix.lower() not in SUPPORTED_BGM_SUFFIXES
        ):
            logger.error("更改背景音乐失败，无效文件：%s", absolute_path)
            self.set_error_message("更改背景音乐失败：文件不存在或格式不受支持")
            return

        d_sakiko_config.set(
            d_sakiko_config.multi_char_background_music_path,
            str(absolute_path),
        )
        self.current_bgm_label.setText(f"当前背景音乐: {absolute_path.name}")
        self.set_error_message(None)
        self.refresh_bgm_display()

    def add_bgm(self, new_bgm_file: str) -> None:
        """把外部背景音乐复制到音乐目录，但不自动选中。"""
        source_path = Path(new_bgm_file).resolve()
        if (
            not source_path.is_file()
            or source_path.suffix.lower() not in SUPPORTED_BGM_SUFFIXES
        ):
            self.set_error_message("添加背景音乐失败：文件不存在或格式不受支持")
            return

        try:
            BGM_DIRECTORY.mkdir(parents=True, exist_ok=True)
            destination_path = BGM_DIRECTORY / source_path.name
            if source_path != destination_path.resolve():
                with tempfile.NamedTemporaryFile(
                    dir=BGM_DIRECTORY,
                    prefix=".d_sakiko_bgm_",
                    suffix=source_path.suffix,
                    delete=False,
                ) as temporary_file:
                    temporary_path = Path(temporary_file.name)
                try:
                    shutil.copy2(source_path, temporary_path)
                    os.replace(temporary_path, destination_path)
                finally:
                    temporary_path.unlink(missing_ok=True)
        except OSError:
            logger.exception("添加背景音乐失败：%s", source_path)
            self.set_error_message("添加背景音乐失败")
            return

        self.set_error_message(None)
        self.refresh_bgm_display()

    def delete_bgm(self, bgm_file: Path) -> None:
        """删除非当前选中的背景音乐。"""
        absolute_path = bgm_file.resolve()
        current_path = Path(
            d_sakiko_config.multi_char_background_music_path.value
        ).resolve()
        if absolute_path == current_path:
            self.set_error_message("不能删除当前正在使用的背景音乐")
            return

        try:
            absolute_path.relative_to(BGM_DIRECTORY.resolve())
            absolute_path.unlink()
        except (OSError, ValueError):
            logger.exception("删除背景音乐失败：%s", absolute_path)
            self.set_error_message("删除背景音乐失败")
            return

        self.set_error_message(None)
        self.refresh_bgm_display()

    def ask_for_bgm_path(self) -> None:
        """打开文件选择器并添加用户选中的音乐。"""
        file_dialog = QFileDialog(self)
        file_dialog.setNameFilter("Audio Files (*.wav *.mp3)")
        if file_dialog.exec_():
            selected_files = file_dialog.selectedFiles()
            if selected_files:
                self.add_bgm(selected_files[0])
