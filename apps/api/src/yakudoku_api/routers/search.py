"""search — 横断検索・論文内検索(M1-12/M2-15。plans/11 §3〜§7、plans/03 §15・§6.7)。

- ``GET /api/search``: 全結果画面(4e)。source=body/note/annotation/chat/article の 5 源
  + 書誌(papers.title/abstract/abstract_ja。API 上は source="body" に合流)。article は
  M2-15 で追加(plans/11 §3.2 (g))。
- ``GET /api/search/preview``: 1e ドロップダウン(上位 3 件+total、ファセット計算なし)。
- ``GET /api/revisions/{revision_id}/search``: 論文内検索(本文のみ、position 昇順)。

アーキテクチャ決定(plans/11 §1): 検索専用の非正規化テーブルは使わず、各実体テーブルへの
PGroonga インデックス(M0-06 で投入済み)を直接クエリする。ページング・グルーピングは
(想定規模: 個人開発・数百ユーザー、docs/09 §1 前提の)ヒット全件を取得したうえで Python 側で
行う(SQL 側 keyset pagination は followup。§10 の性能要件は別途 PF-06 で検証)。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select, text
from sqlalchemy.sql.elements import TextClause
from yakudoku_core.db.models import (
    Annotation,
    Article,
    ArticleBlock,
    ChatMessage,
    ChatThread,
    DocumentRevision,
    LibraryItem,
    Note,
    Paper,
    User,
)
from yakudoku_core.search.pgroonga_query import (
    chat_qa_snippet,
    finalize_snippet_html,
    is_valid_query,
    matched_in,
    normalize_query,
    snippet_lang_for,
)

from yakudoku_api.chat.evidence import BlockRow, derive_display
from yakudoku_api.deps import CurrentUser, DbDep
from yakudoku_api.errors import ProblemException
from yakudoku_api.routers.viewer import resolve_accessible_revision
from yakudoku_api.schemas.chat import AnchorRef
from yakudoku_api.schemas.common import LastPosition, LibraryItemSummary
from yakudoku_api.schemas.common import decode_cursor as _decode_cursor
from yakudoku_api.schemas.common import encode_cursor as _encode_cursor
from yakudoku_api.schemas.library import build_paper_bib
from yakudoku_api.schemas.search import (
    InPaperSearchItem,
    InPaperSearchResponse,
    MatchedInValue,
    SearchFacetPaper,
    SearchFacets,
    SearchFacetSource,
    SearchGroup,
    SearchGroupArticle,
    SearchHit,
    SearchHitTargetArticle,
    SearchHitTargetChat,
    SearchHitTargetNote,
    SearchHitTargetViewer,
    SearchHitWithPaper,
    SearchPreviewPaper,
    SearchPreviewResponse,
    SearchResponse,
    SearchSort,
    SearchSourceFilter,
)

router = APIRouter(tags=["search"])

_DEFAULT_STYLE = "natural"
_PAGE_LIMIT_DEFAULT = 10
_PAGE_LIMIT_MAX = 20
_GROUP_TOP_HITS = 5
_IN_PAPER_LIMIT_DEFAULT = 50
_IN_PAPER_LIMIT_MAX = 100
_FACET_PAPERS_MAX = 20


# ============================================================================
# 入力正規化・アクセスヘルパ
# ============================================================================
def _validate_query_param(raw: str) -> str:
    """q を正規化し 1〜200 字を検証する(plans/11 §3.1)。不正なら 422。"""
    query = normalize_query(raw)
    if not is_valid_query(query):
        raise ProblemException(
            "validation_error", detail="q は 1〜200 字で指定してください(空文字は不可)"
        )
    return query


def _resolve_style(user: User) -> str:
    """既定翻訳スタイル(users.settings->translation->style。未設定は natural)。"""
    settings = user.settings if isinstance(user.settings, dict) else {}
    translation = settings.get("translation")
    if isinstance(translation, dict):
        style = translation.get("style")
        if isinstance(style, str) and style:
            return style
    return _DEFAULT_STYLE


def _ts(value: dt.datetime) -> float:
    return value.timestamp()


# ============================================================================
# 源別ヒット取得(plans/11 §3.2。body/note/annotation/chat の 4 源 + 書誌)
# ============================================================================
@dataclass(slots=True)
class _HitRow:
    library_item_id: str
    kind: str  # "body" | "note" | "annotation" | "chat" | "biblio"
    score: float
    hit_at: dt.datetime
    ref: dict[str, Any]


_HITS_SQL: TextClause = text(
    """
    WITH params AS (
      SELECT pgroonga_query_escape(:q) AS pq
    ),
    my_items AS (
      SELECT li.id AS library_item_id, li.paper_id,
             COALESCE((li.reading_position->>'revision_id')::uuid, p.latest_revision_id)
                                                                       AS revision_id
      FROM library_items li
      JOIN papers p ON p.id = li.paper_id
      WHERE li.user_id = CAST(:user_id AS uuid)
    ),
    hit_body_source AS (
      SELECT mi.library_item_id, b.revision_id, b.block_id,
             pgroonga_score(b.tableoid, b.ctid)::float AS score,
             dr.created_at AS hit_at
      FROM block_search_index b
      JOIN my_items mi ON mi.revision_id = b.revision_id
      JOIN document_revisions dr ON dr.id = b.revision_id
      WHERE b.source_text &@~ ((SELECT pq FROM params), NULL,
          'pgroonga_block_search_index_source_text')::pgroonga_full_text_search_condition
    ),
    hit_body_translation AS (
      SELECT mi.library_item_id, ts.revision_id, tu.block_id,
             pgroonga_score(tu.tableoid, tu.ctid)::float AS score,
             tu.updated_at AS hit_at
      FROM translation_units tu
      JOIN translation_sets ts ON ts.id = tu.set_id
      JOIN my_items mi ON mi.revision_id = ts.revision_id
      WHERE tu.text_ja &@~ ((SELECT pq FROM params), NULL,
          'pgroonga_translation_units_text_ja')::pgroonga_full_text_search_condition
        AND ts.style = :style
        AND (ts.scope = 'shared' OR ts.user_id = CAST(:user_id AS uuid))
        AND tu.set_id = (
          SELECT u2.set_id
          FROM translation_units u2
          JOIN translation_sets s2 ON s2.id = u2.set_id
          WHERE s2.revision_id = ts.revision_id
            AND s2.style = :style
            AND (s2.scope = 'shared' OR s2.user_id = CAST(:user_id AS uuid))
            AND u2.block_id = tu.block_id
          ORDER BY (s2.scope = 'personal') DESC
          LIMIT 1
        )
    ),
    hit_body AS (
      SELECT COALESCE(s.library_item_id, t.library_item_id) AS library_item_id,
             COALESCE(s.revision_id, t.revision_id)         AS revision_id,
             COALESCE(s.block_id, t.block_id)                AS block_id,
             COALESCE(s.score, 0) + COALESCE(t.score, 0)     AS score,
             (s.block_id IS NOT NULL)                        AS matched_source,
             (t.block_id IS NOT NULL)                        AS matched_translation,
             GREATEST(COALESCE(s.hit_at, '-infinity'::timestamptz),
                      COALESCE(t.hit_at, '-infinity'::timestamptz)) AS hit_at
      FROM hit_body_source s
      FULL OUTER JOIN hit_body_translation t
        ON t.revision_id = s.revision_id AND t.block_id = s.block_id
    ),
    hit_note AS (
      SELECT n.library_item_id, n.id AS note_id,
             (n.title &@~ ((SELECT pq FROM params), NULL,
                 'pgroonga_notes_body')::pgroonga_full_text_search_condition)    AS title_matched,
             (n.body_md &@~ ((SELECT pq FROM params), NULL,
                 'pgroonga_notes_body')::pgroonga_full_text_search_condition)  AS body_matched,
             pgroonga_score(n.tableoid, n.ctid)::float AS score,
             n.created_at AS hit_at
      FROM notes n
      JOIN library_items li ON li.id = n.library_item_id AND li.user_id = CAST(:user_id AS uuid)
      WHERE n.title &@~ ((SELECT pq FROM params), NULL,
                'pgroonga_notes_body')::pgroonga_full_text_search_condition
         OR n.body_md &@~ ((SELECT pq FROM params), NULL,
                'pgroonga_notes_body')::pgroonga_full_text_search_condition
    ),
    hit_annotation AS (
      SELECT a.library_item_id, a.id AS annotation_id, a.anchor,
             pgroonga_score(a.tableoid, a.ctid)::float AS score,
             a.created_at AS hit_at
      FROM annotations a
      JOIN library_items li ON li.id = a.library_item_id AND li.user_id = CAST(:user_id AS uuid)
      WHERE a.kind = 'comment' AND a.body &@~ ((SELECT pq FROM params), NULL,
          'pgroonga_annotations_text')::pgroonga_full_text_search_condition
    ),
    hit_chat_raw AS (
      SELECT th.library_item_id, m.thread_id, m.id AS message_id, m.role,
             pgroonga_score(m.tableoid, m.ctid)::float AS score,
             m.created_at AS hit_at
      FROM chat_messages m
      JOIN chat_threads th ON th.id = m.thread_id
      JOIN library_items li ON li.id = th.library_item_id AND li.user_id = CAST(:user_id AS uuid)
      WHERE m.text_plain &@~ ((SELECT pq FROM params), NULL,
          'pgroonga_chat_messages_text')::pgroonga_full_text_search_condition
    ),
    hit_chat AS (
      SELECT library_item_id, thread_id,
             MIN(message_id) AS message_id,
             SUM(score)      AS score,
             MAX(hit_at)     AS hit_at
      FROM (
        SELECT r.*,
               COALESCE(
                 CASE WHEN r.role = 'user' THEN r.message_id
                      ELSE (SELECT m2.id FROM chat_messages m2
                            WHERE m2.thread_id = r.thread_id
                              AND m2.role = 'user' AND m2.id < r.message_id
                            ORDER BY m2.id DESC LIMIT 1)
                 END, r.message_id) AS pair_key
        FROM hit_chat_raw r
      ) x
      GROUP BY library_item_id, thread_id, pair_key
    ),
    hit_article AS (
      SELECT ar.library_item_id, ab.article_id, ab.id AS article_block_id,
             pgroonga_score(ab.tableoid, ab.ctid)::float AS score,
             ab.updated_at AS hit_at
      FROM article_blocks ab
      JOIN articles       ar ON ar.id = ab.article_id
      JOIN library_items  li ON li.id = ar.library_item_id AND li.user_id = CAST(:user_id AS uuid)
      WHERE ab.text_plain &@~ ((SELECT pq FROM params), NULL,
          'pgroonga_article_blocks_text')::pgroonga_full_text_search_condition
    ),
    hit_biblio AS (
      SELECT mi.library_item_id,
             pgroonga_score(p.tableoid, p.ctid)::float AS score,
             (p.title &@~ ((SELECT pq FROM params), NULL,
                 'pgroonga_papers_biblio_en')::pgroonga_full_text_search_condition)
                                                                            AS title_matched,
             COALESCE(p.abstract &@~ ((SELECT pq FROM params), NULL,
                 'pgroonga_papers_biblio_en')::pgroonga_full_text_search_condition,
                      false)                                                AS abstract_matched,
             COALESCE(p.abstract_ja &@~ ((SELECT pq FROM params), NULL,
                 'pgroonga_papers_biblio_ja')::pgroonga_full_text_search_condition,
                      false)                                                AS abstract_ja_matched,
             p.created_at AS hit_at
      FROM papers p
      JOIN my_items mi ON mi.paper_id = p.id
      WHERE p.title &@~ ((SELECT pq FROM params), NULL,
          'pgroonga_papers_biblio_en')::pgroonga_full_text_search_condition
         OR p.abstract &@~ ((SELECT pq FROM params), NULL,
             'pgroonga_papers_biblio_en')::pgroonga_full_text_search_condition
         OR p.abstract_ja &@~ ((SELECT pq FROM params), NULL,
             'pgroonga_papers_biblio_ja')::pgroonga_full_text_search_condition
    )
    SELECT library_item_id, 'body'::text AS kind, score, hit_at,
           jsonb_build_object('revision_id', revision_id, 'block_id', block_id,
                               'matched_source', matched_source,
                               'matched_translation', matched_translation) AS ref
    FROM hit_body
    UNION ALL
    SELECT library_item_id, 'note', score, hit_at,
           jsonb_build_object('note_id', note_id, 'title_matched', title_matched,
                               'body_matched', body_matched)
    FROM hit_note
    UNION ALL
    SELECT library_item_id, 'annotation', score, hit_at,
           jsonb_build_object('annotation_id', annotation_id, 'anchor', anchor)
    FROM hit_annotation
    UNION ALL
    SELECT library_item_id, 'chat', score, hit_at,
           jsonb_build_object('thread_id', thread_id, 'message_id', message_id)
    FROM hit_chat
    UNION ALL
    SELECT library_item_id, 'article', score, hit_at,
           jsonb_build_object('article_id', article_id, 'article_block_id', article_block_id)
    FROM hit_article
    UNION ALL
    SELECT library_item_id, 'biblio', score, hit_at,
           jsonb_build_object('title_matched', title_matched, 'abstract_matched', abstract_matched,
                               'abstract_ja_matched', abstract_ja_matched)
    FROM hit_biblio
    """
)


async def _fetch_all_hits(db: DbDep, user_id: str, query: str, style: str) -> list[_HitRow]:
    rows = (await db.execute(_HITS_SQL, {"user_id": user_id, "q": query, "style": style})).all()
    return [
        _HitRow(
            library_item_id=str(r.library_item_id),
            kind=str(r.kind),
            score=float(r.score or 0.0),
            hit_at=r.hit_at,
            ref=dict(r.ref or {}),
        )
        for r in rows
        if r.library_item_id is not None
    ]


_SOURCE_FILTER_KINDS: dict[str, tuple[str, ...]] = {
    "body": ("body", "biblio"),
    "notes": ("note", "annotation"),
    "chat": ("chat",),
    "article": ("article",),
}


def _apply_filters(
    hits: list[_HitRow], *, source: SearchSourceFilter, library_item_id: str | None
) -> list[_HitRow]:
    out = hits
    if source != "all":
        allowed = _SOURCE_FILTER_KINDS[source]
        out = [h for h in out if h.kind in allowed]
    if library_item_id:
        out = [h for h in out if h.library_item_id == library_item_id]
    return out


def _facet_source_counts(hits: list[_HitRow]) -> SearchFacetSource:
    body = sum(1 for h in hits if h.kind in ("body", "biblio"))
    notes = sum(1 for h in hits if h.kind in ("note", "annotation"))
    chat = sum(1 for h in hits if h.kind == "chat")
    article = sum(1 for h in hits if h.kind == "article")
    return SearchFacetSource(all=len(hits), body=body, notes=notes, chat=chat, article=article)


async def _paper_titles_for(db: DbDep, library_item_ids: list[str]) -> dict[str, str]:
    if not library_item_ids:
        return {}
    rows = (
        await db.execute(
            select(LibraryItem.id, Paper.title)
            .join(Paper, Paper.id == LibraryItem.paper_id)
            .where(LibraryItem.id.in_(library_item_ids))
        )
    ).all()
    return {str(lid): title for lid, title in rows}


async def _articles_for(db: DbDep, library_item_ids: list[str]) -> dict[str, Article]:
    """記事ヒットを含むグループのヘッダ用(plans/11 §4・§6.1)。1 論文 1 記事(一意制約)。"""
    if not library_item_ids:
        return {}
    rows = (
        (await db.execute(select(Article).where(Article.library_item_id.in_(library_item_ids))))
        .scalars()
        .all()
    )
    return {str(a.library_item_id): a for a in rows}


async def _compute_facets(db: DbDep, all_hits: list[_HitRow]) -> SearchFacets:
    """ファセットは絞り込み前の全ヒット集合で計算する(plans/11 §6.1)。"""
    source = _facet_source_counts(all_hits)
    counts: dict[str, int] = {}
    for h in all_hits:
        counts[h.library_item_id] = counts.get(h.library_item_id, 0) + 1
    titles = await _paper_titles_for(db, list(counts.keys()))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], titles.get(kv[0], "")))[
        :_FACET_PAPERS_MAX
    ]
    papers = [
        SearchFacetPaper(library_item_id=lid, title=titles.get(lid, ""), count=count)
        for lid, count in ranked
    ]
    return SearchFacets(source=source, papers=papers)


# ============================================================================
# グルーピング・ソート・カーソル(plans/11 §3.3・§3.5)
# ============================================================================
@dataclass(slots=True)
class _Group:
    library_item_id: str
    group_score: float
    group_at: dt.datetime
    hits: list[_HitRow] = field(default_factory=list)

    @property
    def hit_count(self) -> int:
        return len(self.hits)


def _build_groups(hits: list[_HitRow]) -> list[_Group]:
    by_item: dict[str, list[_HitRow]] = {}
    for h in hits:
        by_item.setdefault(h.library_item_id, []).append(h)
    groups: list[_Group] = []
    for lid, hs in by_item.items():
        groups.append(
            _Group(
                library_item_id=lid,
                group_score=max(h.score for h in hs),
                group_at=max(h.hit_at for h in hs),
                hits=hs,
            )
        )
    return groups


def _sort_groups(groups: list[_Group], sort: SearchSort) -> list[_Group]:
    if sort == "recency":
        return sorted(groups, key=lambda g: (-_ts(g.group_at), g.library_item_id))
    return sorted(groups, key=lambda g: (-g.group_score, -_ts(g.group_at), g.library_item_id))


def _encode_group_cursor(sort: SearchSort, group: _Group) -> str:
    if sort == "recency":
        return _encode_cursor(group.group_at.isoformat(), group.library_item_id)
    return _encode_cursor([group.group_score, group.group_at.isoformat()], group.library_item_id)


def _apply_group_cursor(groups: list[_Group], cursor: str | None) -> list[_Group]:
    if not cursor:
        return groups
    try:
        data = _decode_cursor(cursor)
    except ValueError as exc:
        raise ProblemException("validation_error", detail="cursor が不正です") from exc
    last_id = str(data.get("id", ""))
    for i, g in enumerate(groups):
        if g.library_item_id == last_id:
            return groups[i + 1 :]
    return groups


# ============================================================================
# 詳細レンダリング(display / snippet / target。plans/11 §4・§5)
# ============================================================================
@dataclass(slots=True)
class _RevBlockInfo:
    block_type: str
    section_path: str
    section_label: str
    element_label: str | None
    paragraph_ordinal: int | None
    page: int | None
    source_text: str


def _compose_body_display(info: _RevBlockInfo, headings: dict[str, str], quality_level: str) -> str:
    """本文ヒットの display(plans/11 §4)。"""
    if info.block_type in ("equation", "figure", "table") and info.element_label:
        return info.element_label
    base = info.section_label
    heading_text = headings.get(info.section_path)
    if heading_text:
        base = f"{base} {heading_text}"
    if quality_level == "B" and info.page is not None:
        base = f"{base} · p.{info.page}"
    return base


async def _pg_snippet(db: DbDep, source_text: str, query: str) -> str:
    if not source_text:
        return ""
    row = (
        await db.execute(
            text(
                "SELECT pgroonga_snippet_html("
                "  :src, pgroonga_query_extract_keywords(pgroonga_query_escape(:q)), 300"
                ") AS fragments"
            ),
            {"src": source_text, "q": query},
        )
    ).one()
    fragments = list(row.fragments or [])
    fragment = fragments[0] if fragments else source_text[:300]
    return finalize_snippet_html(fragment)


class _SearchRenderer:
    """ヒットの display/snippet/target 構築(リビジョン・ノート・チャット等のキャッシュ付き)。"""

    def __init__(self, db: DbDep, user_id: str, style: str, query: str) -> None:
        self.db = db
        self.user_id = user_id
        self.style = style
        self.query = query
        self._rev_blocks: dict[str, dict[str, _RevBlockInfo]] = {}
        self._rev_headings: dict[str, dict[str, str]] = {}
        self._rev_quality: dict[str, str] = {}
        self._tr_cache: dict[str, dict[str, str]] = {}
        self._note_cache: dict[str, Note] = {}
        self._ann_cache: dict[str, Annotation] = {}
        self._chat_msg_cache: dict[int, ChatMessage] = {}
        self._chat_thread_cache: dict[str, ChatThread] = {}
        self._thread_msgs_cache: dict[str, list[ChatMessage]] = {}
        self._paper_cache: dict[str, Paper] = {}
        self._article_blocks_cache: dict[str, list[ArticleBlock]] = {}

    async def render(self, hit: _HitRow) -> SearchHit:
        if hit.kind == "body":
            return await self._render_body(hit)
        if hit.kind == "biblio":
            return await self._render_biblio(hit)
        if hit.kind == "note":
            return await self._render_note(hit)
        if hit.kind == "annotation":
            return await self._render_annotation(hit)
        if hit.kind == "chat":
            return await self._render_chat(hit)
        if hit.kind == "article":
            return await self._render_article(hit)
        raise ProblemException("internal_error", detail=f"unexpected hit kind: {hit.kind}")

    # -- リビジョン索引(block_search_index を revision 単位で一括ロード) -----------------
    async def _rev_index(self, revision_id: str) -> tuple[dict[str, _RevBlockInfo], dict[str, str]]:
        if revision_id in self._rev_blocks:
            return self._rev_blocks[revision_id], self._rev_headings[revision_id]
        rows = (
            await self.db.execute(
                text(
                    "SELECT block_id, block_type, section_path, section_label, element_label, "
                    "paragraph_ordinal, page, source_text FROM block_search_index "
                    "WHERE revision_id = CAST(:rid AS uuid)"
                ),
                {"rid": revision_id},
            )
        ).all()
        blocks: dict[str, _RevBlockInfo] = {}
        headings: dict[str, str] = {}
        for r in rows:
            blocks[r.block_id] = _RevBlockInfo(
                block_type=r.block_type,
                section_path=r.section_path,
                section_label=r.section_label,
                element_label=r.element_label,
                paragraph_ordinal=r.paragraph_ordinal,
                page=r.page,
                source_text=r.source_text,
            )
            if r.block_type == "heading" and r.section_path not in headings:
                headings[r.section_path] = r.source_text
        self._rev_blocks[revision_id] = blocks
        self._rev_headings[revision_id] = headings
        return blocks, headings

    async def _quality_of(self, revision_id: str) -> str:
        if revision_id in self._rev_quality:
            return self._rev_quality[revision_id]
        quality = await self.db.scalar(
            select(DocumentRevision.quality_level).where(DocumentRevision.id == revision_id)
        )
        resolved = quality or "A"
        self._rev_quality[revision_id] = resolved
        return resolved

    async def _translation_map(self, revision_id: str) -> dict[str, str]:
        if revision_id in self._tr_cache:
            return self._tr_cache[revision_id]
        rows = (
            await self.db.execute(
                text(
                    "SELECT tu.block_id, tu.text_ja, ts.scope FROM translation_units tu "
                    "JOIN translation_sets ts ON ts.id = tu.set_id "
                    "WHERE ts.revision_id = CAST(:rid AS uuid) AND ts.style = :style "
                    "AND (ts.scope = 'shared' OR ts.user_id = CAST(:uid AS uuid))"
                ),
                {"rid": revision_id, "style": self.style, "uid": self.user_id},
            )
        ).all()
        winner: dict[str, str] = {}
        for r in rows:
            if r.scope == "personal":
                winner[r.block_id] = r.text_ja
            else:
                winner.setdefault(r.block_id, r.text_ja)
        self._tr_cache[revision_id] = winner
        return winner

    async def _anchor_display(self, anchor: Any) -> str | None:
        if not isinstance(anchor, dict):
            return None
        revision_id = str(anchor.get("revision_id") or "")
        block_id = str(anchor.get("block_id") or "")
        if not revision_id or not block_id:
            return None
        blocks, _headings = await self._rev_index(revision_id)
        info = blocks.get(block_id)
        if info is None:
            return None
        row = BlockRow(
            block_id=block_id,
            block_type=info.block_type,
            section_path=info.section_path,
            section_label=info.section_label,
            paragraph_ordinal=info.paragraph_ordinal,
            element_label=info.element_label,
        )
        return derive_display(row)

    # -- 本文 -------------------------------------------------------------------------
    async def _render_body(self, hit: _HitRow) -> SearchHit:
        ref = hit.ref
        revision_id = str(ref["revision_id"])
        block_id = str(ref["block_id"])
        matched: list[MatchedInValue] = matched_in(
            matched_source=bool(ref.get("matched_source")),
            matched_translation=bool(ref.get("matched_translation")),
        )
        lang = snippet_lang_for(matched)
        quality = await self._quality_of(revision_id)
        blocks, headings = await self._rev_index(revision_id)
        info = blocks.get(block_id)
        display = _compose_body_display(info, headings, quality) if info else ""
        if lang == "en":
            snippet_source = info.source_text if info else ""
        else:
            tmap = await self._translation_map(revision_id)
            snippet_source = tmap.get(block_id, info.source_text if info else "")
        snippet = await _pg_snippet(self.db, snippet_source, self.query)
        anchor = AnchorRef(
            revision_id=revision_id,
            block_id=block_id,
            start=None,
            end=None,
            quote=None,
            side="source",
            display=display,
        )
        return SearchHit(
            source="body",
            matched_in=matched,
            display=display,
            snippet=snippet,
            snippet_lang=lang,
            target=SearchHitTargetViewer(library_item_id=hit.library_item_id, anchor=anchor),
        )

    # -- 書誌(source="body" に合流。plans/11 §6.1) -----------------------------------
    async def _paper_of(self, library_item_id: str) -> Paper:
        if library_item_id in self._paper_cache:
            return self._paper_cache[library_item_id]
        paper = (
            await self.db.execute(
                select(Paper)
                .join(LibraryItem, LibraryItem.paper_id == Paper.id)
                .where(LibraryItem.id == library_item_id)
            )
        ).scalar_one()
        self._paper_cache[library_item_id] = paper
        return paper

    async def _render_biblio(self, hit: _HitRow) -> SearchHit:
        ref = hit.ref
        title_matched = bool(ref.get("title_matched"))
        abstract_matched = bool(ref.get("abstract_matched"))
        ja_matched = bool(ref.get("abstract_ja_matched"))
        matched = matched_in(
            matched_source=title_matched or abstract_matched, matched_translation=ja_matched
        )
        lang = snippet_lang_for(matched)
        paper = await self._paper_of(hit.library_item_id)
        if lang == "en":
            snippet_source = paper.title if title_matched else (paper.abstract or "")
        else:
            snippet_source = paper.abstract_ja or ""
        snippet = await _pg_snippet(self.db, snippet_source, self.query)
        return SearchHit(
            source="body",
            matched_in=matched,
            display="書誌",
            snippet=snippet,
            snippet_lang=lang,
            target=SearchHitTargetViewer(library_item_id=hit.library_item_id, anchor=None),
        )

    # -- メモ ------------------------------------------------------------------------
    async def _note_of(self, note_id: str) -> Note:
        if note_id in self._note_cache:
            return self._note_cache[note_id]
        note = await self.db.get(Note, note_id)
        if note is None:
            raise ProblemException("internal_error", detail="note が見つかりません")
        self._note_cache[note_id] = note
        return note

    async def _render_note(self, hit: _HitRow) -> SearchHit:
        ref = hit.ref
        note_id = str(ref["note_id"])
        note = await self._note_of(note_id)
        display = f"メモ · {note.created_at.month}/{note.created_at.day}"
        anchors = note.anchors if isinstance(note.anchors, list) else []
        if anchors:
            anchor_display = await self._anchor_display(anchors[0])
            if anchor_display:
                display = f"{display} · 根拠: {anchor_display}"
        snippet_source = note.body_md if ref.get("body_matched") else note.title
        snippet = await _pg_snippet(self.db, snippet_source, self.query)
        return SearchHit(
            source="note",
            matched_in=None,
            display=display,
            snippet=snippet,
            snippet_lang="ja",
            target=SearchHitTargetNote(library_item_id=hit.library_item_id, note_id=note_id),
        )

    # -- 注釈(コメント) ---------------------------------------------------------------
    async def _annotation_of(self, annotation_id: str) -> Annotation:
        if annotation_id in self._ann_cache:
            return self._ann_cache[annotation_id]
        ann = await self.db.get(Annotation, annotation_id)
        if ann is None:
            raise ProblemException("internal_error", detail="annotation が見つかりません")
        self._ann_cache[annotation_id] = ann
        return ann

    async def _render_annotation(self, hit: _HitRow) -> SearchHit:
        ref = hit.ref
        annotation_id = str(ref["annotation_id"])
        anchor_raw = ref.get("anchor")
        anchor: dict[str, Any] = anchor_raw if isinstance(anchor_raw, dict) else {}
        ann = await self._annotation_of(annotation_id)
        anchor_display = await self._anchor_display(anchor) or ""
        display = f"注釈 · {ann.created_at.month}/{ann.created_at.day}"
        if anchor_display:
            display = f"{display} · {anchor_display}"
        snippet = await _pg_snippet(self.db, ann.body or "", self.query)
        anchor_ref = AnchorRef(
            revision_id=str(anchor.get("revision_id") or ""),
            block_id=str(anchor.get("block_id") or ""),
            start=anchor.get("start"),
            end=anchor.get("end"),
            quote=anchor.get("quote"),
            side=anchor.get("side", "source"),
            display=anchor_display,
        )
        return SearchHit(
            source="annotation",
            matched_in=None,
            display=display,
            snippet=snippet,
            snippet_lang="ja",
            target=SearchHitTargetViewer(library_item_id=hit.library_item_id, anchor=anchor_ref),
        )

    # -- チャット --------------------------------------------------------------------
    async def _chat_message_of(self, message_id: int) -> ChatMessage:
        if message_id in self._chat_msg_cache:
            return self._chat_msg_cache[message_id]
        msg = await self.db.get(ChatMessage, message_id)
        if msg is None:
            raise ProblemException("internal_error", detail="chat message が見つかりません")
        self._chat_msg_cache[message_id] = msg
        return msg

    async def _chat_thread_of(self, thread_id: str) -> ChatThread:
        if thread_id in self._chat_thread_cache:
            return self._chat_thread_cache[thread_id]
        th = await self.db.get(ChatThread, thread_id)
        if th is None:
            raise ProblemException("internal_error", detail="chat thread が見つかりません")
        self._chat_thread_cache[thread_id] = th
        return th

    async def _thread_messages(self, thread_id: str) -> list[ChatMessage]:
        if thread_id in self._thread_msgs_cache:
            return self._thread_msgs_cache[thread_id]
        rows = (
            (
                await self.db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == thread_id)
                    .order_by(ChatMessage.id)
                )
            )
            .scalars()
            .all()
        )
        msgs = list(rows)
        self._thread_msgs_cache[thread_id] = msgs
        return msgs

    async def _render_chat(self, hit: _HitRow) -> SearchHit:
        ref = hit.ref
        thread_id = str(ref["thread_id"])
        message_id = int(ref["message_id"])
        msg = await self._chat_message_of(message_id)
        thread = await self._chat_thread_of(thread_id)
        thread_name = "メインスレッド" if thread.is_main else thread.title
        display = f"{thread_name} · {msg.created_at.month}/{msg.created_at.day}"
        siblings = await self._thread_messages(thread_id)
        idx = next((i for i, m in enumerate(siblings) if m.id == msg.id), None)
        other: ChatMessage | None = None
        if idx is not None:
            nxt = siblings[idx + 1] if idx + 1 < len(siblings) else None
            prev = siblings[idx - 1] if idx - 1 >= 0 else None
            if msg.role == "user" and nxt is not None and nxt.role == "assistant":
                other = nxt
            elif msg.role == "assistant" and prev is not None and prev.role == "user":
                other = prev
        hit_snippet = await _pg_snippet(self.db, msg.text_plain or "", self.query)
        qa_snippet = chat_qa_snippet(
            hit_role=msg.role,
            hit_snippet_html=hit_snippet,
            other_text_plain=other.text_plain if other else None,
        )
        return SearchHit(
            source="chat",
            matched_in=None,
            display=display,
            snippet=qa_snippet,
            snippet_lang="ja",
            target=SearchHitTargetChat(
                library_item_id=hit.library_item_id,
                thread_id=thread_id,
                message_id=str(message_id),
            ),
        )

    # -- 記事(M2-15。plans/11 §3.2 (g)・§4) -------------------------------------------
    async def _article_blocks(self, article_id: str) -> list[ArticleBlock]:
        if article_id in self._article_blocks_cache:
            return self._article_blocks_cache[article_id]
        rows = (
            (
                await self.db.execute(
                    select(ArticleBlock)
                    .where(ArticleBlock.article_id == article_id)
                    .order_by(ArticleBlock.position)
                )
            )
            .scalars()
            .all()
        )
        blocks = list(rows)
        self._article_blocks_cache[article_id] = blocks
        return blocks

    async def _render_article(self, hit: _HitRow) -> SearchHit:
        ref = hit.ref
        article_id = str(ref["article_id"])
        article_block_id = str(ref["article_block_id"])
        blocks = await self._article_blocks(article_id)
        heading_text: str | None = None
        target_block: ArticleBlock | None = None
        for blk in blocks:
            if blk.type == "heading" and isinstance(blk.content, dict):
                heading = blk.content.get("heading")
                if isinstance(heading, dict) and heading.get("text"):
                    heading_text = str(heading["text"])
            if str(blk.id) == article_block_id:
                target_block = blk
                break
        display = f"「{heading_text}」セクション" if heading_text else "記事冒頭"
        snippet_source = target_block.text_plain if target_block is not None else ""
        snippet = await _pg_snippet(self.db, snippet_source, self.query)
        return SearchHit(
            source="article",
            matched_in=None,
            display=display,
            snippet=snippet,
            snippet_lang="ja",
            target=SearchHitTargetArticle(
                library_item_id=hit.library_item_id, article_block_id=article_block_id
            ),
        )


# ============================================================================
# LibraryItemSummary 一括構築(グループヘッダ用。plans/11 §3.5)
# ============================================================================
async def _library_item_summaries(
    db: DbDep, library_item_ids: list[str]
) -> dict[str, LibraryItemSummary]:
    if not library_item_ids:
        return {}
    rows = (
        await db.execute(
            select(LibraryItem, Paper)
            .join(Paper, Paper.id == LibraryItem.paper_id)
            .where(LibraryItem.id.in_(library_item_ids))
        )
    ).all()
    pairs: dict[str, tuple[LibraryItem, Paper]] = {str(it.id): (it, p) for it, p in rows}

    def _revision_id_of(item: LibraryItem, paper: Paper) -> str | None:
        rp = item.reading_position
        if isinstance(rp, dict) and rp.get("revision_id"):
            return str(rp["revision_id"])
        return str(paper.latest_revision_id) if paper.latest_revision_id else None

    rev_ids = {rid for it, p in pairs.values() if (rid := _revision_id_of(it, p)) is not None}
    quality_map: dict[str, str] = {}
    if rev_ids:
        qrows = (
            await db.execute(
                select(DocumentRevision.id, DocumentRevision.quality_level).where(
                    DocumentRevision.id.in_(rev_ids)
                )
            )
        ).all()
        quality_map = {str(rid): q for rid, q in qrows}

    out: dict[str, LibraryItemSummary] = {}
    for lid, (item, paper) in pairs.items():
        rid = _revision_id_of(item, paper)
        quality = quality_map.get(rid, "B") if rid else "B"
        last_position: LastPosition | None = None
        rp = item.reading_position
        if isinstance(rp, dict) and rp.get("revision_id") and rp.get("block_id"):
            updated_at_iso = item.updated_at.isoformat() if item.updated_at else ""
            last_position = LastPosition(
                revision_id=str(rp["revision_id"]),
                block_id=str(rp["block_id"]),
                mode=rp.get("mode") or rp.get("view_mode") or "translation",
                section_display="",
                saved_at=rp.get("saved_at") or updated_at_iso,
            )
        out[lid] = LibraryItemSummary(
            id=lid,
            paper=build_paper_bib(paper),
            status=item.status,
            priority=item.priority,
            deadline=item.deadline.isoformat() if item.deadline else None,
            tags=list(item.tags or []),
            suggested_tags=list(item.suggested_tags or []),
            quality_level=quality,
            source="arxiv" if paper.arxiv_id else "upload",
            progress_pct=100 if item.status == "done" else 0,
            comprehension=item.understanding,
            importance=item.importance,
            reading_seconds_total=item.total_active_seconds,
            one_line_note=item.one_line_note or None,
            summary_3line=paper.summary_lines,
            thumbnail_url=None,
            pipeline=None,
            last_position=last_position,
            added_at=item.added_at.isoformat(),
            updated_at=item.updated_at.isoformat(),
            finished_at=item.finished_at.isoformat() if item.finished_at else None,
        )
    return out


# ============================================================================
# GET /api/search(全結果画面 4e。plans/03 §15.1)
# ============================================================================
@router.get("/api/search", response_model=SearchResponse, operation_id="search_all")
async def search_all(
    q: str,
    user: CurrentUser,
    db: DbDep,
    source: SearchSourceFilter = "all",
    library_item_id: str | None = Query(default=None),
    sort: SearchSort = "relevance",
    cursor: str | None = Query(default=None),
    limit: int = Query(default=_PAGE_LIMIT_DEFAULT, ge=1, le=_PAGE_LIMIT_MAX),
) -> SearchResponse:
    query = _validate_query_param(q)
    style = _resolve_style(user)
    all_hits = await _fetch_all_hits(db, str(user.id), query, style)

    facets = await _compute_facets(db, all_hits)
    total = len(all_hits)
    paper_count = len({h.library_item_id for h in all_hits})

    filtered = _apply_filters(all_hits, source=source, library_item_id=library_item_id)
    groups = _sort_groups(_build_groups(filtered), sort)
    after_cursor = _apply_group_cursor(groups, cursor)
    page = after_cursor[:limit]
    has_more = len(after_cursor) > limit
    next_cursor = _encode_group_cursor(sort, page[-1]) if has_more and page else None

    summaries = await _library_item_summaries(db, [g.library_item_id for g in page])
    article_group_ids = [
        g.library_item_id for g in page if any(h.kind == "article" for h in g.hits)
    ]
    articles = await _articles_for(db, article_group_ids)
    renderer = _SearchRenderer(db, str(user.id), style, query)
    result_groups: list[SearchGroup] = []
    for g in page:
        top = sorted(g.hits, key=lambda h: (-h.score, -_ts(h.hit_at)))[:_GROUP_TOP_HITS]
        rendered = [await renderer.render(h) for h in top]
        summary = summaries.get(g.library_item_id)
        if summary is None:
            continue
        article: SearchGroupArticle | None = None
        art = articles.get(g.library_item_id)
        if art is not None:
            article = SearchGroupArticle(
                article_id=str(art.id), title=art.title, generated_at=art.generated_at.isoformat()
            )
        result_groups.append(
            SearchGroup(library_item=summary, hit_count=g.hit_count, article=article, hits=rendered)
        )

    return SearchResponse(
        query=query,
        total=total,
        paper_count=paper_count,
        facets=facets,
        groups=result_groups,
        next_cursor=next_cursor,
    )


# ============================================================================
# GET /api/search/preview(1e ドロップダウン。plans/03 §15.2)
# ============================================================================
@router.get(
    "/api/search/preview", response_model=SearchPreviewResponse, operation_id="search_preview"
)
async def search_preview(q: str, user: CurrentUser, db: DbDep) -> SearchPreviewResponse:
    query = _validate_query_param(q)
    style = _resolve_style(user)
    all_hits = await _fetch_all_hits(db, str(user.id), query, style)
    top = sorted(all_hits, key=lambda h: (-h.score, -_ts(h.hit_at), h.library_item_id))[:3]

    titles = await _paper_titles_for(db, [h.library_item_id for h in top])
    renderer = _SearchRenderer(db, str(user.id), style, query)
    items: list[SearchHitWithPaper] = []
    for h in top:
        rendered = await renderer.render(h)
        items.append(
            SearchHitWithPaper(
                source=rendered.source,
                matched_in=rendered.matched_in,
                display=rendered.display,
                snippet=rendered.snippet,
                snippet_lang=rendered.snippet_lang,
                target=rendered.target,
                library_item=SearchPreviewPaper(
                    id=h.library_item_id, title=titles.get(h.library_item_id, "")
                ),
            )
        )
    return SearchPreviewResponse(total=len(all_hits), items=items)


# ============================================================================
# GET /api/revisions/{revision_id}/search(論文内検索 `/`。plans/03 §6.7)
# ============================================================================
@router.get(
    "/api/revisions/{revision_id}/search",
    response_model=InPaperSearchResponse,
    operation_id="search_in_paper",
)
async def search_in_paper(
    revision_id: str,
    q: str,
    user: CurrentUser,
    db: DbDep,
    limit: int = Query(default=_IN_PAPER_LIMIT_DEFAULT, ge=1, le=_IN_PAPER_LIMIT_MAX),
) -> InPaperSearchResponse:
    query = _validate_query_param(q)
    revision, _paper = await resolve_accessible_revision(db, revision_id, user)
    style = _resolve_style(user)

    source_rows = (
        await db.execute(
            text(
                "SELECT block_id FROM block_search_index "
                "WHERE revision_id = CAST(:rid AS uuid) "
                "AND source_text &@~ (pgroonga_query_escape(:q), NULL, "
                "'pgroonga_block_search_index_source_text')"
                "::pgroonga_full_text_search_condition"
            ),
            {"rid": revision_id, "q": query},
        )
    ).all()
    source_hit_ids = {r.block_id for r in source_rows}

    translation_rows = (
        await db.execute(
            text(
                "SELECT tu.block_id, tu.text_ja FROM translation_units tu "
                "JOIN translation_sets ts ON ts.id = tu.set_id "
                "WHERE ts.revision_id = CAST(:rid AS uuid) AND ts.style = :style "
                "AND (ts.scope = 'shared' OR ts.user_id = CAST(:uid AS uuid)) "
                "AND tu.text_ja &@~ (pgroonga_query_escape(:q), NULL, "
                "'pgroonga_translation_units_text_ja')"
                "::pgroonga_full_text_search_condition "
                "AND tu.set_id = ("
                "  SELECT u2.set_id FROM translation_units u2 "
                "  JOIN translation_sets s2 ON s2.id = u2.set_id "
                "  WHERE s2.revision_id = ts.revision_id AND s2.style = :style "
                "    AND (s2.scope = 'shared' OR s2.user_id = CAST(:uid AS uuid)) "
                "    AND u2.block_id = tu.block_id "
                "  ORDER BY (s2.scope = 'personal') DESC LIMIT 1"
                ")"
            ),
            {"rid": revision_id, "style": style, "uid": str(user.id), "q": query},
        )
    ).all()
    translation_text_by_block = {r.block_id: r.text_ja for r in translation_rows}

    rows = (
        await db.execute(
            text(
                "SELECT block_id, block_type, section_path, section_label, element_label, "
                "paragraph_ordinal, page, source_text, position FROM block_search_index "
                "WHERE revision_id = CAST(:rid AS uuid) ORDER BY position"
            ),
            {"rid": revision_id},
        )
    ).all()
    headings = {r.section_path: r.source_text for r in rows if r.block_type == "heading"}

    items: list[InPaperSearchItem] = []
    for r in rows:
        matched_source = r.block_id in source_hit_ids
        matched_translation = r.block_id in translation_text_by_block
        if not matched_source and not matched_translation:
            continue
        matched = matched_in(matched_source=matched_source, matched_translation=matched_translation)
        lang = snippet_lang_for(matched)
        info = _RevBlockInfo(
            block_type=r.block_type,
            section_path=r.section_path,
            section_label=r.section_label,
            element_label=r.element_label,
            paragraph_ordinal=r.paragraph_ordinal,
            page=r.page,
            source_text=r.source_text,
        )
        display = _compose_body_display(info, headings, revision.quality_level)
        ja_text = translation_text_by_block.get(r.block_id, r.source_text)
        snippet_source = r.source_text if lang == "en" else ja_text
        snippet = await _pg_snippet(db, snippet_source, query)
        section_id = r.section_path.split("/")[-1] if r.section_path else ""
        items.append(
            InPaperSearchItem(
                block_id=r.block_id,
                section_id=section_id,
                display=display,
                matched_in=matched,
                snippet=snippet,
            )
        )
        if len(items) >= limit:
            break

    return InPaperSearchResponse(items=items)
