"""无网络回归：鉴权边界、文件完整性、取消和 Qt 页面生命周期。"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GPT_SoVITS"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from live2d_download.hosted.service import ResourceService, Resource, DownloadError, Cancelled, validate_key


class Response:
    # 构造测试替身的响应、连接或同步状态。
    def __init__(self, data=b"zip-data", status=200, length=None):
        self.data, self.status_code = data, status
        self.headers = {"Content-Length": str(len(data) if length is None else length)}

    # 模拟网络上下文进入。
    def __enter__(self): return self
    # 模拟网络上下文退出。
    def __exit__(self, *args): pass
    # 把响应分成两块，以覆盖下载中途取消。
    def iter_content(self, size):
        yield self.data[:3]
        yield self.data[3:]


class Session:
    # 构造测试替身的响应、连接或同步状态。
    def __init__(self, response):
        self.headers, self.response, self.calls = {}, response, []
    # 模拟网络上下文进入。
    def __enter__(self): return self
    # 模拟网络上下文退出。
    def __exit__(self, *args): pass
    # 记录请求参数并返回预设响应。
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class ServiceTests(unittest.TestCase):
    # 准备每项测试独立使用的服务、资源或窗口。
    def setUp(self):
        self.service = ResourceService("test-only-token")
        self.resource = Resource("model", "ournotes/anon/model/r1.zip", "test", "anon",
                                 hashlib.sha256(b"zip-data").hexdigest(), "model")

    # 执行下载测试或模拟可取消的慢下载。
    def download(self, folder, response, event=None, progress=None):
        session = Session(response)
        with patch("live2d_download.hosted.service.requests.Session", return_value=session):
            result = self.service.download(self.resource, Path(folder), event or Event(), progress or (lambda *_: None))
        return result, session

    # 验证成功校验、同名另存及 Token 不进入 URL。
    def test_success_and_existing_file_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "r1.zip"
            original.write_bytes(b"user-file")
            path, session = self.download(folder, Response())
            self.assertEqual(path.name, "r1 (1).zip")
            self.assertEqual(path.read_bytes(), b"zip-data")
            self.assertEqual(original.read_bytes(), b"user-file")
            self.assertFalse(list(Path(folder).glob("*.part")))
            self.assertFalse(session.calls[0][1]["allow_redirects"])
            self.assertEqual(session.headers["Authorization"], "Bearer test-only-token")
            self.assertNotIn("test-only-token", session.calls[0][0])

    # 验证摘要不符不留下完整或部分文件。
    def test_image_overwrite_and_failure_preserves_original(self):
        """图片同名直接覆盖，校验失败仍保留旧图且不生成编号副本。"""
        for kind,key in (("background","images/backgrounds/a.png"),("avatar","images/chat-icons/anon/a.png")):
            self.resource = Resource(kind,key,"a","anon",hashlib.sha256(b"zip-data").hexdigest())
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder)/"a.png"
                target.write_bytes(b"old-image")
                with self.assertRaises(DownloadError):
                    self.download(folder,Response(b"damaged"))
                self.assertEqual(target.read_bytes(),b"old-image")
                path,_ = self.download(folder,Response())
                self.assertEqual(path,target)
                self.assertEqual(path.read_bytes(),b"zip-data")
                self.assertEqual(list(Path(folder).iterdir()),[target])

    def test_hash_mismatch_never_publishes(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(DownloadError, "SHA-256"):
                self.download(folder, Response(b"damaged"))
            self.assertEqual(list(Path(folder).iterdir()), [])

    # 验证长度不符不发布下载结果。
    def test_length_mismatch_never_publishes(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(DownloadError, "大小"):
                self.download(folder, Response(length=99))
            self.assertEqual(list(Path(folder).iterdir()), [])

    # 验证流读取中取消会清理临时文件。
    def test_cancel_during_stream_removes_partial(self):
        event = Event()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(Cancelled):
                self.download(folder, Response(), event, lambda *_: event.set())
            self.assertEqual(list(Path(folder).iterdir()), [])

    # 验证重定向和 HTTP 错误正文不被保存为资源。
    def test_no_redirect_or_unauthorized_body_saved(self):
        for status in (302, 401, 403, 404, 503):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(DownloadError):
                    self.download(folder, Response(status=status))
                self.assertEqual(list(Path(folder).iterdir()), [])

    # 验证越界路径及 Windows 特殊名称被拒绝。
    def test_paths_reject_escape_and_windows_special_names(self):
        for key in ("https://evil/zip", "ournotes/../x", "ournotes/a\\b", "images/a:b", "images/CON.png", "images/a./x"):
            with self.subTest(key=key), self.assertRaises(DownloadError):
                validate_key(key)

    # 验证目录摘要传递及来源角色隔离。
    def test_catalog_carries_sha_and_rejects_foreign_character(self):
        row = dict(r2_key=self.resource.key, character_id="anon", model_id="model", name=None,
                   sha256=self.resource.sha256)
        for character in ("anon", "tomori"):
            session = Session(Response(json.dumps({"items": [row]}).encode()))
            with patch("live2d_download.hosted.service.requests.Session", return_value=session):
                if character == "anon":
                    resources = self.service.catalog("model", character, Event())
                    self.assertEqual(resources[0].sha256, self.resource.sha256)
                else:
                    with self.assertRaises(DownloadError): self.service.catalog("model", character, Event())


from PyQt5.QtWidgets import QApplication
from live2d_download.hosted.ui import DownloadWizardWindow
from live2d_download.hosted.catalog import BANDS, CHARACTERS
from live2d_download.hosted.catalog import Selection
from live2d_download.hosted.legacy import LegacyService


class LegacyBridgeTests(unittest.TestCase):
    # 验证 V2 使用目标快照，且不清理其他任务缓存。
    def test_existing_target_snapshot_and_cache_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / ".model_download_cache" / "036_test"
            cache.mkdir(parents=True)
            (cache / "model.json").write_text("{}")
            sibling = cache.parent / "unrelated"
            sibling.mkdir()
            with (patch("live2d_download.hosted.legacy.APP_ROOT", root),
                  patch("live2d_download.hosted.legacy.PROJECT_ROOT", root),
                  patch("live2d_download.hosted.legacy.BestdoriClient"),
                  patch("live2d_download.hosted.legacy.Live2dDownloader"),
                  patch("live2d_download.hosted.legacy.AddCostume") as installer):
                selection = Selection("existing", "tomori", "custom", "自定义角色")
                LegacyService().download("036_test", "常服", selection, Event(), lambda *_: None)
                installer.add_costume_for_existed_char.assert_called_once_with("custom", "036_test", "常服")
                installer.add_costume_for_new_character.assert_not_called()
            self.assertFalse(cache.exists())
            self.assertTrue(sibling.exists())

    # 验证下载后已取消的 V2 任务不会继续安装。
    def test_cancel_after_download_never_installs(self):
        event = Event()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("live2d_download.hosted.legacy.APP_ROOT", Path(directory)),
            patch("live2d_download.hosted.legacy.BestdoriClient"),
            patch("live2d_download.hosted.legacy.Live2dDownloader") as downloader,
            patch("live2d_download.hosted.legacy.AddCostume") as installer,
        ):
            downloader.return_value.download_live2d_name.side_effect = lambda **_: event.set()
            with self.assertRaises(Cancelled):
                LegacyService().download("036_test", "常服", Selection("existing", "tomori", "custom"), event, lambda *_: None)
            installer.add_costume_for_existed_char.assert_not_called()


class FakeService:
    # 构造测试替身的响应、连接或同步状态。
    def __init__(self):
        self.started = Event()
        self.fail = False
    # 返回空预览，避免测试访问网络。
    def preview(self, key, event, refresh=False): return b""
    # 返回预设目录，按开关模拟目录查询失败。
    def catalog(self, kind, character, event):
        if self.fail: raise DownloadError("模拟网络失败")
        key = f"ournotes/{character}/model/r1.zip" if kind == "model" else (
            "images/backgrounds/a.png" if kind == "background" else f"images/chat-icons/{character}/a.png")
        return [Resource(kind, key, "sample", character, model_id="model")]
    # 执行下载测试或模拟可取消的慢下载。
    def download(self, resource, directory, cancel, progress):
        self.started.set()
        cancel.wait(3)
        if cancel.is_set(): raise Cancelled()
        return Path(directory) / resource.filename


class FakeLegacy:
    # 返回预设目录，按开关模拟目录查询失败。
    def catalog(self, character, event): return []
    # 返回预设 V2 名称，不访问 Bestdori。
    def metadata(self, costume, event): return costume, b""


class QtTests(unittest.TestCase):
    @classmethod
    # 为 Qt 回归复用一个离屏应用实例。
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    # 准备每项测试独立使用的服务、资源或窗口。
    def setUp(self):
        self.service = FakeService()
        self.window = DownloadWizardWindow([SimpleNamespace(character_name="本地爱音", character_folder_name="custom")],
                                           self.service, FakeLegacy())
        self.window.show()
    # 驱动 Qt 事件循环，等待异步条件并在超时后断言失败。
    def wait_for(self, predicate):
        until = time.monotonic() + 4
        while not predicate() and time.monotonic() < until:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        self.assertTrue(predicate())
    # 等待后台停止后关闭测试窗口。
    def tearDown(self):
        self.window.close()
        self.wait_for(self.window.hub.idle)
        self.window.close()
        self.app.processEvents()

    # 验证分组不重不漏，且十二张队徽存在。
    def test_all_groups_have_assets_and_unique_members(self):
        members = [c for _, _, values in BANDS for c in values]
        self.assertEqual(len(members), len(set(members)))
        self.assertEqual(set(members), set(CHARACTERS))
        for identifier, _, _ in BANDS[:-1]:
            self.assertTrue((ROOT / "GPT_SoVITS/assets/band_logo" / (identifier + ".png")).is_file())

    def test_drag_integer_coordinates_return_and_reverse(self):
        """复现整数鼠标位移，验证左拖、右拖及不足阈值回弹不会产生空动画帧。"""
        from PyQt5.QtCore import Qt
        self.window.choose_mode("new")
        carousel = self.window.characters.carousel
        frames = []
        carousel.slide.valueChanged.connect(frames.append)
        for direction, expected in ((-1,1),(1,0)):
            start = int(carousel.width()/2)
            finish = start + direction*int(carousel.card_step()*.3)
            carousel.mousePressEvent(SimpleNamespace(button=lambda:Qt.LeftButton,x=lambda:start))
            carousel.mouseMoveEvent(SimpleNamespace(x=lambda:finish))
            carousel.mouseReleaseEvent(SimpleNamespace(x=lambda:finish))
            self.wait_for(lambda: carousel.index == expected and carousel.drag_offset == 0)
        start = int(carousel.width()/2)
        carousel.mousePressEvent(SimpleNamespace(button=lambda:Qt.LeftButton,x=lambda:start))
        carousel.mouseMoveEvent(SimpleNamespace(x=lambda:start-2))
        carousel.mouseReleaseEvent(SimpleNamespace(x=lambda:start-2))
        self.wait_for(lambda: carousel.drag_offset == 0)
        self.assertEqual(carousel.index,0)
        self.assertTrue(frames)
        self.assertTrue(all(isinstance(frame,(int,float)) for frame in frames))
        carousel._slide(None)
        self.assertEqual(carousel.drag_offset,0)

    def test_background_retargets_from_current_color(self):
        """验证快速切换乐队从当前色继续过渡，且不改动资源选择。"""
        self.window.choose_mode("new")
        self.window.characters.select_band(3)
        animation = self.window.background_animation
        animation.setCurrentTime(180)
        current = self.window.background_color.name()
        self.window.characters.select_band(9)
        self.assertEqual(animation.startValue().name(),current)
        self.wait_for(lambda: animation.state() == animation.Stopped)
        self.assertEqual(self.window.background_color,animation.endValue())
        self.assertEqual(self.window.selection.source,"arale")

    def test_cached_center_and_neighbors_fade_again(self):
        """验证缓存立绘初次进入时淡入，切换和回弹只对新进入角色淡入。"""
        from PyQt5.QtGui import QPixmap
        self.window.choose_mode("new")
        carousel = self.window.characters.carousel
        visible = [carousel.members[i] for i in (-1,0,1)]
        for character in carousel.members:
            carousel.pictures[character] = QPixmap(10,10)
        carousel.visible_characters.clear()
        carousel.changed()
        self.assertTrue(all(carousel.alphas[c] == 0 for c in visible))
        self.wait_for(lambda: all(carousel.alphas[c] == 1 for c in visible))
        carousel.changed()
        self.assertTrue(all(carousel.alphas[c] == 1 for c in visible))
        carousel.shift(1)
        self.wait_for(lambda:carousel.index == 1)
        retained = {carousel.members[0],carousel.members[1]}
        self.assertTrue(all(carousel.alphas[c] == 1 for c in retained))
        newcomer = carousel.members[2]
        self.assertLess(carousel.alphas[newcomer],1)
        self.wait_for(lambda:carousel.alphas[newcomer] == 1)
        carousel.shift(0)
        self.wait_for(lambda:carousel.slide.state() == carousel.slide.Stopped)
        self.assertTrue(all(carousel.alphas[c] == 1 for c in carousel.visible_characters))

    def test_watermark_crossfade_and_others(self):
        """验证中途切换保留当前水印权重，Others 平滑清空。"""
        self.window.choose_mode("new")
        self.window.characters.select_band(3)
        self.window.watermark_animation.setCurrentTime(150)
        before = dict(self.window.watermark_weights)
        self.window.characters.select_band(9)
        self.assertEqual(self.window.watermark_from,before)
        self.wait_for(lambda:self.window.watermark_animation.state() == self.window.watermark_animation.Stopped)
        self.assertEqual(self.window.watermark_weights,{9:1.0})
        self.window.characters.select_band(12)
        self.wait_for(lambda:self.window.watermark_animation.state() == self.window.watermark_animation.Stopped)
        self.assertEqual(self.window.watermark_weights,{})

    def test_window_resizable_and_download_cache_is_local(self):
        """验证初始尺寸不锁定窗口，临时下载目录位于项目缓存下。"""
        from live2d_download.hosted.catalog import CACHE_ROOT
        old_width = self.window.width()
        self.window.resize(old_width+30,self.window.height()+20)
        self.app.processEvents()
        self.assertEqual(self.window.width(),old_width+30)
        self.assertTrue(self.window.zip_root.is_relative_to(CACHE_ROOT))
        self.assertTrue(CACHE_ROOT.is_relative_to(ROOT))

    def test_image_grid_pagination_and_global_filter(self):
        """验证图片只创建当前页、跨页搜索有效，翻页使旧预览回调失效。"""
        self.service.catalog = lambda kind,character,event: [
            Resource("background",f"images/backgrounds/{i:03d}.png",f"image-{i:03d}","")
            for i in range(55)]
        self.window.choose_mode("background")
        page = self.window.resources
        self.wait_for(lambda:len(page.rows) == 24)
        generation = page.generation
        self.assertEqual(page.page_label.text(),"1 / 3")
        page.turn_page(1)
        self.assertEqual(len(page.rows),24)
        self.assertGreater(page.generation,generation)
        self.assertEqual(page.rows[0][2].title,"image-024")
        page.turn_page(1)
        self.assertEqual(len(page.rows),7)
        self.assertFalse(page.next_page.isEnabled())
        self.assertFalse(hasattr(page,"search"))

    def test_background_gallery_style_hooks_and_centering(self):
        """验证背景页样式名称及单张筛选结果在容器中居中。"""
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QFrame
        self.window.choose_mode("background")
        page = self.window.resources
        self.wait_for(lambda:bool(page.rows))
        listing,item,row = page.rows[0]
        self.assertIsInstance(row,QFrame)
        self.assertEqual(row.objectName(),"resourceCard")
        self.assertEqual(row.button.objectName(),"downloadItemBtn")
        self.assertEqual(listing.objectName(),"galleryContainer")
        self.assertEqual(page.previous_page.objectName(),"pageBtn")
        self.assertEqual(page.page_label.objectName(),"pageIndicator")
        self.assertTrue(page.heading.isHidden())
        self.assertFalse(hasattr(page,"search"))
        self.assertTrue(row.name.isHidden())
        self.assertTrue(row.status.isHidden())
        self.assertEqual(listing.verticalScrollBarPolicy(),Qt.ScrollBarAlwaysOn)
        self.assertNotIn("每页",page.panels["background"][1].text())
        generation = page.generation
        page.names_toggle.setChecked(True)
        self.assertFalse(row.name.isHidden())
        self.assertEqual(page.generation,generation)
        page.names_toggle.setChecked(False)
        listing.center_items()
        self.app.processEvents()
        rect = listing.visualItemRect(item)
        self.assertLessEqual(abs(rect.center().x()-listing.viewport().rect().center().x()),3)
        self.assertEqual(row.layout().contentsMargins().left(),0)
        self.assertEqual(row.layout().spacing(),0)
        self.assertEqual(row.bottom_bar.objectName(),"cardBottomBar")
        self.assertEqual(row.bottom_bar.height(),self.window.metrics.px(32))
        self.assertEqual(row.picture.width(),row.width())
        self.assertLess(row.button.width(),row.width()/2)

    def test_image_gallery_four_columns_resize_and_center(self):
        """验证调整窗口宽度后仍为四列，卡片自适应且整体左右留白对称。"""
        self.service.catalog = lambda *args: [
            Resource("background",f"images/backgrounds/{i}.png",str(i),"") for i in range(24)]
        self.window.choose_mode("background")
        page = self.window.resources
        self.wait_for(lambda:len(page.rows) == 24)
        widths = []
        for width in (780,1040):
            self.window.resize(width,600)
            for _ in range(10):
                self.app.processEvents()
            listing = page.rows[0][0]
            rects = [listing.visualItemRect(listing.item(i)) for i in range(5)]
            self.assertEqual(rects[0].top(),rects[3].top())
            self.assertGreater(rects[4].top(),rects[0].top())
            margins = listing.viewportMargins()
            left = margins.left()+rects[0].left()
            right = margins.right()+listing.viewport().width()-rects[3].right()-1
            self.assertLessEqual(abs(left-right),3)
            widths.append(rects[0].width())
        self.assertGreater(widths[1],widths[0])

    def test_original_preview_reads_full_image_and_closes_safely(self):
        """验证原图按窗口适配、下载按钮居中、关闭按钮和 Escape 实际关闭窗口。"""
        from PyQt5.QtCore import QBuffer,QIODevice
        from PyQt5.QtGui import QPixmap
        from live2d_download.hosted.ui import OriginalPreview
        pixmap = QPixmap(1600,900)
        pixmap.fill()
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        pixmap.save(buffer,"PNG")
        data = bytes(buffer.data())
        requested = []
        def preview(key,event,refresh=False):
            """记录预览请求并提供可解码的原图。"""
            requested.append(key)
            return data
        self.service.preview = preview
        self.window.choose_mode("background")
        page = self.window.resources
        self.wait_for(lambda:bool(page.rows))
        self.assertEqual(page.panels["background"][1].text(),"Ournotes 故事模式背景图")
        row = page.rows[0][2]
        dialog = OriginalPreview(row)
        dialog.show()
        self.wait_for(lambda:dialog.pixmap is not None)
        self.assertIn(row.entry.key,requested)
        self.assertFalse(hasattr(dialog,"actual_size"))
        self.assertLessEqual(dialog.picture.width(),dialog.scroll.viewport().width())
        self.assertLessEqual(abs(dialog.save.geometry().center().x()-dialog.rect().center().x()),2)
        dialog.close()
        self.assertFalse(dialog.isVisible())
        dialog.loaded(data,"")
        self.assertTrue(dialog.closed)
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest
        dialog = OriginalPreview(row)
        dialog.show()
        job = dialog.job
        QTest.keyClick(dialog,Qt.Key_Escape)
        self.assertFalse(dialog.isVisible())
        self.assertTrue(dialog.closed)
        if job in self.window.hub.jobs:
            self.assertTrue(self.window.hub.jobs[job][0].is_set())

    def test_resource_preview_never_shows_as_top_level_window(self):
        """捕获控件显示事件，防止构建预览标签时出现临时独立小窗口。"""
        from PyQt5.QtCore import QObject,QEvent
        from PyQt5.QtWidgets import QWidget
        from live2d_download.hosted.ui import ResourceRow
        shown = []
        class Observer(QObject):
            def eventFilter(self, watched, event):
                """记录构建资源条目期间显示的顶层窗口。"""
                if event.type() == QEvent.Show and isinstance(watched,QWidget) and watched.isWindow():
                    shown.append(watched)
                return False
        observer = Observer()
        self.app.installEventFilter(observer)
        try:
            for entry,legacy in ((Resource("background","images/backgrounds/a.png","a",""),False),
                                 (Resource("avatar","images/chat-icons/anon/a.png","a","anon"),False),
                                 (Resource("model","ournotes/anon/a.zip","a","anon"),False),
                                 ("costume",True)):
                row = ResourceRow(self.window.resources,entry,legacy)
                self.app.processEvents()
                self.assertFalse(row.picture.isWindow())
                self.assertFalse(row.detail.isWindow())
            self.assertEqual(shown,[])
        finally:
            self.app.removeEventFilter(observer)

    def test_model_columns_have_scrollbars_refresh_and_placeholder(self):
        """验证双列独立滚动条、容器刷新入口与默认预览占位图。"""
        from PyQt5.QtCore import Qt
        self.window.legacy.catalog = lambda *args:[f"costume-{i}" for i in range(20)]
        self.window.show_resources()
        page = self.window.resources
        self.wait_for(lambda:len(page.rows) == 21)
        self.assertFalse(hasattr(page,"search"))
        self.assertEqual(len(page.refresh_buttons),2)
        for box,status,listing in page.panels.values():
            self.assertEqual(listing.verticalScrollBarPolicy(),Qt.ScrollBarAlwaysOn)
            self.assertGreater(listing.verticalScrollBar().width(),0)
            self.assertTrue(any(button.parent() is box for button in page.refresh_buttons))
        for listing,item,row in page.rows:
            self.assertFalse(row.picture.pixmap().isNull())
            self.assertEqual(row.content_layout.spacing(),self.window.metrics.px(4))

    def test_avatar_grid_and_page_styles_are_separate(self):
        """验证头像使用缩略图网格，页面只应用自己在集中样式文件中的规则。"""
        from PyQt5.QtWidgets import QListWidget
        from live2d_download.hosted.appearance import PAGE_STYLES
        self.assertEqual(set(PAGE_STYLES),{"common","home","characters","backgrounds","avatars","models"})
        self.window.choose_mode("avatar")
        self.window.show_resources()
        page = self.window.resources
        self.wait_for(lambda:bool(page.rows))
        self.assertEqual(page.panels["avatar"][2].viewMode(),QListWidget.IconMode)
        self.assertTrue(page.rows[0][2].image_tile)
        self.assertEqual(page.styleSheet(),self.window.metrics.page_stylesheet("avatars"))
        self.assertTrue(page.heading.isHidden())
        self.assertFalse(hasattr(page,"search"))
        self.assertFalse(page.back.isHidden())
        row = page.rows[0][2]
        self.assertTrue(row.name.isHidden())
        self.assertTrue(row.status.isHidden())
        page.names_toggle.setChecked(True)
        self.assertFalse(row.name.isHidden())

    def test_navigation_preserves_character_and_requires_target(self):
        """验证默认角色页、跨类型保留来源、已有角色必选目标及背景直达网格。"""
        window = self.window
        self.assertIs(window.stack.currentWidget(),window.characters)
        self.assertEqual(window.selection.mode,"new")
        window.characters.select_band(9)
        source = window.selection.source
        window.type_tabs["avatar"].click()
        self.assertEqual(window.selection.mode,"avatar")
        self.assertEqual(window.selection.source,source)
        self.assertTrue(window.model_options.isHidden())
        window.type_tabs["model"].click()
        window.mode_tabs["existing"].click()
        self.assertEqual(window.selection.source,source)
        self.assertFalse(window.characters.next.isEnabled())
        self.assertTrue(window.characters.source_box.isEnabled())
        window.characters.receiver.setCurrentIndex(1)
        self.assertTrue(window.characters.next.isEnabled())
        window.type_tabs["background"].click()
        self.assertIs(window.stack.currentWidget(),window.resources)
        self.assertTrue(window.resources.back.isHidden())
        window.type_tabs["model"].click()
        self.assertEqual(window.selection.source,source)
        self.assertEqual(window.selection.target_id,"custom")
        window.update_navigation(True)
        self.assertFalse(window.navigation.isEnabled())
        window.update_navigation(False)

    def test_arrow_edge_hover_fades_and_disables_hidden_hitbox(self):
        """验证左右热区独立淡入淡出，中心区域与离开页面时隐藏且不拦截点击。"""
        from PyQt5.QtCore import QPoint,Qt
        carousel = self.window.characters.carousel
        carousel.arrow_hover_timer.stop()
        carousel.update_arrow_hover(QPoint(1,carousel.height()//2))
        self.wait_for(lambda:carousel.arrow_layers[0].graphicsEffect().opacity() == 1)
        self.assertFalse(carousel.arrow_layers[0].testAttribute(Qt.WA_TransparentForMouseEvents))
        carousel.update_arrow_hover(QPoint(carousel.width()-1,carousel.height()//2))
        self.wait_for(lambda:carousel.arrow_layers[1].graphicsEffect().opacity() == 1)
        self.assertEqual(carousel.arrow_layers[0].graphicsEffect().opacity(),0)
        self.assertTrue(carousel.arrow_layers[0].testAttribute(Qt.WA_TransparentForMouseEvents))
        carousel.update_arrow_hover(QPoint(carousel.width()//2,carousel.height()//2))
        self.wait_for(lambda:carousel.arrow_layers[1].graphicsEffect().opacity() == 0)
        self.assertTrue(carousel.arrow_layers[1].testAttribute(Qt.WA_TransparentForMouseEvents))

    # 验证接收角色必选，以及切换模式清空旧目标。
    def test_carousel_animation_settles_and_group_change_cancels(self):
        """验证中间帧确实移动缩放、结束提交选择以及换组取消旧动画。"""
        self.window.choose_mode("new")
        carousel = self.window.characters.carousel
        original = carousel.card_rect(0)
        carousel.shift(1)
        carousel.slide.setCurrentTime(120)
        self.assertEqual(carousel.index, 0)
        self.assertLess(carousel.card_rect(0).center().x(), original.center().x())
        self.assertLess(carousel.card_rect(0).width(), original.width())
        self.wait_for(lambda: carousel.index == 1)
        self.assertEqual(carousel.drag_offset, 0)
        carousel.shift(-1)
        self.window.characters.select_band(9)
        self.assertEqual(carousel.index, 0)
        self.assertEqual(carousel.drag_offset, 0)
        self.assertEqual(carousel.members[0], "arale")

    # 验证接收角色必选，以及切换模式清空旧目标。
    def test_existing_target_required_and_mode_resets_it(self):
        self.window.choose_mode("existing")
        self.assertFalse(self.window.characters.next.isEnabled())
        self.window.characters.receiver.setCurrentIndex(1)
        self.assertTrue(self.window.characters.next.isEnabled())
        self.assertEqual(self.window.selection.target_id, "custom")
        self.window.show_home()
        self.window.choose_mode("avatar")
        self.assertEqual(self.window.selection.target_id, "")
        self.assertTrue(self.window.characters.next.isEnabled())

    # 验证空版本收起而失败版本保留错误。
    def test_empty_v2_hides_but_failed_v3_does_not(self):
        self.window.choose_mode("new")
        self.window.show_resources()
        page = self.window.resources
        self.wait_for(lambda: "loading" not in page.states.values())
        self.assertTrue(page.panels["v2"][0].isHidden())
        self.service.fail = True
        page.load()
        self.wait_for(lambda: "loading" not in page.states.values())
        self.assertEqual(page.states["model"], "error")
        self.assertFalse(page.panels["model"][0].isHidden())

    # 验证取消请求后，后台结束前仍禁止返回。
    def test_cancel_blocks_navigation_until_worker_finishes(self):
        self.window.choose_mode("background")
        page = self.window.resources
        self.wait_for(lambda: bool(page.rows))
        row = page.rows[0][2]
        row.clicked()
        self.assertTrue(self.window.hub.busy)
        self.assertFalse(page.back.isEnabled())
        page.go_back()
        self.assertIs(self.window.stack.currentWidget(), page)
        row.clicked()
        self.assertFalse(row.button.isEnabled())
        self.wait_for(lambda: not self.window.hub.busy)
        self.assertTrue(page.back.isEnabled())
        self.assertIn("取消", row.status.text())

    # 验证关闭窗口先取消再等待线程池退出。
    def test_close_cancels_and_waits(self):
        self.window.choose_mode("background")
        self.wait_for(lambda: bool(self.window.resources.rows))
        self.window.resources.rows[0][2].clicked()
        self.window.close()
        self.assertTrue(self.window.closing)
        self.wait_for(lambda: not self.window.isVisible())
        self.assertTrue(self.window.hub.idle())

    # 验证过期目录回调不能改写新页面。
    def test_old_catalog_callback_cannot_change_new_page(self):
        self.window.choose_mode("background")
        old = self.window.resources.generation
        self.window.show_home()
        self.window.choose_mode("avatar")
        self.window.show_resources()
        self.window.resources.catalog_ready(old, "background", [], "stale")
        self.wait_for(lambda: "loading" not in self.window.resources.states.values())
        self.assertEqual(set(self.window.resources.panels), {"avatar"})


if __name__ == "__main__": unittest.main()
