#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

import requests


API_ROOT = "https://gitee.com/api/v5"


def raise_for_gitee_error(response: requests.Response, action: str) -> None:
    """在 Gitee API 失败时抛出包含响应正文的错误。"""

    if response.ok:
        return
    body = response.text[:1000]
    raise RuntimeError(f"{action} 失败：HTTP {response.status_code}，响应：{body}")


def get_release_by_tag(owner: str, repo: str, tag: str, token: str) -> dict[str, object] | None:
    """按 tag 查询 Gitee release。"""

    params = {"access_token": token} if token else None
    response = requests.get(
        f"{API_ROOT}/repos/{owner}/{repo}/releases/tags/{tag}",
        params=params,
        timeout=30,
    )
    if response.status_code == 404:
        return None
    raise_for_gitee_error(response, "查询 Gitee release")
    data = response.json()
    if data is None:
        return None
    if isinstance(data, list) and not data:
        return None
    if not isinstance(data, dict):
        raise RuntimeError(f"Gitee release 查询结果不是对象：{data!r}")
    return dict(data)


def delete_release(owner: str, repo: str, release_id: int, token: str) -> None:
    """删除已有 Gitee release。"""

    response = requests.delete(
        f"{API_ROOT}/repos/{owner}/{repo}/releases/{release_id}",
        params={"access_token": token},
        timeout=30,
    )
    if response.status_code not in {200, 204, 404}:
        raise_for_gitee_error(response, "删除 Gitee release")


def branch_exists(owner: str, repo: str, branch: str, token: str) -> bool:
    """检查 Gitee 仓库中目标分支是否存在。"""

    response = requests.get(
        f"{API_ROOT}/repos/{owner}/{repo}/branches/{branch}",
        params={"access_token": token},
        timeout=30,
    )
    if response.status_code == 404:
        return False
    raise_for_gitee_error(response, f"查询 Gitee 分支 {branch}")
    data = response.json()
    return isinstance(data, dict)


def _run_git(command: list[str], cwd: Path) -> None:
    """运行 git 命令，失败时隐藏 token 并抛出错误。"""

    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        raise RuntimeError(f"git 命令失败：{' '.join(command[:2])}，输出：{stderr or stdout or '未知错误'}")


def create_shell_branch(owner: str, repo: str, branch: str, token: str) -> None:
    """创建只包含 README.md 的空壳提交并推送到 Gitee。"""

    remote_url = f"https://{quote(owner, safe='')}:{quote(token, safe='')}@gitee.com/{owner}/{repo}.git"
    with tempfile.TemporaryDirectory(prefix="d_sakiko_gitee_shell_") as temp_dir:
        work_dir = Path(temp_dir)
        _run_git(["git", "init", "-b", branch], work_dir)
        _run_git(["git", "config", "user.name", "D_sakiko Release Bot"], work_dir)
        _run_git(["git", "config", "user.email", "release-bot@example.invalid"], work_dir)
        readme = work_dir / "README.md"
        readme.write_text(
            "# D_sakiko release mirror\n\n"
            "This repository is used as a lightweight mirror for D_sakiko update release assets.\n",
            encoding="utf-8",
        )
        _run_git(["git", "add", "README.md"], work_dir)
        _run_git(["git", "commit", "-m", "Initialize release mirror"], work_dir)
        _run_git(["git", "push", remote_url, f"HEAD:{branch}"], work_dir)


def ensure_shell_commit(owner: str, repo: str, branch: str, token: str) -> None:
    """确保 Gitee 仓库存在用于挂 release tag 的空壳提交。"""

    if branch_exists(owner, repo, branch, token):
        return
    print(f"[Gitee] 分支 {branch} 不存在，创建空壳 README 提交。")
    create_shell_branch(owner, repo, branch, token)


def create_release(
    owner: str,
    repo: str,
    tag: str,
    name: str,
    body: str,
    target_commitish: str,
    token: str,
    prerelease: bool = False,
) -> int:
    """创建 Gitee release，返回 release_id。"""

    payload = {
        "access_token": token,
        "tag_name": tag,
        "name": name,
        "body": body,
        "target_commitish": target_commitish,
        "prerelease": prerelease,
    }
    response = requests.post(f"{API_ROOT}/repos/{owner}/{repo}/releases", json=payload, timeout=30)
    raise_for_gitee_error(response, "创建 Gitee release")
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("id"), int):
        raise RuntimeError(f"Gitee release 创建失败，响应缺少 id：{data}")
    return int(data["id"])


def upload_release_asset(owner: str, repo: str, release_id: int, file_path: Path, token: str) -> None:
    """上传单个附件到 Gitee release。"""

    with file_path.open("rb") as file:
        response = requests.post(
            f"{API_ROOT}/repos/{owner}/{repo}/releases/{release_id}/attach_files",
            data={"access_token": token},
            files={"file": (file_path.name, file)},
            timeout=1200,
        )
    raise_for_gitee_error(response, f"上传 Gitee release 附件 {file_path.name}")


def sync_release_assets(
    owner: str,
    repo: str,
    tag: str,
    release_name: str,
    release_body: str,
    target_commitish: str,
    asset_paths: list[Path],
    token: str,
) -> None:
    """删除旧 release，创建新 release，并批量上传附件。"""

    existing_release = get_release_by_tag(owner, repo, tag, token)
    if existing_release is not None and isinstance(existing_release.get("id"), int):
        delete_release(owner, repo, int(existing_release["id"]), token)
    ensure_shell_commit(owner, repo, target_commitish, token)
    release_id = create_release(owner, repo, tag, release_name, release_body, target_commitish, token)
    for asset_path in asset_paths:
        upload_release_asset(owner, repo, release_id, asset_path, token)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="同步 GitHub release assets 到 Gitee release。")
    parser.add_argument("--owner", required=True, help="Gitee owner。")
    parser.add_argument("--repo", required=True, help="Gitee repo。")
    parser.add_argument("--tag", required=True, help="release tag。")
    parser.add_argument("--name", required=True, help="release 名称。")
    parser.add_argument("--body-file", required=True, help="release body 文件。")
    parser.add_argument("--target-commitish", default="main", help="Gitee release target_commitish。")
    parser.add_argument("--asset", action="append", default=[], help="需要上传的附件，可重复。")
    parser.add_argument("--token-env", default="GITEE_TOKEN", help="保存 Gitee token 的环境变量名。")
    return parser.parse_args()


def main() -> int:
    """CLI 入口。"""

    args = parse_args()
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        raise RuntimeError(f"缺少环境变量：{args.token_env}")
    asset_paths = [Path(item) for item in args.asset]
    missing = [str(path) for path in asset_paths if not path.exists()]
    if missing:
        raise RuntimeError("Gitee 同步附件不存在：\n" + "\n".join(missing))
    sync_release_assets(
        owner=args.owner,
        repo=args.repo,
        tag=args.tag,
        release_name=args.name,
        release_body=Path(args.body_file).read_text(encoding="utf-8"),
        target_commitish=args.target_commitish,
        asset_paths=asset_paths,
        token=token,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
