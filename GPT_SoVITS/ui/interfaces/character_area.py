# 此文件包含了“用户自身角色设定”的相关对话框和组件

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QListWidgetItem,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtGui import QIcon

from qfluentwidgets import (
    ListWidget, PushButton, LineEdit, TextEdit,
    StrongBodyLabel, SubtitleLabel, AvatarWidget,
    SwitchButton, ComboBox, CardWidget, IconWidget,
    FluentIcon, InfoBar, InfoBarPosition, TransparentToolButton,
    BodyLabel, ToolTipFilter, SegmentedWidget,
    SettingCardGroup, PushSettingCard, CaptionLabel,
    MessageBoxBase,
)

from ..components.fluent_icon import MyFluentIcon
from ..custom_widgets.transparent_scroll_area import TransparentScrollArea
from ..file_manager import show_file_in_manager

try:
    from character import CharacterAttributes, GetCharacterAttributes, find_default_l2d_json
except ImportError:
    from GPT_SoVITS.character import CharacterAttributes, GetCharacterAttributes, find_default_l2d_json

try:
    from character_creation import (
        CharacterCreationError,
        CharacterDiskRecord,
        create_character_resources,
        discover_complete_character_records,
        safe_character_id_from_name,
    )
    from live2d_support.model_importer import (
        Live2DModelImportError,
        import_live2d_model,
    )
except ImportError:
    from GPT_SoVITS.character_creation import (
        CharacterCreationError,
        CharacterDiskRecord,
        create_character_resources,
        discover_complete_character_records,
        safe_character_id_from_name,
    )
    from GPT_SoVITS.live2d_support.model_importer import (
        Live2DModelImportError,
        import_live2d_model,
    )


AVATAR_PATH = Path("../avatar")
AVATAR_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp"})


def _get_internal_avatar_path(persona_id: str | None, suffix: str) -> Path:
    """根据用户人设 ID 和图片扩展名生成内部头像路径。"""
    if (
            not persona_id
            or Path(persona_id).name != persona_id
            or persona_id in {".", ".."}
    ):
        raise ValueError("对话身份 ID 无效")

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


class CharacterCreationDialog(MessageBoxBase):
    """收集角色必要信息，并在确认时原子创建角色资源。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化角色创建表单。"""
        super().__init__(parent)
        self.created_record: CharacterDiskRecord | None = None
        self.avatar_source_path: str | None = None
        self._suggested_id = ""
        self.widget.setMinimumWidth(600)
        self.widget.setMinimumHeight(570)

        self.title_label = SubtitleLabel("创建角色", self)
        self.description_label = BodyLabel(
            "角色只需要名称、稳定 ID 和角色描述即可参与对话；Live2D、语音和头像都可以稍后添加。",
            self,
        )
        self.description_label.setWordWrap(True)

        self.name_edit = LineEdit(self)
        self.name_edit.setPlaceholderText("角色显示名称")
        self.id_edit = LineEdit(self)
        self.id_edit.setPlaceholderText("小写英文、数字、下划线或连字符")
        self.description_edit = TextEdit(self)
        self.description_edit.setPlaceholderText("请亲自填写角色设定；该内容会作为 system prompt。")
        self.description_edit.setMinimumHeight(220)
        self.avatar_edit = LineEdit(self)
        self.avatar_edit.setReadOnly(True)
        self.avatar_button = PushButton("选择头像（可选）", self)
        self.avatar_button.clicked.connect(self._select_avatar)

        self.name_label = StrongBodyLabel("角色名称", self)
        self.id_label = StrongBodyLabel("角色 ID", self)
        self.id_hint_label = CaptionLabel(
            "只能使用小写英文字母、数字、下划线和连字符；创建后作为稳定资源 ID。",
            self,
        )
        self.id_hint_label.setWordWrap(True)
        self.prompt_label = StrongBodyLabel("角色描述（Prompt）", self)
        self.prompt_hint_label = CaptionLabel(
            "必须由你亲自填写，内容会作为角色对话的 system prompt。",
            self,
        )
        self.prompt_hint_label.setWordWrap(True)
        self.avatar_label = StrongBodyLabel("角色头像", self)

        avatar_row = QHBoxLayout()
        avatar_row.setContentsMargins(0, 0, 0, 0)
        avatar_row.setSpacing(8)
        avatar_row.addWidget(self.avatar_edit, stretch=1)
        avatar_row.addWidget(self.avatar_button)

        form_layout = QGridLayout()
        form_layout.setContentsMargins(0, 4, 0, 0)
        form_layout.setHorizontalSpacing(16)
        form_layout.setVerticalSpacing(8)
        form_layout.addWidget(self.name_label, 0, 0, Qt.AlignTop)
        form_layout.addWidget(self.name_edit, 0, 1)
        form_layout.addWidget(self.id_label, 1, 0, Qt.AlignTop)
        form_layout.addWidget(self.id_edit, 1, 1)
        form_layout.addWidget(self.id_hint_label, 2, 1)
        form_layout.addWidget(self.prompt_label, 3, 0, Qt.AlignTop)
        form_layout.addWidget(self.description_edit, 3, 1)
        form_layout.addWidget(self.prompt_hint_label, 4, 1)
        form_layout.addWidget(self.avatar_label, 5, 0, Qt.AlignTop)
        form_layout.addLayout(avatar_row, 5, 1)

        self.restart_hint_label = CaptionLabel(
            "创建完成后会立即出现在此面板中，并在程序重启后进入对话角色列表。",
            self,
        )
        self.restart_hint_label.setWordWrap(True)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.description_label)
        self.viewLayout.addLayout(form_layout)
        self.viewLayout.addWidget(self.restart_hint_label)
        self.yesButton.setText("创建")
        self.cancelButton.setText("取消")

        self.name_edit.textChanged.connect(self._suggest_character_id)

    def _suggest_character_id(self, character_name: str) -> None:
        """在用户尚未填写 ID 时，从安全英文名称生成建议。"""
        if self.id_edit.text().strip() not in ("", self._suggested_id):
            return
        suggested_id = safe_character_id_from_name(character_name)
        if suggested_id:
            self._suggested_id = suggested_id
            self.id_edit.setText(suggested_id)

    def _select_avatar(self) -> None:
        """选择可选角色头像文件。"""
        selected_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择角色头像",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp)",
        )
        if selected_path:
            self.avatar_source_path = selected_path
            self.avatar_edit.setText(selected_path)

    def validate(self) -> bool:
        """校验并创建角色资源；失败时保持 Fluent 对话框打开。"""
        try:
            self.created_record = create_character_resources(
                character_name=self.name_edit.text(),
                character_folder_name=self.id_edit.text(),
                character_description=self.description_edit.toPlainText(),
                avatar_source_path=self.avatar_source_path,
            )
        except (CharacterCreationError, OSError, ValueError) as exc:
            InfoBar.warning(
                title="创建角色失败",
                content=str(exc),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3000,
                parent=self,
            )
            return False
        return True


