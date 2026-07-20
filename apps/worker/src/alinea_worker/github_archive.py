"""固定 commit の GitHub archive を安全に取得・展開する worker ヘルパ(Task 21・設計 §8)。

実ロジック(REST クライアント・境界検証・展開)は py-core の
:mod:`alinea_core.code_analysis.github` / :mod:`alinea_core.code_analysis.archive` にある
(api と共有し apps 間 import を避ける)。本モジュールは worker 向けに 1 関数へまとめ、
一時 archive を確実に破棄する(bytes はメモリ内・GC 対象。ディスクへ落とさない)。

**リポジトリのコードは実行しない。依存のインストール・build・test も行わない。**
"""

from __future__ import annotations

import httpx
from alinea_core.code_analysis.archive import ExtractedRepo, extract_repository
from alinea_core.code_analysis.github import (
    GitHubError,
    RepoMetadata,
    download_archive,
    resolve_repo_metadata,
)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


async def fetch_repo_metadata(owner: str, repo: str) -> RepoMetadata:
    """default branch commit・対象コードファイル一覧・概算 byte 数を取得する。"""
    async with httpx.AsyncClient(trust_env=False, timeout=_TIMEOUT) as client:
        return await resolve_repo_metadata(client, owner, repo)


async def fetch_and_extract(
    owner: str, repo: str, commit_sha: str, *, client: httpx.AsyncClient | None = None
) -> ExtractedRepo:
    """固定 commit の tarball を取得し、安全に対象コードだけを抽出する。

    一時 archive(bytes)は関数スコープを抜けると解放される(ディスクへ書かない)。展開後の
    :class:`ExtractedRepo` は対象コードファイルのみを保持する(秘密/生成物/vendor は除外済み)。
    失敗・cancel でも一時データは残らない。
    """
    own = client is None
    c = client or httpx.AsyncClient(trust_env=False, timeout=_TIMEOUT)
    try:
        tar_bytes = await download_archive(c, owner, repo, commit_sha)
    finally:
        if own:
            await c.aclose()
    # extract_repository は tar を丸ごとメモリで検査・展開する(streaming 上限は download 側)。
    return extract_repository(tar_bytes, commit_sha=commit_sha)


__all__ = [
    "GitHubError",
    "RepoMetadata",
    "fetch_and_extract",
    "fetch_repo_metadata",
]
