# Live2D 下载器 API 文档

## 概述

`live2d_download` 是一个用于从 Bestdori 网站下载 BanG Dream! Live2D 角色模型的 Python 模块。该模块提供了完整的下载、缓存和资源管理功能，支持并发下载、缓存复用等特性。

## 快速开始

### 基本使用示例

```python
from pathlib import Path
from live2d_download.models import FileProgress, ModelProgress, ProgressCallback
from live2d_download.bestdori_client import BestdoriClient
from live2d_download.live2d_downloader import Live2dDownloader

# 创建客户端
client = BestdoriClient()
# 下载器自身需要一个客户端作为参数传入；客户端用于实际发起网络请求。
downloader = Live2dDownloader(client)

# 自定义进度回调
class ProgressPrinter(ProgressCallback):
  	# 继承 ProgressCallback 类并且重写 __call__ 函数，在下载时传入此类的对象，就可以在函数中得到进度回调信息。
    # 如果不需要进度信息，可以忽略这部分代码
    def __call__(self, *, file: FileProgress | None = None, model: ModelProgress | None = None) -> None:
        if file is not None:
            print(f"File[{file.event}] {file.live2d_name} - {file.rel_path}: {file.bytes_done}/{file.bytes_total}")
        if model is not None:
            print(f"Model {model.live2d_name}: {model.files_done}/{model.files_total} files done")

# 下载指定服装
downloader.download_live2d_name(
  	# live2d 服装的标识名称
    live2d_name="036_live_event_307_ssr",
  	# 下载到哪个文件夹
  	# 下载后，实际的文件存放位置为 models/036_live_event_307_ssr/
    root_dir=Path("./models"),
  	# 可以选择使用或不使用进度回调
    progress=ProgressPrinter()
)
```

### 高级使用：搜索角色服装

```python
from pathlib import Path
from live2d_download.live2d_downloader import Live2dDownloader
from live2d_download.live2d_service import Live2dService
from live2d_download.bestdori_client import BestdoriClient
from live2d_download.models import Language, Server


client = BestdoriClient(server = Server.JAPANESE)
# 使用简体中文
service = Live2dService(client, language=Language.SIMPLIFIED_CHINESE)
# 下载器
downloader = Live2dDownloader(client)

# 列出所有角色
characters = service.list_characters()
print(characters)  # [{"id": "1", "name": "戸山 香澄"}, ...]

# 搜索角色
# 请注意：service 使用的语言是简体中文，因此搜索时只能搜索到简体中文名称匹配的角色。
# 关于在所有语言中搜索角色名称的用法，请参考该函数代码中的文档
character = service.search_character("Tomori")
print(character)  # None
character = service.search_character("灯")
print(character)  # {'id': '36', 'name': '高松 灯'}
if character:
    print(f"找到角色: {character['name']} (ID: {character['id']})")
    
    # 获取该角色的所有服装
    costumes = service.search_costumes(int(character["id"]), include_live_event=True)
    print(f"服装列表: {costumes}")
    
    # 下载第一套服装
    if costumes:
        print("正在下载服装:", costumes[0])
        downloader.download_live2d_name(
            live2d_name=costumes[0],
            root_dir=Path("./models")
        )
        print("下载完成")
```

### 取消下载

请注意：`CancelToken` 只能取消在子线程中进行下载的任务；对于跨进程运行，`CancelToken` 无法生效。

```python
from pathlib import Path
import time
from live2d_download.bestdori_client import BestdoriClient
from live2d_download.live2d_downloader import Live2dDownloader
from live2d_download.models import CancelToken, CancelledError
import threading

# 创建取消令牌
cancel = CancelToken.new()
client = BestdoriClient()
downloader = Live2dDownloader(client)

# 在后台线程执行下载
def download_task():
    try:
        downloader.download_live2d_name(
            live2d_name="036_dream_festival_3_ur",
            root_dir=Path("./models"),
            cancel=cancel
        )
    except CancelledError:
        print("下载已取消")

thread = threading.Thread(target=download_task)
thread.start()

# 在需要时取消下载
time.sleep(0.5)
cancel.cancel()
time.sleep(1)  # 等待下载线程结束
```

