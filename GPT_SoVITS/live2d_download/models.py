from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import pathlib
from threading import Event
from typing import Any, Optional, Protocol

@dataclass
class CancelToken:
    """
    包装 threading.Event，用于取消一个正在执行的操作。
    
    > 怎么和 js 里面 Promise 的 CancelToken 有点像呢？
    """
    _event: Event

    @classmethod
    def new(cls) -> "CancelToken":
        return cls(Event())

    def cancel(self) -> None:
        """
        标记当前操作为已取消状态
        """
        self._event.set()

    def cancelled(self) -> bool:
        """
        检查当前操作是否已被取消
        """
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """
        如果当前操作已被取消，则抛出 CancelledError
        """
        if self.cancelled():
            raise CancelledError("Operation cancelled")


class CancelledError(RuntimeError):
    pass


class HttpStatusError(RuntimeError):
    """
    表示 HTTP 请求返回了非 2xx 状态码的错误。
    """
    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"HTTP {status_code}: {url}")
        self.status_code = status_code
        self.url = url
    

class ModelParseError(RuntimeError):
    """
    表示模型数据解析失败的错误。
    """
    pass


def validate_rel_path(rel_path: str) -> pathlib.Path:
    """校验并解析服务器给出的相对路径。确保其不存在“..“等上方路径访问。

    安全策略（严格，遇到异常直接报错终止）：
    - 必须是相对路径（不能以 / 开头）
    - 禁止出现 ..
    - 禁止出现反斜杠（Windows 风格路径）
    - 禁止包含空字节
    """
    if "\x00" in rel_path:
        raise ValueError(f"非法路径（包含空字节）: {rel_path!r}")
    if "\\" in rel_path:
        raise ValueError(f"非法路径（包含反斜杠）: {rel_path!r}")

    p = pathlib.Path(rel_path)
    if p.is_absolute():
        raise ValueError(f"非法路径（绝对路径）: {rel_path!r}")
    if any(part == ".." for part in p.parts):
        raise ValueError(f"非法路径（包含 ..）: {rel_path!r}")
    if not p.parts:
        raise ValueError(f"非法路径（空路径）: {rel_path!r}")
    return p


@dataclass(frozen=True)
class FileProgress:
    live2d_name: str
    rel_path: str                 # 例如 data/textures/texture_00.png
    bytes_done: int
    bytes_total: Optional[int]    # 可能拿不到 Content-Length
    event: str = "download"       # download/cache_hit/downloaded/linked/copied/skipped/missing_optional


@dataclass(frozen=True)
class ModelProgress:
    live2d_name: str
    files_done: int
    files_total: int


class ProgressCallback(Protocol):
    def __call__(self, *, file: Optional[FileProgress] = None, model: Optional[ModelProgress] = None) -> None:
        """
        当下载器完成某个文件或更新模型整体进度时，这个方法就会被调用。你可以继承此类，在方法中编写自己的逻辑，以便处理下载进度信息。
        请注意：
        file 和 model 参数不会同时为 None。但是，二者中可能有一个为 None，也有可能都有值。
        请永远不要假设两个回调参数（file/model）都会被传入。

        如果一个文件未发生网络下载（例如缓存命中/跳过/optional 缺失），程序仍会发送一次 file 回调，
        你可以通过 FileProgress.event 判断具体事件。
        """
        ...


@dataclass(frozen=True)
class AssetKey:
    bundle_name: str
    file_name: str
    server: Optional[Server] = None

    def to_rel_cache_path(self) -> pathlib.PurePath:
        """将 AssetKey 转换为相对缓存路径字符串，用于在缓存目录中保存文件。

        例如，AssetKey("036_general", "angry01.mtn") 会被转换为
        "036_general/angry01.mtn"
        """
        return pathlib.PurePath(self.bundle_name) / self.file_name


@dataclass(frozen=True)
class Live2dFileSpec:
    source: AssetKey
    rel_path: str          # 相对到模型根目录的保存路径
    optional: bool = False # physics.json 之类允许 404
    kind: str = "unknown"  # model/physics/texture/motion/expression


class Server(Enum):
    """
    Bangdream 游戏具有多个服务器；这个枚举可以用来指定从哪个服务器获得数据。
    """
    # 日服
    JAPANESE = "jp"
    # 大概是美服吧（？
    ENGLISH = "en"
    # 台湾服务器
    TAIWAN = "tw"
    # 国服
    CHINA = "cn"
    # 韩服
    KOREAN = "kr"


class Language(Enum):
    """
    bestdori API 返回的大部分名字都具有多语言版本，这个枚举表示请求网站时，返回的角色名等字符串需要使用哪种语言。
    """
    # 你知道吗？这些 ID 是有意义的。Bestdori 网站返回的数据中，所有“名称”相关字段都是一个列表；而这些 ID 正好是对应语言文本的下标。
    # 日语原文
    JAPANESE = 0
    # 日语假名的罗马音拼写
    KANA = 1
    # 繁体中文
    TRADITIONAL_CHINESE = 2
    # 简体中文
    SIMPLIFIED_CHINESE = 3
    # 韩文
    KOREAN = 4


