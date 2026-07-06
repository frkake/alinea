"""識別子・トークン生成ユーティリティ。

- `new_ulid()`: X-Request-Id / SSE イベント id 用の単調増加寄りな 26 文字 ULID。
- `new_token()` / `sha256_hex()`: セッション・拡張トークン・メールリンクの秘匿値生成とハッシュ。
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    """26 文字の ULID(48bit ミリ秒 + 80bit ランダム、Crockford Base32)。"""
    timestamp_ms = int(time.time() * 1000)
    randomness = int.from_bytes(os.urandom(10), "big")
    value = (timestamp_ms << 80) | randomness
    chars = [""] * 26
    for i in range(25, -1, -1):
        chars[i] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(chars)


def new_token(nbytes: int = 32) -> str:
    """URL-safe なランダムトークン(セッション・メールリンク・拡張トークンの平文値)。"""
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str) -> str:
    """トークンの SHA-256 16 進ハッシュ(DB/Redis には平文を残さない)。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
