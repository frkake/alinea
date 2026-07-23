"""GitHub REST クライアント(公開リポジトリのメタ・tree・archive)(§8・§13)。

api(見積り)と worker(取得)の両方が使う共有クライアント。apps 間 import を避けるため
py-core に置く。**リポジトリのコードは実行しない**。archive は tar.gz を bytes で受け取り、
:mod:`alinea_core.code_analysis.archive` が安全に展開する。

上限:
- default branch commit を解決してから ``git/trees?recursive=1`` を取る。truncated=True(巨大
  リポジトリ)は :class:`GitHubError('tree_truncated')` で拒否する(設計 §5)。
- archive download は圧縮 100 MiB を streaming で強制する(MAX_COMPRESSED_BYTES)。
- private / 404 は ``not_public``、403/429 は ``rate_limited`` を送出する。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

from alinea_core.code_analysis.archive import (
    CODE_EXTENSIONS,
    MAX_COMPRESSED_BYTES,
    MAX_TARGET_CODE_BYTES,
    MAX_TARGET_FILES,
    is_target_code_file,
)

_API_BASE = "https://api.github.com"
_USER_AGENT = "AlineaBot/1.0 (+https://alinea.app)"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class GitHubError(Exception):
    """GitHub アクセスの失敗(公開でない/レート制限/巨大/取得失敗)。"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass
class RepoMetadata:
    owner: str
    repo: str
    default_branch: str
    commit_sha: str
    tree_files: list[str] = field(default_factory=list)  # 対象コードファイルの repo 相対 path
    total_code_bytes: int = 0


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
    token = os.environ.get("GITHUB_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _raise_for_access(resp: httpx.Response) -> None:
    if resp.status_code == 404:
        raise GitHubError("not_public", "repository not found or private")
    if resp.status_code in (403, 429):
        raise GitHubError("rate_limited", f"github status {resp.status_code}")
    if resp.status_code >= 400:
        raise GitHubError("github_error", f"github status {resp.status_code}")


async def resolve_repo_metadata(
    client: httpx.AsyncClient, owner: str, repo: str
) -> RepoMetadata:
    """default branch の commit SHA を解決し、recursive tree から対象コードファイルを集める。

    tree が truncated(巨大リポジトリ)なら見積りを拒否する(設計 §5)。
    """
    resp = await client.get(f"{_API_BASE}/repos/{owner}/{repo}", headers=_headers())
    _raise_for_access(resp)
    meta = resp.json()
    default_branch = meta.get("default_branch") or "main"

    # default branch の HEAD commit SHA を解決(branch ref)。
    branch_resp = await client.get(
        f"{_API_BASE}/repos/{owner}/{repo}/branches/{default_branch}", headers=_headers()
    )
    _raise_for_access(branch_resp)
    commit_sha = branch_resp.json().get("commit", {}).get("sha")
    if not commit_sha:
        raise GitHubError("github_error", "could not resolve default branch commit")

    tree_resp = await client.get(
        f"{_API_BASE}/repos/{owner}/{repo}/git/trees/{commit_sha}",
        params={"recursive": "1"},
        headers=_headers(),
    )
    _raise_for_access(tree_resp)
    tree = tree_resp.json()
    if tree.get("truncated"):
        raise GitHubError("tree_truncated", "repository too large (tree truncated)")

    files: list[str] = []
    total = 0
    for entry in tree.get("tree", []):
        if entry.get("type") != "blob":
            continue
        path = entry.get("path", "")
        if is_target_code_file(path):
            files.append(path)
            size = entry.get("size")
            if isinstance(size, int):
                total += size
    # 見積り段階で対象コードの上限(§8: 10 MiB / 2,000 files)を強制する。ここで弾かないと
    # estimate は費用を返して保存し、ユーザーが確定・課金した後に worker の extract_repository が
    # ``target_code_too_large`` / ``too_many_files`` で必ず落ちる(estimate と extract で同じ
    # is_target_code_file を使うが、上限は extract 側にしか無かった)。tree_truncated と同じく
    # 「大きすぎる」として見積りを拒否し、確定前にユーザーへ返す。
    if total > MAX_TARGET_CODE_BYTES or len(files) > MAX_TARGET_FILES:
        raise GitHubError("repo_too_large", "target code exceeds analysis limit")
    return RepoMetadata(
        owner=owner,
        repo=repo,
        default_branch=default_branch,
        commit_sha=commit_sha,
        tree_files=files,
        total_code_bytes=total,
    )


async def download_archive(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    commit_sha: str,
    *,
    max_compressed_bytes: int = MAX_COMPRESSED_BYTES,
) -> bytes:
    """固定 commit の tarball を streaming で取得する。圧縮上限を超えたら拒否する。

    GitHub の archive endpoint は 302 で codeload へリダイレクトする(httpx は follow_redirects で
    追従)。commit SHA を指定するため branch 名ではなく固定 commit に固定される(設計 §8)。
    """
    url = f"{_API_BASE}/repos/{owner}/{repo}/tarball/{commit_sha}"
    chunks: list[bytes] = []
    total = 0
    async with client.stream(
        "GET", url, headers=_headers(), follow_redirects=True
    ) as resp:
        _raise_for_access(resp)
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > max_compressed_bytes:
                raise GitHubError("archive_too_large", "compressed archive exceeds limit")
            chunks.append(chunk)
    return b"".join(chunks)


__all__ = [
    "CODE_EXTENSIONS",
    "GitHubError",
    "RepoMetadata",
    "download_archive",
    "resolve_repo_metadata",
]
