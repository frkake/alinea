"""translations — 翻訳セット取得・ユニット・優先繰り上げ・オンデマンド翻訳・再翻訳、
および読書位置の保存(plans/03 §7・§5.8)。認証はすべて `session`。

翻訳系ジョブは DB では `jobs.kind='translation'` 1 種で、用途は `payload.reason` で判別する
(plans/06 §3.1)。API はジョブを作成して job_id を返すところまでを担い、実行は worker(§21)。
"""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any, Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import (
    DocumentRevision,
    LibraryItem,
    Paper,
    TranslationSet,
    TranslationUnit,
    User,
)
from yakudoku_core.document.blocks import Block, DocumentContent, Section
from yakudoku_core.document.plaintext import block_to_plain
from yakudoku_core.jobs.store import JobStore
from yakudoku_core.translation import glossary as glossary_core
from yakudoku_core.translation.glossary import glossary_hash
from yakudoku_core.translation.pipeline import (
    BLOCKING_FLAGS,
    TRANSLATABLE_BLOCK_TYPES,
    compute_progress,
    compute_translation_scope,
    resolve_display_units,
    run_quality_checks,
)
from yakudoku_core.translation.placeholder import encode_block

from yakudoku_api.deps import CurrentUser, DbDep
from yakudoku_api.errors import ProblemException
from yakudoku_api.routers.viewer import (
    resolve_accessible_revision,
    resolve_owned_library_item,
)
from yakudoku_api.schemas.viewer import (
    PositionRequest,
    PositionResponse,
    PrioritizeRequest,
    PrioritizeResponse,
    RetranslateRequest,
    RetranslateResponse,
    SectionTranslateRequest,
    SectionTranslateResponse,
    TranslationSetItem,
    TranslationsListResponse,
    TranslationUnitItem,
    UnitProposal,
    UnitsResponse,
)

router = APIRouter(tags=["translations"])

_ON_DEMAND_PRIORITY = 100  # plans/06 §3.1: オンデマンド系は作成時 priority=100(yk:interactive)


# --- 解決ヘルパ ---------------------------------------------------------------------


def _as_content(revision: DocumentRevision) -> DocumentContent:
    return DocumentContent.model_validate(revision.content)


def _find_section(content: DocumentContent, section_id: str) -> Section | None:
    def walk(sec: Section) -> Section | None:
        if sec.id == section_id:
            return sec
        for sub in sec.sections:
            found = walk(sub)
            if found is not None:
                return found
        return None

    for top in content.sections:
        found = walk(top)
        if found is not None:
            return found
    return None


async def _set_with_access(
    db: AsyncSession, set_id: str, user: User
) -> tuple[TranslationSet, DocumentRevision, Paper]:
    tset = await db.get(TranslationSet, set_id)
    if tset is None:
        raise ProblemException("not_found")
    revision, paper = await resolve_accessible_revision(db, str(tset.revision_id), user)
    return tset, revision, paper


async def _user_library_item_id(db: AsyncSession, user: User, paper_id: str) -> str | None:
    li = await db.scalar(
        select(LibraryItem.id).where(
            LibraryItem.user_id == user.id, LibraryItem.paper_id == paper_id
        )
    )
    return str(li) if li else None


async def _effective_set_id(
    db: AsyncSession, revision_id: str, style: str, user_id: str
) -> TranslationSet | None:
    rows = (
        (
            await db.execute(
                select(TranslationSet).where(
                    TranslationSet.revision_id == revision_id,
                    TranslationSet.style == style,
                    or_(TranslationSet.scope == "shared", TranslationSet.user_id == user_id),
                )
            )
        )
        .scalars()
        .all()
    )
    personal = next((s for s in rows if s.scope == "personal"), None)
    return personal or next((s for s in rows if s.scope == "shared"), None)


# --- §7.1 翻訳セット一覧 ------------------------------------------------------------


