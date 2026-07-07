"""glossaries ルータ — 用語集 3 層 CRUD・promote・訳語変更の影響再翻訳(plans/03 §7.9)。

訳語統一の内部機構(語彙帳とは別物)。認証はすべて `session`。

- scope=global は運営管理・読み取り専用(書き込みは 403)。
- scope=user / paper の CRUD は :mod:`yakudoku_core.translation.glossary` に委譲する。
- PATCH の `dry_run` は「影響ブロック数だけ返す(副作用なし)」/「実適用 + 影響ブロックのみ
  再翻訳ジョブ enqueue」を切り替える(plans/06 §8.3-§8.4)。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import Glossary, GlossaryTerm, LibraryItem, User
from yakudoku_core.jobs.store import JobStore
from yakudoku_core.translation import glossary as glossary_core
from yakudoku_core.translation.pipeline import resolve_display_units

from yakudoku_api.deps import CurrentUser, DbDep
from yakudoku_api.errors import ProblemException
from yakudoku_api.schemas.glossaries import (
    GlossaryDryRunResponse,
    GlossaryPatchResponse,
    GlossaryPromoteResponse,
    GlossaryTermCreateRequest,
    GlossaryTermItem,
    GlossaryTermPatchRequest,
    GlossaryTermsListResponse,
)

router = APIRouter(tags=["glossaries"])

_ON_DEMAND_PRIORITY = 100  # plans/06 §3.1: 訳語変更起因の再翻訳は yk:interactive 相当


def _to_item(term: GlossaryTerm, glossary: Glossary) -> GlossaryTermItem:
    return GlossaryTermItem(
        id=str(term.id),
        scope=glossary.scope,
        library_item_id=str(glossary.library_item_id) if glossary.scope == "paper" else None,
        source_term=term.source_term,
        target_term=term.target_term,
        pos_label=term.pos_label or None,
        policy=term.policy,
        auto_extracted=term.auto_extracted,
    )


async def _owned_library_item(db: AsyncSession, library_item_id: str, user: User) -> LibraryItem:
    item = await db.get(LibraryItem, library_item_id)
    if item is None or str(item.user_id) != str(user.id):
        raise ProblemException("not_found")
    return item


def _check_user_glossary_owner(glossary: Glossary, user: User) -> None:
    """scope=user の用語集が自分のものかを検証する(呼び出し側で scope=user と確定済み)。"""
    if str(glossary.user_id) != str(user.id):
        raise ProblemException("forbidden")


# --- §7.9 GET 一覧 ------------------------------------------------------------------


@router.get(
    "/api/glossary/terms",
    response_model=GlossaryTermsListResponse,
    operation_id="glossary_list_terms",
)
async def list_terms(
    scope: Literal["user", "paper"],
    user: CurrentUser,
    db: DbDep,
    library_item_id: str | None = None,
) -> GlossaryTermsListResponse:
    li_id: str | None = None
    if scope == "paper":
        if not library_item_id:
            raise ProblemException(
                "validation_error", detail="scope=paper には library_item_id が必要です"
            )
        await _owned_library_item(db, library_item_id, user)
        li_id = library_item_id

    rows = await glossary_core.list_terms(
        db,
        user_id=str(user.id) if scope == "user" else None,
        library_item_id=li_id,
    )
    return GlossaryTermsListResponse(items=[_to_item(term, gl) for term, gl in rows])


# --- §7.9 POST 作成 -----------------------------------------------------------------


@router.post(
    "/api/glossary/terms",
    response_model=GlossaryTermItem,
    status_code=status.HTTP_201_CREATED,
    operation_id="glossary_create_term",
)
async def create_term(
    body: GlossaryTermCreateRequest, user: CurrentUser, db: DbDep
) -> GlossaryTermItem:
    if body.scope == "global":
        raise ProblemException("forbidden")
    library_item_id: str | None = None
    user_id: str | None = None
    if body.scope == "paper":
        if not body.library_item_id:
            raise ProblemException(
                "validation_error", detail="scope=paper には library_item_id が必要です"
            )
        await _owned_library_item(db, body.library_item_id, user)
        library_item_id = body.library_item_id
    else:
        user_id = str(user.id)

    try:
        term = await glossary_core.create_term(
            db,
            scope=body.scope,
            source_term=body.source_term,
            target_term=body.target_term,
            policy=body.policy,
            user_id=user_id,
            library_item_id=library_item_id,
        )
    except glossary_core.DuplicateTermError as exc:
        raise ProblemException("duplicate", detail=str(exc)) from exc

    glossary = await glossary_core.get_glossary(
        db, scope=body.scope, user_id=user_id, library_item_id=library_item_id
    )
    assert glossary is not None
    await db.commit()
    return _to_item(term, glossary)


# --- §7.9 PATCH 更新(dry_run 影響数 / 実適用) --------------------------------------


async def _resolve_term_or_404(db: AsyncSession, term_id: str) -> tuple[GlossaryTerm, Glossary]:
    found = await glossary_core.get_term(db, term_id)
    if found is None:
        raise ProblemException("not_found")
    return found


async def _affected_by_revision(
    db: AsyncSession, term: GlossaryTerm, contexts: list[dict[str, str]]
) -> dict[str, tuple[list[str], dict[str, str]]]:
    """revision_id -> (再翻訳対象 block_id 一覧, その revision の適用コンテキスト)。

    state='edited'/'protected' の unit は除外する(plans/06 §8.4-4。上書きしない)。
    """
    out: dict[str, tuple[list[str], dict[str, str]]] = {}
    for ctx in contexts:
        candidates = await glossary_core.find_affected_blocks(
            db, revision_id=ctx["revision_id"], source_term=term.source_term
        )
        if not candidates:
            continue
        units = await resolve_display_units(db, ctx["revision_id"], "natural", ctx["user_id"])
        eligible = [
            bid
            for bid in candidates
            if units.get(bid) is None or units[bid].state not in ("edited", "protected")
        ]
        if eligible:
            out[ctx["revision_id"]] = (eligible, ctx)
    return out


@router.patch(
    "/api/glossary/terms/{term_id}",
    operation_id="glossary_patch_term",
)
async def patch_term(
    term_id: str,
    body: GlossaryTermPatchRequest,
    user: CurrentUser,
    db: DbDep,
    response: Response,
    dry_run: bool = False,
) -> GlossaryDryRunResponse | GlossaryPatchResponse:
    term, glossary = await _resolve_term_or_404(db, term_id)
    if glossary.scope == "global":
        raise ProblemException("forbidden")
    if glossary.scope == "paper":
        await _owned_library_item(db, str(glossary.library_item_id), user)
    else:
        _check_user_glossary_owner(glossary, user)

    contexts = await glossary_core.target_contexts_for_glossary(db, glossary)
    affected = await _affected_by_revision(db, term, contexts)
    total_affected = sum(len(block_ids) for block_ids, _ctx in affected.values())

    if dry_run:
        # plans/03 §7.9: dry_run=true は 200(副作用なし)。
        return GlossaryDryRunResponse(affected_block_count=total_affected)

    response.status_code = status.HTTP_202_ACCEPTED

    term = await glossary_core.update_term(
        db, term, target_term=body.target_term, policy=body.policy
    )

    job_id: str | None = None
    store = JobStore(db)
    for revision_id, (block_ids, ctx) in affected.items():
        personal = await glossary_core.resolve_or_create_personal_set(
            db, revision_id=revision_id, style="natural", user_id=ctx["user_id"]
        )
        snapshot, _hash = await glossary_core.build_snapshot(
            db, user_id=ctx["user_id"], library_item_id=ctx["library_item_id"], shared=False
        )
        personal.glossary_snapshot = snapshot
        await db.flush()
        jid = await store.enqueue(
            kind="translation",
            priority=_ON_DEMAND_PRIORITY,
            user_id=ctx["user_id"],
            paper_id=ctx["paper_id"],
            library_item_id=ctx["library_item_id"],
            idempotency_key=f"glossary:{term.id}:{revision_id}",
            payload={
                "set_id": str(personal.id),
                "block_ids": block_ids,
                "reason": "glossary_change",
                "term_id": str(term.id),
            },
        )
        if job_id is None:
            job_id = jid

    await db.commit()
    return GlossaryPatchResponse(
        term=_to_item(term, glossary),
        affected_block_count=total_affected,
        job_id=job_id,
    )


# --- §7.9 DELETE ---------------------------------------------------------------------


@router.delete(
    "/api/glossary/terms/{term_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="glossary_delete_term",
)
async def delete_term(term_id: str, user: CurrentUser, db: DbDep) -> Response:
    term, glossary = await _resolve_term_or_404(db, term_id)
    if glossary.scope == "global":
        raise ProblemException("forbidden")
    if glossary.scope == "paper":
        await _owned_library_item(db, str(glossary.library_item_id), user)
    else:
        _check_user_glossary_owner(glossary, user)
    await glossary_core.delete_term(db, term)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- §7.9 promote(論文ローカル→ユーザー昇格) -----------------------------------------


@router.post(
    "/api/glossary/terms/{term_id}/promote",
    response_model=GlossaryPromoteResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="glossary_promote_term",
)
async def promote_term(term_id: str, user: CurrentUser, db: DbDep) -> GlossaryPromoteResponse:
    term, glossary = await _resolve_term_or_404(db, term_id)
    if glossary.scope != "paper":
        raise ProblemException("conflict", detail="promote は scope=paper の語のみ対象です")
    await _owned_library_item(db, str(glossary.library_item_id), user)
    promoted = await glossary_core.promote_term(db, term, user_id=str(user.id))
    user_glossary = await glossary_core.get_glossary(db, scope="user", user_id=str(user.id))
    assert user_glossary is not None
    await db.commit()
    return GlossaryPromoteResponse(term=_to_item(promoted, user_glossary))
