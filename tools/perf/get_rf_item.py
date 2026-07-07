"""perf.yml 用: §14 シード(Rectified Flow)の library_item_id / revision_id を出力する。

PF-04(ビューア初期表示)の k6 スクリプトが対象を必要とするため、dev セッション Cookie で
`GET /api/library-items` を叩き、arxiv_id=2209.03003 の項目を探して viewer 応答から
revision_id を取る。標準ライブラリのみに依存する(get_cookie.py と同方針)。

使用: python tools/perf/get_rf_item.py "$COOKIE" > item.env
出力: 1 行に "ITEM_ID=<uuid> REVISION_ID=<uuid>"(k6 の -e 用に GITHUB_OUTPUT へ書く側で分割する)。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:3000")
ARXIV_ID = "2209.03003"


def _require_http(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"http(s) 以外の URL は開かない: {url}")
    return url


def _get_json(url: str, cookie: str) -> object:
    req = urllib.request.Request(  # noqa: S310  (_require_http 済み)
        _require_http(url), headers={"Cookie": cookie}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: get_rf_item.py <cookie>", file=sys.stderr)
        return 1
    cookie = sys.argv[1]

    data = _get_json(f"{APP_BASE_URL}/api/library-items?limit=200", cookie)
    items = data.get("items", []) if isinstance(data, dict) else []
    item = next((i for i in items if (i.get("paper") or {}).get("arxiv_id") == ARXIV_ID), None)
    if item is None:
        print(f"seed item (arxiv_id={ARXIV_ID}) not found", file=sys.stderr)
        return 1
    item_id = item["id"]

    viewer = _get_json(f"{APP_BASE_URL}/api/library-items/{item_id}/viewer", cookie)
    revision_id = viewer.get("revision", {}).get("id") if isinstance(viewer, dict) else None
    if not revision_id:
        print("revision_id not found in viewer response", file=sys.stderr)
        return 1

    sys.stdout.write(f"ITEM_ID={item_id} REVISION_ID={revision_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
