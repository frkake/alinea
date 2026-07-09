"""決定的なテスト用アセット生成(PNG)。

同一引数で常にバイト同一の PNG を返す(タイムスタンプ等の非決定要素を含めない)。
FakeImageProvider とモックサーバの両方が使う。
"""

from __future__ import annotations

import functools
import io

from PIL import Image


@functools.lru_cache(maxsize=8)
def png_bytes(width: int = 1024, height: int = 1024) -> bytes:
    """単色 RGB の PNG バイト列(決定的)。"""
    img = Image.new("RGB", (width, height), (62, 92, 118))  # アクセント色(plans/08)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()