## 下载流程

### 1. 整体流程

下载器采用三层架构设计，将网络请求、业务逻辑和下载执行分离：

```
用户 → Live2dDownloader → Live2dService → BestdoriClient → Bestdori API
                ↓                ↓              ↓
           并发下载执行   实现单个搜素功能 网络请求 + 缓存
                ↓
           AssetCache
                ↓
        全局资产缓存目录
                ↓
          硬链接/复制
                ↓
            模型目录
```

### 2. 详细步骤

#### 步骤 1: 解析服装信息
- `Live2dService.build_costume()` 调用 `BestdoriClient.get_build_data()`
- 从 Bestdori API 获取 `buildData.asset` JSON 数据
- 解析出所有需要下载的文件清单（model、textures、motions、expressions、physics）
- 返回 `Live2dCostume` 对象，包含完整的文件规格列表

#### 步骤 2: 并发下载文件
- `Live2dDownloader.download_costume()` 使用线程池（默认 10 个并发）
- 每个文件一个下载任务，由 `_download_one()` 处理
- 支持取消令牌和进度回调

#### 步骤 3: 资产缓存机制
- 每个文件先检查全局缓存（`AssetCache`）
- 缓存路径格式：`{cache_dir}/live2d/assets/{server}/{bundle_name}/{file_name}`
- 如果缓存命中，直接跳到步骤 4
- 如果缓存未命中：
  - 从 Bestdori 下载文件流
  - 原子写入缓存目录（先写 `.tmp.xxx`，成功后 `os.replace()`）
  - 发送 `download` / `downloaded` 进度事件

#### 步骤 4: 文件存放到模型目录
- 使用硬链接（`os.link()`）将缓存文件链接到模型目录
- 硬链接失败时降级为复制（`shutil.copy2()`）
- 链接模式可配置：`AUTO`（自动降级）、`HARDLINK`（仅硬链接）、`COPY`（仅复制）
- 发送 `linked` / `copied` / `skipped` 进度事件

#### 步骤 5: 生成 model.json
- 所有文件下载完成后，调用 `Live2dCostume.render_model_json()`
- 生成符合 Live2D Cubism 规范的 `model.json`
- 包含模型文件、纹理、动作、表情等相对路径
- 原子写入到模型目录

### 3. 进度事件

下载过程中会触发多种进度事件（通过 `FileProgress.event` 字段）：

| 事件 | 含义 |
|------|------|
| `download` | 正在从网络下载（实时进度） |
| `downloaded` | 网络下载完成 |
| `cache_hit` | 缓存命中，跳过下载 |
| `linked` | 硬链接到模型目录 |
| `copied` | 复制到模型目录 |
| `skipped` | 文件已存在，跳过 |
| `missing_optional` | 可选文件（如 `physics.json`）不存在，跳过 |

## 缓存与链接机制

### 全局资产缓存

#### 缓存目录结构

```
{用户缓存目录}/D_sakiko/
├── live2d/
│   └── assets/
│       └── jp/                    # 服务器标识
│           ├── 036_general/       # bundle_name
│           │   ├── idle01.mtn
│           │   └── angry01.mtn
│           └── 036_live_event_307_ssr/
│               ├── model3.json
│               └── texture_00.png
├── chara_roster.json              # API 缓存（24h）
├── assets_info.json               # API 缓存（24h）
└── ...
```

用户缓存目录通过 `platformdirs.user_cache_path("D_sakiko")` 自动确定：
- macOS: `~/Library/Caches/D_sakiko`
- Windows: `%LOCALAPPDATA%\D_sakiko\Cache`
- Linux: `~/.cache/D_sakiko`

#### 缓存优势

1. **节省磁盘空间**：多个模型共享相同的贴图、动作文件时，只下载一次
2. **加速下载**：重新下载模型时，如果文件已缓存则秒级完成
3. **硬链接优化**：同一磁盘分区内，硬链接不占用额外空间，且修改互不影响

