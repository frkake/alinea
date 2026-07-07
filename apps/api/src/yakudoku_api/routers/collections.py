"""collections — コレクション CRUD・entries・共有リンク(plans/03 §13・docs/06 §4)。

- ``GET/POST/PATCH/DELETE /api/collections[/{id}]``: 一覧(作成日時昇順)・作成・部分更新・削除
  (削除は entries のみ。LibraryItem は残す。ON DELETE CASCADE で collection_entries /
  collection_share_tokens も自動削除)。
- entries(§13.2): 追加(末尾)・担当/発表時間/注記の編集・削除・全件並べ替え。
  ``order``(API 表現。1 始まり)は ``position`` 昇順(タイブレークは ``id``)で算出する
  導出値であり、``position`` 自体は必ずしも連番でなくてよい(削除で詰めない)。
- 共有(§13.3): token は 8 文字 ``[A-Za-z0-9]``(CSPRNG)。アクティブは常に最大 1 本
  (発行済みで再発行は 409)。revoke 後の再発行は新しい token(履行済み行は残し新規挿入)。
- ``CollectionEntryOut.library_item`` は library_items ルータの読み出し専用ヘルパ
  ``_summary_for`` を再利用して組み立てる(重複実装しない。notifications.py と同方針)。

main.py への ``app.include_router(collections.router)`` 登録は本タスクの所有外
(main.py は article レーンが編集する取り決め)。followups に登録依頼を明記する。
"""

from __future__ import annotations

import datetime as dt
import secrets
import string
import uuid

from fastapi import APIRouter, Response
from sqlalchemy import func, select
from yakudoku_core.db.models import (
    Collection,
    CollectionEntry,
    CollectionShareToken,
    LibraryItem,
)

from yakudoku_api.deps import CurrentUser, DbDep, SettingsDep
from yakudoku_api.errors import ProblemException
from yakudoku_api.routers.library_items import _summary_for
from yakudoku_api.schemas.collections import (
    CollectionCreateBody,
    CollectionDetailResponse,
    CollectionEntryOut,
    CollectionListItem,
    CollectionListResponse,
    CollectionPatchBody,
    CollectionProgress,
    EntriesOrderBody,
    EntryCreateBody,
    EntryPatchBody,
    OkResponse,
    ShareInfo,
    SharePatchBody,
)
from yakudoku_api.services.deadlines import days_left, today_jst
from yakudoku_api.settings import ApiSettings

router = APIRouter(tags=["collections"])

_TOKEN_ALPHABET = string.ascii_letters + string.digits


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _generate_share_token() -> str:
    """8 文字 ``[A-Za-z0-9]``(CSPRNG。plans/03 §13.3)。"""
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(8))


def _parse_date(value: str | None) -> dt.date | None:
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ProblemException(
            "validation_error", detail="deadline は YYYY-MM-DD 形式で指定してください"
        ) from exc


# --- 所有チェック -------------------------------------------------------------------
async def _get_owned_collection(db: DbDep, user_id: str, collection_id: str) -> Collection:
    if not _valid_uuid(collection_id):
        raise ProblemException("not_found")
    collection = await db.get(Collection, collection_id)
    if collection is None or str(collection.user_id) != str(user_id):
        raise ProblemException("not_found")
    return collection


async def _get_owned_entry(db: DbDep, user_id: str, entry_id: str) -> CollectionEntry:
    if not _valid_uuid(entry_id):
        raise ProblemException("not_found")
    entry = await db.get(CollectionEntry, entry_id)
    if entry is None:
        raise ProblemException("not_found")
    collection = await db.get(Collection, entry.collection_id)
    if collection is None or str(collection.user_id) != str(user_id):
        raise ProblemException("not_found")
    return entry


# --- 組み立てヘルパ ------------------------------------------------------------------
async def _ordered_entries(db: DbDep, collection_id: str) -> list[CollectionEntry]:
    rows = (
        await db.execute(
            select(CollectionEntry)
            .where(CollectionEntry.collection_id == collection_id)
            .order_by(CollectionEntry.position.asc(), CollectionEntry.id.asc())
        )
    ).scalars()
    return list(rows.all())


