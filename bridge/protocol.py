"""
消息协议定义
"""
import json
from typing import Dict, Any, Optional

Message = Dict[str, Any]

def create_message(msg_type: str, data: Any) -> str:
    """创建 JSON 序列化的 WebSocket 消息"""
    return json.dumps({"type": msg_type, "data": data}, ensure_ascii=False)

def parse_message(raw: str) -> Optional[Message]:
    """解析 WebSocket 消息"""
    try:
        return json.loads(raw)
    except Exception:
        return None