#### 缓存安全性

- **原子写入**：使用临时文件 + `os.replace()` 保证不会产生损坏的缓存
- **多线程安全**：每个 `AssetKey` 独立锁，避免并发下载同一文件
- **路径校验**：严格检查相对路径，禁止 `..`、绝对路径、符号链接等攻击

### API 响应缓存

`BestdoriClient` 会缓存以下 API 响应（默认 24 小时）：
- 角色列表 (`/api/characters/all.2.json`)
- 角色详情 (`/api/characters/{id}.json`)
- 资产索引 (`/api/explorer/jp/assets/_info.json`)
- 服装索引 (`/api/costumes/all.5.json`)

缓存文件格式：
```json
{
  "data": { /* API 响应内容 */ },
  "created_at": 1737292800.0
}
```

### 硬链接 vs 复制

| 策略 | 优点 | 缺点 |
|------|------|------|
| 硬链接 | 不占用额外空间；创建速度快 | 需要同一文件系统；修改会影响缓存 |
| 复制 | 适用所有场景；文件完全独立 | 占用双倍空间；速度较慢 |

下载器默认使用 `LinkMode.AUTO`：优先硬链接，失败时自动降级为复制。

## 项目结构

```
live2d_download/
├── __init__.py                   # 模块入口
├── models.py                     # 数据模型和类型定义
├── cache.py                      # 通用缓存管理
├── asset_cache.py                # 资产缓存逻辑
├── bestdori_client.py            # Bestdori API 客户端
├── live2d_service.py             # 业务逻辑层
└── live2d_downloader.py          # 下载执行层
```

## 主要类说明

### 1. `models.py` - 数据模型

#### `CancelToken`

线程安全的取消令牌，用于中断长时间运行的操作。

```python
class CancelToken:
    @classmethod
    def new(cls) -> "CancelToken":
        """创建新的取消令牌"""
        
    def cancel(self) -> None:
        """标记当前操作为已取消状态"""
        
    def cancelled(self) -> bool:
        """检查当前操作是否已被取消"""
        
    def raise_if_cancelled(self) -> None:
        """如果已取消则抛出 CancelledError"""
```

#### `FileProgress` / `ModelProgress`

进度信息数据类，用于回调。`ProgressCallback`.`__call__` 方法中，下载器会传入以下两个对象表示进度信息。

```python
@dataclass(frozen=True)
class FileProgress:
    live2d_name: str              # 服装名称
    rel_path: str                 # 文件相对路径
    bytes_done: int               # 已下载字节数
    bytes_total: Optional[int]    # 总字节数（可能未知，此时为 0）
    event: str                    # 进度事件类型

@dataclass(frozen=True)
class ModelProgress:
    live2d_name: str              # 服装名称
    files_done: int               # 已完成文件数
    files_total: int              # 总文件数
```

#### `ProgressCallback`

进度回调协议，用户可实现该协议以自定义进度处理。

```python
class ProgressCallback(Protocol):
    def __call__(self, *, file: Optional[FileProgress] = None, model: Optional[ModelProgress] = None) -> None:
        """进度回调方法
        
        注意：file 和 model 可能同时传入，也可能只传入一个。
        """
```

#### `Live2dCostume`

Live2D 服装模型的完整描述。

```python
@dataclass
class Live2dCostume:
    live2d_name: str                 # 服装名称，如 "037_casual-2023"
    files: list[Live2dFileSpec]      # 需要下载的所有文件清单

    @classmethod
    def from_build_data(cls, live2d_name: str, build_data: dict[str, Any]) -> "Live2dCostume":
        """从 buildData.asset JSON 解析生成文件清单"""
        
    def render_model_json(self) -> dict[str, Any]:
        """生成 model.json 的字典内容"""
        
    def find_file_by_kind(self, kind: str) -> list[Live2dFileSpec]:
        """根据文件类型查找文件清单"""
        
    def categorize_motion_files(self) -> dict[str, list[Live2dFileSpec]]:
        """将 motion 文件按动作类别分类"""
```

