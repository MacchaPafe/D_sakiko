from dataclasses import dataclass
import json
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from .live2d_service import Live2dService
from .bestdori_client import BestdoriClient
from .asset_cache import AssetCache, LinkMode
from .models import (
    CancelToken,
    FileProgress,
    AssetKey,
    Live2dCostume,
    Live2dFileSpec,
    ModelProgress,
    ProgressCallback,
    Language,
    validate_rel_path,
)


@dataclass(frozen=True)
class DownloadResult:
    live2d_name: str
    model_dir: Path
    downloaded_files: list[Path]
    skipped_files: list[Path]


class Live2dDownloader:
    def __init__(
        self,
        client: BestdoriClient,
        language: Language = Language.SIMPLIFIED_CHINESE,
        *,
        max_workers: int = 10,
        use_asset_cache: bool = True,
        asset_cache: Optional[AssetCache] = None,
        link_mode: LinkMode = LinkMode.AUTO,
    ) -> None:
        """
        创建一个下载器客户端
        
        :param client: BestdoriClient 实例，直接创建一个然后传进来就行
        :param language: 访问服务器使用的语言，会影响获得的字符串等数据
        :param max_workers: 下载一个 live2d 模型时，最多并发下载的文件数量
        """
        self.client = client
        self.max_workers = max_workers
        self._service = None  # 延迟初始化 Live2dService
        self.language = language

        self.use_asset_cache = use_asset_cache
        self.asset_cache = asset_cache or AssetCache.global_default()
        self.link_mode = link_mode
    
    @property
    def service(self) -> Live2dService:
        if self._service is None:
            self._service = Live2dService(self.client, language=self.language)
        return self._service

    def download_costume(
        self,
        costume: Live2dCostume,
        *,
        root_dir: Path,
        overwrite: bool = False,
        cancel: Optional[CancelToken] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> DownloadResult:
        """并发下载一套服装到本地，并生成 model.json。

        并发模型：
        - 使用 ThreadPoolExecutor(max_workers=self.max_workers)
        - 每个 Live2dFileSpec 一个任务
        - 主线程收集 future 完成情况并更新 model/files 进度
        """

        live2d_name = costume.live2d_name
        # 实际下载到的目标文件夹，为 root_dir / live2d_name（live2d 内部名称）
        model_dir = root_dir / live2d_name
        model_dir.mkdir(parents=True, exist_ok=True)

        files_total = len(costume.files)
        files_done = 0
        downloaded_files: list[Path] = []
        skipped_files: list[Path] = []

        progress_cb = self._wrap_progress(progress)
        if progress_cb is not None:
            progress_cb(model=self._build_model_progress(live2d_name, files_done, files_total))

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(
                    self._download_one,
                    live2d_name=live2d_name,
                    spec=spec,
                    model_dir=model_dir,
                    overwrite=overwrite,
                    cancel=cancel,
                    progress=progress_cb,
                )
                for spec in costume.files
            ]
            
            try:
                for fut in as_completed(futures):
                    rel_path, full_path, skipped = fut.result()
                    if skipped:
                        skipped_files.append(full_path)
                    else:
                        downloaded_files.append(full_path)

                    files_done += 1
                    if progress_cb is not None:
                        progress_cb(model=self._build_model_progress(live2d_name, files_done, files_total))

            except Exception:
                # 若发生异常（包括取消），尽量取消未开始的任务
                for fut in futures:
                    fut.cancel()
                raise

        # 校验：所有非 optional 的文件都应存在
        missing_required = [
            spec.rel_path
            for spec in costume.files
            if (not spec.optional) and (not (model_dir / Path(spec.rel_path)).exists())
        ]
        if missing_required:
            raise RuntimeError(
                "下载完成但发现缺失必需文件：" + ", ".join(missing_required)
            )

        # 生成 model.json：只把实际存在的文件纳入（尤其是 optional 404 的 physics.json）
        present_specs = [
            spec for spec in costume.files if (model_dir / Path(spec.rel_path)).exists()
        ]
        costume_present = Live2dCostume(
            live2d_name=costume.live2d_name,
            files=present_specs,
        )
        model_json = costume_present.render_model_json()
        with open(model_dir / "model.json", "w", encoding="utf-8") as f:
            json.dump(model_json, f, ensure_ascii=False, indent=2)

        return DownloadResult(
            live2d_name=live2d_name,
            model_dir=model_dir,
            downloaded_files=downloaded_files,
            skipped_files=skipped_files,
        )

    @staticmethod
    def _build_model_progress(live2d_name: str, files_done: int, files_total: int) -> ModelProgress:
        return ModelProgress(live2d_name, files_done, files_total)

    @staticmethod
    def _wrap_progress(progress: Optional[ProgressCallback]) -> Optional[ProgressCallback]:
        """把 progress 回调包装成线程安全（串行化）的版本。

        说明：download_costume 会把该回调传给 _download_one，所以 file/model 两级回调都会被同一把锁保护。
        """
        if progress is None:
            return None
        lock = Lock()

        def _safe_progress(*, file=None, model=None):
            with lock:
                progress(file=file, model=model)

        return _safe_progress

    @staticmethod
    def _safe_prepare_destination(model_dir: Path, rel_path: Path) -> Path:
        """在 model_dir 下为 rel_path 创建目录并返回最终 full_path。

        额外防护：
        - 拒绝在任何 symlink 目录之下写入
        - 拒绝写入到 symlink 文件
        """
        model_root = model_dir.resolve()

        full_path = model_dir / rel_path
        full_resolved = full_path.resolve(strict=False)
        try:
            is_inside = full_resolved.is_relative_to(model_root)
        except AttributeError:
            # Python < 3.9 兼容（本项目一般不需要，但保底一下）
            is_inside = str(full_resolved).startswith(str(model_root) + "/")
        if not is_inside:
            raise ValueError(f"非法路径（越界写入）: {rel_path.as_posix()}")

        # 逐级创建目录并检查 symlink，避免 parents=True 跟随已有 symlink 目录
        current = model_dir
        for part in rel_path.parts[:-1]:
            current = current / part
            if current.exists():
                if current.is_symlink():
                    raise ValueError(f"非法路径（父目录为 symlink）: {current}")
                if not current.is_dir():
                    raise ValueError(f"非法路径（父路径不是目录）: {current}")
            else:
                current.mkdir(exist_ok=True)

        if full_path.exists() and full_path.is_symlink():
            raise ValueError(f"非法路径（目标文件为 symlink）: {full_path}")

        return full_path

    def download_live2d_name(
        self,
        live2d_name: str,
        *,
        root_dir: Path,
        overwrite: bool = False,
        cancel: Optional[CancelToken] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> DownloadResult:
        """便捷方法：通过传入角色的服装名称和角色 ID，直接下载服装"""
        costume = self.service.build_costume(live2d_name)
        return self.download_costume(
            costume,
            root_dir=root_dir,
            overwrite=overwrite,
            cancel=cancel,
            progress=progress,
        )

    # --- 内部：下载单文件（建议拆出以便测试） ---
    def _download_one(
        self,
        *,
        live2d_name: str,
        spec: Live2dFileSpec,
        model_dir: Path,
        overwrite: bool,
        cancel: Optional[CancelToken],
        progress: Optional[ProgressCallback],
    ) -> tuple[str, Path, bool]:
        """下载单个文件

        :param live2d_name: 服装名称，例如 037_casual-2023
        :param spec: 要下载的文件信息
        :param model_dir: 模型下载的目标根目录
        :param overwrite: 是否覆盖已存在的文件。如果此选项设置为 False，且目标文件已经存在，则跳过此次下载。
        :param cancel: 可选的取消令牌
        :param progress: 可选的进度回调函数
        返回下载内容相对模型根目录的路径、下载内容的绝对路径以及是否跳过了下载。
        如果跳过了下载（第三个返回值为 True)，那么不保证前两个路径真实存在。
        """
        if cancel is not None:
            cancel.raise_if_cancelled()

        rel_path = validate_rel_path(spec.rel_path)
        full_path = self._safe_prepare_destination(model_dir, rel_path)
        # 如果文件存在且不允许覆盖，则跳过下载
        if full_path.exists() and not overwrite:
            if progress is not None:
                progress(file=FileProgress(live2d_name, spec.rel_path, 0, 0, event="skipped"))
            return spec.rel_path, full_path, True

        # --- 优先走全局资产缓存：下载进缓存，再硬链接/复制到模型目录 ---
        if self.use_asset_cache:
            key = spec.source
            # 兜底：将 client.server 纳入 key，避免跨服串用
            if key.server is None:
                key = AssetKey(key.bundle_name, key.file_name, server=self.client.server)

            def _on_bytes(done: int, total: Optional[int]) -> None:
                if progress is not None:
                    progress(file=FileProgress(
                        live2d_name,
                        spec.rel_path,
                        done,
                        total,
                        event="download",
                    ))

            result = self.asset_cache.get_or_download(
                key=key,
                open_stream=lambda: self.client.iter_download_asset(
                    bundle_name=key.bundle_name,
                    file_name=key.file_name,
                    cancel=cancel,
                    allow_not_found=spec.optional,
                ),
                allow_not_found=spec.optional,
                cancel=cancel,
                on_bytes=_on_bytes,
            )

            if result.path is None:
                # optional 缺失
                if progress is not None:
                    progress(file=FileProgress(live2d_name, spec.rel_path, 0, 0, event="missing_optional"))
                return spec.rel_path, full_path, True

            if progress is not None and result.event == "cache_hit":
                progress(file=FileProgress(live2d_name, spec.rel_path, 0, 0, event="cache_hit"))

            action = self.asset_cache.materialize_to(
                cache_path=result.path,
                dest_path=full_path,
                mode=self.link_mode,
                overwrite=overwrite,
            )

            if progress is not None:
                if result.event == "downloaded":
                    progress(file=FileProgress(live2d_name, spec.rel_path, 0, 0, event="downloaded"))
                if action in ("linked", "copied", "skipped"):
                    progress(file=FileProgress(live2d_name, spec.rel_path, 0, 0, event=action))

            return spec.rel_path, full_path, action == "skipped"

        # 开始下载
        stream = self.client.iter_download_asset(
            bundle_name=spec.source.bundle_name,
            file_name=spec.source.file_name,
            cancel=cancel,
            allow_not_found=spec.optional,
        )
        if stream is None:
            # 允许文件不存在，且文件确实不存在
            if progress is not None:
                # 发送一个进度回调
                progress(file=FileProgress(live2d_name, spec.rel_path, 0, 0, event="missing_optional"))
            return spec.rel_path, full_path, True

        with stream, open(full_path, "wb") as f:
            for chunk in stream.iter_bytes():
                f.write(chunk)
                if progress is not None:
                    progress(file=FileProgress(
                        live2d_name,
                        spec.rel_path,
                        stream.downloaded_bytes,
                        stream.total_bytes,
                        event="download",
                    ))

        if progress is not None:
            progress(file=FileProgress(
                live2d_name,
                spec.rel_path,
                stream.downloaded_bytes,
                stream.total_bytes,
                event="downloaded",
            ))

        return spec.rel_path, full_path, False
