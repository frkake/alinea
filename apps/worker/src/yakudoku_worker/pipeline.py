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
import posixpath
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import fitz  # PyMuPDF
import httpx
import structlog
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.arxiv.fetch import (
    FetchError,
    RedisLike,
    Throttle,
    arxiv_throttle,
    make_arxiv_client,
)
from yakudoku_core.arxiv.ids import ArxivId, eprint_url, normalize_arxiv_id
from yakudoku_core.arxiv.metadata import ArxivMeta, fetch_metadata
from yakudoku_core.db.models import (
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
from yakudoku_core.document.blocks import Block, DocumentContent
from yakudoku_core.document.plaintext import block_to_plain
from yakudoku_core.ingest import joblog, progress
from yakudoku_core.ingest.bib_estimate import estimate_bibliography
from yakudoku_core.ingest.reanchor import ReanchorStats, reanchor_paper
from yakudoku_core.ingest.thumbnail import render_thumbnail, select_thumbnail_figure
from yakudoku_core.jobs.store import JobStore
from yakudoku_core.parsing.html_parser import (
    PARSER_VERSION as HTML_PARSER_VERSION,
)
from yakudoku_core.parsing.html_parser import (
    ParsedDocument,
    parse_arxiv_html,
)
from yakudoku_core.parsing.latex_parser import (
    PARSER_VERSION as LATEX_PARSER_VERSION,
)
from yakudoku_core.parsing.latex_parser import (
    LatexParseError,
    extract_latex_archive,
    parse_arxiv_latex,
    parse_latex_source,
    select_main_tex,
)
from yakudoku_core.parsing.pdf_parser import (
    PARSER_VERSION as PDF_PARSER_VERSION,
)
from yakudoku_core.parsing.pdf_parser import (
    ParsedPdfDocument,
    PdfParseError,
    parse_pdf,
)
from yakudoku_core.search.rebuild import rebuild_block_search_index
from yakudoku_core.settings import CoreSettings, get_settings
from yakudoku_core.storage.s3 import S3Storage, StorageKeys
from yakudoku_core.translation.glossary import build_snapshot
from yakudoku_core.translation.pipeline import (
    TranslationContext,
    TranslationSettings,
    compute_translation_scope,
    find_shared_set,
    translate_block,
    translate_section,
)

from yakudoku_worker import notify

# ✦3行要約 + 提案タグ(plans/07 §3.1・plans/05 §11.1。1 呼び出しで生成)。
SUMMARY_SCHEMA_NAME = "summary_3line_v1"
SUMMARY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary_lines", "suggested_tags"],
    "properties": {
        "summary_lines": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"type": "string", "maxLength": 60},
        },
        "suggested_tags": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string", "maxLength": 30},
        },
    },
}
SUMMARY_SYSTEM_PROMPT = (
    "あなたは学術論文の要約者です。与えられた論文を次の 3 行で要約してください。\n"
    "1 行目: 課題(この論文が解こうとしている問題)\n"
    "2 行目: 手法(提案アプローチの核心)\n"
    "3 行目: 結果(主要な成果。数値は本文にあるものだけを使う)\n"
    "各行は日本語 60 文字以内。行頭に番号や記号を付けない(表示側が ① ② ③ を付ける)。"
    "本文にない数値・主張を作らない。\n"
    "あわせて、この論文の主題を表す提案タグを suggested_tags に最大 5 件挙げてください"
    "(英語小文字の短い名詞。例: distillation, solver)。"
)

_MAX_SUGGESTED_TAGS = 5

log = structlog.get_logger("yakudoku.worker.pipeline")

_LATEX_FIGURE_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".eps", ".ps")
_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}


@dataclass(frozen=True)
class FigureAssetPayload:
    content: bytes
    ext: str
    content_type: str


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
    router: Any  # yakudoku_llm.router.LLMRouter(apps→llm 依存を型で固定しない)
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
    return (settings.yakudoku_arxiv_base_url or "https://arxiv.org").rstrip("/")


def _html_figure_asset_url(settings: CoreSettings, ref: ArxivId, asset_key: str) -> str:
    source = asset_key.strip()
    base = _www_base(settings)
    if source.startswith(("http://", "https://")):
        return source
    if source.startswith("//"):
        return f"https:{source}"
    if source.startswith("/"):
        return urljoin(f"{base}/", source.lstrip("/"))
    html_root = f"{base}/html/"
    if source.startswith(f"{ref.versioned}/"):
        return urljoin(html_root, source)
    return urljoin(f"{html_root}{ref.versioned}/", source)


