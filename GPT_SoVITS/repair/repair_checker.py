from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from repair import REPAIR_CLIENT_VERSION
from repair.repair_manifest import (
    APP_ID,
    RepairFileEntry,
    RepairManifest,
    compare_versions,
    object_relative_path,
    parse_manifest_bytes,
    resolve_under_root,
    sha256_bytes,
    sha256_file,
)
from repair.repair_paths import (
    get_repair_object_cache,
    get_repair_plan_dir,
    get_repair_staging_root,
    make_repair_run_id,
)
from repair.repair_security import verify_manifest_signature
from update.update_checker import detect_arch, detect_platform, read_current_version
from update.update_paths import get_version_file


CHANNEL = "stable"
DEFAULT_REPAIR_BASE_URLS: tuple[str, ...] = ("https://d-sakiko-data.xjtutoolbox.com/d-sakiko/repair/",)
REQUEST_TIMEOUT = (5.0, 20.0)


class HttpResponse(Protocol):
    """描述修复下载所需的最小 HTTP 响应。"""

    status_code: int
    content: bytes

    def iter_content(self, chunk_size: int) -> Iterable[bytes]:
        """按块返回响应体。"""


class HttpSession(Protocol):
    """描述修复流程所需的最小 requests 会话。"""

    headers: dict[str, str]

    def get(
        self,
        url: str,
        *,
        timeout: tuple[float, float],
        stream: bool = False,
    ) -> HttpResponse:
        """发起 GET 请求。"""

    def close(self) -> None:
        """关闭会话。"""


class RepairError(RuntimeError):
    """表示完整性检查或修复准备失败。"""


class RepairVersionUnsupported(RepairError):
    """表示所有资源源都明确不支持当前版本。"""


class RepairServiceUnavailable(RepairError):
    """表示修复服务、签名或清单暂时不可用。"""


class RepairCancelled(RepairError):
    """表示用户在安全阶段取消了修复流程。"""


@dataclass(frozen=True)
class FetchedManifest:
    """保存已验签并完成身份校验的远端 manifest。"""

    manifest: RepairManifest
    content: bytes
    sha256: str
    source_base_url: str


@dataclass(frozen=True)
class RepairCandidate:
    """描述一个缺失或偏离官方内容的本地文件。"""

    entry: RepairFileEntry
    original_state: str
    original_sha256: str | None


@dataclass(frozen=True)
class RepairCheckResult:
    """保存一次完整性检查结果。"""

    fetched_manifest: FetchedManifest
    candidates: tuple[RepairCandidate, ...]
    base_urls: tuple[str, ...]

    @property
    def total_download_size(self) -> int:
        """返回所有候选文件的逻辑下载总大小。"""

        return sum(candidate.entry.size for candidate in self.candidates)


@dataclass(frozen=True)
class PreparedRepair:
    """保存下载完成后交给独立修复器的计划。"""

    plan_file: Path
    staging_dir: Path
    candidate_count: int


def get_configured_repair_base_urls() -> tuple[str, ...]:
    """读取独立修复资源 base URL 列表。"""

    configured = tuple(
        item.strip().rstrip("/")
        for item in os.environ.get("DSAKIKO_REPAIR_BASE_URLS", "").split(",")
        if item.strip()
    )
    return configured or DEFAULT_REPAIR_BASE_URLS


def manifest_relative_url(version: str, platform_name: str, arch: str) -> str:
    """拼接当前版本 manifest 的资源相对 URL。"""

    return f"manifests/{APP_ID}/{CHANNEL}/{version}/{platform_name}-{arch}.json"


def _new_session() -> requests.Session:
    """创建配置统一 User-Agent 的 requests 会话。"""

    session = requests.Session()
    session.headers.update({"User-Agent": f"D_sakiko-Repair/{REPAIR_CLIENT_VERSION}"})
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def _validate_manifest_identity(
    manifest: RepairManifest,
    version: str,
    platform_name: str,
    arch: str,
) -> None:
    """确认 manifest 只描述当前应用、版本和平台。"""

    if manifest.app_id != APP_ID:
        raise RepairError(f"manifest app_id 不匹配：{manifest.app_id}")
    if manifest.channel != CHANNEL:
        raise RepairError(f"manifest channel 不匹配：{manifest.channel}")
    if manifest.version != version:
        raise RepairError(f"manifest 版本不匹配：当前 {version}，清单 {manifest.version}")
    if manifest.platform != platform_name or manifest.arch not in {arch, "universal"}:
        raise RepairError(
            f"manifest 平台架构不匹配：当前 {platform_name}-{arch}，"
            f"清单 {manifest.platform}-{manifest.arch}"
        )
    if compare_versions(REPAIR_CLIENT_VERSION, manifest.min_repair_client_version) < 0:
        raise RepairError(
            f"当前程序的修复组件过旧：{REPAIR_CLIENT_VERSION}，"
            f"清单要求 {manifest.min_repair_client_version}，请先升级到新版本"
        )


