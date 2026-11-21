"""首轮命名、官方联网回传与本地网页读取的回归测试。"""
from __future__ import annotations

from dataclasses import replace
import socket
from unittest.mock import Mock, patch

import pytest
import requests

from chat.chat import Chat, ChatManager, Message
from chat.tool_calling import ToolCallingAgentRuntime
from chat.web_reader import PAGE_SIZE, Page, WebReader, _html_page
from character import CharacterAttributes
from emotion_enum import EmotionEnum
from llm.adapters.chat_completions import build_messages, parse_response
from llm.deepseek_chat import supports_chat_tools
from llm.types import LLMConfig, LLMRequest, LLMResult

FLASH = LLMConfig("deepseek", "deepseek/deepseek-flash", "test-key")


def test_first_title_is_persisted_once_and_manual_names_win() -> None:
    """首次生成消耗机会，重启、改名、复制和旧存档均不能误命名。"""
    person = CharacterAttributes()
    person.character_name = "祥子"
    manager = ChatManager()
    chat = manager.create_single_character_chat(person)
    chat = Chat.from_dict(chat.to_dict())
    assert chat.claim_first_title("one")
    assert not Chat.from_dict(chat.to_dict()).claim_first_title("two")
    chat.name = chat.name
    assert not chat.apply_first_title("one", "不应生效")
    named = manager.create_single_character_chat(person, name="练习")
    assert not named.claim_first_title("named")
    assert not Chat.from_dict(Chat().to_dict()).claim_first_title("old")
    other = manager.create_single_character_chat(person)
    assert other.claim_first_title("first")
    assert not other.apply_first_title("late", "迟到")
    assert other.apply_first_title("first", "活动报名")
    assert not other.apply_first_title("first", "再次命名")
    assert Chat.from_dict(other.to_dict()).name == "活动报名"


@pytest.mark.parametrize("returned_title", ["排练时间查询", ""])
def test_title_requests_follow_the_first_user_turn(returned_title: str) -> None:
    """工具接续请求可取标题，拿到标题或进入第二轮后就停止。"""
    from dp_local2 import DSLocalAndVoiceGen

    engine = DSLocalAndVoiceGen.__new__(DSLocalAndVoiceGen)
    engine.d_sakiko_config = object()
    engine.current_chat_id = "chat"
    engine.chat_manager = Mock(storage_error=None)
    engine.chat_manager.get_chat_by_id.return_value.turns = []
    engine._request_turn_id = engine._title_request_turn_id = "first"
    engine._generated_title = ""
    tool_call = {"type": "function", "id": "time", "function": {"name": "get_time", "arguments": "{}"}}
    transport = Mock(side_effect=[
        LLMResult(tool_calls=[tool_call]), LLMResult(), LLMResult(text="三点", title=returned_title),
        LLMResult(), LLMResult(),
    ])
    with patch.object(engine, "_current_deepseek_file_service", return_value=None), patch.object(
        engine, "_execute_completion_with_provider_policy", transport
    ):
        for phase in ("tool-runtime", "rolling-summary", "tool-runtime", "plain-runtime"):
            engine._completion_with_current_config(phase, [], _reasoning_snapshot_locked=True)
        engine._request_turn_id = "second"
        engine._completion_with_current_config("tool-runtime", [], _reasoning_snapshot_locked=True)

    titles = [call.args[1].get("extra_body", {}).get("title") for call in transport.call_args_list]
    enabled = {"enabled": True}
    assert titles == [enabled, None, enabled, None if returned_title else enabled, None]
    assert engine._generated_title == returned_title


def test_non_thinking_conflict_is_actionable_before_sending() -> None:
    """关闭思考时由用户选择如何处理冲突，不发起自动重试或切换。"""
    from dp_local2 import DSLocalAndVoiceGen
    from llm.errors import LLMError

    engine = DSLocalAndVoiceGen.__new__(DSLocalAndVoiceGen)
    engine.d_sakiko_config = object()
    engine._native_web_turn = True
    engine._execute_completion_with_provider_policy = Mock()
    with pytest.raises(LLMError, match="请开启思考"):
        engine._completion_with_current_config("tool-runtime", [], [{"type": "function"}],
            thinking={"type": "disabled"}, _reasoning_snapshot_locked=True)
    engine._execute_completion_with_provider_policy.assert_not_called()