#### `Live2dFileSpec`

单个文件的下载规格。

```python
@dataclass(frozen=True)
class Live2dFileSpec:
    source: AssetKey          # 资源键（bundle_name, file_name, server）
    rel_path: str             # 相对模型根目录的保存路径
    optional: bool = False    # 是否允许 404
    kind: str = "unknown"     # 文件类型：model/physics/texture/motion/expression
```

#### `Server` / `Language`

枚举类型，用于指定服务器和语言。

```python
class Server(Enum):
    JAPANESE = "jp"           # 日服
    ENGLISH = "en"            # 美服
    TAIWAN = "tw"             # 台服
    CHINA = "cn"              # 国服
    KOREAN = "kr"             # 韩服

class Language(Enum):
    JAPANESE = 0              # 日语原文
    KANA = 1                  # 日语假名的罗马音拼写
    TRADITIONAL_CHINESE = 2   # 繁体中文
    SIMPLIFIED_CHINESE = 3    # 简体中文
    KOREAN = 4                # 韩文
```

### 2. `cache.py` - 通用缓存管理

#### `CacheManager`

提供统一的缓存读写接口，支持文本、二进制和 JSON 数据。

```python
class CacheManager:
    def __init__(self, cache_dir: pathlib.Path = ...):
        """初始化缓存管理器
        
        默认使用 platformdirs.user_cache_path("D_sakiko")
        """
    
    def read_cache(self, filename: str) -> str | bytes | None:
        """读取缓存文件，自动判断文本或二进制"""
        
    def write_cache(self, filename: str, data: str | bytes) -> None:
        """写入缓存文件"""
        
    def read_json(self, filename: str) -> dict | None:
        """读取 JSON 缓存"""
        
    def write_json(self, filename: str, data: dict) -> None:
        """写入 JSON 缓存"""
        
    def read_expire_json(self, filename: str, expire_seconds: int) -> dict | None:
        """读取带过期时间的 JSON 缓存"""
        
    def write_expire_json(self, filename: str, data: dict) -> None:
        """写入带过期时间的 JSON 缓存"""
        
    def resolve_path(self, rel: str | pathlib.PurePath) -> pathlib.Path:
        """将相对路径转换为缓存目录下的绝对路径（安全校验）"""
        
    def atomic_write_bytes(self, dest: pathlib.Path, write_fn: Callable[[BinaryIO], None]) -> None:
        """原子写入二进制文件（临时文件 + os.replace）"""
```

### 3. `asset_cache.py` - 资产缓存

#### `AssetCache`

全局资产缓存层，确保相同文件只下载一次。

```python
class AssetCache:
    def __init__(self, cache: CacheManager = ..., *, namespace: str = "live2d"):
        """初始化资产缓存"""
    
    @classmethod
    def global_default(cls) -> "AssetCache":
        """获取全局默认实例"""
    
    def get_cache_path(self, key: AssetKey) -> Path:
        """获取资产的缓存路径"""
        
    def get_or_download(
        self,
        *,
        key: AssetKey,
        open_stream: Callable[[], Optional[DownloadStream]],
        allow_not_found: bool,
        cancel: Optional[CancelToken] = None,
        on_bytes: Optional[Callable[[int, Optional[int]], None]] = None,
    ) -> CacheResult:
        """获取缓存文件；若未命中则下载写入缓存（线程安全）"""
        
    def materialize_to(
        self,
        *,
        cache_path: Path,
        dest_path: Path,
        mode: LinkMode = LinkMode.AUTO,
        overwrite: bool = False,
    ) -> str:
        """将缓存文件链接/复制到目标路径
        
        返回：linked / copied / skipped
        """
```

#### `LinkMode`

缓存文件存放策略。

```python
class LinkMode(Enum):
    AUTO = "auto"          # 优先硬链接，失败则复制
    HARDLINK = "hardlink"  # 只允许硬链接，失败报错
    COPY = "copy"          # 永远复制
```

### 4. `bestdori_client.py` - API 客户端

