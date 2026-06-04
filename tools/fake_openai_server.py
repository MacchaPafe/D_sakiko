#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TypeAlias


JSONPrimitive: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONPrimitive | list["JSONValue"] | dict[str, "JSONValue"]


class Scenario(str, Enum):
    """定义假 LLM 服务可返回的固定测试场景。"""

    # 始终返回完全合法的 JSON array，用于确认假服务和正常回复链路可用。
    VALID = "valid"
    # 返回顶层单个 JSON object，且 emotion 非法；用于测试单 object 兼容和情绪兜底。
    TOP_LEVEL_OBJECT_NEUTRAL = "top-level-object-neutral"
    # 返回日英模式下缺少 translation 的 JSON；用于测试宽松模式是否允许翻译为空。
    MISSING_TRANSLATION = "missing-translation"
    # 返回缺少 text 的 JSON；用于测试宽松模式仍然拒绝不可显示的段落。
    MISSING_TEXT = "missing-text"
    # 始终返回纯自然语言；用于测试完全非 JSON 输出的错误处理。
    PLAIN_TEXT = "plain-text"
    # 返回带多余字段的合法段落；用于测试严格模式拒绝、宽松模式忽略多余字段。
    EXTRA_FIELDS = "extra-fields"
    # 初始阶段返回自然语言，收口/修复阶段返回可宽松解析的 JSON；用于测试小 schema 错误恢复。
    LENIENT_RETRY = "lenient-retry"
    # 初始阶段返回自然语言，收口/修复阶段仍返回非 JSON；用于测试原文兜底显示开关。
    RAW_FALLBACK = "raw-fallback"
    # 初始阶段返回自然语言，收口/修复阶段返回缺 text 的 JSON；用于测试缺 text 不被宽松解析吞掉。
    MISSING_TEXT_RETRY = "missing-text-retry"


class Phase(str, Enum):
    """描述当前请求大致处于普通回复、最终收口或格式修复阶段。"""

    INITIAL = "initial"
    FINAL_JSON = "final-json"
    FORMAT_RETRY = "format-retry"


def _message_content(message: object) -> str:
    """从 OpenAI 风格 message 对象中读取 content 字符串。"""

    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, list):
        return json.dumps(content, ensure_ascii=False)
    return str(content or "")


def _request_messages(payload: object) -> list[object]:
    """从请求载荷中读取 messages 列表。"""

    if not isinstance(payload, dict):
        return []
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return []
    return list(messages)


def detect_phase(messages: list[object]) -> Phase:
    """根据最后一条用户消息判断当前请求处于哪个格式处理阶段。"""

    if not messages:
        return Phase.INITIAL
    last_content = _message_content(messages[-1])
    if "下面是模型输出" in last_content and "请只纠正 JSON 结构" in last_content:
        return Phase.FORMAT_RETRY
    if "请将上一条 assistant 候选回复转换" in last_content:
        return Phase.FINAL_JSON
    return Phase.INITIAL


def scenario_content(scenario: Scenario, phase: Phase) -> str:
    """根据测试场景与请求阶段返回固定 assistant content。"""

    if scenario == Scenario.VALID:
        return '[{"text":"こんにちは。","translation":"你好。","emotion":"happiness"}]'
    if scenario == Scenario.TOP_LEVEL_OBJECT_NEUTRAL:
        return '{"text":"你好。","emotion":"neutral"}'
    if scenario == Scenario.MISSING_TRANSLATION:
        return '[{"text":"こんにちは。","emotion":"happiness"}]'
    if scenario == Scenario.MISSING_TEXT:
        return '[{"translation":"你好。","emotion":"happiness"}]'
    if scenario == Scenario.PLAIN_TEXT:
        return "今天我不想返回 JSON。"
    if scenario == Scenario.EXTRA_FIELDS:
        return '[{"text":"你好。","translation":"你好。","emotion":"happiness","debug":"ignored"}]'
    if scenario == Scenario.LENIENT_RETRY:
        if phase == Phase.INITIAL:
            return "这是一条普通候选回复，会触发最终 JSON 收口。"
        return '[{"text":"こんにちは。","emotion":"neutral","debug":"ignored"}]'
    if scenario == Scenario.RAW_FALLBACK:
        if phase == Phase.INITIAL:
            return "这是一条普通候选回复，会触发最终 JSON 收口。"
        return "最终收口后仍然不是 JSON，用来测试原文兜底显示。"
    if scenario == Scenario.MISSING_TEXT_RETRY:
        if phase == Phase.INITIAL:
            return "这是一条普通候选回复，会触发最终 JSON 收口。"
        return '[{"translation":"你好。","emotion":"happiness"}]'
    raise ValueError(f"未知测试场景：{scenario}")