@router.get(
    "/api/revisions/{revision_id}/translations",
    response_model=TranslationsListResponse,
    operation_id="translations_list_sets",
)
async def list_translation_sets(
    revision_id: str, user: CurrentUser, db: DbDep
) -> TranslationsListResponse:
    revision, _paper = await resolve_accessible_revision(db, revision_id, user)
    content = _as_content(revision)
    in_scope = set(compute_translation_scope(content).in_scope_block_ids)

    sets = (
        (
            await db.execute(
                select(TranslationSet).where(
                    TranslationSet.revision_id == revision_id,
                    or_(
                        TranslationSet.scope == "shared",
                        TranslationSet.user_id == user.id,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )

    items: list[TranslationSetItem] = []
    for tset in sets:
        rows = (
            await db.execute(
                select(TranslationUnit.block_id, TranslationUnit.quality_flags).where(
                    TranslationUnit.set_id == tset.id
                )
            )
        ).all()
        scoped = [{"quality_flags": flags} for (bid, flags) in rows if bid in in_scope]
        items.append(
            TranslationSetItem(
                set_id=str(tset.id),
                style=tset.style,
                scope=tset.scope,
                status=tset.status,
                progress_pct=compute_progress(scoped, len(in_scope)),
                glossary_snapshot_id=glossary_hash(list(tset.glossary_snapshot or [])),
            )
        )
    return TranslationsListResponse(items=items)


# --- §7.2 翻訳ユニット --------------------------------------------------------------


@router.get(
    "/api/revisions/{revision_id}/translations/{style}/units",
    response_model=UnitsResponse,
    operation_id="translations_list_units",
)
async def list_units(
    revision_id: str,
    style: str,
    section_id: str,
    user: CurrentUser,
    db: DbDep,
) -> UnitsResponse:
    if style not in ("natural", "literal"):
        raise ProblemException("validation_error", detail="style は natural / literal のみ")
    revision, _paper = await resolve_accessible_revision(db, revision_id, user)
    tset = await _effective_set_id(db, revision_id, style, str(user.id))
    if tset is None:
        raise ProblemException("not_found")

    content = _as_content(revision)
    section = _find_section(content, section_id)
    if section is None:
        raise ProblemException("not_found")
    section_block_ids = [b.id for b in section.blocks]

    units = await resolve_display_units(db, revision_id, style, str(user.id))
    items: list[TranslationUnitItem] = []
    for bid in section_block_ids:
        unit = units.get(bid)
        if unit is None:
            continue  # ユニット未生成のブロックはクライアントが原文で合成する(§7.2)
        flags = list(unit.quality_flags or [])
        blocked = bool(set(flags) & BLOCKING_FLAGS)
        proposal = None
        if isinstance(unit.proposal, dict) and unit.proposal:
            proposal = UnitProposal(
                text_ja=str(unit.proposal.get("text_ja", "")),
                generated_at=str(unit.proposal.get("generated_at", "")),
                model=str(unit.proposal.get("model", "")),
            )
        items.append(
            TranslationUnitItem(
                unit_id=str(unit.id),
                block_id=bid,
                text_ja=None if blocked else unit.text_ja,
                state=unit.state,
                quality_flags=flags,
                proposal=proposal,
            )
        )
    return UnitsResponse(set_id=str(tset.id), items=items)


# --- §7.3 直訳のオンデマンド生成開始(plans/06 §10.2) ------------------------------


class LiteralTranslationRequest(BaseModel):
    style: Literal["literal"]
    priority_section_id: str | None = None


class LiteralTranslationResponse(BaseModel):
    set_id: str
    job_id: str | None


async def _create_literal_set(
    db: AsyncSession, revision: DocumentRevision, paper: Paper, user: User
) -> TranslationSet:
    """style='literal' の TranslationSet を確保する(plans/06 §10.2 手順2・§9.2 の scope 決定)。

    public 論文は shared(全ユーザー共通)、private は personal。用語スナップショットは
    §8.1 の 3 層マージ(shared 構築時は global のみ)。一意インデックス
    (``uq_translation_sets_shared`` / ``uq_translation_sets_personal``)への競合は
    既存行を再取得して返す(worker 側 ``_ensure_translation_set`` と同方針)。
    """
    revision_id = str(revision.id)
    library_item_id = await _user_library_item_id(db, user, str(paper.id))
    shared = paper.visibility == "public"
    snapshot, _ghash = await glossary_core.build_snapshot(
        db, user_id=str(user.id), library_item_id=library_item_id, shared=shared
    )
    tset = TranslationSet(
        revision_id=revision_id,
        style="literal",
        scope="shared" if shared else "personal",
        user_id=None if shared else str(user.id),
        glossary_snapshot=snapshot,
        status="pending",
    )
    db.add(tset)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _effective_set_id(db, revision_id, "literal", str(user.id))
        if existing is None:
            raise
        return existing
    return tset


@router.post(
    "/api/revisions/{revision_id}/translations",
    response_model=LiteralTranslationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="translations_start_literal",
)
async def start_literal_translation(
    revision_id: str,
    body: LiteralTranslationRequest,
    user: CurrentUser,
    db: DbDep,
    response: Response,
) -> LiteralTranslationResponse:
    revision, paper = await resolve_accessible_revision(db, revision_id, user)

    tset = await _effective_set_id(db, revision_id, "literal", str(user.id))
    if tset is not None and tset.status == "complete":
        # 2 回目以降の切替は即時(plans/06 §10.2 手順1)。
        response.status_code = status.HTTP_200_OK
        return LiteralTranslationResponse(set_id=str(tset.id), job_id=None)
    if tset is None:
        tset = await _create_literal_set(db, revision, paper, user)

    content = _as_content(revision)
    scope = compute_translation_scope(content)
    library_item_id = await _user_library_item_id(db, user, str(paper.id))

    store = JobStore(db)
    job_ids: dict[str, str] = {}
    for sec in scope.sections:
        section_id = str(sec["section_id"])
        block_ids = list(sec["block_ids"])
        # 表示中セクション優先(yk:interactive 相当)。残りはセクション順(yk:bulk 相当。§10.2 手順3)。
        priority = _ON_DEMAND_PRIORITY if section_id == body.priority_section_id else 0
        job_ids[section_id] = await store.enqueue(
            kind="translation",
            priority=priority,
            user_id=str(user.id),
            paper_id=str(paper.id),
            library_item_id=library_item_id,
            idempotency_key=f"xlate:{tset.id}:{section_id}",
            payload={
                "set_id": str(tset.id),
                "section_id": section_id,
                "block_ids": block_ids,
                "reason": "literal",
                "table_block_id": None,
            },
        )

    representative = (
        job_ids.get(body.priority_section_id) if body.priority_section_id else None
    ) or next(iter(job_ids.values()), None)
    return LiteralTranslationResponse(set_id=str(tset.id), job_id=representative)


# --- §7.4 開いたセクションを優先翻訳 -----------------------------------------------


async def _bump_priority(db: AsyncSession, set_ids: list[str], section_id: str) -> list[str]:
    """該当する queued な翻訳ジョブの priority を +100 する(plans/06 §10.1)。返り値=job_id 列。"""
    if not set_ids:
        return []
    result = await db.execute(
        text(
            "UPDATE jobs SET priority = priority + 100 "
            "WHERE kind = 'translation' AND status = 'queued' "
            "AND payload->>'set_id' = ANY(:set_ids) "
            "AND payload->>'section_id' = :section_id "
            "RETURNING id"
        ),
        {"set_ids": set_ids, "section_id": section_id},
    )
    job_ids = [str(r[0]) for r in result.all()]
    await db.commit()
    return job_ids


@router.post(
    "/api/translation-sets/{set_id}/prioritize",
    response_model=PrioritizeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="translations_prioritize",
)
async def prioritize(
    set_id: str, body: PrioritizeRequest, user: CurrentUser, db: DbDep, response: Response
) -> PrioritizeResponse:
    tset, _revision, _paper = await _set_with_access(db, set_id, user)
    await _bump_priority(db, [str(tset.id)], body.section_id)
    # 該当ジョブなし(実行中・完了・対象外)は 202 のまま no-op(§10.1)。
    response.status_code = status.HTTP_202_ACCEPTED
    return PrioritizeResponse(ok=True)


# --- §7.5 付録等のオンデマンド翻訳 -------------------------------------------------


@router.post(
    "/api/translation-sets/{set_id}/sections/{section_id}/translate",
    response_model=SectionTranslateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="translations_section_translate",
)
async def section_translate(
    set_id: str,
    section_id: str,
    body: SectionTranslateRequest,
    user: CurrentUser,
    db: DbDep,
) -> SectionTranslateResponse:
    tset, revision, paper = await _set_with_access(db, set_id, user)
    content = _as_content(revision)
    section = _find_section(content, section_id)
    if section is None:
        raise ProblemException("not_found")

    if body.block_id is not None:
        reason = "table"
        block_ids = [body.block_id]
        table_block_id: str | None = body.block_id
    else:
        reason = "on_demand"
        # 付録スコープ外なので type 条件のみで対象を再計算する(§10.3)。
        block_ids = [b.id for b in section.blocks if b.type in TRANSLATABLE_BLOCK_TYPES]
        table_block_id = None

    store = JobStore(db)
    job_id = await store.enqueue(
        kind="translation",
        priority=_ON_DEMAND_PRIORITY,
        user_id=str(user.id),
        paper_id=str(paper.id),
        library_item_id=await _user_library_item_id(db, user, str(paper.id)),
        idempotency_key=f"xlate:{set_id}:{section_id}",
        payload={
            "set_id": str(tset.id),
            "section_id": section_id,
            "block_ids": block_ids,
            "reason": reason,
            "table_block_id": table_block_id,
        },
    )
    return SectionTranslateResponse(job_id=job_id)


# --- §7.6 再翻訳(指示なし。指示つきは M1 で拡張) --------------------------------


def _blocks_hash(block_ids: list[str]) -> str:
    payload = ",".join(sorted(block_ids))
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()


@router.post(
    "/api/translation-units/{unit_id}/retranslate",
    response_model=RetranslateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="translations_retranslate",
)
async def retranslate(
    unit_id: str, body: RetranslateRequest, user: CurrentUser, db: DbDep
) -> RetranslateResponse:
    try:
        unit = await db.get(TranslationUnit, int(unit_id))
    except ValueError:
        raise ProblemException("not_found") from None
    if unit is None:
        raise ProblemException("not_found")
    tset, _revision, paper = await _set_with_access(db, str(unit.set_id), user)

    # state=edited への再翻訳は discard_edit 必須(409 conflict / detail edit_protected)。
    if unit.state == "edited" and not body.discard_edit:
        raise ProblemException(
            "conflict",
            detail="edit_protected: 編集済みユニットの再翻訳には discard_edit=true が必要です",
        )

    instruction = (body.instruction or "").strip()
    reason = "instructed" if instruction else "retranslate"
    instruction_hash = (
        hashlib.blake2b(instruction.encode("utf-8"), digest_size=8).hexdigest()
        if instruction
        else "0"
    )

    store = JobStore(db)
    job_id = await store.enqueue(
        kind="translation",
        priority=_ON_DEMAND_PRIORITY,
        user_id=str(user.id),
        paper_id=str(paper.id),
        library_item_id=await _user_library_item_id(db, user, str(paper.id)),
        idempotency_key=f"rexlate:{tset.id}:{_blocks_hash([unit.block_id])}:{instruction_hash}",
        payload={
            "set_id": str(tset.id),
            "block_ids": [unit.block_id],
            "unit_id": str(unit.id),
            "reason": reason,
            "instruction": instruction,
        },
    )
    return RetranslateResponse(job_id=job_id)


# --- §7.7 手動編集 -------------------------------------------------------------------


class UnitEditRequest(BaseModel):
    text_ja: str


class UnitEditResponse(BaseModel):
    unit_id: str
    state: str
    text_ja: str
    set_id: str


async def _get_unit_or_404(db: AsyncSession, unit_id: str) -> TranslationUnit:
    try:
        unit = await db.get(TranslationUnit, int(unit_id))
    except ValueError:
        raise ProblemException("not_found") from None
    if unit is None:
        raise ProblemException("not_found")
    return unit


async def _ensure_personal_unit(
    db: AsyncSession, tset: TranslationSet, unit: TranslationUnit, user: User
) -> tuple[TranslationSet, TranslationUnit]:
    """共有セットへの書き込みは personal フォーク+ユニット複製を用意する(plans/06 §9.2)。

    ``tset.scope != "shared"`` ならそのまま返す(すでに自分の personal セット)。
    """
    if tset.scope != "shared":
        return tset, unit
    personal_set = await glossary_core.resolve_or_create_personal_set(
        db, revision_id=str(tset.revision_id), style=tset.style, user_id=str(user.id)
    )
    existing = await db.scalar(
        select(TranslationUnit).where(
            TranslationUnit.set_id == personal_set.id, TranslationUnit.block_id == unit.block_id
        )
    )
    if existing is not None:
        return personal_set, existing
    forked = TranslationUnit(
        set_id=str(personal_set.id),
        block_id=unit.block_id,
        source_hash=unit.source_hash,
        content_ja=unit.content_ja,
        text_ja=unit.text_ja,
        state=unit.state,
        quality_flags=list(unit.quality_flags or []),
        proposal=unit.proposal,
        model=unit.model,
    )
    db.add(forked)
    await db.flush()
    return personal_set, forked


@router.put(
    "/api/translation-units/{unit_id}",
    response_model=UnitEditResponse,
    operation_id="translations_edit_unit",
)
async def edit_unit(
    unit_id: str, body: UnitEditRequest, user: CurrentUser, db: DbDep
) -> UnitEditResponse:
    unit = await _get_unit_or_404(db, unit_id)
    tset, _revision, _paper = await _set_with_access(db, str(unit.set_id), user)
    target_set, target_unit = await _ensure_personal_unit(db, tset, unit, user)

    # 手動編集はプレースホルダ構造を失う(単一の text インラインとして保存。plans/06 §11.3)。
    # content_ja の実体は Inline[] | TableTranslationJson(plans/02 §3.2・plans/06 §10.4)で
    # モデルの dict[str, Any] 注釈は簡略化されている。
    target_unit.text_ja = body.text_ja
    target_unit.content_ja = [{"t": "text", "v": body.text_ja}]  # type: ignore[assignment]
    target_unit.state = "edited"
    target_unit.quality_flags = []
    await db.commit()
    return UnitEditResponse(
        unit_id=str(target_unit.id),
        state="edited",
        text_ja=target_unit.text_ja,
        set_id=str(target_set.id),
    )


# --- §7.8 proposal の採用・破棄 ------------------------------------------------------


class ProposalAcceptResponse(BaseModel):
    unit_id: str
    text_ja: str
    state: str


def _find_block(content: DocumentContent, block_id: str) -> Block | None:
    for _sec, blk in content.iter_blocks():
        if blk.id == block_id:
            return blk
    return None


def _recompute_quality_flags(
    revision: DocumentRevision, block_id: str, text_ja: str, snapshot: list[Any]
) -> list[str]:
    """proposal 採用時の品質フラグ再計算(plans/06 §11.2)。プレースホルダ検証は対象外(§11.1 で
    保存前に済んでいるため)。ブロックが見つからない場合はフラグなしとする。
    """
    block = _find_block(_as_content(revision), block_id)
    if block is None:
        return []
    encoded = encode_block(block.model_dump())
    source_plain = block_to_plain(block)
    return run_quality_checks(encoded, source_plain, text_ja, snapshot)


@router.post(
    "/api/translation-units/{unit_id}/proposal/accept",
    response_model=ProposalAcceptResponse,
    operation_id="translations_accept_proposal",
)
async def accept_proposal(unit_id: str, user: CurrentUser, db: DbDep) -> ProposalAcceptResponse:
    unit = await _get_unit_or_404(db, unit_id)
    tset, revision, _paper = await _set_with_access(db, str(unit.set_id), user)

    proposal = unit.proposal
    if not isinstance(proposal, dict) or not proposal:
        raise ProblemException("not_found", detail="採用可能な proposal がありません")

    target_set, target_unit = await _ensure_personal_unit(db, tset, unit, user)
    text_ja = str(proposal.get("text_ja", ""))
    content_ja = proposal.get("content_ja")
    target_unit.text_ja = text_ja
    if content_ja is not None:
        target_unit.content_ja = content_ja
    target_unit.state = "machine"
    if proposal.get("model"):
        target_unit.model = str(proposal["model"])
    target_unit.quality_flags = _recompute_quality_flags(
        revision, unit.block_id, text_ja, list(target_set.glossary_snapshot or [])
    )
    target_unit.proposal = None
    await db.commit()
    return ProposalAcceptResponse(
        unit_id=str(target_unit.id), text_ja=target_unit.text_ja, state=target_unit.state
    )


@router.delete(
    "/api/translation-units/{unit_id}/proposal",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="translations_discard_proposal",
)
async def discard_proposal(unit_id: str, user: CurrentUser, db: DbDep) -> Response:
    unit = await _get_unit_or_404(db, unit_id)
    await _set_with_access(db, str(unit.set_id), user)
    unit.proposal = None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- §5.8 読書位置の自動保存 -------------------------------------------------------


@router.put(
    "/api/library-items/{item_id}/position",
    response_model=PositionResponse,
    operation_id="library_items_save_position",
)
async def save_position(
    item_id: str, body: PositionRequest, user: CurrentUser, db: DbDep
) -> PositionResponse:
    item = await resolve_owned_library_item(db, item_id, user)
    item.reading_position = {
        "revision_id": body.revision_id,
        "block_id": body.block_id,
        "view_mode": body.mode,
    }
    saved_at = dt.datetime.now(dt.UTC)
    await db.commit()

    # 副作用(§10.1-a): 位置のセクションを解決し、当該リビジョンの queued 翻訳ジョブを繰り上げる。
    await _prioritize_from_position(db, body.revision_id, body.block_id)
    return PositionResponse(saved_at=saved_at.isoformat())


async def _prioritize_from_position(db: AsyncSession, revision_id: str, block_id: str) -> None:
    revision = await db.get(DocumentRevision, revision_id)
    if revision is None:
        return
    content = _as_content(revision)
    section_id: str | None = None
    for sec, blk in content.iter_blocks():
        if blk.id == block_id:
            section_id = sec.id
            break
    if section_id is None:
        return
    set_ids = [
        str(sid)
        for sid in (
            await db.execute(
                select(TranslationSet.id).where(TranslationSet.revision_id == revision_id)
            )
        )
        .scalars()
        .all()
    ]
    await _bump_priority(db, set_ids, section_id)