#### `BestdoriClient`

负责与 Bestdori API 交互，提供网络请求和文件下载功能。

```python
class BestdoriClient:
    def __init__(
        self,
        *,
        server: Server = Server.JAPANESE,
        base_assets_url: str = "https://bestdori.com/assets",
        timeout_seconds: float = 30.0,
        session: Optional[requests.Session] = None,
        cache: Optional[CachePolicy] = None,
        user_agent: str = "bestdori-live2d-downloader-python/0.1",
    ):
        """初始化 Bestdori 客户端"""
    
    # --- API 端点 ---
    def get_characters_roster(self) -> dict[str, Any]:
        """获取角色列表（缓存 24h）"""
        
    def get_character(self, chara_id: int) -> dict[str, Any]:
        """获取角色详情（缓存 24h）"""
        
    def get_assets_index(self) -> dict[str, Any]:
        """获取资产索引（缓存 24h）"""
        
    def get_live2d_assets_map(self) -> dict[str, Any]:
        """获取 live2d 服装映射"""
        
    def validate_live2d_name(self, live2d_name: str) -> bool:
        """验证服装名称是否有效"""
        
    def get_build_data(self, live2d_name: str) -> dict[str, Any]:
        """获取服装的 buildData.asset"""
        
    def get_costume_index(self) -> dict[str, Any]:
        """获取服装索引（缓存 24h）"""
        
    def get_costume_icon(self, costume_id: int, live2d_name: str) -> bytes:
        """获取服装图标二进制数据"""
    
    # --- 下载 ---
    def iter_download_asset(
        self,
        bundle_name: str,
        file_name: str,
        *,
        cancel: Optional[CancelToken] = None,
        allow_not_found: bool = False,
        chunk_size: int = 1024 * 256,
        retries: int = 3,
        backoff_base: float = 0.5,
    ) -> Optional[DownloadStream]:
        """下载资源文件并返回流迭代器（默认重试 3 次）"""
```

#### `DownloadStream`

可关闭的下载流，支持分块迭代和 `with` 语句。

```python
@dataclass
class DownloadStream:
    resp: requests.Response
    chunk_size: int
    cancel: Optional[CancelToken] = None
    total_bytes: Optional[int] = None
    downloaded_bytes: int = 0

    def __enter__(self) -> "DownloadStream":
        """进入上下文管理器"""
        
    def __exit__(self, exc_type, exc, tb) -> None:
        """退出时自动关闭连接"""
        
    def close(self) -> None:
        """关闭 HTTP 连接"""
        
    def iter_bytes(self) -> BytesIterator:
        """迭代获取数据块（自动检查取消令牌）"""
```

### 5. `live2d_service.py` - 业务逻辑层

#### `Live2dService`

提供高层业务接口，协调客户端和业务逻辑。

```python
class Live2dService:
    def __init__(self, client: BestdoriClient, language: Language = Language.SIMPLIFIED_CHINESE):
        """初始化服务层"""
    
    def list_characters(self) -> list[dict[str, Any]]:
        """返回角色列表，每个角色包含 id 和 name 字段"""
        
    def search_character(self, name: str, *, match_threshold=75) -> Optional[dict[str, Any]]:
        """模糊搜索角色（使用 rapidfuzz）
        
        返回最匹配的角色字典，找不到时返回 None
        """
        
    def search_costumes(
        self,
        chara_id: int,
        *,
        include_live_event: bool = True,
    ) -> list[str]:
        """获取指定角色的所有服装名称列表（已排序）
        
        排序规则：
        - live_event 结尾的排在最后
        - 其余按名称中数字部分排序
        """
        
    def get_costume_icon(self, live2d_name: str) -> Optional[bytes]:
        """获取指定服装的图标二进制数据"""
        
    def build_costume(self, live2d_name: str) -> Live2dCostume:
        """拉取 buildData.asset 并解析成 Live2dCostume"""
```

### 6. `live2d_downloader.py` - 下载执行层

#### `Live2dDownloader`

