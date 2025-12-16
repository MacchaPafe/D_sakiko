import sys
import json
import os
import shutil
import glob
import time

from PyQt5.QtWidgets import (QApplication, QWidget, QRadioButton,
                             QVBoxLayout, QLabel, QButtonGroup, QHBoxLayout, QLineEdit, QPushButton, QListWidget,
                             QAbstractItemView, QSizePolicy, QFileDialog, QComboBox, QStackedWidget, QFormLayout,
                             QDialog, QDialogButtonBox)
from PyQt5.QtCore import Qt, QTimer


# 设置这个变量来缩短 litellm 的加载时间，禁止其请求网络
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import litellm


# 将当前文件夹加入 sys.path，强制搜索当前目录的模块（即使 os.getcwd() 不是当前目录）
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)


import character
from qconfig import d_sakiko_config, PROVIDER_FRIENDLY_NAME_MAP, FAMOUS_CHAT_PROVIDERS, OTHER_CHAT_PROVIDERS


class AdaptiveStackedWidget(QStackedWidget):
    """
    A QStackedWidget that automatically adjusts its size to fit the currently active widget.
    
    This solves the issue where QStackedWidget retains the size of the largest widget
    or doesn't shrink when switching to a smaller widget.
    """
    def __init__(self):
        super().__init__()
        # Update geometry when the current page changes
        self.currentChanged.connect(self.updateGeometry)

    def sizeHint(self):
        """Return the size hint of the currently active widget."""
        if self.currentWidget():
            return self.currentWidget().sizeHint()
        return super().sizeHint()

    def minimumSizeHint(self):
        """Return the minimum size hint of the currently active widget."""
        if self.currentWidget():
            return self.currentWidget().minimumSizeHint()
        return super().minimumSizeHint()


