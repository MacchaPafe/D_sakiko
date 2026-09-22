"""演出宿主路由；普通模式进程和桌宠窗口只允许一个消费端。"""

import threading


class PresentationRouter:
    def __init__(self, normal_queue):
        self.normal_queue = normal_queue
        self.target = normal_queue
        self.latest_model = None
        self.process = None
        self.factory = None
        self.lock = threading.RLock()

    def put(self, event):
        with self.lock:
            if isinstance(event, dict) and event.get("type") == "switch_live2d":
                self.latest_model = dict(event)
            self.target.put(event)

    def start_normal(self):
        with self.lock:
            import queue

            while True:
                try:
                    self.normal_queue.get_nowait()
                except queue.Empty:
                    break
            process = self.factory()
            try:
                process.start()
                ready = getattr(process, "ready_event", None)
                if ready is not None and not ready.wait(15):
                    raise RuntimeError("普通角色窗口初始化失败或超时。")
                if not process.is_alive():
                    raise RuntimeError("普通角色窗口未能启动。")
            except Exception:
                if process.is_alive():
                    process.terminate()
                    process.join(2)
                raise
            self.process = process
            self.target = self.normal_queue
            if self.latest_model is not None:
                self.target.put(self.latest_model)

    def stop_normal(self):
        process = self.process
        if process is None:
            return
        self.normal_queue.put({"type": "exit"})
        process.join(3)
        if process.is_alive():
            process.terminate()
            process.join(2)
        if process.is_alive():
            raise RuntimeError("原角色窗口未能退出。")
        self.process = None

    def use_pet(self, target):
        with self.lock:
            self.stop_normal()
            self.target = target
            if self.latest_model is not None:
                target.put(self.latest_model)

    def close(self):
        self.stop_normal()
