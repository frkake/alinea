"""export API テスト(M1-16/M2-15 / plans/03 §18・docs/00 P5・docs/06 §10・§11)。

- PY-EXP-01: 論文単位 Markdown が書誌+メモ+注釈+チャット+リソース一覧
  (URL・メモ・§ チップのテキスト化)を含み、Obsidian 互換 front-matter を持つ。
- PY-EXP-02: BibTeX の必須フィールド(author/title/year/eprint)を含み、再パースできる。
  決定(deviations): 本タスクは ``uv add`` 禁止・依存追加不可のため ``bibtexparser`` を
  導入できない。同等の検証を行う最小限の正規表現パーサをテスト内に実装し、
  「主要リファレンスマネージャが読める」構造上の要件(entry type + key + 必須フィールド)を
  機械的に確認する。
- PY-EXP-03(M2-15): CSV は UTF-8 BOM 付き・16 列ヘッダ固定(plans/03 §18 逐語)。
- PY-EXP-04(M2-15): 全量 JSON(``POST /api/export/full`` → ``jobs.kind='export'``。
  ``GET /api/export/full/{job_id}`` は ``download_url`` を ``jobs.result`` から返す)。
  実際のジョブ実行(zip 化・S3 アップロード)は :mod:`alinea_worker.tasks.export_user_data`
  の責務(apps/worker/tests/test_export_bulk.py で検証)。本ファイルは API 層(ジョブ作成・
  状態取得)のみを検証し、完了状態は DB を直接更新して模す。
- PY-ANN-03: 注釈一覧フィルタ(color/has_comment/placed=false)と Markdown エクスポート
  (export/annotations)の内容一致。

DB は実 PostgreSQL・Redis も実インスタンス。他タスクの WIP ルータを巻き込まないよう、
本タスク所有の ``export.router`` と(export が直接呼ぶ)``annotations.router`` のみを
マウントした専用アプリで検証する(test_dashboard.py と同方針)。
"""

from __future__ import annotations

import csv
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from alinea_api.routers.export import _ExportCsvRow, render_csv
from alinea_api.schemas.common import PaperBib
from alinea_api.schemas.export import render_bibtex, render_bibtex_entry, unique_cite_key
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.db.models import Job, User
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _build_app() -> FastAPI:
    """本タスク所有ルータ(export)+依存先(annotations)をマウントしたアプリ。"""
    from alinea_api.errors import register_exception_handlers
    from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from alinea_api.ratelimit import RateLimitMiddleware
    from alinea_api.redis_client import get_redis
    from alinea_api.routers import annotations, export
    from alinea_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(export.router)
    app.include_router(annotations.router)
    return app


async def _noop_export_wakeup(_job_id: str) -> None:
    """``get_export_job_wakeup`` の差し替え(実 arq 接続をせず即時応答。PY-EXP-04)。"""