def fetch_manifest(
    base_urls: tuple[str, ...],
    version: str,
    platform_name: str,
    arch: str,
    *,
    session: HttpSession | None = None,
) -> FetchedManifest:
    """按源下载同源 manifest/signature，并返回首个有效结果。"""

    if not base_urls:
        raise RepairServiceUnavailable("尚未配置程序文件修复资源地址")
    own_session = session is None
    http = session or _new_session()
    relative = manifest_relative_url(version, platform_name, arch)
    errors: list[str] = []
    not_found_count = 0
    try:
        for base_url in base_urls:
            manifest_url = f"{base_url.rstrip('/')}/{relative}"
            signature_url = f"{manifest_url}.sig"
            try:
                manifest_response = http.get(manifest_url, timeout=REQUEST_TIMEOUT)
                if manifest_response.status_code == 404:
                    not_found_count += 1
                    errors.append(f"{manifest_url}: HTTP 404")
                    continue
                if manifest_response.status_code < 200 or manifest_response.status_code >= 300:
                    raise RepairError(f"HTTP {manifest_response.status_code}")
                signature_response = http.get(signature_url, timeout=REQUEST_TIMEOUT)
                if signature_response.status_code < 200 or signature_response.status_code >= 300:
                    raise RepairError(f"签名 HTTP {signature_response.status_code}")
                verify_manifest_signature(manifest_response.content, signature_response.content)
                manifest = parse_manifest_bytes(manifest_response.content)
                _validate_manifest_identity(manifest, version, platform_name, arch)
                return FetchedManifest(
                    manifest=manifest,
                    content=manifest_response.content,
                    sha256=sha256_bytes(manifest_response.content),
                    source_base_url=base_url,
                )
            except Exception as exc:
                errors.append(f"{manifest_url}: {exc}")
        if not_found_count == len(base_urls):
            raise RepairVersionUnsupported(
                f"当前版本暂不支持程序文件修复：{version} {platform_name}-{arch}，请尝试先升级到新版本"
            )
        raise RepairServiceUnavailable("修复服务暂时不可用：\n" + "\n".join(errors))
    finally:
        if own_session:
            http.close()


