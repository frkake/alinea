"""ingest エンドポイントの DTO(plans/03 §3.1・§3.2・§3.4)。

命名は ``Ingest`` 接頭辞(並行実装のスキーマ名衝突回避)。共通型(``PipelineState`` /
``LastPosition``。§1.7)は他タスクの定義と OpenAPI 上で衝突しないよう接頭辞付きで再掲する。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from alinea_core.db.models import Job
from pydantic import BaseModel, Field

# --- 共通サブオブジェクト(§1.7 の PipelineState / LastPosition の接頭辞付き再掲) ------


class IngestLastPosition(BaseModel):
    """§1.7 ``LastPosition``。読みかけ位置の要約(拡張・1d カードの「前回…」表示)。"""

    revision_id: str
    block_id: str
    mode: str  # "translation" | "parallel" | "source" | "pdf" | "article"
    section_display: str
    saved_at: str


class IngestPipelineState(BaseModel):
    """§1.7 ``PipelineState``。取り込み・翻訳の進行状態(3a/1d カード表示)。"""

    job_id: str
    stage: str  # DB jobs.stage(kind=ingest)の 8 値
    status: str  # Job.status と同値
    progress_pct: int
    readable_upto: str | None = None
    failed_reason: str | None = None


# --- GET /api/ingest/check(§3.1) ---------------------------------------------------


class IngestCheckBib(BaseModel):
    """kind=arxiv のみ非 null。メタデータ API 由来の軽量書誌プレビュー。"""

    title: str
    authors_short: str
    venue: str | None = None
    year: int | None = None


class IngestCheckSaved(BaseModel):
    """既にライブラリにある場合の要約(拡張は状態3へ)。"""

    library_item_id: str
    status: str
    added_at: str
    progress_pct: int
    last_position: IngestLastPosition | None = None
    pipeline: IngestPipelineState | None = None


class IngestHuggingFaceInfo(BaseModel):
    """kind="huggingface" のときの補助情報(Task 18)。

    - ``repo_kind``: paper / model / dataset / space。
    - ``arxiv_id``: 一意に決まった arXiv ID(Paper URL は path から、Model/Dataset/Space は
      ``arxiv:<ID>`` タグから)。決められない場合は ``None``。
    - ``arxiv_candidates``: Model/Dataset/Space の ``arxiv:<ID>`` タグが 0 件/複数件で一意に
      決まらないとき、選択可能な候補一覧(空 = 関連論文が見つからない)。
    """

    repo_kind: Literal["paper", "model", "dataset", "space"]
    repo_id: str
    arxiv_id: str | None = None
    arxiv_candidates: list[str] = Field(default_factory=list)


class IngestCheckResponse(BaseModel):
    """§3.1 GET /api/ingest/check の 200 応答。"""

    kind: Literal["arxiv", "site", "pdf", "unsupported", "huggingface"]
    arxiv_id: str | None = None
    arxiv_version: str | None = None
    # kind="site"(他サイトアダプタ。ACL Anthology 等)のときのみ非 null。
    site: str | None = None
    external_id: str | None = None
    bib: IngestCheckBib | None = None
    latex_available: bool | None = None
    suggested_tags: list[str] = Field(default_factory=list)
    saved: IngestCheckSaved | None = None
    # kind="huggingface" のときのみ非 null(Task 18)。
    huggingface: IngestHuggingFaceInfo | None = None


# --- POST /api/ingest/arxiv(§3.2) --------------------------------------------------


class IngestArxivRequest(BaseModel):
    """§3.2 リクエスト本文。"""

    url: str
    status: str | None = None
    tags: list[str] | None = None
    collection_id: str | None = None
    quick_note: str | None = None


class IngestArxivResponse(BaseModel):
    """§3.2 の 202 応答。"""

    paper_id: str
    library_item_id: str
    job_id: str
    duplicate: bool = False


# --- POST /api/ingest/site(S8。他サイト取り込み) ------------------------------------


class SiteIngestRequest(BaseModel):
    """POST /api/ingest/site のリクエスト本文(ACL Anthology 等の論文ページ URL)。"""

    url: str
    status: str | None = None
    tags: list[str] | None = None
    collection_id: str | None = None
    quick_note: str | None = None


class SiteIngestResponse(BaseModel):
    """POST /api/ingest/site の 202 応答(§3.2 と同型 + duplicate)。"""

    job_id: str
    library_item_id: str
    paper_id: str
    duplicate: bool = False


# --- POST /api/ingest/pdf(§3.3) ----------------------------------------------------


class IngestPdfMeta(BaseModel):
    """§3.3 の `meta`(multipart の JSON 文字列フィールド)。

    応答型は §3.2 と同型のため :class:`IngestArxivResponse` を再利用する(重複定義しない)。
    """

    source_url: str | None = None
    title_guess: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    collection_id: str | None = None
    quick_note: str | None = None


# --- GET /api/ingest/recent(§3.4) --------------------------------------------------


class IngestRecentItem(BaseModel):
    library_item_id: str
    title: str
    pipeline: IngestPipelineState
    completed_at: str | None = None
    viewer_url: str


class IngestRecentResponse(BaseModel):
    items: list[IngestRecentItem]


# --- ビルダ(純粋関数。DB アクセスはルータ側で行い、値だけ渡す) ----------------------


def authors_short(authors: list[Any]) -> str:
    """PaperBib.authors_short 形式("Liu, Gong, Liu")。姓(名前末尾トークン)を最大 3 件。"""
    names: list[str] = []
    for a in authors[:3]:
        raw = a.get("name") if isinstance(a, dict) else str(a)
        if not raw:
            continue
        parts = str(raw).split()
        names.append(parts[-1] if parts else str(raw))
    joined = ", ".join(names)
    if len(authors) > 3 and joined:
        joined += " et al."
    return joined


def build_pipeline_state(job: Job) -> IngestPipelineState:
    """Job(kind=ingest)から PipelineState を組み立てる(§1.7 の写像規則)。"""
    failed_reason: str | None = None
    if job.status == "failed" and job.error:
        try:
            parsed = json.loads(job.error)
            failed_reason = parsed.get("message") if isinstance(parsed, dict) else job.error
        except (ValueError, json.JSONDecodeError):
            failed_reason = job.error
    return IngestPipelineState(
        job_id=str(job.id),
        stage=job.stage,
        status=job.status,
        progress_pct=job.progress,
        readable_upto=None,
        failed_reason=failed_reason,
    )
