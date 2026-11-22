"""在没有本地角色包时查看归档中的台词和录音。"""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from chat.chat import Chat
from ui_qt.components.chat_display import ChatDisplay
from ui_qt.theme import DEFAULT_CHARACTER_THEME_SEED, derive_theme_palette


def show_archive_reader(parent: QWidget, chat: Chat) -> None:
    """打开独立阅读窗口，按消息标识播放已恢复的本地录音。"""
    dialog = QDialog(parent)
    dialog.setWindowTitle(chat.name)
    dialog.resize(640, 540)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("安装对应角色后可继续生成。点击带星号的角色名可播放已有录音。"))
    view = ChatDisplay(derive_theme_palette(DEFAULT_CHARACTER_THEME_SEED), dialog)
    view.setContextMenuPolicy(Qt.NoContextMenu)
    layout.addWidget(view)
    player = QMediaPlayer(dialog)

    def play(link: QUrl) -> None:
        """仅从当前档案内的对应消息取录音。"""
        index = link.query().removeprefix("msg=")
        if index.isdecimal() and int(index) < len(chat.message_list):
            path = Path(chat.message_list[int(index)].audio_path)
            if path.is_file():
                player.setMedia(QMediaContent(QUrl.fromLocalFile(str(path.resolve()))))
                player.play()

    view.audioLinkClicked.connect(play)
    view.render_chat(chat)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec_()
    player.stop()
