"""旧 V2 服务薄封装：仍使用原下载器与 AddCostume 安装规则。"""
from pathlib import Path
import re
import shutil

from live2d_download.bestdori_client import BestdoriClient
from live2d_download.live2d_downloader import Live2dDownloader
from live2d_download.live2d_service import Live2dService
from live2d_download.models import CancelToken, CancelledError
from ui_constants import AddCostume
from .catalog import APP_ROOT, PROJECT_ROOT, CHARACTERS
from .service import Cancelled, DownloadError, check_cancel


class LegacyService:
    def catalog(self, character, cancel):
        """查询并校验资源目录，明确区分空目录与网络失败。"""
        check_cancel(cancel)
        try:
            with BestdoriClient(timeout_seconds=15).session as session:
                service = Live2dService(BestdoriClient(timeout_seconds=15, session=session))
                result = service.search_costumes(CHARACTERS[character]["bestdori_index"])
            check_cancel(cancel)
            if not all(re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in result):
                raise ValueError()
            return result
        except Cancelled:
            raise
        except Exception:
            raise DownloadError("Bestdori 目录读取失败，请重试") from None

    def metadata(self, costume, cancel):
        """读取旧 V2 服装名称和图标，不改变安装缓存。"""
        check_cancel(cancel)
        client = BestdoriClient(timeout_seconds=15)
        try:
            service = Live2dService(client)
            name = service.get_costume_name(costume, other_language=True)
            check_cancel(cancel)
            icon = service.get_costume_icon(costume)
            return (name.strip() if name and name != "Unknown" else costume, icon or b"")
        finally:
            client.session.close()

    def download(self, costume, title, selection, cancel, progress):
        """沿用旧 V2 下载和安装流程，使用任务快照并仅清理本次服装缓存。"""
        info = CHARACTERS[selection.source]
        client = BestdoriClient(timeout_seconds=15)
        try:
            if selection.mode == "new" and (PROJECT_ROOT / "live2d_related" / selection.source).exists():
                raise DownloadError("该角色已存在，请从“为软件包内角色添加服装”入口下载")

            def report(*, file=None, model=None):
                """将 V2 文件数量进度转换为统一任务信号所用单位。"""
                if model is not None:
                    progress(model.files_done * 1024, model.files_total * 1024)

            Live2dDownloader(client).download_live2d_name(
                live2d_name=costume, root_dir=APP_ROOT / ".model_download_cache",
                progress=report, cancel=CancelToken(cancel))
            check_cancel(cancel)
            # 旧安装器依赖 GPT_SoVITS 工作目录，独立入口在启动时固定该目录。
            if selection.mode == "existing":
                safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).rstrip(". ") or costume
                if safe_title in (".", ".."):
                    safe_title = costume
                return Path(AddCostume.add_costume_for_existed_char(
                    selection.target_id, costume, safe_title))
            if (PROJECT_ROOT / "live2d_related" / selection.source).exists():
                raise DownloadError("该角色已存在，无法重复安装")
            AddCostume.add_costume_for_new_character(info["display_name"], selection.source, costume)
            return PROJECT_ROOT / "live2d_related" / selection.source
        except CancelledError:
            raise Cancelled("下载已取消") from None
        except (Cancelled, DownloadError):
            raise
        except Exception:
            raise DownloadError("V2 下载或安装失败，请重试；若已生成不完整角色目录，请先检查该目录") from None
        finally:
            client.session.close()
            # 仅清理本次 costume 的临时目录，不删除共享 HTTP 缓存或其他任务文件。
            root = (APP_ROOT / ".model_download_cache").resolve()
            folder = (root / costume).resolve()
            if folder.parent == root and folder.is_dir() and not (root / costume).is_symlink():
                shutil.rmtree(folder, ignore_errors=True)
