"""assets 配信の識別子エンコード(plans/03 §22.1)。

``GET /api/assets/{asset_id}`` の ``asset_id`` は assets バケットのストレージキーを
base64url でエンコードした不透明トークン。ビューア(図タブ)の ``image_url`` は
:func:`encode_asset_id` でこのトークンを組み立てる(他タスクから import して用いる)。

authorization はデコードしたキー先頭の paper_id をキー(``figures/{paper_id}/…`` /
``thumbnails/{paper_id}/…``)から取り出して行う(ルータ側)。エンコードにキー以上の情報を
埋めない(改ざんは所有チェックで弾く)。
"""

from __future__ import annotations

import base64
import binascii


def encode_asset_id(storage_key: str) -> str:
    """assets バケットのキーを URL 安全な asset_id に変換する。"""
    raw = storage_key.encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_asset_id(asset_id: str) -> str | None:
    """asset_id をストレージキーへ復号する。壊れていれば None。"""
    try:
        padded = asset_id + "=" * (-len(asset_id) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        key = raw.decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    # パストラバーサル・空キーを拒否する。
    if not key or ".." in key or key.startswith("/"):
        return None
    return key


def paper_id_from_key(storage_key: str) -> str | None:
    """assets キー(``figures/{pid}/…`` / ``thumbnails/{pid}/…``)から paper_id を取り出す。"""
    parts = storage_key.split("/")
    if len(parts) >= 2 and parts[0] in {"figures", "thumbnails"}:
        return parts[1] or None
    return None
