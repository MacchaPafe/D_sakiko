"""此文件实现了缓存信息的统一读写接口。

当前用途：
- bestdori API 的 JSON/文本缓存
- Live2D 全局资产缓存所需的原子写入能力（由更高层 AssetCache 组合使用）
"""

import json
import os
import pathlib
import tempfile
import time
from typing import BinaryIO, Callable, overload

import platformdirs

from .models import validate_rel_path


APP_NAME = "D_sakiko"


class CacheManager:
    def __init__(self, cache_dir: pathlib.Path = platformdirs.user_cache_path(APP_NAME, ensure_exists=True)):
        self.cache_dir = cache_dir
    
    def read_cache(self, filename: str) -> str | bytes | None:
        """
        读取缓存目录下名为 filename 的文件内容。

        如果文件不存在，返回 None。
        如果文件是文本文件，返回 str 类型内容。
        如果文件是二进制文件，返回 bytes 类型内容。
        """
        file_path = self.cache_dir / filename
        if not file_path.exists():
            return None
        
        # 尝试以文本方式读取
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            # 如果文本读取失败，尝试以二进制方式读取
            with open(file_path, "rb") as f:
                return f.read()

    @overload
    def write_cache(self, filename: str, data: str) -> None:
        """
        将文本数据写入缓存目录下名为 filename 的文件中。
        """
    
    @overload
    def write_cache(self, filename: str, data: bytes) -> None:
        """
        将二进制数据写入缓存目录下名为 filename 的文件中。
        """
    
    def write_cache(self, filename: str, data: str | bytes) -> None:
        file_path = self.cache_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if isinstance(data, str) else "wb"
        if isinstance(data, str):
            with open(file_path, mode, encoding="utf-8") as f:
                f.write(data)
        else:
            with open(file_path, mode) as f:
                f.write(data)
    
    def read_json(self, filename: str) -> dict | None:
        """
        读取缓存目录下名为 filename 的 JSON 文件内容，并反序列化为字典。

        如果文件不存在或者文件无法被 JSON 正确解析为字典，返回 None。
        """
        file_path = self.cache_dir / filename
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    
    def write_json(self, filename: str, data: dict) -> None:
        """
        将一个字典中的数据通过 JSON 序列化写入缓存目录下名为 filename 的文件中。
        """
        file_path = self.cache_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    
    def read_expire_json(self, filename: str, expire_seconds: int) -> dict | None:
        """
        读取缓存目录下名为 filename 的 JSON 文件内容，并反序列化为字典。

        如果文件不存在、文件无法被 JSON 正确解析为字典，或者文件已过期，返回 None。
        如果文件中不包含过期时间信息，则认为文件已过期，返回 None。
        """
        file_path = self.cache_dir / filename
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                created_at = data.get("created_at", 0)
                if time.time() - created_at > expire_seconds:
                    return None
                return data.get("data", None)
        except (json.JSONDecodeError, OSError):
            return None
    
    def write_expire_json(self, filename: str, data: dict) -> None:
        """
        将一个字典中的数据通过 JSON 序列化写入缓存目录下名为 filename 的文件中，且包含创建时间信息，以便检查是否过期。
        通过此方法写入的 JSON 数据必须使用 read_expire_json 方法读取；通过 read_json 方法读取时，获得的数据将与写入的内容不同。
        """
        file_path = self.cache_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump({
                "data": data,
                "created_at": time.time()
            }, f, indent=4)
    
    def resolve_path(self, rel: str | pathlib.PurePath) -> pathlib.Path:
        """
        将相对缓存路径转换为缓存目录下的（较）绝对路径，且不允许任何越界情况出现
        如果越界出现，则报错 ValueError
        """
        rel_path = validate_rel_path(str(rel))
        return self.cache_dir / rel_path
    
    def ensure_parent_dir(self, rel: str | pathlib.PurePath) -> None:
        """
        确保相对缓存路径所在的父目录存在
        """
        rel_path = validate_rel_path(str(rel))
        full_path = self.cache_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
    
    def atomic_write_bytes(self, dest: pathlib.Path, write_fn: Callable[[BinaryIO], None]) -> None:
        """以原子方式写入二进制文件。

        写入流程：同目录临时文件 -> 写入/flush/fsync -> os.replace 原子替换。
        """
        dest.parent.mkdir(parents=True, exist_ok=True)

        tmp_fd: int | None = None
        tmp_path: pathlib.Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".tmp.", dir=str(dest.parent))
            tmp_fd = fd
            tmp_path = pathlib.Path(tmp_name)

            with os.fdopen(fd, "wb") as f:
                write_fn(f)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, dest)
        except Exception:
            try:
                if tmp_fd is not None:
                    os.close(tmp_fd)
            except Exception:
                pass
            try:
                if tmp_path is not None and tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
            raise


cache_manager = CacheManager()