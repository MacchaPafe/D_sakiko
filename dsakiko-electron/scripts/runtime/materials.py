from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Dict
import zipfile


def digest(file: Path) -> str:
    """分块计算文件摘要，避免把大型权重或归档读入内存。"""
    value = hashlib.sha256()
    with file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def wheel_lock(root: Path) -> None:
    """依据实际交付的 wheel 生成带哈希的完整离线清单和许可证副本。"""
    entries: Dict[str, str] = {}
    license_root = root / "licenses" / "python-wheels"
    for wheel in sorted((root / "wheelhouse").glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
            metadata = email.message_from_bytes(archive.read(metadata_name))
            name = str(metadata["Name"])
            if name.lower() in entries:
                raise ValueError("wheelhouse 内存在同名包的多个版本：" + name)
            entries[name.lower()] = f"{name}=={metadata['Version']} --hash=sha256:{digest(wheel)}"
            for member in archive.namelist():
                if member.endswith("/"):
                    continue
                leaf = Path(member).name.lower()
                if "license" in leaf or "copying" in leaf or "notice" in leaf:
                    target = license_root / name / member.replace("/", "__")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
    (root / "requirements.lock").write_text("\n".join(entries[key] for key in sorted(entries)) + "\n", encoding="utf-8")


def ffmpeg(wheel: Path, root: Path) -> None:
    """从固定的 imageio-ffmpeg wheel 提取目标平台可执行文件及发行说明。"""
    with zipfile.ZipFile(wheel) as archive:
        binary = next(name for name in archive.namelist() if "/binaries/ffmpeg-" in name and not name.endswith("/"))
        target = root / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(binary) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
        target.chmod(0o755)
        licenses = root / "licenses" / "imageio-ffmpeg"
        licenses.mkdir(parents=True, exist_ok=True)
        for member in archive.namelist():
            if "license" in member.lower() or member.endswith("METADATA") or member.endswith("README.md"):
                if not member.endswith("/"):
                    (licenses / member.replace("/", "__")).write_bytes(archive.read(member))
        (licenses / "source.txt").write_text("imageio-ffmpeg 0.6.0\nhttps://github.com/imageio/imageio-ffmpeg/tree/v0.6.0\nhttps://github.com/imageio/imageio-binaries\nwheel SHA-256: " + digest(wheel) + "\n", encoding="utf-8")


def archive_tree(root: Path, destination: Path) -> None:
    """生成不含符号链接的 ZIP64，清单中的展开大小来自实际归档成员。"""
    expanded = 0
    temporary = destination.with_suffix(".zip.partial")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for file in sorted(root.rglob("*")):
                if file.is_symlink():
                    raise ValueError("归档禁止符号链接：" + str(file))
                if file.is_file():
                    archive.write(file, file.relative_to(root).as_posix())
                    expanded += file.stat().st_size
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    destination.with_suffix(".metadata.json").write_text(json.dumps({"path": destination.name, "sizeBytes": destination.stat().st_size, "expandedBytes": expanded, "sha256": digest(destination), "archive": "zip"}, indent=2) + "\n", encoding="utf-8")


def audit_macos(root: Path) -> None:
    """检查 Mach-O 不引用构建机器上的 Homebrew 或用户目录动态库。"""
    magic = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf"}
    checked = 0
    for file in root.rglob("*"):
        if not file.is_file():
            continue
        with file.open("rb") as stream:
            if stream.read(4) not in magic:
                continue
        result = subprocess.run(["otool", "-L", str(file)], check=True, capture_output=True, text=True)
        identities = subprocess.run(["otool", "-D", str(file)], check=True, capture_output=True, text=True)
        own_ids = {line.strip() for line in identities.stdout.splitlines() if not line.endswith(":")}
        for line in result.stdout.splitlines()[1:]:
            # Universal 二进制会再次输出另一架构的文件标题，它不是动态库依赖。
            if not line.startswith("\t"):
                continue
            dependency = line.strip().split(" (", 1)[0]
            # LC_ID_DYLIB 是库自身的标识，不是需要从该路径加载的外部依赖。
            if dependency in own_ids:
                continue
            if dependency.startswith("/") and not dependency.startswith(("/usr/lib/", "/System/Library/")):
                raise ValueError(f"不可移植的动态库引用：{file}: {dependency}")
        checked += 1
    print(f"Mach-O 动态库路径检查通过：{checked} 个文件", flush=True)


def main() -> None:
    """执行运行包构建所需的标准库文件操作。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["wheel-lock", "ffmpeg", "zip", "audit-macos"])
    parser.add_argument("root", type=Path)
    parser.add_argument("extra", type=Path, nargs="?")
    args = parser.parse_args()
    if args.action == "wheel-lock":
        wheel_lock(args.root)
    elif args.action == "audit-macos":
        audit_macos(args.root)
    elif args.action == "zip" and args.extra is not None:
        archive_tree(args.root, args.extra)
    elif args.action == "ffmpeg" and args.extra is not None:
        ffmpeg(args.extra, args.root)
    else:
        parser.error("该操作需要额外路径")


if __name__ == "__main__":
    main()
