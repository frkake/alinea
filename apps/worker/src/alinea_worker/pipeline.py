"""取り込みステートマシンの駆動(plans/05 §2・§11・M0-18)。

8 段階 ``queued → fetching → parsing → structuring → translating_abstract → readable
→ translating_body → complete`` を駆動する。各段の出力はドメインテーブル(一意制約付き)へ
落ちるため、再実行時は「出力が既にあればスキップ」で二重処理が構造的に起きない(§2.3)。
段階の進行は :meth:`JobStore.checkpoint` で記録し、途中から再開できる(PY-JOB-02)。

外部依存(arXiv HTTP・Redis・S3・LLMRouter)は arq の ``ctx`` から注入する(apps 間 import を
避けるための DI。テストは Fake/ASGI スタブを注入して決定的にする)。通知発火は M1-07 に委譲する
(Global Constraints)。

**セッション注意**: :class:`JobStore` は各操作で ``session.expire_all()`` を呼ぶため、ORM
インスタンスを跨いで保持すると属性アクセス時に同期 IO(lazy load)が走り破綻する。本モジュールは
ID を文字列で保持し、読み書きの直前に ``session.get`` で都度取得する。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from typing import Any

import fitz  # PyMuPDF
import httpx
import structlog
from alinea_core.arxiv.fetch import (
    FetchError,
    RedisLike,
    Throttle,
    arxiv_throttle,
    make_arxiv_client,
)
from alinea_core.arxiv.ids import ArxivId, eprint_url, normalize_arxiv_id
from alinea_core.arxiv.metadata import ArxivMeta, fetch_metadata
from alinea_core.db.models import (
    DocumentRevision,
    Job,
    LibraryItem,
    Paper,
    QuotaLimit,
    SourceAsset,
    TranslationSet,
    UsageRecord,
    User,
)
from alinea_core.document.blocks import Block, DocumentContent
from alinea_core.document.plaintext import block_to_plain
from alinea_core.ingest import assess_document_completeness, joblog, progress
from alinea_core.ingest.bib_estimate import estimate_bibliography
from alinea_core.ingest.reanchor import ReanchorStats, reanchor_paper
from alinea_core.ingest.thumbnail import render_thumbnail, select_thumbnail_figure
from alinea_core.jobs.store import JobStore
from alinea_core.parsing.html_parser import (
    PARSER_VERSION as HTML_PARSER_VERSION,
)
from alinea_core.parsing.html_parser import (
    ParsedDocument,
)
from alinea_core.parsing.latex_parser import (
    PARSER_VERSION as LATEX_PARSER_VERSION,
)
from alinea_core.parsing.pdf_parser import (
    PARSER_VERSION as PDF_PARSER_VERSION,
)
from alinea_core.parsing.pdf_parser import (
    ParsedPdfDocument,
)
from alinea_core.search.rebuild import rebuild_block_search_index
from alinea_core.settings import CoreSettings, get_settings
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_core.translation.glossary import build_snapshot
from alinea_core.translation.pipeline import (
    TranslationContext,
    TranslationSettings,
    compute_translation_scope,
    find_shared_set,
    translate_block,
    translate_section,
)
from botocore.exceptions import ClientError
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_worker import notify
from alinea_worker.figure_assets import (
    FigureAssetError,
    FigureAssetPayload,
    conversion_requires_isolation,
    extract_inline_svg,
    fetch_html_asset,
    figure_asset_payload,
    isolated_figure_asset_payload,
    resolve_latex_source,
)
from alinea_worker.latex_pdf import LatexPdfBuildError, build_latex_translation_pdfs_if_ready
from alinea_worker.source_candidates import (
    CandidateUnavailable,
    SourceCandidate,
    embedded_pdf_bytes,
    load_original_pdf,
    parse_html_candidate,
    parse_latex_candidate,
    parse_pdf_candidate,
)

# 論文概要 + 提案タグ(1 呼び出しで生成)。DB フィールド名は後方互換のため summary_lines のまま。
SUMMARY_SCHEMA_NAME = "summary_3line_v1"
SUMMARY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary_lines", "suggested_tags"],
    "properties": {
        "summary_lines": {
            "type": "array",
            "minItems": 4,
            "maxItems": 6,
            "items": {"type": "string", "maxLength": 240},
        },
        "suggested_tags": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string", "maxLength": 30},
        },
    },
}
SUMMARY_SYSTEM_PROMPT = (
    "あなたは学術論文の編集者です。この概要だけを読めば、論文が何を問題とし、何を提案し、"
    "どう検証し、何が分かったかを具体的に説明できる内容にしてください。\n"
    "summary_lines は次のラベルで始まる日本語 4〜6 項目です: "
    "課題、提案、仕組み、検証、結果、限界。仕組みまたは限界は情報が乏しい場合のみ省略できます。\n"
    "各項目は『ラベル: 本文』形式で、固有の手法名、比較対象、データセット、評価指標、重要な数値を"
    "素材にある範囲で具体的に含めてください。宣伝文句や『高性能を達成』だけの抽象表現は禁止です。"
    "本文にない数値・主張を作らないでください。\n"
    "あわせて、この論文の主題を表す提案タグを suggested_tags に最大 5 件挙げてください"
    "(英語小文字の短い名詞。例: distillation, solver)。"
)

_MAX_SUGGESTED_TAGS = 5
MAX_FIGURES_PER_DOCUMENT = 200
MAX_TOTAL_FIGURE_MATERIALIZED_BYTES = 128 * 1024 * 1024


async def _materialize_figure_payload(
    data: bytes,
    source_name: str,
    content_type: str | None = None,
    *,
    materialized_budget: int | None = None,
) -> FigureAssetPayload:
    if materialized_budget is not None and len(data) >= materialized_budget:
        raise FigureAssetError(
            "figure_bytes_exceeded",
            "document figure bytes exceed the aggregate safe limit",
        )
    if conversion_requires_isolation(
        data,
        source_name=source_name,
        content_type=content_type,
    ):
        return await isolated_figure_asset_payload(
            data,
            source_name=source_name,
            content_type=content_type,
        )
    return figure_asset_payload(
        data,
        source_name=source_name,
        content_type=content_type,
    )


async def _materialize_inline_svg(
    raw_html: str,
    *,
    materialized_budget: int | None = None,
) -> FigureAssetPayload:
    try:
        svg = extract_inline_svg(raw_html)
        return await _materialize_figure_payload(
            svg,
            "inline.svg",
            "image/svg+xml",
            materialized_budget=materialized_budget,
        )
    except FigureAssetError as exc:
        if exc.code in {"asset_too_large", "figure_bytes_exceeded", "image_too_large"}:
            raise
        raise FigureAssetError("unsafe_inline_figure", "inline SVG was rejected") from exc
    except Exception as exc:
        raise FigureAssetError("unsafe_inline_figure", "inline SVG could not be extracted") from exc


log = structlog.get_logger("alinea.worker.pipeline")


async def _commit_with_asset_cleanup(
    session: Any,
    storage: Any,
    uploaded_keys: list[str],
) -> None:
    """Commit DB state and best-effort remove new revision assets on failure."""

    try:
        await session.commit()
    except Exception:
        try:
            await storage.delete_many(storage.assets_bucket, uploaded_keys)
        except Exception as cleanup_error:
            log.warning(
                "revision_asset_cleanup_failed",
                error_type=type(cleanup_error).__name__,
                key_count=len(uploaded_keys),
            )
        raise


_HISTORICAL_CANDIDATE_FAILURES = [
    {
        "format": "unknown",
        "code": "historical_diagnostics_unavailable",
        "message": "candidate failure history was not recorded",
    }
]
_RETRYABLE_CANDIDATE_CODES = frozenset(
    {"network_error", "rate_limited", "upstream_5xx", "storage_error"}
)


class IngestJobPayload(BaseModel):
    """ingest ジョブの payload(plans/05 §2.7)。"""

    mode: str = "initial"  # initial | reingest
    source: str = "arxiv"  # arxiv | pdf_upload
    arxiv_id: str | None = None
    requested_version: str | None = None
    url: str | None = None
    library_item_id: str | None = None
    # 通知「変更する」(B→A 昇格提案の apply。plans/03 §16.4・plans/05 §12.3)経由の reingest
    # にのみ立てるフラグ。structuring で新リビジョンが確定した時点で adopt-revision と同一の
    # 内部処理(papers.latest_revision_id 切替+reanchor_paper)を自動実行する(M1-07 followup)。
    # 通常の reingest(再取り込みボタン)では立てない — 自動適用はしない(P6)。
    adopt_on_complete: bool = False


@dataclass
class IngestDeps:
    """パイプラインの外部依存(arq ctx から注入)。"""

    s3: S3Storage
    router: Any  # alinea_llm.router.LLMRouter(apps→llm 依存を型で固定しない)
    settings: CoreSettings
    http: httpx.AsyncClient | None = None
    redis: RedisLike | None = None
    publish: Any | None = None
    arq_pool: Any | None = None
    throttle: Throttle = arxiv_throttle
    translation_quota_limit: int | None = None  # テスト上書き用(§2.6)


def deps_from_ctx(ctx: dict[str, Any]) -> IngestDeps:
    """arq ``ctx`` から依存を取り出す(未提供分は設定から生成)。"""
    settings = ctx.get("settings") or get_settings()
    return IngestDeps(
        s3=ctx.get("s3") or S3Storage(settings),
        router=ctx["router"],
        settings=settings,
        http=ctx.get("arxiv_http"),
        redis=ctx.get("redis"),
        publish=ctx.get("publish"),
        arq_pool=ctx.get("arq_pool"),
        throttle=ctx.get("throttle", arxiv_throttle),
        translation_quota_limit=ctx.get("translation_quota_limit"),
    )


def _www_base(settings: CoreSettings) -> str:
    return (settings.alinea_arxiv_base_url or "https://arxiv.org").rstrip("/")


def _to_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _is_pdf_like(data: bytes) -> bool:
    """Perform the shallow validation required before retaining an original PDF."""

    return len(data) >= 8 and data[:1024].lstrip().startswith(b"%PDF-")


def _extract_pdf_text(data: bytes) -> str:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            return "\n".join(page.get_text("text", sort=True) for page in doc)
        finally:
            doc.close()
    except Exception:
        return ""


def _embedded_pdf_identity(
    diagnostics: list[dict[str, Any]],
) -> tuple[str, str] | None:
    entries = [
        item
        for item in diagnostics
        if item.get("kind") == "embedded_pdf"
        or "embedded_pdf_source" in item
        or "embedded_pdf_sha256" in item
    ]
    if not entries:
        return None
    if len(entries) != 1:
        raise ValueError("embedded PDF diagnostics are ambiguous")
    entry = entries[0]
    source = entry.get("embedded_pdf_source")
    digest = entry.get("embedded_pdf_sha256")
    if (
        entry.get("kind") != "embedded_pdf"
        or not isinstance(source, str)
        or not source
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError("embedded PDF diagnostics are invalid")
    return source, digest


_EMBEDDED_PDF_PROVENANCE_KEYS = (
    "embedded_pdf_source",
    "embedded_pdf_sha256",
    "embedded_pdf_container_sha256",
    "embedded_pdf_container_storage_key",
)


def _is_missing_s3_object(exc: ClientError) -> bool:
    error = exc.response.get("Error", {})
    return str(error.get("Code") or "") in {"NoSuchKey", "404", "NotFound"}


class IngestRun:
    """1 本の ingest ジョブの状態機械実行。段階ごとに冪等・再開可能。"""

    def __init__(self, session: AsyncSession, store: JobStore, job: Job, deps: IngestDeps) -> None:
        self.session = session
        self.store = store
        self.deps = deps
        # JobStore の expire_all に耐えるため、ID は文字列で保持する。
        self.job_id = str(job.id)
        self.paper_id: str | None = str(job.paper_id) if job.paper_id else None
        self.library_item_id: str | None = str(job.library_item_id) if job.library_item_id else None
        self.user_id: str | None = str(job.user_id) if job.user_id else None
        self.payload = IngestJobPayload.model_validate(job.payload or {})
        self.ckpt = JobStore.get_checkpoint(job)
        self.is_pdf_upload = self.payload.source == "pdf_upload"
        # pdf_upload には arxiv_id/url が無い(plans/05 §9.1)。arXiv 系のみ ID 正規化する。
        self.ref: ArxivId | None = (
            None
            if self.is_pdf_upload
            else normalize_arxiv_id(self.payload.arxiv_id or self.payload.url or "")
        )
        self._allow_latest_pdf_alias = (
            self.ref is not None
            and self.ref.version is None
            and self.payload.requested_version is None
        )
        self.source_version: str = ""
        self.source_format: str = "pdf_upload" if self.is_pdf_upload else "arxiv_html"
        self.revision_id: str | None = None
        self.set_id: str | None = None
        self.content: DocumentContent | None = None
        self.parsed: ParsedDocument | None = None
        self.parsed_pdf: ParsedPdfDocument | None = None
        self.latex_binary_files: dict[str, bytes] = {}
        self.latex_main_tex_name: str | None = None
        self.latex_graphicspaths: list[str] = []
        self._latex_archive_bytes: bytes | None = None
        self._pdf_bytes: bytes | None = None
        self._pdf_text: str | None = None
        self._candidate_failures: list[dict[str, Any]] = []
        self._candidate_diagnostics: list[dict[str, Any]] = []
        self._candidate_completeness: dict[str, Any] | None = None
        self._candidate_storage_key: str | None = None
        self._candidate_sha256: str | None = None
        self._candidate_provenance_validation_required = False
        self.style: str = "natural"
        self._settings_obj: TranslationSettings | None = None

    @property
    def parser_version(self) -> str:
        """取得優先順位 LaTeX > HTML > PDF(plans/05 §1.3・§5・M2-01)。

        ``source_format`` は候補受理時に確定するため、structuring から読む本プロパティは
        常に実際に使ったパーサと一致する。
        """
        if self.is_pdf_upload:
            return PDF_PARSER_VERSION
        if self.source_format == "latex":
            return LATEX_PARSER_VERSION
        if self.source_format == "pdf":
            return PDF_PARSER_VERSION
        return HTML_PARSER_VERSION

    # -- ORM 取得(都度フレッシュ) ---------------------------------------

    async def _get_paper(self) -> Paper:
        if self.paper_id is None:
            raise FetchError("source_not_found", "ingest job has no paper_id")
        paper = await self.session.get(Paper, self.paper_id)
        if paper is None:
            raise FetchError("source_not_found", f"paper not found: {self.paper_id}")
        return paper

    async def _get_job(self) -> Job:
        job = await self.session.get(Job, self.job_id)
        if job is None:
            raise LookupError(f"ingest job not found: {self.job_id}")
        return job

    async def _get_library_item(self) -> LibraryItem | None:
        if self.library_item_id is None:
            return None
        return await self.session.get(LibraryItem, self.library_item_id)

    async def _load_user_settings(self) -> TranslationSettings:
        if self._settings_obj is None:
            settings: dict[str, Any] | None = None
            if self.user_id is not None:
                user = await self.session.get(User, self.user_id)
                settings = user.settings if user is not None else None
            self._settings_obj = TranslationSettings.from_user_settings(settings)
        return self._settings_obj

    def _resolve_style(self, visibility: str, settings: TranslationSettings) -> str:
        # 公開論文は shared・自然訳固定。private はユーザー既定スタイル(§11.2.1)。
        return "natural" if visibility == "public" else settings.default_style

    async def _log(
        self,
        stage: str,
        level: str,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
        timeline: bool = False,
    ) -> None:
        job = await self._get_job()
        await joblog.log(self.session, job, stage, level, message, detail=detail, timeline=timeline)

    # -- SSE 進捗発行(2a §5.7・plans/03 §21.2) ---------------------------

    async def _publish_stage(
        self, stage: str, progress_pct: int, *, status: str = "running"
    ) -> None:
        """段階遷移を SSE で発行する(InfoPanel の再取り込み進捗トースト。M1-07 followup)。

        ``routers/jobs.py`` の ``GET /api/jobs/{job_id}/events`` は ``events:user:{user_id}``
        (``services/events.py``)に流れたイベントを ``job_id`` で絞り込んで転送する。ここで
        publish する ``data`` は InfoPanel.tsx の ``onProgress`` がそのまま読む形
        (``{stage, status, progress_pct}``。``_job_state_frame`` の progress 分岐と同形)に揃える。
        """
        if self.deps.publish is None or self.user_id is None:
            return
        try:
            await self.deps.publish(
                {
                    "type": "job.progress",
                    "user_id": self.user_id,
                    "job_id": self.job_id,
                    "status": status,
                    "stage": stage,
                    "progress_pct": progress_pct,
                }
            )
        except Exception as exc:  # SSE は best-effort。ジョブ本体を止めない(§2.4 と同方針)。
            await log.awarning("ingest_publish_stage_failed", stage=stage, error=str(exc))

    async def _reanchor_after_adopt(self, old_revision_id: str) -> None:
        """通知「変更する」経由の reingest(``adopt_on_complete``)。

        ``POST /api/library-items/{id}/adopt-revision``(§6.8)と同一の ``reanchor_paper``
        (py-core 共有ロジック)を structuring の最終処理として同一ジョブ内で実行する
        (plans/05 §4.5「別ジョブにしない」)。自動適用はしない(P6): このパスは
        ``adopt_on_complete=true`` のときのみ通る(notifications action=apply が立てるフラグ)。
        """
        assert self.paper_id is not None and self.revision_id is not None
        if old_revision_id == self.revision_id:
            return
        stats: ReanchorStats = await reanchor_paper(
            self.session,
            paper_id=self.paper_id,
            old_revision_id=old_revision_id,
            new_revision_id=self.revision_id,
        )
        await self.session.commit()
        await self._log(
            "structuring",
            "info",
            f"リビジョン昇格のリアンカー: 移動 {stats.moved} 件・未配置 {stats.unplaced} 件",
            detail={"moved": stats.moved, "unplaced": stats.unplaced, "adopted": True},
        )

    async def _throttle(self) -> None:
        if self.deps.redis is not None:
            await self.deps.throttle(self.deps.redis)

    # -- 状態機械 ---------------------------------------------------------

    async def run(self) -> None:
        await self._stage_fetching()
        await self._stage_parse_and_structure()
        await self._stage_translating_abstract()
        await self._ensure_translation_set()
        await self._stage_readable()
        await self._stage_translating_body()

    # -- fetching ---------------------------------------------------------

    async def _existing_source_version(self) -> str | None:
        stmt = (
            select(DocumentRevision.source_version)
            .where(DocumentRevision.paper_id == self.paper_id)
            .order_by(DocumentRevision.created_at.desc())
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def _stage_fetching(self) -> None:
        # pdf_upload は従来どおり API が先行保存した原本だけを確認する。
        fetch_ck = self.ckpt.get("fetching")
        if fetch_ck is not None and not isinstance(fetch_ck, dict):
            raise FetchError("parse_error", "fetching checkpoint is not an object")
        if fetch_ck and fetch_ck.get("source_version"):
            self.source_version = str(fetch_ck["source_version"])
            self.source_format = str(fetch_ck.get("source_format", "arxiv_html"))
            if self.is_pdf_upload:
                await self._load_pdf_upload_bytes()
                return

            # arXiv の再開時も原本 PDF 不変条件を再確認する。資産行が stale でも
            # canonical key、最後に network の順で回復する。
            assert self.ref is not None
            self.ref = self._resolved_source_ref()
            http = self.deps.http
            owns_http = http is None
            if http is None:
                http = make_arxiv_client(self.deps.settings)
            try:
                await self._acquire_original_pdf(http)
            finally:
                if owns_http:
                    await http.aclose()
            return

        await self.store.set_progress(self.job_id, 10, stage="fetching")
        await self._publish_stage("fetching", 10)
        if self.is_pdf_upload:
            await self._stage_fetching_pdf()
            return

        prior = await self._existing_source_version()
        http = self.deps.http
        owns_http = http is None
        if http is None:
            http = make_arxiv_client(self.deps.settings)
        assert self.ref is not None
        source_bytes = b""
        try:
            meta = await fetch_metadata(self.ref, http=http, settings=self.deps.settings)
            paper = await self._get_paper()
            self._apply_metadata(paper, meta)
            self.source_version = (
                self.payload.requested_version or meta.latest_version or prior or "v1"
            )
            self.ref = self._resolved_source_ref()
            await self.session.commit()
            source_bytes = await self._acquire_original_pdf(http)
        finally:
            if owns_http:
                await http.aclose()

        await self._log(
            "fetching",
            "info",
            "原文 PDF を取得しました",
            detail={"format": "pdf", "bytes": len(source_bytes)},
        )
        await self.store.checkpoint(
            self.job_id,
            "fetching",
            {"source_version": self.source_version},
            progress=10,
        )

    def _resolved_source_ref(self) -> ArxivId:
        assert self.ref is not None
        version = self.source_version.removeprefix("v")
        if version.isdigit():
            return ArxivId(self.ref.id, int(version))
        return self.ref

    async def _acquire_original_pdf(self, http: httpx.AsyncClient) -> bytes:
        """Load a retained original PDF or fetch and retain it before candidates run."""

        assert self.paper_id is not None and self.ref is not None
        canonical_key = StorageKeys.original_pdf(self.paper_id, self.source_version)
        compatible_versions = [self.source_version]
        if self._allow_latest_pdf_alias and self.source_version != "latest":
            compatible_versions.append("latest")
        assets = list(
            (
                await self.session.execute(
                    select(SourceAsset)
                    .where(
                        SourceAsset.paper_id == self.paper_id,
                        SourceAsset.source_version.in_(compatible_versions),
                        SourceAsset.kind == "pdf",
                    )
                    .order_by(SourceAsset.storage_key.asc(), SourceAsset.id.asc())
                )
            )
            .scalars()
            .all()
        )
        exact_assets = [asset for asset in assets if asset.source_version == self.source_version]
        latest_assets = [asset for asset in assets if asset.source_version == "latest"]
        reconcile_assets = list(exact_assets)
        if self._allow_latest_pdf_alias and self.source_version != "latest":
            reconcile_assets.extend(latest_assets)
        cache_diagnostics: list[str] = []
        cache_storage_failed = False
        keys: list[tuple[str, str, str | None]] = []

        def add_key(label: str, key: str, canonical_version: str | None = None) -> None:
            if all(existing != key for _label, existing, _version in keys):
                keys.append((label, key, canonical_version))

        add_key("canonical", canonical_key, self.source_version)
        for asset in exact_assets:
            add_key("asset", asset.storage_key)
        if self._allow_latest_pdf_alias and self.source_version != "latest":
            for asset in latest_assets:
                add_key("latest_asset", asset.storage_key)
            add_key(
                "latest_canonical",
                StorageKeys.original_pdf(self.paper_id, "latest"),
                "latest",
            )

        for label, key, canonical_version in keys:
            try:
                if canonical_version is not None:
                    data = await load_original_pdf(self.deps.s3, self.paper_id, canonical_version)
                else:
                    data = await self.deps.s3.get(self.deps.s3.sources_bucket, key)
            except ClientError as exc:
                if not _is_missing_s3_object(exc):
                    cache_storage_failed = True
                    cache_diagnostics.append(f"{label}_storage_error")
                    continue
                cache_diagnostics.append(f"{label}_missing")
                continue
            except Exception:
                cache_storage_failed = True
                cache_diagnostics.append(f"{label}_storage_error")
                continue
            if not _is_pdf_like(data):
                cache_diagnostics.append(f"{label}_invalid")
                continue
            try:
                if key != canonical_key:
                    await self.deps.s3.put(
                        self.deps.s3.sources_bucket,
                        canonical_key,
                        data,
                        content_type="application/pdf",
                    )
                await self._record_original_pdf_asset(canonical_key, data, assets=reconcile_assets)
            except Exception as exc:
                await self.session.rollback()
                raise FetchError(
                    "storage_error",
                    f"original pdf retention failed: cache={label}_hit; network=not_used",
                ) from exc
            self._pdf_bytes = data
            return data

        base = _www_base(self.deps.settings)
        url = f"{base}/pdf/{self.ref.versioned}"
        context = ",".join(cache_diagnostics) or "cache_miss"

        def unavailable(kind: str, network: str) -> FetchError:
            effective_kind = (
                "storage_error" if cache_storage_failed and kind == "source_not_found" else kind
            )
            return FetchError(
                effective_kind,
                f"original pdf unavailable: cache={context}; network={network}",
            )

        try:
            await self._throttle()
            resp = await http.get(url, timeout=httpx.Timeout(120.0, connect=5.0))
        except httpx.HTTPError as exc:
            raise unavailable("network_error", "request_failed") from exc
        if resp.status_code == 429:
            raise unavailable("rate_limited", "http_429")
        if resp.status_code == 408:
            raise unavailable("network_error", "http_408")
        if resp.status_code == 404:
            raise unavailable("source_not_found", "http_404")
        if resp.status_code >= 500:
            raise unavailable("upstream_5xx", "upstream_5xx")
        if resp.status_code != 200:
            raise unavailable("source_not_found", f"http_{resp.status_code}")
        data = resp.content
        if not _is_pdf_like(data):
            raise unavailable("source_not_found", "invalid_pdf")
        try:
            await self.deps.s3.put(
                self.deps.s3.sources_bucket,
                canonical_key,
                data,
                content_type="application/pdf",
            )
            await self._record_original_pdf_asset(canonical_key, data, assets=reconcile_assets)
        except Exception as exc:
            await self.session.rollback()
            raise FetchError(
                "storage_error",
                f"original pdf retention failed: cache={context}; network=downloaded",
            ) from exc
        self._pdf_bytes = data
        return data

    async def _record_original_pdf_asset(
        self, key: str, data: bytes, *, assets: list[SourceAsset]
    ) -> None:
        assert self.ref is not None
        source_url = f"{_www_base(self.deps.settings)}/pdf/{self.ref.versioned}"
        digest = hashlib.sha256(data).hexdigest()
        if assets:
            for asset in assets:
                asset.source_url = source_url
                asset.source_version = self.source_version
                asset.storage_key = key
                asset.content_type = "application/pdf"
                asset.byte_size = len(data)
                asset.sha256 = digest
            await self.session.commit()
            return
        await self._record_source_asset(
            "pdf",
            key,
            content_type="application/pdf",
            byte_size=len(data),
            source_url=source_url,
            sha256=digest,
        )

    async def _get_pdf_bytes(self) -> bytes:
        """原本 PDF を S3 から取得する(未取得なら canonical key を読む)。"""
        if self._pdf_bytes is not None:
            return self._pdf_bytes
        assert self.paper_id is not None
        data = await self.deps.s3.get(
            self.deps.s3.sources_bucket,
            StorageKeys.original_pdf(self.paper_id, self.source_version or "v1"),
        )
        self._pdf_bytes = data
        return data

    async def _load_pdf_upload_bytes(self) -> bytes:
        """Load an uploaded PDF while preserving missing-vs-transient storage semantics."""

        try:
            return await self._get_pdf_bytes()
        except ClientError as exc:
            if _is_missing_s3_object(exc):
                raise FetchError("source_not_found", "original pdf is missing") from exc
            raise FetchError("storage_error", "original pdf storage is unavailable") from exc
        except FetchError:
            raise
        except Exception as exc:
            raise FetchError("storage_error", "original pdf storage is unavailable") from exc

    async def _stage_fetching_pdf(self) -> None:
        """pdf_upload: ローカル資産(拡張が送信済みの原本 PDF)の存在確認のみで完了する(§9.2)。

        `POST /api/ingest/pdf` が S3 に既に PUT 済みのため、再取得(HTTP)は発生しない。
        """
        self.source_version = "v1"
        self.source_format = "pdf_upload"
        data = await self._load_pdf_upload_bytes()

        await self._log(
            "fetching",
            "info",
            joblog.fetch_timeline_message(self.source_format),
            detail={"format": self.source_format, "bytes": len(data)},
            timeline=True,
        )
        await self.store.checkpoint(
            self.job_id,
            "fetching",
            {"source_version": self.source_version, "source_format": self.source_format},
            progress=10,
        )

    def _apply_metadata(self, paper: Paper, meta: ArxivMeta) -> None:
        paper.arxiv_id = meta.arxiv_id
        paper.title = meta.title or paper.title
        paper.authors = list(meta.authors)
        paper.abstract = meta.abstract
        paper.published_on = _to_date(meta.published_on)
        paper.arxiv_categories = list(meta.arxiv_categories)
        paper.doi = meta.doi
        paper.venue = meta.venue
        paper.license = meta.license
        paper.latest_version = meta.latest_version

    async def _fetch_latex_candidate_bytes(self, http: httpx.AsyncClient) -> bytes:
        assert self.ref is not None
        try:
            await self._throttle()
            resp = await http.get(
                eprint_url(self.ref, self.deps.settings.alinea_arxiv_base_url or None),
                timeout=httpx.Timeout(60.0, connect=5.0),
            )
        except httpx.HTTPError as exc:
            raise CandidateUnavailable(
                "latex", "network_error", "arxiv e-print request failed"
            ) from exc
        if resp.status_code == 408:
            raise CandidateUnavailable("latex", "network_error", "arxiv e-print request timed out")
        if resp.status_code == 429:
            raise CandidateUnavailable(
                "latex", "rate_limited", "arxiv e-print request was rate limited"
            )
        if resp.status_code == 404:
            raise CandidateUnavailable("latex", "source_not_found", "arxiv e-print returned 404")
        if resp.status_code >= 500:
            raise CandidateUnavailable("latex", "upstream_5xx", "arxiv e-print upstream failure")
        if resp.status_code != 200:
            raise CandidateUnavailable("latex", "source_not_found", "arxiv e-print was unavailable")
        if "pdf" in resp.headers.get("content-type", "").lower():
            raise CandidateUnavailable(
                "latex",
                "source_not_found",
                "arxiv e-print returned PDF instead of LaTeX source",
            )
        return resp.content

    async def _fetch_html_candidate_bytes(self, http: httpx.AsyncClient) -> bytes:
        assert self.ref is not None
        url = f"{_www_base(self.deps.settings)}/html/{self.ref.versioned}"
        try:
            await self._throttle()
            resp = await http.get(url, timeout=httpx.Timeout(30.0, connect=5.0))
        except httpx.HTTPError as exc:
            raise CandidateUnavailable(
                "arxiv_html", "network_error", "arxiv html request failed"
            ) from exc
        if resp.status_code == 408:
            raise CandidateUnavailable(
                "arxiv_html", "network_error", "arxiv html request timed out"
            )
        if resp.status_code == 429:
            raise CandidateUnavailable(
                "arxiv_html", "rate_limited", "arxiv html request was rate limited"
            )
        if resp.status_code == 404:
            raise CandidateUnavailable("arxiv_html", "source_not_found", "arxiv html returned 404")
        if resp.status_code >= 500:
            raise CandidateUnavailable("arxiv_html", "upstream_5xx", "arxiv html upstream failure")
        if resp.status_code != 200:
            raise CandidateUnavailable(
                "arxiv_html", "source_not_found", "arxiv html was unavailable"
            )
        if "ltx_document" not in resp.text:
            raise CandidateUnavailable(
                "arxiv_html", "source_not_found", "arxiv html has no ltx_document"
            )
        return resp.content

    def _pdf_text_for_completeness(self) -> str:
        if self._pdf_text is not None:
            return self._pdf_text
        assert self._pdf_bytes is not None
        # Shallow acquisition deliberately leaves deep validation to the PDF candidate.
        self._pdf_text = _extract_pdf_text(self._pdf_bytes)
        return self._pdf_text

    async def _latex_candidate(self, http: httpx.AsyncClient) -> SourceCandidate:
        raw = await self._fetch_latex_candidate_bytes(http)
        candidate, binary_files, main_tex_name = parse_latex_candidate(
            raw, pdf_text=self._pdf_text_for_completeness()
        )
        self._latex_archive_bytes = raw
        self.latex_binary_files = binary_files
        self.latex_main_tex_name = main_tex_name
        self.latex_graphicspaths = list(candidate.graphicspaths)
        return candidate

    async def _html_candidate(self, http: httpx.AsyncClient) -> SourceCandidate:
        raw = await self._fetch_html_candidate_bytes(http)
        candidate = parse_html_candidate(raw, pdf_text=self._pdf_text_for_completeness())
        self._latex_archive_bytes = None
        self.latex_binary_files = {}
        self.latex_main_tex_name = None
        self.latex_graphicspaths = []
        return candidate

    async def _pdf_candidate(self) -> SourceCandidate:
        data = await self._get_pdf_bytes()
        candidate = parse_pdf_candidate(data, pdf_text=self._pdf_text_for_completeness())
        self._latex_archive_bytes = None
        self.latex_binary_files = {}
        self.latex_main_tex_name = None
        self.latex_graphicspaths = []
        return candidate

    def _set_candidate_state(
        self,
        candidate: SourceCandidate,
        failures: list[dict[str, Any]],
        *,
        completeness: dict[str, Any] | None = None,
    ) -> None:
        self.source_format = candidate.source_format
        self.content = candidate.content
        self._candidate_provenance_validation_required = True
        self._candidate_failures = failures
        self._candidate_diagnostics = [dict(item) for item in candidate.diagnostics]
        self._candidate_completeness = completeness or candidate.report.as_dict()
        try:
            embedded_pdf = _embedded_pdf_identity(self._candidate_diagnostics)
        except ValueError as exc:
            raise FetchError("parse_error", "selected source diagnostics are invalid") from exc
        if candidate.source_format != "latex" and embedded_pdf is None:
            self._latex_archive_bytes = None
            self.latex_binary_files = {}
            self.latex_main_tex_name = None
            self.latex_graphicspaths = []
        elif candidate.source_format == "latex":
            self.latex_graphicspaths = list(candidate.graphicspaths)
        if isinstance(candidate.parsed, ParsedPdfDocument):
            self.parsed = None
            self.parsed_pdf = candidate.parsed
        else:
            self.parsed = candidate.parsed
            self.parsed_pdf = None

    def _selected_embedded_pdf_provenance(self) -> dict[str, str] | None:
        try:
            embedded_pdf = _embedded_pdf_identity(self._candidate_diagnostics)
        except ValueError as exc:
            raise FetchError("parse_error", "selected source diagnostics are invalid") from exc
        if embedded_pdf is None:
            return None
        if (
            self.source_format != "pdf"
            or self._candidate_storage_key is None
            or self._candidate_sha256 is None
        ):
            raise FetchError("parse_error", "selected embedded source identity is incomplete")
        return {
            "embedded_pdf_source": embedded_pdf[0],
            "embedded_pdf_sha256": embedded_pdf[1],
            "embedded_pdf_container_sha256": self._candidate_sha256,
            "embedded_pdf_container_storage_key": self._candidate_storage_key,
        }

    def _validate_revision_candidate_provenance(self, revision: DocumentRevision) -> None:
        if not self._candidate_provenance_validation_required:
            return
        expected = self._selected_embedded_pdf_provenance()
        stats = revision.stats if isinstance(revision.stats, dict) else {}
        if expected is None:
            if any(key in stats for key in _EMBEDDED_PDF_PROVENANCE_KEYS):
                raise FetchError(
                    "parse_error",
                    "selected source does not match existing embedded revision provenance",
                )
            return
        actual = {key: stats.get(key) for key in expected}
        if revision.source_format != "pdf" or actual != expected:
            raise FetchError(
                "parse_error",
                "selected embedded source does not match existing revision provenance",
            )

    @staticmethod
    def _checkpoint_candidate_failures(checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
        failures = checkpoint.get("candidate_failures", [])
        if not isinstance(failures, list):
            raise FetchError("parse_error", "parsing checkpoint candidate failures are invalid")
        return [dict(item) for item in failures if isinstance(item, dict)]

    @staticmethod
    def _checkpoint_candidate_diagnostics(checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
        raw_diagnostics = checkpoint.get("candidate_diagnostics", [])
        if not isinstance(raw_diagnostics, list) or not all(
            isinstance(item, dict) for item in raw_diagnostics
        ):
            raise FetchError("parse_error", "parsing checkpoint candidate diagnostics are invalid")
        diagnostics = [dict(item) for item in raw_diagnostics]
        try:
            _embedded_pdf_identity(diagnostics)
        except ValueError as exc:
            raise FetchError(
                "parse_error", "parsing checkpoint embedded source identity is invalid"
            ) from exc
        return diagnostics

    async def _load_retained_source_bytes(
        self,
        *,
        kind: str,
        canonical_key: str,
        source_format: str,
        selected_key: str | None = None,
        expected_sha256: str | None = None,
    ) -> bytes:
        assert self.paper_id is not None
        assets = (
            (
                await self.session.execute(
                    select(SourceAsset)
                    .where(
                        SourceAsset.paper_id == self.paper_id,
                        SourceAsset.source_version == self.source_version,
                        SourceAsset.kind == kind,
                    )
                    .order_by(SourceAsset.storage_key.asc(), SourceAsset.id.asc())
                )
            )
            .scalars()
            .all()
        )
        keys = (
            [selected_key]
            if selected_key is not None
            else list(dict.fromkeys([canonical_key, *(asset.storage_key for asset in assets)]))
        )
        storage_failed = False
        for key in keys:
            try:
                data = await self.deps.s3.get(self.deps.s3.sources_bucket, key)
            except ClientError as exc:
                if _is_missing_s3_object(exc):
                    continue
                storage_failed = True
                continue
            except Exception:
                storage_failed = True
                continue
            if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
                raise FetchError(
                    "storage_error",
                    f"stored selected source digest mismatch: format={source_format}",
                )
            return data
        reason = "storage_unavailable" if storage_failed else "object_missing"
        raise FetchError(
            "storage_error",
            f"stored selected source unavailable: format={source_format}; reason={reason}",
        )

    async def _load_checkpoint_candidate(self, checkpoint: dict[str, Any]) -> SourceCandidate:
        assert self.paper_id is not None
        source_format = str(checkpoint.get("source_format") or "")
        diagnostics = self._checkpoint_candidate_diagnostics(checkpoint)
        embedded_pdf = _embedded_pdf_identity(diagnostics)
        if embedded_pdf is not None and source_format != "pdf":
            raise FetchError("parse_error", "parsing checkpoint embedded source format is invalid")
        selected_key_value = checkpoint.get("source_storage_key")
        selected_sha_value = checkpoint.get("source_sha256")
        if (selected_key_value is None) != (selected_sha_value is None):
            raise FetchError("parse_error", "parsing checkpoint has incomplete source identity")
        if selected_key_value is not None and (
            not isinstance(selected_key_value, str)
            or not selected_key_value
            or not isinstance(selected_sha_value, str)
            or len(selected_sha_value) != 64
            or any(char not in "0123456789abcdef" for char in selected_sha_value)
        ):
            raise FetchError("parse_error", "parsing checkpoint source identity is invalid")
        selected_key = selected_key_value
        selected_sha256 = selected_sha_value
        if embedded_pdf is not None and selected_key is None:
            raise FetchError("parse_error", "parsing checkpoint has incomplete source identity")
        canonical_keys = {
            "latex": StorageKeys.latex_tar(self.paper_id, self.source_version),
            "arxiv_html": StorageKeys.arxiv_html(self.paper_id, self.source_version),
            "pdf": StorageKeys.original_pdf(self.paper_id, self.source_version),
            "pdf_upload": StorageKeys.original_pdf(self.paper_id, self.source_version),
        }
        canonical_key = (
            canonical_keys["latex"]
            if embedded_pdf is not None
            else canonical_keys.get(source_format)
        )
        if selected_key is not None and selected_key != canonical_key:
            raise FetchError("parse_error", "parsing checkpoint source key is invalid")
        try:
            if source_format == "latex":
                raw = await self._load_retained_source_bytes(
                    kind="arxiv_latex",
                    canonical_key=canonical_keys["latex"],
                    source_format=source_format,
                    selected_key=selected_key,
                    expected_sha256=selected_sha256,
                )
                candidate, binary_files, main_tex_name = parse_latex_candidate(
                    raw, pdf_text=self._pdf_text_for_completeness()
                )
                self._latex_archive_bytes = raw
                self.latex_binary_files = binary_files
                self.latex_main_tex_name = main_tex_name
                self.latex_graphicspaths = list(candidate.graphicspaths)
            elif source_format == "arxiv_html":
                raw = await self._load_retained_source_bytes(
                    kind="arxiv_html",
                    canonical_key=canonical_keys["arxiv_html"],
                    source_format=source_format,
                    selected_key=selected_key,
                    expected_sha256=selected_sha256,
                )
                candidate = parse_html_candidate(raw, pdf_text=self._pdf_text_for_completeness())
            elif source_format == "pdf" and embedded_pdf is not None:
                raw = await self._load_retained_source_bytes(
                    kind="arxiv_latex",
                    canonical_key=canonical_keys["latex"],
                    source_format=source_format,
                    selected_key=selected_key,
                    expected_sha256=selected_sha256,
                )
                wrapper, binary_files, main_tex_name = parse_latex_candidate(
                    raw, pdf_text=self._pdf_text_for_completeness()
                )
                selected = embedded_pdf_bytes(wrapper.report, binary_files)
                if selected is None or selected[0] != embedded_pdf[0]:
                    raise FetchError("parse_error", "stored embedded source identity is invalid")
                _member_name, member_bytes = selected
                if hashlib.sha256(member_bytes).hexdigest() != embedded_pdf[1]:
                    raise FetchError("storage_error", "stored embedded source digest mismatch")
                self._latex_archive_bytes = raw
                self.latex_binary_files = binary_files
                self.latex_main_tex_name = main_tex_name
                self.latex_graphicspaths = list(wrapper.graphicspaths)
                candidate = parse_pdf_candidate(
                    member_bytes, pdf_text=_extract_pdf_text(member_bytes)
                )
                candidate.diagnostics = diagnostics
            elif source_format in {"pdf", "pdf_upload"}:
                raw = await self._load_retained_source_bytes(
                    kind="pdf",
                    canonical_key=canonical_keys[source_format],
                    source_format=source_format,
                    selected_key=selected_key,
                    expected_sha256=selected_sha256,
                )
                self._pdf_bytes = raw
                candidate = parse_pdf_candidate(raw, pdf_text=self._pdf_text_for_completeness())
            else:
                raise FetchError("parse_error", "parsing checkpoint has an invalid source format")
        except CandidateUnavailable as exc:
            code = "no_text_layer" if exc.code == "no_text_layer" else "parse_error"
            raise FetchError(
                code,
                f"stored selected source is invalid: format={source_format}; code={exc.code}",
            ) from exc

        expected_parser = str(checkpoint.get("parser_version") or "")
        if expected_parser and candidate.parsed.parser_version != expected_parser:
            raise FetchError(
                "parse_error",
                f"stored selected source parser mismatch: format={source_format}",
            )
        if not candidate.report.accepted:
            failure = {"format": candidate.source_format, **candidate.report.as_dict()}
            raise FetchError(
                "document_incomplete",
                json.dumps({"candidates": [failure]}, ensure_ascii=False, sort_keys=True),
            )

        failures = self._checkpoint_candidate_failures(checkpoint)
        completeness = candidate.report.as_dict()
        stored_completeness = checkpoint.get("completeness")
        if isinstance(stored_completeness, dict):
            completeness.update(stored_completeness)
        if not completeness.get("accepted"):
            raise FetchError("document_incomplete", "stored selected source is incomplete")
        self._set_candidate_state(candidate, failures, completeness=completeness)
        self._candidate_storage_key = selected_key
        self._candidate_sha256 = selected_sha256
        return candidate

    async def _select_source_candidate(self) -> tuple[SourceCandidate, list[dict[str, Any]]]:
        failures: list[dict[str, Any]] = []
        http = self.deps.http
        owns_http = http is None
        if http is None:
            http = make_arxiv_client(self.deps.settings)
        try:
            for source_format in ("latex", "arxiv_html", "pdf"):
                try:
                    if source_format == "latex":
                        candidate = await self._latex_candidate(http)
                    elif source_format == "arxiv_html":
                        candidate = await self._html_candidate(http)
                    else:
                        candidate = await self._pdf_candidate()
                except CandidateUnavailable as exc:
                    failure: dict[str, Any] = exc.as_dict()
                    failures.append(failure)
                    display_format = {
                        "latex": "LaTeX",
                        "arxiv_html": "arXiv HTML",
                        "pdf": "PDF",
                    }.get(exc.source_format, exc.source_format)
                    await self._log(
                        "fetching",
                        "warn",
                        f"{display_format} ソース候補を利用できません(次候補へフォールバック)",
                        detail=failure,
                    )
                    continue

                if (
                    candidate.source_format == "latex"
                    and candidate.report.code == "embedded_pdf_wrapper"
                ):
                    wrapper_failure = {
                        "format": candidate.source_format,
                        **candidate.report.as_dict(),
                    }
                    failures.append(wrapper_failure)
                    await self._log(
                        "parsing",
                        "warn",
                        "latex ソース候補は不完全です(埋め込み PDF を確認)",
                        detail=wrapper_failure,
                    )
                    selected = embedded_pdf_bytes(candidate.report, self.latex_binary_files)
                    if selected is None:
                        continue
                    member_name, member_bytes = selected
                    try:
                        promoted = parse_pdf_candidate(
                            member_bytes, pdf_text=_extract_pdf_text(member_bytes)
                        )
                    except CandidateUnavailable as exc:
                        failure = {
                            **exc.as_dict(),
                            "embedded_pdf_source": member_name,
                        }
                        failures.append(failure)
                        await self._log(
                            "parsing",
                            "warn",
                            "埋め込み PDF ソース候補を利用できません(次候補へフォールバック)",
                            detail=failure,
                        )
                        continue
                    promoted.diagnostics = [
                        {
                            "kind": "embedded_pdf",
                            "embedded_pdf_source": member_name,
                            "embedded_pdf_sha256": hashlib.sha256(member_bytes).hexdigest(),
                        }
                    ]
                    if promoted.report.accepted:
                        return promoted, failures
                    failure = {
                        "format": promoted.source_format,
                        **promoted.report.as_dict(),
                        "embedded_pdf_source": member_name,
                    }
                    failures.append(failure)
                    await self._log(
                        "parsing",
                        "warn",
                        "埋め込み PDF ソース候補は不完全です(次候補へフォールバック)",
                        detail=failure,
                    )
                    continue

                if candidate.report.accepted:
                    return candidate, failures
                failure = {"format": candidate.source_format, **candidate.report.as_dict()}
                failures.append(failure)
                await self._log(
                    "parsing",
                    "warn",
                    f"{candidate.source_format} ソース候補は不完全です(次候補へフォールバック)",
                    detail=failure,
                )
        finally:
            if owns_http:
                await http.aclose()

        diagnostics = json.dumps({"candidates": failures}, ensure_ascii=False, sort_keys=True)
        retryable = next(
            (
                str(failure.get("code"))
                for failure in failures
                if failure.get("code") in _RETRYABLE_CANDIDATE_CODES
            ),
            None,
        )
        raise FetchError(retryable or "document_incomplete", diagnostics)

    async def _adopt_source_candidate(
        self, candidate: SourceCandidate, failures: list[dict[str, Any]]
    ) -> str | None:
        assert self.paper_id is not None and self.ref is not None
        self._set_candidate_state(candidate, failures)
        embedded_pdf = _embedded_pdf_identity(self._candidate_diagnostics)

        key: str | None = None
        kind: str | None = None
        content_type = "application/octet-stream"
        source_url = ""
        retained_bytes = candidate.source_bytes
        if candidate.source_format == "latex" or embedded_pdf is not None:
            if embedded_pdf is not None:
                if self._latex_archive_bytes is None:
                    raise FetchError("parse_error", "embedded source container is unavailable")
                retained_bytes = self._latex_archive_bytes
            key = StorageKeys.latex_tar(self.paper_id, self.source_version)
            kind = "arxiv_latex"
            content_type = "application/gzip"
            source_url = eprint_url(self.ref, self.deps.settings.alinea_arxiv_base_url or None)
        elif candidate.source_format == "arxiv_html":
            key = StorageKeys.arxiv_html(self.paper_id, self.source_version)
            kind = "arxiv_html"
            content_type = "text/html; charset=utf-8"
            source_url = f"{_www_base(self.deps.settings)}/html/{self.ref.versioned}"
        else:
            key = StorageKeys.original_pdf(self.paper_id, self.source_version)

        assert key is not None
        self._candidate_storage_key = key
        self._candidate_sha256 = hashlib.sha256(retained_bytes).hexdigest()
        existing = await self._find_revision()
        existing_revision_id: str | None = None
        if existing is not None:
            self._validate_revision_candidate_provenance(existing)
            existing_revision_id = str(existing.id)

        if kind is not None:
            try:
                await self.deps.s3.put(
                    self.deps.s3.sources_bucket,
                    key,
                    retained_bytes,
                    content_type=content_type,
                )
                await self._record_source_asset(
                    kind,
                    key,
                    content_type=content_type.split(";", 1)[0],
                    byte_size=len(retained_bytes),
                    source_url=source_url,
                    sha256=hashlib.sha256(retained_bytes).hexdigest(),
                )
            except Exception as exc:
                await self.session.rollback()
                raise FetchError(
                    "storage_error",
                    f"candidate source retention failed: format={candidate.source_format}",
                ) from exc

        await self._log(
            "fetching",
            "info",
            joblog.fetch_timeline_message(candidate.source_format),
            detail={"format": candidate.source_format, "bytes": len(candidate.source_bytes)},
            timeline=True,
        )
        return existing_revision_id

    async def _record_source_asset(
        self,
        kind: str,
        key: str,
        *,
        content_type: str,
        byte_size: int,
        source_url: str,
        sha256: str | None = None,
    ) -> None:
        asset = (
            (
                await self.session.execute(
                    select(SourceAsset)
                    .where(
                        SourceAsset.paper_id == self.paper_id,
                        SourceAsset.source_version == self.source_version,
                        SourceAsset.kind == kind,
                    )
                    .order_by(SourceAsset.storage_key.asc(), SourceAsset.id.asc())
                )
            )
            .scalars()
            .first()
        )
        if asset is None:
            asset = SourceAsset(
                paper_id=self.paper_id,
                kind=kind,
                source_url=source_url,
                source_version=self.source_version,
                storage_key=key,
                content_type=content_type,
                byte_size=byte_size,
                sha256=sha256,
            )
            self.session.add(asset)
        else:
            asset.source_url = source_url
            asset.storage_key = key
            asset.content_type = content_type
            asset.byte_size = byte_size
            asset.sha256 = sha256
        await self.session.commit()

    # -- parsing + structuring -------------------------------------------

    async def _find_revision(self, *, parser_version: str | None = None) -> DocumentRevision | None:
        stmt = select(DocumentRevision).where(
            DocumentRevision.paper_id == self.paper_id,
            DocumentRevision.source_version == self.source_version,
            DocumentRevision.parser_version == (parser_version or self.parser_version),
        )
        return (await self.session.execute(stmt)).scalars().first()

    def _restore_revision(self, revision: DocumentRevision) -> None:
        self.source_format = revision.source_format
        self.revision_id = str(revision.id)
        try:
            self.content = DocumentContent.model_validate(revision.content)
        except (TypeError, ValueError) as exc:
            raise FetchError(
                "parse_error", f"stored revision content is invalid: revision_id={revision.id}"
            ) from exc

    async def _checkpoint_revision(
        self,
    ) -> tuple[DocumentRevision, str | None] | None:
        if "structuring" not in self.ckpt:
            return None
        checkpoint = self.ckpt["structuring"]
        if not isinstance(checkpoint, dict):
            raise FetchError("parse_error", "structuring checkpoint is not an object")
        revision_id = checkpoint.get("revision_id")
        if not isinstance(revision_id, str) or not revision_id:
            raise FetchError("parse_error", "structuring checkpoint revision identity is invalid")
        revision = await self.session.get(DocumentRevision, str(revision_id))
        if (
            revision is None
            or str(revision.paper_id) != self.paper_id
            or revision.source_version != self.source_version
        ):
            raise FetchError("parse_error", "structuring checkpoint revision is unavailable")
        adopt_from = checkpoint.get("adopt_from_revision_id")
        if adopt_from is not None and not isinstance(adopt_from, str):
            raise FetchError("parse_error", "structuring checkpoint adoption identity is invalid")
        return revision, (str(adopt_from) if adopt_from else None)

    async def _legacy_source_manifest(self, revision: DocumentRevision) -> dict[str, Any]:
        if revision.source_format != "latex" or self.paper_id is None:
            return {}
        try:
            raw = await self._load_retained_source_bytes(
                kind="arxiv_latex",
                canonical_key=StorageKeys.latex_tar(self.paper_id, self.source_version),
                source_format="latex",
            )
            _candidate, binary_files, _main_tex = parse_latex_candidate(raw, pdf_text="")
        except (CandidateUnavailable, FetchError):
            return {}
        return {"binary_files": sorted(binary_files)}

    async def _ensure_revision_diagnostics(self, revision: DocumentRevision) -> None:
        stats = dict(revision.stats or {})
        stored_completeness = stats.get("completeness")
        if isinstance(stored_completeness, dict) and isinstance(
            stored_completeness.get("accepted"), bool
        ):
            completeness = dict(stored_completeness)
        else:
            assert self.content is not None
            report = assess_document_completeness(
                self.content,
                pdf_text=self._pdf_text_for_completeness(),
                source_manifest=await self._legacy_source_manifest(revision),
            )
            completeness = report.as_dict()
        stored_failures = stats.get("candidate_failures")
        if isinstance(stored_failures, list):
            failures = [dict(item) for item in stored_failures if isinstance(item, dict)]
        elif self._candidate_completeness is not None:
            failures = [dict(item) for item in self._candidate_failures]
        elif self.is_pdf_upload:
            failures = []
        else:
            failures = [dict(item) for item in _HISTORICAL_CANDIDATE_FAILURES]
        stats["candidate_failures"] = failures
        stats["completeness"] = completeness
        revision.stats = stats
        await self.session.commit()
        if not completeness.get("accepted"):
            raise FetchError(
                "document_incomplete",
                json.dumps(
                    {
                        "revision_id": str(revision.id),
                        "format": revision.source_format,
                        "completeness": completeness,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )

    async def _finalize_revision(
        self, revision: DocumentRevision, *, adopt_from_revision_id: str | None
    ) -> None:
        self._validate_revision_candidate_provenance(revision)
        self._restore_revision(revision)
        await self._ensure_revision_diagnostics(revision)
        paper = await self._get_paper()
        paper.latest_revision_id = revision.id
        await self.session.commit()
        if self.payload.adopt_on_complete and adopt_from_revision_id is not None:
            await self._reanchor_after_adopt(adopt_from_revision_id)
        await self.store.checkpoint(
            self.job_id,
            "structuring",
            {
                "revision_id": self.revision_id,
                "adopt_from_revision_id": adopt_from_revision_id,
            },
            progress=35,
        )

    async def _restore_structuring_checkpoint_candidate(self, revision: DocumentRevision) -> None:
        assert self.paper_id is not None
        stats = revision.stats if isinstance(revision.stats, dict) else {}
        revision_has_embedded_provenance = any(
            key in stats for key in _EMBEDDED_PDF_PROVENANCE_KEYS
        )
        raw_parse_ck = self.ckpt.get("parsing")
        if raw_parse_ck is None:
            if revision_has_embedded_provenance:
                raise FetchError(
                    "parse_error", "embedded revision parsing checkpoint is unavailable"
                )
            return
        if not isinstance(raw_parse_ck, dict):
            if revision_has_embedded_provenance:
                raise FetchError("parse_error", "parsing checkpoint is not an object")
            return
        raw_diagnostics = raw_parse_ck.get("candidate_diagnostics", [])
        checkpoint_has_embedded_provenance = isinstance(raw_diagnostics, list) and any(
            isinstance(item, dict)
            and (
                item.get("kind") == "embedded_pdf"
                or "embedded_pdf_source" in item
                or "embedded_pdf_sha256" in item
            )
            for item in raw_diagnostics
        )
        checkpoint_has_embedded_provenance = checkpoint_has_embedded_provenance or (
            raw_parse_ck.get("source_format") == "pdf"
            and raw_parse_ck.get("source_storage_key")
            == StorageKeys.latex_tar(self.paper_id, self.source_version)
        )
        if not revision_has_embedded_provenance and not checkpoint_has_embedded_provenance:
            return

        checkpoint_format = raw_parse_ck.get("source_format")
        checkpoint_parser = raw_parse_ck.get("parser_version")
        if (
            checkpoint_format != revision.source_format
            or checkpoint_parser != revision.parser_version
        ):
            raise FetchError(
                "parse_error", "parsing checkpoint source does not match revision identity"
            )
        self.source_format = str(checkpoint_format)
        self._candidate_provenance_validation_required = True
        self._candidate_failures = self._checkpoint_candidate_failures(raw_parse_ck)
        self._candidate_diagnostics = self._checkpoint_candidate_diagnostics(raw_parse_ck)
        if _embedded_pdf_identity(self._candidate_diagnostics) is None:
            raise FetchError("parse_error", "embedded parsing checkpoint identity is incomplete")
        await self._load_checkpoint_candidate(raw_parse_ck)

    async def _stage_parse_and_structure(self) -> None:
        checkpoint_revision = await self._checkpoint_revision()
        if checkpoint_revision is not None:
            revision, adopt_from_revision_id = checkpoint_revision
            await self._restore_structuring_checkpoint_candidate(revision)
            await self._finalize_revision(revision, adopt_from_revision_id=adopt_from_revision_id)
            return
        checkpoint_candidate: SourceCandidate | None = None
        parse_ck: dict[str, Any] = {}
        if self.is_pdf_upload:
            self._candidate_provenance_validation_required = True
            raw_parse_ck = self.ckpt.get("parsing")
            if raw_parse_ck is not None and not isinstance(raw_parse_ck, dict):
                raise FetchError("parse_error", "parsing checkpoint is not an object")
            parse_ck = raw_parse_ck if isinstance(raw_parse_ck, dict) else {}
            if parse_ck:
                checkpoint_parser = parse_ck.get("parser_version")
                if (
                    parse_ck.get("source_format") != "pdf_upload"
                    or not isinstance(checkpoint_parser, str)
                    or not checkpoint_parser
                ):
                    raise FetchError("parse_error", "parsing checkpoint identity is invalid")
                checkpoint_candidate = await self._load_checkpoint_candidate(parse_ck)
                existing = await self._find_revision(parser_version=checkpoint_parser)
            else:
                existing = await self._find_revision()
            if existing is not None:
                if existing.source_format != "pdf":
                    raise FetchError(
                        "parse_error", "parsing checkpoint source format does not match revision"
                    )
                adopt_from = parse_ck.get("adopt_from_revision_id")
                await self._finalize_revision(
                    existing,
                    adopt_from_revision_id=(str(adopt_from) if adopt_from else None),
                )
                return
        else:
            raw_parse_ck = self.ckpt.get("parsing")
            if raw_parse_ck is not None:
                if not isinstance(raw_parse_ck, dict):
                    raise FetchError("parse_error", "parsing checkpoint is not an object")
                parse_ck = raw_parse_ck
                checkpoint_format = parse_ck.get("source_format")
                checkpoint_parser = parse_ck.get("parser_version")
                if (
                    checkpoint_format not in {"latex", "arxiv_html", "pdf"}
                    or not isinstance(checkpoint_parser, str)
                    or not checkpoint_parser
                ):
                    raise FetchError("parse_error", "parsing checkpoint identity is invalid")
                self.source_format = str(checkpoint_format)
                self._candidate_provenance_validation_required = True
                self._candidate_failures = self._checkpoint_candidate_failures(parse_ck)
                self._candidate_diagnostics = self._checkpoint_candidate_diagnostics(parse_ck)
                checkpoint_completeness = parse_ck.get("completeness")
                self._candidate_completeness = (
                    dict(checkpoint_completeness)
                    if isinstance(checkpoint_completeness, dict)
                    else None
                )
                existing = await self._find_revision(parser_version=checkpoint_parser)
                if existing is not None:
                    if existing.source_format != checkpoint_format:
                        raise FetchError(
                            "parse_error",
                            "parsing checkpoint source format does not match revision",
                        )
                    embedded_pdf = _embedded_pdf_identity(self._candidate_diagnostics)
                    if embedded_pdf is not None:
                        checkpoint_candidate = await self._load_checkpoint_candidate(parse_ck)
                    adopt_from = parse_ck.get("adopt_from_revision_id")
                    await self._finalize_revision(
                        existing,
                        adopt_from_revision_id=(str(adopt_from) if adopt_from else None),
                    )
                    return
                checkpoint_candidate = await self._load_checkpoint_candidate(parse_ck)

        await self.store.set_progress(self.job_id, 20, stage="parsing")
        await self._publish_stage("parsing", 20)
        assert self.paper_id is not None
        # adopt_on_complete(通知「変更する」経由の reingest)は新リビジョン確定後に旧リビジョンを
        # 追従させる(§4.5)。構造化前に「現在の latest」を旧リビジョンとして確定しておく。
        if "adopt_from_revision_id" in parse_ck:
            checkpoint_adopt_from = parse_ck.get("adopt_from_revision_id")
            old_revision_id = str(checkpoint_adopt_from) if checkpoint_adopt_from else None
        else:
            old_revision_id = str((await self._get_paper()).latest_revision_id or "") or None
        if self.is_pdf_upload:
            upload_candidate = checkpoint_candidate
            if upload_candidate is None:
                data = await self._get_pdf_bytes()
                try:
                    upload_candidate = parse_pdf_candidate(
                        data, pdf_text=self._pdf_text_for_completeness()
                    )
                except CandidateUnavailable as exc:
                    raise FetchError(exc.code, exc.message) from exc
                if not upload_candidate.report.accepted:
                    raise FetchError(
                        "document_incomplete",
                        json.dumps(
                            {
                                "candidates": [
                                    {
                                        "format": upload_candidate.source_format,
                                        **upload_candidate.report.as_dict(),
                                    }
                                ]
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    )
                assert isinstance(upload_candidate.parsed, ParsedPdfDocument)
                self.parsed_pdf = upload_candidate.parsed
                self.content = upload_candidate.content
                self._candidate_failures = []
                self._candidate_completeness = upload_candidate.report.as_dict()
                self._candidate_storage_key = StorageKeys.original_pdf(
                    self.paper_id, self.source_version
                )
                self._candidate_sha256 = hashlib.sha256(upload_candidate.source_bytes).hexdigest()
                await self.store.checkpoint(
                    self.job_id,
                    "parsing",
                    {
                        "source_format": "pdf_upload",
                        "parser_version": self.parser_version,
                        "candidate_failures": [],
                        "completeness": self._candidate_completeness,
                        "adopt_from_revision_id": old_revision_id,
                        "source_storage_key": self._candidate_storage_key,
                        "source_sha256": self._candidate_sha256,
                    },
                    progress=20,
                )
            data = upload_candidate.source_bytes

            await self.store.set_progress(self.job_id, 35, stage="structuring")
            await self._publish_stage("structuring", 35)
            await self._structure_pdf(data)
            assert self.revision_id is not None
            created_revision = await self.session.get(DocumentRevision, self.revision_id)
            assert created_revision is not None
            await self._finalize_revision(created_revision, adopt_from_revision_id=old_revision_id)
            return

        # arXiv: LaTeX → HTML → retained original PDF の順に完全性を評価する。
        candidate = checkpoint_candidate
        if candidate is None:
            candidate, failures = await self._select_source_candidate()
            existing_revision_id = await self._adopt_source_candidate(candidate, failures)
            await self.store.checkpoint(
                self.job_id,
                "parsing",
                {
                    "source_format": self.source_format,
                    "parser_version": self.parser_version,
                    "candidate_failures": self._candidate_failures,
                    "candidate_diagnostics": self._candidate_diagnostics,
                    "completeness": self._candidate_completeness,
                    "adopt_from_revision_id": old_revision_id,
                    "source_storage_key": self._candidate_storage_key,
                    "source_sha256": self._candidate_sha256,
                },
                progress=20,
            )
            if existing_revision_id is not None:
                existing = await self.session.get(DocumentRevision, existing_revision_id)
                if existing is None:
                    raise FetchError("parse_error", "selected existing revision is unavailable")
                await self._finalize_revision(existing, adopt_from_revision_id=old_revision_id)
                return

        # structuring: リビジョン永続化・図保存・検索索引・サムネイル。
        await self.store.set_progress(self.job_id, 35, stage="structuring")
        await self._publish_stage("structuring", 35)
        if candidate.source_format == "pdf":
            await self._structure_pdf(candidate.source_bytes)
        else:
            await self._structure()
        assert self.revision_id is not None
        created_revision = await self.session.get(DocumentRevision, self.revision_id)
        assert created_revision is not None
        await self._finalize_revision(created_revision, adopt_from_revision_id=old_revision_id)

    async def _structure(self) -> None:
        assert self.parsed is not None and self.paper_id is not None
        warnings = list(self.parsed.warnings)
        unresolved = _degrade_unresolved_refs(self.parsed)
        content = self.parsed.to_document_content()
        scope = compute_translation_scope(content)
        stats: dict[str, Any] = {
            "pages": None,  # HTML 経路は PDF 由来のページ数を持たない(§6.10)
            "figures": len(self.parsed.figures),
            "tables": len(self.parsed.tables),
            "blocks": len(self.parsed.blocks),
            "translatable_blocks": len(scope.in_scope_block_ids),
            "candidate_failures": self._candidate_failures,
            "completeness": self._candidate_completeness,
        }
        if self.source_format == "latex":
            stats["latex_source"] = {
                "main_tex": self.latex_main_tex_name,
                "binary_files": sorted(self.latex_binary_files),
                "graphicspaths": list(self.latex_graphicspaths),
                "build_version": "latex-ja-pdf-1.0.0",
            }
        revision = DocumentRevision(
            paper_id=self.paper_id,
            source_version=self.source_version,
            parser_version=self.parser_version,
            quality_level=self.parsed.quality_level,
            source_format=self.parsed.source_format,
            content=content.model_dump(),
            stats=stats,
        )
        self.session.add(revision)
        await self.session.flush()
        self.revision_id = str(revision.id)
        self.content = content

        paper = await self._get_paper()
        uploaded_keys: list[str] = []
        figure_bytes, fig_warnings, figure_failures = await self._save_figures(
            self.revision_id,
            uploaded_keys=uploaded_keys,
        )
        warnings.extend(fig_warnings)
        stats = {**stats, "figure_asset_failures": figure_failures}
        revision.stats = stats
        content = self.parsed.to_document_content()
        revision.content = content.model_dump()
        self.content = content
        await rebuild_block_search_index(self.session, self.revision_id, content)
        warnings.extend(
            await self._make_thumbnail(
                paper,
                figure_bytes,
                self.parsed.figures,
                uploaded_keys=uploaded_keys,
            )
        )
        await _commit_with_asset_cleanup(self.session, self.deps.s3, uploaded_keys)

        for warning in warnings:
            await self._log("structuring", "warn", warning)
        if unresolved:
            await self._log(
                "structuring",
                "warn",
                f"未解決の相互参照を原文テキストに縮退({unresolved} 件)",
                detail={"unresolved_refs": unresolved},
            )
        await self._log(
            "structuring",
            "info",
            joblog.structuring_timeline_message(stats),
            detail={"stats": stats},
            timeline=True,
        )

    async def _save_figures(
        self,
        revision_id: str,
        *,
        uploaded_keys: list[str] | None = None,
    ) -> tuple[dict[str, bytes], list[str], list[dict[str, str]]]:
        """図アセットを S3 に保存する(best-effort。失敗は warn で続行。§2.4)。"""
        out: dict[str, bytes] = {}
        warnings: list[str] = []
        failures: list[dict[str, str]] = []
        materialized_bytes = 0
        if self.parsed is None or self.paper_id is None:
            return out, warnings, failures
        for figure_index, fig in enumerate(self.parsed.figures):
            inline_raw = fig.raw
            if self.source_format == "arxiv_html":
                # Author-controlled HTML is never retained in a new revision.
                fig.raw = None
            try:
                if figure_index >= MAX_FIGURES_PER_DOCUMENT:
                    raise FigureAssetError("figure_limit_exceeded", "document has too many figures")
                materialized_budget = MAX_TOTAL_FIGURE_MATERIALIZED_BYTES - materialized_bytes
                if materialized_budget <= 0:
                    raise FigureAssetError(
                        "figure_bytes_exceeded",
                        "document figure bytes exceed the aggregate safe limit",
                    )
                if fig.asset_key is None or not fig.asset_key.strip():
                    if (
                        self.source_format == "arxiv_html"
                        and isinstance(inline_raw, str)
                        and bool(inline_raw.strip())
                    ):
                        payload = await _materialize_inline_svg(
                            inline_raw,
                            materialized_budget=materialized_budget,
                        )
                    else:
                        raise FigureAssetError(
                            "missing_asset_key", "figure has no materializable asset"
                        )
                elif self.source_format == "latex":
                    resolved = resolve_latex_source(
                        binary_files=self.latex_binary_files,
                        requested=fig.asset_key,
                        main_tex_name=self.latex_main_tex_name,
                        graphicspaths=self.latex_graphicspaths,
                    )
                    payload = await _materialize_figure_payload(
                        resolved.content,
                        resolved.source_name,
                        materialized_budget=materialized_budget,
                    )
                else:
                    if self.deps.http is None or self.ref is None:
                        raise FigureAssetError(
                            "source_not_found", "HTML figure source is not available"
                        )
                    await self._throttle()

                    async def load_with_budget(
                        data: bytes,
                        source_name: str,
                        content_type: str | None,
                        budget: int = materialized_budget,
                    ) -> FigureAssetPayload:
                        return await _materialize_figure_payload(
                            data,
                            source_name,
                            content_type,
                            materialized_budget=budget,
                        )

                    payload = await fetch_html_asset(
                        self.deps.http,
                        base=_www_base(self.deps.settings),
                        versioned=self.ref.versioned,
                        source=fig.asset_key,
                        payload_loader=load_with_budget,
                    )
                retained_bytes = payload.source_size or len(payload.content)
                next_materialized_bytes = materialized_bytes + retained_bytes + len(payload.content)
                if next_materialized_bytes > MAX_TOTAL_FIGURE_MATERIALIZED_BYTES:
                    raise FigureAssetError(
                        "figure_bytes_exceeded",
                        "document figure bytes exceed the aggregate safe limit",
                    )
                key = StorageKeys.figure(self.paper_id, revision_id, fig.id, payload.ext)
                await self.deps.s3.put(
                    self.deps.s3.assets_bucket,
                    key,
                    payload.content,
                    content_type=payload.content_type,
                )
                if uploaded_keys is not None:
                    uploaded_keys.append(key)
                fig.asset_key = key
                out[fig.id] = payload.content
                materialized_bytes = next_materialized_bytes
            except FigureAssetError as exc:
                fig.asset_key = None
                failures.append(
                    {
                        "code": exc.code,
                        "figure_id": fig.id,
                        "source": self.source_format,
                    }
                )
                warnings.append(f"図の保存に失敗(続行): {fig.id} [{exc.code}]")
            except Exception as exc:
                fig.asset_key = None
                failures.append(
                    {
                        "code": "figure_asset_error",
                        "figure_id": fig.id,
                        "source": self.source_format,
                    }
                )
                warnings.append(
                    f"図の保存に失敗(続行): {fig.id} [figure_asset_error: {type(exc).__name__}]"
                )
        return out, warnings, failures

    async def _make_thumbnail(
        self,
        paper: Paper,
        figure_bytes: dict[str, bytes],
        figures: list[Block],
        *,
        uploaded_keys: list[str] | None = None,
    ) -> list[str]:
        if self.paper_id is None:
            return []
        selected = select_thumbnail_figure(figures)
        if selected is None or selected.id not in figure_bytes:
            return []  # 図なし → thumbnail_key は NULL のまま(§8 ③④)
        try:
            card, card_2x = render_thumbnail(figure_bytes[selected.id])
        except (OSError, ValueError) as exc:
            return [f"サムネイル生成に失敗(続行): {exc}"]
        new_thumbnail = paper.thumbnail_key is None
        thumbnail_key = StorageKeys.thumbnail(self.paper_id)
        retina_key = StorageKeys.thumbnail(self.paper_id, retina=True)
        await self.deps.s3.put(
            self.deps.s3.assets_bucket,
            thumbnail_key,
            card,
            content_type="image/webp",
        )
        if new_thumbnail and uploaded_keys is not None:
            uploaded_keys.append(thumbnail_key)
        await self.deps.s3.put(
            self.deps.s3.assets_bucket,
            retina_key,
            card_2x,
            content_type="image/webp",
        )
        if new_thumbnail and uploaded_keys is not None:
            uploaded_keys.append(retina_key)
        paper.thumbnail_key = thumbnail_key
        return []

    # -- structuring (PDF 候補 / pdf_upload。品質 B。plans/05 §6・§9.2) ---

    async def _structure_pdf(self, data: bytes) -> None:
        """PDF の structuring 段: リビジョン永続化・図表資産・索引・サムネ。

        pdf_parser が既に図表を切り出し済み(HTTP 再取得不要)。書誌推定は upload のみ。
        """
        assert self.parsed_pdf is not None and self.paper_id is not None
        warnings = list(self.parsed_pdf.warnings)
        content = self.parsed_pdf.to_document_content()
        scope = compute_translation_scope(content)
        stats: dict[str, Any] = dict(self.parsed_pdf.stats)
        stats["translatable_blocks"] = len(scope.in_scope_block_ids)
        stats["candidate_failures"] = self._candidate_failures
        stats["completeness"] = self._candidate_completeness
        embedded_pdf_provenance = self._selected_embedded_pdf_provenance()
        if embedded_pdf_provenance is not None:
            stats.update(embedded_pdf_provenance)

        revision = DocumentRevision(
            paper_id=self.paper_id,
            source_version=self.source_version,
            parser_version=self.parser_version,
            quality_level=self.parsed_pdf.quality_level,
            source_format=self.parsed_pdf.source_format,
            content=content.model_dump(),
            stats=stats,
        )
        self.session.add(revision)
        await self.session.flush()
        self.revision_id = str(revision.id)

        # 図・表・数式の切り出し画像は既にパーサが切り出し済み(HTTP 再取得不要)。
        # S3 保存後に block.asset_key を確定してから再シリアライズする(§6.6.3)。
        uploaded_keys: list[str] = []
        figure_bytes, fig_warnings, figure_failures = await self._save_pdf_assets(
            self.revision_id,
            uploaded_keys=uploaded_keys,
        )
        warnings.extend(fig_warnings)
        stats = {**stats, "figure_asset_failures": figure_failures}
        revision.stats = stats
        revision.content = content.model_dump()
        self.content = content

        paper = await self._get_paper()
        abstract_text = _extract_pdf_abstract(content)
        if abstract_text and not paper.abstract:
            paper.abstract = abstract_text
        if self.is_pdf_upload:
            await self._apply_bib_estimate(paper, data)

        await rebuild_block_search_index(self.session, self.revision_id, content)
        warnings.extend(
            await self._make_thumbnail(
                paper,
                figure_bytes,
                self.parsed_pdf.figures,
                uploaded_keys=uploaded_keys,
            )
        )
        await _commit_with_asset_cleanup(self.session, self.deps.s3, uploaded_keys)

        for warning in warnings:
            await self._log("structuring", "warn", warning)
        await self._log(
            "structuring",
            "info",
            joblog.structuring_timeline_message(stats),
            detail={"stats": stats},
            timeline=True,
        )

    async def _save_pdf_assets(
        self,
        revision_id: str,
        *,
        uploaded_keys: list[str] | None = None,
    ) -> tuple[dict[str, bytes], list[str], list[dict[str, str]]]:
        """図・表・数式の切り出し PNG を S3 へ保存し block.asset_key を確定する(§6.6.3)。"""
        output: dict[str, bytes] = {}
        warnings: list[str] = []
        failures: list[dict[str, str]] = []
        materialized_bytes = 0
        if self.parsed_pdf is None or self.paper_id is None:
            return output, warnings, failures
        blocks_by_id = {b.id: b for b in self.parsed_pdf.blocks}
        for figure_index, (block_id, png) in enumerate(self.parsed_pdf.figure_images.items()):
            block = blocks_by_id.get(block_id)
            if block is None:
                continue
            try:
                if figure_index >= MAX_FIGURES_PER_DOCUMENT:
                    raise FigureAssetError("figure_limit_exceeded", "document has too many figures")
                next_materialized_bytes = materialized_bytes + len(png) * 2
                if next_materialized_bytes > MAX_TOTAL_FIGURE_MATERIALIZED_BYTES:
                    raise FigureAssetError(
                        "figure_bytes_exceeded",
                        "document figure bytes exceed the aggregate safe limit",
                    )
                key = StorageKeys.figure(self.paper_id, revision_id, block_id, "png")
                await self.deps.s3.put(
                    self.deps.s3.assets_bucket, key, png, content_type="image/png"
                )
                if uploaded_keys is not None:
                    uploaded_keys.append(key)
                block.asset_key = key
                output[block_id] = png
                materialized_bytes = next_materialized_bytes
            except FigureAssetError as exc:
                block.asset_key = None
                failures.append({"code": exc.code, "figure_id": block_id, "source": "pdf"})
                warnings.append(f"図/表アセットの保存に失敗(続行): {block_id} [{exc.code}]")
            except Exception as exc:
                block.asset_key = None
                failures.append(
                    {"code": "figure_asset_error", "figure_id": block_id, "source": "pdf"}
                )
                warnings.append(f"図/表アセットの保存に失敗(続行): {block_id} — {exc}")
        return output, warnings, failures

    async def _apply_bib_estimate(self, paper: Paper, data: bytes) -> None:
        """アップロード PDF の書誌推定で papers を補完する(§9.3)。

        Crossref で DOI 直一致が取れた場合のみタイトルを上書きする(拡張から渡された
        ``title_guess`` を粗いフォントヒューリスティクスで劣化させないため)。
        著者・DOI・出版日・掲載誌は元々空のため常に補完する。
        """
        try:
            estimate = await estimate_bibliography(data)
        except Exception as exc:
            await self._log(
                "structuring", "warn", "書誌推定に失敗(続行)", detail={"error": str(exc)}
            )
            return
        if not estimate.bib_estimated and estimate.title:
            paper.title = estimate.title
        if estimate.authors:
            paper.authors = estimate.authors
        if estimate.doi:
            paper.doi = estimate.doi
        if estimate.arxiv_id and not paper.arxiv_id:
            paper.arxiv_id = estimate.arxiv_id
        if estimate.published_on:
            paper.published_on = _to_date(estimate.published_on)
        if estimate.venue:
            paper.venue = estimate.venue
        paper.bib_estimated = estimate.bib_estimated

    # -- translating_abstract --------------------------------------------

    async def _stage_translating_abstract(self) -> None:
        settings = await self._load_user_settings()
        paper = await self._get_paper()
        self.style = self._resolve_style(paper.visibility, settings)
        # 冪等: 共有資産(abstract_ja + summary_lines)が既にあれば再生成しない(§2.3)。
        if paper.abstract_ja and paper.summary_lines is not None:
            return

        await self.store.set_progress(self.job_id, 50, stage="translating_abstract")
        await self._publish_stage("translating_abstract", 50)
        paper = await self._get_paper()
        if paper.abstract:
            unit = await translate_block(
                {
                    "id": f"abstract-{paper.id}",
                    "type": "paragraph",
                    "inlines": [{"t": "text", "v": paper.abstract}],
                },
                self.deps.router,
                ctx=TranslationContext(style=self.style, task="translation"),
                user_id=self.user_id,
                library_item_id=self.library_item_id,
                job_id=self.job_id,
            )
            if unit.text_ja:
                paper.abstract_ja = unit.text_ja

        await self._generate_summary(paper)
        await self.session.commit()
        await self.store.checkpoint(self.job_id, "translating_abstract", {}, progress=50)

    async def _generate_summary(self, paper: Paper) -> None:
        from alinea_llm.types import ContentPart, JsonSchemaSpec, LLMRequest, Message

        material = self._summary_material(paper)
        req = LLMRequest(
            model="",
            system=[ContentPart(type="text", text=SUMMARY_SYSTEM_PROMPT, cache_hint=True)],
            messages=[Message(role="user", parts=[ContentPart(type="text", text=material)])],
            max_output_tokens=2048,
            effort="low",
            json_schema=JsonSchemaSpec(name=SUMMARY_SCHEMA_NAME, json_schema=SUMMARY_JSON_SCHEMA),
            metadata={"task": "summary"},
        )
        try:
            resp = await self.deps.router.complete(
                "summary",
                request=req,
                mode="structured",
                user_id=self.user_id,
                library_item_id=self.library_item_id,
                job_id=self.job_id,
            )
        except Exception as exc:  # 部分成功(要約なしで続行。§3.1)
            await self._log(
                "translating_abstract",
                "warn",
                "要約生成に失敗(続行)",
                detail={"event": "summary_failed", "reason": str(exc)},
            )
            return

        data = resp.parsed or {}
        lines = data.get("summary_lines")
        llm_tags = [str(t) for t in (data.get("suggested_tags") or [])]
        if lines and _summary_numbers_ok(lines, material):
            paper.summary_lines = [str(x) for x in lines]
        else:
            await self._log(
                "translating_abstract",
                "warn",
                "要約の数値検証に失敗(要約なしで続行)",
                detail={"event": "summary_failed", "reason": "number_mismatch"},
            )
        await self._apply_suggested_tags(paper, llm_tags)

    def _summary_material(self, paper: Paper) -> str:
        parts = [f"タイトル: {paper.title}", f"アブストラクト: {paper.abstract}"]
        if self.content is not None and self.content.sections:
            first = self.content.sections[0]
            intro = " ".join(block_to_plain(b) for b in first.blocks if b.type == "paragraph")
            if intro:
                parts.append(f"導入: {intro[:2000]}")
        return "\n".join(parts)

    async def _apply_suggested_tags(self, paper: Paper, llm_tags: list[str]) -> None:
        library_item = await self._get_library_item()
        if library_item is None:
            return
        cooccur = await self._cooccurring_tags(paper, library_item)
        merged: list[str] = []
        for tag in [*paper.arxiv_categories, *cooccur, *llm_tags]:
            if tag and tag not in merged:
                merged.append(tag)
            if len(merged) >= _MAX_SUGGESTED_TAGS:
                break
        library_item.suggested_tags = merged

    async def _cooccurring_tags(self, paper: Paper, library_item: LibraryItem) -> list[str]:
        """arXiv カテゴリを共有する同一ユーザーの library_items の確定タグ上位 2 件(§11.1)。"""
        if not paper.arxiv_categories:
            return []
        stmt = (
            select(LibraryItem.tags)
            .join(Paper, Paper.id == LibraryItem.paper_id)
            .where(
                LibraryItem.user_id == library_item.user_id,
                LibraryItem.id != library_item.id,
                Paper.arxiv_categories.overlap(paper.arxiv_categories),
            )
        )
        counts: dict[str, int] = {}
        for row in (await self.session.execute(stmt)).scalars():
            for tag in row or []:
                counts[tag] = counts.get(tag, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [tag for tag, _ in ranked[:2]]

    # -- translation set --------------------------------------------------

    async def _ensure_translation_set(self) -> None:
        assert self.revision_id is not None
        rev_id = self.revision_id
        paper = await self._get_paper()
        if paper.visibility == "public":
            existing = await find_shared_set(self.session, rev_id, self.style)
            if existing is not None:
                self.set_id = str(existing.id)
                return
            snapshot, _ = await build_snapshot(
                self.session,
                user_id=self.user_id,
                library_item_id=self.library_item_id,
                shared=True,
            )
            tset = TranslationSet(
                revision_id=rev_id,
                style=self.style,
                scope="shared",
                glossary_snapshot=snapshot,
                status="pending",
            )
            self.session.add(tset)
            try:
                await self.session.commit()
            except IntegrityError:
                await self.session.rollback()
                existing = await find_shared_set(self.session, rev_id, self.style)
                self.set_id = str(existing.id) if existing is not None else None
            else:
                self.set_id = str(tset.id)
            return

        existing_personal = (
            (
                await self.session.execute(
                    select(TranslationSet).where(
                        TranslationSet.revision_id == rev_id,
                        TranslationSet.style == self.style,
                        TranslationSet.scope == "personal",
                        TranslationSet.user_id == self.user_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing_personal is not None:
            self.set_id = str(existing_personal.id)
            return
        snapshot, _ = await build_snapshot(
            self.session, user_id=self.user_id, library_item_id=self.library_item_id, shared=False
        )
        tset = TranslationSet(
            revision_id=rev_id,
            style=self.style,
            scope="personal",
            user_id=self.user_id,
            glossary_snapshot=snapshot,
            status="pending",
        )
        self.session.add(tset)
        await self.session.commit()
        self.set_id = str(tset.id)

    # -- readable ---------------------------------------------------------

    async def _stage_readable(self) -> None:
        assert self.content is not None and self.set_id is not None
        first = progress.first_translatable_section(self.content)
        await self.store.set_progress(self.job_id, 55, stage="readable")
        await self._publish_stage("readable", 55)
        if first is not None:
            # 第 1 本文セクションを ingest ジョブ内で直接翻訳(§2.1。冪等 UPSERT)。
            await translate_section(
                self.session,
                self.set_id,
                first,
                self.deps.router,
                reason="initial",
                user_id=self.user_id,
                library_item_id=self.library_item_id,
                job_id=self.job_id,
                job_store=None,
                publish=self.deps.publish,
            )
        await self.store.checkpoint(self.job_id, "readable", {"section_id": first}, progress=55)

    # -- translating_body -------------------------------------------------

    async def _stage_translating_body(self) -> None:
        assert self.content is not None and self.set_id is not None
        settings = await self._load_user_settings()
        scope = compute_translation_scope(self.content)
        first = progress.first_translatable_section(self.content)
        section_block_map = {s["section_id"]: s["block_ids"] for s in scope.sections}
        body_section_ids = [sid for sid in section_block_map if sid != first]

        await self.store.set_progress(self.job_id, 55, stage="translating_body")
        await self._publish_stage("translating_body", 55)

        # クォータ確認(翻訳段のみ停止。§2.6)。
        if body_section_ids and await self._is_over_quota():
            await self.store.mark_waiting_quota(self.job_id)
            await self._publish_stage("translating_body", 55, status="waiting_quota")
            await self._log(
                "translating_body",
                "warn",
                "月次クォータ超過のため翻訳を保留(BYOK 登録で自動再開)",
                detail={"status": "waiting_quota"},
            )
            return

        appendix_untranslated = bool(scope.appendix_section_ids) and not (
            settings.auto_translate_appendix
        )
        enqueued = await self._enqueue_body_jobs(
            body_section_ids, section_block_map, appendix_untranslated=appendix_untranslated
        )

        if self.deps.arq_pool is not None:
            for jid in enqueued:
                await self.deps.arq_pool.enqueue_job("run_job", jid, _queue_name="alinea:bulk")
            # 本文ジョブ 0 件(§2.1)、または全件が冪等キー経由で既存の完了済みジョブを指す
            # (reingest 等で translation_set を再利用した場合。§11.3)ときはその場で確定する。
            # finalize_ingest_if_body_complete は残件数(queued/running/waiting_quota)を
            # 自前で数えるため、genuinely 新規かつ未完了のジョブがある通常経路では no-op になる
            # (remaining > 0 → status='partial' のみ設定して抜ける)ので常時呼んで安全。
            await self._finalize(settings, scope.appendix_section_ids)
            return

        # arq プール無し(テスト/単純デプロイ): 本文ジョブをその場で駆動して完了確定。
        await self._drain_body_jobs(enqueued)
        await self._finalize(settings, scope.appendix_section_ids)

    async def _enqueue_body_jobs(
        self,
        body_section_ids: list[str],
        section_block_map: dict[str, list[str]],
        *,
        appendix_untranslated: bool,
    ) -> list[str]:
        assert self.set_id is not None
        enqueued: list[str] = []
        for sid in body_section_ids:
            job_id = await self.store.enqueue(
                kind="translation",
                payload={
                    "set_id": self.set_id,
                    "section_id": sid,
                    "block_ids": section_block_map.get(sid),
                    "reason": "initial",
                    # arq 経路の完了確定(§11.3)用文脈。最後の翻訳ジョブが
                    # finalize_ingest_if_body_complete を呼んで親を complete にする。
                    "ingest_job_id": self.job_id,
                    "source_version": self.source_version,
                    "appendix_untranslated": appendix_untranslated,
                },
                idempotency_key=f"tr:{self.set_id}:{sid}:initial",
                priority="bulk",
                user_id=self.user_id,
                paper_id=self.paper_id,
                library_item_id=self.library_item_id,
            )
            enqueued.append(job_id)
        return enqueued

    async def _drain_body_jobs(self, job_ids: list[str]) -> None:
        assert self.set_id is not None
        for jid in job_ids:
            claimed = await self.store.claim(jid)
            if claimed is None:
                continue  # 先着処理済み(冪等)。
            payload = dict(claimed.payload or {})
            section_id = str(payload["section_id"])
            block_ids = payload.get("block_ids")
            result = await translate_section(
                self.session,
                self.set_id,
                section_id,
                self.deps.router,
                block_ids=block_ids,
                reason="initial",
                user_id=self.user_id,
                library_item_id=self.library_item_id,
                job_id=jid,
                job_store=self.store,
                publish=self.deps.publish,
            )
            await self.store.succeed(jid, {"section_id": result.section_id})

    async def _finalize(self, settings: TranslationSettings, appendix_ids: list[str]) -> None:
        assert self.content is not None and self.set_id is not None
        appendix_untranslated = bool(appendix_ids) and not settings.auto_translate_appendix
        completed = await progress.finalize_ingest_if_body_complete(
            self.session,
            set_id=self.set_id,
            ingest_job_id=self.job_id,
            content=self.content,
            style=self.style,
            source_version=self.source_version,
            appendix_untranslated=appendix_untranslated,
        )
        if completed:
            await self._build_latex_translation_pdf()
            # 完了ナッジ(§21.2)。job_events は job_id 一致のイベントを受けて DB の
            # 最終状態(succeeded)を再確認し done フレームを組む(routers/jobs.py 参照)ため、
            # ここでの data 自体は any でよいが InfoPanel の onProgress と同形に揃える。
            await self._publish_stage("complete", 100, status="succeeded")
            await self._fire_translation_complete()

    async def _build_latex_translation_pdf(self) -> None:
        assert self.set_id is not None
        try:
            outcome = await build_latex_translation_pdfs_if_ready(
                self.session,
                self.deps.s3,
                self.deps.settings,
                set_id=self.set_id,
            )
        except LatexPdfBuildError as exc:
            await self._log(
                "translating_body",
                "warn",
                "日本語PDFのビルドに失敗(原文/訳文ビューは利用可能)",
                detail={"code": exc.kind, **exc.detail},
            )
            return
        if not outcome.built:
            if outcome.skipped_reason not in {"not_latex", "not_shared", "already_built"}:
                await self._log(
                    "translating_body",
                    "warn",
                    "日本語PDFのビルドをスキップ",
                    detail={"reason": outcome.skipped_reason},
                )
            return
        for warning in outcome.warnings:
            await self._log("translating_body", "warn", warning)
        await self._log(
            "translating_body",
            "info",
            "日本語PDFをビルドしました",
            detail={
                "translated_pdf": outcome.translated_key,
            },
            timeline=True,
        )

    async def _fire_translation_complete(self) -> None:
        """取り込み完了通知(plans/05 §12.1)。job_id 単位で 1 回限り(notify.py 側で保証)。"""
        if self.user_id is None or self.library_item_id is None:
            return
        paper = await self._get_paper()
        await notify.fire_translation_complete(
            self.session,
            self.deps.redis,
            user_id=self.user_id,
            library_item_id=self.library_item_id,
            paper_title=paper.title,
            job_id=self.job_id,
        )

    async def _is_over_quota(self) -> bool:
        """月次全文翻訳本数のクォータ確認(§2.6)。超過なら True。"""
        limit = self.deps.translation_quota_limit
        if limit is None:
            row = await self.session.get(QuotaLimit, "translation_papers")
            if row is None:
                return False
            limit = row.monthly_limit
        if self.user_id is None:
            return False
        month_start = dt.datetime.now(dt.UTC).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        used = (
            (
                await self.session.execute(
                    select(UsageRecord.library_item_id)
                    .where(
                        UsageRecord.user_id == self.user_id,
                        UsageRecord.task == "translation",
                        UsageRecord.created_at >= month_start,
                        UsageRecord.library_item_id.is_not(None),
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        return len(used) >= limit


def _extract_pdf_abstract(content: DocumentContent) -> str:
    """``Abstract`` 見出しセクションの段落テキストを連結する(PDF は papers.abstract を
    パーサから直接持たないため、要約生成の素材に使う best-effort 抽出。§6.5 の固定見出し)。
    """
    for sec in content.sections:
        if sec.heading.title == "Abstract":
            texts = [block_to_plain(b) for b in sec.blocks if b.type == "paragraph"]
            return " ".join(t for t in texts if t).strip()
    return ""


def _degrade_unresolved_refs(parsed: ParsedDocument) -> int:
    """未解決の要素間参照(式/図/表/定理/アルゴリズム)を原文 text に縮退する(§4.3)。

    セクション参照はセクション要素 id を IR で保持しないため検証対象外(過剰縮退を避ける)。
    引用(citation)は reference_entry の label で別途解決される。返り値は縮退件数。
    """
    labels: set[str] = {blk.label for blk in parsed.blocks if blk.label}
    element_kinds = {"equation", "figure", "table", "theorem", "algorithm"}
    count = 0

    def fix(inlines: list[Any]) -> None:
        nonlocal count
        for il in inlines:
            if il.t == "ref" and il.kind in element_kinds and (il.ref or "") not in labels:
                il.t = "text"
                il.v = il.v or ""
                count += 1
            elif il.t == "emphasis" and getattr(il, "children", None):
                # Inline モデルは children 属性を持たない(v 形)。dict ベースの入れ子形
                # (plans/06 §4.2)を受けた場合のみ再帰する。素の emphasis で
                # AttributeError にならないようガードする。
                fix(il.children)

    for blk in parsed.blocks:
        fix(blk.inlines)
        fix(blk.caption)
    return count


def _summary_numbers_ok(lines: list[Any], material: str) -> bool:
    """各行の数値トークンが入力素材に部分一致で存在するか(plans/07 §3.1 検証)。"""
    import re

    num_re = re.compile(r"[0-9][0-9.,×^%]*")  # noqa: RUF001 (plans/07 §3.1 の数値トークン)
    for line in lines:
        for token in num_re.findall(str(line)):
            core = token.rstrip("×^%").rstrip(".,")  # noqa: RUF001 (同上)
            if core and core not in material:
                return False
    return True


async def run_ingest(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """ingest ジョブの本体(arq ハンドラから呼ばれる)。"""
    deps = deps_from_ctx(ctx)
    run = IngestRun(store.session, store, job, deps)
    await run.run()
