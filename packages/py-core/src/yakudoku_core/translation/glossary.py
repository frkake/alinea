"""用語集スナップショット凍結・3 層 CRUD・逆引き検索・promote(plans/06 §8・docs/03 §7)。

M0-17 の範囲は **スナップショット部のみ**(3 層マージの凍結・ハッシュ・プロンプト反映)。
M1-14/M1-15 で 3 層 CRUD・逆引き検索(§8.3)・訳語変更の適用に使う personal フォーク解決
(§9.2)・promote(§8.5)を追加する。

適用優先度: 論文ローカル(paper)> ユーザー(user)> グローバル既定(global)。翻訳時に適用
用語のスナップショットを凍結し、TranslationSet に記録する(再現性)。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yakudoku_core.db.models import Glossary, GlossaryTerm, LibraryItem, Paper, TranslationSet

# origin の優先順(数値が大きいほど優先)。docs/03 §7 の 3 層優先度。
_ORIGIN_PRIORITY = {"global": 0, "user": 1, "paper": 2}


class DuplicateTermError(ValueError):
    """同一 glossary 内に同じ ``lower(source_term)`` の語が既にある(plans/02 §4.5 の一意制約)。"""


def glossary_hash(snapshot: list[dict[str, Any]]) -> str:
    """スナップショットの正準ハッシュ(plans/06 §8.1)。

    ``sha256(canonical_json(snapshot))[:16]``。canonical_json はキー順固定・空白なしの
    UTF-8。plans/03 §7.1 の ``glossary_snapshot_id`` はこの値を返す(導出識別子)。
    """
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


async def build_snapshot(
    session: AsyncSession,
    *,
    user_id: str | None,
    library_item_id: str | None,
    shared: bool,
) -> tuple[list[dict[str, Any]], str]:
    """3 層マージして ``GlossarySnapshotJson`` と ``glossary_hash`` を返す(plans/06 §8.1)。

    - 収集: ``scope='global'`` の全語、(shared でなければ)``scope='user' AND user_id`` と
      ``scope='paper' AND library_item_id`` の語。**shared 構築時は global のみ**
      (plans/02 §3.4 の制約)。paper スコープからは**ユーザー確定語のみ**
      (``auto_extracted=false``。自動抽出の提案は共有可能性を壊さないため除外)。
    - マージ: ``lower(source_term)`` をキーに paper > user > global で 1 語 1 訳に確定。
    - 正規化: ``source_term`` の小文字順でソートし平坦化。
    """
    stmt = select(GlossaryTerm, Glossary.scope).join(
        Glossary, Glossary.id == GlossaryTerm.glossary_id
    )
    if shared:
        stmt = stmt.where(Glossary.scope == "global")
    else:
        from sqlalchemy import and_, or_

        conditions = [Glossary.scope == "global"]
        if user_id is not None:
            conditions.append(and_(Glossary.scope == "user", Glossary.user_id == user_id))
        if library_item_id is not None:
            conditions.append(
                and_(
                    Glossary.scope == "paper",
                    Glossary.library_item_id == library_item_id,
                    GlossaryTerm.auto_extracted.is_(False),
                )
            )
        stmt = stmt.where(or_(*conditions))

    rows = (await session.execute(stmt)).all()

    # lower(source_term) をキーに、origin 優先度の高い語で上書き確定。
    merged: dict[str, dict[str, Any]] = {}
    for term, scope in rows:
        entry = {
            "source_term": term.source_term,
            "target_term": term.target_term,
            "policy": term.policy,
            "origin": scope,
        }
        key = term.source_term.lower()
        current = merged.get(key)
        if current is None or _ORIGIN_PRIORITY[scope] >= _ORIGIN_PRIORITY[current["origin"]]:
            merged[key] = entry

    snapshot = [merged[k] for k in sorted(merged)]
    return snapshot, glossary_hash(snapshot)


def format_glossary_lines(snapshot: list[dict[str, Any]]) -> str:
    """system[1] の用語表テキスト(plans/06 §5.3・§8.2)。空なら ``(用語表なし)``。"""
    if not snapshot:
        return "(用語表なし)"
    lines: list[str] = []
    for e in snapshot:
        src = e["source_term"]
        tgt = e["target_term"]
        policy = e.get("policy", "translate")
        if policy == "keep_original":
            lines.append(f"- {src} → 原語のまま [policy=keep_original]")
        elif policy == "both":
            lines.append(f"- {src} → {tgt} [初出時のみ「{tgt}({src})」と併記] [policy=both]")
        else:
            lines.append(f"- {src} → {tgt} [policy=translate]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3 層 CRUD(plans/03 §7.9・plans/02 §4.5)
# ---------------------------------------------------------------------------


async def get_glossary(
    session: AsyncSession,
    *,
    scope: str,
    user_id: str | None = None,
    library_item_id: str | None = None,
) -> Glossary | None:
    """指定スコープの ``glossaries`` 行を返す(存在しなければ None)。"""
    stmt = select(Glossary).where(Glossary.scope == scope)
    if scope == "user":
        stmt = stmt.where(Glossary.user_id == user_id)
    elif scope == "paper":
        stmt = stmt.where(Glossary.library_item_id == library_item_id)
    result: Glossary | None = await session.scalar(stmt)
    return result


async def get_or_create_glossary(
    session: AsyncSession,
    *,
    scope: str,
    user_id: str | None = None,
    library_item_id: str | None = None,
) -> Glossary:
    """scope=user/paper の入れ物行を取得、無ければ作成する(一意インデックスにより冪等)。"""
    existing = await get_glossary(
        session, scope=scope, user_id=user_id, library_item_id=library_item_id
    )
    if existing is not None:
        return existing
    glossary = Glossary(
        id=str(uuid.uuid4()),
        scope=scope,
        user_id=user_id if scope == "user" else None,
        library_item_id=library_item_id if scope == "paper" else None,
    )
    session.add(glossary)
    await session.flush()
    return glossary


async def list_terms(
    session: AsyncSession,
    *,
    user_id: str | None = None,
    library_item_id: str | None = None,
) -> list[tuple[GlossaryTerm, Glossary]]:
    """global(常に含む)+ 要求スコープ(user_id / library_item_id いずれか)の語一覧。

    ``source_term`` 昇順(大文字小文字無視。plans/03 §7.9)。ページングなし。
    """
    from sqlalchemy import and_, or_

    conditions = [Glossary.scope == "global"]
    if user_id is not None:
        conditions.append(and_(Glossary.scope == "user", Glossary.user_id == user_id))
    if library_item_id is not None:
        conditions.append(
            and_(Glossary.scope == "paper", Glossary.library_item_id == library_item_id)
        )
    stmt = (
        select(GlossaryTerm, Glossary)
        .join(Glossary, Glossary.id == GlossaryTerm.glossary_id)
        .where(or_(*conditions))
    )
    rows = (await session.execute(stmt)).all()
    ordered = sorted(rows, key=lambda row: row[0].source_term.lower())
    return [(term, glossary) for term, glossary in ordered]


async def get_term(session: AsyncSession, term_id: str) -> tuple[GlossaryTerm, Glossary] | None:
    """``term_id`` の語と、その所属 glossary を返す。"""
    term = await session.get(GlossaryTerm, term_id)
    if term is None:
        return None
    glossary = await session.get(Glossary, term.glossary_id)
    if glossary is None:
        return None
    return term, glossary


async def _term_exists(session: AsyncSession, glossary_id: str, source_term: str) -> bool:
    existing = await session.scalar(
        select(GlossaryTerm.id).where(
            GlossaryTerm.glossary_id == glossary_id,
            GlossaryTerm.source_term.ilike(source_term),
        )
    )
    return existing is not None


async def create_term(
    session: AsyncSession,
    *,
    scope: str,
    source_term: str,
    target_term: str,
    policy: str,
    pos_label: str = "",
    user_id: str | None = None,
    library_item_id: str | None = None,
    auto_extracted: bool = False,
) -> GlossaryTerm:
    """scope=user/paper の語を作成する(plans/03 §7.9 POST)。global は呼び出し側で 403 とする。"""
    glossary = await get_or_create_glossary(
        session, scope=scope, user_id=user_id, library_item_id=library_item_id
    )
    if await _term_exists(session, str(glossary.id), source_term):
        raise DuplicateTermError(f"'{source_term}' はすでに存在します")
    term = GlossaryTerm(
        id=str(uuid.uuid4()),
        glossary_id=str(glossary.id),
        source_term=source_term,
        target_term=target_term,
        pos_label=pos_label,
        policy=policy,
        auto_extracted=auto_extracted,
    )
    session.add(term)
    await session.flush()
    return term


async def update_term(
    session: AsyncSession,
    term: GlossaryTerm,
    *,
    target_term: str | None = None,
    policy: str | None = None,
) -> GlossaryTerm:
    """語を更新する(PATCH の実適用部)。

    paper スコープの自動抽出語は確定操作(訳語確定・修正)で ``auto_extracted=False`` に
    更新する(plans/06 §8.4-1)。
    """
    if target_term is not None:
        term.target_term = target_term
    if policy is not None:
        term.policy = policy
    if term.auto_extracted:
        term.auto_extracted = False
    await session.flush()
    return term


async def delete_term(session: AsyncSession, term: GlossaryTerm) -> None:
    await session.delete(term)
    await session.flush()


async def promote_term(session: AsyncSession, term: GlossaryTerm, *, user_id: str) -> GlossaryTerm:
    """論文ローカル→ユーザー用語集へ複製する(plans/06 §8.5)。元の paper term は残す。

    再翻訳ジョブは起動しない(「次の論文から効く」)。ユーザー用語集に同名語が既にある場合は
    その語を新しい訳語・訳し方で上書きする(昇格は「この訳で確定させる」操作のため)。
    """
    user_glossary = await get_or_create_glossary(session, scope="user", user_id=user_id)
    existing = await session.scalar(
        select(GlossaryTerm).where(
            GlossaryTerm.glossary_id == user_glossary.id,
            GlossaryTerm.source_term.ilike(term.source_term),
        )
    )
    if existing is not None:
        existing.target_term = term.target_term
        existing.policy = term.policy
        existing.pos_label = term.pos_label
        existing.auto_extracted = False
        await session.flush()
        return existing
    promoted = GlossaryTerm(
        id=str(uuid.uuid4()),
        glossary_id=str(user_glossary.id),
        source_term=term.source_term,
        target_term=term.target_term,
        pos_label=term.pos_label,
        policy=term.policy,
        auto_extracted=False,
    )
    session.add(promoted)
    await session.flush()
    return promoted


# ---------------------------------------------------------------------------
# 訳語変更 → 影響ブロック検索(逆引きインデックス。plans/06 §8.3)
# ---------------------------------------------------------------------------


def _word_boundary_re(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", re.IGNORECASE)


async def find_affected_blocks(
    session: AsyncSession, *, revision_id: str, source_term: str
) -> list[str]:
    """``source_term`` を含む翻訳対象スコープ内ブロックの block_id 一覧(plans/06 §8.3)。

    PGroonga(``pgroonga_block_search_index_source_text``)で候補抽出し、語境界の正規表現で
    厳密化する(``&@~`` の部分一致による過剰ヒットを除去)。
    """
    rows = (
        await session.execute(
            text(
                "SELECT block_id, source_text FROM block_search_index "
                "WHERE revision_id = :revision_id AND in_translation_scope "
                "AND source_text &@~ :source_term"
            ),
            {"revision_id": revision_id, "source_term": source_term},
        )
    ).all()
    pattern = _word_boundary_re(source_term)
    return [str(block_id) for block_id, source_text in rows if pattern.search(source_text or "")]


# ---------------------------------------------------------------------------
# 訳語変更の適用対象(revision と実施ユーザー)の解決(plans/06 §8.4)
# ---------------------------------------------------------------------------


async def target_contexts_for_glossary(
    session: AsyncSession, glossary: Glossary
) -> list[dict[str, str]]:
    """訳語変更を適用する対象(revision_id・user_id・library_item_id・paper_id)の一覧。

    - scope=paper: その論文(library_item)の最新リビジョン 1 件。
    - scope=user: そのユーザーが自然訳(natural)の shared/personal セットを持つ全リビジョン
      (§9.2(a): ユーザー用語集の適用がフォーク契機になる)。
    """
    if glossary.scope == "paper":
        if glossary.library_item_id is None:
            return []
        li = await session.get(LibraryItem, glossary.library_item_id)
        if li is None:
            return []
        paper = await session.get(Paper, li.paper_id)
        if paper is None or paper.latest_revision_id is None:
            return []
        return [
            {
                "revision_id": str(paper.latest_revision_id),
                "user_id": str(li.user_id),
                "library_item_id": str(li.id),
                "paper_id": str(paper.id),
            }
        ]
    if glossary.scope == "user":
        if glossary.user_id is None:
            return []
        user_id = str(glossary.user_id)
        rows = (
            await session.execute(
                select(LibraryItem.id, LibraryItem.paper_id, Paper.latest_revision_id)
                .join(Paper, Paper.id == LibraryItem.paper_id)
                .where(LibraryItem.user_id == user_id, Paper.latest_revision_id.is_not(None))
            )
        ).all()
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for li_id, paper_id, revision_id in rows:
            rid = str(revision_id)
            if rid in seen:
                continue
            has_set = await session.scalar(
                select(TranslationSet.id).where(
                    TranslationSet.revision_id == rid,
                    TranslationSet.style == "natural",
                    (TranslationSet.scope == "shared")
                    | ((TranslationSet.scope == "personal") & (TranslationSet.user_id == user_id)),
                )
            )
            if has_set is None:
                continue
            seen.add(rid)
            out.append(
                {
                    "revision_id": rid,
                    "user_id": user_id,
                    "library_item_id": str(li_id),
                    "paper_id": str(paper_id),
                }
            )
        return out
    return []


# ---------------------------------------------------------------------------
# personal フォークの解決(plans/06 §9.2。glossary 適用/手動編集/proposal 採用で共有)
# ---------------------------------------------------------------------------


async def resolve_or_create_personal_set(
    session: AsyncSession, *, revision_id: str, style: str, user_id: str
) -> TranslationSet:
    """personal セットを解決、無ければ shared から差分保存フォークを作る(plans/06 §9.2)。

    既存 personal セットはそのまま返す(glossary_snapshot は呼び出し側の必要に応じて
    上書きすること。§8.4-2 の「置き換え」はここでは行わない=手動編集・proposal 採用からの
    呼び出しでは既存スナップショットを保全する)。
    """
    personal = await session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == revision_id,
            TranslationSet.style == style,
            TranslationSet.scope == "personal",
            TranslationSet.user_id == user_id,
        )
    )
    if personal is not None:
        return personal
    shared = await session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == revision_id,
            TranslationSet.style == style,
            TranslationSet.scope == "shared",
        )
    )
    new_set = TranslationSet(
        id=str(uuid.uuid4()),
        revision_id=revision_id,
        style=style,
        scope="personal",
        user_id=user_id,
        base_set_id=str(shared.id) if shared is not None else None,
        glossary_snapshot=list(shared.glossary_snapshot) if shared is not None else [],
        status=shared.status if shared is not None else "pending",
    )
    session.add(new_set)
    await session.flush()
    return new_set
