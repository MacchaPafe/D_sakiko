"""V2/V3 共用的模型摘戴动作配置与预览。"""

from pathlib import Path

from PyQt5.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout
from live2d_support.mask_actions import mask_actions, mask_motion_options, save_mask_actions


class MaskActionsDialog(QDialog):
    def __init__(self, model_path, send_preview, parent=None):
        super().__init__(parent)
        self.setWindowTitle("当前模型的面具动作")
        self.model_path = str(model_path)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择模型自身的摘戴动作。无需配置循环或保持动作。"))
        form = QFormLayout()
        self.selectors = {}
        choices = mask_motion_options(self.model_path)
        bindings = mask_actions(self.model_path)
        for key, label in (("on", "戴上面具"), ("off", "摘下面具")):
            selector = QComboBox()
            selector.addItem("未配置", "")
            for file in choices:
                selector.addItem(file, file)
            selector.setCurrentIndex(max(0, selector.findData(bindings.get(key, ""))))
            self.selectors[key] = selector
            row = QHBoxLayout()
            row.addWidget(selector, 1)
            preview = QPushButton("预览")
            preview.setEnabled(bool(selector.currentData()))
            selector.currentIndexChanged.connect(lambda _index, combo=selector, button=preview: button.setEnabled(bool(combo.currentData())))
            preview.clicked.connect(lambda checked=False, combo=selector: send_preview({
                "file": str(Path(self.model_path).resolve().parent / combo.currentData()), "auto_expression": False}))
            row.addWidget(preview)
            form.addRow(label, row)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        try:
            save_mask_actions(self.model_path, {key: combo.currentData() for key, combo in self.selectors.items()})
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法保存面具动作", str(error))
            return
        self.accept()
