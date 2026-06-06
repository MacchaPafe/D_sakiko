# 此文件包含了“用户自身角色设定”的相关对话框和组件

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal, QUrl
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFileDialog, QFrame, QListWidgetItem, QStackedWidget
from PyQt5.QtGui import QIcon, QDesktopServices

from qfluentwidgets import (
    ListWidget, PushButton, LineEdit, TextEdit,
    StrongBodyLabel, SubtitleLabel, AvatarWidget,
    SwitchButton, ComboBox, CardWidget, IconWidget,
    FluentIcon, InfoBar, InfoBarPosition, TransparentToolButton,
    BodyLabel, ToolTipFilter, SegmentedWidget,
    SettingCardGroup, PushSettingCard
)

from ..components.fluent_icon import MyFluentIcon
from ..custom_widgets.transparent_scroll_area import TransparentScrollArea

try:
    from character import GetCharacterAttributes, CharacterAttributes
except ImportError:
    from GPT_SoVITS.character import GetCharacterAttributes, CharacterAttributes


AVATAR_PATH = Path("../avatar")
AVATAR_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp"})


def _get_internal_avatar_path(persona_id: str | None, suffix: str) -> Path:
    """根据用户人设 ID 和图片扩展名生成内部头像路径。"""
    if (
            not persona_id
            or Path(persona_id).name != persona_id
            or persona_id in {".", ".."}
    ):
        raise ValueError("用户人设 ID 无效")

    normalized_suffix = suffix.lower()
    if normalized_suffix not in AVATAR_SUFFIXES:
        raise ValueError("头像文件扩展名无效")

    return AVATAR_PATH / f"{persona_id}{normalized_suffix}"


def _copy_avatar_to_internal_directory(
        source_path: str,
        persona_id: str | None,
) -> Path:
    """将头像原子复制到内部目录，复制失败时保留已有头像。"""
    destination = _get_internal_avatar_path(
        persona_id,
        Path(source_path).suffix,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    source = Path(source_path)
    if source.resolve() == destination.resolve():
        return destination

    file_descriptor, temporary_path_value = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_path_value)
    try:
        shutil.copy2(source, temporary_path)
        os.replace(temporary_path, destination)
    finally:
        try:
            temporary_path.unlink()
        except OSError:
            pass

    return destination


def _remove_internal_avatar_files(
        persona_id: str | None,
        keep_path: Path | None = None,
) -> None:
    """删除指定用户人设的内部头像，可保留当前正在使用的文件。"""
    try:
        validated_persona_id = _get_internal_avatar_path(
            persona_id,
            ".png",
        ).stem
    except ValueError:
        return

    avatar_directory = AVATAR_PATH.resolve()
    try:
        candidates = list(avatar_directory.iterdir())
    except OSError:
        return

    resolved_keep_path = keep_path.resolve() if keep_path is not None else None
    for candidate in candidates:
        if (
                candidate.stem != validated_persona_id
                or candidate.suffix.lower() not in AVATAR_SUFFIXES
        ):
            continue

        try:
            if (
                    resolved_keep_path is not None
                    and candidate.resolve() == resolved_keep_path
            ):
                continue
            candidate.unlink()
        except OSError:
            pass


