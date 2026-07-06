"""arXiv URL/ID 正規化と arXiv リクエスト URL 生成(plans/05 §3.1)。

docs/02 §2 の「abs / pdf / html / e-print / 旧形式をすべて `arxiv_id + version` に解決」を
確定する。新形式(2007-04 以降)・旧形式(archive(.subject)?/YYMMNNN)・URL 全パターンに対応。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 新形式 ID: YYMM.NNNN(2014-12 以前)/ YYMM.NNNNN(2015-01 以降)
_NEW_ID = r"\d{4}\.\d{4,5}"
# 旧形式 ID: archive(.subject)?/YYMMNNN(例 cs/9901002, math.GT/0309136, cond-mat/0207270)
_OLD_ID = r"[a-z][a-z-]+(?:\.[A-Z]{2})?/\d{7}"
_ID = rf"(?P<id>{_NEW_ID}|{_OLD_ID})"
_VER = r"(?:v(?P<ver>\d+))?"
_HOST = r"(?:www\.|export\.|browse\.)?arxiv\.org"
_AR5IV = r"(?:ar5iv\.labs\.arxiv\.org|ar5iv\.org)"
_SCHEME = r"(?:https?://)?"  # scheme は任意(拡張の Cite as 行・手入力に対応)
_TAIL = r"(?:[?#].*)?"  # 末尾の ?query / #fragment は無視

_PATTERNS: list[re.Pattern[str]] = [
    # 1) .../abs/2209.03003v3
    re.compile(rf"^{_SCHEME}{_HOST}/abs/{_ID}{_VER}{_TAIL}$"),
    # 2) .../pdf/2209.03003v3(.pdf 拡張子は任意)
    re.compile(rf"^{_SCHEME}{_HOST}/pdf/{_ID}{_VER}(?:\.pdf)?{_TAIL}$"),
    # 3) .../html/2209.03003v3
    re.compile(rf"^{_SCHEME}{_HOST}/html/{_ID}{_VER}{_TAIL}$"),
    # 4) .../e-print/... ・ /format/...
    re.compile(rf"^{_SCHEME}{_HOST}/(?:e-print|format)/{_ID}{_VER}{_TAIL}$"),
    # 5) ar5iv ミラー
    re.compile(rf"^{_SCHEME}{_AR5IV}/(?:html|abs)/{_ID}{_VER}{_TAIL}$"),
    # 6) テキスト形式 "arXiv:2209.03003v3"
    re.compile(rf"^(?i:arxiv):{_ID}{_VER}$"),
    # 7) 素の ID("2209.03003v3" / "cs/9901002")
    re.compile(rf"^{_ID}{_VER}$"),
]

# scheme(任意)+ arXiv 系ホスト + パス。ホスト部のみ小文字化するために使う
# (旧形式 ID の '.GT' 等の大文字はパスに含まれるため保持される)。
_HOST_PREFIX = re.compile(
    rf"^({_SCHEME})((?:{_HOST}|{_AR5IV}))(/.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ArxivId:
    """正規化済み arXiv 識別子。`id` はバージョン抜き(papers.arxiv_id と同値)。"""

    id: str
    version: int | None = None

    @property
    def arxiv_id(self) -> str:
        """papers.arxiv_id 相当(`id` の別名)。"""
        return self.id

    @property
    def version_suffix(self) -> str:
        """'v3' 形式のサフィックス。version 未指定なら空文字。"""
        return f"v{self.version}" if self.version is not None else ""

    @property
    def versioned(self) -> str:
        """バージョン付き文字列(例 '2209.03003v3' / 版指定なしは '2209.03003')。"""
        return f"{self.id}{self.version_suffix}"


# plans/05 §3.1 は ArxivRef 名を用いる。互換のため別名を提供する。
ArxivRef = ArxivId


def parse_arxiv_url(raw: str) -> ArxivId | None:
    """URL/ID 文字列を ArxivId に解決する。arXiv 由来でなければ None。"""
    s = raw.strip()
    if not s:
        return None
    # scheme + ホスト部のみ小文字化(パス以降は保持)
    if m := _HOST_PREFIX.match(s):
        s = m.group(1).lower() + m.group(2).lower() + m.group(3)
    for pat in _PATTERNS:
        if hit := pat.match(s):
            ver = hit.group("ver")
            return ArxivId(hit.group("id"), int(ver) if ver else None)
    return None


def normalize_arxiv_id(url_or_id: str) -> ArxivId:
    """URL/ID を ArxivId に正規化する。解決できなければ ValueError。"""
    ref = parse_arxiv_url(url_or_id)
    if ref is None:
        raise ValueError(f"not a recognizable arxiv id or url: {url_or_id!r}")
    return ref


def _hosts(base_url: str | None) -> tuple[str, str]:
    """(export ホスト, www ホスト) を返す。base_url override があれば両方 override。"""
    if base_url:
        b = base_url.rstrip("/")
        return b, b
    return "https://export.arxiv.org", "https://arxiv.org"


def api_query_url(ref: ArxivId, base_url: str | None = None) -> str:
    """メタデータ API(Atom)の URL(§3.2)。"""
    export, _www = _hosts(base_url)
    return f"{export}/api/query?id_list={ref.versioned}&max_results=1"


def oai_url(ref: ArxivId, base_url: str | None = None) -> str:
    """OAI-PMH(ライセンス取得)の URL(§3.3)。identifier はバージョン抜き。"""
    export, _www = _hosts(base_url)
    return f"{export}/oai2?verb=GetRecord&identifier=oai:arXiv.org:{ref.id}&metadataPrefix=arXiv"


def eprint_url(ref: ArxivId, base_url: str | None = None) -> str:
    """e-print(LaTeX ソース有無判定)の URL(§3.4)。"""
    export, _www = _hosts(base_url)
    return f"{export}/e-print/{ref.versioned}"
