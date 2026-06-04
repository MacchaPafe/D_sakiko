# 获得模型最长上下文长度的线程
from PyQt5.QtCore import QThread, pyqtSignal


class GetModelLimitThread(QThread):
    model_input_token_limit = pyqtSignal(object) # int | None

    def __init__(self, model: str, parent=None) -> None:
        super().__init__(parent)
        self.model = model

    def run(self) -> None:
        from chat.model_token_usage import get_model_input_token_limit

        self.model_input_token_limit.emit(get_model_input_token_limit(self.model))
