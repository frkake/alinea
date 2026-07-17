"""chat ルータ — スレッド CRUD・メッセージ SSE・regenerate(plans/03 §10、plans/07 §2)。

SSE 契約は plans/03 §10.3 が正(`start` / `delta` / `evidence` / `done` / `error`)。
LLM は build_router_for_user(task='chat')経由、クォータは check_quota(task='chat')。
P1 忠実性: 根拠は実在検証済みのみ配信(evidence.py)、aside(論文外の知識/推測)と
assistant ロールが「AI生成」明示のメタデータ(docs/05 §6)。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import AsyncIterator, Sequence
from typing import Annotated, Any

from alinea_core.db.models import (
    Annotation,
    ChatMessage,
    ChatThread,
    LibraryItem,
    Note,
    Paper,
)
from alinea_core.db.revisions import get_latest_paper_revision
from alinea_core.document.blocks import DocumentContent
from alinea_core.document.plaintext import strip_markdown
from alinea_llm.errors import ProviderChainExhausted
from alinea_llm.protocols import UsageDraft
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from alinea_api.chat.context_builder import build_chat_request, render_annotations_context
from alinea_api.chat.evidence import EvidenceValidator, load_validator
from alinea_api.chat.prompts import resolve_user_content
from alinea_api.chat.stream_pipeline import (
    StreamPipeline,
    record_usage,
    request_for_model,
    resolve_primary,
)
from alinea_api.deps import CurrentUser, DbDep, RedisDep, SettingsDep
from alinea_api.errors import Problem, ProblemException, build_problem
from alinea_api.llm.deps import ProviderFactory, build_router_for_user, check_quota
from alinea_api.schemas.chat import (
    AnchorRef,
    AsideBlock,
    ChatMessageListResponse,
    ChatThreadListResponse,
    EvidenceRef,
    MarkdownBlock,
    MessageBlock,
    RegenerateRequest,
    SendMessageRequest,
    ThreadCreateRequest,
    ThreadPatchRequest,
)
from alinea_api.schemas.chat import (
    ChatMessage as ChatMessageOut,
)
from alinea_api.schemas.chat import (
    ChatThread as ChatThreadOut,
)
from alinea_api.schemas.common import decode_cursor, encode_cursor

router = APIRouter(tags=["chat"])

_SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
_PLACEHOLDER_RE = re.compile(r"⟦A:(\d+)⟧")
_DEFAULT_MESSAGE_LIMIT = 50
_MAX_MESSAGE_LIMIT = 100
_PROVIDER_ERROR_TITLE = "回答の生成に失敗しました"


# ---------------------------------------------------------------------------
# テスト注入点: provider factory(既定は None → 実アダプタ build_provider)
# ---------------------------------------------------------------------------
def get_chat_provider_factory() -> ProviderFactory | None:
    return None


ProviderFactoryDep = Annotated[ProviderFactory | None, Depends(get_chat_provider_factory)]


# ---------------------------------------------------------------------------
# 認可・ロード補助
# ---------------------------------------------------------------------------
async def _owned_item(db: DbDep, user: CurrentUser, item_id: str) -> LibraryItem:
    item = await db.get(LibraryItem, item_id)
    if item is None or str(item.user_id) != str(user.id):
        raise ProblemException("not_found")
    return item


async def _owned_thread(
    db: DbDep, user: CurrentUser, thread_id: str
) -> tuple[ChatThread, LibraryItem]:
    thread = await db.get(ChatThread, thread_id)
    if thread is None:
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, thread.library_item_id)
    if item is None or str(item.user_id) != str(user.id):
        raise ProblemException("not_found")
    return thread, item


async def _ensure_main_thread(db: DbDep, item_id: str) -> ChatThread:
    """メインスレッドが無ければ自動作成する(plans/03 §10.1)。"""
    existing = (
        (
            await db.execute(
                select(ChatThread).where(
                    ChatThread.library_item_id == item_id, ChatThread.is_main.is_(True)
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing
    main = ChatThread(library_item_id=item_id, title="メイン", is_main=True)
    db.add(main)
    await db.flush()
    return main


async def _thread_out(db: DbDep, thread: ChatThread) -> ChatThreadOut:
    row = (
        await db.execute(
            select(func.count(ChatMessage.id), func.max(ChatMessage.created_at)).where(
                ChatMessage.thread_id == thread.id
            )
        )
    ).one()
    count, last_at = int(row[0]), row[1]
    return ChatThreadOut(
        id=str(thread.id),
        title=thread.title,
        is_main=thread.is_main,
        message_count=count,
        last_message_at=last_at.isoformat() if last_at is not None else None,
    )


def _authors_short(authors: Sequence[Any] | None) -> str:
    names: list[str] = []
    for a in authors or []:
        if isinstance(a, str):
            names.append(a)
        elif isinstance(a, dict):
            names.append(str(a.get("name") or a.get("family") or ""))
    joined = ", ".join(n for n in names if n)
    return joined[:200]


async def _load_paper_context(
    db: DbDep, item: LibraryItem
) -> tuple[DocumentContent, str, dict[str, str]]:
    paper = await db.get(Paper, item.paper_id)
    if paper is None or paper.latest_revision_id is None:
        raise ProblemException("conflict", detail="この論文はまだ読解できる状態ではありません。")
    rev = await get_latest_paper_revision(db, paper)
    if rev is None:
        raise ProblemException("conflict", detail="この論文はまだ読解できる状態ではありません。")
    content = DocumentContent.model_validate(rev.content)
    year = paper.published_on.year if paper.published_on else None
    venue_year = " ".join(str(x) for x in (paper.venue, year) if x)
    bib = {
        "title": paper.title,
        "authors_short": _authors_short(paper.authors),
        "venue_year": venue_year,
        "arxiv_id": paper.arxiv_id or "",
    }
    return content, str(rev.id), bib


async def _annotations_context(
    db: DbDep, item: LibraryItem, validator: EvidenceValidator
) -> str:
    """設定 chat.include_annotations_and_notes=true 時の system[2] 文脈(plans/07 §2.2.5)。

    highlight/comment 注釈(bookmark は quote を持たないため除外)と メモを整形する。
    """
    ann_rows = (
        (
            await db.execute(
                select(Annotation)
                .where(
                    Annotation.library_item_id == item.id,
                    Annotation.kind != "bookmark",
                )
                .order_by(Annotation.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    note_rows = (
        (
            await db.execute(
                select(Note)
                .where(Note.library_item_id == item.id)
                .order_by(Note.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    annotations = [
        {"kind": a.kind, "color": a.color, "body": a.body, "anchor": a.anchor} for a in ann_rows
    ]
    notes = [{"title": n.title, "body_md": n.body_md} for n in note_rows]
    return render_annotations_context(annotations=annotations, notes=notes, validator=validator)


async def _validator_for_item(db: DbDep, item: LibraryItem) -> EvidenceValidator:
    paper = await db.get(Paper, item.paper_id)
    revision = await get_latest_paper_revision(db, paper) if paper is not None else None
    if revision is None:
        return EvidenceValidator("", [])
    return await load_validator(db, str(revision.id))


def _normalize_anchor(anchor: dict[str, Any], revision_id: str) -> dict[str, Any]:
    quote = anchor.get("quote")
    if isinstance(quote, str) and len(quote) > 500:
        quote = quote[:500]  # AnchorJson.quote は最大 500 文字(plans/02 §3.1)
    return {
        "revision_id": anchor.get("revision_id") or revision_id,
        "block_id": anchor.get("block_id", ""),
        "start": anchor.get("start"),
        "end": anchor.get("end"),
        "quote": quote,
        "side": anchor.get("side", "source"),
    }


# ---------------------------------------------------------------------------
# メッセージ整形(DB → plans/03 §10.2)
# ---------------------------------------------------------------------------
def _placeholder_to_token(md: str) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: f"[[ev:{m.group(1)}]]", md)


def _evidence_refs(
    md: str, evidence_anchors: Sequence[dict[str, Any]], validator: EvidenceValidator
) -> list[EvidenceRef]:
    refs = sorted({int(n) for n in _PLACEHOLDER_RE.findall(md)})
    out: list[EvidenceRef] = []
    for n in refs:
        if 1 <= n <= len(evidence_anchors):
            ar = validator.with_display(evidence_anchors[n - 1])
            out.append(EvidenceRef(ref=n, display=ar["display"], anchor=AnchorRef(**ar)))
    return out


def _message_out(msg: ChatMessage, validator: EvidenceValidator) -> ChatMessageOut:
    content = msg.content if isinstance(msg.content, dict) else {}
    segments = content.get("segments", []) if isinstance(content, dict) else []
    evidence_anchors = list(msg.evidence_anchors or [])
    blocks: list[MessageBlock] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type")
        md = str(seg.get("md", ""))
        if seg_type in ("outside_knowledge", "speculation"):
            blocks.append(AsideBlock(label=seg_type, text=_placeholder_to_token(md)))
        else:
            blocks.append(
                MarkdownBlock(
                    text=_placeholder_to_token(md),
                    evidence=_evidence_refs(md, evidence_anchors, validator),
                )
            )
    context_anchors = [
        AnchorRef(**validator.with_display(a, context_chip=True))
        for a in (msg.context_anchors or [])
        if isinstance(a, dict)
    ]
    error: Problem | None = None
    if msg.error:
        try:
            error = Problem(**json.loads(msg.error))
        except (ValueError, TypeError):
            error = None
    status = "error" if msg.status == "error" else "complete"
    quick_action = content.get("quick_action") if isinstance(content, dict) else None
    return ChatMessageOut(
        id=str(msg.id),
        role="assistant" if msg.role == "assistant" else "user",
        blocks=blocks,
        context_anchors=context_anchors,
        quick_action=quick_action,
        status=status,
        error=error,
        created_at=msg.created_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# スレッド CRUD(plans/03 §10.1)
# ---------------------------------------------------------------------------
@router.get(
    "/api/library-items/{item_id}/chat/threads",
    response_model=ChatThreadListResponse,
    operation_id="chat_list_threads",
)
async def list_threads(item_id: str, user: CurrentUser, db: DbDep) -> ChatThreadListResponse:
    item = await _owned_item(db, user, item_id)
    await _ensure_main_thread(db, item.id)
    await db.commit()
    threads = (
        (
            await db.execute(
                select(ChatThread)
                .where(ChatThread.library_item_id == item.id)
                .order_by(ChatThread.is_main.desc(), ChatThread.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return ChatThreadListResponse(items=[await _thread_out(db, t) for t in threads])


@router.post(
    "/api/library-items/{item_id}/chat/threads",
    response_model=ChatThreadOut,
    status_code=201,
    operation_id="chat_create_thread",
)
async def create_thread(
    item_id: str, body: ThreadCreateRequest, user: CurrentUser, db: DbDep
) -> ChatThreadOut:
    item = await _owned_item(db, user, item_id)
    await _ensure_main_thread(db, item.id)
    thread = ChatThread(library_item_id=item.id, title=body.title, is_main=False)
    db.add(thread)
    await db.commit()
    return await _thread_out(db, thread)


@router.patch(
    "/api/chat/threads/{thread_id}",
    response_model=ChatThreadOut,
    operation_id="chat_update_thread",
)
async def update_thread(
    thread_id: str, body: ThreadPatchRequest, user: CurrentUser, db: DbDep
) -> ChatThreadOut:
    thread, _item = await _owned_thread(db, user, thread_id)
    thread.title = body.title
    await db.commit()
    return await _thread_out(db, thread)


@router.delete(
    "/api/chat/threads/{thread_id}",
    status_code=204,
    operation_id="chat_delete_thread",
)
async def delete_thread(thread_id: str, user: CurrentUser, db: DbDep) -> Response:
    thread, _item = await _owned_thread(db, user, thread_id)
    if thread.is_main:
        raise ProblemException("conflict", detail="メインスレッドは削除できません。")
    await db.delete(thread)
    await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# メッセージ取得(plans/03 §10.2)
# ---------------------------------------------------------------------------
@router.get(
    "/api/chat/threads/{thread_id}/messages",
    response_model=ChatMessageListResponse,
    operation_id="chat_list_messages",
)
async def list_messages(
    thread_id: str,
    user: CurrentUser,
    db: DbDep,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_MESSAGE_LIMIT),
) -> ChatMessageListResponse:
    thread, item = await _owned_thread(db, user, thread_id)
    limit = max(1, min(limit, _MAX_MESSAGE_LIMIT))
    stmt = select(ChatMessage).where(ChatMessage.thread_id == thread.id)
    if cursor:
        try:
            decoded = decode_cursor(cursor)
            stmt = stmt.where(ChatMessage.id < int(decoded["k"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise ProblemException("bad_request", detail="カーソルが不正です。") from exc
    stmt = stmt.order_by(ChatMessage.id.desc()).limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    page = rows[:limit]
    validator = await _validator_for_item(db, item)
    next_cursor = encode_cursor(page[-1].id, str(page[-1].id)) if has_more and page else None
    return ChatMessageListResponse(
        items=[_message_out(m, validator) for m in page], next_cursor=next_cursor
    )


# ---------------------------------------------------------------------------
# 送信・再生成の共通ストリーミング(plans/03 §10.3、plans/07 §2.1)
# ---------------------------------------------------------------------------
def _finish_reason(stop_reason: str | None) -> str:
    return "stop" if (stop_reason is None or stop_reason == "end") else stop_reason


def _include_annotations(user: Any) -> bool:
    """設定 chat.include_annotations_and_notes(既定 True・plans/07 §2.2.1)。"""
    settings = getattr(user, "settings", None)
    if isinstance(settings, dict):
        chat_settings = settings.get("chat")
        if isinstance(chat_settings, dict) and "include_annotations_and_notes" in chat_settings:
            return bool(chat_settings["include_annotations_and_notes"])
    return True


def _sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


async def _load_history(db: DbDep, thread_id: str, *, before_id: int) -> list[tuple[str, str]]:
    """履歴を (role, text_plain) で古い順に返す(status='error' は除外・§2.2.6)。"""
    rows = (
        await db.execute(
            select(ChatMessage.role, ChatMessage.text_plain)
            .where(
                ChatMessage.thread_id == thread_id,
                ChatMessage.id < before_id,
                ChatMessage.status != "error",
            )
            .order_by(ChatMessage.id.asc())
        )
    ).all()
    return [(str(role), str(text or "")) for role, text in rows]


async def _prepare_turn(
    db: DbDep,
    settings: SettingsDep,
    r: RedisDep,
    factory: ProviderFactory | None,
    *,
    user_id: str,
    thread: ChatThread,
    item: LibraryItem,
    user_text: str,
    quick_action: str | None,
    context_anchors: list[dict[str, Any]],
    history_before_id: int | None,
    include_annotations: bool,
    reuse_user_msg_id: int | None = None,
) -> tuple[Any, Any, EvidenceValidator, int, int, str]:
    """user/assistant メッセージを挿入し、LLMRequest と Router を用意する。

    ``reuse_user_msg_id`` 指定時(同一質問の再生成)は新しい user メッセージを作らず、
    その id を返す(§10.4: 編集なし再生成は assistant のみ追記)。
    """
    content, revision_id, bib = await _load_paper_context(db, item)
    validator = await load_validator(db, revision_id)
    ctx_anchors = [_normalize_anchor(a, revision_id) for a in context_anchors]

    if reuse_user_msg_id is None:
        user_content_json: dict[str, Any] = {"segments": [{"type": "text", "md": user_text}]}
        if quick_action is not None:
            user_content_json["quick_action"] = quick_action
        user_msg = ChatMessage(
            thread_id=thread.id,
            role="user",
            content=user_content_json,
            text_plain=strip_markdown(user_text),
            context_anchors=ctx_anchors,
            evidence_anchors=[],
            status="complete",
        )
        db.add(user_msg)
        await db.flush()
        user_msg_id = user_msg.id
    else:
        user_msg_id = reuse_user_msg_id

    before_id = history_before_id if history_before_id is not None else user_msg_id
    history = await _load_history(db, str(thread.id), before_id=before_id)
    annotations_text = (
        await _annotations_context(db, item, validator) if include_annotations else None
    )
    request = build_chat_request(
        content=content,
        revision_id=revision_id,
        title=bib["title"],
        authors_short=bib["authors_short"],
        venue_year=bib["venue_year"],
        arxiv_id=bib["arxiv_id"],
        user_content=user_text,
        history=history,
        context_anchors=ctx_anchors,
        include_annotations=include_annotations,
        annotations_text=annotations_text,
    )
    llm_router = await build_router_for_user(
        db, user_id, "chat", settings=settings, cache=r, provider_factory=factory
    )

    assistant = ChatMessage(
        thread_id=thread.id, role="assistant", content={"segments": []}, status="streaming"
    )
    db.add(assistant)
    await db.flush()
    thread.updated_at = dt.datetime.now(dt.UTC)
    assistant_msg_id = assistant.id
    await db.commit()
    return request, llm_router, validator, user_msg_id, assistant_msg_id, str(item.id)


async def _stream_answer(
    db: DbDep,
    llm_router: Any,
    request: Any,
    validator: EvidenceValidator,
    *,
    thread_id: str,
    user_msg_id: int,
    assistant_msg_id: int,
    user_id: str,
    library_item_id: str,
) -> AsyncIterator[str]:
    """assistant メッセージを LLM ストリームで確定保存しつつ SSE を返す(§2.1)。"""
    yield _sse(
        "start",
        {
            "message_id": str(assistant_msg_id),
            "thread_id": str(thread_id),
            "user_message_id": str(user_msg_id),
        },
    )
    pipeline = StreamPipeline(validator)
    final_response: Any = None
    error: Problem | None = None
    provider_name = ""
    model_id = ""
    try:
        provider_name, model_id, provider = resolve_primary(llm_router)
        stream_req = request_for_model(request, model_id)
        async for ev in provider.generate_stream(stream_req):
            if ev.type == "text_delta" and ev.delta:
                for sse in pipeline.feed(ev.delta):
                    yield _sse(sse.event, sse.data)
            elif ev.type == "end":
                final_response = ev.response
            elif ev.type == "error":
                error = build_problem(
                    "provider_error",
                    status=502,
                    title=_PROVIDER_ERROR_TITLE,
                    detail=ev.error_message,
                )
        for sse in pipeline.finish():
            yield _sse(sse.event, sse.data)
    except ProviderChainExhausted:
        error = build_problem(
            "provider_error",
            status=502,
            title=_PROVIDER_ERROR_TITLE,
            detail="利用可能なモデルがありません。",
        )
    except Exception:
        error = build_problem("provider_error", status=502, title=_PROVIDER_ERROR_TITLE)

    # 確定保存(P3: 失敗回答も status='error' で残す)。
    assistant = await db.get(ChatMessage, assistant_msg_id)
    if assistant is not None:
        assistant.content = pipeline.content_json()
        assistant.evidence_anchors = pipeline.evidence_anchors_json()
        assistant.text_plain = pipeline.text_plain()
        assistant.provider = provider_name
        assistant.model = model_id
        if error is not None:
            assistant.status = "error"
            assistant.error = json.dumps(error.model_dump(mode="json"), ensure_ascii=False)
        else:
            assistant.status = "complete"
            if final_response is not None:
                await record_usage(
                    llm_router,
                    UsageDraft(
                        user_id=user_id,
                        library_item_id=library_item_id,
                        task="chat",
                        provider=provider_name,
                        model=model_id,
                        key_source="operator",
                        usage=final_response.usage,
                        status="ok",
                        latency_ms=final_response.latency_ms,
                        request_id=final_response.request_id,
                    ),
                )
    await db.commit()

    if error is not None:
        yield _sse("error", error.model_dump(mode="json"))
    else:
        yield _sse(
            "done",
            {
                "message_id": str(assistant_msg_id),
                "finish_reason": _finish_reason(
                    final_response.stop_reason if final_response is not None else None
                ),
            },
        )


@router.post("/api/chat/threads/{thread_id}/messages", operation_id="chat_send_message")
async def send_message(
    thread_id: str,
    body: SendMessageRequest,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    r: RedisDep,
    factory: ProviderFactoryDep,
) -> StreamingResponse:
    thread, item = await _owned_thread(db, user, thread_id)
    await check_quota(db, str(user.id), "chat", settings=settings, cache=r)

    user_text = resolve_user_content(body.quick_action, body.content)
    if not user_text.strip() and not body.context_anchors:
        raise ProblemException("bad_request", detail="質問内容が空です。")

    context_anchors = [a.model_dump() for a in body.context_anchors]
    (
        request,
        llm_router,
        validator,
        user_msg_id,
        assistant_msg_id,
        library_item_id,
    ) = await _prepare_turn(
        db,
        settings,
        r,
        factory,
        user_id=str(user.id),
        thread=thread,
        item=item,
        user_text=user_text,
        quick_action=body.quick_action,
        context_anchors=context_anchors,
        history_before_id=None,
        include_annotations=_include_annotations(user),
    )
    return StreamingResponse(
        _stream_answer(
            db,
            llm_router,
            request,
            validator,
            thread_id=str(thread.id),
            user_msg_id=user_msg_id,
            assistant_msg_id=assistant_msg_id,
            user_id=str(user.id),
            library_item_id=library_item_id,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ---------------------------------------------------------------------------
# 再生成(plans/03 §10.4)。旧回答は残し新回答を追記する(P3)。
# ---------------------------------------------------------------------------
@router.post("/api/chat/messages/{message_id}/regenerate", operation_id="chat_regenerate")
async def regenerate(
    message_id: str,
    body: RegenerateRequest,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    r: RedisDep,
    factory: ProviderFactoryDep,
) -> StreamingResponse:
    try:
        target = await db.get(ChatMessage, int(message_id))
    except ValueError as exc:
        raise ProblemException("not_found") from exc
    if target is None:
        raise ProblemException("not_found")
    thread, item = await _owned_thread(db, user, str(target.thread_id))
    await check_quota(db, str(user.id), "chat", settings=settings, cache=r)

    # 再生成対象より前で最も新しい user 質問を特定する。
    orig_user = (
        (
            await db.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.thread_id == thread.id,
                    ChatMessage.role == "user",
                    ChatMessage.id < target.id,
                )
                .order_by(ChatMessage.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    # 編集あり: 新しい user メッセージを追記。編集なし: 元の質問を再利用(assistant のみ追記)。
    reuse_user_msg_id: int | None
    if body.content is not None:
        user_text = body.content
        quick_action: str | None = None
        context_anchors: list[dict[str, Any]] = []
        reuse_user_msg_id = None
    elif orig_user is not None:
        oc = orig_user.content if isinstance(orig_user.content, dict) else {}
        segs = oc.get("segments", [])
        user_text = " ".join(str(s.get("md", "")) for s in segs if isinstance(s, dict)).strip()
        quick_action = oc.get("quick_action")
        context_anchors = [a for a in (orig_user.context_anchors or []) if isinstance(a, dict)]
        reuse_user_msg_id = orig_user.id
    else:
        raise ProblemException("bad_request", detail="再生成できる質問がありません。")

    # 文脈は再生成対象の質問より前のみ(直前の失敗回答を入れない・§2.8)。
    history_before = orig_user.id if orig_user is not None else target.id
    (
        request,
        llm_router,
        validator,
        user_msg_id,
        assistant_msg_id,
        library_item_id,
    ) = await _prepare_turn(
        db,
        settings,
        r,
        factory,
        user_id=str(user.id),
        thread=thread,
        item=item,
        user_text=user_text,
        quick_action=quick_action,
        context_anchors=context_anchors,
        history_before_id=history_before,
        include_annotations=_include_annotations(user),
        reuse_user_msg_id=reuse_user_msg_id,
    )
    return StreamingResponse(
        _stream_answer(
            db,
            llm_router,
            request,
            validator,
            thread_id=str(thread.id),
            user_msg_id=user_msg_id,
            assistant_msg_id=assistant_msg_id,
            user_id=str(user.id),
            library_item_id=library_item_id,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
