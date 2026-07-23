"""記事生成(記事モード 1h)の純ロジック(plans/07 §4)。

- :mod:`alinea_core.article.schema`: 記事構造 JSON スキーマ(モデル出力)。
- :mod:`alinea_core.article.sources`: 素材収集(stage=collecting_sources)。
- :mod:`alinea_core.article.prompts`: 生成プロンプト(system/user、プリセット 4 種)。
- :mod:`alinea_core.article.postprocess`: 検証・正規化(stage=generating の後半)。

ジョブ実行(DB 書き込み・LLM 呼び出し)は :mod:`alinea_worker.tasks.generate_article` の責務。
"""

from alinea_core.article.postprocess import (
    ARTICLE_BLOCK_SCHEMA_SPEC,
    ARTICLE_SCHEMA_SPEC,
    ArticleGenerationError,
    BlockTypeMismatchError,
    NormalizedArticle,
    NormalizedBlock,
    build_attribution_block,
    build_disclaimer,
    normalize_article,
    normalize_rewritten_block,
)
from alinea_core.article.prompts import (
    PRESET_INCLUDE_MATH_DEFAULT,
    PRESET_OUTLINES,
    build_article_block_system_prompt,
    build_article_system_prompt,
    build_article_user_prompt,
    build_block_rewrite_user_prompt,
    build_regenerate_suffix,
)
from alinea_core.article.publication import (
    PUBLISHABLE_BLOCK_TYPES,
    build_paper_meta,
    sanitize_article_blocks,
    sanitize_overview_figure,
)
from alinea_core.article.sources import ArticleSources, collect_article_sources
from alinea_core.article.storage_keys import (
    article_snapshot_key,
    article_versions_cache_key,
)
from alinea_core.article.wire import (
    EvidenceDisplayResolver,
    ExplainerRef,
    article_block_wire_id,
    block_content_to_wire,
    build_article_block_wire,
    build_evidence_wire,
    parse_article_block_pk,
)

__all__ = [
    "ARTICLE_BLOCK_SCHEMA_SPEC",
    "ARTICLE_SCHEMA_SPEC",
    "PRESET_INCLUDE_MATH_DEFAULT",
    "PRESET_OUTLINES",
    "PUBLISHABLE_BLOCK_TYPES",
    "ArticleGenerationError",
    "ArticleSources",
    "BlockTypeMismatchError",
    "EvidenceDisplayResolver",
    "ExplainerRef",
    "NormalizedArticle",
    "NormalizedBlock",
    "article_block_wire_id",
    "article_snapshot_key",
    "article_versions_cache_key",
    "block_content_to_wire",
    "build_article_block_system_prompt",
    "build_article_block_wire",
    "build_article_system_prompt",
    "build_article_user_prompt",
    "build_attribution_block",
    "build_block_rewrite_user_prompt",
    "build_disclaimer",
    "build_evidence_wire",
    "build_paper_meta",
    "build_regenerate_suffix",
    "collect_article_sources",
    "normalize_article",
    "normalize_rewritten_block",
    "parse_article_block_pk",
    "sanitize_article_blocks",
    "sanitize_overview_figure",
]
