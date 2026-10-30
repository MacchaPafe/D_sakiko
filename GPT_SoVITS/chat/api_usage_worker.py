"""额度脚本独立进程；只接受 JSON 输入，不向 JavaScript 注册宿主能力。"""
from __future__ import annotations

import json
import sys

from api_usage import MAX_BYTES, UsageError, origin, redact


def emit(value: dict) -> None:
    text = json.dumps(value, ensure_ascii=True, allow_nan=False)
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise UsageError("查询结果超过 1 MiB。")
    print(text, flush=True)


def run(payload: dict) -> None:
    import quickjs
    import requests

    variables = payload["variables"]
    context = quickjs.Context()
    context.set_memory_limit(16 * 1024 * 1024)
    context.set_max_stack_size(256 * 1024)
    context.set_time_limit(5)
    code = payload["code"]
    if len(code.encode("utf-8")) > 65536:
        raise UsageError("查询脚本超过 64 KiB。")
    for name, value in variables.items():
        # CC Switch 占位符位于 JS 字符串中；同时转义双引号、单引号和模板字符串。
        escaped = json.dumps(str(value), ensure_ascii=True)[1:-1].replace("'", "\\'").replace("`", "\\`").replace("${", "\\${")
        code = code.replace("{{" + name + "}}", escaped)
    context.eval("var __usageConfig = " + code + ";")
    if context.eval("typeof __usageConfig.extractor") != "function":
        raise UsageError("脚本缺少 extractor(response) 函数。")
    request = json.loads(context.eval("JSON.stringify(__usageConfig.request)"))
    if not isinstance(request, dict) or not isinstance(request.get("url"), str):
        raise UsageError("脚本缺少有效的 request.url。")
    request_origin = origin(request["url"])
    if any(value and value in request_origin for value in (variables.get("apiKey"), variables.get("accessToken"))):
        raise UsageError("查询凭据请放在请求头或请求体中，而非目标主机名。")
    method = str(request.get("method", "GET")).upper()
    if method not in {"GET", "POST"}:
        raise UsageError("额度查询只接受 GET 或 POST 请求。")
    headers = request.get("headers", {})
    if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items()):
        raise UsageError("request.headers 须为文本键值对象。")
    body = request.get("body")
    if body is not None and not isinstance(body, str):
        raise UsageError("request.body 须为字符串。")
    # 只发送不含路径／查询参数／认证信息的源，等待父进程批准后再联网。
    emit({"stage": "request", "url": request_origin})
    permission = json.loads(sys.stdin.readline(MAX_BYTES))
    if permission.get("continue") is not True:
        return
    with requests.Session() as session:
        with session.request(method, request["url"], headers=headers, data=body,
                             timeout=(min(3, payload["timeout"]), payload["timeout"]),
                             allow_redirects=False, stream=True) as response:
            if not 200 <= response.status_code < 300:
                raise UsageError(f"服务商返回 HTTP {response.status_code}，请检查凭据、查询地址或稍后重试。")
            chunks, size = [], 0
            for chunk in response.iter_content(8192):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise UsageError("服务商响应超过 1 MiB。")
                chunks.append(chunk)
            try:
                data = json.loads(b"".join(chunks).decode("utf-8-sig"))
            except (ValueError, UnicodeError):
                raise UsageError("服务商返回的内容不是有效 JSON。") from None
    context.eval("var __usageResponse = " + json.dumps(data, ensure_ascii=True, allow_nan=False) + ";")
    # 先检查特殊数值，避免 JSON.stringify 把 NaN/Infinity 悄悄变成 null。
    result_json = context.eval("""JSON.stringify(__usageConfig.extractor(__usageResponse), function(k,v) {
      if (typeof v === 'number' && !Number.isFinite(v)) throw Error('查询结果包含非有限数字');
      return v;
    })""")
    if result_json is None or result_json == "{}":
        raise UsageError("脚本没有返回额度数据。")
    emit({"data": json.loads(result_json)})


if __name__ == "__main__":
    payload = {}
    try:
        line = sys.stdin.readline(MAX_BYTES + 1)
        if len(line.encode("utf-8")) > MAX_BYTES:
            raise UsageError("查询配置过大。")
        payload = json.loads(line)
        run(payload)
    except Exception as error:
        variables = payload.get("variables", {})
        emit({"error": redact(error, (variables.get("apiKey", ""), variables.get("accessToken", "")))})
