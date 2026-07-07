"""export API テスト(M1-16 / plans/03 §18・docs/00 P5・docs/06 §10・§11)。

- PY-EXP-01: 論文単位 Markdown が書誌+メモ+注釈+チャット+リソース一覧
  (URL・メモ・§ チップのテキスト化)を含み、Obsidian 互換 front-matter を持つ。
- PY-EXP-02: BibTeX の必須フィールド(author/title/year/eprint)を含み、再パースできる。
  決定(deviations): 本タスクは ``uv add`` 禁止・依存追加不可のため ``bibtexparser`` を
  導入できない。同等の検証を行う最小限の正規表現パーサをテスト内に実装し、
  「主要リファレンスマネージャが読める」構造上の要件(entry type + key + 必須フィールド)を
  機械的に確認する。
- PY-ANN-03: 注釈一覧フィルタ(color/has_comment/placed=false)と Markdown エクスポート
  (export/annotations)の内容一致。

DB は実 PostgreSQL・Redis も実インスタンス。他タスクの WIP ルータを巻き込まないよう、
本タスク所有の ``export.router`` と(export が直接呼ぶ)``annotations.router`` のみを
マウントした専用アプリで検証する(test_dashboard.py と同方針)。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.schemas.common import PaperBib
from yakudoku_api.schemas.export import render_bibtex, render_bibtex_entry, unique_cite_key
from yakudoku_api.services.session_service import create_session
from yakudoku_api.services.user_service import purge_user, upsert_user_by_email
from yakudoku_core.db.models import User


def _build_app() -> FastAPI:
    """本タスク所有ルータ(export)+依存先(annotations)をマウントしたアプリ。"""
    from yakudoku_api.errors import register_exception_handlers
    from yakudoku_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from yakudoku_api.ratelimit import RateLimitMiddleware
    from yakudoku_api.redis_client import get_redis
    from yakudoku_api.routers import annotations, export
    from yakudoku_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(export.router)
    app.include_router(annotations.router)
    return app


@pytest_asyncio.fixture
async def auth(
    db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[tuple[AsyncClient, str]]:
    email = f"exp-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)
    token = await create_session(redis_client, user.id)
    transport = ASGITransport(app=_build_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        ac.cookies.set("yk_session", token)
        try:
            yield ac, uid
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


# ---------------------------------------------------------------------------
# PY-EXP-01: 論文単位 Markdown
# ---------------------------------------------------------------------------
async def test_export_paper_markdown_includes_all_sections(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None

    paper = await factories.make_paper(
        db_session,
        owner=user,
        visibility="private",
        arxiv_id="2209.03003",
        title="Flow Straight and Fast",
    )
    rev = await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(
        db_session, user=user, paper=paper, status="reading", tags=["flow"]
    )
    await factories.make_note(db_session, library_item=item, title="要点", body_md="整流フロー概要")
    await factories.make_annotation(
        db_session,
        library_item=item,
        revision=rev,
        kind="comment",
        color="important",
        body="ここが核心",
    )
    thread = await factories.make_chat_thread(db_session, library_item=item)
    await factories.make_chat_message(
        db_session, thread=thread, role="user", text_plain="EMA teacher とは?"
    )
    await factories.make_chat_message(
        db_session, thread=thread, role="assistant", text_plain="教師モデルの指数移動平均です。"
    )
    resource = await factories.make_resource_link(
        db_session,
        library_item=item,
        kind="github",
        url="https://github.com/gnobitab/RectifiedFlow",
    )
    resource.title = "公式実装"
    resource.note_md = "著者公式の実装"
    await db_session.commit()

    resp = await client.get(f"/api/library-items/{item.id}/export/markdown")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/markdown")
    assert 'filename="2209.03003.md"' in resp.headers["content-disposition"]

    body = resp.text
    # Obsidian 互換 front-matter。
    assert body.startswith("---\n")
    front, _, rest = body[4:].partition("\n---\n")
    assert "title: Flow Straight and Fast" in front
    assert "arxiv_id: '2209.03003'" in front
    assert "status: reading" in front
    # 書誌。
    assert "# Flow Straight and Fast" in rest
    assert "**arXiv**: 2209.03003" in rest
    # メモ。
    assert "## メモ" in rest
    assert "### 要点" in rest
    assert "整流フロー概要" in rest
    # 注釈(§ チップのテキスト化 + コメント)。
    assert "## 注釈" in rest
    assert "ここが核心" in rest
    assert "§1" in rest or "Introduction" in rest  # 既定ドキュメントの先頭ブロックの節見出し。
    # チャット履歴。
    assert "## チャット履歴" in rest
    assert "EMA teacher とは?" in rest
    assert "教師モデルの指数移動平均です。" in rest
    # リソース一覧(種類・タイトル・URL・メモ)。
    assert "## リソース" in rest
    assert "https://github.com/gnobitab/RectifiedFlow" in rest
    assert "公式実装" in rest
    assert "著者公式の実装" in rest


async def test_export_paper_markdown_non_arxiv_uses_title_slug_filename(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(
        db_session,
        owner=user,
        visibility="private",
        arxiv_id=None,
        title="A Very Special Paper: Straight & Fast!",
    )
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    await db_session.commit()

    resp = await client.get(f"/api/library-items/{item.id}/export/markdown")
    assert resp.status_code == 200, resp.text
    disposition = resp.headers["content-disposition"]
    assert 'filename="a-very-special-paper-straight-fast.md"' in disposition


async def test_export_markdown_other_users_item_404(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, _uid = auth
    other_user = await factories.make_user(db_session)
    other_item = await factories.make_library_item(db_session, user=other_user)
    await db_session.commit()
    try:
        resp = await client.get(f"/api/library-items/{other_item.id}/export/markdown")
        assert resp.status_code == 404, resp.text
    finally:
        await purge_user(db_session, str(other_user.id))
        await db_session.commit()


# ---------------------------------------------------------------------------
# PY-ANN-03: 注釈一覧フィルタと export/annotations の内容一致
# ---------------------------------------------------------------------------
async def test_export_annotations_matches_filtered_list_content(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    rev = await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)

    important = await factories.make_annotation(
        db_session,
        library_item=item,
        revision=rev,
        kind="comment",
        color="important",
        body="重要コメント",
        anchor=factories.anchor_for(rev, 0),
    )
    question = await factories.make_annotation(
        db_session,
        library_item=item,
        revision=rev,
        kind="highlight",
        color="question",
        anchor=factories.anchor_for(rev, 1),
    )
    unplaced = await factories.make_annotation(
        db_session,
        library_item=item,
        revision=rev,
        kind="highlight",
        color="idea",
        anchor=factories.anchor_for(rev, 3),  # blk-p3(sec-2)。idx2 は equation(引用文なし)。
    )
    unplaced.orphaned = True
    await db_session.commit()

    # フィルタ: color=important(一覧パネルのチップ相当)。
    filtered_important = await client.get(
        f"/api/library-items/{item.id}/annotations", params={"color": "important"}
    )
    assert filtered_important.status_code == 200, filtered_important.text
    important_items = filtered_important.json()["items"]
    assert len(important_items) == 1
    assert important_items[0]["comment"] == "重要コメント"

    # フィルタ: has_comment=false(コメント無しのみ)。
    filtered_no_comment = await client.get(
        f"/api/library-items/{item.id}/annotations", params={"has_comment": "false"}
    )
    no_comment_ids = {i["id"] for i in filtered_no_comment.json()["items"]}
    assert str(question.id) in no_comment_ids
    assert str(important.id) not in no_comment_ids

    # フィルタ: placed=false(未配置のみ)。
    filtered_unplaced = await client.get(
        f"/api/library-items/{item.id}/annotations", params={"placed": "false"}
    )
    unplaced_items = filtered_unplaced.json()["items"]
    assert len(unplaced_items) == 1
    assert unplaced_items[0]["id"] == str(unplaced.id)
    assert unplaced_items[0]["placed"] is False

    # Markdown エクスポート(フィルタなし=全件)。内容が一覧パネルの表示値と一致すること。
    resp = await client.get(f"/api/library-items/{item.id}/export/annotations")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/markdown")
    md = resp.text

    assert "重要コメント" in md
    assert "[important]" in md
    assert "[question]" in md
    assert "[idea]" in md
    assert "*(未配置)*" in md  # 未配置(placed=false)の注釈は明示される。
    # § チップのテキスト化(quote 実データは anchor_for の引用文)。
    quote_important = important_items[0]["anchor"]["quote"]
    assert quote_important in md
    display_important = important_items[0]["anchor"]["display"]
    assert display_important in md


async def test_export_annotations_empty_shows_placeholder(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    item = await factories.make_library_item(db_session, user=user)
    await db_session.commit()

    resp = await client.get(f"/api/library-items/{item.id}/export/annotations")
    assert resp.status_code == 200, resp.text
    assert "注釈はありません" in resp.text


# ---------------------------------------------------------------------------
# PY-EXP-02: BibTeX(unit)
# ---------------------------------------------------------------------------
_FIELD_RE = re.compile(r"(\w+)\s*=\s*\{([^{}]*)\}")
_ENTRY_HEAD_RE = re.compile(r"^@(\w+)\{([^,\s]+),", re.MULTILINE)


def _parse_bibtex_entries(text: str) -> list[tuple[str, str, dict[str, str]]]:
    """最小限の BibTeX パーサ(entry_type, cite_key, fields)。

    ``bibtexparser`` は本タスクでは追加不可(uv add 禁止)のため、同ライブラリが要求する
    基本構造(``@type{key, field = {value}, ...}``)を機械的に検証する代替実装
    (deviations 参照)。
    """
    entries: list[tuple[str, str, dict[str, str]]] = []
    for block in text.strip().split("\n\n"):
        head = _ENTRY_HEAD_RE.match(block)
        assert head is not None, f"BibTeX entry の先頭形式が不正: {block!r}"
        fields = dict(_FIELD_RE.findall(block))
        entries.append((head.group(1), head.group(2), fields))
    return entries


def _paper_bib(**overrides: Any) -> PaperBib:
    defaults: dict[str, Any] = dict(
        id="p1",
        title="Flow Straight and Fast",
        authors=["Xingchang Liu", "Qiang Liu"],
        authors_short="Liu, Liu",
        venue=None,
        year=2022,
        arxiv_id="2209.03003",
        arxiv_version="v1",
        doi=None,
        license="cc-by-4.0",
        visibility="public",
        abstract="",
    )
    defaults.update(overrides)
    return PaperBib(**defaults)


def test_bibtex_entry_has_required_fields_and_is_reparseable() -> None:
    paper = _paper_bib()
    text = render_bibtex([paper])
    entries = _parse_bibtex_entries(text)
    assert len(entries) == 1
    entry_type, cite_key, fields = entries[0]
    assert entry_type == "misc"  # arXiv 論文。
    assert cite_key == "liu2022"
    # docs/06 §11「主要リファレンスマネージャで読み込める」の機械検証: 必須フィールド。
    assert fields["author"] == "Xingchang Liu and Qiang Liu"
    assert fields["title"] == "Flow Straight and Fast"
    assert fields["year"] == "2022"
    assert fields["eprint"] == "2209.03003"
    assert fields["archivePrefix"] == "arXiv"


def test_bibtex_non_arxiv_paper_is_article_without_eprint() -> None:
    paper = _paper_bib(arxiv_id=None, venue="NeurIPS", doi="10.1000/xyz")
    text = render_bibtex([paper])
    entries = _parse_bibtex_entries(text)
    entry_type, _cite_key, fields = entries[0]
    assert entry_type == "article"
    assert "eprint" not in fields
    assert fields["journal"] == "NeurIPS"
    assert fields["doi"] == "10.1000/xyz"


def test_bibtex_dedupes_cite_keys_for_multiple_papers() -> None:
    p1 = _paper_bib(id="p1")
    p2 = _paper_bib(id="p2", arxiv_id="2209.03004")  # 同姓・同年 → キー衝突。
    text = render_bibtex([p1, p2])
    entries = _parse_bibtex_entries(text)
    keys = [key for _type, key, _fields in entries]
    assert keys == ["liu2022", "liu2022a"]


def test_unique_cite_key_increments_suffix() -> None:
    used: set[str] = set()
    paper = _paper_bib()
    assert unique_cite_key(paper, used) == "liu2022"
    assert unique_cite_key(paper, used) == "liu2022a"
    assert unique_cite_key(paper, used) == "liu2022b"


def test_render_bibtex_entry_escapes_braces_in_title() -> None:
    paper = _paper_bib(title="A {Weird} Title")
    entry = render_bibtex_entry(paper, cite_key="k1")
    assert "A \\{Weird\\} Title" in entry


def test_render_bibtex_empty_list_is_empty_string() -> None:
    assert render_bibtex([]) == ""
