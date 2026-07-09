"""翻訳プロンプト(plans/06 §5-7)。

system 2 層(静的プリアンブル + 論文スコープ)+ バッチ user メッセージのビルダと、
structured output スキーマ・対訳例集を公開する。
"""

from alinea_core.translation.prompts.examples import (
    BAD_EXAMPLES,
    GOOD_EXAMPLES,
    TRANSLATION_EXAMPLES,
    TranslationExample,
    format_examples,
)
from alinea_core.translation.prompts.templates import (
    FIELD_PROFILES,
    PROMPT_VERSION,
    TargetBlock,
    TranslatedBlock,
    TranslationBatchOut,
    build_paper_context,
    build_system_preamble,
    build_user_message,
    field_profile,
)

__all__ = [
    "BAD_EXAMPLES",
    "FIELD_PROFILES",
    "GOOD_EXAMPLES",
    "PROMPT_VERSION",
    "TRANSLATION_EXAMPLES",
    "TargetBlock",
    "TranslatedBlock",
    "TranslationBatchOut",
    "TranslationExample",
    "build_paper_context",
    "build_system_preamble",
    "build_user_message",
    "field_profile",
    "format_examples",
]
