# 配置 LLM（大语言模型）API 相关的配置 UI
import contextlib
import os

# 设置这个变量来缩短 litellm 的加载时间，禁止其请求网络
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import litellm
from PyQt5.QtWidgets import QVBoxLayout, QStackedWidget, QWidget, QHBoxLayout, QDialog

from qconfig import d_sakiko_config, OTHER_CHAT_PROVIDERS, FAMOUS_CHAT_PROVIDERS, PROVIDER_FRIENDLY_NAME_MAP

with contextlib.redirect_stdout(None):
    from qfluentwidgets import ComboBox, BodyLabel, LineEdit, PasswordLineEdit, EditableComboBox, MessageBoxBase, \
    ListWidget, ToolTipFilter

from ..custom_widgets.transparent_scroll_area import TransparentScrollArea


class MoreProvidersDialog(MessageBoxBase):
    """
    A dialog to select from a larger list of LLM providers.

    Features:
    - Searchable list of providers.
    - Returns the selected provider string.
    """

    def __init__(self, parent=None, providers=None):
        super().__init__(parent)

        self.selected_provider = None
        self.providers = providers or []

        # Search filter input
        self.filter_input = LineEdit()
        self.filter_input.setPlaceholderText("搜索供应商...")
        self.filter_input.textChanged.connect(self.filter_items)
        self.viewLayout.addWidget(self.filter_input)

        # List of providers
        self.list_widget = ListWidget()
        self.list_widget.addItems(self.providers)
        self.viewLayout.addWidget(self.list_widget)

        # Dialog buttons (OK/Cancel)
        self.yesButton.setText(self.tr("确定"))
        self.cancelButton.setText(self.tr("取消"))
        self.yesButton.clicked.connect(self.accept_selection)
        self.cancelButton.clicked.connect(self.reject)

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


