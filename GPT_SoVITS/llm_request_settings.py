"""对话请求超时配置；秒数只作用于单次大模型请求。"""

MAX_REQUEST_TIMEOUT = 86400


def normalize_request_timeout(value, default=30):
    """旧配置或无效值使用默认值，不允许关闭超时。"""
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_REQUEST_TIMEOUT:
        return value
    return default


def request_timeout(config, *, theater=False):
    """按调用模式选择超时，兼容缺少新字段的配置快照。"""
    if theater:
        name, default = "theater_request_timeout_seconds", 100
    else:
        name, default = "llm_request_timeout_seconds", 30
    item = getattr(config, name, None)
    return normalize_request_timeout(getattr(item, "value", default), default)
