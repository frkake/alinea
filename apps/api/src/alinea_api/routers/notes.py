"""notes ルータ — メモ CRUD・チャット昇格・まとめてメモ化(plans/03 §9・§10.5、docs/05 §8)。

- GET/POST /api/library-items/{id}/notes、PATCH/DELETE /api/notes/{note_id}(§9)。
- POST /api/chat/threads/{thread_id}/summarize-to-note(§10.5)。スレッド全体を LLM 要約し、
  同期実行で Note を作成して返す(ジョブ化しない)。LLM は build_router_for_user(task='summary')
  経由、クォータは check_quota(task='summary')。task='summary'・job_id なしは
  ``chat_messages`` クォータへ合算される(``llm/deps.py`` の ``_COUNTER_SQL``)。
- ``anchors`` の ``display`` は保存せず、出力時に ``block_search_index`` から決定的に導出する
  (docs/05 §5・chat.evidence と同型)。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from alinea_core.db.models import ChatMessage, ChatThread, LibraryItem
from alinea_core.db.models import Note as NoteModel
from alinea_llm.errors import ProviderChainExhausted
from fastapi import APIRouter, Depends, Response
from sqlalchemy import select

from alinea_api.chat.evidence import EvidenceValidator, load_validator
from alinea_api.deps import CurrentUser, DbDep, RedisDep, SettingsDep
from alinea_api.errors import ProblemException
from alinea_api.llm.deps import ProviderFactory, build_router_for_user, check_quota
from alinea_api.schemas.chat import AnchorRef
from alinea_api.schemas.notes import (
    Note as NoteOut,
)
from alinea_api.schemas.notes import (
    NoteCreate,
    NoteListResponse,
    NotePatch,
    NoteSource,
    SummarizeToNoteResponse,
)

router = APIRouter(tags=["notes"])


# ---------------------------------------------------------------------------
# テスト注入点: provider factory(既定は None → 実アダプタ build_provider)
# ---------------------------------------------------------------------------
def get_notes_provider_factory() -> ProviderFactory | None:
    return None


ProviderFactoryDep = Annotated[ProviderFactory | None, Depends(get_notes_provider_factory)]


# ---------------------------------------------------------------------------
# 所有チェック
# ---------------------------------------------------------------------------
def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def _owned_item(db: DbDep, user_id: str, item_id: str) -> LibraryItem:
    if not _valid_uuid(item_id):
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, item_id)
    if item is None or str(item.user_id) != str(user_id):
        raise ProblemException("not_found")
    return item


async def _owned_note(db: DbDep, user_id: str, note_id: str) -> NoteModel:
    if not _valid_uuid(note_id):
        raise ProblemException("not_found")
    note = await db.get(NoteModel, note_id)
    if note is None:
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, note.library_item_id)
    if item is None or str(item.user_id) != str(user_id):
        raise ProblemException("not_found")
    return note


async def _owned_thread(db: DbDep, user_id: str, thread_id: str) -> tuple[ChatThread, LibraryItem]:
    if not _valid_uuid(thread_id):
        raise ProblemException("not_found")
    thread = await db.get(ChatThread, thread_id)
    if thread is None:
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, thread.library_item_id)
    if item is None or str(item.user_id) != str(user_id):
        raise ProblemException("not_found")
    return thread, item


# ---------------------------------------------------------------------------
# 根拠 display 導出(revision 単位でキャッシュ)
# ---------------------------------------------------------------------------
async def _validator_for(
    db: DbDep, revision_id: str, cache: dict[str, EvidenceValidator]
) -> EvidenceValidator:
    if revision_id not in cache:
        cache[revision_id] = (
            await load_validator(db, revision_id) if revision_id else EvidenceValidator("", [])
        )
    return cache[revision_id]


async def _note_out(db: DbDep, note: NoteModel, cache: dict[str, EvidenceValidator]) -> NoteOut:
    anchors_out: list[AnchorRef] = []
    for a in note.anchors or []:
        if not isinstance(a, dict):
            continue
        revision_id = str(a.get("revision_id", ""))
        validator = await _validator_for(db, revision_id, cache)
        anchors_out.append(AnchorRef(**validator.with_display(a)))
    source = (
        NoteSource(chat_message_id=str(note.source_chat_message_id))
        if note.source_chat_message_id is not None
        else None
    )
    return NoteOut(
        id=str(note.id),
        content_md=note.body_md,
        source=source,
        anchors=anchors_out,
        created_at=note.created_at.isoformat(),
        updated_at=note.updated_at.isoformat(),
    )


# ============================================================================
# 一覧(§9)
# ============================================================================
@router.get(
    "/api/library-items/{item_id}/notes",
    response_model=NoteListResponse,
    operation_id="notes_list",
)
async def list_notes(item_id: str, user: CurrentUser, db: DbDep) -> NoteListResponse:
    await _owned_item(db, user.id, item_id)
    rows = (
        (
            await db.execute(
                select(NoteModel)
                .where(NoteModel.library_item_id == item_id)
                .order_by(NoteModel.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    cache: dict[str, EvidenceValidator] = {}
    items = [await _note_out(db, n, cache) for n in rows]
    return NoteListResponse(items=items)


# ============================================================================
# 作成(§9。チャット昇格は source_message_id 指定)
# ============================================================================
@router.post(
    "/api/library-items/{item_id}/notes",
    response_model=NoteOut,
    status_code=201,
    operation_id="notes_create",
)
async def create_note(item_id: str, body: NoteCreate, user: CurrentUser, db: DbDep) -> NoteOut:
    item = await _owned_item(db, user.id, item_id)

    provided_anchors = "anchors" in body.model_fields_set
    source_chat_message_id: int | None = None
    anchors_json: list[dict[str, Any]] = []

    if body.source_message_id is not None:
        try:
            msg_pk = int(body.source_message_id)
        except (TypeError, ValueError) as exc:
            raise ProblemException(
                "validation_error", detail="source_message_id が不正です"
            ) from exc
        msg = await db.get(ChatMessage, msg_pk)
        thread = await db.get(ChatThread, msg.thread_id) if msg is not None else None
        if msg is None or thread is None or str(thread.library_item_id) != str(item.id):
            raise ProblemException("validation_error", detail="対象のメッセージが見つかりません")
        source_chat_message_id = msg.id
        if not provided_anchors:
            # source_message_id 指定・anchors 省略時はメッセージの根拠アンカーを複写する(§9)。
            anchors_json = [dict(a) for a in (msg.evidence_anchors or []) if isinstance(a, dict)]

    if provided_anchors:
        anchors_json = [a.model_dump(mode="json") for a in (body.anchors or [])]

    note = NoteModel(
        id=str(uuid.uuid4()),
        library_item_id=item.id,
        title="",
        body_md=body.content_md,
        anchors=anchors_json,
        source_chat_message_id=source_chat_message_id,
    )
    db.add(note)
    await db.commit()
    cache: dict[str, EvidenceValidator] = {}
    return await _note_out(db, note, cache)


# ============================================================================
# 更新(§9)
# ============================================================================
@router.patch(
    "/api/notes/{note_id}",
    response_model=NoteOut,
    operation_id="notes_update",
)
async def patch_note(note_id: str, body: NotePatch, user: CurrentUser, db: DbDep) -> NoteOut:
    note = await _owned_note(db, user.id, note_id)
    note.body_md = body.content_md
    await db.commit()
    await db.refresh(note)
    cache: dict[str, EvidenceValidator] = {}
    return await _note_out(db, note, cache)


# ============================================================================
# 削除(§9)
# ============================================================================
@router.delete(
    "/api/notes/{note_id}",
    status_code=204,
    operation_id="notes_delete",
)
async def delete_note(note_id: str, user: CurrentUser, db: DbDep) -> Response:
    note = await _owned_note(db, user.id, note_id)
    await db.delete(note)
    await db.commit()
    return Response(status_code=204)


# ============================================================================
# まとめてメモ化(§10.5)
# ============================================================================
@router.post(
    "/api/chat/threads/{thread_id}/summarize-to-note",
    response_model=SummarizeToNoteResponse,
    status_code=201,
    operation_id="notes_summarize_to_note",
)
async def summarize_to_note(
    thread_id: str,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    r: RedisDep,
    factory: ProviderFactoryDep,
) -> SummarizeToNoteResponse:
    thread, item = await _owned_thread(db, user.id, thread_id)
    await check_quota(db, str(user.id), "summary", settings=settings, cache=r)

    rows = (
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.thread_id == thread.id, ChatMessage.status != "error")
                .order_by(ChatMessage.id.asc())
            )
        )
        .scalars()
        .all()
    )

    transcript_lines: list[str] = []
    anchors_json: list[dict[str, Any]] = []
    seen_blocks: set[tuple[str, str]] = set()
    for msg in rows:
        speaker = "あなた" if msg.role == "user" else "アシスタント"
        transcript_lines.append(f"{speaker}: {msg.text_plain}")
        if msg.role != "assistant":
            continue
        for a in msg.evidence_anchors or []:
            if not isinstance(a, dict):
                continue
            key = (str(a.get("revision_id", "")), str(a.get("block_id", "")))
            if key in seen_blocks:
                continue
            seen_blocks.add(key)
            anchors_json.append(dict(a))

    prompt = (
        "以下は論文読解チャットの会話ログです。要点を整理し、Markdown 形式の短いメモとして"
        "日本語でまとめてください(下書きとして後で編集されます)。\n\n"
        "# 会話ログ\n" + "\n".join(transcript_lines)
    )

    llm_router = await build_router_for_user(
        db, str(user.id), "summary", settings=settings, cache=r, provider_factory=factory
    )
    try:
        resp = await llm_router.complete(
            "summary", prompt, user_id=str(user.id), library_item_id=str(item.id)
        )
    except ProviderChainExhausted as exc:
        raise ProblemException("provider_error", detail="要約の生成に失敗しました。") from exc

    note = NoteModel(
        id=str(uuid.uuid4()),
        library_item_id=item.id,
        title="",
        body_md=resp.text,
        anchors=anchors_json,
        source_chat_message_id=None,
    )
    db.add(note)
    await db.commit()
    cache: dict[str, EvidenceValidator] = {}
    return SummarizeToNoteResponse(note=await _note_out(db, note, cache))