def scan_local_files(
    app_root: Path,
    manifest: RepairManifest,
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[RepairCandidate, ...]:
    """扫描 manifest 文件内容，不检查 mode 或额外本地文件。"""

    candidates: list[RepairCandidate] = []
    total = len(manifest.files)
    for index, entry in enumerate(manifest.files, start=1):
        if cancelled is not None and cancelled():
            raise RepairCancelled("用户取消完整性检查")
        target = resolve_under_root(app_root, entry.path)
        if not target.is_file():
            candidates.append(RepairCandidate(entry=entry, original_state="missing", original_sha256=None))
        else:
            actual_sha = sha256_file(target)
            if actual_sha != entry.sha256:
                candidates.append(
                    RepairCandidate(entry=entry, original_state="modified", original_sha256=actual_sha)
                )
        if progress_callback is not None:
            progress_callback(index, total, entry.path)
    return tuple(candidates)


def check_integrity(
    app_root: Path,
    base_urls: tuple[str, ...],
    *,
    session: HttpSession | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> RepairCheckResult:
    """获取当前版本 manifest 并扫描本地完整性。"""

    try:
        version = read_current_version(get_version_file(app_root))
    except Exception as exc:
        raise RepairError("无法识别当前版本，请重新安装或升级到新版本") from exc
    platform_name = detect_platform()
    arch = detect_arch()
    fetched = fetch_manifest(base_urls, version, platform_name, arch, session=session)
    candidates = scan_local_files(
        app_root,
        fetched.manifest,
        progress_callback=progress_callback,
        cancelled=cancelled,
    )
    return RepairCheckResult(fetched_manifest=fetched, candidates=candidates, base_urls=base_urls)


def _download_object(
    http: HttpSession,
    base_urls: tuple[str, ...],
    entry: RepairFileEntry,
    destination: Path,
    *,
    progress_callback: Callable[[int], None] | None,
    cancelled: Callable[[], bool] | None,
) -> None:
    """按源下载单个 object 到 `.part`，校验后原子落盘。"""

    errors: list[str] = []
    relative = object_relative_path(entry.sha256).as_posix()
    part_file = destination.with_name(f"{destination.name}.part")
    part_file.parent.mkdir(parents=True, exist_ok=True)
    for base_url in base_urls:
        url = f"{base_url.rstrip('/')}/{relative}"
        try:
            response = http.get(url, timeout=REQUEST_TIMEOUT, stream=True)
            if response.status_code < 200 or response.status_code >= 300:
                raise RepairError(f"HTTP {response.status_code}")
            part_file.unlink(missing_ok=True)
            with part_file.open("wb") as output:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if cancelled is not None and cancelled():
                        raise RepairCancelled("用户取消修复资源下载")
                    if not chunk:
                        continue
                    output.write(chunk)
                    if progress_callback is not None:
                        progress_callback(len(chunk))
            if part_file.stat().st_size != entry.size:
                raise RepairError(f"object 大小不匹配：期望 {entry.size}，实际 {part_file.stat().st_size}")
            if sha256_file(part_file) != entry.sha256:
                raise RepairError(f"object SHA256 不匹配：{entry.sha256}")
            part_file.replace(destination)
            return
        except RepairCancelled:
            part_file.unlink(missing_ok=True)
            raise
        except Exception as exc:
            part_file.unlink(missing_ok=True)
            errors.append(f"{url}: {exc}")
    raise RepairServiceUnavailable(f"修复文件下载失败：{entry.path}\n" + "\n".join(errors))


def prepare_repair(
    app_root: Path,
    check_result: RepairCheckResult,
    *,
    session: HttpSession | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PreparedRepair:
    """下载并校验所有候选 object，随后原子写出 repair plan。"""

    if not check_result.candidates:
        raise RepairError("没有需要修复的文件")
    manifest = check_result.fetched_manifest.manifest
    run_id = make_repair_run_id(manifest.version)
    staging_dir = get_repair_staging_root(app_root) / run_id
    cache_root = get_repair_object_cache(app_root)
    own_session = session is None
    http = session or _new_session()
    downloaded = 0
    total = check_result.total_download_size

    def report(delta: int, path: str) -> None:
        """累计下载字节并发送总进度。"""

        nonlocal downloaded
        downloaded += delta
        if progress_callback is not None:
            progress_callback(min(downloaded, total), total, path)

    plan_files: list[dict[str, object]] = []
    try:
        for candidate in check_result.candidates:
            if cancelled is not None and cancelled():
                raise RepairCancelled("用户取消修复资源下载")
            entry = candidate.entry
            cache_file = cache_root.parent / object_relative_path(entry.sha256)
            if cache_file.is_file() and (
                cache_file.stat().st_size != entry.size or sha256_file(cache_file) != entry.sha256
            ):
                cache_file.unlink()
            if not cache_file.is_file():
                _download_object(
                    http,
                    check_result.base_urls,
                    entry,
                    cache_file,
                    progress_callback=lambda delta, path=entry.path: report(delta, path),
                    cancelled=cancelled,
                )
            else:
                report(entry.size, entry.path)
            staged_file = staging_dir / object_relative_path(entry.sha256)
            staged_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cache_file, staged_file)
            if sha256_file(staged_file) != entry.sha256:
                raise RepairError(f"staging SHA256 复验失败：{entry.path}")
            plan_files.append(
                {
                    "path": entry.path,
                    "original_state": candidate.original_state,
                    "original_sha256": candidate.original_sha256,
                    "sha256": entry.sha256,
                    "size": entry.size,
                    "mode": entry.mode,
                    "staged_file": staged_file.relative_to(app_root).as_posix(),
                }
            )
        plan = {
            "schema": 1,
            "app_id": manifest.app_id,
            "version": manifest.version,
            "platform": manifest.platform,
            "arch": manifest.arch,
            "manifest_sha256": check_result.fetched_manifest.sha256,
            "staging_dir": staging_dir.relative_to(app_root).as_posix(),
            "files": plan_files,
        }
        plan_dir = get_repair_plan_dir(app_root)
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_file = plan_dir / f"{run_id}.json"
        temp_file = plan_file.with_name(f".{plan_file.name}.tmp")
        temp_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_file.replace(plan_file)
        return PreparedRepair(
            plan_file=plan_file,
            staging_dir=staging_dir,
            candidate_count=len(check_result.candidates),
        )
    except Exception:
        for part_file in staging_dir.rglob("*.part") if staging_dir.exists() else ():
            part_file.unlink(missing_ok=True)
        raise
    finally:
        if own_session:
            http.close()
