"""S4: 公式実装(GitHub)自動検出の単体テスト。

`_extract_official_repo` を直接テストする。
ネットワーク・DB・パイプライン不使用。
"""

from __future__ import annotations

from alinea_core.arxiv.metadata import _extract_official_repo

# --------------------------------------------------------------------------- #
# Positive cases
# --------------------------------------------------------------------------- #


def test_extracts_from_comment_https_url() -> None:
    """comment に https:// 付き GitHub URL → 正規化 URL を返す。"""
    result = _extract_official_repo(
        comment="Code: https://github.com/gnobitab/RectifiedFlow",
        abstract="",
    )
    assert result == "https://github.com/gnobitab/RectifiedFlow"


def test_extracts_bare_url_without_scheme() -> None:
    """scheme なし github.com/owner/repo → https:// を補って返す。"""
    result = _extract_official_repo(
        comment="Code available at github.com/openai/whisper. ICLR 2023.",
        abstract="",
    )
    assert result == "https://github.com/openai/whisper"


def test_extracts_from_abstract_when_no_comment_url() -> None:
    """comment に URL なし、abstract にある場合 → abstract から抽出。"""
    result = _extract_official_repo(
        comment="ICLR 2023 (spotlight)",
        abstract="Implementation at https://github.com/huggingface/diffusers available.",
    )
    assert result == "https://github.com/huggingface/diffusers"


def test_normalizes_deep_path_to_owner_repo() -> None:
    """深いパス github.com/owner/repo/blob/main/... → owner/repo に正規化。"""
    result = _extract_official_repo(
        comment="See https://github.com/pytorch/pytorch/blob/main/README.md for details.",
        abstract="",
    )
    assert result == "https://github.com/pytorch/pytorch"


def test_strips_dot_git_suffix() -> None:
    """.git サフィックスを除去する。"""
    result = _extract_official_repo(
        comment="Code: github.com/owner/myrepo.git",
        abstract="",
    )
    assert result == "https://github.com/owner/myrepo"


def test_comment_takes_priority_over_abstract() -> None:
    """comment と abstract の両方に URL があれば comment を優先する。"""
    result = _extract_official_repo(
        comment="See github.com/first/repo for code.",
        abstract="Also at https://github.com/second/repo.",
    )
    assert result == "https://github.com/first/repo"


def test_underscores_and_dashes_in_repo_name() -> None:
    """アンダースコア・ハイフンを含む owner/repo を正しく抽出。"""
    result = _extract_official_repo(
        comment="Code: https://github.com/some-org/my_project",
        abstract="",
    )
    assert result == "https://github.com/some-org/my_project"


# --------------------------------------------------------------------------- #
# Negative cases
# --------------------------------------------------------------------------- #


def test_returns_none_when_no_github_url() -> None:
    """GitHub URL が存在しない場合は None。"""
    result = _extract_official_repo(
        comment="ICLR 2023 (spotlight). 14 pages, 8 figures.",
        abstract="We present a new method.",
    )
    assert result is None


def test_rejects_gist_url() -> None:
    """github.com/gist/... は Gist なのでスキップ → None。"""
    result = _extract_official_repo(
        comment="Snippet: https://gist.github.com/user/abc123",
        abstract="",
    )
    assert result is None


def test_rejects_gist_via_comment_only() -> None:
    """github.com/gist/{id} パターンを None にする。"""
    result = _extract_official_repo(
        comment="github.com/gist/abcdef1234567890",
        abstract="",
    )
    assert result is None


def test_returns_none_for_empty_inputs() -> None:
    """comment も abstract も空の場合は None。"""
    result = _extract_official_repo(comment="", abstract="")
    assert result is None


def test_returns_none_for_none_inputs() -> None:
    """comment / abstract が None の場合は None。"""
    result = _extract_official_repo(comment=None, abstract=None)
    assert result is None


def test_rejects_owner_starting_with_dot() -> None:
    """owner が '.' で始まる場合(相対パスアーティファクト)は None。"""
    result = _extract_official_repo(
        comment="github.com/.hidden/repo",
        abstract="",
    )
    assert result is None


def test_rejects_repo_with_only_owner_segment() -> None:
    """github.com/owner のみでリポジトリ名がない場合は None。"""
    result = _extract_official_repo(
        comment="See github.com/torvalds for Linux.",
        abstract="",
    )
    assert result is None
