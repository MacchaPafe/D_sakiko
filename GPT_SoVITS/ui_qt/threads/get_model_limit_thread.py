"""后台查询模型上下文上限，不让慢查询阻止应用退出。"""
from __future__ import annotations

import threading
from PyQt5.QtCore import QObject, pyqtSignal


class GetModelLimitThread(QObject):
    model_input_token_limit = pyqtSignal(object)

    def __init__(self, model: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        model = self.model

        def run() -> None:
            """用配置快照查询与发送路径一致的模型上限。"""
            from chat.model_token_usage import resolve_model_input_token_limit
            from qconfig import create_d_sakiko_config_snapshot

            result = resolve_model_input_token_limit(create_d_sakiko_config_snapshot(), model)
            try:
                if model == self.model:
                    self.model_input_token_limit.emit(result)
            except RuntimeError:
                pass  # 宿主窗口已经销毁，迟到结果无需显示。

        self._thread = threading.Thread(
            target=run, name="ModelContextLimit", daemon=True
        )
        self._thread.start()
