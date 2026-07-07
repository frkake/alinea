"""サムネイル生成(plans/05 §8・docs/02 §7)。

選定優先順位: ① `number == '1'` の figure → ② 画像面積最大の figure → ③ 図が無ければ
生成せず(概要図生成完了で差し替え)→ ④ `thumbnail_key = NULL`(フロントがタイトルカード)。

リサイズ: 白背景 RGB → 4:3 中央 cover クロップ → LANCZOS で 480x360 / 960x720 → WebP。
"""

from __future__ import annotations

import io

from PIL import Image

from yakudoku_core.document.blocks import Block

# card / card@2x の出力寸法(§8。4:3)。
CARD_SIZE = (480, 360)
CARD_2X_SIZE = (960, 720)
_WEBP_QUALITY = 82
_WEBP_METHOD = 6


def select_thumbnail_figure(
    figures: list[Block], *, areas: dict[str, int] | None = None
) -> Block | None:
    """サムネイル元の figure を選ぶ(§8 の優先順位 ①②)。

    ① ``number == '1'`` の figure。② 画像面積(``areas`` が渡された場合)最大の figure。
    面積不明なら文書順で最初の画像付き figure。画像付き figure が無ければ None(③④)。
    """
    with_asset = [f for f in figures if f.asset_key]
    if not with_asset:
        return None
    for fig in with_asset:
        if fig.number == "1":
            return fig
    if areas:
        return max(with_asset, key=lambda f: areas.get(f.id, 0))
    return with_asset[0]


def _cover_crop(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """4:3 中央 cover クロップ + LANCZOS リサイズ。白背景 RGB 化。"""
    rgb = Image.new("RGB", image.size, (255, 255, 255))
    if image.mode in ("RGBA", "LA", "P"):
        converted = image.convert("RGBA")
        rgb.paste(converted, mask=converted.split()[-1])
    else:
        rgb.paste(image.convert("RGB"))

    target_w, target_h = size
    target_ratio = target_w / target_h
    src_w, src_h = rgb.size
    src_ratio = src_w / src_h
    if src_ratio > target_ratio:
        # 横長すぎ → 幅を削る
        new_w = round(src_h * target_ratio)
        left = (src_w - new_w) // 2
        box = (left, 0, left + new_w, src_h)
    else:
        # 縦長すぎ → 高さを削る
        new_h = round(src_w / target_ratio)
        top = (src_h - new_h) // 2
        box = (0, top, src_w, top + new_h)
    cropped = rgb.crop(box)
    return cropped.resize(size, Image.Resampling.LANCZOS)


def _to_webp(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="WEBP", quality=_WEBP_QUALITY, method=_WEBP_METHOD)
    return buf.getvalue()


def render_thumbnail(source_png: bytes) -> tuple[bytes, bytes]:
    """ソース画像から card(480x360)/ card@2x(960x720)の WebP 2 枚を生成する(§8)。"""
    with Image.open(io.BytesIO(source_png)) as image:
        image.load()
        card = _to_webp(_cover_crop(image, CARD_SIZE))
        card_2x = _to_webp(_cover_crop(image, CARD_2X_SIZE))
    return card, card_2x
