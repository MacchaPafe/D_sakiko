"""仅供本机宿主访问的固定 HTTP 操作，不依赖 Web 框架。"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import math
import os
import signal
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, cast

from .contracts import SchedulerConfig, SpeechRequest, SpeechSampling, VoiceProfile
from .scheduler import RequestError, Scheduler
from .worker import ProcessWorker
from .gpt_sovits_resources import GptSovitsResources
from .heartbeat import HostProbe, probe_host, watch_host
from .runtime import RunDirectory, process_exists


def object_value(value: object) -> Dict[str, object]:
    """拒绝数组和标量，确保报文是 JSON 对象。"""
    if not isinstance(value, dict):
        raise RequestError("invalid_request", "需要 JSON 对象")
    return cast(Dict[str, object], value)


def text_value(value: object) -> str:
    """验证必填文本，不隐式转换其他类型。"""
    if not isinstance(value, str) or not value.strip():
        raise RequestError("invalid_request", "缺少必填文本")
    return value


def integer_value(value: object, minimum: int = 0) -> int:
    """布尔值不能作为整数控制参数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RequestError("invalid_request", "整数参数无效")
    return value


def request_from_json(body: Dict[str, object], assets_root: Path) -> SpeechRequest:
    """校验文件路径和语言，避免 HTTP 入口变成任意本机文件读取。"""
    voice = object_value(body.get("voice"))
    paths: List[str] = []
    for key in ("gpt_weights_path", "sovits_weights_path", "reference_audio_path"):
        path = Path(text_value(voice.get(key))).resolve(strict=True)
        allowed = assets_root.resolve()
        if allowed not in path.parents:
            raise RequestError("invalid_request", "模型和参考音频必须位于宿主授权的资源目录")
        if not path.is_file():
            raise RequestError("invalid_request", "资源不是文件")
        paths.append(str(path))
    language = text_value(body.get("language"))
    reference_language = text_value(voice.get("reference_language"))
    supported = {"zh", "ja", "en", "ko", "yue", "all_zh", "all_ja", "all_ko", "all_yue", "auto", "auto_yue"}
    if language not in supported or reference_language not in supported:
        raise RequestError("invalid_request", "不支持的合成语言")
    speed_value = body.get("speed", 1.0)
    if isinstance(speed_value, bool) or not isinstance(speed_value, (int, float)) or not math.isfinite(speed_value) or speed_value <= 0:
        raise RequestError("invalid_request", "语速必须为有限正数")
    text = text_value(body.get("text"))
    if len(text) > 5000:
        raise RequestError("invalid_request", "单条语音文本过长")
    sampling_data = body.get("sampling")
    sampling: Optional[SpeechSampling] = None
    if sampling_data is not None:
        values = object_value(sampling_data)
        top_p = values.get("top_p")
        temperature = values.get("temperature")
        if isinstance(top_p, bool) or not isinstance(top_p, (int, float)) or not math.isfinite(top_p) or not 0 < top_p <= 1:
            raise RequestError("invalid_request", "top_p 必须在 (0, 1] 内")
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature) or temperature <= 0:
            raise RequestError("invalid_request", "temperature 必须为有限正数")
        sampling = SpeechSampling(integer_value(values.get("top_k"), 1), float(top_p), float(temperature), integer_value(values.get("seed"), -1), integer_value(values.get("sample_steps"), 1))
    return SpeechRequest(text, language, VoiceProfile(paths[0], paths[1], paths[2], text_value(voice.get("reference_text")), reference_language), integer_value(body.get("priority")), float(speed_value), integer_value(body.get("sentence_pause_ms", 300)), sampling)