def _model_from_payload(payload: object) -> str:
    """从请求载荷读取模型名称，用于回显到响应中。"""

    if not isinstance(payload, dict):
        return "fake-model"
    model = payload.get("model")
    return str(model or "fake-model")


def build_chat_completion_response(payload: object, scenario: Scenario) -> dict[str, JSONValue]:
    """构造 OpenAI-compatible chat completions 响应。"""

    messages = _request_messages(payload)
    phase = detect_phase(messages)
    content = scenario_content(scenario, phase)
    return {
        "id": f"chatcmpl-fake-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": _model_from_payload(payload),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }


def build_models_response() -> dict[str, JSONValue]:
    """构造 OpenAI-compatible models 列表响应。"""

    return {
        "object": "list",
        "data": [
            {
                "id": "fake-model",
                "object": "model",
                "created": 0,
                "owned_by": "local-test",
            }
        ],
    }


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    """处理 OpenAI-compatible 测试请求。"""

    scenario: Scenario = Scenario.LENIENT_RETRY
    server_version = "FakeOpenAI/1.0"

    def _send_json(self, status_code: int, payload: dict[str, JSONValue]) -> None:
        """写出 JSON 响应。"""

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> object:
        """读取并解析请求 JSON；失败时返回空字典。"""

        raw_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_length)
        except ValueError:
            content_length = 0
        if content_length <= 0:
            return {}
        body = self.rfile.read(content_length)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:
        """处理模型列表与健康检查请求。"""

        if self.path.rstrip("/") == "/v1/models":
            self._send_json(200, build_models_response())
            return
        if self.path.rstrip("/") in {"", "/health", "/v1/health"}:
            self._send_json(200, {"status": "ok", "scenario": self.scenario.value})
            return
        self._send_json(404, {"error": {"message": f"未知路径：{self.path}"}})

    def do_POST(self) -> None:
        """处理 chat completions 请求。"""

        normalized_path = self.path.rstrip("/")
        if normalized_path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": f"未知路径：{self.path}"}})
            return
        payload = self._read_json_body()
        response = build_chat_completion_response(payload, self.scenario)
        self._send_json(200, response)
        phase = detect_phase(_request_messages(payload))
        print(
            f"[fake-openai] scenario={self.scenario.value} phase={phase.value} "
            f"model={_model_from_payload(payload)}",
            flush=True,
        )

    def log_message(self, format: str, *args: object) -> None:
        """关闭 BaseHTTPRequestHandler 默认访问日志。"""

        return


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(
        description=(
            "启动一个本地 OpenAI-compatible 假 LLM 服务，用于测试模型 JSON 格式错误处理。"
        )
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址。")
    parser.add_argument("--port", type=int, default=8008, help="监听端口。")
    parser.add_argument(
        "--scenario",
        choices=[scenario.value for scenario in Scenario],
        default=Scenario.LENIENT_RETRY.value,
        help="固定返回内容场景。",
    )
    return parser.parse_args()


def main() -> int:
    """启动 HTTP 服务。"""

    args = parse_args()
    FakeOpenAIHandler.scenario = Scenario(str(args.scenario))
    server = ThreadingHTTPServer((str(args.host), int(args.port)), FakeOpenAIHandler)
    print(
        "Fake OpenAI-compatible server is running.\n"
        f"  Base URL: http://{args.host}:{args.port}/v1\n"
        f"  Model: openai/fake-model\n"
        f"  API key: any non-empty string\n"
        f"  Scenario: {FakeOpenAIHandler.scenario.value}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping fake server.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
