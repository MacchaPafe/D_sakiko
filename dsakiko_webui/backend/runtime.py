from __future__ import annotations

import os
import queue
import logging
import multiprocessing
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .assets import AssetRegistry, PROJECT_ROOT
from .protocol import ProtocolError
from .uploads import PendingImageStore


GPT_ROOT = PROJECT_ROOT / "GPT_SoVITS"
logger = logging.getLogger(__name__)


class HeadlessRuntime:
    """把现有聊天与语音模块组合成不依赖 Qt 的 WebUI 运行时。"""

    def __init__(
        self,
        assets: AssetRegistry | None = None,
        uploads: PendingImageStore | None = None,
    ) -> None:
        self.assets = assets or AssetRegistry()
        self.uploads = uploads or PendingImageStore()
        self.status = "starting"
        self.status_stage = "waiting"
        self.status_message = "等待初始化。"
        self.status_progress: float | None = 0.0
        self.error_message: str | None = None
        self.session_id = f"session_{uuid.uuid4().hex}"
        self.phase = "idle"
        self.active_chat_id: str | None = None
        self.active_turn_id: str | None = None
        self._lock = threading.RLock()
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stopping = threading.Event()
        self._client_message_turns: dict[tuple[str, str], str] = {}
        self._reported_orphan_chat_ids: set[str] = set()

        self.characters: list[Any] = []
        self.user_personas: list[Any] = []
        self.character_by_id: dict[str, Any] = {}
        self.character_by_name: dict[str, Any] = {}
        self.character_entities: dict[str, dict[str, Any]] = {}
        self.chat_manager: Any = None
        self.dp_chat: Any = None
        self.audio_gen: Any = None
        self._voice_settings_by_character: dict[str, dict[str, float]] = {}

    def initialize(self) -> None:
        try:
            self._set_status("starting", "loading_config", "正在加载角色与对话。", 0.15)
            multiprocessing.set_start_method("spawn", force=True)
            os.chdir(GPT_ROOT)
            if str(GPT_ROOT) not in sys.path:
                sys.path.insert(0, str(GPT_ROOT))

            import audio_generator
            import character
            import dp_local2
            from chat.chat import get_chat_manager

            character_manager = character.GetCharacterAttributes()
            self.characters = list(character_manager.character_class_list)
            self.user_personas = list(character_manager.user_characters)
            self.character_by_id = {item.character_folder_name: item for item in self.characters}
            self.character_by_name = {item.character_name: item for item in self.characters}
            self.character_entities = {
                item.character_folder_name: self.assets.register_character(item)
                for item in self.characters
            }
            self.chat_manager = get_chat_manager()

            self._set_status("starting", "loading_llm", "正在初始化聊天运行时。", 0.45)
            self.dp_chat = dp_local2.DSLocalAndVoiceGen(self.characters, self.chat_manager)
            valid_chats = [
                chat for chat in self.chat_manager.single_character_chats()
                if chat.get_character_name() in self.character_by_name
            ]
            if valid_chats:
                self.dp_chat.switch_chat(valid_chats[0].chat_id)
            elif self.characters:
                chat = self.chat_manager.create_single_character_chat(self.characters[0])
                self.chat_manager.save()
                self.dp_chat.switch_chat(chat.chat_id)

            self.text_queue: queue.Queue[Any] = queue.Queue()
            self.is_audio_play_complete: queue.Queue[Any] = queue.Queue()
            self.is_text_generating_queue: queue.Queue[Any] = queue.Queue()
            self.dp_event_queue: queue.Queue[Any] = queue.Queue()
            self.command_queue: queue.Queue[Any] = queue.Queue()
            self.message_queue: queue.Queue[Any] = queue.Queue()
            self.char_state_queue: queue.Queue[Any] = queue.Queue()
            self.change_char_queue: queue.Queue[Any] = queue.Queue()

            self._set_status("starting", "loading_tts", "正在启动语音模型进程。", 0.7)
            self.audio_gen = audio_generator.AudioGenerate()
            self.audio_gen.initialize(self.characters, self.message_queue)
            self._voice_settings_by_character = {
                item.character_name: {
                    "speech_speed": float(self.audio_gen.speed),
                    "sentence_pause_seconds": float(self.audio_gen.pause_second),
                }
                for item in self.characters
            }
            try:
                self.audio_gen.request_preload_character(self.dp_chat.get_current_character())
            except ValueError:
                pass

            threading.Thread(
                target=self.dp_chat.text_generator,
                args=(
                    self.text_queue,
                    self.is_audio_play_complete,
                    self.is_text_generating_queue,
                    self.dp_event_queue,
                    self.command_queue,
                    self.message_queue,
                    self.char_state_queue,
                    self.change_char_queue,
                    self.audio_gen,
                ),
                name="WebUIDialogue",
                daemon=True,
            ).start()
            threading.Thread(target=self._run_tts_pipeline, name="WebUITTS", daemon=True).start()
            threading.Thread(target=self._forward_dp_events, name="WebUIEvents", daemon=True).start()

            self._restore_client_message_index()
            self._set_status("ready", "ready", "WebUI 后端已就绪。", 1.0)
        except Exception as exc:
            self.error_message = str(exc)
            self._set_status("error", "initialization_failed", "后端初始化失败，请查看电脑端日志。", None)
            raise

    def _set_status(self, state: str, stage: str, message: str, progress: float | None) -> None:
        self.status = state
        self.status_stage = stage
        self.status_message = message
        self.status_progress = progress
        self.events.put({
            "type": "runtime_status",
            "chat_id": None,
            "turn_id": None,
            "request_id": None,
            "data": {"state": state, "stage": stage, "message": message, "progress": progress},
        })
        if state == "ready":
            self.events.put({
                "type": "runtime_ready",
                "chat_id": None,
                "turn_id": None,
                "request_id": None,
                "data": {
                    "mode": "web",
                    "capabilities": self.capabilities(),
                },
            })

    def runtime_status_event(self) -> dict[str, Any]:
        return {
            "type": "runtime_status",
            "chat_id": None,
            "turn_id": None,
            "request_id": None,
            "data": {
                "state": self.status,
                "stage": self.status_stage,
                "message": self.status_message,
                "progress": self.status_progress,
            },
        }

    def capabilities(self) -> dict[str, bool]:
        return {
            "tts": True,
            "translation": True,
            "backgrounds": True,
            "cancel_turn": True,
            "image_input": bool(
                self.dp_chat is not None
                and self.dp_chat._current_model_supports_vision()
            ),
        }

    def runtime_ready_event(self) -> dict[str, Any] | None:
        if self.status != "ready":
            return None
        return {
            "type": "runtime_ready",
            "chat_id": None,
            "turn_id": None,
            "request_id": None,
            "data": {
                "mode": "web",
                "capabilities": self.capabilities(),
            },
        }

    def _restore_client_message_index(self) -> None:
        for chat in self.chat_manager.single_character_chats():
            for item in self._message_meta(chat):
                client_message_id = item.get("client_message_id")
                turn_id = item.get("turn_id")
                if isinstance(client_message_id, str) and isinstance(turn_id, str):
                    self._client_message_turns[(chat.chat_id, client_message_id)] = turn_id

    def _message_meta(self, chat: Any) -> list[dict[str, Any]]:
        raw = chat.meta.extra.get("webui_messages")
        if not isinstance(raw, list):
            raw = []
            chat.meta.extra["webui_messages"] = raw
        while len(raw) < len(chat.message_list):
            index = len(raw)
            message = chat.message_list[index]
            raw.append({
                "id": f"msg_{chat.chat_id}_{index}",
                "created_at": 0,
                "turn_id": None,
                "client_message_id": None,
                "sequence": 0,
                "role": "user" if message.character_name == "User" else "assistant",
            })
        return raw

    def _persona_entity(self, chat: Any) -> dict[str, str]:
        snapshot = getattr(chat.prompt_generator, "user_persona", None)
        if snapshot is None:
            return {"id": "default", "name": "默认身份"}
        return {"id": snapshot.persona_id, "name": snapshot.name}

    def _character_for_chat(self, chat: Any) -> Any:
        character = self.character_by_name.get(chat.get_character_name())
        if character is None:
            raise ProtocolError("CHARACTER_NOT_FOUND", "这条会话对应的角色已不存在。")
        return character

    def _serialize_message(self, chat: Any, index: int) -> dict[str, Any]:
        message = chat.message_list[index]
        meta = self._message_meta(chat)[index]
        role = "user" if message.character_name == "User" else "assistant"
        audio_url = None
        attachments = []
        if role == "assistant" and message.audio_path and message.audio_path != "NO_AUDIO":
            path = Path(message.audio_path)
            if path.is_file() and path.name != "silence.wav":
                try:
                    media_id = self.assets.register_media(path, "audio")
                    audio_url = f"/api/v1/media/{media_id}"
                except ValueError:
                    logger.warning("WebUI 拒绝提供允许目录之外的历史音频。")
        if message.attachments:
            from chat.attachments import resolve_attachment_path

            for attachment in message.attachments:
                if not attachment.is_image():
                    continue
                image_url = None
                image_path = resolve_attachment_path(attachment.path)
                if image_path.is_file():
                    try:
                        media_id = self.assets.register_media(image_path, "attachment")
                        image_url = f"/api/v1/media/{media_id}"
                    except ValueError:
                        logger.warning("WebUI 拒绝提供允许目录之外的图片附件。")
                attachments.append({
                    "type": "image",
                    "mime_type": attachment.mime_type,
                    "original_name": attachment.original_name,
                    "image_url": image_url,
                })
        return {
            "id": str(meta.get("id") or f"msg_{chat.chat_id}_{index}"),
            "role": role,
            "text": message.text,
            "translation": message.translation,
            "created_at": int(meta.get("created_at") or 0),
            "turn_id": meta.get("turn_id") if isinstance(meta.get("turn_id"), str) else None,
            "client_message_id": (
                meta.get("client_message_id")
                if isinstance(meta.get("client_message_id"), str) else None
            ),
            "sequence": int(meta.get("sequence") or 0),
            "emotion": message.emotion.as_string() if role == "assistant" else None,
            "audio_url": audio_url,
            "audio_duration_ms": None,
            "attachments": attachments,
            "status": "ready",
        }

    def chat_list_snapshot(self) -> dict[str, Any]:
        with self._lock:
            chats = []
            source = self.chat_manager.single_character_chats()
            for fallback_order, chat in enumerate(source):
                character = self.character_by_name.get(chat.get_character_name())
                if character is None:
                    if chat.chat_id not in self._reported_orphan_chat_ids:
                        logger.warning("WebUI 跳过缺少对应角色的会话：%s", chat.chat_id)
                        self._reported_orphan_chat_ids.add(chat.chat_id)
                    continue
                meta = self._message_meta(chat)
                last_message = chat.message_list[-1] if chat.message_list else None
                last_active = int(meta[-1].get("created_at") or 0) if meta else 0
                chats.append({
                    "chat_id": chat.chat_id,
                    "name": chat.name,
                    "character": self.character_entities[character.character_folder_name],
                    "user_persona": self._persona_entity(chat),
                    "last_message_preview": (
                        (last_message.translation or last_message.text) if last_message else "暂无消息"
                    ),
                    "last_active_at": last_active or (len(source) - fallback_order),
                    "status": self.phase if chat.chat_id == self.active_chat_id else "idle",
                })
            chats.sort(key=lambda item: item["last_active_at"], reverse=True)
            return {
                "current_chat_id": self.dp_chat.current_chat_id,
                "chats": chats,
                "characters": list(self.character_entities.values()),
                "user_personas": [
                    {
                        "id": str(item.persona_id),
                        "name": "默认身份" if item.is_default_user else item.effective_character_name,
                        "description": (
                            "AI 不会知道任何关于你的信息。"
                            if item.is_default_user
                            else item.effective_character_description
                        ),
                        "is_default": bool(item.is_default_user),
                    }
                    for item in self.user_personas
                ],
            }

    def state_snapshot(self) -> dict[str, Any]:
        with self._lock:
            chat = self.dp_chat.current_chat
            character = self._character_for_chat(chat)
            return {
                "current_chat_id": chat.chat_id,
                "chat_name": chat.name,
                "character": self.character_entities[character.character_folder_name],
                "user_persona": self._persona_entity(chat),
                "messages": [self._serialize_message(chat, index) for index in range(len(chat.message_list))],
                "phase": self.phase,
                "turn_id": self.active_turn_id,
                "background": self.assets.current_background(),
                "backgrounds": self.assets.backgrounds,
            }

    def handle_command(self, command_type: str, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if self.status != "ready" and command_type != "ping":
            raise ProtocolError("RUNTIME_NOT_READY", "后端仍在初始化，请稍后重试。", True)
        if command_type == "sync":
            events = [self._local_event("chat_list_snapshot", self.chat_list_snapshot())]
            if self.chat_manager.single_character_chats():
                events.append(self._local_event("state_snapshot", self.state_snapshot(), self.dp_chat.current_chat_id))
            return {"accepted": True}, events
        if command_type == "get_chat_list":
            return {"accepted": True}, [self._local_event("chat_list_snapshot", self.chat_list_snapshot())]
        if command_type == "ping":
            return {"accepted": True}, [self._local_event("pong", {
                "client_time": payload.get("client_time"),
                "server_time": int(time.time()),
            })]
        if command_type == "next_background":
            background = self.assets.next_background()
            return {"background_id": background["id"]}, [self._local_event("background_changed", {
                "background": background,
                "backgrounds": self.assets.backgrounds,
            })]

        with self._lock:
            if command_type == "send_message":
                return self._send_message(payload)
            if command_type == "cancel_turn":
                return self._cancel_turn(payload)
            if command_type == "switch_chat":
                return self._switch_chat(payload)
            if command_type == "create_chat":
                return self._create_chat(payload)
        raise ProtocolError("INVALID_COMMAND", "不支持这个命令。")

    def _send_message(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        chat_id = payload.get("chat_id")
        client_message_id = payload.get("client_message_id")
        text = payload.get("text")
        raw_upload_ids = payload.get("image_upload_ids", [])
        if not isinstance(chat_id, str) or chat_id != self.dp_chat.current_chat_id:
            raise ProtocolError("CHAT_MISMATCH", "当前会话已经变化，请重新发送。", True)
        if not isinstance(client_message_id, str) or not client_message_id:
            raise ProtocolError("INVALID_MESSAGE", "消息缺少客户端编号。")
        if not isinstance(raw_upload_ids, list) or any(
            not isinstance(upload_id, str) or not upload_id
            for upload_id in raw_upload_ids
        ):
            raise ProtocolError("INVALID_MESSAGE", "图片附件编号无效。")
        upload_ids = list(raw_upload_ids)
        if not isinstance(text, str) or len(text.strip()) > 10_000 or (not text.strip() and not upload_ids):
            raise ProtocolError("INVALID_MESSAGE", "消息需要包含文字或图片，文字最多 10000 个字符。")
        previous_turn = self._client_message_turns.get((chat_id, client_message_id))
        if previous_turn:
            return {
                "chat_id": chat_id,
                "turn_id": previous_turn,
                "client_message_id": client_message_id,
                "deduplicated": True,
            }, [self._local_event("state_snapshot", self.state_snapshot(), chat_id, previous_turn)]
        if self.phase != "idle":
            raise ProtocolError("CHAT_BUSY", "请等待当前回复完成。", True, {
                "active_chat_id": self.active_chat_id,
                "active_turn_id": self.active_turn_id,
            })

        if upload_ids and not self.dp_chat._current_model_supports_vision():
            raise ProtocolError(
                "IMAGE_INPUT_UNSUPPORTED",
                "当前模型不支持图片输入，请在电脑端切换支持视觉的模型。",
            )
        try:
            pending_images = self.uploads.resolve(upload_ids)
        except ValueError as exc:
            raise ProtocolError("INVALID_IMAGE_UPLOAD", str(exc)) from exc

        from chat.attachments import import_image_attachment, resolve_attachment_path
        from chat.chat import Message
        from emotion_enum import EmotionEnum

        turn_id = f"turn_{uuid.uuid4().hex}"
        chat = self.dp_chat.current_chat
        imported_attachments = []
        try:
            imported_attachments = [
                import_image_attachment(chat_id, str(item.path))
                for item in pending_images
            ]
        except Exception as exc:
            for attachment in imported_attachments:
                resolve_attachment_path(attachment.path).unlink(missing_ok=True)
            logger.warning("WebUI 图片导入失败：%s", exc)
            raise ProtocolError("IMAGE_IMPORT_FAILED", "图片导入失败，请重新选择。") from exc

        user_message = Message(
            "User",
            text.strip(),
            "",
            EmotionEnum.HAPPINESS,
            "",
            attachments=imported_attachments,
        )
        chat.add_message(user_message)
        meta = self._message_meta(chat)
        meta[-1] = {
            "id": f"msg_{uuid.uuid4().hex}",
            "created_at": int(time.time()),
            "turn_id": turn_id,
            "client_message_id": client_message_id,
            "sequence": 0,
            "role": "user",
        }
        try:
            self.chat_manager.save()
        except Exception as exc:
            chat.message_list.pop()
            if len(meta) > len(chat.message_list):
                meta.pop()
            for attachment in imported_attachments:
                resolve_attachment_path(attachment.path).unlink(missing_ok=True)
            raise ProtocolError("INTERNAL_ERROR", "消息保存失败，请重试。", True) from exc

        self.uploads.discard(upload_ids)
        self._client_message_turns[(chat_id, client_message_id)] = turn_id
        self.phase = "thinking"
        self.active_chat_id = chat_id
        self.active_turn_id = turn_id
        self.command_queue.put({
            "type": "send_message",
            "chat_id": chat_id,
            "turn_id": turn_id,
            "text": text.strip(),
            "append_user_message": False,
        })
        return {
            "chat_id": chat_id,
            "turn_id": turn_id,
            "client_message_id": client_message_id,
            "deduplicated": False,
        }, [
            self._local_event("user_message_ack", {"message": self._serialize_message(chat, len(chat.message_list) - 1)}, chat_id, turn_id),
            self._local_event("assistant_turn_phase", {"phase": "thinking"}, chat_id, turn_id),
            self._local_event("chat_list_snapshot", self.chat_list_snapshot()),
        ]

    def _cancel_turn(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if self.phase == "idle" or not self.active_turn_id:
            raise ProtocolError("TURN_NOT_FOUND", "当前没有正在生成的回复。")
        if payload.get("chat_id") != self.active_chat_id or payload.get("turn_id") != self.active_turn_id:
            raise ProtocolError("TURN_MISMATCH", "要取消的回复已不是当前回复。", True)
        self.dp_chat.request_cancel_turn(self.active_chat_id, self.active_turn_id)
        return {
            "chat_id": self.active_chat_id,
            "turn_id": self.active_turn_id,
            "cancellation_requested": True,
        }, []

    def _switch_chat(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if self.phase != "idle":
            raise ProtocolError("CHAT_BUSY", "当前回复完成后才能切换会话。", True)
        current_character = self.dp_chat.get_current_character()
        self._voice_settings_by_character[current_character.character_name] = {
            "speech_speed": float(self.audio_gen.speed),
            "sentence_pause_seconds": float(self.audio_gen.pause_second),
        }
        chat_id = payload.get("chat_id")
        if not isinstance(chat_id, str) or not self.dp_chat.switch_chat(chat_id):
            raise ProtocolError("CHAT_NOT_FOUND", "这条会话已经不存在。")
        voice = self._voice_settings_by_character[self.dp_chat.get_current_character().character_name]
        self.audio_gen.speed = voice["speech_speed"]
        self.audio_gen.pause_second = voice["sentence_pause_seconds"]
        self.audio_gen.request_preload_character(self.dp_chat.get_current_character())
        return {"current_chat_id": chat_id}, [
            self._local_event("chat_list_snapshot", self.chat_list_snapshot()),
            self._local_event("state_snapshot", self.state_snapshot(), chat_id),
        ]

    def _create_chat(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if self.phase != "idle":
            raise ProtocolError("CHAT_BUSY", "当前回复完成后才能新建会话。", True)
        character = self.character_by_id.get(payload.get("character_id"))
        if character is None:
            raise ProtocolError("CHARACTER_NOT_FOUND", "没有找到这个角色。")
        name = payload.get("name")
        if name is not None and (not isinstance(name, str) or len(name.strip()) > 80):
            raise ProtocolError("INVALID_COMMAND", "会话名称最多 80 个字符。")
        persona_id = payload.get("user_persona_id") or "default"
        persona = next((item for item in self.user_personas if item.persona_id == persona_id), None)
        if persona is None:
            raise ProtocolError("USER_PERSONA_NOT_FOUND", "没有找到这个对话身份。")
        chat = self.chat_manager.create_single_character_chat(character, name=name, user_character=persona)
        self.chat_manager.save()
        self.dp_chat.switch_chat(chat.chat_id)
        voice = self._voice_settings_by_character[character.character_name]
        self.audio_gen.speed = voice["speech_speed"]
        self.audio_gen.pause_second = voice["sentence_pause_seconds"]
        self.audio_gen.request_preload_character(character)
        return {"chat_id": chat.chat_id, "current_chat_id": chat.chat_id}, [
            self._local_event("chat_list_snapshot", self.chat_list_snapshot()),
            self._local_event("state_snapshot", self.state_snapshot(), chat.chat_id),
        ]

    @staticmethod
    def _local_event(
        event_type: str,
        data: dict[str, Any],
        chat_id: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "type": event_type,
            "chat_id": chat_id,
            "turn_id": turn_id,
            "request_id": None,
            "data": data,
        }

    def _forward_dp_events(self) -> None:
        while not self._stopping.is_set():
            raw = self.dp_event_queue.get()
            if not isinstance(raw, dict):
                continue
            event_type = raw.get("type")
            chat_id = str(raw.get("chat_id") or "") or None
            turn_id = str(raw.get("turn_id") or "") or None
            if event_type == "assistant_turn_phase":
                phase = "thinking" if raw.get("phase") == "llm" else str(raw.get("phase") or "thinking")
                if phase == "thinking" and self.phase == "thinking":
                    continue
                self.phase = phase
                self.events.put(self._local_event("assistant_turn_phase", {"phase": phase}, chat_id, turn_id))
            elif event_type == "assistant_turn_error":
                try:
                    self._rollback_failed_user_message(chat_id, turn_id)
                except Exception:
                    logger.exception("WebUI 推理失败消息回滚未完成")
                self.events.put(self._local_event("error", {
                    "error": {
                        "code": "LLM_FAILED",
                        "message": str(raw.get("message") or "角色回复生成失败，请稍后重试。"),
                        "retryable": True,
                        "details": {},
                    },
                }, chat_id, turn_id))
            elif event_type == "assistant_turn_complete" and raw.get("status") in {"error", "cancelled"}:
                status = str(raw.get("status"))
                self._finish_turn(chat_id, turn_id)
                self.events.put(self._local_event("assistant_turn_complete", {
                    "status": status,
                    "segment_count": self._turn_segment_count(chat_id, turn_id),
                    "error": None,
                }, chat_id, turn_id))
                self.events.put(self._local_event("chat_list_snapshot", self.chat_list_snapshot()))

    def _rollback_failed_user_message(self, chat_id: str | None, turn_id: str | None) -> None:
        """推理未产生角色回复时，仅从持久记录中撤回本轮 WebUI 用户消息。"""
        if not chat_id or not turn_id or self._turn_segment_count(chat_id, turn_id):
            return
        with self._lock:
            chat = self.chat_manager.get_chat_by_id(chat_id)
            if chat is None:
                return
            meta = self._message_meta(chat)
            index = next((
                index
                for index, item in enumerate(meta)
                if item.get("turn_id") == turn_id and item.get("role") == "user"
            ), None)
            if index is None or index >= len(chat.message_list):
                return

            from chat.attachments import resolve_attachment_path

            message = chat.message_list[index]
            client_message_id = meta[index].get("client_message_id")
            for attachment in message.attachments:
                resolve_attachment_path(attachment.path).unlink(missing_ok=True)
            chat.delete_message_at(index)
            meta.pop(index)
            if isinstance(client_message_id, str):
                self._client_message_turns.pop((chat_id, client_message_id), None)
            self.chat_manager.save()

    def settings_snapshot(self) -> dict[str, Any]:
        with self._lock:
            if self.status != "ready":
                raise ProtocolError("RUNTIME_NOT_READY", "后端仍在初始化，请稍后重试。", True)
            character = self.dp_chat.get_current_character()
            voice = self._voice_settings_by_character.setdefault(character.character_name, {
                "speech_speed": float(self.audio_gen.speed),
                "sentence_pause_seconds": float(self.audio_gen.pause_second),
            })

            from qconfig import PROVIDER_FRIENDLY_NAME_MAP, d_sakiko_config

            d_sakiko_config.reload_from_disk()
            models = d_sakiko_config.llm_api_model.value
            keys = d_sakiko_config.llm_api_key.value
            base_urls = d_sakiko_config.llm_api_base_url.value
            current_provider = str(d_sakiko_config.llm_api_provider.value or "")
            options = [{
                "id": "default_deepseek",
                "label": "DeepSeek 公共 API",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
            }]
            if isinstance(models, dict):
                for provider, model in models.items():
                    provider = str(provider or "").strip()
                    model = str(model or "").strip()
                    configured = (
                        provider == current_provider
                        or (isinstance(keys, dict) and provider in keys)
                        or (isinstance(base_urls, dict) and provider in base_urls)
                    )
                    if provider and model and configured:
                        friendly = PROVIDER_FRIENDLY_NAME_MAP.get(provider, provider)
                        options.append({
                            "id": f"provider:{provider}",
                            "label": friendly,
                            "provider": provider,
                            "model": model,
                        })
            custom_model = str(d_sakiko_config.custom_llm_api_model.value or "").strip()
            if custom_model and (
                d_sakiko_config.enable_custom_llm_api_provider.value
                or (
                    str(d_sakiko_config.custom_llm_api_url.value or "").strip()
                    and str(d_sakiko_config.custom_llm_api_key.value or "").strip()
                )
            ):
                options.append({
                    "id": "custom",
                    "label": "自定义 API",
                    "provider": "custom",
                    "model": custom_model,
                })

            if d_sakiko_config.use_default_deepseek_api.value:
                selected_id = "default_deepseek"
            elif d_sakiko_config.enable_custom_llm_api_provider.value:
                selected_id = "custom"
            else:
                selected_id = f"provider:{current_provider}"
            return {
                "voice": {
                    "character_id": character.character_folder_name,
                    "character_name": character.character_name,
                    **voice,
                },
                "llm": {"selected_id": selected_id, "options": options},
                "capabilities": self.capabilities(),
            }

    def update_settings(
        self,
        *,
        speech_speed: float | None,
        sentence_pause_seconds: float | None,
        llm_choice_id: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            if self.phase != "idle":
                raise ProtocolError("CHAT_BUSY", "回复完成后才能修改设置。", True)
            snapshot = self.settings_snapshot()
            if speech_speed is not None:
                self.audio_gen.speed = float(speech_speed)
            if sentence_pause_seconds is not None:
                self.audio_gen.pause_second = float(sentence_pause_seconds)
            character = self.dp_chat.get_current_character()
            self._voice_settings_by_character[character.character_name] = {
                "speech_speed": float(self.audio_gen.speed),
                "sentence_pause_seconds": float(self.audio_gen.pause_second),
            }

            if llm_choice_id is not None:
                valid_ids = {item["id"] for item in snapshot["llm"]["options"]}
                if llm_choice_id not in valid_ids:
                    raise ProtocolError("INVALID_SETTING", "选择的大模型配置已不存在，请刷新后重试。")
                from qconfig import create_d_sakiko_config_snapshot, d_sakiko_config

                with d_sakiko_config as config:
                    config.set(config.use_default_deepseek_api, llm_choice_id == "default_deepseek")
                    config.set(config.enable_custom_llm_api_provider, llm_choice_id == "custom")
                    if llm_choice_id.startswith("provider:"):
                        config.set(config.llm_api_provider, llm_choice_id.removeprefix("provider:"))
                self.dp_chat.d_sakiko_config = create_d_sakiko_config_snapshot()
            return self.settings_snapshot()

    def _run_tts_pipeline(self) -> None:
        while not self._stopping.is_set():
            payload = self.text_queue.get()
            if payload == "bye":
                return
            if not isinstance(payload, dict) or payload.get("type") != "model_response":
                continue
            self._process_model_response(payload)

    def _process_model_response(self, payload: dict[str, Any]) -> None:
        chat_id = str(payload.get("chat_id") or "")
        turn_id = str(payload.get("turn_id") or "")
        segments = payload.get("segments")
        chat = self.chat_manager.get_chat_by_id(chat_id)
        character = self.character_by_name.get(payload.get("character_name"))
        if chat is None or character is None or not isinstance(segments, list):
            self.is_audio_play_complete.put("yes")
            return

        self.phase = "tts"
        self.events.put(self._local_event("assistant_turn_phase", {"phase": "tts"}, chat_id, turn_id))
        had_tts_error = False
        emitted = 0
        for sequence, segment in enumerate(segments):
            if not isinstance(segment, dict) or self.dp_chat.is_turn_cancelled(chat_id, turn_id):
                break
            message_index = segment.get("message_index")
            if not isinstance(message_index, int) or not 0 <= message_index < len(chat.message_list):
                continue
            message = chat.message_list[message_index]
            audio_path = "NO_AUDIO"
            if payload.get("if_generate_audio", True) and not segment.get("force_no_audio"):
                try:
                    audio_path = self.audio_gen.generate_audio_for_character_sync(
                        self.audio_gen.clean_text_for_audio(str(segment.get("text") or "")),
                        character,
                        bool(payload.get("sakiko_state", True)),
                        str(payload.get("audio_language_choice") or self.dp_chat.audio_language_choice),
                        segment_index=sequence + 1,
                        segment_total=len(segments),
                        emotion=str(segment.get("emotion") or "LABEL_0"),
                    )
                except Exception:
                    had_tts_error = True
                    logger.exception("WebUI 语音合成失败")
            message.audio_path = audio_path
            message.translation = str(segment.get("translation") or message.translation)
            meta = self._message_meta(chat)
            meta[message_index] = {
                "id": str(meta[message_index].get("id") or f"msg_{uuid.uuid4().hex}"),
                "created_at": int(time.time()),
                "turn_id": turn_id,
                "client_message_id": None,
                "sequence": sequence,
                "role": "assistant",
            }
            self.chat_manager.save()
            self.events.put(self._local_event("assistant_segment_ready", {
                "message": self._serialize_message(chat, message_index),
            }, chat_id, turn_id))
            emitted += 1

        self.is_audio_play_complete.put("yes")
        if not payload.get("turn_complete", True):
            return
        cancelled = self.dp_chat.is_turn_cancelled(chat_id, turn_id)
        status = "cancelled" if cancelled else ("error" if had_tts_error else "success")
        error = None
        if had_tts_error:
            error = {
                "code": "TTS_FAILED",
                "message": "部分语音生成失败，已保留文本回复。",
                "retryable": True,
                "details": {},
            }
            self.events.put(self._local_event("error", {"error": error}, chat_id, turn_id))
        self._finish_turn(chat_id, turn_id)
        self.events.put(self._local_event("assistant_turn_complete", {
            "status": status,
            "segment_count": emitted,
            "error": error,
        }, chat_id, turn_id))
        self.events.put(self._local_event("chat_list_snapshot", self.chat_list_snapshot()))

    def _turn_segment_count(self, chat_id: str | None, turn_id: str | None) -> int:
        chat = self.chat_manager.get_chat_by_id(chat_id) if chat_id else None
        if chat is None:
            return 0
        return sum(1 for item in self._message_meta(chat) if item.get("turn_id") == turn_id and item.get("role") == "assistant")

    def _finish_turn(self, chat_id: str | None, turn_id: str | None) -> None:
        with self._lock:
            if self.active_chat_id == chat_id and self.active_turn_id == turn_id:
                self.phase = "idle"
                self.active_chat_id = None
                self.active_turn_id = None
            if chat_id and turn_id:
                self.dp_chat.clear_cancelled_turn(chat_id, turn_id)
            self.chat_manager.save()

    def shutdown(self) -> None:
        self.status = "stopping"
        self._stopping.set()
        if self.dp_chat is not None:
            self.command_queue.put({"type": "exit"})
        if self.audio_gen is not None:
            self.audio_gen.shutdown_worker()
        if self.chat_manager is not None:
            self.chat_manager.save()
