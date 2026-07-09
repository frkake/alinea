"""viewer / translations エンドポイントの DTO と純粋な導出ヘルパ(plans/03 §5.8・§6・§7)。

DB アクセスはルータ側に置き、本モジュールは Pydantic モデルと決定的な導出関数のみを持つ。
識別子・レスポンス型は plans/03 の逐語(§1.7 の共通スキーマ / §6・§7 の各定義)。
"""

from __future__ import annotations

from typing import Any, Literal

from alinea_core.licenses import classify_license
from pydantic import BaseModel, Field

from alinea_api.schemas.assets import encode_asset_id
from alinea_api.schemas.common import (
    LastPosition,
    LibraryItemSummary,
)

# --- 共通(plans/03 §1.7) ----------------------------------------------------------


class RevisionInfo(BaseModel):
    id: str
    quality_level: str
    source_version: str | None
    parser_version: str
    page_count: int | None
    figure_count: int
    table_count: int
    created_at: str


class NewerRevision(BaseModel):
    id: str
    reason: str  # "arxiv_update" | "parser_upgrade" | "promotion"


class TocNode(BaseModel):
    section_id: str
    number: str | None
    title_ja: str | None
    title_en: str
    translated: bool
    in_progress_denominator: bool
    on_demand: bool
    annotation_count: int
    bookmarked: bool
    children: list[TocNode] = Field(default_factory=list)


class ViewerTranslation(BaseModel):
    style: str
    set_id: str
    status: str  # "pending" | "partial" | "complete"
    progress_pct: int


class ViewerCounts(BaseModel):
    annotations: int
    resources: int
    figures: int
    notes: int


class LicenseCard(BaseModel):
    license: str
    # "allowed" | "allowed_with_sa" | "allowed_nc" | "allowed_nd" | "forbidden"
    figure_reuse: str
    message: str


class TimelineEntry(BaseModel):
    at: str
    label: str


class ViewerInit(BaseModel):
    """plans/03 §6.1 のレスポンス。"""

    library_item: LibraryItemSummary
    revision: RevisionInfo
    newer_revision: NewerRevision | None
    toc: list[TocNode]
    translation: ViewerTranslation | None
    counts: ViewerCounts
    last_position: LastPosition | None
    license_card: LicenseCard
    ingest_timeline: list[TimelineEntry]
    today_reading_minutes: int


# --- §6.2 リビジョン一覧 ------------------------------------------------------------


class RevisionListItem(BaseModel):
    id: str
    quality_level: str
    source_version: str | None
    parser_version: str
    created_at: str
    is_current: bool


class RevisionListResponse(BaseModel):
    items: list[RevisionListItem]


# --- §6.4 単一ブロック --------------------------------------------------------------


class BlockTranslation(BaseModel):
    text_ja: str
    state: str  # "machine" | "edited" | "protected"


class BlockDetail(BaseModel):
    block: dict[str, Any]
    section_id: str
    display: str
    translation: BlockTranslation | None


# --- §6.5 図表タブ ------------------------------------------------------------------


class FigurePosition(BaseModel):
    section_display: str
    page: int | None


class FigureItem(BaseModel):
    block_id: str
    kind: str  # "figure" | "table"
    label: str | None
    display: str
    caption_en: str
    caption_ja: str | None
    image_url: str | None
    position: FigurePosition


class FiguresResponse(BaseModel):
    items: list[FigureItem]


# --- §6.6 参考文献 ------------------------------------------------------------------


class ReferenceInLibrary(BaseModel):
    library_item_id: str


class ReferenceItem(BaseModel):
    ref_id: str
    aliases: list[str] = Field(default_factory=list)
    number: str
    raw: str | None
    authors: str | None
    title: str | None
    venue_year: str | None
    arxiv_id: str | None
    doi: str | None
    url: str | None
    in_library: ReferenceInLibrary | None


class ReferencesResponse(BaseModel):
    items: list[ReferenceItem]


# --- §5.8 読書位置 ------------------------------------------------------------------


class PositionRequest(BaseModel):
    revision_id: str
    block_id: str
    mode: Literal["translation", "parallel", "source", "pdf", "article"]


class PositionResponse(BaseModel):
    saved_at: str


# --- §7.1 翻訳セット一覧 ------------------------------------------------------------


class TranslationSetItem(BaseModel):
    set_id: str
    style: str
    scope: str  # "shared" | "personal"
    status: str  # "pending" | "partial" | "complete"
    progress_pct: int
    glossary_snapshot_id: str


class TranslationsListResponse(BaseModel):
    items: list[TranslationSetItem]


# --- §7.2 翻訳ユニット --------------------------------------------------------------


