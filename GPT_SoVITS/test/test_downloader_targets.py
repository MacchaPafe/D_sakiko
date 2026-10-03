"""下载器本地角色匹配、歧义处理及安装层冲突回归。"""
from __future__ import annotations

from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest

from live2d_download.hosted.catalog import CHARACTERS, Selection
from live2d_download.hosted.legacy import LegacyService
from live2d_download.hosted.service import DownloadError
from live2d_download.hosted.targets import LocalTarget, creation_conflicts, default_target, read_targets


@pytest.mark.parametrize(
    ("targets", "expected"),
    [
        ([LocalTarget("sakiko", "改过名字", True)], "sakiko"),
        ([LocalTarget("custom", CHARACTERS["sakiko"]["display_name"], True)], "custom"),
        ([LocalTarget("sakiko", CHARACTERS["sakiko"]["display_name"], True)], "sakiko"),
        ([LocalTarget("sakiko", "别的名字", True),
          LocalTarget("custom", CHARACTERS["sakiko"]["display_name"], True)], None),
        ([LocalTarget("a", CHARACTERS["sakiko"]["display_name"], True),
          LocalTarget("b", CHARACTERS["sakiko"]["display_name"], True)], None),
        ([LocalTarget("sakiko", "", False)], None),
        ([LocalTarget("custom", "丰川祥子", True)], None),
        ([], None),
    ],
)
def test_default_requires_unique_usable_identity(targets: list[LocalTarget], expected: str | None) -> None:
    """只有唯一可用的目录或精确名称匹配才能自动选择，不做别名推断。"""
    result = default_target("sakiko", targets)
    assert (result.identifier if result else None) == expected


def test_incomplete_directory_blocks_creation_without_becoming_receiver(tmp_path: Path) -> None:
    """遗留空目录会阻止新建，但不能自动成为添加服装的接收角色。"""
    folder = tmp_path / "live2d_related/sakiko"
    folder.mkdir(parents=True)
    targets = read_targets(tmp_path)
    assert creation_conflicts(tmp_path, "sakiko", targets)
    assert default_target("sakiko", targets) is None
    (folder / "name.txt").write_text(CHARACTERS["sakiko"]["display_name"], encoding="utf-8")
    assert not read_targets(tmp_path)[0].usable
    (folder / "character_description.txt").write_text("角色描述", encoding="utf-8")
    assert read_targets(tmp_path)[0].usable


def test_name_conflict_in_nonstandard_folder(tmp_path: Path) -> None:
    """自定义文件夹中的同名角色同样阻止新建，名称会去除首尾空白与 BOM。"""
    folder = tmp_path / "live2d_related/custom"
    folder.mkdir(parents=True)
    (folder / "name.txt").write_text("\ufeff " + CHARACTERS["sakiko"]["display_name"] + "\n", encoding="utf-8")
    assert creation_conflicts(tmp_path, "sakiko", read_targets(tmp_path))


def test_v2_rechecks_same_name_before_network_download(tmp_path: Path) -> None:
    """V2 安装层与界面使用同样的名称冲突规则，冲突时不开始网络下载。"""
    folder = tmp_path / "live2d_related/custom"
    folder.mkdir(parents=True)
    (folder / "name.txt").write_text(CHARACTERS["sakiko"]["display_name"], encoding="utf-8")
    with patch("live2d_download.hosted.legacy.PROJECT_ROOT", tmp_path), \
            patch("live2d_download.hosted.legacy.APP_ROOT", tmp_path), \
            patch("live2d_download.hosted.legacy.BestdoriClient"), \
            patch("live2d_download.hosted.legacy.Live2dDownloader") as downloader:
        with pytest.raises(DownloadError, match="已存在"):
            LegacyService().download("costume", "常服", Selection("new", "sakiko"), Event(), lambda *_: None)
        downloader.assert_not_called()
