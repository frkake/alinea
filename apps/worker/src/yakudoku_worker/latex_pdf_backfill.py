"""Backfill translated PDFs for completed LaTeX translation sets."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import DocumentRevision, SourceAsset, TranslationSet
from yakudoku_core.db.session import get_sessionmaker
from yakudoku_core.settings import get_settings
from yakudoku_core.storage.s3 import S3Storage

from yakudoku_worker.latex_pdf import (
    LatexPdfBuildError,
    build_latex_translation_pdfs_if_ready,
)

_ORIGINAL_PDF_KINDS = ("pdf", "arxiv_pdf", "pdf_upload", "extension_capture")
_LATEX_SOURCE_KINDS = ("arxiv_latex", "latex")


async def _candidate_set_ids(
    session: AsyncSession, *, paper_id: str | None, limit: int | None
) -> list[str]:
    translated_exists = (
        select(SourceAsset.id)
        .where(
            SourceAsset.paper_id == DocumentRevision.paper_id,
            SourceAsset.source_version == DocumentRevision.source_version,
            SourceAsset.kind == "translated_pdf",
        )
        .exists()
    )
    bilingual_exists = (
        select(SourceAsset.id)
        .where(
            SourceAsset.paper_id == DocumentRevision.paper_id,
            SourceAsset.source_version == DocumentRevision.source_version,
            SourceAsset.kind == "bilingual_pdf",
        )
        .exists()
    )
    original_pdf_exists = (
        select(SourceAsset.id)
        .where(
            SourceAsset.paper_id == DocumentRevision.paper_id,
            SourceAsset.source_version == DocumentRevision.source_version,
            SourceAsset.kind.in_(_ORIGINAL_PDF_KINDS),
        )
        .exists()
    )
    latex_asset_exists = (
        select(SourceAsset.id)
        .where(
            SourceAsset.paper_id == DocumentRevision.paper_id,
            SourceAsset.source_version == DocumentRevision.source_version,
            SourceAsset.kind.in_(_LATEX_SOURCE_KINDS),
        )
        .exists()
    )
    stmt = (
        select(TranslationSet.id)
        .join(DocumentRevision, DocumentRevision.id == TranslationSet.revision_id)
        .where(
            TranslationSet.status == "complete",
            DocumentRevision.source_format == "latex",
            latex_asset_exists,
            (~translated_exists) | (original_pdf_exists & ~bilingual_exists),
        )
        .order_by(TranslationSet.updated_at.desc())
    )
    if paper_id:
        stmt = stmt.where(DocumentRevision.paper_id == paper_id)
    if limit is not None:
        stmt = stmt.limit(limit)
    return [str(row[0]) for row in (await session.execute(stmt)).all()]


async def backfill_latex_translation_pdfs(
    *, paper_id: str | None = None, limit: int | None = None
) -> int:
    """Build missing translated/bilingual PDFs. Returns the number of built sets."""

    settings = get_settings()
    maker = get_sessionmaker()
    storage = S3Storage(settings)
    built = 0
    async with maker() as session:
        set_ids = await _candidate_set_ids(session, paper_id=paper_id, limit=limit)
        if not set_ids:
            print("No missing LaTeX translated PDFs.")
            return 0
        print(f"Found {len(set_ids)} completed LaTeX translation set(s) missing PDFs.")
        for set_id in set_ids:
            try:
                outcome = await build_latex_translation_pdfs_if_ready(
                    session,
                    storage,
                    settings,
                    set_id=set_id,
                )
            except LatexPdfBuildError as exc:
                print(f"WARN set={set_id} failed code={exc.kind} detail={exc.detail}")
                continue
            if outcome.built:
                built += 1
                print(
                    "built "
                    f"set={set_id} translated={outcome.translated_key} "
                    f"bilingual={outcome.bilingual_key}"
                )
            else:
                print(f"skip set={set_id} reason={outcome.skipped_reason}")
    return built


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-id", default=None)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    asyncio.run(backfill_latex_translation_pdfs(paper_id=args.paper_id, limit=args.limit))


if __name__ == "__main__":
    main()
