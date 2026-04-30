from PyQt5.QtCore import QThread, pyqtSignal

from log import get_logger

logger = get_logger(__name__)


class CUDADetectThread(QThread):
    """
    检查当前 Python 解释器是否可调用电脑上的 cuda（GPU）来进行推理。
    """
    # True：当前电脑存在 cuda；False：当前电脑不存在 cuda 或者 解释器无法调用。
    cuda_exist_signal = pyqtSignal(bool)

    def run(self) -> None:
        try:
            import torch

            cuda_available = torch.cuda.is_available()
            self.cuda_exist_signal.emit(cuda_available)
        except Exception:
            logger.exception("检测 cuda 可用性时发生错误")
            self.cuda_exist_signal.emit(False)


class MPSDetectThread(QThread):
    """
    检查当前 Python 解释器是否可调用苹果电脑上的 mps（GPU）来进行推理。
    """
    # True：当前苹果电脑存在 mps；False：当前苹果电脑不存在 mps 或者 解释器无法调用。
    mps_exist_signal = pyqtSignal(bool)

    def run(self) -> None:
        try:
            import torch

            mps_available = torch.mps.is_available()
            self.mps_exist_signal.emit(mps_available)
        except Exception:
            logger.exception("检测 mps 可用性时发生错误")
            self.mps_exist_signal.emit(False)