class UserPersonaDetailView(QWidget):
    """
    用户人设详情编辑视图
    """
    DEFAULT_USER_NOTICE = "选择此人设时，AI 不会得知任何关于用户身份和人设的信息。"
    DEFAULT_USER_DESCRIPTION = "无法为该人设填写内容，请新增人设。"
    ROLE_NOTICE = "开启后，对话将使用所选角色的人设，无法手动编辑信息。"
    DEFAULT_DESCRIPTION = "输入你自己的详细信息... (例如：性格、背景、说话方式等)"

    character_data_changed = pyqtSignal(CharacterAttributes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_character = None
        self.is_loading = False
        
        self.init_ui()

    def init_ui(self):
        self.v_layout = QVBoxLayout(self)
        self.v_layout.setContentsMargins(30, 30, 30, 30)
        self.v_layout.setSpacing(20)

        # 1. Header (Avatar + Name)
        self.header_layout = QHBoxLayout()
        self.avatar_widget = AvatarWidget(self)
        self.avatar_widget.setRadius(40)
        self.avatar_widget.setFixedSize(80, 80)
        
        self.header_info_layout = QVBoxLayout()
        self.header_info_layout.setSpacing(5)
        self.name_edit = LineEdit(self)
        self.name_edit.setPlaceholderText(self.tr("角色该如何称呼你？"))
        self.name_edit.textChanged.connect(self.on_name_changed)
        self.name_edit.setFixedWidth(200)
        
        self.avatar_btn = PushButton(self.tr("更改头像"), self)
        self.avatar_btn.setFixedWidth(100)
        self.avatar_btn.setToolTip(self.tr("头像只用于个性装扮，不会影响对话"))
        self.avatar_btn.installEventFilter(ToolTipFilter(self.avatar_btn))
        self.avatar_btn.clicked.connect(self.change_avatar)
        
        self.header_info_layout.addWidget(self.name_edit)
        self.header_info_layout.addWidget(self.avatar_btn)
        
        self.header_layout.addWidget(self.avatar_widget)
        self.header_layout.addSpacing(20)
        self.header_layout.addLayout(self.header_info_layout)
        self.header_layout.addStretch(1)
        
        self.v_layout.addLayout(self.header_layout)
        
        # 2. "Play as existing character" Card
        self.role_card = CardWidget(self)
        self.role_layout = QVBoxLayout(self.role_card)
        self.role_layout.setContentsMargins(16, 16, 16, 16)
        
        self.role_header_layout = QHBoxLayout()
        self.role_icon = IconWidget(FluentIcon.PEOPLE, self)
        self.role_icon.setFixedSize(18, 18)
        self.role_title = StrongBodyLabel("扮演已有角色", self)
        self.role_switch = SwitchButton(self)
        self.role_switch.setOnText("开启")
        self.role_switch.setOffText("关闭")
        self.role_switch.checkedChanged.connect(self.on_role_switch_changed)
        
        self.role_header_layout.addWidget(self.role_icon)
        self.role_header_layout.addSpacing(10)
        self.role_header_layout.addWidget(self.role_title)
        self.role_header_layout.addStretch(1)
        self.role_header_layout.addWidget(self.role_switch)
        
        self.role_combo = ComboBox(self)
        self.role_combo.setPlaceholderText("选择一个角色...")
        self.role_combo.currentIndexChanged.connect(self.on_existing_character_selected)
        
        self.role_notice = BodyLabel(self.ROLE_NOTICE, self)
        self.role_notice.setStyleSheet("color: #808080; font-size: 12px;")
        self.role_notice.setWordWrap(True)
        
        self.role_layout.addLayout(self.role_header_layout)
        self.role_layout.addSpacing(10)
        self.role_layout.addWidget(self.role_combo)
        self.role_layout.addWidget(self.role_notice)
        
        self.v_layout.addWidget(self.role_card)
        
        # 3. Description
        self.desc_label = StrongBodyLabel(self.tr("人设描述"), self)
        self.desc_edit = TextEdit(self)
        self.desc_edit.setPlaceholderText(self.tr(self.DEFAULT_DESCRIPTION))
        self.desc_edit.textChanged.connect(self.on_desc_changed)
        self.desc_edit.setFixedHeight(350)

        self.v_layout.addWidget(self.desc_label)
        self.v_layout.addWidget(self.desc_edit)
        
        self.v_layout.addStretch(1)

    def set_character(self, character: CharacterAttributes):
        self.current_character = character
        self.update_view()

    def update_view(self):
        if not self.current_character:
            return
            
        self.is_loading = True
        
        # Basic Info
        self.name_edit.setText(self.current_character.effective_character_name)
        self.desc_edit.setText(self.current_character.effective_character_description)
        
        effective_icon_path = self.current_character.effective_icon_path
        if effective_icon_path and os.path.exists(effective_icon_path):
            self.avatar_widget.setImage(effective_icon_path)
        else:
            self.avatar_widget.setImage(MyFluentIcon.USER.path())
            
        # Role Switch
        # Populate combo first
        self.populate_role_combo()

        if self.current_character.is_default_user:
            self.role_switch.setChecked(False)
            self.role_switch.setEnabled(False)
            self.role_combo.setVisible(False)
            self.role_notice.setText(self.DEFAULT_USER_NOTICE)
            self.role_notice.setVisible(True)
            self.set_fields_editable(False)
            self.desc_edit.setPlaceholderText(self.DEFAULT_USER_DESCRIPTION)
            self.is_loading = False
            return
        else:
            self.desc_edit.setPlaceholderText(self.DEFAULT_DESCRIPTION)

        self.role_switch.setEnabled(True)
        self.role_notice.setText(self.ROLE_NOTICE)
        
        if self.current_character.user_as_character:
            self.role_switch.setChecked(True)
            self.role_combo.setVisible(True)
            self.role_notice.setVisible(True)
            
            # Find index in combo
            index = -1
            for i in range(self.role_combo.count()):
                if self.role_combo.itemData(i).character_name == self.current_character.user_as_character.character_name:
                    index = i
                    break
            if index != -1:
                self.role_combo.setCurrentIndex(index)
                
            self.set_fields_editable(False)
        else:
            self.role_switch.setChecked(False)
            self.role_combo.setVisible(False)
            self.role_notice.setVisible(False)
            self.set_fields_editable(True)
            
        self.is_loading = False

    def populate_role_combo(self):
        self.role_combo.clear()
        # 访问角色管理器单例
        cm = GetCharacterAttributes()
        for char in cm.character_class_list:
            self.role_combo.addItem(char.character_name, userData=char)

    def set_fields_editable(self, editable):
        self.name_edit.setReadOnly(not editable)
        self.desc_edit.setReadOnly(not editable)
        self.avatar_btn.setEnabled(editable)
        self.name_edit.setEnabled(editable) 

    def on_name_changed(self, text):
        if self.is_loading or not self.current_character or self.current_character.is_default_user:
            return

        self.current_character.character_name = text
        self.save_data()
        self.character_data_changed.emit(self.current_character)

    def on_desc_changed(self):
        if self.is_loading or not self.current_character or self.current_character.is_default_user:
            return

        self.current_character.character_description = self.desc_edit.toPlainText()
        self.save_data()

    def change_avatar(self):
        if not self.current_character or self.current_character.is_default_user:
            return
            
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择头像", "", "Images (*.png *.jpg *.jpeg *.bmp)"
        )
        if file_path:
            try:
                local_path = _copy_avatar_to_internal_directory(
                    file_path,
                    self.current_character.persona_id,
                )
                self.current_character.icon_path = str(local_path)
            except (IndexError, OSError, AttributeError, ValueError):
                InfoBar.warning("添加头像失败", "未能成功复制选择的头像到内部文件夹", parent=self)
            else:
                _remove_internal_avatar_files(
                    self.current_character.persona_id,
                    keep_path=local_path,
                )
                self.avatar_widget.setImage(str(local_path))
                self.save_data()
                self.character_data_changed.emit(self.current_character)

    def on_role_switch_changed(self, checked):
        if self.is_loading or not self.current_character or self.current_character.is_default_user:
            return
            
        self.role_combo.setVisible(checked)
        self.role_notice.setVisible(checked)
        self.set_fields_editable(not checked)
        
        if checked:
            self.on_existing_character_selected(self.role_combo.currentIndex())
        else:
            self.current_character.user_as_character = None
            self.update_view()
            self.save_data()
            self.character_data_changed.emit(self.current_character)

    def on_existing_character_selected(self, index):
        if (
                self.is_loading
                or not self.current_character
                or self.current_character.is_default_user
                or not self.role_switch.isChecked()
        ):
            return
            
        target_char = self.role_combo.itemData(index)
        if target_char:
            self.current_character.user_as_character = target_char
            self.update_view()
            self.save_data()
            self.character_data_changed.emit(self.current_character)

    def save_data(self) -> None:
        """保存用户人设数据。"""
        GetCharacterAttributes().save_data()


