"""用語集 3 層 CRUD・逆引き検索・適用対象解決・personal フォーク解決の直接呼び出しテスト
(plans/06 §8.3-§8.5・§9.2、docs/03 §7)。

M1-14/M1-15 で追加された ``alinea_core.translation.glossary`` の CRUD/解決系関数を、
API ルータ(HTTP)を経由せず直接呼ぶ(DI 層を挟まない分、意図が読みやすい単体テストになる)。
DB は実 PostgreSQL(SQLite 代替禁止)。テストデータはユニーク UUID/ランダム英字語で他テスト
と衝突しないようにする。
"""

from __future__ import annotations

import random
import string
import uuid

from alinea_core.db.models import (
    DocumentRevision,
    Glossary,
    LibraryItem,
    Paper,
    TranslationSet,
    User,
)
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.search.rebuild import rebuild_block_search_index
from alinea_core.translation.glossary import (
    DuplicateTermError,
    create_term,
    delete_term,
    find_affected_blocks,
    format_glossary_lines,
    get_or_create_glossary,
    get_term,
    list_terms,
    promote_term,
    resolve_or_create_personal_set,
    target_contexts_for_glossary,
    update_term,
)
from sqlalchemy.ext.asyncio import AsyncSession


def _id() -> str:
    return str(uuid.uuid4())


def _word() -> str:
    """PGroonga のトークナイズに適した純英字のランダム語(他テストとの衝突を避ける)。"""
    return "".join(random.choices(string.ascii_lowercase, k=10))


# ---------------------------------------------------------------------------
# format_glossary_lines(純粋関数)
# ---------------------------------------------------------------------------
def test_format_glossary_lines_empty_snapshot() -> None:
    assert format_glossary_lines([]) == "(用語表なし)"


def test_format_glossary_lines_covers_all_policies() -> None:
    snapshot = [
        {"source_term": "flow", "target_term": "フロー", "policy": "translate", "origin": "global"},
        {"source_term": "GPU", "target_term": "GPU", "policy": "keep_original", "origin": "global"},
        {
            "source_term": "distillation",
            "target_term": "蒸留",
            "policy": "both",
            "origin": "user",
        },
    ]
    text = format_glossary_lines(snapshot)
    lines = text.splitlines()
    assert len(lines) == 3
    assert "flow → フロー [policy=translate]" in lines[0]
    assert "GPU → 原語のまま [policy=keep_original]" in lines[1]
    assert "distillation → 蒸留 [初出時のみ「蒸留(distillation)」と併記] [policy=both]" in lines[2]


