from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
import shutil
import threading
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse

from chat.attachments import (
    SUPPORTED_IMAGE_MIME_TYPES,
    chat_attachment_dir,
    detect_image_mime_type,
    reference_audio_dir,
    resolve_attachment_path,
)
from chat.chat import MessageAttachment, RemoteFileReference
from log import get_logger


logger = get_logger(__name__)

DEEPSEEK_FILES_MODEL = "deepseek-v4-flash-vision-exp"
DEEPSEEK_OFFICIAL_API_BASE = "https://api.deepseek.com"
DEEPSEEK_FILE_EXPIRATION_SECONDS = 30 * 24 * 60 * 60
DEEPSEEK_FILE_RENEWAL_WINDOW_SECONDS = 5 * 60
DEEPSEEK_FILE_MAX_BYTES = 64 * 1024 * 1024
UPLOAD_JOURNAL_VERSION = 1

DraftUploadState = Literal[
    "pending",
    "uploading",
    "ready",
    "failed",
    "cancelled",
    "committed",
]


class RemoteAttachmentError(RuntimeError):
    """表示远端附件管理过程中可向上层报告的错误。"""


class MissingLocalAttachmentError(RemoteAttachmentError):
    """表示远端引用需要更新但本地附件已经缺失。"""


@dataclasses.dataclass(frozen=True)
class DeepSeekFileServiceConfig:
    """保存一次 DeepSeek Files 操作所需的临时服务配置。"""

    api_base: str
    api_key: str
    model: str = DEEPSEEK_FILES_MODEL

    @property
    def api_key_digest(self) -> str:
        """返回不会泄露明文凭据的 API Key 摘要。"""
        return hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()

    @property
    def scope_key(self) -> tuple[str, str]:
        """返回用于匹配远端引用的服务作用域键。"""
        return self.api_base, self.api_key_digest


@dataclasses.dataclass
class DraftAttachmentRecord:
    """记录一个由应用接管的图片草稿及其上传状态。"""

    draft_attachment_id: str
    staging_path: str
    mime_type: str
    original_name: str
    upload_state: DraftUploadState = "pending"
    remote_refs: list[RemoteFileReference] = dataclasses.field(default_factory=list)
    created_at: str = ""
    error_message: str = ""
    cancel_requested: bool = False
    formal_path: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "DraftAttachmentRecord":
        """从上传日志字典中加载草稿记录。"""
        raw_state = str(data.get("upload_state") or "pending")
        allowed_states = {
            "pending", "uploading", "ready", "failed", "cancelled", "committed",
        }
        state = cast(
            DraftUploadState,
            raw_state if raw_state in allowed_states else "failed",
        )
        raw_refs = data.get("remote_refs")
        refs = [
            RemoteFileReference.from_dict(item)
            for item in raw_refs
            if isinstance(item, dict)
        ] if isinstance(raw_refs, list) else []
        return cls(
            draft_attachment_id=str(data.get("draft_attachment_id") or ""),
            staging_path=str(data.get("staging_path") or ""),
            mime_type=str(data.get("mime_type") or ""),
            original_name=str(data.get("original_name") or ""),
            upload_state=state,
            remote_refs=refs,
            created_at=str(data.get("created_at") or ""),
            error_message=str(data.get("error_message") or ""),
            cancel_requested=data.get("cancel_requested") is True,
            formal_path=str(data.get("formal_path") or ""),
        )

    def as_dict(self) -> dict[str, object]:
        """将草稿记录转换为可原子保存的字典。"""
        return {
            "draft_attachment_id": self.draft_attachment_id,
            "staging_path": self.staging_path,
            "mime_type": self.mime_type,
            "original_name": self.original_name,
            "upload_state": self.upload_state,
            "remote_refs": [reference.as_dict() for reference in self.remote_refs],
            "created_at": self.created_at,
            "error_message": self.error_message,
            "cancel_requested": self.cancel_requested,
            "formal_path": self.formal_path,
        }

    def as_payload(self) -> dict[str, object]:
        """返回 UI 与后端队列之间使用的草稿描述符。"""
        payload = self.as_dict()
        payload["staging_path"] = str(resolve_attachment_path(self.staging_path))
        return payload

    def remote_reference_for(
            self,
            service: DeepSeekFileServiceConfig,
    ) -> RemoteFileReference | None:
        """返回当前服务作用域下的远端引用。"""
        for reference in self.remote_refs:
            if reference.matches_scope(*service.scope_key):
                return reference
        return None


