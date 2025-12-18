import sys
import contextlib
import os
import shutil
import glob
import time

from PyQt5.QtWidgets import (QApplication, QWidget,
                             QVBoxLayout, QLabel, QHBoxLayout, QLineEdit, QPushButton, QListWidget, QFileDialog,
                             QStackedWidget, QDialog)
from PyQt5.QtCore import Qt, QTimer

from GPT_SoVITS.components.custom_setting_area import CustomSettingArea
from GPT_SoVITS.components.gpt_sovits_area import GPTSoVITSArea
from GPT_SoVITS.components.llm_api_area import LLMAPIArea

# 去广告
with contextlib.redirect_stdout(None):
    from qfluentwidgets import SegmentedWidget, PrimaryPushButton, PushButton

# 将当前文件夹加入 sys.path，强制搜索当前目录的模块（即使 os.getcwd() 不是当前目录）
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)


from GPT_SoVITS.qconfig import d_sakiko_config, PROVIDER_FRIENDLY_NAME_MAP, FAMOUS_CHAT_PROVIDERS, OTHER_CHAT_PROVIDERS


class DSakikoConfigWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle('数字小祥启动参数配置')

        # 分栏组件
        self.pivot = SegmentedWidget(self)
        # 存放各个分栏下的内容
        self.stackedWidget = QStackedWidget(self)
        # Layout
        self.vBoxLayout = QVBoxLayout(self)

        # 对应的分栏内容
        self.LLMApiArea = LLMAPIArea(self)
        self.GPTSoVITSArea = GPTSoVITSArea(self)
        self.customSettingArea = CustomSettingArea(self)

        # 添加到分栏中
        self.addSubInterface(self.LLMApiArea, "LLMApiArea", self.tr("大模型 API 配置"))
        self.addSubInterface(self.GPTSoVITSArea, "GPTSoVITSArea", self.tr("语音合成模型配置"))
        self.addSubInterface(self.customSettingArea, "customSettingArea", self.tr("个性化"))

        # 连接信号并初始化当前标签页
        self.stackedWidget.currentChanged.connect(self.onCurrentIndexChanged)
        self.stackedWidget.setCurrentWidget(self.LLMApiArea)
        self.pivot.setCurrentItem(self.LLMApiArea.objectName())

        # 添加标题和分栏组件到主布局
        self.vBoxLayout.setContentsMargins(30, 0, 30, 30)
        self.vBoxLayout.addWidget(self.pivot, 0, Qt.AlignmentFlag.AlignHCenter)
        self.vBoxLayout.addWidget(self.stackedWidget, 1, Qt.AlignmentFlag.AlignHCenter)

        self.saveButton=PrimaryPushButton(self.tr("保存配置"), self)
        self.saveButton.clicked.connect(self.save_config)
        self.vBoxLayout.addWidget(self.saveButton)
        self.save_success_label=QLabel('')
        self.save_success_label.setWordWrap(True)

        self.clear_success_label_timer = QTimer()
        self.clear_success_label_timer.timeout.connect(self.clear_save_success_label)
        self.clear_success_label_timer.setSingleShot(True)
        self.clear_success_label_timer.setInterval(3000)  # 3秒后触发

        self.exit_btn=PushButton(self.tr("关闭窗口"), self)
        self.exit_btn.clicked.connect(self.close)
        self.vBoxLayout.addWidget(self.exit_btn)

        self.vBoxLayout.addWidget(self.save_success_label)

        self.resize(500, 600)
        
        # self.load_config_to_ui()
        # self.llm_provider_combobox.currentIndexChanged.connect(self.on_llm_provider_changed)
    
    def addSubInterface(self, widget: QLabel, object_name: str, text: str):
        """
        添加一个 Qt 组件为一个分栏项目。

        :param widget: 要添加的 Qt 组件实例。   
        :param object_name: 该组件的标识符，需要是唯一的，取什么都行
        :param text: 分栏标题上显示的文字
        """
        widget.setObjectName(object_name)
        widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stackedWidget.addWidget(widget)

        # 使用全局唯一的 objectName 作为路由键
        self.pivot.addItem(
            routeKey=object_name,
            text=text,
            onClick=lambda: self.stackedWidget.setCurrentWidget(widget)
        )

    def onCurrentIndexChanged(self, index):
        widget = self.stackedWidget.widget(index)
        self.pivot.setCurrentItem(widget.objectName())
    
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
            dest_path = os.path.join('font/', new_filename)

            # 2. 先尝试清理旧文件（尽力而为，删不掉也不报错）
            # 查找所有名字是 custom_font_ 开头的文件
            old_files = glob.glob(os.path.join('font/', 'custom_font_*'))
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

    app = QApplication(sys.argv)
    win = DSakikoConfigWindow()

    win.show()
    sys.exit(app.exec_())