# ---------------------------------------------------------------------------
# 3 層 CRUD(get_or_create_glossary・create_term・get_term・list_terms・update_term・
# delete_term・promote_term)
# ---------------------------------------------------------------------------
async def test_get_or_create_glossary_is_idempotent_for_user_and_paper_scopes(
    db_session: AsyncSession,
) -> None:
    user = User(id=_id(), email=f"{_id()}@t.test")
    db_session.add(user)
    await db_session.flush()
    paper = Paper(id=_id(), title="P", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    li = LibraryItem(id=_id(), user_id=user.id, paper_id=paper.id)
    db_session.add(li)
    await db_session.commit()

    first = await get_or_create_glossary(db_session, scope="user", user_id=user.id)
    second = await get_or_create_glossary(db_session, scope="user", user_id=user.id)
    assert first.id == second.id
    assert first.scope == "user"

    paper_first = await get_or_create_glossary(db_session, scope="paper", library_item_id=li.id)
    paper_second = await get_or_create_glossary(db_session, scope="paper", library_item_id=li.id)
    assert paper_first.id == paper_second.id
    assert paper_first.scope == "paper"
    assert paper_first.id != first.id


async def test_create_term_duplicate_is_case_insensitive(db_session: AsyncSession) -> None:
    user = User(id=_id(), email=f"{_id()}@t.test")
    db_session.add(user)
    await db_session.commit()

    term_text = _word()
    created = await create_term(
        db_session,
        scope="user",
        source_term=term_text,
        target_term="訳語",
        policy="translate",
        user_id=user.id,
    )
    assert created.source_term == term_text

    try:
        await create_term(
            db_session,
            scope="user",
            source_term=term_text.upper(),
            target_term="別訳",
            policy="translate",
            user_id=user.id,
        )
        raise AssertionError("同一語(大文字小文字無視)の重複作成は失敗するはず")
    except DuplicateTermError:
        pass


async def test_get_term_returns_none_for_unknown_id(db_session: AsyncSession) -> None:
    assert await get_term(db_session, _id()) is None


async def test_list_terms_orders_case_insensitively_and_scopes_by_user_and_paper(
    db_session: AsyncSession,
) -> None:
    user = User(id=_id(), email=f"{_id()}@t.test")
    db_session.add(user)
    await db_session.flush()
    paper = Paper(id=_id(), title="P", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    li = LibraryItem(id=_id(), user_id=user.id, paper_id=paper.id)
    db_session.add(li)
    await db_session.commit()

    sfx = uuid.uuid4().hex[:8]
    upper_first = f"Zebra{sfx}"
    lower_first = f"apple{sfx}"
    paper_term = f"Mango{sfx}"

    await create_term(
        db_session,
        scope="user",
        source_term=upper_first,
        target_term="x",
        policy="translate",
        user_id=user.id,
    )
    await create_term(
        db_session,
        scope="user",
        source_term=lower_first,
        target_term="y",
        policy="translate",
        user_id=user.id,
    )
    await create_term(
        db_session,
        scope="paper",
        source_term=paper_term,
        target_term="z",
        policy="translate",
        library_item_id=li.id,
    )

    rows = await list_terms(db_session, user_id=user.id, library_item_id=li.id)
    ours = [
        (t.source_term, g.scope)
        for t, g in rows
        if t.source_term in {upper_first, lower_first, paper_term}
    ]
    assert ours == sorted(ours, key=lambda pair: pair[0].lower())
    assert (paper_term, "paper") in ours
    assert (upper_first, "user") in ours


async def test_update_term_applies_partial_change_and_clears_auto_extracted(
    db_session: AsyncSession,
) -> None:
    user = User(id=_id(), email=f"{_id()}@t.test")
    db_session.add(user)
    await db_session.commit()
    term = await create_term(
        db_session,
        scope="user",
        source_term=_word(),
        target_term="旧訳",
        policy="translate",
        user_id=user.id,
        auto_extracted=True,
    )
    assert term.auto_extracted is True

    updated = await update_term(db_session, term, target_term="新訳")
    assert updated.target_term == "新訳"
    assert updated.policy == "translate"  # policy 未指定なら変えない
    assert updated.auto_extracted is False  # 確定操作で自動抽出フラグは落ちる

    updated2 = await update_term(db_session, updated, policy="both")
    assert updated2.target_term == "新訳"  # target_term 未指定なら変えない
    assert updated2.policy == "both"


async def test_delete_term_removes_row(db_session: AsyncSession) -> None:
    user = User(id=_id(), email=f"{_id()}@t.test")
    db_session.add(user)
    await db_session.commit()
    term = await create_term(
        db_session,
        scope="user",
        source_term=_word(),
        target_term="訳",
        policy="translate",
        user_id=user.id,
    )
    term_id = str(term.id)
    await delete_term(db_session, term)
    assert await get_term(db_session, term_id) is None


async def test_promote_term_creates_then_overwrites_user_term(db_session: AsyncSession) -> None:
    user = User(id=_id(), email=f"{_id()}@t.test")
    db_session.add(user)
    await db_session.flush()
    paper = Paper(id=_id(), title="P", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    li = LibraryItem(id=_id(), user_id=user.id, paper_id=paper.id)
    db_session.add(li)
    await db_session.commit()

    source_term = _word()
    paper_term = await create_term(
        db_session,
        scope="paper",
        source_term=source_term,
        target_term="論文訳",
        policy="both",
        library_item_id=li.id,
        auto_extracted=True,
    )

    promoted = await promote_term(db_session, paper_term, user_id=user.id)
    assert promoted.target_term == "論文訳"
    assert promoted.auto_extracted is False
    # 元の paper term は消えず、そのまま残る。
    assert await get_term(db_session, str(paper_term.id)) is not None

    # 再度異なる訳語で promote すると、ユーザー用語集内の既存語を上書きする(新規作成しない)。
    paper_term.target_term = "改訳"
    paper_term.policy = "translate"
    await db_session.flush()
    promoted_again = await promote_term(db_session, paper_term, user_id=user.id)
    assert promoted_again.id == promoted.id
    assert promoted_again.target_term == "改訳"
    assert promoted_again.policy == "translate"


# ---------------------------------------------------------------------------
# find_affected_blocks(語境界厳密化。PGroonga 部分一致の過剰ヒットを除去)
# ---------------------------------------------------------------------------
def _p(block_id: str, text: str) -> Block:
    return Block(id=block_id, type="paragraph", inlines=[Inline(t="text", v=text)])


async def test_find_affected_blocks_excludes_substring_matches(db_session: AsyncSession) -> None:
    paper = Paper(id=_id(), title="P", visibility="public")
    db_session.add(paper)
    await db_session.flush()

    term = _word()
    content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Intro"),
                blocks=[
                    _p("blk-exact", f"We study {term} in this work."),
                    _p("blk-substr", f"This is about non{term}ish behavior only."),
                    _p("blk-none", "This paragraph is unrelated entirely."),
                ],
            )
        ],
    )
    revision = DocumentRevision(
        id=_id(),
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content=content.model_dump(),
    )
    db_session.add(revision)
    await db_session.flush()
    await rebuild_block_search_index(db_session, str(revision.id), content)
    await db_session.commit()

    hits = await find_affected_blocks(db_session, revision_id=str(revision.id), source_term=term)
    assert hits == ["blk-exact"]