@dataclass
class Live2dCostume:
    live2d_name: str                 # 例如 037_casual-2023
    files: list[Live2dFileSpec]      # 需要下载的所有文件清单

    @classmethod
    def from_build_data(cls, live2d_name: str, build_data: dict[str, Any]) -> "Live2dCostume":
        """从 buildData.asset JSON（dict）解析生成文件清单。

        要求：build_data 至少包含 Base 字段，且 Base 内含 model/physics/textures/motions/expressions。
        - model/motions 去掉 .bytes 后缀
        - textures 若无扩展名则补 .png
        - physics.json 标记为 optional=True（允许 404）

        :param live2d_name: 服装名称，例如 037_casual-2023
        :param build_data: 从 buildData.asset 获得的 JSON 数据
        :raises ModelParseError: 当 build_data 格式不符合预期时抛出
        """
        try:
            base = build_data["Base"]
        except KeyError as e:
            raise ModelParseError("buildData.asset 缺少 Base 字段") from e
        files = []
        # 解析 moc 文件
        try:
            file_name = base["model"]["fileName"].removesuffix(".bytes")
            moc = Live2dFileSpec(
                source=AssetKey(base["model"]["bundleName"], file_name),
                rel_path=file_name,
                kind="model"
            )
            files.append(moc)
        except (KeyError, AttributeError) as e:
            raise ModelParseError("buildData.asset 缺少 moc 模型相关字段") from e

        # 解析 physics 文件
        try:
            files.append(Live2dFileSpec(
                source=AssetKey(base["physics"]["bundleName"], base["physics"]["fileName"]),
                rel_path=base["physics"]["fileName"],
                optional=True,
                kind="physics"
            ))
        except KeyError:
            pass  # physics.json 可选

        # 解析 texture 文件
        try:
            for texture_file in base["textures"]:
                one_bundle_name = texture_file["bundleName"]
                one_file_name = texture_file["fileName"]
                if not one_file_name.lower().endswith((".png", ".jpg", ".jpeg")):
                    one_file_name += ".png"

                files.append(Live2dFileSpec(
                    source=AssetKey(one_bundle_name, one_file_name),
                    rel_path=f"textures/{one_file_name}",
                    kind="texture"
                ))
        except KeyError as e:
            raise ModelParseError("buildData.asset 缺少 texture 相关字段") from e

        # 解析 motion 文件
        try:
            for one in base["motions"]:
                bundle_name = one["bundleName"]
                file_name = one["fileName"].removesuffix(".bytes")
                files.append(Live2dFileSpec(
                    source=AssetKey(bundle_name, file_name),
                    rel_path=f"motions/{file_name}",
                    kind="motion"
                ))
        except KeyError as e:
            raise ModelParseError("buildData.asset 缺少 motion 相关字段") from e

        # 解析 expression 文件
        try:
            for one in base["expressions"]:
                bundle_name = one["bundleName"]
                file_name = one["fileName"].removesuffix(".bytes")
                files.append(Live2dFileSpec(
                    source=AssetKey(bundle_name, file_name),
                    rel_path=f"expressions/{file_name}",
                    kind="expression"
                ))
        except KeyError:
            raise ModelParseError("buildData.asset 缺少 expression 相关字段")

        return cls(live2d_name=live2d_name, files=files)

    def find_file_by_kind(self, kind: str) -> list[Live2dFileSpec]:
        """根据文件类型（kind）查找对应的文件清单列表。"""
        return [f for f in self.files if f.kind == kind]
    
    def categorize_motion_files(self) -> dict[str, list[Live2dFileSpec]]:
        """将 motion 文件按动作类别进行分类。

        返回一个字典，键为动作类别（如 idle、walk 等），值为对应的文件清单列表。
        """
        motion_dict: dict[str, list[Live2dFileSpec]] = {}
        for f in self.find_file_by_kind("motion"):
            # 假设 rel_path 格式为 motions/{category}0x.mtn
            basename = os.path.basename(f.rel_path)
            stem, _ = os.path.splitext(basename)
            # 提取字母部分，忽略数字部分（例如 idle01 -> idle）
            category = ''.join(filter(str.isalpha, stem))
            if category not in motion_dict:
                motion_dict[category] = []
            motion_dict[category].append(f)
        return motion_dict

    def render_model_json(self) -> dict[str, Any]:
        """根据已下载文件的相对路径，生成 model.json 的 dict。

        这个方法应当是“纯组装”：不读写磁盘。
        :raises ModelParseError: 当缺少必要的模型文件时抛出
        """
        model = self.find_file_by_kind("model")
        if not model:
            raise ModelParseError("无法生成 model.json：缺少 moc 模型文件")
        physics = self.find_file_by_kind("physics")
        motions = self.categorize_motion_files()
        
        result = {
            "version": "3.1",
            # 仿照 Go 下载器的逻辑，写一点静态的布局数据
             "layout": {
                "center_x": 0,
                "center_y": 0,
                "width": 2
            },
            "hit_areas_custom": {
                "body_x": [
                -0.3,
                0.2
                ],
                "body_y": [
                0.3,
                -1.9
                ],
                "head_x": [
                -0.25,
                1
                ],
                "head_y": [
                0.25,
                0.2
                ]
            },
            "model": model[0].rel_path,
            "textures": [f.rel_path for f in self.find_file_by_kind("texture")],
            "motions": {
                category: [{
                    # 名称去除扩展名和数字部分
                    "name": os.path.basename(f.rel_path).removesuffix(".json").removesuffix(".mtn"),
                    "file": f.rel_path
                } for f in files] for category, files in motions.items()
            },
            "expressions": [{
                "name": os.path.basename(f.rel_path).removesuffix(".json").removesuffix(".exp.json"),
                "file": f.rel_path
            } for f in self.find_file_by_kind("expression")],
        }

        # physics 允许不存在；因此，只在有时添加
        if physics:
            result["physics"] = physics[0].rel_path

        return result
