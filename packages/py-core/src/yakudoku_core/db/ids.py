"""ID 生成ユーティリティ。

決定(plans/02 §1.1): DB 主キーは UUIDv4(`gen_random_uuid()`)。API 露出のため推測不能性を優先し、
拡張なしで生成できることを重視する。plans/00 §6.4 の接頭辞付き ULID は「ログ・URL 上で型が
自明」を狙う一般規約だが、DB 層は plans/02 §1.1(DDL の所有者)を正とし UUID を用いる。

`new_ulid()` は arq ジョブ相関 ID・SSE イベント ID など、時系列ソートが有用で DB 主キーでない
用途に使う(plans/01 §5 の単調増加 `id:`)。
"""

from __future__ import annotations

import uuid

from ulid import ULID


def new_uuid() -> str:
    """UUIDv4 文字列(DB 主キーはサーバ側 gen_random_uuid() だが、アプリ生成が要る場面で使う)。"""
    return str(uuid.uuid4())


def new_ulid() -> str:
    """時系列ソート可能な ULID 文字列(SSE イベント ID・相関 ID 用)。"""
    return str(ULID())