负责实际的下载执行，支持并发、缓存、进度回调和取消。

```python
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
    ):
        """初始化下载器
        
        Args:
            client: BestdoriClient 实例
            language: 语言设置
            max_workers: 最大并发下载数
            use_asset_cache: 是否启用全局资产缓存
            asset_cache: 自定义资产缓存实例
            link_mode: 缓存文件落盘策略
        """
    
    def download_costume(
        self,
        costume: Live2dCostume,
        *,
        root_dir: Path,
        overwrite: bool = False,
        cancel: Optional[CancelToken] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> DownloadResult:
        """并发下载一套服装到本地，并生成 model.json
        
        Args:
            costume: Live2dCostume 对象
            root_dir: 下载根目录
            overwrite: 是否覆盖已存在文件
            cancel: 取消令牌
            progress: 进度回调函数
            
        Returns:
            DownloadResult 对象，包含下载结果信息
        """
        
    def download_live2d_name(
        self,
        live2d_name: str,
        *,
        root_dir: Path,
        overwrite: bool = False,
        cancel: Optional[CancelToken] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> DownloadResult:
        """便捷方法：通过服装名称直接下载
        
        内部会自动调用 service.build_costume() 解析文件清单
        """
```

#### `DownloadResult`

下载结果数据类。

```python
@dataclass(frozen=True)
class DownloadResult:
    live2d_name: str              # 服装名称
    model_dir: Path               # 模型目录
    downloaded_files: list[Path]  # 已下载文件列表
    skipped_files: list[Path]     # 跳过的文件列表
```

## 错误处理

### 异常类型

| 异常 | 继承自 | 说明 |
|------|--------|------|
| `CancelledError` | `RuntimeError` | 操作被取消 |
| `HttpStatusError` | `RuntimeError` | HTTP 请求返回非 2xx 状态码 |
| `ModelParseError` | `RuntimeError` | buildData.asset 解析失败 |

### 错误示例

```python
from live2d_download.models import CancelledError, HttpStatusError, ModelParseError

try:
    downloader.download_live2d_name(
        live2d_name="invalid_name",
        root_dir=Path("./models")
    )
except HttpStatusError as e:
    print(f"HTTP 错误: {e.status_code} - {e.url}")
except ModelParseError as e:
    print(f"模型解析失败: {e}")
except CancelledError:
    print("下载已取消")
```

## 配置选项

### BestdoriClient 配置

```python
from live2d_download.bestdori_client import BestdoriClient, CachePolicy
from live2d_download.models import Server

client = BestdoriClient(
    server=Server.JAPANESE,           # 服务器选择
    timeout_seconds=30.0,             # 请求超时时间
    cache=CachePolicy(
        enabled=True,                 # 启用 API 响应缓存
        ttl_seconds=24 * 3600,        # 缓存过期时间（24 小时）
        cache_dir=Path("./my_cache")  # 自定义缓存目录（可选）
    ),
)
```

### Live2dDownloader 配置

```python
from live2d_download.live2d_downloader import Live2dDownloader
from live2d_download.asset_cache import LinkMode

downloader = Live2dDownloader(
    client=client,
    max_workers=20,                   # 并发下载数（默认 10）
    use_asset_cache=True,             # 启用全局资产缓存
    link_mode=LinkMode.AUTO,          # 硬链接策略（AUTO/HARDLINK/COPY）
)
```

## 最佳实践

### 1. 复用客户端实例

```python
# 推荐：复用同一个 client，共享 Session 和缓存
client = BestdoriClient()
service = Live2dService(client)
downloader = Live2dDownloader(client)

# 不推荐：频繁创建新实例
for costume in costumes:
    client = BestdoriClient()  # ❌ 不必要的开销
    downloader = Live2dDownloader(client)
    downloader.download_live2d_name(costume, ...)
```

### 2. 适当的并发数

```python
# 网络条件良好时
downloader = Live2dDownloader(client, max_workers=20)

# 网络不稳定或服务器限速时
downloader = Live2dDownloader(client, max_workers=5)
```