async def _progress_counts(db: DbDep, collection_id: str) -> tuple[int, int]:
    """(total, done) — done は ``library_item.status == 'done'`` の件数。"""
    row = (
        await db.execute(
            select(
                func.count(CollectionEntry.id),
                func.count(CollectionEntry.id).filter(LibraryItem.status == "done"),
            )
            .select_from(CollectionEntry)
            .outerjoin(LibraryItem, LibraryItem.id == CollectionEntry.library_item_id)
            .where(CollectionEntry.collection_id == collection_id)
        )
    ).one()
    return int(row[0]), int(row[1])


async def _included_note_count(db: DbDep, collection_id: str) -> int:
    """§13.3: ``one_line_note`` 非空のエントリ数(share.included_note_count)。"""
    return (
        await db.execute(
            select(func.count())
            .select_from(CollectionEntry)
            .join(LibraryItem, LibraryItem.id == CollectionEntry.library_item_id)
            .where(CollectionEntry.collection_id == collection_id, LibraryItem.one_line_note != "")
        )
    ).scalar_one()


async def _latest_share(db: DbDep, collection_id: str) -> CollectionShareToken | None:
    return (
        (
            await db.execute(
                select(CollectionShareToken)
                .where(CollectionShareToken.collection_id == collection_id)
                .order_by(CollectionShareToken.created_at.desc())
            )
        )
        .scalars()
        .first()
    )


async def _active_share(db: DbDep, collection_id: str) -> CollectionShareToken | None:
    return (
        (
            await db.execute(
                select(CollectionShareToken).where(
                    CollectionShareToken.collection_id == collection_id,
                    CollectionShareToken.status == "active",
                )
            )
        )
        .scalars()
        .first()
    )


def _share_info(
    settings: ApiSettings, token: CollectionShareToken | None, included_note_count: int
) -> ShareInfo:
    if token is None:
        return ShareInfo(
            status="none",
            token=None,
            url=None,
            include_notes=False,
            included_note_count=included_note_count,
        )
    is_active = token.status == "active"
    url = f"{settings.app_base_url.rstrip('/')}/c/{token.token}" if is_active else None
    return ShareInfo(
        status=token.status,
        token=token.token if is_active else None,
        url=url,
        include_notes=token.include_notes,
        included_note_count=included_note_count,
    )


async def _entry_out(db: DbDep, entry: CollectionEntry, order: int) -> CollectionEntryOut:
    item = await db.get(LibraryItem, entry.library_item_id)
    assert item is not None
    summary = await _summary_for(db, item)
    return CollectionEntryOut(
        id=str(entry.id),
        order=order,
        library_item=summary,
        assignee=entry.assignee or None,
        assignee_is_self=entry.assignee_is_self,
        presentation_minutes=entry.presentation_minutes,
        note=entry.note or None,
    )


async def _order_of(db: DbDep, collection_id: str, entry_id: str) -> int:
    entries = await _ordered_entries(db, collection_id)
    for idx, e in enumerate(entries, start=1):
        if str(e.id) == str(entry_id):
            return idx
    raise ProblemException("not_found")


async def _detail_response(
    db: DbDep, settings: ApiSettings, collection: Collection
) -> CollectionDetailResponse:
    entries = await _ordered_entries(db, str(collection.id))
    entry_outs: list[CollectionEntryOut] = []
    done = 0
    for idx, entry in enumerate(entries, start=1):
        out = await _entry_out(db, entry, idx)
        if out.library_item.status == "done":
            done += 1
        entry_outs.append(out)

    share_token = await _latest_share(db, str(collection.id))
    included = await _included_note_count(db, str(collection.id))
    return CollectionDetailResponse(
        id=str(collection.id),
        name=collection.name,
        description=collection.description or None,
        deadline=collection.deadline.isoformat() if collection.deadline else None,
        days_left=days_left(collection.deadline, today_jst()),
        progress=CollectionProgress(done=done, total=len(entry_outs)),
        share=_share_info(settings, share_token, included),
        entries=entry_outs,
    )


