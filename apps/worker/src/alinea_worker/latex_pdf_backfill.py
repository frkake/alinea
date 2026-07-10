"""Build missing or stale translated PDFs for completed LaTeX translation sets."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from collections.abc import Sequence
from typing import Any

from alinea_core.db.models import DocumentRevision, SourceAsset, TranslationSet, TranslationUnit
from alinea_core.db.session import get_sessionmaker
from alinea_core.settings import get_settings
from alinea_core.storage.s3 import S3Storage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_worker.latex_pdf import (
    PDF_BUILD_VERSION,
    LatexPdfBuildError,
    _translation_units_digest,
    build_latex_translation_pdfs_if_ready,
)

_LATEX_SOURCE_KINDS = ("arxiv_latex", "latex")
_FAILURE_DETAIL_LIMIT = 1200


async def _candidate_set_ids(
    session: AsyncSession, *, paper_id: str | None, limit: int | None
) -> list[str]:
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
        select(TranslationSet.id, TranslationSet.style, DocumentRevision.stats)
        .join(DocumentRevision, DocumentRevision.id == TranslationSet.revision_id)
        .where(
            TranslationSet.status == "complete",
            TranslationSet.scope == "shared",
            DocumentRevision.source_format == "latex",
            latex_asset_exists,
        )
        .order_by(TranslationSet.updated_at.desc())
    )
    if paper_id:
        stmt = stmt.where(DocumentRevision.paper_id == paper_id)
    if limit is not None:
        stmt = stmt.limit(limit)
    candidates: list[str] = []
    for set_id, style, stats in (await session.execute(stmt)).all():
        units = {
            unit.block_id: unit
            for unit in (
                await session.execute(
                    select(TranslationUnit).where(TranslationUnit.set_id == set_id)
                )
            ).scalars()
        }
        translation_digest = _translation_units_digest(units)
        success = ((stats or {}).get("translated_pdf") or {}).get(style)
        if (
            isinstance(success, dict)
            and success.get("build_version") == PDF_BUILD_VERSION
            and success.get("translation_set_id") == str(set_id)
            and success.get("translation_digest") == translation_digest
        ):
            continue
        failures = (stats or {}).get("translated_pdf_failures") or {}
        failure = failures.get(style)
        if (
            isinstance(failure, dict)
            and failure.get("build_version") == PDF_BUILD_VERSION
            and failure.get("translation_set_id") == str(set_id)
            and failure.get("translation_digest") == translation_digest
        ):
            continue
        candidates.append(str(set_id))
    return candidates


async def _record_build_failure(
    session: AsyncSession, set_id: str, exc: LatexPdfBuildError
) -> None:
    tset = await session.get(TranslationSet, set_id)
    if tset is None:
        return
    revision = await session.get(DocumentRevision, str(tset.revision_id))
    if revision is None:
        return
    stats = dict(revision.stats or {})
    failures: dict[str, Any] = dict(stats.get("translated_pdf_failures") or {})
    units = {
        unit.block_id: unit
        for unit in (
            await session.execute(select(TranslationUnit).where(TranslationUnit.set_id == set_id))
        ).scalars()
    }
    failures[tset.style] = {
        "build_version": PDF_BUILD_VERSION,
        "translation_set_id": set_id,
        "translation_digest": _translation_units_digest(units),
        "code": exc.kind,
        "detail": _compact_failure_detail(exc.detail),
        "failed_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    stats["translated_pdf_failures"] = failures
    revision.stats = stats
    await session.commit()


def _compact_failure_detail(detail: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in detail.items():
        if isinstance(value, str):
            compact[key] = value[-_FAILURE_DETAIL_LIMIT:]
        elif isinstance(value, list):
            compact[key] = value[:5]
        elif isinstance(value, dict):
            compact[key] = _compact_failure_detail(value)
        else:
            compact[key] = value
    return compact


async def backfill_latex_translation_pdfs(
    *, paper_id: str | None = None, limit: int | None = None
) -> int:
    """Build missing or stale translated PDFs. Returns the number of built sets."""

    settings = get_settings()
    maker = get_sessionmaker()
    storage = S3Storage(settings)
    built = 0
    async with maker() as session:
        set_ids = await _candidate_set_ids(session, paper_id=paper_id, limit=limit)
        if not set_ids:
            print("No missing or stale LaTeX translated PDFs.")
            return 0
        print(
            f"Found {len(set_ids)} completed LaTeX translation set(s) with missing or stale PDFs."
        )
        for set_id in set_ids:
            try:
                outcome = await build_latex_translation_pdfs_if_ready(
                    session,
                    storage,
                    settings,
                    set_id=set_id,
                )
            except LatexPdfBuildError as exc:
                await _record_build_failure(session, set_id, exc)
                print(f"WARN set={set_id} failed code={exc.kind} detail={exc.detail}")
                continue
            if outcome.built:
                built += 1
                print(f"built set={set_id} translated={outcome.translated_key}")
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