### 3. 利用进度回调实现 UI 更新

```python
from PySide6.QtCore import QObject, Signal

class DownloadWorker(QObject):
    file_progress = Signal(str, int, int)  # (file_name, done, total)
    model_progress = Signal(int, int)      # (done, total)
    
    def __init__(self, downloader):
        super().__init__()
        self.downloader = downloader
    
    def progress_callback(self, *, file=None, model=None):
        if file is not None and file.event == "download":
            self.file_progress.emit(file.rel_path, file.bytes_done, file.bytes_total or 0)
        if model is not None:
            self.model_progress.emit(model.files_done, model.files_total)
    
    def download(self, live2d_name, root_dir):
        self.downloader.download_live2d_name(
            live2d_name=live2d_name,
            root_dir=root_dir,
            progress=self.progress_callback
        )
```

### 4. 优雅的取消处理

```python
class DownloadManager:
    def __init__(self):
        self.cancel_token = None
        self.thread = None
    
    def start_download(self, live2d_name, root_dir):
        self.cancel_token = CancelToken.new()
        
        def task():
            try:
                downloader.download_live2d_name(
                    live2d_name=live2d_name,
                    root_dir=root_dir,
                    cancel=self.cancel_token
                )
            except CancelledError:
                print("下载已取消")
        
        self.thread = threading.Thread(target=task)
        self.thread.start()
    
    def cancel_download(self):
        if self.cancel_token:
            self.cancel_token.cancel()
        if self.thread:
            self.thread.join(timeout=5.0)
```

## 性能优化建议

### 1. 缓存命中率

- 首次下载一批角色后，后续重新下载速度会显著提升
- 相同角色的不同服装通常共享大部分资源（如通用表情、动作）；只需要重复下载 texture 图片，moc 模型文件与 physics 物理文件

### 2. 磁盘 I/O

- 优先使用硬链接（`LinkMode.AUTO` 或 `HARDLINK`）
- 确保缓存目录和模型目录在同一文件系统且在同一个磁盘分区
- SSD 会显著提升性能

### 3. 网络优化

- 根据网络带宽调整 `max_workers`
- 可通过 `requests.Session` 设置代理：

```python
import requests

session = requests.Session()
session.proxies = {
    'http': 'http://127.0.0.1:7890',
    'https': 'http://127.0.0.1:7890',
}

client = BestdoriClient(session=session)
```

## 常见问题

### Q: 下载失败怎么办？

A: 下载器内置了自动重试机制（默认 3 次），会自动处理临时网络错误和服务器限速。如果仍然失败，检查：
- 网络连接是否正常
- 服装名称是否正确（通过 `search_costumes` 获取）
- Bestdori 服务器是否可访问

### Q: 缓存占用太多磁盘空间？

A: 可以定期清理缓存目录：
```python
import shutil
from platformdirs import user_cache_path

cache_dir = user_cache_path("D_sakiko", ensure_exists=True)
shutil.rmtree(cache_dir / "live2d" / "assets")
```

不过，如果缓存目录和 live2d 下载目标目录位于同一磁盘，可以使用硬链接的话，清理缓存目录不会释放任何空间。此外，因为需要重复下载同一角色的通用文件，之后使用时缓存占用反而可能增加。

### Q: 如何禁用缓存？

A: 创建下载器时设置 `use_asset_cache=False`：
```python
downloader = Live2dDownloader(client, use_asset_cache=False)
```

### Q: 硬链接失败怎么办？

A: 通常是跨文件系统导致。可以：
1. 使用 `LinkMode.COPY` 强制复制
2. 将缓存目录和模型目录放在同一磁盘分区

## 版本信息

- **Python 要求**: >= 3.9
- **主要依赖**:
  - `requests`: HTTP 客户端
  - `rapidfuzz`: 模糊字符串匹配
  - `platformdirs`: 跨平台目录获取
- 请通过包管理器安装以上依赖

## 许可证

本模块是 D_sakiko 项目的一部分，遵循项目主许可证。

---

*最后更新: 2026年1月19日*