class MoreProvidersDialog(QDialog):
    """
    A dialog to select from a larger list of LLM providers.
    
    Features:
    - Searchable list of providers.
    - Returns the selected provider string.
    """
    def __init__(self, parent=None, providers=None):
        super().__init__(parent)
        self.setWindowTitle("选择更多 LLM 供应商")
        self.resize(400, 500)
        self.selected_provider = None
        self.providers = providers or []
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Search filter input
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("搜索供应商...")
        self.filter_input.textChanged.connect(self.filter_items)
        layout.addWidget(self.filter_input)

        # List of providers
        self.list_widget = QListWidget()
        self.list_widget.addItems(self.providers)
        layout.addWidget(self.list_widget)

        # Dialog buttons (OK/Cancel)
        buttons_layout = QHBoxLayout()
        accept = QPushButton("确定")
        reject = QPushButton("取消")
        accept.clicked.connect(self.accept_selection)
        reject.clicked.connect(self.reject)
        buttons_layout.addStretch(1)
        buttons_layout.addWidget(accept, alignment=Qt.AlignmentFlag.AlignHCenter, stretch=2)
        buttons_layout.addStretch(1)
        buttons_layout.addWidget(reject, alignment=Qt.AlignmentFlag.AlignHCenter, stretch=2)
        buttons_layout.addStretch(1)

        layout.addLayout(buttons_layout)

        # Apply styles to match the main window
        self.setStyleSheet("""
            QDialog { background-color: #E6F2FF; color: #7799CC; }
            QLineEdit { background-color: #FFFFFF; border: 2px solid #B3D1F2; border-radius: 9px; padding: 5px; font-weight: bold; }
            QListWidget { background-color: #FFFFFF; border: 3px solid #B3D1F2; border-radius: 9px; padding: 5px; color: #7799CC; outline: 0px; }
            QListWidget::item { height: 30px; padding-left: 10px; border-radius: 5px; margin-bottom: 2px; }
            QListWidget::item:hover { background-color: #E6F2FF; color: #7799CC; }
            QListWidget::item:selected { background-color: #7FB2EB; color: #FFFFFF; }
        """)

    def filter_items(self, text):
        """Filter the list items based on the search text."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setHidden(text.lower() not in item.text().lower())

    def accept_selection(self):
        """Handle OK button click."""
        selected_items = self.list_widget.selectedItems()
        if selected_items:
            self.selected_provider = selected_items[0].text()
            self.accept()
        else:
            # If nothing selected, treat as cancel or just do nothing
            # Here we choose to do nothing to let user select again
            pass


class conf_ui(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        self.setWindowTitle('数字小祥启动参数配置')
        #self.setGeometry(100, 100, 300, 200)

        layout = QVBoxLayout()

        label_api = QLabel('1.当前大模型API配置：')
        layout.addWidget(label_api)

        # LLM Provider Selection
        self.llm_provider_combobox = QComboBox()
        layout.addWidget(self.llm_provider_combobox)

        # Stacked Widget for different provider settings
        # Use AdaptiveStackedWidget to resize based on content
        self.llm_stack = AdaptiveStackedWidget()
        layout.addWidget(self.llm_stack)

        # Page 0: Up's DeepSeek API (No config needed)
        self.page_up_api = QWidget()
        self.page_up_api.setObjectName("page_up_api")
        layout_up = QVBoxLayout()
        up_hint_label = QLabel("使用 Up 主提供的 DeepSeek API，无需额外配置。")
        up_hint_label.setMinimumHeight(30)
        layout_up.addWidget(up_hint_label)
        self.page_up_api.setLayout(layout_up)
        self.llm_stack.addWidget(self.page_up_api)

        # Page 1: Custom API (URL, Model, Key)
        self.page_custom_api = QWidget()
        self.page_custom_api.setObjectName("page_custom_api")
        layout_custom = QFormLayout()
        self.custom_url_input = QLineEdit()
        self.custom_url_input.setMinimumWidth(300)
        self.custom_url_input.setPlaceholderText("https://api.your-llm-provider.com/v1")
        self.custom_model_input = QLineEdit()
        self.custom_model_input.setMinimumWidth(300)
        self.custom_model_input.setPlaceholderText("openai/gpt-5")
        self.custom_model_input.setToolTip("请输入完整的模型名称，例如 openai/gpt-5、gemini/gemini-2.5-pro 等。")
        
        # Custom API Key with Toggle
        self.custom_key_layout = QHBoxLayout()
        self.custom_key_input = QLineEdit()
        self.custom_key_input.setEchoMode(QLineEdit.Password)
        self.custom_key_input.setMinimumWidth(260)
        self.custom_key_toggle = QPushButton("👁")
        self.custom_key_toggle.setFixedWidth(30)
        self.custom_key_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.custom_key_toggle.clicked.connect(lambda: self.toggle_password(self.custom_key_input))
        self.custom_key_layout.addWidget(self.custom_key_input)
        self.custom_key_layout.addWidget(self.custom_key_toggle)
        
        layout_custom.addRow("API URL:", self.custom_url_input)
        layout_custom.addRow("模型名称:", self.custom_model_input)
        layout_custom.addRow("API Key:", self.custom_key_layout)
        self.page_custom_api.setLayout(layout_custom)
        self.llm_stack.addWidget(self.page_custom_api)

        # Page 2: Standard API (Model, Key)
        self.page_standard_api = QWidget()
        self.page_standard_api.setObjectName("page_standard_api")
        layout_standard = QFormLayout()
        self.standard_model_combo = QComboBox()
        self.standard_model_combo.setEditable(True) # Allow custom model names
        self.standard_model_combo.setMinimumWidth(300)
        self.standard_model_combo.setToolTip("点击下拉框最右侧可以从模型列表中选择。不要选择非文本输出类模型！")
        
        # Standard API Key with Toggle
        self.standard_key_layout = QHBoxLayout()
        self.standard_key_input = QLineEdit()
        self.standard_key_input.setEchoMode(QLineEdit.Password)
        self.standard_key_input.setMinimumWidth(260)
        self.standard_key_toggle = QPushButton("👁")
        self.standard_key_toggle.setFixedWidth(30)
        self.standard_key_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.standard_key_toggle.clicked.connect(lambda: self.toggle_password(self.standard_key_input))
        self.standard_key_layout.addWidget(self.standard_key_input)
        self.standard_key_layout.addWidget(self.standard_key_toggle)

        layout_standard.addRow("模型名称:", self.standard_model_combo)
        layout_standard.addRow("API Key:", self.standard_key_layout)
        self.page_standard_api.setLayout(layout_standard)
        self.llm_stack.addWidget(self.page_standard_api)

        label_2 = QLabel('2.退出程序后是否删除缓存音频：（鼠标悬浮查看说明）')
        label_2.setToolTip("删除历史生成音频可以节省硬盘空间，但如果没备份的话，点击历史消息就无法再播放对应历史音频！如果确定要删除，建议备份生成不错的那几句。")
        self.radio_2_1 = QRadioButton('不删除')
        self.radio_2_1.setToolTip("删除历史生成音频可以节省硬盘空间，但如果没备份的话，点击历史消息就无法再播放对应历史音频！如果确定要删除，建议备份生成不错的那几句。")
        self.radio_2_2 = QRadioButton('删除')
        self.radio_2_2.setToolTip("删除历史生成音频可以节省硬盘空间，但如果没备份的话，点击历史消息就无法再播放对应历史音频！如果确定要删除，建议备份生成不错的那几句。")
        self.btn_group_2= QButtonGroup()
        self.btn_group_2.addButton(self.radio_2_1)
        self.btn_group_2.addButton(self.radio_2_2)
        # 如果设置为不删除缓存音频，则选中“不删除”选项
        if not d_sakiko_config.delete_audio_cache_on_exit.value:
            self.radio_2_1.setChecked(True)
        else:
            self.radio_2_2.setChecked(True)

        radio_2_layout=QHBoxLayout()
        radio_2_layout.addWidget(self.radio_2_1)
        radio_2_layout.addWidget(self.radio_2_2)
        layout.addWidget(label_2)
        layout.addLayout(radio_2_layout)

        label_3=QLabel('3.是否启用fp16（半精度浮点）推理音频：（鼠标悬浮查看说明）')
        label_3.setToolTip("启用后可以加快推理速度，但会小幅损失一些音质。注意！gtx16系（不包括rtx20系）以及之前的显卡不要开启！")
        self.radio_3_1 = QRadioButton('不启用')
        self.radio_3_1.setToolTip("启用后可以加快推理速度，但会小幅损失一些音质。注意！gtx16系（不包括rtx20系）以及之前的显卡不要开启！")
        self.radio_3_2 = QRadioButton('启用')
        self.radio_3_2.setToolTip("启用后可以加快推理速度，但会小幅损失一些音质。注意！gtx16系（不包括rtx20系）以及之前的显卡不要开启！")
        self.btn_group_3= QButtonGroup()
        self.btn_group_3.addButton(self.radio_3_1)
        self.btn_group_3.addButton(self.radio_3_2)

        if d_sakiko_config.enable_fp32_inference.value:
            self.radio_3_1.setChecked(True)
        else:
            self.radio_3_2.setChecked(True)

        radio_3_layout=QHBoxLayout()
        radio_3_layout.addWidget(self.radio_3_1)
        radio_3_layout.addWidget(self.radio_3_2)
        layout.addWidget(label_3)
        layout.addLayout(radio_3_layout)

        label_4=QLabel('4.可设置GPT-SoVITS推理采样步数：（鼠标悬浮查看说明）')
        label_4.setToolTip("降低采样步数可降低生成时间，但生成质量也会降低；步数越高，音质越好，推理时间也会相应增加。建议根据自己的硬件性能和需求进行调整。默认是16。")    #共有四档，4、8、16、32
        
        self.sample_step_combobox = QComboBox()
        self.sample_step_combobox.addItems(['4', '8', '16', '32'])
        # 读取并且显示当前的采样步数设置
        current_step=str(d_sakiko_config.sovits_inference_sampling_steps.value)
        index=self.sample_step_combobox.findText(current_step)
        if index >=0:
            self.sample_step_combobox.setCurrentIndex(index)
        layout.addWidget(label_4)
        layout.addWidget(self.sample_step_combobox)

        label_5=QLabel('5.调整角色登场顺序：（拖拽调整位置）')
        characters=character.GetCharacterAttributes()
        chatacter_list=characters.character_class_list
        self.character_names=[char.character_name for char in chatacter_list]
        self.character_list_widget=QListWidget()
        self.character_list_widget.setDragDropMode(QAbstractItemView.InternalMove)
        self.character_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.character_list_widget.addItems(self.character_names)
        layout.addWidget(label_5)
        layout.addWidget(self.character_list_widget)

        fun_6_layout = QHBoxLayout()
        # 设置控件之间的间距，数字越小挨得越近
        fun_6_layout.setSpacing(10)
        label_6 = QLabel('6.可更改字体：')
        # 让标签也自适应大小，不抢空间
        label_6.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_fun_6 = QPushButton('选择字体文件')
        self.btn_fun_6.clicked.connect(self.user_select_font_file)
        # 【关键点1】设置按钮的大小策略为 Fixed
        # 意思就是：按钮的大小完全由它的内容（文字）决定，绝不拉伸
        self.btn_fun_6.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.label_6_info = QLabel('')
        fun_6_layout.addWidget(label_6)
        fun_6_layout.addWidget(self.btn_fun_6)
        fun_6_layout.addWidget(self.label_6_info)
        # 在最后添加一个弹簧
        # 这个弹簧会占据这一行所有剩下的空白区域，把前面三个控件挤到最左边
        fun_6_layout.addStretch(1)
        layout.addLayout(fun_6_layout)


        self.save_btn=QPushButton('保存配置')
        self.save_btn.clicked.connect(self.save_config)
        layout.addWidget(self.save_btn)
        self.save_success_label=QLabel('')
        self.save_success_label.setWordWrap(True)

        self.clear_success_label_timer = QTimer()
        self.clear_success_label_timer.timeout.connect(self.clear_save_success_label)
        self.clear_success_label_timer.setSingleShot(True)
        self.clear_success_label_timer.setInterval(3000)  # 3秒后触发

        self.exit_btn=QPushButton('关闭窗口')
        self.exit_btn.clicked.connect(self.close)
        layout.addWidget(self.exit_btn)

        layout.addWidget(self.save_success_label)

        self.setLayout(layout)
        self.setStyleSheet("""
                                    QWidget {
                                        background-color: #E6F2FF;
                                        color: #7799CC;
                                    }
                                    QLabel {                    
                                        font-weight: bold;
                                    }
                                    QTextBrowser{
                                        text-decoration: none;
                                        background-color: #FFFFFF;
                                        border: 3px solid #B3D1F2;
                                        border-radius:9px;
                                        padding: 5px;
                                    }

                                    QLineEdit {
                                        font-weight: bold;
                                        background-color: #FFFFFF;
                                        border: 2px solid #B3D1F2;
                                        border-radius: 9px;
                                        padding: 5px;
                                    }
                                    
                                    QRadioButton {
                                        font-weight: bold;
                                    }
                                    
                                    QPushButton {                
                                        font-weight: bold;
                                        background-color: #7FB2EB;
                                        color: #ffffff;
                                        border-radius: 6px;
                                        padding: 6px;
                                    }

                                    QPushButton:hover {
                                        background-color: #3FB2EB;
                                    }

                                    QScrollBar:vertical {
                                        border: none;
                                        background: #D0E2F0;
                                        width: 10px;
                                        margin: 0px 0px 0px 0px;
                                    }

                                    QScrollBar::handle:vertical {
                                        background: #B3D1F2;
                                        min-height: 20px;
                                        border-radius: 3px;
                                    }

                                    QSlider::groove:horizontal {
                                        /* 滑槽背景 */
                                        border: 1px solid #B3D1F2;  /* 使用边框色作为滑槽边框 */
                                        height: 8px;
                                        background: #D0E2F0;       /* 使用浅色背景 */
                                        margin: 2px 0;
                                        border-radius: 4px;
                                    }

                                    QSlider::handle:horizontal {
                                        /* 滑块手柄 */
                                        background: #7FB2EB;       /* 使用按钮的亮蓝色 */
                                        border: 1px solid #4F80E0;
                                        width: 16px;
                                        margin: -4px 0;            /* 垂直方向上的偏移，使手柄在滑槽上居中 */
                                        border-radius: 8px;        /* 使手柄成为圆形 */
                                    }

                                    QSlider::handle:horizontal:hover {
                                        /* 鼠标悬停时的手柄颜色 */
                                        background: #3FB2EB;       /* 使用按钮的 hover 亮色 */
                                        border: 1px solid #3F60D0;
                                    }

                                    QSlider::sub-page:horizontal {
                                        /* 进度条（已滑过部分） */
                                        background: #AACCFF;       /* 使用一个中间的蓝色，比滑槽背景深，比手柄浅 */
                                        border-radius: 4px;
                                        margin: 2px 0;
                                    }
                                    
                                    QListWidget {
                                        background-color: #FFFFFF;
                                        border: 3px solid #B3D1F2;  /* 3px 稍粗边框 */
                                        border-radius: 9px;         /* 9px 圆角 */
                                        padding: 5px;               /* 内边距，让文字不贴边 */
                                        outline: 0px;               /* 去除选中时的虚线框，更美观 */
                                        color: #7799CC;             /* 字体颜色 */
                                    }
                                
                                    /* 列表中的每一项 */
                                    QListWidget::item {
                                        height: 30px;               /* 给每一项固定的高度，方便拖拽 */
                                        padding-left: 10px;         /* 文字左侧留白 */
                                        border-radius: 5px;         /* 列表项内部也做小圆角 */
                                        margin-bottom: 2px;         /* 项与项之间留一点缝隙 */
                                    }
                                
                                    /* 鼠标悬停在项上时 */
                                    QListWidget::item:hover {
                                        background-color: #E6F2FF;  /* 非常浅的蓝色背景 */
                                    }
                                
                                    /* 选中某一项时 */
                                    QListWidget::item:selected {
                                        background-color: #7FB2EB;  /* 按钮同款深蓝色背景 */
                                        color: #FFFFFF;             /* 文字变白 */
                                    }
                                    
                                    /* 拖拽过程中的样式（可选） */
                                    QListWidget::item:selected:!active {
                                        background-color: #9FC5EE;  /* 当列表失去焦点但仍被选中时的颜色 */
                                    }

                                    /* QStackedWidget Style */
                                    QStackedWidget {
                                        border: 3px solid #B3D1F2;
                                        border-radius: 9px;
                                        background-color: #FFFFFF;
                                    }
                                    
                                    /* Make pages inside QStackedWidget transparent to show the white background */
                                    #page_up_api, #page_custom_api, #page_standard_api {
                                        background-color: transparent;
                                    }

                                    /* QComboBox Style */
                                    QComboBox {
                                        background-color: #FFFFFF;
                                        border: 2px solid #B3D1F2;
                                        border-radius: 9px;
                                        padding: 5px;
                                        font-weight: bold;
                                        color: #7799CC;
                                        text-align: center;
                                    }
                                    QComboBox:hover {
                                        border: 2px solid #7FB2EB;
                                    }
                                    QComboBox::drop-down {
                                        subcontrol-origin: padding;
                                        subcontrol-position: top right;
                                        width: 20px;
                                        border-left-width: 0px;
                                        border-top-right-radius: 9px;
                                        border-bottom-right-radius: 9px;
                                        text-align: center;
                                    }
                                    QComboBox QAbstractItemView {
                                        background-color: #FFFFFF;
                                        border: 2px solid #B3D1F2;
                                        border-radius: 9px;
                                        selection-background-color: #E6F2FF;
                                        selection-color: #7799CC;
                                        outline: none;
                                        color: #7799CC;
                                        text-align: center;
                                    }

                                    /* ScrollBar Styles */
                                    QScrollBar:vertical {
                                        border: none;
                                        background: #F0F6FF;
                                        width: 12px;
                                        margin: 0px;
                                        border-radius: 6px;
                                    }
                                    QScrollBar::handle:vertical {
                                        background: #B3D1F2;
                                        min-height: 20px;
                                        border-radius: 6px;
                                    }
                                    QScrollBar::handle:vertical:hover {
                                        background: #7FB2EB;
                                    }
                                    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                                        height: 0px;
                                    }
                                    QScrollBar:horizontal {
                                        border: none;
                                        background: #F0F6FF;
                                        height: 12px;
                                        margin: 0px;
                                        border-radius: 6px;
                                    }
                                    QScrollBar::handle:horizontal {
                                        background: #B3D1F2;
                                        min-width: 20px;
                                        border-radius: 6px;
                                    }
                                    QScrollBar::handle:horizontal:hover {
                                        background: #7FB2EB;
                                    }
                                    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                                        width: 0px;
                                    }

                                """)
        

        # Populate ComboBox
        self.populate_llm_combobox()
        
        self.load_config_to_ui()
        self.llm_provider_combobox.currentIndexChanged.connect(self.on_llm_provider_changed)

    def toggle_password(self, line_edit):
        if line_edit.echoMode() == QLineEdit.Password:
            line_edit.setEchoMode(QLineEdit.Normal)
        else:
            line_edit.setEchoMode(QLineEdit.Password)
    
    def clear_save_success_label(self):
        """
        清除“保存成功”这个提示标签的文字。
        """
        self.save_success_label.setText('')
    
    def show_save_status(self, message: str):
        """
        在点击保存按键时，显示保存状态信息，并在3秒后自动清除。
        """
        self.save_success_label.setText(message)
        self.clear_success_label_timer.start()  # 启动定时器，3秒后清除

    def update_model_list(self, provider):
        """
        Update the model list for the given provider using litellm.
        """
        self.standard_model_combo.blockSignals(True)
        self.standard_model_combo.clear()
        
        # Add current configured model first if it exists
        current_model = d_sakiko_config.llm_api_model.value.get(provider)
        if current_model:
            self.standard_model_combo.addItem(current_model)
            
        try:
            # Get valid models from litellm
            # Note: litellm.utils.get_valid_models() returns a list of all models
            all_models = litellm.utils.get_valid_models(custom_llm_provider=provider)
            
            # Simple filtering based on provider name
            # This is a heuristic as litellm doesn't strictly categorize by provider in this list
            provider_lower = provider.lower()
            filtered_models = []
            
            # Common prefixes/keywords for providers
            keywords = {
                "openai": ["gpt", "dall-e", "tts", "whisper"],
                "anthropic": ["claude"],
                "google": ["gemini", "palm"],
                "deepseek": ["deepseek"],
                "azure": ["azure"],
                "cohere": ["command"],
                "mistral": ["mistral", "mixtral"],
                "ollama": ["llama", "mistral", "gemma"],
                "groq": ["llama", "mixtral", "gemma"],
            }
            
            target_keywords = keywords.get(provider_lower, [provider_lower])
            
            for model in all_models:
                model_lower = model.lower()
                # Check if model matches any keyword for the provider
                if any(k in model_lower for k in target_keywords):
                    filtered_models.append(model)
            
            # Sort and add to combobox
            filtered_models.sort()
            for model in filtered_models:
                if model != current_model: # Avoid duplicate
                    self.standard_model_combo.addItem(model)
                    
        except Exception as e:
            print(f"Error fetching models for {provider}: {e}")
            
        self.standard_model_combo.blockSignals(False)

    def load_settings_for_provider(self, provider):
        """
        Load settings (API Key, Model, URL) for the specified provider from config.
        """
        if provider == "deepseek_up":
            return
            
        keys = d_sakiko_config.llm_api_key.value
        models = d_sakiko_config.llm_api_model.value
        
        if provider == "custom":
            self.custom_url_input.setText(d_sakiko_config.custom_llm_api_url.value)
            self.custom_model_input.setText(d_sakiko_config.custom_llm_api_model.value)
            self.custom_key_input.setText(keys.get("custom_llm_api_key", ""))
        else:
            # Standard provider
            # 1. Update model list
            self.update_model_list(provider)
            
            # 2. Set current model
            # If the provider in config matches the current one, use the configured model
            # Otherwise, we might want a default or the first one in the list
            if models.get(provider):
                current_model = models.get(provider)
                self.standard_model_combo.setCurrentText(current_model)
            
            # 3. Set API Key
            self.standard_key_input.setText(keys.get(provider, ""))

    def load_config_to_ui(self):
        """
        Load configuration from d_sakiko_config and update UI elements.
        """
        use_up = d_sakiko_config.use_default_deepseek_api.value
        enable_custom = d_sakiko_config.enable_custom_llm_api_provider.value
        provider = d_sakiko_config.llm_api_provider.value
        
        target_data = "deepseek_up"
        if not use_up:
            if enable_custom:
                target_data = "custom"
            else:
                target_data = provider
                # Ensure provider exists in combobox
                index = self.llm_provider_combobox.findData(target_data)
                if index == -1:
                    custom_index = self.llm_provider_combobox.findData("custom")
                    self.llm_provider_combobox.insertItem(custom_index, target_data, target_data)
        
        index = self.llm_provider_combobox.findData(target_data)
        if index >= 0:
            # Block signals to prevent triggering on_llm_provider_changed automatically
            # We want to control the loading process
            self.llm_provider_combobox.blockSignals(True)
            self.llm_provider_combobox.setCurrentIndex(index)
            self.llm_provider_combobox.blockSignals(False)
            
            # Manually load settings and set stack page
            self.load_settings_for_provider(target_data)
            
            if target_data == "deepseek_up":
                self.llm_stack.setCurrentIndex(0)
            elif target_data == "custom":
                self.llm_stack.setCurrentIndex(1)
            else:
                self.llm_stack.setCurrentIndex(2)

    def populate_llm_combobox(self):
        """
        Populate the LLM provider ComboBox with default options.
        
        Options include:
        1. Up's DeepSeek API (Default)
        2. Famous Providers (OpenAI, Google, etc.) from FAMOUS_CHAT_PROVIDERS
        3. Custom API
        4. "More..." option to open the full provider list
        """
        self.llm_provider_combobox.clear()
        self.llm_provider_combobox.addItem("Up 的 DeepSeek API", "deepseek_up")
        
        # Add famous providers with friendly names
        for provider in FAMOUS_CHAT_PROVIDERS:
            friendly_name = PROVIDER_FRIENDLY_NAME_MAP.get(provider, provider)
            self.llm_provider_combobox.addItem(friendly_name, provider)
            
        self.llm_provider_combobox.addItem("自定义 API（与 OpenAI 兼容的任意网站）", "custom")
        self.llm_provider_combobox.addItem("更多...", "more")

    def on_llm_provider_changed(self, index):
        data = self.llm_provider_combobox.itemData(index)
        
        # Handle "More..." selection
        if data == "more":
            # Block signals to prevent recursive calls when we modify the combobox
            self.llm_provider_combobox.blockSignals(True)
            # 弹出窗口来允许用户选择更多的提供商
            dialog = MoreProvidersDialog(self, sorted(OTHER_CHAT_PROVIDERS))
            if dialog.exec_() == QDialog.Accepted and dialog.selected_provider:
                provider = dialog.selected_provider
                
                # Check if the provider is already in the list
                existing_index = self.llm_provider_combobox.findData(provider)
                
                if existing_index == -1:
                    # Insert the new provider before "Custom" (which is usually near the end)
                    # Current order: [Up, Famous..., Custom, More]
                    custom_index = self.llm_provider_combobox.findData("custom")
                    if custom_index == -1:
                        # Fallback if custom is missing for some reason
                        custom_index = self.llm_provider_combobox.count() - 1
                    
                    self.llm_provider_combobox.insertItem(custom_index, provider, provider)
                    self.llm_provider_combobox.setCurrentIndex(custom_index)
                else:
                    # If already exists, just select it
                    self.llm_provider_combobox.setCurrentIndex(existing_index)
            else:
                # If user cancelled, revert to the first item (Up's API) or handle gracefully
                # Here we revert to index 0 to avoid staying on "More..."
                self.llm_provider_combobox.setCurrentIndex(0)

            # Unblock signals
            self.llm_provider_combobox.blockSignals(False)
            
            # Manually trigger the change handler for the new selection
            # This ensures the correct page is shown in the stacked widget
            self.on_llm_provider_changed(self.llm_provider_combobox.currentIndex())
            return

        
        # Load settings for the new provider BEFORE saving
        # This ensures the UI fields are populated with the correct data for the selected provider
        self.load_settings_for_provider(data)

        # Standard logic for switching pages
        if data == "deepseek_up":
            self.llm_stack.setCurrentIndex(0)
        elif data == "custom":
            self.llm_stack.setCurrentIndex(1)
        else:
            self.llm_stack.setCurrentIndex(2)
            
    def save_ui_to_config(self) -> bool:
        """
        Save the current UI state to the configuration file. However, we don't save the config to disk.
        
        This method retrieves values from the active page in the StackedWidget
        and updates the d_sakiko_config object. It handles three cases:
        1. Up's DeepSeek API: Sets use_default_deepseek_api to True.
        2. Custom API: Sets enable_custom_llm_api_provider to True and saves URL/Model/Key.
        3. Standard Provider: Updates llm_api_provider, llm_api_model, and saves the Key.
        """
        # Save LLM Settings
        index = self.llm_provider_combobox.currentIndex()
        provider_data = self.llm_provider_combobox.itemData(index)
        
        if provider_data == "deepseek_up":
            # 只更新这个“是否使用 Up 的 DeepSeek API”选项   
            d_sakiko_config.use_default_deepseek_api.value = True
        elif provider_data == "custom":
            if not self.custom_url_input.text() or not self.custom_model_input.text() or not self.custom_key_input.text():
                # 如果有任何一个字段为空，则不保存配置，保持原样
                self.show_save_status('自定义 API 的 URL、模型名称和 API Key 都不能为空，配置未保存。')
                return False

            d_sakiko_config.use_default_deepseek_api.value = False
            # 启用自定义 OpenAI 兼容 API 提供商
            # 这会覆盖其他已经启用的标准提供商
            d_sakiko_config.enable_custom_llm_api_provider.value = True
            d_sakiko_config.custom_llm_api_url.value = self.custom_url_input.text()
            d_sakiko_config.custom_llm_api_model.value = self.custom_model_input.text()
            
            # Update key in the dictionary
            d_sakiko_config.custom_llm_api_key.value = self.custom_key_input.text()
        else:
            if not self.standard_key_input.text() or not provider_data:
                # 如果 API Key 为空，则不保存配置，保持原样
                self.show_save_status('API Key 和模型类型不能为空。配置未保存。')
                return False
            
            d_sakiko_config.use_default_deepseek_api.value = False
            d_sakiko_config.enable_custom_llm_api_provider.value = False
            # 存储选择的标准提供商
            d_sakiko_config.llm_api_provider.value = provider_data
            d_sakiko_config.llm_api_model.value[provider_data] = self.standard_model_combo.currentText()
            
            # Update key in the dictionary
            keys = d_sakiko_config.llm_api_key.value
            keys[provider_data] = self.standard_key_input.text()
            d_sakiko_config.llm_api_key.value = keys
        
        # 设置退出时是否删除缓存音频
        if self.radio_2_1.isChecked():
            d_sakiko_config.delete_audio_cache_on_exit.value = False
        else:
            d_sakiko_config.delete_audio_cache_on_exit.value = True
        
        # 设置是否启用fp16推理
        if self.radio_3_1.isChecked():
            d_sakiko_config.enable_fp32_inference.value = True
        else:
            d_sakiko_config.enable_fp32_inference.value = False
        
        data = self.sample_step_combobox.currentText()
        d_sakiko_config.sovits_inference_sampling_steps.value = int(data)

        # 存储角色顺序
        ordered_names=[]
        count = self.character_list_widget.count()
        for i in range(count):
            item = self.character_list_widget.item(i)
            ordered_names.append(item.text())
        order_data_to_save={
            "character_num": len(ordered_names),
            "character_names": ordered_names,
        }
        d_sakiko_config.character_order.value = order_data_to_save

        return True

    def save_config(self):
        """
        Save the current UI state to the config, and then save the config to disk.
        """
        if self.save_ui_to_config():
            d_sakiko_config.save()

            self.show_save_status("保存成功！大模型相关配置立刻生效，音频推理与角色顺序等配置在下次启动时应用")

    def user_select_font_file(self):
        file_path, file_type = QFileDialog.getOpenFileName(
            self,
            "选择字体文件（.ttf/.otf/.ttc）",
            "",
            "字体类型文件 (*.ttf *.otf *.ttc)"
        )
        if not file_path:
            self.label_6_info.setText('取消了选择')
            return

        try:
            # 1. 生成带时间戳的唯一新文件名
            timestamp = int(time.time())
            file_ext = os.path.splitext(file_path)[1].lower()
            new_filename = f"custom_font_{timestamp}{file_ext}"
            dest_path = os.path.join('../font/', new_filename)

            # 2. 先尝试清理旧文件（尽力而为，删不掉也不报错）
            # 查找所有名字是 custom_font_ 开头的文件
            old_files = glob.glob(os.path.join('../font/', 'custom_font_*'))
            for old_file in old_files:
                try:
                    os.remove(old_file)
                    print(f"已清理旧文件: {old_file}")
                except Exception:
                    # 关键点：如果旧文件被锁，直接跳过，不要抛出异常打断流程
                    print(f"旧文件被占用，本次跳过删除: {old_file}")

            # 3. 复制新文件 (因为名字是唯一的，绝对不会冲突)
            shutil.copy(file_path, dest_path)
            self.label_6_info.setText('成功应用字体')

        except Exception as e:
            self.label_6_info.setText('字体应用失败')
            print('错误信息：', e)




if __name__ == '__main__':
    import os

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    app = QApplication(sys.argv)
    win = conf_ui()

    win.show()
    sys.exit(app.exec_())