@pytest_asyncio.fixture
async def auth(
    db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[tuple[AsyncClient, str]]:
    from alinea_api.routers.export import get_export_job_wakeup

    email = f"exp-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)
    token = await create_session(redis_client, user.id)
    app = _build_app()
    app.dependency_overrides[get_export_job_wakeup] = lambda: _noop_export_wakeup
    transport = ASGITransport(app=app)
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

    # papers.arxiv_id はユーザーを問わずグローバルに一意(uq_papers_arxiv_id)。他テスト
    # (test_chat.py 等)や並走する他エージェントのシード投入と衝突しないよう、固定の
    # 実在 arXiv ID ではなくテストごとにユニークな ID を使う(deviations 参照)。
    arxiv_id = f"9909.{uuid.uuid4().int % 100000:05d}"
    paper = await factories.make_paper(
        db_session,
        owner=user,
        visibility="private",
        arxiv_id=arxiv_id,
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
    assert f'filename="{arxiv_id}.md"' in resp.headers["content-disposition"]

    body = resp.text
    # Obsidian 互換 front-matter。
    assert body.startswith("---\n")
    front, _, rest = body[4:].partition("\n---\n")
    assert "title: Flow Straight and Fast" in front
    assert f"arxiv_id: '{arxiv_id}'" in front
    assert "status: reading" in front
    # 書誌。
    assert "# Flow Straight and Fast" in rest
    assert f"**arXiv**: {arxiv_id}" in rest
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


# ---------------------------------------------------------------------------
# PY-EXP-03: CSV(unit)— UTF-8 BOM・16 列ヘッダ固定(plans/03 §18 逐語)
# ---------------------------------------------------------------------------
_CSV_EXPECTED_HEADER = (
    "title,authors,year,venue,arxiv_id,doi,status,priority,deadline,tags,quality,"
    "added_at,finished_at,reading_hours,comprehension,importance"
)


def _csv_row(**overrides: Any) -> _ExportCsvRow:
    defaults: dict[str, Any] = dict(
        paper=_paper_bib(),
        status="reading",
        priority="high",
        deadline="2026-07-16",
        tags=["flow", "ml"],
        quality="A",
        added_at="2026-01-01T00:00:00+00:00",
        finished_at="",
        reading_hours=1.5,
        comprehension=4,
        importance="high",
    )
    defaults.update(overrides)
    return _ExportCsvRow(**defaults)


def test_render_csv_has_utf8_bom_and_16_column_header() -> None:
    content = render_csv([_csv_row()])
    assert content.startswith("﻿")
    header_line = content[1:].split("\r\n", 1)[0]
    assert header_line == _CSV_EXPECTED_HEADER
    assert len(header_line.split(",")) == 16


def test_render_csv_row_values_and_list_joins() -> None:
    content = render_csv([_csv_row()])
    lines = content[1:].split("\r\n")
    assert lines[1].startswith("Flow Straight and Fast,")
    assert "Xingchang Liu; Qiang Liu" in lines[1]
    assert '"flow, ml"' in lines[1]  # tags はカンマ区切り(CSV クォート内)。
    assert ",1.50," in lines[1]  # reading_hours は小数 2 桁。


def test_render_csv_empty_list_is_header_only() -> None:
    content = render_csv([])
    lines = content[1:].strip("\r\n").split("\r\n")
    assert lines == [_CSV_EXPECTED_HEADER]


# ---------------------------------------------------------------------------
# PY-EXP-03: CSV(integration)
# ---------------------------------------------------------------------------
async def test_export_csv_endpoint_returns_all_items_with_bom(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(
        db_session, owner=user, visibility="private", title="Flow Straight and Fast"
    )
    rev = await factories.make_revision(db_session, paper=paper, quality_level="B")
    item = await factories.make_library_item(
        db_session,
        user=user,
        paper=paper,
        status="reading",
        tags=["flow"],
        priority="high",
    )
    item.understanding = 4
    item.importance = "high"
    item.total_active_seconds = 5400  # 1.5h
    await db_session.commit()

    resp = await client.get("/api/export/csv")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'filename="library.csv"' in resp.headers["content-disposition"]

    raw = resp.content
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    text_body = raw.decode("utf-8-sig")
    lines = text_body.strip("\r\n").split("\r\n")
    assert lines[0] == _CSV_EXPECTED_HEADER
    assert len(lines) == 2
    row = lines[1]
    assert row.startswith("Flow Straight and Fast,")
    assert ",reading,high," in row
    assert ",flow," in row
    assert str(rev.quality_level) in row
    assert ",1.50,4,high" in row


async def test_export_csv_ignores_foreign_and_invalid_reading_revisions(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(
        db_session, owner=user, visibility="private", title="Owned CSV paper"
    )
    await factories.make_revision(db_session, paper=paper, quality_level="B")
    item = await factories.make_library_item(db_session, user=user, paper=paper, status="reading")
    foreign_paper = await factories.make_paper(
        db_session, owner=user, visibility="private", title="Foreign CSV paper"
    )
    foreign_revision = await factories.make_revision(
        db_session, paper=foreign_paper, quality_level="A"
    )
    item.reading_position = {
        "revision_id": str(foreign_revision.id),
        "block_id": "blk-p1",
    }
    await db_session.commit()

    foreign_response = await client.get("/api/export/csv")
    assert foreign_response.status_code == 200, foreign_response.text
    foreign_rows = list(csv.reader(foreign_response.content.decode("utf-8-sig").splitlines()))
    assert foreign_rows[1][10] == "B"

    item.reading_position = {"revision_id": "not-a-uuid", "block_id": "blk-p1"}
    await db_session.commit()
    invalid_response = await client.get("/api/export/csv")
    assert invalid_response.status_code == 200, invalid_response.text
    invalid_rows = list(csv.reader(invalid_response.content.decode("utf-8-sig").splitlines()))
    assert invalid_rows[1][10] == "B"


async def test_export_csv_other_users_items_not_included(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, _uid = auth
    other_user = await factories.make_user(db_session)
    other_item = await factories.make_library_item(db_session, user=other_user)
    await db_session.commit()
    try:
        resp = await client.get("/api/export/csv")
        assert resp.status_code == 200, resp.text
        text_body = resp.content.decode("utf-8-sig")
        assert str(other_item.paper_id) not in text_body
        lines = text_body.strip("\r\n").split("\r\n")
        assert lines == [_CSV_EXPECTED_HEADER]  # 自分の分がゼロ件ならヘッダのみ。
    finally:
        await purge_user(db_session, str(other_user.id))
        await db_session.commit()


# ---------------------------------------------------------------------------
# PY-EXP-04: 全量 JSON(API 層。ジョブ実処理は apps/worker/tests/test_export_bulk.py)
# ---------------------------------------------------------------------------
async def test_export_full_starts_job_and_status_reflects_completion(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth

    start = await client.post("/api/export/full")
    assert start.status_code == 202, start.text
    job_id = start.json()["job_id"]

    job = await db_session.get(Job, job_id)
    assert job is not None
    assert job.kind == "export"
    assert str(job.user_id) == uid

    pending = await client.get(f"/api/export/full/{job_id}")
    assert pending.status_code == 200, pending.text
    pending_body = pending.json()
    assert pending_body["download_url"] is None
    assert pending_body["job"]["id"] == job_id
    assert pending_body["job"]["status"] == "queued"

    # ジョブ実行(zip 化・S3 アップロード)は worker の責務(M2-15。ここでは完了状態を模す)。
    job.status = "succeeded"
    job.result = {"download_url": "https://example.test/exports/abc.zip"}
    await db_session.commit()

    done = await client.get(f"/api/export/full/{job_id}")
    assert done.status_code == 200, done.text
    done_body = done.json()
    assert done_body["download_url"] == "https://example.test/exports/abc.zip"
    assert done_body["job"]["status"] == "succeeded"


async def test_export_full_status_other_users_job_is_404(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, _uid = auth
    other_user = await factories.make_user(db_session)
    other_job = await factories.make_job(db_session, kind="export", user=other_user)
    await db_session.commit()
    try:
        resp = await client.get(f"/api/export/full/{other_job.id}")
        assert resp.status_code == 404, resp.text
    finally:
        await purge_user(db_session, str(other_user.id))
        await db_session.commit()
