import platform
import subprocess

from PyQt5.QtCore import QThread, pyqtSignal


class MemoryDetectThread(QThread):
    """
    检查当前系统的显存或者内存情况。对于 Windows/Linux 系统，检查 cuda 显存；对于 MacOS 系统，检查内存。
    """
    # float | None
    memory_size_signal = pyqtSignal(object)

    def run(self) -> None:
        system = platform.system()
        if system == "Windows" or system == "Linux":  # Windows 或 Linux 系统
            try:
                import torch
                if torch.cuda.is_available():
                    free_mem, total_mem = torch.cuda.mem_get_info(0)
                    total_mem_gb = total_mem / (1024 ** 3)
                    self.memory_size_signal.emit(total_mem_gb)
            except Exception:
                self.memory_size_signal.emit(None)
        elif system == "Darwin":  # MacOS
            try:
                # -n 参数表示只输出值，不输出键名
                output = subprocess.check_output(['sysctl', '-n', 'hw.memsize'])
                
                # 获取到的是字节(Bytes)字符串，转换为整型
                total_bytes = int(output.strip())
                
                # 转换为 GB (除以 1024 的 3 次方)
                total_gb = total_bytes / (1024**3)
                self.memory_size_signal.emit(total_gb)

            except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
                self.memory_size_signal.emit(None)
