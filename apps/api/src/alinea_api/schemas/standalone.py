"""論文単位スタンドアロンエクスポートの API スキーマ(Feature S3)。"""

from __future__ import annotations

from pydantic import BaseModel


class StandaloneAvailability(BaseModel):
    """成果物ごとの生成有無(UI の選択可否判定に使う。最新リビジョン基準)。"""

    source_html: bool
    translation_html: bool
    bilingual_html: bool
    article_html: bool
    pdf_original: bool
    pdf_translated: bool
    pdf_bilingual: bool