class SystemCharacterDetailView(QWidget):
    """
    系统角色详情视图（只读属性 + 可编辑名称/描述）
    """
    character_data_changed = pyqtSignal(CharacterAttributes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_character = None
        self.is_loading = False
        self.init_ui()

    def init_ui(self):
        self.v_layout = QVBoxLayout(self)
        self.v_layout.setContentsMargins(30, 30, 30, 30)
        self.v_layout.setSpacing(20)

        # 1. Header
        self.header_layout = QHBoxLayout()
        self.avatar_widget = AvatarWidget(self)
        self.avatar_widget.setRadius(40)
        self.avatar_widget.setFixedSize(80, 80)
        
        self.header_info_layout = QVBoxLayout()
        self.header_info_layout.setSpacing(5)
        
        self.name_label = StrongBodyLabel("角色名称", self)
        self.name_edit = LineEdit(self)
        self.name_edit.setPlaceholderText("角色名称")
        self.name_edit.textChanged.connect(self.on_name_changed)
        self.name_edit.setFixedWidth(200)
        
        self.header_info_layout.addWidget(self.name_label)
        self.header_info_layout.addWidget(self.name_edit)
        
        self.header_layout.addWidget(self.avatar_widget)
        self.header_layout.addSpacing(20)
        self.header_layout.addLayout(self.header_info_layout)
        self.header_layout.addStretch(1)
        
        self.v_layout.addLayout(self.header_layout)

        # 2. Description
        self.desc_label = StrongBodyLabel("角色描述 (Prompt)", self)
        self.desc_edit = TextEdit(self)
        self.desc_edit.setPlaceholderText("角色描述...")
        self.desc_edit.textChanged.connect(self.on_desc_changed)
        self.desc_edit.setFixedHeight(200)
        
        self.v_layout.addWidget(self.desc_label)
        self.v_layout.addWidget(self.desc_edit)

        # 3. Read-only Properties (Technical Specs)
        self.specs_group = SettingCardGroup("技术参数", self)
        
        self.live2d_card = PushSettingCard(
            "查看", FluentIcon.GAME, "Live2D 模型", "未加载", self.specs_group
        )
        self.gpt_card = PushSettingCard(
            "查看", FluentIcon.CHAT, "GPT 模型", "未加载", self.specs_group
        )
        self.sovits_card = PushSettingCard(
            "查看", FluentIcon.MUSIC, "SoVITS 模型", "未加载", self.specs_group
        )
        self.ref_audio_card = PushSettingCard(
            "查看", FluentIcon.MICROPHONE, "参考音频", "未加载", self.specs_group
        )

        self.live2d_card.clicked.connect(lambda: self.show_file(self.live2d_card.toolTip()))
        self.gpt_card.clicked.connect(lambda: self.show_file(self.gpt_card.toolTip()))
        self.sovits_card.clicked.connect(lambda: self.show_file(self.sovits_card.toolTip()))
        self.ref_audio_card.clicked.connect(lambda: self.show_file(self.ref_audio_card.toolTip()))
        
        self.specs_group.addSettingCard(self.live2d_card)
        self.specs_group.addSettingCard(self.gpt_card)
        self.specs_group.addSettingCard(self.sovits_card)
        self.specs_group.addSettingCard(self.ref_audio_card)
        
        self.v_layout.addWidget(self.specs_group)
        self.v_layout.addStretch(1)

    @staticmethod
    def show_file(file_path: str | None) -> None:
        """在系统文件管理器中定位文件，无法选中时至少打开其所在目录。"""
        if not file_path:
            return

        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            return

        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
                return

            if sys.platform == "win32":
                subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])
                return
        except OSError:
            pass

        directory = path if path.is_dir() else path.parent
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            pass

    def set_character(self, character: CharacterAttributes):
        self.current_character = character
        self.update_view()

    def update_view(self):
        if not self.current_character:
            return

        self.is_loading = True
        
        self.name_edit.setText(self.current_character.character_name)
        self.desc_edit.setText(self.current_character.character_description)
        
        if self.current_character.icon_path and os.path.exists(self.current_character.icon_path):
            self.avatar_widget.setImage(self.current_character.icon_path)
        else:
            self.avatar_widget.setImage(MyFluentIcon.USER.path())
            
        # Update specs
        self.live2d_card.setContent(os.path.basename(self.current_character.live2d_json) if self.current_character.live2d_json else "无")
        self.live2d_card.setToolTip(self.current_character.live2d_json)
        
        self.gpt_card.setContent(os.path.basename(self.current_character.GPT_model_path) if self.current_character.GPT_model_path else "无")
        self.gpt_card.setToolTip(self.current_character.GPT_model_path)
        
        self.sovits_card.setContent(os.path.basename(self.current_character.sovits_model_path) if self.current_character.sovits_model_path else "无")
        self.sovits_card.setToolTip(self.current_character.sovits_model_path)
        
        self.ref_audio_card.setContent(os.path.basename(self.current_character.gptsovits_ref_audio) if self.current_character.gptsovits_ref_audio else "无")
        self.ref_audio_card.setToolTip(self.current_character.gptsovits_ref_audio)
        
        self.is_loading = False

    def on_name_changed(self, text):
        if self.is_loading or not self.current_character:
            return
        
        self.current_character.character_name = text
        self.save_to_file("name.txt", text)
        
        self.character_data_changed.emit(self.current_character)

    def on_desc_changed(self):
        if self.is_loading or not self.current_character:
            return
            
        text = self.desc_edit.toPlainText()
        self.current_character.character_description = text
        self.save_to_file("character_description.txt", text)

    def save_to_file(self, filename, content):
        if not self.current_character or not self.current_character.character_folder_name:
            return
            
        # Construct path: live2d_related/{folder_name}/{filename}
        # Assuming we are running from project root
        folder_path = os.path.join("live2d_related", self.current_character.character_folder_name)
        if not os.path.exists(folder_path):
            # Try ../live2d_related if we are in a subdir (though usually CWD is root)
            folder_path = os.path.join("../live2d_related", self.current_character.character_folder_name)
            
        if os.path.exists(folder_path):
            file_path = os.path.join(folder_path, filename)
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            except Exception as e:
                print(f"Error saving {filename}: {e}")