def test_web_detail_renders_clickable_title_and_escapes_page_text() -> None:
    """来源标题可点击，网页标题里的 HTML 不能成为可执行的富文本。"""
    from qtUI import ToolCallInfoDialog

    html = ToolCallInfoDialog._web_result_html("搜索命中\n<img src=x>\nhttps://example.org/?a=1&b=2")
    assert '<a href="https://example.org/?a=1&amp;b=2">&lt;img src=x&gt;</a>' in html
    assert "<img" not in html


def test_only_official_flash_receives_native_tools() -> None:
    """供应商、实际地址、模型与协议共同决定扩展字段的可用性。"""
    assert supports_chat_tools(FLASH)
    assert supports_chat_tools(replace(FLASH, provider="deepseek_up"))
    assert supports_chat_tools(replace(FLASH, provider="custom", base_url="https://api.deepseek.com/v1"))
    assert not supports_chat_tools(replace(FLASH, provider="custom"))
    assert not supports_chat_tools(replace(FLASH, base_url="https://api.deepseek.com.invalid"))
    assert not supports_chat_tools(replace(FLASH, model="deepseek/deepseek-v4-pro"))


def test_page_snapshot_links_and_continuation() -> None:
    """正文保留结构与链接，续读同一快照且不带入导航内容。"""
    page = _html_page("https://example.org/news/", (
        "<html><title>报名</title><body><nav>顶部导航</nav><main><h1>活动</h1>"
        "<ul><li>周六</li></ul><a href='rules'>规则</a><p>" + "报名" * PAGE_SIZE +
        "</p></main></body></html>").encode())
    assert "# 活动" in page.content and "https://example.org/news/rules" in page.content
    assert "顶部导航" not in page.content and page.title == "报名"
    reader = WebReader()
    with patch("chat.web_reader._fetch", return_value=page) as fetch:
        first = reader.read({"url": "https://example.org/short"})
        next_part = reader.read({"url": page.url, "offset": first["next_offset"]})
    fetch.assert_called_once()
    assert len(first["content"]) == PAGE_SIZE
    assert next_part["content"] == page.content[PAGE_SIZE:2 * PAGE_SIZE]


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://[::1]/", "http://192.168.1.1/",
                                 "http://198.19.0.1/", "file:///tmp/x", "http://user:pw@example.org"])
def test_reader_only_connects_to_public_web(url: str) -> None:
    """在发起连接前拒绝本机地址、其他协议和带凭据的网址。"""
    with patch("requests.Session.get") as get, pytest.raises(ValueError):
        WebReader().read({"url": url})
    get.assert_not_called()


@pytest.mark.parametrize("proxies", [{}, {"https": "http://127.0.0.1:7897"}])
def test_clash_fake_ip_transport(proxies: dict[str, str]) -> None:
    """显式代理按域名转发；仅 TUN 模式按确认的映射地址发起连接。"""
    url = "https://example.org/notice"
    addr = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.255.254", 443))
    response = requests.Response()
    response.status_code = 200
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    response._content = b"notice"
    response._content_consumed = True
    with patch("socket.getaddrinfo", return_value=[addr]), patch(
        "requests.utils.get_environ_proxies", return_value=proxies
    ), patch("requests.Session.get", return_value=response) as get:
        assert WebReader().read({"url": url})["content"] == "notice"
    assert get.call_args.args[0] == (url if proxies else "https://198.18.255.254:443/notice")
    assert get.call_args.kwargs["headers"]["Host"] == "example.org"
    assert get.call_args.kwargs["proxies"] == proxies