class LLMAPIArea(TransparentScrollArea):
    def __init__(self, parent):
        super().__init__(parent)

        self.v_box_layout = QVBoxLayout(self.view)

        # LLM Provider Selection
        self.llm_provider_combobox = ComboBox()
        self.v_box_layout.addWidget(self.llm_provider_combobox)

        # Stacked Widget for different provider settings
        # Use AdaptiveStackedWidget to resize based on content
        self.llm_stack = AdaptiveStackedWidget()
        self.v_box_layout.addWidget(self.llm_stack)

        # Page 0: Up's DeepSeek API (No config needed)

        # 用于放入 stacked widget 的空白 widget
        self.page_up_api = QWidget()
        self.page_up_api.setObjectName("page_up_api")
        # 当前分栏的 layout
        layout_up = QVBoxLayout()
        up_hint_label = BodyLabel(self.tr("使用 Up 主提供的 DeepSeek API，无需额外配置。"), self.page_up_api)
        up_hint_label.setMinimumHeight(30)
        layout_up.addWidget(up_hint_label)
        self.page_up_api.setLayout(layout_up)
        # 添加到 stacked widget
        self.llm_stack.addWidget(self.page_up_api)

        # Page 1: 自定义 API (包含自定义 Base URL, 模型, API Key)
        self.page_custom_api = QWidget()
        self.page_custom_api.setObjectName("page_custom_api")
        layout_custom = QVBoxLayout()
        layout_custom.setContentsMargins(0, 20, 0, 20)
        layout_custom.setSpacing(10)

        self.custom_url_input = LineEdit()
        self.custom_url_input.setMinimumWidth(300)
        self.custom_url_input.setPlaceholderText("https://api.your-llm-provider.com/v1")
        self.custom_model_input = LineEdit()
        self.custom_model_input.setMinimumWidth(300)
        self.custom_model_input.setPlaceholderText("openai/gpt-5")
        self.custom_model_input.setToolTip("请输入完整的模型名称，例如 openai/gpt-5、gemini/gemini-2.5-pro 等。")
        self.custom_model_input.installEventFilter(ToolTipFilter(self.custom_model_input))

        # Custom API Key Input
        self.custom_key_layout = QHBoxLayout()
        self.custom_key_input = PasswordLineEdit()
        self.custom_key_input.setMinimumWidth(260)
        self.custom_key_layout.addWidget(self.custom_key_input)

        # Row 1: URL
        h_layout_1 = QHBoxLayout()
        label_1 = BodyLabel(self.tr("API URL:"), self.page_custom_api)
        label_1.setFixedWidth(110)
        h_layout_1.addWidget(label_1)
        h_layout_1.addWidget(self.custom_url_input)
        layout_custom.addLayout(h_layout_1)

        # Row 2: Model
        h_layout_2 = QHBoxLayout()
        label_2 = BodyLabel(self.tr("模型名称:"), self.page_custom_api)
        label_2.setFixedWidth(110)
        h_layout_2.addWidget(label_2)
        h_layout_2.addWidget(self.custom_model_input)
        layout_custom.addLayout(h_layout_2)

        # Row 3: Key
        label_3 = BodyLabel(self.tr("API Key:"), self.page_custom_api)
        label_3.setFixedWidth(110)
        self.custom_key_layout.insertWidget(0, label_3)
        layout_custom.addLayout(self.custom_key_layout)

        layout_custom.addStretch(1)
        self.page_custom_api.setLayout(layout_custom)
        self.llm_stack.addWidget(self.page_custom_api)

        # Page 2: Standard API (Model, Key)
        self.page_standard_api = QWidget()
        self.page_standard_api.setObjectName("page_standard_api")
        layout_standard = QVBoxLayout()
        layout_standard.setContentsMargins(0, 20, 0, 20)
        layout_standard.setSpacing(10)

        self.standard_model_combo = EditableComboBox()
        self.standard_model_combo.setMinimumWidth(300)
        self.standard_model_combo.setToolTip("点击下拉框最右侧可以从模型列表中选择。不要选择非文本输出类模型！")
        self.standard_model_combo.installEventFilter(ToolTipFilter(self.standard_model_combo))

        # Standard API Key with Toggle
        self.standard_key_layout = QHBoxLayout()
        self.standard_key_input = PasswordLineEdit()
        self.standard_key_input.setMinimumWidth(260)
        self.standard_key_layout.addWidget(self.standard_key_input)

        # Row 1: Model
        h_layout_s1 = QHBoxLayout()
        label_s1 = BodyLabel(self.tr("模型名称:"), self.page_standard_api)
        label_s1.setFixedWidth(110)
        h_layout_s1.addWidget(label_s1)
        h_layout_s1.addWidget(self.standard_model_combo)
        layout_standard.addLayout(h_layout_s1)

        # Row 2: Key
        label_s2 = BodyLabel(self.tr("API Key:"), self.page_standard_api)
        label_s2.setFixedWidth(110)
        self.standard_key_layout.insertWidget(0, label_s2)
        layout_standard.addLayout(self.standard_key_layout)

        layout_standard.addStretch(1)
        self.page_standard_api.setLayout(layout_standard)
        self.llm_stack.addWidget(self.page_standard_api)

        # 加载模型选择框的内容
        self.populate_llm_combobox()
        self.llm_provider_combobox.currentIndexChanged.connect(self.on_llm_provider_changed)

        # 加载初始内容
        self.load_config_to_ui()

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
                if model != current_model:  # Avoid duplicate
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
            self.custom_key_input.setText(d_sakiko_config.custom_llm_api_key.value)
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
        self.llm_provider_combobox.addItem("Up 的 DeepSeek API", userData="deepseek_up")

        # Add famous providers with friendly names
        for provider in FAMOUS_CHAT_PROVIDERS:
            friendly_name = PROVIDER_FRIENDLY_NAME_MAP.get(provider, provider)
            self.llm_provider_combobox.addItem(friendly_name, userData=provider)

        self.llm_provider_combobox.addItem("自定义 API（与 OpenAI 兼容的任意网站）", userData="custom")
        self.llm_provider_combobox.addItem("更多...", userData="more")

    def on_llm_provider_changed(self, index):
        data = self.llm_provider_combobox.itemData(index)

        # Handle "More..." selection
        if data == "more":
            # Block signals to prevent recursive calls when we modify the combobox
            self.llm_provider_combobox.blockSignals(True)
            # 弹出窗口来允许用户选择更多的提供商
            dialog = MoreProvidersDialog(self.window(), sorted(OTHER_CHAT_PROVIDERS))
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

                    self.llm_provider_combobox.insertItem(custom_index, provider, userData=provider)
                    self.llm_provider_combobox.setCurrentIndex(custom_index)
                else:
                    # If already exists, just select it
                    self.llm_provider_combobox.setCurrentIndex(existing_index)
            else:
                # If user canceled, revert to the first item (Up's API) or handle gracefully
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

    def load_config_to_ui(self):
        """
        从 d_sakiko_config 实例中加载 LLM 相关的配置到 UI 组件中。
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
                    self.llm_provider_combobox.insertItem(custom_index, target_data, userData=target_data)

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

    def reset_error_indicators(self):
        """Reset error indicators on input fields."""
        self.custom_url_input.setError(False)
        self.custom_model_input.setError(False)
        self.custom_key_input.setError(False)
        self.standard_key_input.setError(False)

    def save_ui_to_config(self) -> bool:
        """
        将当前 ui 的设置存储到 d_sakiko_config 中

        根据当前选择不同，存在三种处理情况：
        1. Up 的 DeepSeek API: 设置 use_default_deepseek_api 为 True.
        2. 自定义 API 网站: 设置 enable_custom_llm_api_provider 为 True 并保存 URL/Model/Key.
        3. 标准提供商（常见的 API 网站）: 更新 llm_api_provider, llm_api_model 配置, 并保存 API Key.

        :return: bool - 如果保存成功返回 True，否则返回 False（例如缺少必填字段时）
        """
        # Save LLM Settings (来自 LLMAPIArea)
        index = self.llm_provider_combobox.currentIndex()
        provider_data = self.llm_provider_combobox.itemData(index)

        if provider_data == "deepseek_up":
            # 只更新这个“是否使用 Up 的 DeepSeek API”选项
            d_sakiko_config.use_default_deepseek_api.value = True

            self.reset_error_indicators()
            return True

        elif provider_data == "custom":
            if (
                    not self.custom_url_input.text()
                    or not self.custom_model_input.text()
                    or not self.custom_key_input.text()
            ):
                # 如果有任何一个字段为空，则不保存配置，保持原样
                # 并且给出错误颜色提示
                if not self.custom_url_input.text():
                    self.custom_url_input.setError(True)
                    self.custom_url_input.setFocus()
                if not self.custom_model_input.text():
                    self.custom_model_input.setError(True)
                    self.custom_model_input.setFocus()
                if not self.custom_key_input.text():
                    self.custom_key_input.setError(True)
                    self.custom_key_input.setFocus()
                return False

            d_sakiko_config.use_default_deepseek_api.value = False
            # 启用自定义 OpenAI 兼容 API 提供商
            # 这会覆盖其他已经启用的标准提供商
            d_sakiko_config.enable_custom_llm_api_provider.value = True
            d_sakiko_config.custom_llm_api_url.value = self.custom_url_input.text()
            d_sakiko_config.custom_llm_api_model.value = self.custom_model_input.text()

            # Update key in the dictionary
            d_sakiko_config.custom_llm_api_key.value = self.custom_key_input.text()

            self.reset_error_indicators()
            return True
        else:
            if not self.standard_key_input.text() or not provider_data:
                # 如果 API Key 为空，则不保存配置，保持原样
                if not self.standard_key_input.text():
                    self.standard_key_input.setError(True)
                    self.standard_key_input.setFocus()
                if not provider_data:
                    self.llm_provider_combobox.setFocus()
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

            self.reset_error_indicators()
            return True
