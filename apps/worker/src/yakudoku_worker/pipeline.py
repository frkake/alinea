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
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx
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
from yakudoku_core.arxiv.ids import ArxivId, normalize_arxiv_id
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
from yakudoku_core.document.blocks import DocumentContent
from yakudoku_core.document.plaintext import block_to_plain
from yakudoku_core.ingest import joblog, progress
from yakudoku_core.ingest.thumbnail import render_thumbnail, select_thumbnail_figure
from yakudoku_core.jobs.store import JobStore
from yakudoku_core.parsing.html_parser import PARSER_VERSION, ParsedDocument, parse_arxiv_html
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


class IngestJobPayload(BaseModel):
    """ingest ジョブの payload(plans/05 §2.7)。"""

    mode: str = "initial"  # initial | reingest
    source: str = "arxiv"  # arxiv | pdf_upload
    arxiv_id: str | None = None
    requested_version: str | None = None
    url: str | None = None
    library_item_id: str | None = None


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


def _to_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


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
        self.ref: ArxivId = normalize_arxiv_id(self.payload.arxiv_id or self.payload.url or "")
        self.source_version: str = ""
        self.source_format: str = "arxiv_html"
        self.revision_id: str | None = None
        self.set_id: str | None = None
        self.content: DocumentContent | None = None
        self.parsed: ParsedDocument | None = None
        self.style: str = "natural"
        self._settings_obj: TranslationSettings | None = None

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
        prior = await self._existing_source_version()
        http = self.deps.http
        owns_http = http is None
        if http is None:
            http = make_arxiv_client(self.deps.settings)
        html_bytes = b""
        try:
            meta = await fetch_metadata(self.ref, http=http, settings=self.deps.settings)
            paper = await self._get_paper()
            self._apply_metadata(paper, meta)
            self.source_version = (
                self.payload.requested_version or meta.latest_version or prior or "v1"
            )
            await self.session.commit()
            base = _www_base(self.deps.settings)
            html_bytes = await self._fetch_html(http, base)
            assert self.paper_id is not None
            await self.deps.s3.put(
                self.deps.s3.sources_bucket,
                StorageKeys.arxiv_html(self.paper_id, self.source_version),
                html_bytes,
                content_type="text/html; charset=utf-8",
            )
            await self._record_source_asset(
                "arxiv_html",
                StorageKeys.arxiv_html(self.paper_id, self.source_version),
                content_type="text/html",
                byte_size=len(html_bytes),
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
            detail={"format": self.source_format, "bytes": len(html_bytes)},
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
            DocumentRevision.parser_version == PARSER_VERSION,
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def _stage_parse_and_structure(self) -> None:
        existing = await self._find_revision()
        if existing is not None:
            self.revision_id = str(existing.id)
            self.content = DocumentContent.model_validate(existing.content)
            return

        # parsing: HTML(S3)→ ブロックモデル。
        await self.store.set_progress(self.job_id, 20, stage="parsing")
        assert self.paper_id is not None
        raw = await self.deps.s3.get(
            self.deps.s3.sources_bucket,
            StorageKeys.arxiv_html(self.paper_id, self.source_version),
        )
        self.parsed = parse_arxiv_html(raw.decode("utf-8"))
        await self.store.checkpoint(self.job_id, "parsing", {}, progress=20)

        # structuring: リビジョン永続化・図保存・検索索引・サムネイル。
        await self.store.set_progress(self.job_id, 35, stage="structuring")
        await self._structure()
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
            parser_version=PARSER_VERSION,
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
        await rebuild_block_search_index(self.session, self.revision_id, content)
        warnings.extend(await self._make_thumbnail(paper, figure_bytes))
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
        if self.parsed is None or self.deps.http is None or self.paper_id is None:
            return out, warnings
        base = f"{_www_base(self.deps.settings)}/html/{self.ref.versioned}/"
        for fig in self.parsed.figures:
            if not fig.asset_key:
                continue
            try:
                await self._throttle()
                resp = await self.deps.http.get(urljoin(base, fig.asset_key), timeout=30.0)
                if resp.status_code != 200:
                    raise FetchError("source_not_found", f"figure {resp.status_code}")
                ext = "svg" if fig.asset_key.endswith(".svg") else "png"
                await self.deps.s3.put(
                    self.deps.s3.assets_bucket,
                    StorageKeys.figure(self.paper_id, revision_id, fig.id, ext),
                    resp.content,
                    content_type=resp.headers.get("content-type", "image/png"),
                )
                out[fig.id] = resp.content
            except (httpx.HTTPError, FetchError) as exc:
                warnings.append(f"図の切り出しに失敗(続行): {fig.label or fig.id} — {exc}")
        return out, warnings

    async def _make_thumbnail(self, paper: Paper, figure_bytes: dict[str, bytes]) -> list[str]:
        if self.parsed is None or self.paper_id is None:
            return []
        selected = select_thumbnail_figure(self.parsed.figures)
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

    # -- translating_abstract --------------------------------------------

    async def _stage_translating_abstract(self) -> None:
        settings = await self._load_user_settings()
        paper = await self._get_paper()
        self.style = self._resolve_style(paper.visibility, settings)
        # 冪等: 共有資産(abstract_ja + summary_lines)が既にあれば再生成しない(§2.3)。
        if paper.abstract_ja and paper.summary_lines is not None:
            return

        await self.store.set_progress(self.job_id, 50, stage="translating_abstract")
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

        # クォータ確認(翻訳段のみ停止。§2.6)。
        if body_section_ids and await self._is_over_quota():
            await self.store.mark_waiting_quota(self.job_id)
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
            if not enqueued:  # 本文ジョブ 0 件 → その場で確定(§2.1)。
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
        await progress.finalize_ingest_if_body_complete(
            self.session,
            set_id=self.set_id,
            ingest_job_id=self.job_id,
            content=self.content,
            style=self.style,
            source_version=self.source_version,
            appendix_untranslated=appendix_untranslated,
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
            elif il.t == "emphasis" and il.children:
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
