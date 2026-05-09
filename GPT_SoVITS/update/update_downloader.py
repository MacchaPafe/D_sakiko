from __future__ import annotations

import shutil
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .update_models import DownloadedPatch, PatchInfo
from .update_security import verify_patch_asset


ProgressCallback = Callable[[int, int, float], None]


def choose_fastest_url(patch: PatchInfo, timeout: float = 5.0) -> str:
    """对 patch.urls 做轻量测速，返回最优下载 URL。"""

    scored_urls: list[tuple[float, str]] = []
    for item in patch.urls:
        request = Request(item.url, method="HEAD", headers={"User-Agent": "D_sakiko-Updater/1.0"})
        start = time.monotonic()
        try:
            with urlopen(request, timeout=timeout):
                scored_urls.append((time.monotonic() - start, item.url))
        except Exception:
            continue
    if scored_urls:
        return min(scored_urls, key=lambda item: item[0])[1]
    return patch.urls[0].url


def _download_from_url(
    url: str,
    dest_file: Path,
    total_size: int,
    progress_callback: ProgressCallback | None,
    timeout: float,
) -> None:
    """从单个 URL 下载文件。"""

    request = Request(url, headers={"User-Agent": "D_sakiko-Updater/1.0"})
    start = time.monotonic()
    downloaded = 0
    with urlopen(request, timeout=timeout) as response:
        with dest_file.open("wb") as file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
                downloaded += len(chunk)
                elapsed = max(time.monotonic() - start, 0.001)
                if progress_callback is not None:
                    progress_callback(downloaded, total_size, downloaded / elapsed)


def download_patch(
    patch: PatchInfo,
    dest_dir: Path,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    """下载 patch zip，支持失败换源；返回 zip 路径。"""

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / patch.file
    ordered_urls = [choose_fastest_url(patch)]
    ordered_urls.extend(item.url for item in patch.urls if item.url not in ordered_urls)
    errors: list[str] = []
    for url in ordered_urls:
        temp_file = dest_file.with_suffix(dest_file.suffix + ".part")
        temp_file.unlink(missing_ok=True)
        try:
            _download_from_url(url, temp_file, patch.size, progress_callback, timeout=60.0)
            temp_file.replace(dest_file)
            verify_patch_asset(dest_file, patch.sha256, patch.signature)
            return dest_file
        except (URLError, RuntimeError, OSError) as exc:
            temp_file.unlink(missing_ok=True)
            dest_file.unlink(missing_ok=True)
            errors.append(f"{url}: {exc}")
    raise RuntimeError("所有补丁下载源均失败：\n" + "\n".join(errors))


def extract_patch_zip(zip_path: Path, package_dir: Path) -> Path:
    """解压 patch zip，返回包含 manifest.json 和 patch.hdiff 的目录。"""

    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(package_dir)

    candidates = [package_dir]
    candidates.extend(child for child in package_dir.iterdir() if child.is_dir())
    for candidate in candidates:
        if (candidate / "manifest.json").exists() and (candidate / "patch.hdiff").exists():
            return candidate
    shutil.rmtree(package_dir, ignore_errors=True)
    raise RuntimeError(f"补丁包缺少 manifest.json 或 patch.hdiff：{zip_path}")


def download_and_prepare_patch(
    patch: PatchInfo,
    download_dir: Path,
    package_dir: Path,
    progress_callback: ProgressCallback | None = None,
) -> DownloadedPatch:
    """下载、校验、解压单个 patch。"""

    zip_path = download_patch(patch, download_dir, progress_callback)
    extracted_dir = extract_patch_zip(zip_path, package_dir / Path(patch.file).stem)
    return DownloadedPatch(patch=patch, zip_path=zip_path, package_dir=extracted_dir)

