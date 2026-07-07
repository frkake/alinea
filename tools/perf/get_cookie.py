"""perf.yml 用: メールリンク認証を実経路で通し、`yk_session=...` Cookie を stdout に出力する。

依存は標準ライブラリのみ。dev シードユーザー(dev@yakudoku.test)でログインし、k6 に渡す Cookie を得る。
使用: python tools/perf/get_cookie.py > cookie.txt
環境: APP_BASE_URL(既定 http://localhost:3000)/ MAILPIT_URL(既定 http://localhost:8025)/
      SEED_EMAIL(既定 dev@yakudoku.test)。
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.request

APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:3000")
MAILPIT_URL = os.environ.get("MAILPIT_URL", "http://localhost:8025")
EMAIL = os.environ.get("SEED_EMAIL", "dev@yakudoku.test")
LINK_RE = re.compile(r"https?://\S+/api/auth/email/verify\?token=\S+")


def _post_json(url: str, payload: dict[str, object]) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", "Origin": APP_BASE_URL}
    )
    urllib.request.urlopen(req, timeout=10).read()


def _get_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    # Mailpit をクリア。
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"{MAILPIT_URL}/api/v1/messages", method="DELETE"), timeout=10
        ).read()
    except OSError:
        pass

    _post_json(f"{APP_BASE_URL}/api/auth/email/request", {"email": EMAIL})

    link = None
    for _ in range(30):
        try:
            data = _get_json(f"{MAILPIT_URL}/api/v1/messages?limit=20")
        except OSError:
            time.sleep(0.5)
            continue
        for msg in data.get("messages", []) if isinstance(data, dict) else []:
            to = msg.get("To", [])
            if any(t.get("Address", "").lower() == EMAIL.lower() for t in to):
                detail = _get_json(f"{MAILPIT_URL}/api/v1/message/{msg['ID']}")
                text = detail.get("Text", "") if isinstance(detail, dict) else ""
                m = LINK_RE.search(text)
                if m:
                    link = m.group(0)
                    break
        if link:
            break
        time.sleep(0.5)

    if not link:
        print("login link not found in Mailpit", file=sys.stderr)
        return 1

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.open(link, timeout=10).read()
    for cookie in jar:
        if cookie.name == "yk_session":
            sys.stdout.write(f"yk_session={cookie.value}")
            return 0

    print("yk_session cookie not set", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
