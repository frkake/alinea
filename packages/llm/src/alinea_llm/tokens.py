"""count_tokens 補助(plans/04 §14)。

用途はチャットの文脈予算見積りと 30 ページ超判定のみ。課金計算には使わない
(課金は API レスポンスの usage が正)。
"""

from __future__ import annotations

import functools

import tiktoken

from alinea_llm.types import ContentPart, LLMRequest

# 画像パートは 1 枚 = 1,600 トークンの固定値(見積り用の保守値。§14)
IMAGE_TOKEN_COST = 1600


@functools.lru_cache(maxsize=1)
def _o200k() -> tiktoken.Encoding:
    return tiktoken.get_encoding("o200k_base")


def _parts_text(parts: list[ContentPart]) -> tuple[str, int]:
    """テキストを連結し、画像パート数を返す。"""
    text_chunks: list[str] = []
    images = 0
    for p in parts:
        if p.type == "image":
            images += 1
        elif p.text:
            text_chunks.append(p.text)
    return "\n".join(text_chunks), images


def estimate_tokens_o200k(req: LLMRequest) -> int:
    """tiktoken o200k_base によるローカル見積り(OpenAI / DeepSeek / xAI 用)。"""
    enc = _o200k()
    total = 0
    images = 0
    sys_text, sys_images = _parts_text(req.system)
    total += len(enc.encode(sys_text))
    images += sys_images
    for msg in req.messages:
        text, imgs = _parts_text(msg.parts)
        total += len(enc.encode(text))
        images += imgs
    return total + images * IMAGE_TOKEN_COST


def budget_for(context_window: int, max_output_tokens: int) -> int:
    """文脈予算の上限 = context_window - max_output_tokens - 2,048(安全帯)。"""
    return max(0, context_window - max_output_tokens - 2048)
