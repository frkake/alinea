"""``jobs.kind='paper_export'`` ハンドラ(論文単位スタンドアロンエクスポート。Feature S3・Task 11)。

1 ライブラリ項目(=ユーザーと論文の組)について、選択した成果物を **サーバ非依存で開ける** 形の
zip に束ねて S3(assets バケット)へアップロードし、署名付き URL を ``jobs.result.download_url``
に格納する。設計は docs/superpowers/specs/2026-07-16-standalone-paper-export-design.md。

成果物(``payload.artifacts``):

- ``source_html`` / ``translation_html`` / ``bilingual_html`` / ``article_html``
  — ``alinea_api.schemas.standalone_html`` の純レンダラで単一 HTML(inline CSS・図は data URI・
  数式は KaTeX ランタイム inline)を生成する。
- ``pdf_original`` — 原文 PDF に ``block_search_index.page/bbox`` を使ったハイライト矩形 +
  コメント(popup)注釈を埋め込む(:mod:`alinea_worker.pdf_annotations`)。
- ``pdf_translated`` — 有効 natural セット由来の訳文 PDF(注釈なし。レイアウト非対応のため)。
- ``pdf_bilingual`` — 原文 PDF(注釈埋め込み済み)と訳文 PDF をページ交互に結合する。

ジョブは開始前に所有者・library item・選択 artifact の可用性を再検証し、availableでない
artifact を含む場合は生成を始めずに失敗する。出力 PDF/zip は一時ディレクトリへストリームし、
成功・失敗・キャンセルいずれの経路でもクリーンアップする。

**HANDLERS 登録**: ``apps/worker/src/alinea_worker/tasks/__init__.py``(共有ファイル)に::

    from alinea_worker.tasks.export_paper import run_export_paper_job
    HANDLERS["paper_export"] = run_export_paper_job
"""

from __future__ import annotations

