import json
import os
import threading
import time
from typing import Callable, List, Dict

# 定义专用于工具持化数据的存放目录位置
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'tool_data')
REMINDER_FILE = os.path.join(DATA_DIR, 'reminders.json')

class ReminderManager:
    def __init__(self, trigger_callback: Callable[[str], None]):
        self.trigger_callback = trigger_callback
        self.reminders: List[Dict] = []
        self.timers: List[threading.Timer] = []
        self._ensure_file_exists()
        self.load_reminders()

    def _ensure_file_exists(self):
        """确保储存各种工具生成数据的本地落盘文件夹都已存在"""
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(REMINDER_FILE):
            with open(REMINDER_FILE, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False)

    def load_reminders(self):
        """加载历史定时的任务状态，处理错进和未完成的部分并继续。"""
        try:
            with open(REMINDER_FILE, 'r', encoding='utf-8') as f:
                self.reminders = json.load(f)
        except Exception:
            self.reminders = []
        
        now = time.time()
        valid_reminders = []
        for r in self.reminders:
            if r.get('status') == 'pending':
                target_time = r.get('target_timestamp', 0)
                if target_time > now:
                    self._schedule_timer(r)
                    valid_reminders.append(r)
                elif now - target_time < 3600 * 12:
                    # 如果是因为程序关闭错过了闹钟，但在 12 小时内，仍然立即投递补发一次
                    self._handle_trigger(r, missed=True)
                    valid_reminders.append(r) # _handle_trigger 已修改状态
                else:
                    # 时间过了太久直接作废
                    r['status'] = 'expired'
                    valid_reminders.append(r)
            else:
                valid_reminders.append(r)
        
        self.reminders = valid_reminders
        self.save_reminders()

    def save_reminders(self):
        """同步回硬盘"""
        with open(REMINDER_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.reminders, f, ensure_ascii=False, indent=2)

    def add_reminder(self, content: str, target_timestamp: float) -> bool:
        """被 tool_calling 直接调用的添加工具回调方法"""
        r = {
            'id': str(time.time()),
            'content': content,
            'target_timestamp': target_timestamp,
            'status': 'pending'
        }
        self.reminders.append(r)
        self.save_reminders()
        self._schedule_timer(r)
        return True

    def _schedule_timer(self, reminder: Dict):
        """异步安全启用的后台线程执行器定时方案"""
        delay = reminder['target_timestamp'] - time.time()
        if delay > 0:
            t = threading.Timer(delay, self._handle_trigger, args=(reminder,))
            t.daemon = True
            t.start()
            self.timers.append(t)

    def _handle_trigger(self, reminder: Dict, missed: bool = False):
        """执行时间到达时回调业务"""
        # 将被触发的任务更新为完成落盘
        for r in self.reminders:
            if r['id'] == reminder['id']:
                r['status'] = 'completed'
                break
        self.save_reminders()
        
        # 构建专属触发指令
        prefix = "【系统内部事件触发：定时提醒】\n"
        if missed:
            msg = f"{prefix}你之前设定的定时提醒因程序关闭已被错过了。事件内容：【{reminder['content']}】。可以根据当前聊天情景向用户传达此情况。”。"
        else:
            msg = f"{prefix}设定的定时时间已到！事件内容：【{reminder['content']}】。请在现在的回复中自然地马上向用户传达此提醒。"
        
        # 将构造出的内部指导通过 callback 指针打入事件列队之中（模拟用户静默指令）
        self.trigger_callback(msg)