def _character_from_disk_record(record: CharacterDiskRecord) -> CharacterAttributes:
    """把待启用磁盘角色转换为仅供角色面板展示的对象。"""
    character = CharacterAttributes()
    character.character_folder_name = record.character_folder_name
    character.character_name = record.character_name
    character.character_description = record.character_description
    character.icon_path = record.icon_path
    character.live2d_json = find_default_l2d_json(
        os.path.join(
            "../live2d_related",
            record.character_folder_name,
            "live2D_model",
        )
    )
    character.GPT_model_path = None
    character.sovits_model_path = None
    character.gptsovits_ref_audio = None
    character.gptsovits_ref_audio_text = None
    character.gptsovits_ref_audio_lan = None
    return character


class UserPersonaDetailView(QWidget):
    """
    用户人设详情编辑视图
    """
    DEFAULT_USER_NOTICE = "选择此身份时，AI 不会得知任何关于你的信息。"
    DEFAULT_USER_DESCRIPTION = "这是默认的人设，无法填写内容，请新增身份。"
    ROLE_NOTICE = "开启后，对话将使用所选角色的身份，无法手动编辑信息。"
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
        self.desc_label = StrongBodyLabel(self.tr("身份描述"), self)
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
        self.import_live2d_button = PushButton("添加 Live2D 模型", self)
        self.import_live2d_button.clicked.connect(self.import_live2d_model)
        self.v_layout.addWidget(self.import_live2d_button)
        self.v_layout.addStretch(1)

    @staticmethod
    def show_file(file_path: str | None) -> None:
        """在系统文件管理器中定位文件，无法选中时至少打开其所在目录。"""
        show_file_in_manager(file_path)

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
        self.live2d_card.setContent(
            os.path.basename(self.current_character.live2d_json)
            if self.current_character.live2d_json
            else "未配置"
        )
        self.live2d_card.setToolTip(self.current_character.live2d_json or "")
        self.import_live2d_button.setText(
            "导入更多 Live2D 模型"
            if self.current_character.live2d_json
            else "添加 Live2D 模型"
        )
        
        self.gpt_card.setContent(os.path.basename(self.current_character.GPT_model_path) if self.current_character.GPT_model_path else "无")
        self.gpt_card.setToolTip(self.current_character.GPT_model_path or "")
        
        self.sovits_card.setContent(os.path.basename(self.current_character.sovits_model_path) if self.current_character.sovits_model_path else "无")
        self.sovits_card.setToolTip(self.current_character.sovits_model_path or "")
        
        ref_audio_path = self.current_character.get_reference_audio_for_emotion("happiness")
        self.ref_audio_card.setContent(os.path.basename(ref_audio_path) if ref_audio_path else "无")
        self.ref_audio_card.setToolTip(ref_audio_path or "")
        
        self.is_loading = False

    def import_live2d_model(self) -> None:
        """为当前角色导入 Live2D 模型，首次导入自动成为默认模型。"""
        if self.current_character is None:
            return
        selected_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "导入 Live2D 模型",
            "",
            "Live2D Model JSON (*.model3.json *.model.json);;JSON Files (*.json)",
        )
        if not selected_path:
            return
        try:
            result = import_live2d_model(
                selected_path,
                self.current_character.character_folder_name,
            )
        except (Live2DModelImportError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        if self.current_character.live2d_json is None:
            self.current_character.live2d_json = result.model_json_path
        self.update_view()
        QMessageBox.information(self, "导入成功", f"已导入模型：{result.model_name}。")

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
        self.pending_characters = self._load_pending_characters()
        
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
        self.segment.addItem("user", "对话身份")
        self.segment.addItem("system", "角色")
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
        self.add_button.setToolTip("添加新身份")
        self.add_button.clicked.connect(self.add_character)
        
        self.delete_button = TransparentToolButton(FluentIcon.DELETE, self)
        self.delete_button.setToolTip("删除当前身份")
        self.delete_button.clicked.connect(self.delete_character)
        
        self.button_layout.addWidget(self.add_button)
        self.button_layout.addWidget(self.delete_button)
        self.button_layout.addStretch(1)

        self.button_container = QWidget()
        self.button_container.setLayout(self.button_layout)

        self.left_layout.addWidget(self.button_container, alignment=Qt.AlignmentFlag.AlignBottom)
        self.restart_notice = BodyLabel(
            "新建角色已写入磁盘，将在程序重启后进入对话角色列表。",
            self,
        )
        self.restart_notice.setWordWrap(True)
        self.restart_notice.setVisible(bool(self.pending_characters))
        self.left_layout.addWidget(self.restart_notice)
        

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
            self.add_button.setToolTip("添加新身份")
            self.delete_button.setVisible(True)
            self.title_label.setText("对话身份列表")
        else:
            self.stack.setCurrentWidget(self.system_view)
            self.button_container.setVisible(True)
            self.add_button.setToolTip("创建角色")
            self.delete_button.setVisible(False)
            self.title_label.setText("角色列表")

        self.load_list_data(key)

    def load_list_data(self, mode):
        self.character_list_widget.clear()
        
        if mode == "user":
            data_source = self.character_manager.user_characters
        else:
            data_source = [
                *self.character_manager.character_class_list,
                *self.pending_characters,
            ]

        pending_ids = {
            character.character_folder_name
            for character in self.pending_characters
        }
            
        for char in data_source:
            display_name = "无身份" if char.is_default_user else char.effective_character_name
            if mode != "user" and char.character_folder_name in pending_ids:
                display_name = f"{display_name}（重启后生效）"
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
                item.setText("无身份" if char.is_default_user else char.effective_character_name)
                effective_icon_path = char.effective_icon_path
                if effective_icon_path and os.path.exists(effective_icon_path):
                    item.setIcon(QIcon(effective_icon_path))
                else:
                    item.setIcon(MyFluentIcon.USER.icon())
                break

    def add_character(self) -> None:
        """按当前页签创建对话身份或待启用角色。"""
        if self.segment.currentRouteKey() == "system":
            dialog = CharacterCreationDialog(self)
            if dialog.exec_() != QDialog.Accepted:
                return
            self.pending_characters = self._load_pending_characters()
            self.restart_notice.setVisible(bool(self.pending_characters))
            self.load_list_data("system")
            if dialog.created_record is not None:
                for row in range(self.character_list_widget.count()):
                    item = self.character_list_widget.item(row)
                    character = item.data(Qt.ItemDataRole.UserRole)
                    if (
                            isinstance(character, CharacterAttributes)
                            and character.character_folder_name
                            == dialog.created_record.character_folder_name
                    ):
                        self.character_list_widget.setCurrentRow(row)
                        self.on_character_selected(item)
                        break
            InfoBar.success(
                title="角色已创建",
                content="角色资源已写入磁盘。重启程序后即可用它创建对话。",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500,
                parent=self,
            )
            return

        new_char = CharacterAttributes.create_user(name="New User", description="")
        self.character_manager.user_characters.append(new_char)
        self.character_manager.save_data()
        
        item = QListWidgetItem(new_char.character_name)
        item.setIcon(FluentIcon.PEOPLE.icon())
        item.setData(Qt.ItemDataRole.UserRole, new_char)
        self.character_list_widget.addItem(item)
        self.character_list_widget.setCurrentRow(self.character_list_widget.count() - 1)
        self.on_character_selected(item)

    def _load_pending_characters(self) -> list[CharacterAttributes]:
        """读取磁盘完整角色，并排除当前进程已经加载的角色。"""
        loaded_ids = {
            character.character_folder_name
            for character in self.character_manager.character_class_list
        }
        loaded_names = {
            character.character_name
            for character in self.character_manager.character_class_list
        }
        return [
            _character_from_disk_record(record)
            for record in discover_complete_character_records()
            if (
                record.character_folder_name not in loaded_ids
                and record.character_name not in loaded_names
            )
        ]

    def delete_character(self):
        row = self.character_list_widget.currentRow()
        if row < 0:
            return
            
        char_to_delete = self.character_list_widget.item(row).data(Qt.ItemDataRole.UserRole)
        if char_to_delete.is_default_user:
            InfoBar.warning(
                title="无法删除",
                content="内置的“无身份”用于保持普通用户对话模式，不能删除。",
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