async def serve(args: argparse.Namespace) -> None:
    """绑定回环地址并输出一次就绪通知，退出时关闭全部 worker。"""
    assets_root = Path(args.assets_root).resolve(strict=True)
    # Node 管道写入 UTF-8；Windows 的默认 stdin 编码可能不是 UTF-8。
    startup = object_value(json.loads(sys.stdin.buffer.readline().decode("utf-8")))
    if set(startup) != {"resources", "device", "precision", "architecture"}:
        raise ValueError("GPT-SoVITS 启动配置字段不完整或包含未知字段")
    architecture = text_value(startup["architecture"])
    device = text_value(startup["device"])
    precision = text_value(startup["precision"])
    if device not in ("cpu", "cuda", "mps") or precision not in ("float32", "float16"):
        raise ValueError("GPT-SoVITS 设备或精度无效")
    if precision == "float16" and device != "cuda":
        raise ValueError("当前设备不支持本实现的半精度推理")
    resources = GptSovitsResources.parse(startup["resources"], architecture)
    host_pid = int(os.environ.get("DSAKIKO_SPEECH_HOST_PID", str(os.getppid())))
    runtime = RunDirectory.create(Path(args.output), host_pid)
    output = runtime.path
    scheduler: Optional[Scheduler] = None
    published = False
    handoff_complete = False
    try:
        config = SchedulerConfig(args.concurrent, args.resident, str(output), 300000)

        def create_worker(voice: VoiceProfile) -> ProcessWorker:
            """每个实例使用独立的可写运行目录。"""
            return ProcessWorker(voice, resources, device, str(output / uuid.uuid4().hex), precision, architecture)

        scheduler = Scheduler(config, create_worker)
        token = os.environ.get("DSAKIKO_SPEECH_TOKEN", "")
        if not token:
            raise RuntimeError("缺少宿主访问凭据")
        stopping = asyncio.Event()
        host = HostProbe.parse(os.environ.get("DSAKIKO_HOST_PROBE", ""))

        async def probe() -> str:
            """网络探测在独立线程执行，不阻塞调度与关闭请求。"""
            assert host is not None
            return await asyncio.to_thread(probe_host, host)

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            """有限长度、单次请求的 HTTP 处理；任务状态只来自调度器。"""
            nonlocal handoff_complete
            status = 200
            result: object = {}
            try:
                headers_raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
                rows = headers_raw.decode("latin1").split("\r\n")
                method, target, _version = rows[0].split(" ", 2)
                headers = dict(row.split(":", 1) for row in rows[1:] if ":" in row)
                lowered = {key.lower(): value.strip() for key, value in headers.items()}
                if not hmac.compare_digest(lowered.get("authorization", ""), "Bearer " + token):
                    status = 401
                    raise RequestError("unauthorized", "无效的本机访问凭据")
                if stopping.is_set() and target != "/shutdown":
                    status = 503
                    raise RequestError("service_unavailable", "语音服务正在关闭")
                length = int(lowered.get("content-length", "0"))
                if length < 0 or length > 1000000:
                    raise RequestError("invalid_request", "请求过大")
                raw = await asyncio.wait_for(reader.readexactly(length), 10)
                body = object_value(json.loads(raw)) if raw else {}
                if method == "POST" and target == "/speech/tasks":
                    result = {"task_id": await scheduler.submit(request_from_json(body, assets_root))}
                    status = 202
                elif method == "GET" and target.startswith("/speech/tasks/"):
                    result = asdict(await scheduler.get_task(target.rsplit("/", 1)[-1]))
                elif method == "POST" and target in ("/speech/tasks/priority", "/speech/tasks/cancel"):
                    task_ids = body.get("task_ids")
                    if not isinstance(task_ids, list) or not all(isinstance(item, str) for item in task_ids):
                        raise RequestError("invalid_request", "task_ids 必须为文本数组")
                    ids = cast(List[str], task_ids)
                    if target.endswith("priority"):
                        await scheduler.set_priority(ids, integer_value(body.get("priority")))
                    else:
                        await scheduler.cancel(ids)
                    status = 204
                elif method == "POST" and target == "/shutdown":
                    # 宿主发送关闭请求前已等待全部音频导入，信号和心跳不能替代此确认。
                    handoff_complete = True
                    stopping.set()
                    status = 204
                else:
                    status = 404
                    raise RequestError("not_found", "未知操作")
            except RequestError as error:
                if status < 400:
                    status = 404 if error.code == "task_unavailable" else 400
                result = {"problem": {"code": error.code, "message": str(error), "retryable": False}}
            except Exception:
                status = 400
                result = {"problem": {"code": "invalid_request", "message": "请求格式或资源路径无效", "retryable": False}}
            payload = b"" if status == 204 else json.dumps(result, ensure_ascii=False).encode("utf-8")
            try:
                writer.write(f"HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode("ascii") + payload)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0, limit=65536)
        port = server.sockets[0].getsockname()[1]
        print(json.dumps({"ready": True, "port": port, "runId": scheduler.run_id}), flush=True)
        published = True
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, stopping.set)
            except NotImplementedError:
                pass
        heartbeat = asyncio.create_task(watch_host(stopping, probe)) if host is not None else None
        try:
            async with server:
                await stopping.wait()
        finally:
            stopping.set()
            if heartbeat is not None:
                await heartbeat
    finally:
        # 关闭失败时保留整次运行，不能删除仍可能被 worker 使用的文件。
        if scheduler is not None:
            await scheduler.close()
        if not published or handoff_complete or not process_exists(host_pid):
            await asyncio.to_thread(runtime.cleanup)
        else:
            logging.getLogger(__name__).warning("宿主仍存活且未确认音频交接，保留运行目录：%s", output)


def main() -> None:
    """读取宿主提供的固定启动参数，不扫描父仓库。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--assets-root", required=True)
    parser.add_argument("--concurrent", type=int, default=1)
    parser.add_argument("--resident", type=int, default=2)
    asyncio.run(serve(parser.parse_args()))


if __name__ == "__main__":
    main()