def _to_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _clean_latex_asset_path(value: str) -> str:
    path = value.strip().strip("{}").replace("\\", "/")
    path = path.split("#", 1)[0].split("?", 1)[0].strip()
    return path.lstrip("/")


def _safe_posix_norm(path: str) -> str | None:
    norm = posixpath.normpath(path).removeprefix("./")
    if norm in {"", "."} or norm == ".." or norm.startswith("../"):
        return None
    return norm


def _latex_asset_candidates(asset_key: str, main_tex_name: str | None) -> list[str]:
    raw = _clean_latex_asset_path(asset_key)
    if not raw:
        return []
    main_dir = ""
    if main_tex_name:
        main_norm = _safe_posix_norm(_clean_latex_asset_path(main_tex_name))
        if main_norm:
            main_dir = posixpath.dirname(main_norm)
    bases = ["", main_dir] if main_dir else [""]
    seen: set[str] = set()
    candidates: list[str] = []
    for base in bases:
        norm = _safe_posix_norm(posixpath.join(base, raw))
        if norm is None:
            continue
        variants = [norm]
        if not posixpath.splitext(norm)[1]:
            variants.extend(f"{norm}{ext}" for ext in _LATEX_FIGURE_EXTS)
        for item in variants:
            if item not in seen:
                seen.add(item)
                candidates.append(item)
    return candidates


def _find_latex_binary_asset(
    binary_files: dict[str, bytes], asset_key: str, main_tex_name: str | None
) -> tuple[str, bytes] | None:
    by_norm = {
        (norm.lower() if (norm := _safe_posix_norm(_clean_latex_asset_path(name))) else ""): name
        for name in binary_files
    }
    by_norm.pop("", None)
    for candidate in _latex_asset_candidates(asset_key, main_tex_name):
        name = by_norm.get(candidate.lower())
        if name is not None:
            return name, binary_files[name]
    return None


def _render_first_page(data: bytes, filetype: str) -> bytes:
    with fitz.open(stream=data, filetype=filetype) as doc:
        if doc.page_count < 1:
            raise FetchError("unsupported_figure_format", "figure document has no pages")
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        return pix.tobytes("png")


def _figure_asset_payload(
    data: bytes, source_name: str, content_type: str | None = None
) -> FigureAssetPayload:
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    ext = posixpath.splitext(source_name)[1].lower()
    if ext == ".pdf" or content_type == "application/pdf" or data.startswith(b"%PDF"):
        return FigureAssetPayload(_render_first_page(data, "pdf"), "png", "image/png")
    if ext in {".eps", ".ps"}:
        return FigureAssetPayload(
            _render_first_page(data, ext.removeprefix(".")), "png", "image/png"
        )
    if ext in _IMAGE_CONTENT_TYPES:
        return FigureAssetPayload(data, ext.removeprefix("."), _IMAGE_CONTENT_TYPES[ext])
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return FigureAssetPayload(data, "png", "image/png")
    if data.startswith(b"\xff\xd8\xff"):
        return FigureAssetPayload(data, "jpg", "image/jpeg")
    if data[:12].startswith(b"RIFF") and data[8:12] == b"WEBP":
        return FigureAssetPayload(data, "webp", "image/webp")
    if data.startswith((b"GIF87a", b"GIF89a")):
        return FigureAssetPayload(data, "gif", "image/gif")
    if b"<svg" in data[:512].lower():
        return FigureAssetPayload(data, "svg", "image/svg+xml")
    raise FetchError(
        "unsupported_figure_format",
        f"unsupported figure format: {source_name or content_type or 'unknown'}",
    )