@dataclasses.dataclass(frozen=True)
class CleanupResult:
    """汇总一次远端附件可达性清理的结果。"""

    deleted_remote_count: int = 0
    expired_record_count: int = 0
    abandoned_draft_count: int = 0


def normalize_deepseek_api_base(raw_api_base: str) -> str | None:
    """规范化 DeepSeek 官方 API 地址，非官方 hostname 返回空值。"""
    candidate = raw_api_base.strip() or DEEPSEEK_OFFICIAL_API_BASE
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if (parsed.hostname or "").lower() != "api.deepseek.com":
        return None
    path = parsed.path.rstrip("/").lower()
    if path not in {"", "/v1", "/beta"}:
        return None
    scheme = (parsed.scheme or "https").lower()
    if scheme not in {"http", "https"}:
        return None
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{scheme}://api.deepseek.com{port}"


def normalize_deepseek_model_name(model: str) -> str:
    """移除常见 provider 前缀并返回 DeepSeek 模型短名称。"""
    normalized = model.strip().lower()
    for prefix in ("openai/", "deepseek/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    return normalized


def build_deepseek_file_service_config(
        *,
        provider_id: str,
        model: str,
        api_base: str,
        api_key: str,
        use_default_deepseek_api: bool = False,
) -> DeepSeekFileServiceConfig | None:
    """根据当前模型配置构造 DeepSeek Files 服务配置。"""
    if use_default_deepseek_api or provider_id == "deepseek_up":
        return None
    if normalize_deepseek_model_name(model) != DEEPSEEK_FILES_MODEL:
        return None
    if provider_id not in {"deepseek", "custom"}:
        return None
    normalized_base = normalize_deepseek_api_base(
        api_base or DEEPSEEK_OFFICIAL_API_BASE
    )
    if normalized_base is None or not api_key.strip():
        return None
    return DeepSeekFileServiceConfig(
        api_base=normalized_base,
        api_key=api_key,
        model=DEEPSEEK_FILES_MODEL,
    )


def remote_reference_is_fresh(
        reference: RemoteFileReference,
        *,
        now: datetime | None = None,
) -> bool:
    """判断远端引用是否位于五分钟续期窗口之外。"""
    current = now or datetime.now(timezone.utc)
    expires_at = _parse_utc_datetime(reference.expires_at)
    if expires_at is None:
        return False
    return expires_at - current > timedelta(seconds=DEEPSEEK_FILE_RENEWAL_WINDOW_SECONDS)


class RemoteAttachmentManager:
    """集中管理图片草稿、DeepSeek Files 引用与可达性清理。"""

    def __init__(self, storage_root: Path | None = None) -> None:
        """加载上传日志并创建最多两个后台网络 worker。"""
        self.storage_root = storage_root or (reference_audio_dir() / "chat_attachment_drafts")
        self.files_root = self.storage_root / "files"
        self.journal_path = self.storage_root / "journal.json"
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="deepseek-file",
        )
        self._drafts: dict[str, DraftAttachmentRecord] = {}
        self._known_refs: list[RemoteFileReference] = []
        self._pending_deletes: list[RemoteFileReference] = []
        self._notice_shown = False
        self._load_journal()

    @property
    def notice_shown(self) -> bool:
        """返回 DeepSeek 首次上传说明是否已经展示。"""
        with self._lock:
            return self._notice_shown

    def mark_notice_shown(self) -> None:
        """持久化首次上传说明已经展示的状态。"""
        with self._lock:
            self._notice_shown = True
            self._save_journal_locked()

    def list_drafts(self) -> list[DraftAttachmentRecord]:
        """返回当前草稿记录的独立副本。"""
        with self._lock:
            return [DraftAttachmentRecord.from_dict(item.as_dict()) for item in self._drafts.values()]

    def get_draft(self, draft_attachment_id: str) -> DraftAttachmentRecord | None:
        """返回指定草稿记录的独立副本。"""
        with self._lock:
            record = self._drafts.get(draft_attachment_id)
            if record is None:
                return None
            return DraftAttachmentRecord.from_dict(record.as_dict())

    def stage_draft_image(self, source_path: str) -> DraftAttachmentRecord:
        """复制用户图片到应用暂存目录并登记草稿。"""
        source = Path(source_path)
        mime_type = detect_image_mime_type(source)
        if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
            raise RemoteAttachmentError(f"图片无法读取或格式不受支持：{source.name}")
        size = source.stat().st_size
        if size > DEEPSEEK_FILE_MAX_BYTES:
            raise RemoteAttachmentError("图片超过 DeepSeek Files API 的 64 MiB 上限。")

        draft_attachment_id = uuid.uuid4().hex
        target_dir = self.files_root / draft_attachment_id
        target_dir.mkdir(parents=True, exist_ok=False)
        extension = source.suffix.lower()
        target_path = target_dir / f"image{extension}"
        try:
            shutil.copy2(source, target_path)
        except Exception:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise

        try:
            stored_path = target_path.relative_to(reference_audio_dir()).as_posix()
        except ValueError:
            stored_path = str(target_path)
        record = DraftAttachmentRecord(
            draft_attachment_id=draft_attachment_id,
            staging_path=stored_path,
            mime_type=mime_type,
            original_name=source.name,
            created_at=_utc_now_text(),
        )
        with self._lock:
            self._drafts[draft_attachment_id] = record
            self._save_journal_locked()
        return DraftAttachmentRecord.from_dict(record.as_dict())

    def stage_existing_attachment(
            self,
            attachment: MessageAttachment,
    ) -> DraftAttachmentRecord:
        """把历史正式附件复制回草稿，并复用其远端引用。"""
        if attachment.availability == "unavailable":
            raise MissingLocalAttachmentError(
                attachment.unavailable_reason or "历史图片附件不可用。"
            )
        source_path = resolve_attachment_path(attachment.path)
        record = self.stage_draft_image(str(source_path))
        with self._lock:
            current = self._drafts[record.draft_attachment_id]
            current.remote_refs = list(attachment.remote_refs)
            current.upload_state = "ready"
            for reference in current.remote_refs:
                self._remember_reference_locked(reference)
            self._save_journal_locked()
            return DraftAttachmentRecord.from_dict(current.as_dict())

    def submit_upload(
            self,
            draft_attachment_id: str,
            service: DeepSeekFileServiceConfig,
    ) -> Future[DraftAttachmentRecord]:
        """在后台 worker 中上传指定草稿附件。"""
        with self._lock:
            record = self._drafts.get(draft_attachment_id)
            if record is None:
                raise RemoteAttachmentError("找不到待上传的图片草稿。")
            record.upload_state = "uploading"
            record.error_message = ""
            record.cancel_requested = False
            self._save_journal_locked()
        return self._executor.submit(
            self.upload_draft_attachment,
            draft_attachment_id,
            service,
        )

    def mark_draft_ready_without_upload(
            self,
            draft_attachment_id: str,
    ) -> DraftAttachmentRecord:
        """把不使用 DeepSeek Files 的图片草稿标记为可发送。"""
        with self._lock:
            record = self._drafts.get(draft_attachment_id)
            if record is None:
                raise RemoteAttachmentError("找不到图片草稿。")
            record.upload_state = "ready"
            record.error_message = ""
            self._save_journal_locked()
            return DraftAttachmentRecord.from_dict(record.as_dict())

    def upload_draft_attachment(
            self,
            draft_attachment_id: str,
            service: DeepSeekFileServiceConfig,
    ) -> DraftAttachmentRecord:
        """上传指定草稿附件并原子记录返回的 file ID。"""
        with self._lock:
            record = self._drafts.get(draft_attachment_id)
            if record is None:
                raise RemoteAttachmentError("找不到待上传的图片草稿。")
            record.upload_state = "uploading"
            record.error_message = ""
            record.cancel_requested = False
            self._save_journal_locked()
            staging_path = resolve_attachment_path(record.staging_path)
            original_name = record.original_name
            mime_type = record.mime_type

        try:
            reference = self._upload_file_with_retry(
                staging_path,
                original_name,
                mime_type,
                service,
            )
        except Exception as exc:
            with self._lock:
                current = self._drafts.get(draft_attachment_id)
                if current is None:
                    raise
                if current.cancel_requested:
                    current.upload_state = "cancelled"
                    self._remove_draft_locked(draft_attachment_id, delete_local=True)
                else:
                    current.upload_state = "failed"
                    current.error_message = str(exc)
                    self._save_journal_locked()
            raise RemoteAttachmentError(str(exc)) from exc

        should_delete = False
        with self._lock:
            current = self._drafts.get(draft_attachment_id)
            if current is None or current.cancel_requested:
                should_delete = True
                if current is not None:
                    self._remove_draft_locked(draft_attachment_id, delete_local=True)
            else:
                current.remote_refs = _replace_scope_reference(current.remote_refs, reference)
                current.upload_state = "ready"
                current.error_message = ""
                self._remember_reference_locked(reference)
                self._save_journal_locked()
                snapshot = DraftAttachmentRecord.from_dict(current.as_dict())

        if should_delete:
            self._delete_or_defer(reference, [service])
            raise RemoteAttachmentError("图片草稿已取消。")
        return snapshot

    def cancel_draft_attachment(
            self,
            draft_attachment_id: str,
            services: Sequence[DeepSeekFileServiceConfig] = (),
    ) -> None:
        """取消草稿上传，并清理暂存副本和不再使用的远端引用。"""
        with self._lock:
            record = self._drafts.get(draft_attachment_id)
            if record is None:
                return
            if record.upload_state == "committed":
                return
            if record.upload_state == "uploading":
                record.cancel_requested = True
                record.upload_state = "cancelled"
                self._save_journal_locked()
                return
            self._remove_draft_locked(draft_attachment_id, delete_local=True)

    def commit_draft_attachment(
            self,
            draft_attachment_id: str,
            chat_id: str,
    ) -> MessageAttachment:
        """把已就绪草稿转为正式聊天附件，并保留记录直至 UI ack。"""
        if not chat_id:
            raise RemoteAttachmentError("提交图片附件时缺少 chat_id。")
        with self._lock:
            record = self._drafts.get(draft_attachment_id)
            if record is None:
                raise RemoteAttachmentError("图片草稿不存在或已经提交。")
            if record.upload_state != "ready":
                raise RemoteAttachmentError("图片仍在上传或上传失败，暂时不能发送。")
            staging_path = resolve_attachment_path(record.staging_path)
            if not staging_path.exists() or not staging_path.is_file():
                raise MissingLocalAttachmentError(f"图片暂存文件已丢失：{record.original_name}")

            target_dir = chat_attachment_dir(chat_id)
            target_dir.mkdir(parents=True, exist_ok=True)
            extension = staging_path.suffix.lower()
            target_path = target_dir / f"{uuid.uuid4().hex}{extension}"
            shutil.move(str(staging_path), target_path)
            try:
                staging_path.parent.rmdir()
            except OSError:
                pass
            formal_path = f"chat_attachments/{chat_id}/{target_path.name}"
            record.formal_path = formal_path
            record.upload_state = "committed"
            self._save_journal_locked()
            return MessageAttachment(
                type="image",
                path=formal_path,
                mime_type=record.mime_type,
                original_name=record.original_name,
                remote_refs=list(record.remote_refs),
            )

    def acknowledge_commit(self, draft_attachment_ids: Iterable[str]) -> None:
        """在 UI 收到后端提交确认后移除对应上传日志。"""
        with self._lock:
            changed = False
            for draft_attachment_id in draft_attachment_ids:
                record = self._drafts.get(draft_attachment_id)
                if record is None or record.upload_state != "committed":
                    continue
                self._drafts.pop(draft_attachment_id, None)
                changed = True
            if changed:
                self._save_journal_locked()

    def rollback_commit(self, draft_attachment_id: str) -> None:
        """在聊天保存失败时把已转正附件恢复为可重试草稿。"""
        with self._lock:
            record = self._drafts.get(draft_attachment_id)
            if record is None or record.upload_state != "committed" or not record.formal_path:
                return
            formal_path = resolve_attachment_path(record.formal_path)
            staging_path = resolve_attachment_path(record.staging_path)
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            if formal_path.exists() and formal_path.is_file():
                shutil.move(str(formal_path), staging_path)
            record.formal_path = ""
            record.upload_state = "ready"
            self._save_journal_locked()

    def ensure_remote_reference(
            self,
            attachment: MessageAttachment,
            service: DeepSeekFileServiceConfig,
    ) -> RemoteFileReference:
        """确保正式附件在当前服务作用域中具有可用远端引用。"""
        if attachment.availability == "unavailable":
            raise MissingLocalAttachmentError(attachment.unavailable_reason or "图片附件不可用。")
        current = attachment.remote_reference_for(*service.scope_key)
        if current is not None and remote_reference_is_fresh(current):
            return current

        local_path = resolve_attachment_path(attachment.path)
        if not local_path.exists() or not local_path.is_file():
            if current is not None and not _reference_is_expired(current):
                return current
            raise MissingLocalAttachmentError(
                f"图片本地文件缺失，无法重新上传：{attachment.original_name or attachment.path}"
            )
        mime_type = detect_image_mime_type(local_path)
        if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
            raise MissingLocalAttachmentError(
                f"图片本地文件无法读取，无法重新上传：{attachment.original_name or attachment.path}"
            )
        reference = self._upload_file_with_retry(
            local_path,
            attachment.original_name or local_path.name,
            mime_type,
            service,
        )
        attachment.remote_refs = _replace_scope_reference(attachment.remote_refs, reference)
        with self._lock:
            self._remember_reference_locked(reference)
            self._save_journal_locked()
        return reference

    def ensure_draft_remote_reference(
            self,
            draft_attachment_id: str,
            service: DeepSeekFileServiceConfig,
    ) -> RemoteFileReference:
        """确保草稿在发送时使用的当前服务作用域中具有可用引用。"""
        record = self.get_draft(draft_attachment_id)
        if record is None:
            raise RemoteAttachmentError("找不到待发送的图片草稿。")
        current = record.remote_reference_for(service)
        if current is not None and remote_reference_is_fresh(current):
            return current
        uploaded = self.upload_draft_attachment(draft_attachment_id, service)
        reference = uploaded.remote_reference_for(service)
        if reference is None:
            raise RemoteAttachmentError("图片重新上传后没有取得远端引用。")
        return reference

    def invalidate_remote_reference(
            self,
            attachment: MessageAttachment,
            service: DeepSeekFileServiceConfig,
    ) -> None:
        """移除附件在当前服务作用域中被服务端拒绝的引用。"""
        attachment.remote_refs = [
            reference
            for reference in attachment.remote_refs
            if not reference.matches_scope(*service.scope_key)
        ]

    def find_unrecoverable_attachments(
            self,
            attachments: Sequence[MessageAttachment],
            service: DeepSeekFileServiceConfig,
    ) -> list[MessageAttachment]:
        """找出本地文件缺失且当前远端引用也不可用的图片附件。"""
        result: list[MessageAttachment] = []
        for attachment in attachments:
            if not attachment.is_image() or attachment.availability == "unavailable":
                continue
            local_path = resolve_attachment_path(attachment.path)
            if local_path.exists() and local_path.is_file():
                continue
            reference = attachment.remote_reference_for(*service.scope_key)
            if reference is None or _reference_is_expired(reference):
                result.append(attachment)
        return result

    @staticmethod
    def mark_attachments_unavailable(
            attachments: Sequence[MessageAttachment],
            reason: str,
    ) -> None:
        """把用户确认忽略的缺失图片标记为不再发送。"""
        for attachment in attachments:
            attachment.availability = "unavailable"
            attachment.unavailable_reason = reason

    def submit_reconcile_and_cleanup(
            self,
            formal_attachments: Sequence[MessageAttachment],
            services: Sequence[DeepSeekFileServiceConfig],
            *,
            abandon_drafts: bool = False,
    ) -> Future[CleanupResult]:
        """在后台 worker 中执行附件可达性清理。"""
        return self._executor.submit(
            self.reconcile_and_cleanup,
            list(formal_attachments),
            list(services),
            abandon_drafts=abandon_drafts,
        )

    def reconcile_and_cleanup(
            self,
            formal_attachments: Sequence[MessageAttachment],
            services: Sequence[DeepSeekFileServiceConfig],
            *,
            abandon_drafts: bool = False,
    ) -> CleanupResult:
        """扫描正式消息和草稿日志，删除不可达或已过期的远端引用。"""
        abandoned_count = 0
        with self._lock:
            if abandon_drafts:
                formal_keys = _reference_keys_from_attachments(formal_attachments)
                for draft_id, record in list(self._drafts.items()):
                    draft_keys = {_reference_key(reference) for reference in record.remote_refs}
                    if record.upload_state == "committed" and draft_keys & formal_keys:
                        self._drafts.pop(draft_id, None)
                        continue
                    self._remove_draft_locked(draft_id, delete_local=True)
                    abandoned_count += 1

            reachable = _reference_keys_from_attachments(formal_attachments)
            for record in self._drafts.values():
                reachable.update(_reference_key(reference) for reference in record.remote_refs)
            candidates = [
                reference
                for reference in self._known_refs
                if _reference_key(reference) not in reachable
            ]

        deleted_count = 0
        expired_count = 0
        for reference in candidates:
            if _reference_is_expired(reference):
                with self._lock:
                    self._forget_reference_locked(reference)
                expired_count += 1
                continue
            if self._delete_or_defer(reference, services):
                deleted_count += 1

        with self._lock:
            self._drop_expired_pending_locked()
            self._save_journal_locked()
        return CleanupResult(deleted_count, expired_count, abandoned_count)

    def shutdown(self) -> None:
        """停止接收新后台任务，但不长时间等待网络请求。"""
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _upload_file_with_retry(
            self,
            path: Path,
            original_name: str,
            mime_type: str,
            service: DeepSeekFileServiceConfig,
    ) -> RemoteFileReference:
        """通过 LiteLLM 上传文件，并按策略自动重试一次。"""
        if path.stat().st_size > DEEPSEEK_FILE_MAX_BYTES:
            raise RemoteAttachmentError("图片超过 DeepSeek Files API 的 64 MiB 上限。")
        last_error: BaseException | None = None
        for attempt in range(2):
            try:
                return self._upload_file_once(path, original_name, mime_type, service)
            except Exception as exc:
                last_error = exc
                if attempt >= 1 or not _is_retryable_upload_error(exc):
                    break
                time.sleep(0.25)
        raise RemoteAttachmentError(f"图片上传失败：{last_error}") from last_error

    @staticmethod
    def _upload_file_once(
            path: Path,
            original_name: str,
            mime_type: str,
            service: DeepSeekFileServiceConfig,
    ) -> RemoteFileReference:
        """执行一次 LiteLLM OpenAI 兼容 Files API 上传。"""
        import litellm

        with path.open("rb") as image_file:
            response = litellm.create_file(
                file=(original_name, image_file, mime_type),
                purpose="user_data",
                expires_after={
                    "anchor": "created_at",
                    "seconds": DEEPSEEK_FILE_EXPIRATION_SECONDS,
                },
                custom_llm_provider="openai",
                api_base=service.api_base,
                api_key=service.api_key,
            )
        if inspect.isawaitable(response):
            raise RemoteAttachmentError("LiteLLM 意外返回异步上传结果。")
        file_id = _read_object_string(response, "id")
        if not file_id:
            raise RemoteAttachmentError("DeepSeek Files API 未返回 file_id。")
        uploaded_at = datetime.now(timezone.utc)
        return RemoteFileReference(
            api_base=service.api_base,
            api_key_digest=service.api_key_digest,
            file_id=file_id,
            uploaded_at=uploaded_at.isoformat(),
            expires_at=(
                uploaded_at + timedelta(seconds=DEEPSEEK_FILE_EXPIRATION_SECONDS)
            ).isoformat(),
        )

    def _delete_or_defer(
            self,
            reference: RemoteFileReference,
            services: Sequence[DeepSeekFileServiceConfig],
    ) -> bool:
        """使用匹配凭据删除远端文件；无凭据时登记待清理。"""
        service = next(
            (
                item for item in services
                if item.scope_key == (reference.api_base, reference.api_key_digest)
            ),
            None,
        )
        if service is None:
            with self._lock:
                if _reference_key(reference) not in {
                    _reference_key(item) for item in self._pending_deletes
                }:
                    self._pending_deletes.append(reference)
                self._save_journal_locked()
            return False
        try:
            from openai import OpenAI

            client = OpenAI(api_key=service.api_key, base_url=service.api_base)
            client.files.delete(reference.file_id)
        except Exception:
            logger.warning("删除 DeepSeek 远端文件失败，将保留到下次清理：%s", reference.file_id)
            with self._lock:
                if _reference_key(reference) not in {
                    _reference_key(item) for item in self._pending_deletes
                }:
                    self._pending_deletes.append(reference)
                self._save_journal_locked()
            return False
        with self._lock:
            self._forget_reference_locked(reference)
            self._save_journal_locked()
        return True

    def _load_journal(self) -> None:
        """从磁盘恢复上传日志，损坏时保留备份并使用空状态。"""
        if not self.journal_path.exists():
            return
        try:
            raw = json.loads(self.journal_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("上传日志顶层必须是对象。")
            raw_drafts = raw.get("drafts")
            if isinstance(raw_drafts, list):
                for item in raw_drafts:
                    if not isinstance(item, dict):
                        continue
                    record = DraftAttachmentRecord.from_dict(item)
                    if record.draft_attachment_id:
                        self._drafts[record.draft_attachment_id] = record
            self._known_refs = _read_reference_list(raw.get("known_refs"))
            self._pending_deletes = _read_reference_list(raw.get("pending_deletes"))
            self._notice_shown = raw.get("notice_shown") is True
            for record in self._drafts.values():
                for reference in record.remote_refs:
                    self._remember_reference_locked(reference)
        except Exception:
            backup_path = self.journal_path.with_name(
                f"journal.corrupt.{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
            )
            try:
                shutil.copy2(self.journal_path, backup_path)
            except Exception:
                logger.exception("备份损坏的图片上传日志失败。")
            logger.exception("图片上传日志损坏，将使用空日志启动。")
            self._drafts.clear()
            self._known_refs.clear()
            self._pending_deletes.clear()

    def _save_journal_locked(self) -> None:
        """在持锁状态下原子保存上传日志。"""
        self.storage_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": UPLOAD_JOURNAL_VERSION,
            "notice_shown": self._notice_shown,
            "drafts": [record.as_dict() for record in self._drafts.values()],
            "known_refs": [reference.as_dict() for reference in self._known_refs],
            "pending_deletes": [reference.as_dict() for reference in self._pending_deletes],
        }
        temporary_path = self.journal_path.with_suffix(
            f".tmp.{os.getpid()}.{threading.get_ident()}"
        )
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, self.journal_path)

    def _remove_draft_locked(self, draft_attachment_id: str, *, delete_local: bool) -> None:
        """在持锁状态下移除草稿及其本地暂存副本。"""
        record = self._drafts.pop(draft_attachment_id, None)
        if record is not None and delete_local and record.staging_path:
            path = resolve_attachment_path(record.staging_path)
            if _path_is_within(path, self.files_root):
                shutil.rmtree(path.parent, ignore_errors=True)
        if record is not None and delete_local and record.formal_path:
            formal_path = resolve_attachment_path(record.formal_path)
            formal_root = reference_audio_dir() / "chat_attachments"
            if _path_is_within(formal_path, formal_root):
                formal_path.unlink(missing_ok=True)
        self._save_journal_locked()

    def _remember_reference_locked(self, reference: RemoteFileReference) -> None:
        """在持锁状态下登记由本程序创建的远端引用。"""
        key = _reference_key(reference)
        if key not in {_reference_key(item) for item in self._known_refs}:
            self._known_refs.append(reference)

    def _forget_reference_locked(self, reference: RemoteFileReference) -> None:
        """在持锁状态下遗忘已经删除或自然过期的引用。"""
        key = _reference_key(reference)
        self._known_refs = [item for item in self._known_refs if _reference_key(item) != key]
        self._pending_deletes = [
            item for item in self._pending_deletes if _reference_key(item) != key
        ]

    def _drop_expired_pending_locked(self) -> None:
        """在持锁状态下移除已经自然过期的待删除记录。"""
        for reference in list(self._pending_deletes):
            if _reference_is_expired(reference):
                self._forget_reference_locked(reference)


