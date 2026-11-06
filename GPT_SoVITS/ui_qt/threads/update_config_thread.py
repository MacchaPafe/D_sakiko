# 本线程用于在后台开启一个 socket 服务端，不断监听请求，如果收到重置配置请求则从本地配置文件中重新读取（重新加载）公开的 d_sakiko_config 字段
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtNetwork import QLocalServer, QLocalSocket


RELOAD_CONFIG_MESSAGE = "reload_config"


def notify_config_reload(app_id: str = "d_sakiko_config", timeout_ms: int = 1000) -> bool:
    """
    通知主程序重新加载 d_sakiko_config。
    """
    socket = QLocalSocket()
    socket.connectToServer(app_id)
    if not socket.waitForConnected(timeout_ms):
        socket.abort()
        return False

    socket.write(RELOAD_CONFIG_MESSAGE.encode("utf-8"))
    ok = socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    return ok


class UpdateConfigThread(QThread):
    """
    开启一个 socket 服务端监听消息，如果收到“加载配置”请求则重新加载公用变量 d_sakiko_config 的内部值
    """
    reload_requested = pyqtSignal()

    def __init__(self, app_id: str, parent=None):
        super().__init__(parent)
        self.app_id = app_id

    def run(self):
        server = QLocalServer()
        # 清除可能没退出的上个服务器记录
        server.removeServer(self.app_id)
        if not server.listen(self.app_id):
            return

        def handle_new_connection():
            socket = server.nextPendingConnection()
            if socket is None:
                return

            try:
                if socket.waitForReadyRead(1000):
                    data = socket.readAll().data().decode('utf-8').strip().lower()
                    if data == RELOAD_CONFIG_MESSAGE:
                        self.reload_requested.emit()
            finally:
                socket.disconnectFromServer()
                socket.deleteLater()

        server.newConnection.connect(handle_new_connection)

        self.exec()

        server.close()
        server.removeServer(self.app_id)

    def requestInterruption(self):
        super().requestInterruption()
        self.quit()
