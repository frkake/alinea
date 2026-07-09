"""arXiv ライセンス URL → papers.license キー正規化(plans/05 §3.3)。

OAI-PMH の `<license>` URL(scheme・末尾スラッシュ非依存)を CHECK 値域
(plans/02 §4.3 / alinea_core.licenses.LicenseId)へ落とす。未対応・取得失敗は
最も安全側(転載不可)の `unknown` に倒す(docs/09 §5.2)。
"""

from __future__ import annotations

import re

from alinea_core.licenses import LicenseId

_UNKNOWN: LicenseId = "unknown"

# キーは scheme を除去し末尾スラッシュを落として小文字化した形。
_LICENSE_MAP: dict[str, LicenseId] = {
    "creativecommons.org/licenses/by/4.0": "cc-by-4.0",
    "creativecommons.org/licenses/by-sa/4.0": "cc-by-sa-4.0",
    "creativecommons.org/licenses/by-nc/4.0": "cc-by-nc-4.0",
    "creativecommons.org/licenses/by-nc-sa/4.0": "cc-by-nc-sa-4.0",
    "creativecommons.org/licenses/by-nd/4.0": "cc-by-nd-4.0",
    "creativecommons.org/licenses/by-nc-nd/4.0": "cc-by-nc-nd-4.0",
    "creativecommons.org/publicdomain/zero/1.0": "cc0",
    "arxiv.org/licenses/nonexclusive-distrib/1.0": "arxiv-nonexclusive",
}

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def normalize_license_url(url: str | None) -> LicenseId:
    """ライセンス URL を papers.license キーへ正規化する(未対応・None は unknown)。"""
    if not url or not url.strip():
        return _UNKNOWN
    key = _SCHEME_RE.sub("", url.strip()).rstrip("/").lower()
    return _LICENSE_MAP.get(key, _UNKNOWN)