# ============================================================================
# CRUD(§13.1)
# ============================================================================
@router.get(
    "/api/collections", response_model=CollectionListResponse, operation_id="collections_list"
)
async def list_collections(user: CurrentUser, db: DbDep) -> CollectionListResponse:
    today = today_jst()
    rows = (
        await db.execute(
            select(Collection)
            .where(Collection.user_id == user.id)
            .order_by(Collection.created_at.asc(), Collection.id.asc())
        )
    ).scalars()
    items: list[CollectionListItem] = []
    for c in rows.all():
        total, done = await _progress_counts(db, str(c.id))
        items.append(
            CollectionListItem(
                id=str(c.id),
                name=c.name,
                deadline=c.deadline.isoformat() if c.deadline else None,
                days_left=days_left(c.deadline, today),
                item_count=total,
                done_count=done,
            )
        )
    return CollectionListResponse(items=items)


@router.post(
    "/api/collections",
    response_model=CollectionDetailResponse,
    status_code=201,
    operation_id="collections_create",
)
async def create_collection(
    body: CollectionCreateBody, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> CollectionDetailResponse:
    name = body.name.strip()
    if not name:
        raise ProblemException("validation_error", detail="name は必須です")
    collection = Collection(
        id=str(uuid.uuid4()),
        user_id=str(user.id),
        name=name,
        description=(body.description or "").strip(),
        deadline=_parse_date(body.deadline),
    )
    db.add(collection)
    await db.commit()
    return await _detail_response(db, settings, collection)


@router.get(
    "/api/collections/{collection_id}",
    response_model=CollectionDetailResponse,
    operation_id="collections_get",
)
async def get_collection(
    collection_id: str, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> CollectionDetailResponse:
    collection = await _get_owned_collection(db, user.id, collection_id)
    return await _detail_response(db, settings, collection)


@router.patch(
    "/api/collections/{collection_id}",
    response_model=CollectionDetailResponse,
    operation_id="collections_update",
)
async def patch_collection(
    collection_id: str,
    body: CollectionPatchBody,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
) -> CollectionDetailResponse:
    collection = await _get_owned_collection(db, user.id, collection_id)
    fields = body.model_fields_set
    if "name" in fields and body.name is not None:
        name = body.name.strip()
        if not name:
            raise ProblemException("validation_error", detail="name は空にできません")
        collection.name = name
    if "description" in fields:
        collection.description = (body.description or "").strip()
    if "deadline" in fields:
        collection.deadline = _parse_date(body.deadline)
    await db.commit()
    return await _detail_response(db, settings, collection)


@router.delete(
    "/api/collections/{collection_id}", status_code=204, operation_id="collections_delete"
)
async def delete_collection(collection_id: str, user: CurrentUser, db: DbDep) -> Response:
    """204。entries のみ削除(ON DELETE CASCADE)。LibraryItem 自体は消さない(§13.1)。"""
    collection = await _get_owned_collection(db, user.id, collection_id)
    await db.delete(collection)
    await db.commit()
    return Response(status_code=204)


# ============================================================================
# entries(§13.2)
# ============================================================================
@router.post(
    "/api/collections/{collection_id}/entries",
    response_model=CollectionEntryOut,
    status_code=201,
    operation_id="collections_add_entry",
)
async def add_entry(
    collection_id: str, body: EntryCreateBody, user: CurrentUser, db: DbDep
) -> CollectionEntryOut:
    collection = await _get_owned_collection(db, user.id, collection_id)
    item = (
        await db.get(LibraryItem, body.library_item_id)
        if _valid_uuid(body.library_item_id)
        else None
    )
    if item is None or str(item.user_id) != str(user.id):
        raise ProblemException("not_found", detail="論文が見つかりません")

    existing = (
        await db.execute(
            select(CollectionEntry.id).where(
                CollectionEntry.collection_id == collection.id,
                CollectionEntry.library_item_id == item.id,
            )
        )
    ).first()
    if existing is not None:
        raise ProblemException("duplicate", detail="すでにこのコレクションにあります")

    max_position = (
        await db.execute(
            select(func.max(CollectionEntry.position)).where(
                CollectionEntry.collection_id == collection.id
            )
        )
    ).scalar_one()
    entry = CollectionEntry(
        id=str(uuid.uuid4()),
        collection_id=str(collection.id),
        library_item_id=str(item.id),
        position=(max_position + 1) if max_position is not None else 0,
    )
    db.add(entry)
    await db.commit()
    order = await _order_of(db, str(collection.id), str(entry.id))
    return await _entry_out(db, entry, order)


@router.patch(
    "/api/collection-entries/{entry_id}",
    response_model=CollectionEntryOut,
    operation_id="collection_entries_update",
)
async def patch_entry(
    entry_id: str, body: EntryPatchBody, user: CurrentUser, db: DbDep
) -> CollectionEntryOut:
    entry = await _get_owned_entry(db, user.id, entry_id)
    fields = body.model_fields_set
    if "assignee" in fields:
        entry.assignee = body.assignee or ""
    if "assignee_is_self" in fields and body.assignee_is_self is not None:
        entry.assignee_is_self = body.assignee_is_self
    if "presentation_minutes" in fields:
        entry.presentation_minutes = body.presentation_minutes
    if "note" in fields:
        entry.note = body.note or ""
    await db.commit()
    order = await _order_of(db, str(entry.collection_id), str(entry.id))
    return await _entry_out(db, entry, order)


@router.delete(
    "/api/collection-entries/{entry_id}", status_code=204, operation_id="collection_entries_delete"
)
async def delete_entry(entry_id: str, user: CurrentUser, db: DbDep) -> Response:
    entry = await _get_owned_entry(db, user.id, entry_id)
    await db.delete(entry)
    await db.commit()
    return Response(status_code=204)


@router.put(
    "/api/collections/{collection_id}/entries/order",
    response_model=OkResponse,
    operation_id="collections_reorder_entries",
)
async def reorder_entries(
    collection_id: str, body: EntriesOrderBody, user: CurrentUser, db: DbDep
) -> OkResponse:
    collection = await _get_owned_collection(db, user.id, collection_id)
    entries = await _ordered_entries(db, str(collection.id))
    existing_ids = {str(e.id) for e in entries}
    given_ids = [str(x) for x in body.entry_ids]
    if len(given_ids) != len(existing_ids) or set(given_ids) != existing_ids:
        raise ProblemException(
            "validation_error",
            detail="entry_ids はコレクションの全エントリと一致する必要があります",
        )
    by_id = {str(e.id): e for e in entries}
    for idx, eid in enumerate(given_ids):
        by_id[eid].position = idx
    await db.commit()
    return OkResponse()


# ============================================================================
# 共有リンク(§13.3)
# ============================================================================
@router.post(
    "/api/collections/{collection_id}/share",
    response_model=ShareInfo,
    status_code=201,
    operation_id="collections_share_issue",
)
async def issue_share(
    collection_id: str, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> ShareInfo:
    collection = await _get_owned_collection(db, user.id, collection_id)
    existing = await _latest_share(db, str(collection.id))
    if existing is not None and existing.status == "active":
        raise ProblemException("conflict", detail="共有リンクは既に発行されています")

    share = CollectionShareToken(
        id=str(uuid.uuid4()),
        collection_id=str(collection.id),
        token=_generate_share_token(),
        status="active",
        include_notes=False,
    )
    db.add(share)
    await db.commit()
    included = await _included_note_count(db, str(collection.id))
    return _share_info(settings, share, included)


@router.patch(
    "/api/collections/{collection_id}/share",
    response_model=ShareInfo,
    operation_id="collections_share_update",
)
async def patch_share(
    collection_id: str, body: SharePatchBody, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> ShareInfo:
    collection = await _get_owned_collection(db, user.id, collection_id)
    share = await _active_share(db, str(collection.id))
    if share is None:
        raise ProblemException("not_found", detail="共有リンクが発行されていません")
    share.include_notes = body.include_notes
    await db.commit()
    included = await _included_note_count(db, str(collection.id))
    return _share_info(settings, share, included)


@router.delete(
    "/api/collections/{collection_id}/share",
    status_code=204,
    operation_id="collections_share_revoke",
)
async def revoke_share(collection_id: str, user: CurrentUser, db: DbDep) -> Response:
    collection = await _get_owned_collection(db, user.id, collection_id)
    share = await _active_share(db, str(collection.id))
    if share is None:
        raise ProblemException("not_found", detail="共有リンクが発行されていません")
    share.status = "revoked"
    share.revoked_at = dt.datetime.now(dt.UTC)
    await db.commit()
    return Response(status_code=204)