class CharacterArea(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CharacterArea")
        self.character_manager = GetCharacterAttributes()
        
        self.init_ui()
        # Default to User mode
        self.segment.setCurrentItem("user")
        
    def init_ui(self):
        self.h_layout = QHBoxLayout(self)
        self.h_layout.setContentsMargins(0, 0, 0, 0)
        self.h_layout.setSpacing(0)
        
        # --- Left Sidebar ---
        self.left_frame = QFrame(self)
        self.left_frame.setFixedWidth(240)
        self.left_frame.setStyleSheet("QFrame { background-color: transparent; border-right: 1px solid rgba(0, 0, 0, 0.1); }")
        
        self.left_layout = QVBoxLayout(self.left_frame)
        self.left_layout.setContentsMargins(10, 20, 10, 20)
        self.left_layout.setSpacing(10)
        
        # Segment Control
        self.segment = SegmentedWidget(self)
        self.segment.addItem("user", "用户人设")
        self.segment.addItem("system", "系统角色")
        self.segment.currentItemChanged.connect(self.on_segment_changed)
        self.left_layout.addWidget(self.segment)
        
        self.title_label = SubtitleLabel("列表", self)
        self.left_layout.addWidget(self.title_label)
        
        self.character_list_widget = ListWidget(self)
        self.character_list_widget.itemClicked.connect(self.on_character_selected)
        self.left_layout.addWidget(self.character_list_widget, stretch=1)
        
        # Buttons (Add/Delete) - only for User mode
        self.button_layout = QHBoxLayout()
        self.add_button = TransparentToolButton(FluentIcon.ADD, self)
        self.add_button.setToolTip("添加新人设")
        self.add_button.clicked.connect(self.add_character)
        
        self.delete_button = TransparentToolButton(FluentIcon.DELETE, self)
        self.delete_button.setToolTip("删除当前人设")
        self.delete_button.clicked.connect(self.delete_character)
        
        self.button_layout.addWidget(self.add_button)
        self.button_layout.addWidget(self.delete_button)
        self.button_layout.addStretch(1)

        self.button_container = QWidget()
        self.button_container.setLayout(self.button_layout)

        self.left_layout.addWidget(self.button_container, alignment=Qt.AlignmentFlag.AlignBottom)
        

        # --- Right Content Area ---
        self.right_scroll = TransparentScrollArea(self)
        self.right_widget = QWidget()
        self.right_widget.setStyleSheet("QWidget { background-color: transparent; }")
        # We use a StackedWidget to switch between User and System views
        self.stacked_layout = QVBoxLayout(self.right_widget)
        self.stacked_layout.setContentsMargins(0, 0, 0, 0)
        
        self.stack = QStackedWidget(self)
        
        self.user_view = UserPersonaDetailView(self)
        self.system_view = SystemCharacterDetailView(self)

        # 两个视图修改角色时，都会触发回调
        self.user_view.character_data_changed.connect(self.on_character_data_changed)
        self.system_view.character_data_changed.connect(self.on_character_data_changed)
        
        self.stack.addWidget(self.user_view)
        self.stack.addWidget(self.system_view)
        
        self.stacked_layout.addWidget(self.stack)
        
        self.right_scroll.setWidget(self.right_widget)
        self.right_scroll.setWidgetResizable(True)
        
        self.h_layout.addWidget(self.left_frame)
        self.h_layout.addWidget(self.right_scroll)

    def on_segment_changed(self, key):
        if key == "user":
            self.stack.setCurrentWidget(self.user_view)
            self.button_container.setVisible(True)
            self.title_label.setText("用户人设列表")
        else:
            self.stack.setCurrentWidget(self.system_view)
            self.button_container.setVisible(False)
            self.title_label.setText("系统角色列表")

        self.load_list_data(key)

    def load_list_data(self, mode):
        self.character_list_widget.clear()
        
        if mode == "user":
            data_source = self.character_manager.user_characters
        else:
            data_source = self.character_manager.character_class_list
            
        for char in data_source:
            display_name = "无用户人设" if char.is_default_user else char.effective_character_name
            item = QListWidgetItem(display_name)
            effective_icon_path = char.effective_icon_path
            if effective_icon_path and os.path.exists(effective_icon_path):
                item.setIcon(QIcon(effective_icon_path))
            else:
                item.setIcon(MyFluentIcon.USER.icon())
            # Store the character object in the item for easy access
            item.setData(Qt.ItemDataRole.UserRole, char)
            self.character_list_widget.addItem(item)
            
        if self.character_list_widget.count() > 0:
            self.character_list_widget.setCurrentRow(0)
            self.on_character_selected(self.character_list_widget.item(0))
        else:
            # Disable right side if no items
            pass

    def on_character_selected(self, item):
        if not item:
            return
            
        char = item.data(Qt.ItemDataRole.UserRole)
        if not char:
            return
        
        if self.stack.currentWidget() == self.user_view:
            self.delete_button.setEnabled(not char.is_default_user)
            self.user_view.set_character(char)
        else:
            self.system_view.set_character(char)

    def on_character_data_changed(self, char):
        # Callback from child views when name/icon changes
        # Find the item in list and update it
        for i in range(self.character_list_widget.count()):
            item = self.character_list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == char:
                item.setText("无用户人设" if char.is_default_user else char.effective_character_name)
                effective_icon_path = char.effective_icon_path
                if effective_icon_path and os.path.exists(effective_icon_path):
                    item.setIcon(QIcon(effective_icon_path))
                else:
                    item.setIcon(MyFluentIcon.USER.icon())
                break

    def add_character(self):
        new_char = CharacterAttributes.create_user(name="New User", description="")
        self.character_manager.user_characters.append(new_char)
        self.character_manager.save_data()
        
        item = QListWidgetItem(new_char.character_name)
        item.setIcon(FluentIcon.PEOPLE.icon())
        item.setData(Qt.ItemDataRole.UserRole, new_char)
        self.character_list_widget.addItem(item)
        self.character_list_widget.setCurrentRow(self.character_list_widget.count() - 1)
        self.on_character_selected(item)

    def delete_character(self):
        row = self.character_list_widget.currentRow()
        if row < 0:
            return
            
        char_to_delete = self.character_list_widget.item(row).data(Qt.ItemDataRole.UserRole)
        if char_to_delete.is_default_user:
            InfoBar.warning(
                title="无法删除",
                content="内置的“无用户人设”用于保持普通用户对话模式，不能删除。",
                orient=Qt.Orientations.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2000,
                parent=self
            )
            return

        if char_to_delete in self.character_manager.user_characters:
            self.character_manager.user_characters.remove(char_to_delete)

        _remove_internal_avatar_files(char_to_delete.persona_id)
        self.character_list_widget.takeItem(row)
        self.character_manager.save_data()
        
        if self.character_list_widget.count() > 0:
            new_row = min(row, self.character_list_widget.count() - 1)
            self.character_list_widget.setCurrentRow(new_row)
            self.on_character_selected(self.character_list_widget.item(new_row))