# ---------------------------------------------------------------------------
# target_contexts_for_glossary(適用対象 revision/user の解決)
# ---------------------------------------------------------------------------
async def test_target_contexts_for_glossary_paper_scope(db_session: AsyncSession) -> None:
    user = User(id=_id(), email=f"{_id()}@t.test")
    db_session.add(user)
    await db_session.flush()
    paper = Paper(id=_id(), title="P", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    revision = DocumentRevision(
        id=_id(),
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content={"quality_level": "A", "sections": []},
    )
    db_session.add(revision)
    await db_session.flush()
    paper.latest_revision_id = revision.id
    li = LibraryItem(id=_id(), user_id=user.id, paper_id=paper.id)
    db_session.add(li)
    await db_session.commit()

    glossary = Glossary(id=_id(), scope="paper", library_item_id=li.id)
    db_session.add(glossary)
    await db_session.commit()

    contexts = await target_contexts_for_glossary(db_session, glossary)
    assert contexts == [
        {
            "revision_id": str(revision.id),
            "user_id": str(user.id),
            "library_item_id": str(li.id),
            "paper_id": str(paper.id),
        }
    ]


async def test_target_contexts_for_glossary_paper_scope_missing_cases_return_empty(
    db_session: AsyncSession,
) -> None:
    # DB 制約(ck_glossaries_scope_refs)により scope=paper は library_item_id 必須なため、
    # 早期 return 分岐(未永続化オブジェクト)はメモリ上に構築するだけで検証する。
    empty_glossary = Glossary(id=_id(), scope="paper", library_item_id=None)
    assert await target_contexts_for_glossary(db_session, empty_glossary) == []

    dangling_glossary = Glossary(id=_id(), scope="paper", library_item_id=_id())
    assert await target_contexts_for_glossary(db_session, dangling_glossary) == []

    unsupported_glossary = Glossary(id=_id(), scope="global")
    db_session.add(unsupported_glossary)
    await db_session.commit()
    assert await target_contexts_for_glossary(db_session, unsupported_glossary) == []

    # paper はあるがまだ latest_revision_id が確定していない(取り込み未完了)。
    user = User(id=_id(), email=f"{_id()}@t.test")
    no_revision_paper = Paper(id=_id(), title="P", visibility="public")
    db_session.add_all([user, no_revision_paper])
    await db_session.flush()
    li_without_revision = LibraryItem(id=_id(), user_id=user.id, paper_id=no_revision_paper.id)
    db_session.add(li_without_revision)
    await db_session.commit()
    no_revision_glossary = Glossary(id=_id(), scope="paper", library_item_id=li_without_revision.id)
    db_session.add(no_revision_glossary)
    await db_session.commit()
    assert await target_contexts_for_glossary(db_session, no_revision_glossary) == []


async def test_target_contexts_for_glossary_paper_scope_rejects_foreign_latest_revision(
    db_session: AsyncSession,
) -> None:
    user = User(id=_id(), email=f"{_id()}@t.test")
    paper = Paper(id=_id(), title="Owned paper", visibility="public")
    foreign_paper = Paper(id=_id(), title="Foreign paper", visibility="public")
    db_session.add_all([user, paper, foreign_paper])
    await db_session.flush()
    foreign_revision = DocumentRevision(
        id=_id(),
        paper_id=str(foreign_paper.id),
        parser_version="foreign-test",
        quality_level="A",
        source_format="latex",
        content={"quality_level": "A", "sections": []},
    )
    db_session.add(foreign_revision)
    await db_session.flush()
    paper.latest_revision_id = foreign_revision.id
    item = LibraryItem(id=_id(), user_id=str(user.id), paper_id=str(paper.id))
    db_session.add(item)
    await db_session.flush()
    glossary = Glossary(id=_id(), scope="paper", library_item_id=str(item.id))
    db_session.add(glossary)
    await db_session.commit()

    assert await target_contexts_for_glossary(db_session, glossary) == []


async def test_target_contexts_for_glossary_user_scope_requires_natural_shared_or_personal_set(
    db_session: AsyncSession,
) -> None:
    user = User(id=_id(), email=f"{_id()}@t.test")
    db_session.add(user)
    await db_session.flush()

    with_set = Paper(id=_id(), title="P1", visibility="public")
    without_set = Paper(id=_id(), title="P2", visibility="public")
    db_session.add_all([with_set, without_set])
    await db_session.flush()

    rev_with_set = DocumentRevision(
        id=_id(),
        paper_id=with_set.id,
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content={"quality_level": "A", "sections": []},
    )
    rev_without_set = DocumentRevision(
        id=_id(),
        paper_id=without_set.id,
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content={"quality_level": "A", "sections": []},
    )
    db_session.add_all([rev_with_set, rev_without_set])
    await db_session.flush()
    with_set.latest_revision_id = rev_with_set.id
    without_set.latest_revision_id = rev_without_set.id

    li_with_set = LibraryItem(id=_id(), user_id=user.id, paper_id=with_set.id)
    li_without_set = LibraryItem(id=_id(), user_id=user.id, paper_id=without_set.id)
    db_session.add_all([li_with_set, li_without_set])
    await db_session.flush()

    shared_set = TranslationSet(
        id=_id(), revision_id=rev_with_set.id, style="natural", scope="shared"
    )
    db_session.add(shared_set)
    await db_session.commit()

    glossary = Glossary(id=_id(), scope="user", user_id=user.id)
    db_session.add(glossary)
    await db_session.commit()

    contexts = await target_contexts_for_glossary(db_session, glossary)
    revision_ids = {c["revision_id"] for c in contexts}
    assert str(rev_with_set.id) in revision_ids
    assert str(rev_without_set.id) not in revision_ids


async def test_target_contexts_for_glossary_user_scope_rejects_foreign_latest_revision(
    db_session: AsyncSession,
) -> None:
    user = User(id=_id(), email=f"{_id()}@t.test")
    paper = Paper(id=_id(), title="Owned paper", visibility="public")
    foreign_paper = Paper(id=_id(), title="Foreign paper", visibility="public")
    db_session.add_all([user, paper, foreign_paper])
    await db_session.flush()
    foreign_revision = DocumentRevision(
        id=_id(),
        paper_id=str(foreign_paper.id),
        parser_version="foreign-test",
        quality_level="A",
        source_format="latex",
        content={"quality_level": "A", "sections": []},
    )
    db_session.add(foreign_revision)
    await db_session.flush()
    paper.latest_revision_id = foreign_revision.id
    item = LibraryItem(id=_id(), user_id=str(user.id), paper_id=str(paper.id))
    db_session.add_all(
        [
            item,
            TranslationSet(
                id=_id(),
                revision_id=str(foreign_revision.id),
                style="natural",
                scope="shared",
            ),
        ]
    )
    glossary = Glossary(id=_id(), scope="user", user_id=str(user.id))
    db_session.add(glossary)
    await db_session.commit()

    assert await target_contexts_for_glossary(db_session, glossary) == []


# ---------------------------------------------------------------------------
# resolve_or_create_personal_set(§9.2 差分保存フォーク)
# ---------------------------------------------------------------------------
async def test_resolve_or_create_personal_set_forks_from_shared_then_reuses(
    db_session: AsyncSession,
) -> None:
    user = User(id=_id(), email=f"{_id()}@t.test")
    db_session.add(user)
    await db_session.flush()
    paper = Paper(id=_id(), title="P", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    revision = DocumentRevision(
        id=_id(),
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content={"quality_level": "A", "sections": []},
    )
    db_session.add(revision)
    await db_session.flush()
    shared = TranslationSet(
        id=_id(),
        revision_id=revision.id,
        style="natural",
        scope="shared",
        glossary_snapshot=[{"source_term": "flow", "target_term": "フロー"}],
        plan={
            "version": 1,
            "include_appendix": False,
            "translate_table_cells": True,
            "suggest_section_selection_over_30_pages": False,
            "target_section_ids": [],
            "target_block_ids": [],
            "pages": None,
        },
        status="complete",
    )
    db_session.add(shared)
    await db_session.commit()

    forked = await resolve_or_create_personal_set(
        db_session, revision_id=str(revision.id), style="natural", user_id=str(user.id)
    )
    assert forked.scope == "personal"
    assert forked.base_set_id == str(shared.id)
    assert forked.glossary_snapshot == [{"source_term": "flow", "target_term": "フロー"}]
    assert forked.plan == shared.plan
    assert forked.status == "complete"

    again = await resolve_or_create_personal_set(
        db_session, revision_id=str(revision.id), style="natural", user_id=str(user.id)
    )
    assert again.id == forked.id


async def test_resolve_or_create_personal_set_without_shared_creates_pending_empty_set(
    db_session: AsyncSession,
) -> None:
    user = User(id=_id(), email=f"{_id()}@t.test")
    db_session.add(user)
    await db_session.flush()
    paper = Paper(id=_id(), title="P", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    revision = DocumentRevision(
        id=_id(),
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content={"quality_level": "A", "sections": []},
    )
    db_session.add(revision)
    await db_session.commit()

    forked = await resolve_or_create_personal_set(
        db_session, revision_id=str(revision.id), style="natural", user_id=str(user.id)
    )
    assert forked.scope == "personal"
    assert forked.base_set_id is None
    assert forked.glossary_snapshot == []
    assert forked.status == "pending"
