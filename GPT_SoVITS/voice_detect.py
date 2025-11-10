import sys
import sounddevice as sd
import numpy as np
from PyQt5.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QTextEdit
from PyQt5.QtCore import QTimer, QThread, pyqtSignal, QObject


# -----------------------------------------------------------------
# (Worker 和 Thread 相關類別與之前相同，保持不變)
# -----------------------------------------------------------------
class ModelLoaderThread(QThread):
    model_loaded = pyqtSignal(object)
    model_load_failed = pyqtSignal(str)

    def __init__(self, model_size, device, compute_type):
        super().__init__()
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type

    def run(self):
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            self.model_loaded.emit(model)
        except Exception as e:
            self.model_load_failed.emit(str(e))


class TranscriptionWorker(QObject):
    finished = pyqtSignal(str)

    def __init__(self, model):
        super().__init__()
        self.model = model

    def transcribe(self, audio_data):
        try:
            segments, _ = self.model.transcribe(audio_data, beam_size=5, language="zh")
            text = "".join([seg.text for seg in segments])
            self.finished.emit(text)
        except Exception as e:
            self.finished.emit(f"[辨識錯誤] {e}")


# -----------------------------------------------------------------
# 步驟 3: 修改主應用程式
# -----------------------------------------------------------------
class RecorderApp(QWidget):
    def __init__(self):
        super().__init__()

        self.is_recording = False
        self.recording = []
        self.is_valid = False
        self.model = None
        self.stream = None  # 初始化串流變數
        self.samplerate = 16000  # 定義取樣率

        self.init_ui()

        # --- 處理模型載入 ---
        self.text_output.append("正在載入模型，請稍候...")
        self.load_model()

    def init_ui(self):
        layout = QVBoxLayout()
        self.button = QPushButton("按住錄音", self)
        self.button.setCheckable(True)
        self.button.pressed.connect(self.start_recording)
        self.button.released.connect(self.stop_recording)
        self.button.setEnabled(False)  # 預設禁用

        self.text_output = QTextEdit(self)
        self.text_output.setReadOnly(True)
        self.record_timer = QTimer()
        self.record_timer.timeout.connect(self.check_valid)

        layout.addWidget(self.button)
        layout.addWidget(self.text_output)
        self.setLayout(layout)
        self.setWindowTitle("錄音+識別 (持久化串流版)")
        self.resize(400, 200)

    def load_model(self):
        # 請確保 "base" 是正確的模型名稱或路徑
        self.model_loader = ModelLoaderThread("./pretrained_models/faster_whisper_base", device="cpu", compute_type="int8")
        self.model_loader.model_loaded.connect(self.on_model_loaded)
        self.model_loader.model_load_failed.connect(self.on_model_load_failed)
        self.model_loader.start()

    def on_model_load_failed(self, error_message):
        self.text_output.append(f"模型載入失敗: {error_message}")

    def on_model_loaded(self, model):
        self.model = model
        self.text_output.append("模型載入完成！")
        # 接著，啟動持久化的音訊串流
        self.start_persistent_stream()

    # ----------------------------------------------------
    # --- 核心修改：啟動持久化音訊串流 ---
    # ----------------------------------------------------
    def start_persistent_stream(self):
        """
        開啟一次音訊串流，並讓它在背景持續運行，直到程式關閉。
        """
        self.text_output.append("正在啟動音訊設備...")
        try:
            self.stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=1,
                callback=self.audio_callback,
                dtype='int16'  # 使用 int16 格式
            )
            # 啟動串流，它將在背景執行緒中持續呼叫 audio_callback
            self.stream.start()

            self.text_output.append("音訊設備已就緒。可以開始錄音。")
            self.button.setEnabled(True)  # 只有串流開啟成功後才啟用按鈕

        except Exception as e:
            self.text_output.append(f"錯誤：無法啟動麥克風串流。")
            self.text_output.append(f"請檢查麥克風是否連接或被其他程式佔用。")
            self.text_output.append(f"錯誤詳情: {e}")
            self.button.setEnabled(False)  # 保持按鈕禁用

    # ----------------------------------------------------

    def check_valid(self):
        self.is_valid = True

    def audio_callback(self, indata, frames, time, status):
        """
        這個函式現在會被「持續不斷」地呼叫。
        我們只在 self.is_recording 為 True 時才收集資料。
        """
        if status:
            print(f"錄音狀態回報: {status}", file=sys.stderr)

        # 核心邏輯：只在「錄音開關」開啟時才儲存
        if self.is_recording:
            self.recording.append(indata.copy())

    def start_recording(self):
        """
        按下按鈕：不再建立串流，只改變「開關」狀態並清空「籃子」。
        """
        if not self.stream:  # 增加一個保險檢查
            self.text_output.append("錯誤：音訊串流尚未準備就緒！")
            return

        self.is_recording = True  # <-- **核心**
        self.is_valid = False
        self.recording = []  # 清空籃子，準備本次錄音
        self.record_timer.start(300)
        self.text_output.append("...開始錄音...")

    def stop_recording(self):
        """
        釋放按鈕：不再關閉串流，只改變「開關」狀態並處理資料。
        """
        if not self.is_recording:
            return  # 避免在快速點擊時重複觸發

        self.is_recording = False  # <-- **核心**
        self.record_timer.stop()
        self.text_output.append("...錄音結束...")

        if not self.is_valid:
            self.text_output.append("錄音時間過短，請重試。")
            return

        try:
            if not self.recording:
                self.text_output.append("錄音失敗，沒有擷取到音訊。")
                return

            audio_data = np.concatenate(self.recording, axis=0).flatten()

            # (重要) 將音訊從 int16 轉換為 float32
            # sounddevice 預設的 int16 範圍是 [-32768, 32767]
            # Whisper 模型需要的是 float32 範圍在 [-1.0, 1.0]
            audio_data = audio_data.astype(np.float32) / 32768.0

        except ValueError:
            self.text_output.append("錄音失敗，拼接音訊時出錯。")
            return

        self.run_transcription_thread(audio_data)

    def run_transcription_thread(self, audio_data):
        # (與前版相同)
        self.button.setEnabled(False)
        self.text_output.append("辨識中...")

        self.transcription_thread = QThread()
        self.worker = TranscriptionWorker(self.model)
        self.worker.moveToThread(self.transcription_thread)

        self.transcription_thread.started.connect(lambda: self.worker.transcribe(audio_data))
        self.worker.finished.connect(self.on_transcription_finished)

        self.transcription_thread.finished.connect(self.transcription_thread.deleteLater)
        self.worker.finished.connect(self.transcription_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)

        self.transcription_thread.start()

    def on_transcription_finished(self, text):
        # (與前版相同)
        self.text_output.append("識別結果: " + text)
        if self.model and not self.is_recording:
            self.button.setEnabled(True)

    # ----------------------------------------------------
    # --- 新增的：關閉事件處理 ---
    # ----------------------------------------------------
    def closeEvent(self, event):
        """
        (極其重要)
        當使用者點擊 'X' 關閉視窗時，我們必須手動停止並關閉
        那個持久化的音訊串流，否則程式會卡住或崩潰。
        """
        self.text_output.append("正在關閉音訊串流...")
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
                self.text_output.append("音訊串流已關閉。")
            except Exception as e:
                print(f"關閉串流時出錯: {e}", file=sys.stderr)

        # 允許視窗關閉
        event.accept()
        # ----------------------------------------------------


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = RecorderApp()
    window.show()
    sys.exit(app.exec_())