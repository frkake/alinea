"""プロンプトキャッシュヘルパ(plans/04 §13)。

翻訳・チャットのプロンプトを「安定プレフィックス順」に構成する補助。Anthropic は
cache_hint を cache_control に写し、OpenAI は prompt_cache_key を設定する。
Google/DeepSeek は暗黙キャッシュのためプレフィックス順を守るだけでよい。
"""

from __future__ import annotations

# Anthropic の最小キャッシュ長(未満は自動的に非キャッシュ。§6.2-5)
MIN_CACHE_TOKENS = {"opus": 1024, "sonnet": 1024, "haiku": 2048}
# キャッシュブレークポイントの最大数(Anthropic。§6.2-5)
MAX_CACHE_BREAKPOINTS = 4


def translation_cache_key(revision_id: str, style: str, glossary_snapshot_id: str) -> str:
    """OpenAI prompt_cache_key(§13)。翻訳のルーティング精度向上に使う。"""
    return f"tr:{revision_id}:{style}:{glossary_snapshot_id}"


def min_cache_tokens_for(model_id: str) -> int:
    """モデル ID からキャッシュ有効化の最小トークン数を推定する(§6.2-5)。"""
    lowered = model_id.lower()
    if "haiku" in lowered:
        return MIN_CACHE_TOKENS["haiku"]
    return MIN_CACHE_TOKENS["opus"]
