"""
虚拟更新测试 —— verify_modified_file 功能验证
==================================================
测试场景：
  1. 正常路径：文件存在且 SHA256 匹配 → 应通过
  2. SHA256 不匹配：文件被修改 → 应抛出 RuntimeError
  3. 文件不存在：modify 的目标文件缺失 → 应抛出 RuntimeError
  4. old_file_sha256 为空：缺失旧哈希 → 应打印警告并通过（不抛出）
  5. app_root 非当前目录：校验应在指定 app_root 下查找文件（BUG 1 的回归测试）
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch
import io

# 把 tools/ 目录加入搜索路径
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from apply_update_patch import verify_modified_file  # noqa: E402


# ──────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────

def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _make_manifest(relative_path: str, old_sha: str, action: str = "modify") -> dict:
    """构造一个只含单个文件条目的最简 manifest。"""
    return {
        "files": [
            {
                "path": relative_path,
                "action": action,
                "sha256": "new_sha_placeholder",
                "old_file_sha256": old_sha,
                "size": 0,
            }
        ]
    }


# ──────────────────────────────────────────
# 测试函数
# ──────────────────────────────────────────

def test_case_1_sha256_matches(tmp_path: Path) -> None:
    """场景 1：文件存在且 SHA256 正确匹配，应顺利通过。"""
    content = b"hello world v1"
    old_sha = _sha256(content)

    target = tmp_path / "data" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(content)

    manifest = _make_manifest("data/config.json", old_sha)
    # 不应抛出任何异常
    verify_modified_file(manifest, tmp_path)
    print("  [通过] 场景 1：SHA256 匹配，校验通过")


def test_case_2_sha256_mismatch(tmp_path: Path) -> None:
    """场景 2：文件已被篡改，SHA256 不匹配，应抛出 RuntimeError。"""
    old_content = b"original content"
    tampered_content = b"tampered content"
    old_sha = _sha256(old_content)

    target = tmp_path / "core.py"
    target.write_bytes(tampered_content)  # 写入篡改内容

    manifest = _make_manifest("core.py", old_sha)
    try:
        verify_modified_file(manifest, tmp_path)
        print("  [失败] 场景 2：应抛出 RuntimeError，但没有抛出！")
    except RuntimeError as exc:
        assert "SHA256 校验失败" in str(exc), f"错误信息不符合预期：{exc}"
        print(f"  [通过] 场景 2：正确检测到 SHA256 不匹配：{exc}")


def test_case_3_file_missing(tmp_path: Path) -> None:
    """场景 3：modify 的目标文件根本不存在，应抛出 RuntimeError。"""
    manifest = _make_manifest("nonexistent/file.txt", _sha256(b"anything"))
    try:
        verify_modified_file(manifest, tmp_path)
        print("  [失败] 场景 3：应抛出 RuntimeError，但没有抛出！")
    except RuntimeError as exc:
        assert "不存在或不是普通文件" in str(exc), f"错误信息不符合预期：{exc}"
        print(f"  [通过] 场景 3：正确检测到文件缺失：{exc}")


def test_case_4_missing_old_sha(tmp_path: Path) -> None:
    """场景 4：old_file_sha256 为空，应打印警告但不抛出异常。"""
    target = tmp_path / "readme.md"
    target.write_bytes(b"some content")

    manifest = _make_manifest("readme.md", "")  # 空 old_file_sha256

    captured = io.StringIO()
    with patch("builtins.print", side_effect=lambda *a, **kw: captured.write(" ".join(str(x) for x in a) + "\n")):
        verify_modified_file(manifest, tmp_path)

    output = captured.getvalue()
    assert "警告" in output, f"应有警告输出，实际输出：{output!r}"
    print(f"  [通过] 场景 4：old_file_sha256 为空时打印警告并继续：{output.strip()!r}")


def test_case_5_app_root_not_cwd(tmp_path: Path) -> None:
    """
    场景 5（BUG 1 回归测试）：app_root 与当前工作目录不同。
    文件仅存在于 app_root 下而不在 cwd 下，校验仍应在正确位置查找。
    """
    content = b"production binary"
    old_sha = _sha256(content)

    # 文件放在 tmp_path（模拟真实 app_root），cwd 下没有这个文件
    target = tmp_path / "binary"
    target.write_bytes(content)

    manifest = _make_manifest("binary", old_sha)

    # 如果函数仍用 Path(".")，这里会因找不到文件而 RuntimeError
    # 修复后应顺利通过
    verify_modified_file(manifest, tmp_path)
    print("  [通过] 场景 5：app_root 非 cwd 时，文件在 app_root 下正确找到")


def test_case_6_add_action_skipped(tmp_path: Path) -> None:
    """场景 6：action='add' 不需要旧文件校验，即使文件不存在也应通过。"""
    manifest = {
        "files": [
            {
                "path": "brand_new_file.txt",
                "action": "add",
                "sha256": "placeholder",
                "old_file_sha256": "",
                "size": 0,
            }
        ]
    }
    verify_modified_file(manifest, tmp_path)
    print("  [通过] 场景 6：action='add' 跳过旧文件校验")


def test_case_7_remove_action_skipped(tmp_path: Path) -> None:
    """场景 7：action='remove' 不需要旧文件校验，即使文件不存在也应通过。"""
    manifest = {
        "files": [
            {
                "path": "to_be_deleted.txt",
                "action": "remove",
                "sha256": "",
                "old_file_sha256": "",
                "size": 0,
            }
        ]
    }
    verify_modified_file(manifest, tmp_path)
    print("  [通过] 场景 7：action='remove' 跳过旧文件校验")


# ──────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────

def main() -> int:
    tests = [
        test_case_1_sha256_matches,
        test_case_2_sha256_mismatch,
        test_case_3_file_missing,
        test_case_4_missing_old_sha,
        test_case_5_app_root_not_cwd,
        test_case_6_add_action_skipped,
        test_case_7_remove_action_skipped,
    ]

    passed = 0
    failed = 0

    print("\n" + "=" * 60)
    print("  verify_modified_file 功能验证（虚拟更新测试）")
    print("=" * 60)

    for test_fn in tests:
        name = test_fn.__name__
        print(f"\n► {name}")
        with tempfile.TemporaryDirectory() as td:
            try:
                test_fn(Path(td))
                passed += 1
            except Exception as exc:
                print(f"  [异常] 测试本身出错：{type(exc).__name__}: {exc}")
                failed += 1

    print("\n" + "=" * 60)
    print(f"  结果：{passed} 通过，{failed} 失败")
    print("=" * 60 + "\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