def _replace_scope_reference(
        references: Sequence[RemoteFileReference],
        replacement: RemoteFileReference,
) -> list[RemoteFileReference]:
    """替换同一作用域引用，同时保留其他服务作用域。"""
    retained = [
        reference
        for reference in references
        if not reference.matches_scope(replacement.api_base, replacement.api_key_digest)
    ]
    retained.append(replacement)
    return retained


def _reference_key(reference: RemoteFileReference) -> tuple[str, str, str]:
    """返回远端引用的稳定唯一键。"""
    return reference.api_base, reference.api_key_digest, reference.file_id


def _reference_keys_from_attachments(
        attachments: Sequence[MessageAttachment],
) -> set[tuple[str, str, str]]:
    """汇总正式消息附件引用的可达性键。"""
    return {
        _reference_key(reference)
        for attachment in attachments
        for reference in attachment.remote_refs
    }


def _read_reference_list(value: object) -> list[RemoteFileReference]:
    """从未知 JSON 值中读取合法远端引用列表。"""
    if not isinstance(value, list):
        return []
    return [
        RemoteFileReference.from_dict(item)
        for item in value
        if isinstance(item, dict)
    ]


def _read_object_string(value: object, key: str) -> str:
    """从字典或 SDK 对象中读取字符串字段。"""
    if isinstance(value, Mapping):
        raw = value.get(key)
    else:
        raw = getattr(value, key, None)
    return raw if isinstance(raw, str) else ""


def _parse_utc_datetime(value: str) -> datetime | None:
    """解析 ISO 8601 时间并统一转换为 UTC。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reference_is_expired(reference: RemoteFileReference) -> bool:
    """判断远端引用是否已经到达预计过期时间。"""
    expires_at = _parse_utc_datetime(reference.expires_at)
    return expires_at is None or expires_at <= datetime.now(timezone.utc)


def _utc_now_text() -> str:
    """返回当前 UTC ISO 8601 时间。"""
    return datetime.now(timezone.utc).isoformat()


def _is_retryable_upload_error(exc: BaseException) -> bool:
    """判断上传错误是否属于网络、限流或服务端临时失败。"""
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code == 429 or status_code >= 500
    name = type(exc).__name__.lower()
    return any(token in name for token in ("timeout", "connection", "network"))


def _path_is_within(path: Path, root: Path) -> bool:
    """判断路径是否位于给定根目录中。"""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
