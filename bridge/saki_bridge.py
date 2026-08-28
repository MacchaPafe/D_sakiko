"""
Saki Bridge — 纯 WebSocket 转发器（Electron 模式）

不再包含 queue_hook、motion_reader 等偷听逻辑。
只做一件事：从 main2.py 的 _bridge_queue 取事件 → WebSocket 广播给 Electron。

用法：
    from saki_bridge import Bridge
    bridge = Bridge(bridge_queue=_bridge_queue)
    bridge.start()
    # ... 程序退出时 ...
    bridge.shutdown()
"""
import asyncio
import threading
import sys
import os
from typing import Optional
from urllib.parse import unquote

# Python 3.9 兼容：全部用 Optional[X] 而非 X | None
bridge_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, bridge_dir)

from ws_server import WSServer

AUDIO_PORT = 9877  # HTTP 静态文件服务端口，供 Electron 加载音频


class Bridge:
    """简化的 Bridge 类，替代旧的 saki_launcher.py"""

    def __init__(self, bridge_queue, motion_queue=None, audio_base=None, renderer_fact_queue=None, renderer_command_queue=None,
                 ws_host="127.0.0.1", ws_port=9876, audio_host="127.0.0.1", audio_port=AUDIO_PORT):
        self.bridge_q = bridge_queue
        # Kept only for compatibility with callers that have not dropped the
        # parameter yet.  This bridge must never consume it: the old Pygame
        # events are renderer-local decisions, not authoritative commands.
        del motion_queue
        self.audio_base = audio_base  # 音频文件根目录，用于 HTTP 静态服务
        self.renderer_fact_queue = renderer_fact_queue
        self.renderer_command_queue = renderer_command_queue
        self.ws_host = ws_host
        self.ws_port = int(ws_port)
        self.audio_host = audio_host
        self.audio_port = int(audio_port)
        self.ws = WSServer(host=self.ws_host, port=self.ws_port,
                           on_message=self._on_renderer_message, on_disconnect=self._on_renderer_disconnect)
        self._reader_thread: Optional[threading.Thread] = None
        self._renderer_command_reader_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._snapshot_thinking = None
        self._renderer_ids_by_writer = {}
        self._audio_server = None

    async def _on_renderer_connect(self, writer):
        """Replay cached exact commands to one newly connected renderer."""
        commands = []
        if self._snapshot_thinking is not None:
            commands.append(self._untarget_command(self._snapshot_thinking))
        if commands:
            await self.ws.send_to(writer, 'renderer_snapshot', {'commands': commands})

    @staticmethod
    def _untarget_command(command):
        result = dict(command)
        data = dict(result.get('data') or {})
        data.pop('target_renderer_ids', None)
        data.pop('target_renderer_id', None)
        result['data'] = data
        return result

    def _cache_command(self, command):
        if not isinstance(command, dict):
            return
        if not self._targets_electron(command):
            return
        command_type = command.get('type')
        if command_type == 'thinking_changed':
            self._snapshot_thinking = command

    @staticmethod
    def _targets_electron(command):
        """Return whether an exact command applies to an Electron runtime."""
        data = command.get('data') or {}
        if not isinstance(data, dict):
            return True
        targets = data.get('target_renderer_ids')
        explicit_targets = []
        if isinstance(targets, (list, tuple, set, frozenset)):
            explicit_targets.extend(str(target) for target in targets if target)
        target = data.get('target_renderer_id')
        if target:
            explicit_targets.append(str(target))
        return not explicit_targets or any(
            target != 'pygame-renderer' for target in explicit_targets
        )

    async def _on_renderer_message(self, message, writer=None):
        """Forward renderer facts verbatim; the shared state consumes them."""
        if self.renderer_fact_queue is not None and isinstance(message, dict):
            if message.get('type') == 'renderer_hello' and writer is not None:
                data = message.get('data') or {}
                renderer_id = str(data.get('renderer_id') or '')
                if renderer_id:
                    self._renderer_ids_by_writer[writer] = {
                        'renderer_id': renderer_id,
                        'renderer_instance_id': str(data.get('renderer_instance_id') or ''),
                    }
                await self._on_renderer_connect(writer)
            self.renderer_fact_queue.put(message)

    async def _on_renderer_disconnect(self, writer):
        identity = self._renderer_ids_by_writer.pop(writer, None)
        if identity is None or self.renderer_fact_queue is None:
            return
        self.renderer_fact_queue.put({
            'v': 2,
            'type': 'renderer_disconnected',
            'data': identity,
        })

    def start(self):
        """启动 WebSocket 服务 + 事件 reader 线程"""
        loop = asyncio.new_event_loop()
        self._loop = loop
        server_thread = threading.Thread(
            target=self._run_server_loop, args=(loop,), daemon=True
        )
        server_thread.start()

    def _run_server_loop(self, loop):
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.ws.start())
        # 启动音频 HTTP 静态文件服务
        if self.audio_base:
            try:
                loop.run_until_complete(self._start_audio_server())
            except OSError as e:
                print(f'[Audio HTTP] Failed to start on port {self.audio_port}: {e}')
            except Exception as e:
                print(f'[Audio HTTP] Failed: {e}')
        # WS 服务启动后，开启 reader 线程
        self._reader_thread = threading.Thread(
            target=self._reader, daemon=True
        )
        self._reader_thread.start()
        if self.renderer_command_queue is not None:
            self._renderer_command_reader_thread = threading.Thread(
                target=self._renderer_command_reader, daemon=True
            )
            self._renderer_command_reader_thread.start()
        loop.run_forever()

    def _reader(self):
        """从 _bridge_queue 取事件 → WS 广播"""
        loop = self._loop
        while True:
            msg = self.bridge_q.get()
            if msg is None:  # shutdown() 发送的停止信号
                break
            if loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self.ws.broadcast(msg['type'], msg['data']), loop
                )

    def _renderer_command_reader(self):
        """Forward already-decided renderer commands without interpreting them."""
        loop = self._loop
        while True:
            command = self.renderer_command_queue.get()
            if command is None:
                break
            if loop is not None and isinstance(command, dict):
                self._cache_command(command)
                asyncio.run_coroutine_threadsafe(
                    self.ws.broadcast('live2d_command', {'command': command}), loop
                )

    async def _start_audio_server(self):
        """启动 HTTP 静态文件服务，供 Electron 加载音频文件"""
        audio_base = os.path.abspath(self.audio_base)
        print(f'[Audio HTTP] Base dir: {audio_base}', flush=True)
        print(f'[Audio HTTP] Starting on port {self.audio_port}...', flush=True)
        async def handle_audio(reader, writer):
            try:
                request = await asyncio.wait_for(reader.read(4096), timeout=5)
                request_str = request.decode('utf-8', errors='replace')
                first_line = request_str.split('\n')[0] if request_str else ''
                parts = first_line.split(' ')
                if len(parts) < 2:
                    writer.close()
                    return
                url_path = unquote(parts[1].split('?')[0])
                # 处理 CORS 预检请求
                if parts[0] == 'OPTIONS':
                    writer.write(b'HTTP/1.0 204 No Content\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, OPTIONS\r\n\r\n')
                    writer.close()
                    return
                # URL: /audio/xxx → audio_base/xxx, /model/xxx → audio_base/live2d_related/xxx
                if url_path.startswith('/model/'):
                    rel = url_path.lstrip('/').replace('model/', 'live2d_related/', 1)
                elif url_path.startswith('/audio/'):
                    rel = url_path.lstrip('/').replace('audio/', '', 1)
                else:
                    rel = url_path.lstrip('/')
                filepath = os.path.realpath(os.path.join(audio_base, rel))
                # commonpath avoids the /base vs /base-other prefix trap.
                try:
                    inside_base = os.path.commonpath((audio_base, filepath)) == audio_base
                except ValueError:
                    inside_base = False
                if not inside_base or not os.path.isfile(filepath):
                    writer.write(b'HTTP/1.0 404 Not Found\r\n\r\n')
                    writer.close()
                    return
                with open(filepath, 'rb') as f:
                    data = f.read()
                # 自动转换旧版 model.json 格式（rana → 13 组）
                if filepath.endswith('.model.json'):
                    try:
                        import json as _json, copy as _copy
                        model_data = _json.loads(data.decode('utf-8'))
                        if 'rana' in model_data.get('motions', {}):
                            new_data = _copy.deepcopy(model_data)
                            new_data.pop('controllers', None)
                            new_data.pop('hit_areas', None)
                            rana = model_data['motions']['rana']
                            motion_map = [
                                ('happiness', 0, 6), ('sadness', 6, 12), ('anger', 12, 18),
                                ('disgust', 18, 24), ('like', 24, 30), ('surprise', 30, 36),
                                ('fear', 36, 42), ('IDLE', 42, 51), ('text_generating', 51, 54),
                                ('bye', 54, 56), ('change_character', 56, 59),
                                ('idle_motion', 59, 60), ('talking_motion', 60, 61)
                            ]
                            new_motions = {}
                            for name, start, end in motion_map:
                                new_motions[name] = rana[start:end]
                            new_data['motions'] = new_motions
                            data = _json.dumps(new_data, ensure_ascii=False).encode('utf-8')
                    except Exception:
                        pass  # 转换失败则用原始数据
                # 根据扩展名决定 Content-Type
                ext = os.path.splitext(filepath)[1].lower()
                ct_map = {
                    '.json': 'application/json',
                    '.wav': 'audio/wav',
                    '.mp3': 'audio/mpeg',
                    '.png': 'image/png',
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.moc': 'application/octet-stream',
                    '.mtn': 'application/octet-stream',
                }
                content_type = ct_map.get(ext, 'application/octet-stream')
                response = b''.join([
                    b'HTTP/1.0 200 OK\r\n',
                    f'Content-Type: {content_type}\r\n'.encode(),
                    b'Access-Control-Allow-Origin: *\r\n',
                    f'Content-Length: {len(data)}\r\n'.encode(),
                    b'\r\n',
                    data,
                ])
                writer.write(response)
                await writer.drain()
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass
        self._audio_server = await asyncio.start_server(handle_audio, self.audio_host, self.audio_port)
        print(f'[Audio HTTP] Serving on http://{self.audio_host}:{self.audio_port}/audio/')

    def shutdown(self):
        """向 reader 线程发送停止信号"""
        if self.bridge_q is not None:
            self.bridge_q.put(None)
        if self.renderer_command_queue is not None:
            self.renderer_command_queue.put(None)
        loop = self._loop
        if loop is not None and loop.is_running():
            async def close_transport():
                try:
                    await self.ws.stop()
                    if self._audio_server is not None:
                        self._audio_server.close()
                        await self._audio_server.wait_closed()
                        self._audio_server = None
                finally:
                    loop.stop()
            asyncio.run_coroutine_threadsafe(close_transport(), loop)