def latex_figure_asset_payload(
    binary_files: dict[str, bytes], asset_key: str, main_tex_name: str | None
) -> tuple[str, FigureAssetPayload] | None:
    found = _find_latex_binary_asset(binary_files, asset_key, main_tex_name)
    if found is None:
        return None
    source_name, data = found
    return source_name, _figure_asset_payload(data, source_name)


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
        self.source_version: str = ""
        self.source_format: str = "pdf_upload" if self.is_pdf_upload else "arxiv_html"
        self.revision_id: str | None = None
        self.set_id: str | None = None
        self.content: DocumentContent | None = None
        self.parsed: ParsedDocument | None = None
        self.parsed_pdf: ParsedPdfDocument | None = None
        self.latex_binary_files: dict[str, bytes] = {}
        self.latex_main_tex_name: str | None = None
        self._pdf_bytes: bytes | None = None
        self.style: str = "natural"
        self._settings_obj: TranslationSettings | None = None

    @property
    def parser_version(self) -> str:
        """取得優先順位 LaTeX > HTML > PDF(plans/05 §1.3・§5・M2-01)。

        ``source_format`` は fetching 段で確定するため(`_stage_fetching`)、parsing/structuring
        段(そのあと)から読む本プロパティは常に実際に使ったパーサと一致する。
        """
        if self.is_pdf_upload:
            return PDF_PARSER_VERSION
        if self.source_format == "latex":
            return LATEX_PARSER_VERSION
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
        # 冪等: checkpoint 済みなら再取得しない(§2.3。S3 に原本が残っている)。
        fetch_ck = self.ckpt.get("fetching")
        if fetch_ck and fetch_ck.get("source_version"):
            self.source_version = str(fetch_ck["source_version"])
            self.source_format = str(fetch_ck.get("source_format", "arxiv_html"))
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
            await self.session.commit()
            base = _www_base(self.deps.settings)
            assert self.paper_id is not None
            # 取得優先順位 LaTeX > HTML > PDF(plans/05 §1.3・§5・M2-01)。
            latex_bytes = await self._fetch_latex_best_effort(http)
            if latex_bytes is not None:
                self.source_format = "latex"
                source_bytes = latex_bytes
                await self.deps.s3.put(
                    self.deps.s3.sources_bucket,
                    StorageKeys.latex_tar(self.paper_id, self.source_version),
                    latex_bytes,
                    content_type="application/gzip",
                )
                await self._record_source_asset(
                    "arxiv_latex",  # plans/02 §4.3 ck_source_assets_kind の許容値
                    StorageKeys.latex_tar(self.paper_id, self.source_version),
                    content_type="application/gzip",
                    byte_size=len(latex_bytes),
                    source_url=eprint_url(
                        self.ref, self.deps.settings.yakudoku_arxiv_base_url or None
                    ),
                )
            else:
                source_bytes = await self._fetch_html(http, base)
                await self.deps.s3.put(
                    self.deps.s3.sources_bucket,
                    StorageKeys.arxiv_html(self.paper_id, self.source_version),
                    source_bytes,
                    content_type="text/html; charset=utf-8",
                )
                await self._record_source_asset(
                    "arxiv_html",
                    StorageKeys.arxiv_html(self.paper_id, self.source_version),
                    content_type="text/html",
                    byte_size=len(source_bytes),
                    source_url=f"{base}/html/{self.ref.versioned}",
                )
            await self._fetch_pdf_best_effort(http, base)
        finally:
            if owns_http:
                await http.aclose()

        await self._log(
            "fetching",
            "info",
            joblog.fetch_timeline_message(self.source_format),
            detail={"format": self.source_format, "bytes": len(source_bytes)},
            timeline=True,
        )
        await self.store.checkpoint(
            self.job_id,
            "fetching",
            {"source_version": self.source_version, "source_format": self.source_format},
            progress=10,
        )

    async def _fetch_latex_best_effort(self, http: httpx.AsyncClient) -> bytes | None:
        """LaTeX ソース(e-print)を試行取得する(取得優先順位 §1.3・§5。M2-01)。

        取得・展開・パース(検証用)のいずれかに失敗した場合は ``None`` を返し、呼び出し側が
        既存の HTML 経路へ可視的にフォールバックする(``jobs.log`` warn。P3)。既存の
        HTML/PDF 経路は変更しない。
        """
        assert self.ref is not None
        try:
            await self._throttle()
            resp = await http.get(
                eprint_url(self.ref, self.deps.settings.yakudoku_arxiv_base_url or None),
                timeout=httpx.Timeout(60.0, connect=5.0),
            )
        except httpx.HTTPError as exc:
            await self._log(
                "fetching",
                "warn",
                "LaTeX ソース取得に失敗(HTML へフォールバック)",
                detail={"error": str(exc)},
            )
            return None
        if resp.status_code != 200 or "pdf" in resp.headers.get("content-type", "").lower():
            return None
        data = resp.content
        try:
            parse_arxiv_latex(data)  # 展開・メイン .tex 特定・構文解析まで検証する
        except LatexParseError as exc:
            await self._log(
                "fetching",
                "warn",
                "LaTeX ソースの解析に失敗(HTML へフォールバック)",
                detail={"error": str(exc), "kind": exc.kind},
            )
            return None
        return data

    async def _get_pdf_bytes(self) -> bytes:
        """アップロード原本 PDF を S3 から取得する(未取得なら都度取得。§9.2)。"""
        if self._pdf_bytes is not None:
            return self._pdf_bytes
        assert self.paper_id is not None
        data = await self.deps.s3.get(
            self.deps.s3.sources_bucket,
            StorageKeys.original_pdf(self.paper_id, self.source_version or "v1"),
        )
        self._pdf_bytes = data
        return data

    async def _stage_fetching_pdf(self) -> None:
        """pdf_upload: ローカル資産(拡張が送信済みの原本 PDF)の存在確認のみで完了する(§9.2)。

        `POST /api/ingest/pdf` が S3 に既に PUT 済みのため、再取得(HTTP)は発生しない。
        """
        self.source_version = "v1"
        self.source_format = "pdf_upload"
        try:
            data = await self._get_pdf_bytes()
        except Exception as exc:
            raise FetchError("source_not_found", f"original pdf missing: {exc}") from exc

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

    async def _fetch_html(self, http: httpx.AsyncClient, base: str) -> bytes:
        assert self.ref is not None
        url = f"{base}/html/{self.ref.versioned}"
        await self._throttle()
        try:
            resp = await http.get(url, timeout=httpx.Timeout(30.0, connect=5.0))
        except httpx.HTTPError as exc:
            raise FetchError("network_error", f"arxiv html fetch failed: {exc}") from exc
        if resp.status_code == 404:
            raise FetchError("source_not_found", f"arxiv html 404: {url}")
        if resp.status_code >= 500:
            raise FetchError("upstream_5xx", f"arxiv html {resp.status_code}")
        if resp.status_code != 200 or "ltx_document" not in resp.text:
            raise FetchError("source_not_found", "arxiv html has no ltx_document")
        self.source_format = "arxiv_html"
        return resp.content

    async def _fetch_pdf_best_effort(self, http: httpx.AsyncClient, base: str) -> None:
        assert self.ref is not None
        url = f"{base}/pdf/{self.ref.versioned}"
        try:
            await self._throttle()
            resp = await http.get(url, timeout=httpx.Timeout(120.0, connect=5.0))
            if resp.status_code != 200:
                raise FetchError("source_not_found", f"pdf {resp.status_code}")
            assert self.paper_id is not None
            await self.deps.s3.put(
                self.deps.s3.sources_bucket,
                StorageKeys.original_pdf(self.paper_id, self.source_version),
                resp.content,
                content_type="application/pdf",
            )
            await self._record_source_asset(
                "pdf",
                StorageKeys.original_pdf(self.paper_id, self.source_version),
                content_type="application/pdf",
                byte_size=len(resp.content),
                source_url=url,
            )
        except (httpx.HTTPError, FetchError) as exc:
            await self._log(
                "fetching",
                "warn",
                "原文 PDF を取得できませんでした(続行)",
                detail={"error": str(exc)},
            )

    async def _record_source_asset(
        self, kind: str, key: str, *, content_type: str, byte_size: int, source_url: str
    ) -> None:
        exists = (
            await self.session.execute(
                select(SourceAsset.id).where(
                    SourceAsset.paper_id == self.paper_id,
                    SourceAsset.source_version == self.source_version,
                    SourceAsset.kind == kind,
                )
            )
        ).first()
        if exists is not None:
            return
        self.session.add(
            SourceAsset(
                paper_id=self.paper_id,
                kind=kind,
                source_url=source_url,
                source_version=self.source_version,
                storage_key=key,
                content_type=content_type,
                byte_size=byte_size,
            )
        )
        await self.session.commit()

    # -- parsing + structuring -------------------------------------------

    async def _find_revision(self) -> DocumentRevision | None:
        stmt = select(DocumentRevision).where(
            DocumentRevision.paper_id == self.paper_id,
            DocumentRevision.source_version == self.source_version,
            DocumentRevision.parser_version == self.parser_version,
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def _stage_parse_and_structure(self) -> None:
        existing = await self._find_revision()
        if existing is not None:
            self.revision_id = str(existing.id)
            self.content = DocumentContent.model_validate(existing.content)
            return

        await self.store.set_progress(self.job_id, 20, stage="parsing")
        await self._publish_stage("parsing", 20)
        assert self.paper_id is not None
        # adopt_on_complete(通知「変更する」経由の reingest)は新リビジョン確定後に旧リビジョンを
        # 追従させる(§4.5)。構造化前に「現在の latest」を旧リビジョンとして確定しておく。
        old_revision_id = str((await self._get_paper()).latest_revision_id or "") or None
        if self.is_pdf_upload:
            data = await self._get_pdf_bytes()
            try:
                self.parsed_pdf = parse_pdf(data)
            except PdfParseError as exc:
                raise FetchError(exc.kind, exc.message) from exc
            except Exception as exc:
                raise FetchError("parse_error", f"pdf parse failed: {exc}") from exc
            await self.store.checkpoint(self.job_id, "parsing", {}, progress=20)

            await self.store.set_progress(self.job_id, 35, stage="structuring")
            await self._publish_stage("structuring", 35)
            await self._structure_pdf(data)
            if self.payload.adopt_on_complete and old_revision_id is not None:
                await self._reanchor_after_adopt(old_revision_id)
            await self.store.checkpoint(
                self.job_id, "structuring", {"revision_id": self.revision_id}, progress=35
            )
            return

        # parsing: LaTeX(優先)または HTML(いずれも S3)→ ブロックモデル(§1.3・§5・M2-01)。
        if self.source_format == "latex":
            raw = await self.deps.s3.get(
                self.deps.s3.sources_bucket,
                StorageKeys.latex_tar(self.paper_id, self.source_version),
            )
            try:
                extracted = extract_latex_archive(raw)
                self.latex_binary_files = extracted.binary_files
                self.latex_main_tex_name, _ = select_main_tex(extracted.text_files)
                self.parsed = parse_latex_source(self.latex_main_tex_name, extracted.text_files)
            except LatexParseError as exc:
                raise FetchError("parse_error", f"latex parse failed: {exc}") from exc
        else:
            self.latex_binary_files = {}
            self.latex_main_tex_name = None
            raw = await self.deps.s3.get(
                self.deps.s3.sources_bucket,
                StorageKeys.arxiv_html(self.paper_id, self.source_version),
            )
            self.parsed = parse_arxiv_html(raw.decode("utf-8"))
        await self.store.checkpoint(self.job_id, "parsing", {}, progress=20)

        # structuring: リビジョン永続化・図保存・検索索引・サムネイル。
        await self.store.set_progress(self.job_id, 35, stage="structuring")
        await self._publish_stage("structuring", 35)
        await self._structure()
        if self.payload.adopt_on_complete and old_revision_id is not None:
            await self._reanchor_after_adopt(old_revision_id)
        await self.store.checkpoint(
            self.job_id, "structuring", {"revision_id": self.revision_id}, progress=35
        )

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
        paper.latest_revision_id = revision.id
        figure_bytes, fig_warnings = await self._save_figures(self.revision_id)
        warnings.extend(fig_warnings)
        content = self.parsed.to_document_content()
        revision.content = content.model_dump()
        self.content = content
        await rebuild_block_search_index(self.session, self.revision_id, content)
        warnings.extend(await self._make_thumbnail(paper, figure_bytes, self.parsed.figures))
        await self.session.commit()

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

    async def _save_figures(self, revision_id: str) -> tuple[dict[str, bytes], list[str]]:
        """図アセットを S3 に保存する(best-effort。失敗は warn で続行。§2.4)。"""
        out: dict[str, bytes] = {}
        warnings: list[str] = []
        if self.parsed is None or self.paper_id is None:
            return out, warnings
        base = None
        if self.deps.http is not None and self.ref is not None:
            base = f"{_www_base(self.deps.settings)}/html/{self.ref.versioned}/"
        for fig in self.parsed.figures:
            if not fig.asset_key:
                continue
            try:
                payload: FigureAssetPayload | None = None
                if self.source_format == "latex":
                    local = latex_figure_asset_payload(
                        self.latex_binary_files, fig.asset_key, self.latex_main_tex_name
                    )
                    if local is not None:
                        _source_name, payload = local
                if payload is None:
                    if self.deps.http is None or base is None:
                        raise FetchError("source_not_found", "figure source is not available")
                    await self._throttle()
                    assert self.ref is not None
                    resp = await self.deps.http.get(
                        _html_figure_asset_url(self.deps.settings, self.ref, fig.asset_key),
                        timeout=30.0,
                    )
                    if resp.status_code != 200:
                        raise FetchError("source_not_found", f"figure {resp.status_code}")
                    payload = _figure_asset_payload(
                        resp.content, fig.asset_key, resp.headers.get("content-type")
                    )
                key = StorageKeys.figure(self.paper_id, revision_id, fig.id, payload.ext)
                await self.deps.s3.put(
                    self.deps.s3.assets_bucket,
                    key,
                    payload.content,
                    content_type=payload.content_type,
                )
                fig.asset_key = key
                out[fig.id] = payload.content
            except (httpx.HTTPError, FetchError, RuntimeError, ValueError) as exc:
                warnings.append(f"図の切り出しに失敗(続行): {fig.label or fig.id} — {exc}")
            except Exception as exc:
                warnings.append(f"図の切り出しに失敗(続行): {fig.label or fig.id} — {exc}")
        return out, warnings

    async def _make_thumbnail(
        self, paper: Paper, figure_bytes: dict[str, bytes], figures: list[Block]
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
        await self.deps.s3.put(
            self.deps.s3.assets_bucket,
            StorageKeys.thumbnail(self.paper_id),
            card,
            content_type="image/webp",
        )
        await self.deps.s3.put(
            self.deps.s3.assets_bucket,
            StorageKeys.thumbnail(self.paper_id, retina=True),
            card_2x,
            content_type="image/webp",
        )
        paper.thumbnail_key = StorageKeys.thumbnail(self.paper_id)
        return []

    # -- structuring (pdf_upload。品質 B。plans/05 §6・§9.2) --------------

    async def _structure_pdf(self, data: bytes) -> None:
        """PDF アップロードの structuring 段: リビジョン永続化・図表資産・書誌推定・索引・サムネ。

        pdf_parser が既に図表を切り出し済み(HTTP 再取得不要)。
        """
        assert self.parsed_pdf is not None and self.paper_id is not None
        warnings = list(self.parsed_pdf.warnings)
        content = self.parsed_pdf.to_document_content()
        scope = compute_translation_scope(content)
        stats: dict[str, Any] = dict(self.parsed_pdf.stats)
        stats["translatable_blocks"] = len(scope.in_scope_block_ids)

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
        fig_warnings = await self._save_pdf_assets(self.revision_id)
        warnings.extend(fig_warnings)
        revision.content = content.model_dump()
        self.content = content

        paper = await self._get_paper()
        paper.latest_revision_id = revision.id
        abstract_text = _extract_pdf_abstract(content)
        if abstract_text and not paper.abstract:
            paper.abstract = abstract_text
        await self._apply_bib_estimate(paper, data)

        await rebuild_block_search_index(self.session, self.revision_id, content)
        warnings.extend(
            await self._make_thumbnail(
                paper, self.parsed_pdf.figure_images, self.parsed_pdf.figures
            )
        )
        await self.session.commit()

        for warning in warnings:
            await self._log("structuring", "warn", warning)
        await self._log(
            "structuring",
            "info",
            joblog.structuring_timeline_message(stats),
            detail={"stats": stats},
            timeline=True,
        )

    async def _save_pdf_assets(self, revision_id: str) -> list[str]:
        """図・表・数式の切り出し PNG を S3 へ保存し block.asset_key を確定する(§6.6.3)。"""
        warnings: list[str] = []
        if self.parsed_pdf is None or self.paper_id is None:
            return warnings
        blocks_by_id = {b.id: b for b in self.parsed_pdf.blocks}
        for block_id, png in self.parsed_pdf.figure_images.items():
            block = blocks_by_id.get(block_id)
            if block is None:
                continue
            try:
                key = StorageKeys.figure(self.paper_id, revision_id, block_id, "png")
                await self.deps.s3.put(
                    self.deps.s3.assets_bucket, key, png, content_type="image/png"
                )
                block.asset_key = key
            except Exception as exc:
                warnings.append(f"図/表アセットの保存に失敗(続行): {block_id} — {exc}")
        return warnings

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
        from yakudoku_llm.types import ContentPart, JsonSchemaSpec, LLMRequest, Message

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
                await self.deps.arq_pool.enqueue_job("run_job", jid, _queue_name="yk:bulk")
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
            # 完了ナッジ(§21.2)。job_events は job_id 一致のイベントを受けて DB の
            # 最終状態(succeeded)を再確認し done フレームを組む(routers/jobs.py 参照)ため、
            # ここでの data 自体は any でよいが InfoPanel の onProgress と同形に揃える。
            await self._publish_stage("complete", 100, status="succeeded")
            await self._fire_translation_complete()

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
