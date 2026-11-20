"""官方 Flash 对话扩展与来源记录的展示投影。"""
from __future__ import annotations

from urllib.parse import urlsplit
from .types import LLMConfig


WEB_TOOLS = ("deepseek_web_search", "deepseek_read_url")
TOOL_CONFLICT = ("DeepSeek 关闭思考时，网络搜索无法与本地工具同时使用。请开启思考，"
                 "或关闭其他工具（包括已启用的世界书检索工具），也可以关闭网络搜索后重试。")


def supports_chat_tools(config: LLMConfig) -> bool:
    """只在已知官方 Chat Completions 连接上传递私有协议字段。"""
    if config.protocol != "chat-completions" or config.provider not in {"deepseek", "deepseek_up", "custom"}:
        return False
    if config.model.removeprefix("openai/").removeprefix("deepseek/") != "deepseek-flash":
        return False
    if not config.base_url:
        return config.provider in {"deepseek", "deepseek_up"}
    parsed = urlsplit(config.base_url)
    return (parsed.scheme == "https" and parsed.netloc.lower() in {"api.deepseek.com", "api.deepseek.com:443"}
            and parsed.path.rstrip("/") in {"", "/v1", "/beta"} and not parsed.query and not parsed.fragment)


def source_text(items: list[dict[str, object]]) -> str:
    """按网址去重，保留标题与网址的对应关系。"""
    lines: list[str] = []
    seen: set[str] = set()
    for item in items:
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")) or url in seen:
            continue
        seen.add(url)
        lines.extend([str(item.get("title") or url).replace("\n", " "), url])
    return "\n".join(lines)


def web_displays(message_id: str, provider_options: dict[str, object]) -> list[dict[str, object]]:
    """从完整协议内容派生界面记录，展示字段不会成为本地函数调用或进入台词。"""
    details = provider_options.get("deepseek", {})
    if not isinstance(details, dict):
        return []
    displays: list[dict[str, object]] = []
    for step in details.get("server_tool_calls", []):
        for call in step.get("calls", []):
            action = call.get("action") or {}
            search = action.get("type") == "search"
            items = call.get("sources") or [] if search else [call.get("page") or action]
            lines = ["搜索命中" if search else "读取网页"]
            if search:
                lines.extend(action.get("queries") or [])
            ok = call.get("status") == "completed"
            if not ok:
                lines.append("失败：" + str((call.get("error") or {}).get("code") or "unavailable"))
            lines.append(source_text(items))
            displays.append({"tool_call_id": str(call["id"]), "tool_name": "deepseek_web_search" if search else "deepseek_read_url",
                             "arguments": action, "ok": ok, "status": "completed", "result_content": "\n".join(lines).strip()})
    citations = [item["url_citation"] for item in details.get("annotations", [])
                 if item.get("type") == "url_citation" and isinstance(item.get("url_citation"), dict)]
    if citations:
        displays.append({"tool_call_id": message_id + "-citations", "tool_name": "deepseek_citations",
                         "arguments": {}, "ok": True, "status": "completed", "result_content": "回答引用\n" + source_text(citations)})
    return displays