import base64
import mimetypes
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import alinea_api.schemas.standalone_html as _standalone_html
from alinea_api.schemas.export import export_filename
from alinea_api.schemas.library import build_paper_bib
from alinea_api.schemas.standalone_html import (
    ArticleBlockView,
    StandaloneMeta,
    TranslationView,
    render_article_html,
    render_document_html,
)
from alinea_core.db.models import (
    Annotation,
    Article,
    ArticleBlock,
    BlockSearchIndex,
    LibraryItem,
    Paper,
    SourceAsset,
)
from alinea_core.db.revisions import get_latest_paper_revision
from alinea_core.document.blocks import DocumentContent
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_core.translation.pipeline import (
    BLOCKING_FLAGS,
    find_effective_set,
    resolve_translation_set_units,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_worker.pdf_annotations import (
    BlockAnnotation,
    embed_block_annotations,
    interleave_bilingual_pdf,
)

_EXPORT_URL_TTL_SECONDS = 24 * 60 * 60  # 有効 24 時間(全量エクスポートと同一。plans/03 §18)
_STANDALONE_PDF_KINDS = ("pdf", "arxiv_pdf", "pdf_upload", "extension_capture")

# 選択可能な全成果物(availability API と 1:1)。
_ALL_ARTIFACTS = (
    "source_html",
    "translation_html",
    "bilingual_html",
    "article_html",
    "pdf_original",
    "pdf_translated",
    "pdf_bilingual",
)
_HTML_ARTIFACTS = frozenset({"source_html", "translation_html", "bilingual_html", "article_html"})


class ArtifactUnavailableError(RuntimeError):
    """選択された成果物が未生成(=可用でない)ため、生成を始める前に失敗させる。"""


# ---------------------------------------------------------------------------
# 成果物の解決に必要な DB 素材(1 度だけ読み、HTML/PDF 生成で使い回す)
# ---------------------------------------------------------------------------
class _ExportContext:
    def __init__(self) -> None:
        self.item: LibraryItem
        self.paper: Paper
        self.revision: Any = None
        self.content: DocumentContent | None = None
        self.paper_bib: Any = None
        self.translation_ready: bool = False
        self.units: dict[str, TranslationView] = {}
        self.article: Article | None = None
        self.article_blocks: list[ArticleBlock] = []
        self.original_pdf_key: str | None = None
        self.translated_pdf_key: str | None = None


def _unit_displayable(text_ja: str, content_ja: Any, quality_flags: list[str] | None) -> bool:
    typed_table = isinstance(content_ja, dict) and content_ja.get("kind") == "table"
    flags = set(quality_flags or [])
    return bool(text_ja or typed_table) and not (flags & BLOCKING_FLAGS)


def _document_from_revision(revision: Any) -> DocumentContent | None:
    try:
        content = DocumentContent.model_validate(revision.content)
    except Exception:  # 壊れた content は「原文なし」として扱う(P3)
        return None
    return content if content.iter_blocks() else None


async def _translated_pdf_key(
    session: AsyncSession, revision: Any, user_id: str
) -> str | None:
    """有効 natural セット由来の訳文 PDF の正規キー(無ければ None)。papers.py と同一規則。"""
    tset = await find_effective_set(session, str(revision.id), "natural", user_id)
    if tset is None:
        return None
    return StorageKeys.translated_pdf(
        str(revision.paper_id),
        revision.source_version,
        "natural",
        translation_set_id=(str(tset.id) if tset.scope == "personal" else None),
    )


async def _original_pdf_key(session: AsyncSession, revision: Any) -> str | None:
    row = (
        await session.execute(
            select(SourceAsset.storage_key)
            .where(
                SourceAsset.paper_id == revision.paper_id,
                SourceAsset.kind.in_(_STANDALONE_PDF_KINDS),
                SourceAsset.source_version == revision.source_version,
            )
            .limit(1)
        )
    ).first()
    return str(row[0]) if row is not None else None


async def _has_translated_pdf_asset(session: AsyncSession, revision: Any, key: str) -> bool:
    row = (
        await session.execute(
            select(SourceAsset.id)
            .where(
                SourceAsset.paper_id == revision.paper_id,
                SourceAsset.kind == "translated_pdf",
                SourceAsset.storage_key == key,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def _build_translation_views(
    session: AsyncSession, revision: Any, user_id: str
) -> dict[str, TranslationView]:
    tset = await find_effective_set(session, str(revision.id), "natural", user_id)
    if tset is None:
        return {}
    units = await resolve_translation_set_units(session, tset)
    return {
        block_id: TranslationView(
            content_ja=unit.content_ja,
            text_ja=unit.text_ja or "",
            displayable=_unit_displayable(
                unit.text_ja or "", unit.content_ja, unit.quality_flags
            ),
        )
        for block_id, unit in units.items()
    }


async def _resolve_context(
    session: AsyncSession, *, user_id: str, library_item_id: str, artifacts: list[str]
) -> _ExportContext:
    """所有者・library item・選択 artifact を再検証し、生成に必要な素材を集める。

    availableでない artifact を含む場合は :class:`ArtifactUnavailableError` を送出する
    (呼び出し元が生成を始める前に失敗させる)。
    """
    ctx = _ExportContext()

    item = await session.get(LibraryItem, library_item_id)
    if item is None or str(item.user_id) != str(user_id):
        raise ArtifactUnavailableError("library item not found or not owned by requester")
    ctx.item = item

    paper = await session.get(Paper, item.paper_id)
    if paper is None:
        raise ArtifactUnavailableError("paper not found for library item")
    ctx.paper = paper
    ctx.paper_bib = build_paper_bib(paper)

    revision = await get_latest_paper_revision(session, paper)
    ctx.revision = revision
    if revision is not None:
        ctx.content = _document_from_revision(revision)

    unknown = [a for a in artifacts if a not in _ALL_ARTIFACTS]
    if unknown:
        raise ArtifactUnavailableError(f"unknown artifact(s): {', '.join(sorted(unknown))}")
    if not artifacts:
        raise ArtifactUnavailableError("no artifacts selected")

    wants_translation = bool(
        {"translation_html", "bilingual_html"} & set(artifacts)
    )
    wants_original_pdf = "pdf_original" in artifacts
    wants_translated_pdf = "pdf_translated" in artifacts
    wants_bilingual_pdf = "pdf_bilingual" in artifacts

    # 原文(HTML)は content が必須。
    if ({"source_html", "translation_html", "bilingual_html"} & set(artifacts)) and (
        ctx.content is None
    ):
        raise ArtifactUnavailableError("source document is not available")

    # 訳文/対訳(HTML)は有効 natural セットの完成が必須。
    if wants_translation:
        tset = (
            await find_effective_set(session, str(revision.id), "natural", user_id)
            if revision is not None
            else None
        )
        if tset is None or tset.status != "complete":
            raise ArtifactUnavailableError("translation is not complete")
        ctx.translation_ready = True
        ctx.units = await _build_translation_views(session, revision, user_id)

    # 記事は Article 行が必須。
    if "article_html" in artifacts:
        article = (
            await session.execute(
                select(Article)
                .where(Article.library_item_id == library_item_id)
                .order_by(Article.generated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if article is None:
            raise ArtifactUnavailableError("article is not available")
        ctx.article = article
        ctx.article_blocks = list(
            (
                await session.execute(
                    select(ArticleBlock)
                    .where(ArticleBlock.article_id == article.id)
                    .order_by(ArticleBlock.position.asc())
                )
            )
            .scalars()
            .all()
        )

    # 原文 PDF は原本アセットが必須。
    if wants_original_pdf or wants_bilingual_pdf:
        if revision is None:
            raise ArtifactUnavailableError("original PDF is not available")
        original_key = await _original_pdf_key(session, revision)
        if original_key is None:
            raise ArtifactUnavailableError("original PDF is not available")
        ctx.original_pdf_key = original_key

    # 訳文 PDF は有効 natural セット由来の訳文 PDF アセットが必須。
    if wants_translated_pdf or wants_bilingual_pdf:
        if revision is None:
            raise ArtifactUnavailableError("translated PDF is not available")
        translated_key = await _translated_pdf_key(session, revision, user_id)
        if translated_key is None or not await _has_translated_pdf_asset(
            session, revision, translated_key
        ):
            raise ArtifactUnavailableError("translated PDF is not available")
        ctx.translated_pdf_key = translated_key

    return ctx


# ---------------------------------------------------------------------------
# 図の data URI 化(HTML 成果物のスタンドアロン化)
# ---------------------------------------------------------------------------
def _document_asset_keys(content: DocumentContent) -> set[str]:
    return {
        block.asset_key
        for _section, block in content.iter_blocks()
        if block.type in ("figure", "table", "equation") and block.asset_key
    }


async def _image_data_uris(storage: Any, asset_keys: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in sorted(asset_keys):
        try:
            data = await storage.get(storage.assets_bucket, key)
        except Exception:  # noqa: S112 — 欠損アセットは skip(P3。レンダラが代替表示)
            continue
        mime = mimetypes.guess_type(key)[0] or "image/png"
        encoded = base64.b64encode(data).decode("ascii")
        out[key] = f"data:{mime};base64,{encoded}"
    return out


def _meta(paper_bib: Any, quality: str, mode_label: str) -> StandaloneMeta:
    import datetime as dt

    return StandaloneMeta(
        title=paper_bib.title,
        authors=list(paper_bib.authors),
        arxiv_id=paper_bib.arxiv_id,
        generated_at=dt.datetime.now(dt.UTC).isoformat(),
        mode_label=mode_label,
        quality_level=quality,
    )


def _html_filename(paper_bib: Any, suffix: str) -> str:
    """HTML 版のファイル名(export_filename の .md を .html に差し替え)。"""
    return export_filename(paper_bib, suffix=suffix).removesuffix(".md") + ".html"


def _pdf_filename(paper_bib: Any, suffix: str) -> str:
    return export_filename(paper_bib, suffix=suffix).removesuffix(".md") + ".pdf"


# ---------------------------------------------------------------------------
# 注釈の収集(原文 PDF への埋め込み用)
# ---------------------------------------------------------------------------
async def _collect_block_annotations(
    session: AsyncSession, *, library_item_id: str, revision_id: str
) -> list[BlockAnnotation]:
    """library item の注釈を block_search_index の page/bbox と突き合わせて収集する。

    bbox を持たないブロックの注釈も返し(page/bbox=None)、埋め込み側で skipped に数える。
    """
    positions = {
        row.block_id: (row.page, list(row.bbox) if row.bbox is not None else None)
        for row in (
            await session.execute(
                select(BlockSearchIndex).where(
                    BlockSearchIndex.revision_id == revision_id
                )
            )
        )
        .scalars()
        .all()
    }
    annotations = (
        (
            await session.execute(
                select(Annotation)
                .where(Annotation.library_item_id == library_item_id)
                .order_by(Annotation.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    out: list[BlockAnnotation] = []
    for ann in annotations:
        anchor = ann.anchor if isinstance(ann.anchor, dict) else {}
        block_id = str(anchor.get("block_id") or "")
        if not block_id:
            continue
        page, bbox = positions.get(block_id, (None, None))
        out.append(
            BlockAnnotation(
                block_id=block_id,
                kind=ann.kind,
                color=ann.color,
                comment=ann.body,
                page=page,
                bbox=bbox,
            )
        )
    return out


# ---------------------------------------------------------------------------
# zip 組み立て
# ---------------------------------------------------------------------------
async def _build_archive(
    session: AsyncSession,
    storage: Any,
    ctx: _ExportContext,
    artifacts: list[str],
    workspace: Path,
) -> tuple[bytes, dict[str, Any]]:
    """選択成果物を生成し zip バイト列を返す。集計(skipped_annotations 等)も返す。"""
    paper_bib = ctx.paper_bib
    quality = ctx.revision.quality_level if ctx.revision is not None else "A"
    # KaTeX ランタイムが vendoring されていれば inline 埋め込み、無ければ LaTeX ソース表示に
    # フォールバックする(設計 決定 A のフォールバック座。欠損ではなく読める劣化)。
    build_katex_runtime = getattr(_standalone_html, "build_katex_runtime", None)
    katex_runtime = build_katex_runtime() if callable(build_katex_runtime) else ""

    files: list[tuple[str, bytes]] = []
    stats: dict[str, Any] = {"artifacts": list(artifacts)}

    # 図の data URI(HTML 系のみ必要。1 度作って使い回す)。
    image_data_uris: dict[str, str] = {}
    if ctx.content is not None and (_HTML_ARTIFACTS & set(artifacts)) - {"article_html"}:
        image_data_uris = await _image_data_uris(storage, _document_asset_keys(ctx.content))

    if "source_html" in artifacts and ctx.content is not None:
        html_doc = render_document_html(
            ctx.content,
            mode="source",
            units={},
            image_data_uris=image_data_uris,
            meta=_meta(paper_bib, quality, "原文"),
            math_runtime=katex_runtime,
        )
        files.append((_html_filename(paper_bib, "-source"), html_doc.encode("utf-8")))

    if "translation_html" in artifacts and ctx.content is not None:
        html_doc = render_document_html(
            ctx.content,
            mode="translation",
            units=ctx.units,
            image_data_uris=image_data_uris,
            meta=_meta(paper_bib, quality, "訳文"),
            math_runtime=katex_runtime,
        )
        files.append((_html_filename(paper_bib, "-translation"), html_doc.encode("utf-8")))

    if "bilingual_html" in artifacts and ctx.content is not None:
        html_doc = render_document_html(
            ctx.content,
            mode="bilingual",
            units=ctx.units,
            image_data_uris=image_data_uris,
            meta=_meta(paper_bib, quality, "対訳"),
            math_runtime=katex_runtime,
        )
        files.append((_html_filename(paper_bib, "-bilingual"), html_doc.encode("utf-8")))

    if "article_html" in artifacts and ctx.article is not None:
        views = [
            ArticleBlockView(type=b.type, content=dict(b.content or {}))
            for b in ctx.article_blocks
        ]
        asset_keys = {
            str(b.content["asset_key"])
            for b in ctx.article_blocks
            if isinstance(b.content, dict) and b.content.get("asset_key")
        }
        article_images = await _image_data_uris(storage, asset_keys)
        meta = StandaloneMeta(
            title=ctx.article.title or paper_bib.title,
            authors=list(paper_bib.authors),
            arxiv_id=paper_bib.arxiv_id,
            generated_at=_meta(paper_bib, quality, "記事").generated_at,
            mode_label="記事",
            quality_level="A",
        )
        html_doc = render_article_html(
            views, image_data_uris=article_images, meta=meta, math_runtime=katex_runtime
        )
        files.append((_html_filename(paper_bib, "-article"), html_doc.encode("utf-8")))

    # --- PDF 系 ---
    annotated_original: bytes | None = None
    if ("pdf_original" in artifacts or "pdf_bilingual" in artifacts) and ctx.original_pdf_key:
        original_bytes = await storage.get(storage.sources_bucket, ctx.original_pdf_key)
        block_annotations = await _collect_block_annotations(
            session,
            library_item_id=str(ctx.item.id),
            revision_id=str(ctx.revision.id),
        )
        annotated_original, embed_result = embed_block_annotations(
            original_bytes, block_annotations
        )
        stats["skipped_annotations"] = embed_result.skipped
        stats["embedded_annotations"] = embed_result.embedded
        if "pdf_original" in artifacts:
            files.append((_pdf_filename(paper_bib, "-original"), annotated_original))

    translated_bytes: bytes | None = None
    if ("pdf_translated" in artifacts or "pdf_bilingual" in artifacts) and ctx.translated_pdf_key:
        translated_bytes = await storage.get(storage.sources_bucket, ctx.translated_pdf_key)
        if "pdf_translated" in artifacts:
            files.append((_pdf_filename(paper_bib, "-translated"), translated_bytes))

    if (
        "pdf_bilingual" in artifacts
        and annotated_original is not None
        and translated_bytes is not None
    ):
        bilingual = interleave_bilingual_pdf(annotated_original, translated_bytes)
        files.append((_pdf_filename(paper_bib, "-bilingual"), bilingual))

    # zip を一時ファイルへストリームしてから読み戻す(メモリ常駐を避ける)。
    zip_path = workspace / "paper_export.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)
    stats["file_names"] = [name for name, _ in files]
    return zip_path.read_bytes(), stats


async def run_export_paper_job(ctx: dict[str, Any], store: JobStore, job: Any) -> None:
    """``kind='paper_export'`` ハンドラ。選択成果物を zip 化し S3 へ保存して署名 URL を返す。"""
    session = store.session
    user_id = str(job.user_id)
    library_item_id = str(job.library_item_id) if job.library_item_id else ""
    payload = job.payload if isinstance(job.payload, dict) else {}
    raw_artifacts = payload.get("artifacts")
    artifacts = [str(a) for a in raw_artifacts] if isinstance(raw_artifacts, list) else []

    storage: Any = ctx.get("s3") or S3Storage(ctx.get("settings"))

    # 開始前の再検証。availableでない artifact を含めば生成を始めずに失敗する。
    export_ctx = await _resolve_context(
        session,
        user_id=user_id,
        library_item_id=library_item_id,
        artifacts=artifacts,
    )

    workspace = Path(tempfile.mkdtemp(prefix="alinea-paper-export-"))
    try:
        archive, stats = await _build_archive(
            session, storage, export_ctx, artifacts, workspace
        )
        key = StorageKeys.export(user_id, str(job.id))
        await storage.put(storage.assets_bucket, key, archive, content_type="application/zip")
        url = await storage.presign_get(
            storage.assets_bucket, key, expires_in=_EXPORT_URL_TTL_SECONDS
        )
        result = {"download_url": url, **stats}
        await store.succeed(str(job.id), result)
    finally:
        # 成功・失敗・キャンセルいずれの経路でも作業用一時ディレクトリを消す。
        shutil.rmtree(workspace, ignore_errors=True)
