"""async ファクトリ関数(plans/12 §2.3)。

決定(plans/12 §2.3): factory-boy / polyfactory は使わず、SQLAlchemy モデルを直接
組み立てる async 関数に統一する。JSONB 契約(AnchorJson / DocumentContentJson。
plans/02 §3)を明示的に組めるようにするため。

- 全関数は第 1 引数に `db: AsyncSession` を取り、依存エンティティは省略時に自動生成する。
- 各関数は `flush()` までを行い(ID を確定)、**commit しない**。API(別セッション)から
  参照するテストは呼び出し側で `await db.commit()` すること(既存テストの慣習)。
- 制約(CHECK / 部分一意)は apps/api/alembic/versions/0001_initial_schema.py が正。
  既定値はすべて制約を満たす有効値にしてある。
- 後始末は `alinea_api.services.user_service.purge_user(db, user_id)` を使う
  (users 1 行 DELETE で個人資産がカスケード削除される。docs/01 §13)。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

from alinea_core.db.models import (
    Annotation,
    Article,
    ArticleBlock,
    ChatMessage,
    ChatThread,
    Collection,
    CollectionEntry,
    CollectionShareToken,
    DocumentRevision,
    Job,
    LibraryItem,
    Note,
    Notification,
    OverviewFigure,
    Paper,
    ReadingSession,
    ResourceLink,
    TranslationSet,
    TranslationUnit,
    User,
    VocabEntry,
)
from sqlalchemy.ext.asyncio import AsyncSession


def _uid() -> str:
    return str(uuid.uuid4())


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 既定ドキュメント内容(§14 Rectified Flow の縮約。quality A)
# ---------------------------------------------------------------------------
def reduced_rectified_flow_content() -> dict[str, Any]:
    """make_revision の既定 content(DocumentContentJson 相当・quality A)。

    §1 Introduction + §2 Method の 2 セクション。段落・数式・図の実ブロックを持ち、
    anchor_for で参照できる。全文は含めない(plans/12 §14.2 のライセンス注記)。
    """
    return {
        "quality_level": "A",
        "sections": [
            {
                "id": "sec-1",
                "heading": {"number": "1", "title": "Introduction"},
                "blocks": [
                    {
                        "id": "blk-p1",
                        "type": "paragraph",
                        "inlines": [
                            {
                                "t": "text",
                                "v": "Rectified flow learns a straight transport map "
                                "between two distributions.",
                            }
                        ],
                    },
                    {
                        "id": "blk-p2",
                        "type": "paragraph",
                        "inlines": [{"t": "text", "v": "We use an EMA teacher for distillation."}],
                    },
                    {
                        "id": "blk-eq1",
                        "type": "equation",
                        "number": "1",
                        "label": "eq:rf",
                        "latex": r"\frac{d}{dt} z_t = v(z_t, t)",
                    },
                ],
            },
            {
                "id": "sec-2",
                "heading": {"number": "2", "title": "Method"},
                "blocks": [
                    {
                        "id": "blk-p3",
                        "type": "paragraph",
                        "inlines": [{"t": "text", "v": "The reflow procedure straightens paths."}],
                    },
                    {
                        "id": "blk-fig1",
                        "type": "figure",
                        "label": "1",
                        "asset_key": "fig-1.png",
                        "caption": [{"t": "text", "v": "Straightened trajectories."}],
                    },
                ],
            },
        ],
    }


def _iter_blocks(content: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(sec: dict[str, Any]) -> None:
        for blk in sec.get("blocks", []):
            out.append(blk)
        for sub in sec.get("sections", []):
            walk(sub)

    for s in content.get("sections", []):
        walk(s)
    return out


def anchor_for(
    revision: DocumentRevision,
    block_index: int = 0,
    start: int | None = None,
    end: int | None = None,
    *,
    side: str = "source",
) -> dict[str, Any]:
    """実在ブロックから AnchorJson(dict)を生成する(plans/12 §2.3)。"""
    blocks = _iter_blocks(revision.content)
    if not blocks:
        raise ValueError("revision.content にブロックが無い")
    blk = blocks[block_index]
    quote = ""
    for il in blk.get("inlines", []):
        if il.get("t") == "text":
            quote = il.get("v", "")
            break
    return {
        "revision_id": str(revision.id),
        "block_id": blk["id"],
        "start": start,
        "end": end,
        "quote": quote[:500],
        "side": side,
    }


def broken_anchor(revision: DocumentRevision) -> dict[str, Any]:
    """実在しない block_id を指すアンカー(リアンカー・除去テスト用)。"""
    return {
        "revision_id": str(revision.id),
        "block_id": "blk-does-not-exist",
        "start": None,
        "end": None,
        "quote": "",
        "side": "source",
    }


# ---------------------------------------------------------------------------
# コアエンティティ
# ---------------------------------------------------------------------------
async def make_user(db: AsyncSession, *, email: str | None = None, display_name: str = "") -> User:
    user = User(
        id=_uid(), email=email or f"u-{uuid.uuid4().hex}@example.com", display_name=display_name
    )
    db.add(user)
    await db.flush()
    return user


async def make_paper(
    db: AsyncSession,
    *,
    owner: User | None = None,
    visibility: str = "public",
    license: str = "cc-by-4.0",
    arxiv_id: str | None = None,
    title: str = "Flow Straight and Fast",
    authors: list[dict[str, Any]] | None = None,
    published_on: dt.date | None = None,
) -> Paper:
    # private 論文は owner 必須(ck_papers_private_has_owner)。
    owner_id: str | None = str(owner.id) if owner is not None else None
    if visibility == "private" and owner_id is None:
        owner = await make_user(db)
        owner_id = str(owner.id)
    paper = Paper(
        id=_uid(),
        arxiv_id=arxiv_id,
        title=title,
        authors=authors or [{"name": "Xingchang Liu"}, {"name": "Qiang Liu"}],
        abstract="We present rectified flow.",
        license=license,
        visibility=visibility,
        owner_user_id=owner_id,
        published_on=published_on or dt.date(2022, 9, 7),
    )
    db.add(paper)
    await db.flush()
    return paper


async def make_revision(
    db: AsyncSession,
    *,
    paper: Paper | None = None,
    quality_level: str = "A",
    source_format: str = "arxiv_html",
    source_version: str = "v1",
    parser_version: str = "test-1",
    content: dict[str, Any] | None = None,
    set_latest: bool = True,
) -> DocumentRevision:
    if paper is None:
        paper = await make_paper(db)
    rev = DocumentRevision(
        id=_uid(),
        paper_id=str(paper.id),
        source_version=source_version,
        parser_version=parser_version,
        quality_level=quality_level,
        source_format=source_format,
        content=content or reduced_rectified_flow_content(),
        stats={},
    )
    db.add(rev)
    await db.flush()
    if set_latest:
        paper.latest_revision_id = str(rev.id)
        await db.flush()
    return rev


async def make_library_item(
    db: AsyncSession,
    *,
    user: User | None = None,
    paper: Paper | None = None,
    status: str = "planned",
    tags: list[str] | None = None,
    suggested_tags: list[str] | None = None,
    priority: str | None = None,
    deadline: dt.date | None = None,
    understanding: int | None = None,
    importance: str | None = None,
    reading_position: dict[str, Any] | None = None,
    queue_order: int | None = None,
) -> LibraryItem:
    if user is None:
        user = await make_user(db)
    if paper is None:
        paper = await make_paper(db, owner=user)
    item = LibraryItem(
        id=_uid(),
        user_id=str(user.id),
        paper_id=str(paper.id),
        status=status,
        tags=tags or [],
        suggested_tags=suggested_tags or [],
        priority=priority,
        deadline=deadline,
        understanding=understanding,
        importance=importance,
        reading_position=reading_position,
        queue_order=queue_order,
    )
    db.add(item)
    await db.flush()
    return item


async def make_translation_set(
    db: AsyncSession,
    *,
    revision: DocumentRevision | None = None,
    style: str = "natural",
    scope: str = "shared",
    user: User | None = None,
    status: str = "pending",
    glossary_snapshot: list[Any] | None = None,
    base_set: TranslationSet | None = None,
) -> TranslationSet:
    if revision is None:
        revision = await make_revision(db)
    # scope の CHECK: shared は user_id/base_set_id が NULL、personal は user_id 必須。
    user_id: str | None = None
    base_id: str | None = None
    if scope == "personal":
        if user is None:
            user = await make_user(db)
        user_id = str(user.id)
        base_id = str(base_set.id) if base_set is not None else None
    tset = TranslationSet(
        id=_uid(),
        revision_id=str(revision.id),
        style=style,
        scope=scope,
        user_id=user_id,
        base_set_id=base_id,
        status=status,
        glossary_snapshot=glossary_snapshot or [],
    )
    db.add(tset)
    await db.flush()
    return tset


async def make_translation_unit(
    db: AsyncSession,
    *,
    translation_set: TranslationSet | None = None,
    block_id: str = "blk-p1",
    text_ja: str = "訳: 本文",
    content_ja: list[dict[str, Any]] | None = None,
    source_hash: str | None = None,
    state: str = "machine",
    quality_flags: list[str] | None = None,
    model: str = "fake-llm",
) -> TranslationUnit:
    if translation_set is None:
        translation_set = await make_translation_set(db)
    unit = TranslationUnit(
        set_id=str(translation_set.id),
        block_id=block_id,
        source_hash=source_hash or _hash(block_id),
        content_ja=content_ja if content_ja is not None else [{"t": "text", "v": text_ja}],
        text_ja=text_ja,
        state=state,
        quality_flags=quality_flags or [],
        model=model,
    )
    db.add(unit)
    await db.flush()
    return unit


async def make_job(
    db: AsyncSession,
    *,
    kind: str = "ingest",
    stage: str = "queued",
    status: str = "queued",
    progress: int = 0,
    user: User | None = None,
    paper: Paper | None = None,
    library_item: LibraryItem | None = None,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    log: list[Any] | None = None,
) -> Job:
    job = Job(
        id=_uid(),
        kind=kind,
        stage=stage,
        status=status,
        progress=progress,
        user_id=str(user.id) if user else None,
        paper_id=str(paper.id) if paper else None,
        library_item_id=str(library_item.id) if library_item else None,
        payload=payload or {},
        idempotency_key=idempotency_key,
        log=log or [],
    )
    db.add(job)
    await db.flush()
    return job


# ---------------------------------------------------------------------------
# 読書資産(注釈・メモ・チャット・語彙・リソース)
# ---------------------------------------------------------------------------
async def make_annotation(
    db: AsyncSession,
    *,
    library_item: LibraryItem | None = None,
    revision: DocumentRevision | None = None,
    kind: str = "highlight",
    color: str | None = "important",
    body: str | None = None,
    anchor: dict[str, Any] | None = None,
    quote: str | None = None,
) -> Annotation:
    if library_item is None:
        library_item = await make_library_item(db)
    if anchor is None:
        if revision is None:
            revision = await make_revision(db, paper=None)
        anchor = anchor_for(revision, 0)
    # ``quote`` は annotations.quote(GENERATED ALWAYS AS anchor->>'quote')に写像される
    # 生成列。ORM で直接 INSERT できないため anchor JSONB 側に畳み込む(0001 §4.7)。
    if quote is not None:
        anchor = {**anchor, "quote": quote}
    # kind_shape CHECK: bookmark=両 NULL / highlight=color / comment=color+body。
    if kind == "bookmark":
        color, body = None, None
    elif kind == "comment" and body is None:
        body = "コメント"
    ann = Annotation(
        id=_uid(),
        library_item_id=str(library_item.id),
        kind=kind,
        color=color,
        body=body,
        anchor=anchor,
    )
    db.add(ann)
    await db.flush()
    return ann


async def make_note(
    db: AsyncSession,
    *,
    library_item: LibraryItem | None = None,
    title: str = "メモ",
    body_md: str = "本文",
    anchors: list[dict[str, Any]] | None = None,
    source_chat_message_id: int | None = None,
) -> Note:
    if library_item is None:
        library_item = await make_library_item(db)
    note = Note(
        id=_uid(),
        library_item_id=str(library_item.id),
        title=title,
        body_md=body_md,
        anchors=anchors or [],
        source_chat_message_id=source_chat_message_id,
    )
    db.add(note)
    await db.flush()
    return note


async def make_chat_thread(
    db: AsyncSession,
    *,
    library_item: LibraryItem | None = None,
    title: str = "メイン",
    is_main: bool = True,
) -> ChatThread:
    if library_item is None:
        library_item = await make_library_item(db)
    thread = ChatThread(
        id=_uid(),
        library_item_id=str(library_item.id),
        title=title,
        is_main=is_main,
    )
    db.add(thread)
    await db.flush()
    return thread


async def make_chat_message(
    db: AsyncSession,
    *,
    thread: ChatThread | None = None,
    role: str = "assistant",
    content: dict[str, Any] | None = None,
    text_plain: str = "回答",
    context_anchors: list[dict[str, Any]] | None = None,
    evidence_anchors: list[dict[str, Any]] | None = None,
    status: str = "complete",
) -> ChatMessage:
    if thread is None:
        thread = await make_chat_thread(db)
    msg = ChatMessage(
        thread_id=str(thread.id),
        role=role,
        content=content or {"segments": [{"type": "text", "text": text_plain}]},
        text_plain=text_plain,
        context_anchors=context_anchors or [],
        evidence_anchors=evidence_anchors or [],
        status=status,
    )
    db.add(msg)
    await db.flush()
    return msg


async def make_vocab_entry(
    db: AsyncSession,
    *,
    user: User | None = None,
    library_item: LibraryItem | None = None,
    revision: DocumentRevision | None = None,
    term: str = "rectified flow",
    kind: str = "collocation",
    context_sentence: str = "Rectified flow learns a straight transport map.",
    context_anchor: dict[str, Any] | None = None,
    generation_status: str = "pending",
) -> VocabEntry:
    if library_item is None:
        library_item = await make_library_item(db, user=user)
    if user is None:
        # library_item の所有者に合わせる。
        user_id = str(library_item.user_id)
    else:
        user_id = str(user.id)
    if context_anchor is None:
        if revision is None:
            revision = await make_revision(db)
        context_anchor = anchor_for(revision, 0)
    entry = VocabEntry(
        id=_uid(),
        user_id=user_id,
        library_item_id=str(library_item.id),
        kind=kind,
        term=term,
        context_anchor=context_anchor,
        context_sentence=context_sentence,
        context_hl_start=0,
        context_hl_end=len(term),
        generation_status=generation_status,
    )
    db.add(entry)
    await db.flush()
    return entry


async def make_resource_link(
    db: AsyncSession,
    *,
    library_item: LibraryItem | None = None,
    kind: str = "github",
    url: str = "https://github.com/gnobitab/RectifiedFlow",
    url_normalized: str | None = None,
    status: str = "active",
    official: bool = False,
) -> ResourceLink:
    if library_item is None:
        library_item = await make_library_item(db)
    link = ResourceLink(
        id=_uid(),
        library_item_id=str(library_item.id),
        kind=kind,
        url=url,
        url_normalized=url_normalized or url.lower(),
        status=status,
        official=official,
    )
    db.add(link)
    await db.flush()
    return link


# ---------------------------------------------------------------------------
# コレクション・記事・通知・読書セッション
# ---------------------------------------------------------------------------
async def make_collection(
    db: AsyncSession,
    *,
    user: User | None = None,
    name: str = "輪読会 2026-07",
    deadline: dt.date | None = None,
    entries_of: list[LibraryItem] | None = None,
    with_share_token: bool = False,
    include_notes: bool = False,
) -> Collection:
    if user is None:
        user = await make_user(db)
    coll = Collection(
        id=_uid(),
        user_id=str(user.id),
        name=name,
        deadline=deadline,
    )
    db.add(coll)
    await db.flush()
    for position, item in enumerate(entries_of or []):
        db.add(
            CollectionEntry(
                id=_uid(),
                collection_id=str(coll.id),
                library_item_id=str(item.id),
                position=position,
            )
        )
    if with_share_token:
        db.add(
            CollectionShareToken(
                id=_uid(),
                collection_id=str(coll.id),
                token=uuid.uuid4().hex[:8],
                status="active",
                include_notes=include_notes,
            )
        )
    await db.flush()
    return coll


async def make_article(
    db: AsyncSession,
    *,
    library_item: LibraryItem | None = None,
    preset: str = "beginner",
    version: int = 1,
    with_blocks: bool = True,
    with_overview_figure: bool = False,
) -> Article:
    if library_item is None:
        library_item = await make_library_item(db)
    article = Article(
        id=_uid(),
        library_item_id=str(library_item.id),
        title="やさしい解説",
        preset=preset,
        version=version,
    )
    db.add(article)
    await db.flush()
    if with_blocks:
        db.add_all(
            [
                ArticleBlock(
                    article_id=str(article.id),
                    position=0,
                    type="heading",
                    text_plain="はじめに",
                    origin="ai",
                ),
                ArticleBlock(
                    article_id=str(article.id),
                    position=1,
                    type="paragraph",
                    text_plain="整流フローの概要。",
                    origin="ai",
                ),
                # attribution は常に末尾(docs/07)。
                ArticleBlock(
                    article_id=str(article.id),
                    position=99,
                    type="attribution",
                    text_plain="元の論文とは別物です。",
                    origin="ai",
                ),
            ]
        )
    if with_overview_figure:
        db.add(
            OverviewFigure(
                id=_uid(),
                article_id=str(article.id),
                version=1,
                is_current=True,
                render_mode="svg",
                dsl={"cards": [{"heading": "整流フロー", "body": "直線輸送"}]},
            )
        )
    await db.flush()
    return article


async def make_notification(
    db: AsyncSession,
    *,
    user: User | None = None,
    kind: str = "translation_complete",
    payload: dict[str, Any] | None = None,
    read: bool = False,
) -> Notification:
    if user is None:
        user = await make_user(db)
    note = Notification(
        user_id=str(user.id),
        kind=kind,
        payload=payload or {},
        read=read,
    )
    db.add(note)
    await db.flush()
    return note


async def make_reading_session(
    db: AsyncSession,
    *,
    library_item: LibraryItem | None = None,
    active_seconds: int = 300,
    view_mode: str = "translation",
    started_at: dt.datetime | None = None,
    ended_at: dt.datetime | None = None,
) -> ReadingSession:
    if library_item is None:
        library_item = await make_library_item(db)
    kwargs: dict[str, Any] = {
        "library_item_id": str(library_item.id),
        "active_seconds": active_seconds,
        "view_mode": view_mode,
    }
    if started_at is not None:
        kwargs["started_at"] = started_at
    if ended_at is not None:
        kwargs["ended_at"] = ended_at
    session = ReadingSession(**kwargs)
    db.add(session)
    await db.flush()
    return session