class UnitProposal(BaseModel):
    text_ja: str
    generated_at: str
    model: str


class TranslationUnitItem(BaseModel):
    unit_id: str
    block_id: str
    text_ja: str | None
    content_ja: Any | None
    state: str  # "machine" | "edited" | "protected"
    quality_flags: list[str]
    proposal: UnitProposal | None


class UnitsResponse(BaseModel):
    set_id: str
    items: list[TranslationUnitItem]


# --- §7.4 / §7.5 / §7.6 -------------------------------------------------------------


class PrioritizeRequest(BaseModel):
    section_id: str


class PrioritizeResponse(BaseModel):
    ok: bool


class SectionTranslateRequest(BaseModel):
    block_id: str | None = None


class SectionTranslateResponse(BaseModel):
    job_id: str


class RetryFailedTranslationsRequest(BaseModel):
    section_id: str | None = None


class RetryFailedTranslationsResponse(BaseModel):
    job_ids: list[str]
    block_count: int


class RetranslateRequest(BaseModel):
    instruction: str | None = None
    discard_edit: bool | None = None


class RetranslateResponse(BaseModel):
    job_id: str


# --- 純粋な導出ヘルパ ---------------------------------------------------------------

# license id -> 表示ラベル(1 行目の接頭。message 全体はサーバー供給値。plans/09 2a §4.2c)。
_LICENSE_LABELS: dict[str, str] = {
    "cc-by-4.0": "CC BY 4.0",
    "cc-by-sa-4.0": "CC BY-SA 4.0",
    "cc-by-nc-4.0": "CC BY-NC 4.0",
    "cc-by-nc-sa-4.0": "CC BY-NC-SA 4.0",
    "cc-by-nd-4.0": "CC BY-ND 4.0",
    "cc-by-nc-nd-4.0": "CC BY-NC-ND 4.0",
    "cc0": "CC0",
    "arxiv-nonexclusive": "arXiv 非独占ライセンス",
    "unknown": "ライセンス不明",
}
# figure_reuse -> 可否テキスト(docs/09 §5.2・2a 逐語「図表転載可」)。
_REUSE_TEXT: dict[str, str] = {
    "allowed": "図表転載可",
    "allowed_with_sa": "図表転載可(SA 表示が必要)",
    "allowed_nc": "図表転載可(非商用の範囲)",
    "allowed_nd": "図表転載可(改変不可・キャプションは分離表示)",
    "forbidden": "図表転載不可",
}


def figure_reuse_for(license_id: str) -> str:
    """ライセンス -> figure_reuse(plans/03 §6.1)。licenses.py のマトリクスから導出する。"""
    policy = classify_license(license_id)
    if policy.figure_embed == "link_card":
        return "forbidden"
    if policy.figure_embed == "caption_separate":
        return "allowed_nd"
    # figure_embed == "allow"
    if "nc" in license_id:
        return "allowed_nc"
    if policy.share_alike:
        return "allowed_with_sa"
    return "allowed"


def build_license_card(license_id: str) -> LicenseCard:
    """ライセンスカード(plans/03 §6.1)。「CC BY 4.0 — 図表転載可」等の表示文を導出。"""
    reuse = figure_reuse_for(license_id)
    label = _LICENSE_LABELS.get(license_id, license_id)
    message = f"{label} — {_REUSE_TEXT[reuse]}"
    return LicenseCard(license=license_id, figure_reuse=reuse, message=message)


def authors_short(authors: list[Any]) -> str:
    """PaperBib.authors_short(例「Liu, Gong, Liu」)。先頭 3 名の姓を ", " 連結。"""
    names: list[str] = []
    for a in authors[:3]:
        name = str(a.get("name", "")) if isinstance(a, dict) else str(a)
        last = name.split()[-1] if name.split() else name
        if last:
            names.append(last)
    short = ", ".join(names)
    if len(authors) > 3:
        short = f"{short} ほか" if short else "ほか"
    return short


def author_names(authors: list[Any]) -> list[str]:
    """PaperBib.authors(表示名の配列)。"""
    out: list[str] = []
    for a in authors:
        name = str(a.get("name", "")) if isinstance(a, dict) else str(a)
        if name:
            out.append(name)
    return out


def asset_url(storage_key: str | None) -> str | None:
    """storage key を /api/assets/{id} 経由の配信 URL に写す(§22.1)。

    asset id はストレージキー(スラッシュを含む)を URL エンコードしたもの。実配信は assets
    ルータ(§22.1)が担う。キーが無ければ null。
    """
    if not storage_key:
        return None
    return f"/api/assets/{encode_asset_id(storage_key)}"


TocNode.model_rebuild()