def test_clash_redirect_checks_all_new_addresses() -> None:
    """即使前一跳由代理处理，重定向中的内网解析结果也必须被拒绝。"""
    fake = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.3", 443))
    private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.2", 443))
    redirect = requests.Response()
    redirect.status_code = 302
    redirect.headers["Location"] = "https://next.example.org/path"
    redirect._content_consumed = True
    with patch("socket.getaddrinfo", side_effect=[[fake], [fake, private]]), patch(
        "requests.utils.get_environ_proxies", return_value={"https": "http://127.0.0.1:7897"}
    ), patch("requests.Session.get", return_value=redirect) as get, pytest.raises(ValueError, match="非公网"):
        WebReader().read({"url": "https://example.org/"})
    get.assert_called_once()


def test_search_selection_can_open_and_continue_a_result() -> None:
    """经真实工具循环把候选网页的正文送回模型，关闭开关时两种工具一起消失。"""
    replies = [
        ("web_search", '{"query":"公告"}'), ("read_url", '{"url":"https://example.org"}'),
        ("read_url", '{"url":"https://example.org","offset":6000}'),
    ]
    schemas: list[set[str]] = []

    def completion(**kwargs: object) -> dict[str, object]:
        """用确定性调用顺序验收工具可见性和工具结果。"""
        schemas.append({t["function"]["name"] for t in kwargs["tools"] or []})
        if replies:
            name, args = replies.pop(0)
            message = {"tool_calls": [{"id": f"call-{len(replies)}", "type": "function",
                                       "function": {"name": name, "arguments": args}}]}
        else:
            message = {"content": "结束"}
        return {"choices": [{"message": message}]}

    runtime = ToolCallingAgentRuntime(completion, debug=False)
    page = Page("https://example.org", "公告", "甲" * PAGE_SIZE + "报名截止")
    with patch("chat.tool_calling.WebSearchTool._search_bocha", return_value=[{"url": page.url}]), patch("chat.web_reader._fetch", return_value=page) as fetch:
        result = runtime.run("m", [], enabled_general_tools=frozenset({"web_search"}), web_backend="bocha")
    assert all(s == {"web_search", "read_url"} for s in schemas)
    assert "报名截止" in result.messages[-1]["content"]
    fetch.assert_called_once()
    runtime.run("m", [], enabled_general_tools=frozenset(), web_backend="bocha")
    assert schemas[-1] == set()


def test_web_protocol_round_trip_and_display_are_separate() -> None:
    """读取材料在存档还原后可回传，切换供应商或编辑历史时不会复用。"""
    chat = Chat()
    chat.add_message(Message(character_name="User", text="读 https://example.org", translation="",
                             emotion=EmotionEnum.HAPPINESS, audio_path=""))
    history = chat.build_llm_query("祥子")
    history.append({"role": "user", "content": "<runtime_controls>台词</runtime_controls>"})
    chat.remember_runtime_controls(history)
    request = LLMRequest(history)
    assert "https://example.org" in str(build_messages(request, FLASH)[-1]["content"])
    steps = [{"index": 0, "calls": [{"id": "srv1", "status": "completed", "kind": "read_link",
              "action": {"type": "read_link", "url": "https://example.org"},
              "page": {"title": "公告", "url": "https://example.org"}, "signature": "signed"}]}]
    result = parse_response({"choices": [{"finish_reason": "stop", "message": {
        "content": "读到了", "title": "讨论公告", "server_tool_calls": steps,
        "annotations": [{"type": "url_citation", "url_citation": {"url": "https://example.org", "title": "公告"}}]}}]}, request, FLASH)
    assert result.title == "讨论公告" and not result.tool_calls
    chat.record_model_response(result)
    loaded = Chat.from_dict(chat.to_dict())
    assert [r.tool_name for r in loaded.get_tool_call_records()] == ["deepseek_read_url", "deepseek_citations"]
    assert all(m["kind"] != "tool-result" for m in loaded.turns[0]["messages"])
    follow = LLMRequest(loaded.build_llm_query("祥子"))
    assert build_messages(follow, FLASH)[-1]["server_tool_calls"] == steps
    assert "server_tool_calls" not in build_messages(follow, replace(FLASH, api_key="other"))[-1]
    follow.messages.insert(0, {"role": "system", "content": "changed"})
    assert "server_tool_calls" not in build_messages(follow, FLASH)[-1]
