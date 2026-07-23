"""vocab API テスト(M2-11。plans/03 §11・docs/11)。

- PY-VOC-01: 「語彙に追加」(anchorから文脈センテンス・出典・追加日が自動付与、重複語 409)。
- PY-VOC-05: 一覧(種別チップ・「復習期」フィルタ・語彙/追加日ソート・語彙帳内検索)。
- PY-VOC-06: SRS 規則(docs/11 §7.1)の全パターン。
- PY-VOC-07: 復習期件数の一致(review-queue 件数 = counts.due)。
- PY-VOC-08: 語彙 Markdown エクスポート。
- PY-VOC-09: 用語集(glossary_terms)からの独立性。

AI 生成成功/失敗(PY-VOC-02〜04)は worker タスクを直接呼ぶ
:mod:`apps/worker/tests/test_generate_vocab_ai.py` で検証する(本ファイルは API 契約のみ)。

DB は実 PostgreSQL。他タスクの WIP ルータを巻き込まないよう、本タスク所有の ``vocab.router``
のみをマウントした専用アプリで検証する(test_dashboard.py と同方針)。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import factories
import pytest_asyncio
from alinea_api.services.deadlines import today_jst
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.db.models import Glossary, GlossaryTerm, User, VocabEntry
from alinea_core.document.blocks import DocumentContent
from alinea_core.search.rebuild import rebuild_block_search_index
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _build_app() -> FastAPI:
    """本タスク所有ルータ(vocab)のみをマウントしたアプリ(test_dashboard.py と同方針)。"""
    from alinea_api.errors import register_exception_handlers
    from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from alinea_api.ratelimit import RateLimitMiddleware
    from alinea_api.redis_client import get_redis
    from alinea_api.routers import vocab
    from alinea_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(vocab.router)
    return app


@pytest_asyncio.fixture
async def auth(db_session: AsyncSession, redis_client: Any) -> AsyncIterator[SimpleNamespace]:
    from alinea_api.routers.vocab import get_vocab_job_wakeup

    email = f"voc-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)  # rollback 後に ORM 属性へ触れないよう先に確定させる
    token = await create_session(redis_client, user.id)

    app = _build_app()
    wakeups: list[str] = []

    async def _noop_wakeup(job_id: str) -> None:
        wakeups.append(job_id)

    app.dependency_overrides[get_vocab_job_wakeup] = lambda: _noop_wakeup

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        ac.cookies.set("yk_session", token)
        try:
            yield SimpleNamespace(client=ac, user_id=uid, wakeups=wakeups)
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


@pytest_asyncio.fixture
async def vocab_ctx(
    auth: SimpleNamespace, db_session: AsyncSession
) -> AsyncIterator[SimpleNamespace]:
    """語彙帳テスト用の私有論文 + リビジョン(block_search_index 構築済み)+ 読書中エントリ。"""
    user = await db_session.get(User, auth.user_id)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    revision = await factories.make_revision(db_session, paper=paper)
    content = DocumentContent.model_validate(revision.content)
    await rebuild_block_search_index(db_session, str(revision.id), content)
    item = await factories.make_library_item(db_session, user=user, paper=paper, status="reading")
    await db_session.commit()
    yield SimpleNamespace(
        client=auth.client,
        user_id=auth.user_id,
        wakeups=auth.wakeups,
        db=db_session,
        user=user,
        paper=paper,
        revision=revision,
        item_id=str(item.id),
    )


def _create_payload(
    ctx: SimpleNamespace,
    *,
    term: str = "reflow",
    block_index: int = 3,
    side: str = "source",
    context_sentence: str = "The reflow procedure straightens paths.",
    highlight: dict[str, int] | None = None,
) -> dict[str, Any]:
    anchor = factories.anchor_for(ctx.revision, block_index, side=side)
    return {
        "library_item_id": ctx.item_id,
        "term": term,
        "anchor": anchor,
        "context_sentence": context_sentence,
        "highlight": highlight or {"start": 4, "end": 10},
    }


# ============================================================================
# PY-VOC-01: 作成・自動付与・重複 409・検証エラー
# ============================================================================
async def test_create_vocab_stores_context_source_and_enqueues_job(
    vocab_ctx: SimpleNamespace,
) -> None:
    resp = await vocab_ctx.client.post("/api/vocab", json=_create_payload(vocab_ctx))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    entry = body["entry"]

    assert entry["term"] == "reflow"
    assert entry["kind"] == "word"  # DB 既定(AI 生成前)
    assert entry["meaning_short"] is None  # 生成中は null(§11.1)
    assert entry["generation"] == "pending"
    assert entry["source"]["library_item_id"] == vocab_ctx.item_id
    assert entry["source"]["paper_title"] == vocab_ctx.paper.title
    assert entry["source"]["display"] == f"{vocab_ctx.paper.title} · §2 ¶1"
    assert entry["anchor"]["block_id"] == "blk-p3"
    assert entry["anchor"]["display"] == "§2 ¶1"
    assert entry["context_sentence"] == "The reflow procedure straightens paths."
    assert entry["highlight"] == {"start": 4, "end": 10}
    assert entry["ai"]["edited_fields"] == []
    assert entry["ai"]["generation_error"] is None
    assert entry["srs"] == {
        "stage": 1,
        "next_review_at": (today_jst() + dt.timedelta(days=1)).isoformat(),
        "review_count": 0,
        "history": [],
    }
    assert body["generation_job_id"]
    assert body["generation_job_id"] in vocab_ctx.wakeups

    # 一覧・詳細でも同じ内容が見える。
    listed = (await vocab_ctx.client.get("/api/vocab")).json()
    assert listed["counts"]["all"] == 1
    assert listed["counts"]["word"] == 1
    assert listed["items"][0]["id"] == entry["id"]

    got = await vocab_ctx.client.get(f"/api/vocab/{entry['id']}")
    assert got.status_code == 200
    assert got.json()["term"] == "reflow"


async def test_create_vocab_duplicate_term_is_409_normalized(
    vocab_ctx: SimpleNamespace,
) -> None:
    first = await vocab_ctx.client.post(
        "/api/vocab", json=_create_payload(vocab_ctx, term="Reflow")
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["entry"]["id"]

    dup = await vocab_ctx.client.post(
        "/api/vocab", json=_create_payload(vocab_ctx, term="  reflow  ")
    )
    assert dup.status_code == 409, dup.text
    body = dup.json()
    assert body["code"] == "duplicate"
    assert body["existing"] == {"vocab_id": first_id}

    # 重複扱いなので語彙帳には 1 件のみ。
    listed = (await vocab_ctx.client.get("/api/vocab")).json()
    assert listed["counts"]["all"] == 1


async def test_create_vocab_requires_source_side_anchor(vocab_ctx: SimpleNamespace) -> None:
    resp = await vocab_ctx.client.post(
        "/api/vocab", json=_create_payload(vocab_ctx, side="translation")
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_create_vocab_invalid_block_is_422(vocab_ctx: SimpleNamespace) -> None:
    broken = factories.broken_anchor(vocab_ctx.revision)
    payload = _create_payload(vocab_ctx)
    payload["anchor"] = broken
    resp = await vocab_ctx.client.post("/api/vocab", json=payload)
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_create_vocab_ownership_is_enforced(
    vocab_ctx: SimpleNamespace, db_session: AsyncSession
) -> None:
    other_item = await factories.make_library_item(db_session, status="reading")
    await db_session.commit()
    payload = _create_payload(vocab_ctx)
    payload["library_item_id"] = str(other_item.id)
    resp = await vocab_ctx.client.post("/api/vocab", json=payload)
    assert resp.status_code == 404


# ============================================================================
# PY-VOC-05: 一覧フィルタ・ソート・語彙帳内検索
# ============================================================================
async def test_list_vocab_kind_filter_search_and_sort(vocab_ctx: SimpleNamespace) -> None:
    word = await vocab_ctx.client.post(
        "/api/vocab", json=_create_payload(vocab_ctx, term="albeit", block_index=0)
    )
    idiom = await vocab_ctx.client.post(
        "/api/vocab",
        json=_create_payload(vocab_ctx, term="boil down to", block_index=1),
    )
    assert word.status_code == 201 and idiom.status_code == 201
    word_id = word.json()["entry"]["id"]
    idiom_id = idiom.json()["entry"]["id"]

    # DB 直更新で idiom エントリを idiom 種別・語義付きにする(AI 生成をシミュレート)。
    entry = await vocab_ctx.db.get(VocabEntry, idiom_id)
    assert entry is not None
    entry.kind = "idiom"
    entry.meaning_short = "煮詰まって〜に帰着する"
    await vocab_ctx.db.commit()

    only_idiom = (await vocab_ctx.client.get("/api/vocab", params={"kind": "idiom"})).json()
    assert [it["id"] for it in only_idiom["items"]] == [idiom_id]
    assert only_idiom["counts"] == {"all": 2, "word": 1, "collocation": 0, "idiom": 1, "due": 0}

    searched = (await vocab_ctx.client.get("/api/vocab", params={"q": "帰着"})).json()
    assert [it["id"] for it in searched["items"]] == [idiom_id]

    by_term = (await vocab_ctx.client.get("/api/vocab", params={"sort": "term"})).json()
    # "albeit" < "boil down to"(語彙 ↑ 昇順。§5.2)。
    assert [it["id"] for it in by_term["items"]] == [word_id, idiom_id]

    bad_kind = await vocab_ctx.client.get("/api/vocab", params={"kind": "not-a-kind"})
    assert bad_kind.status_code == 422


# ============================================================================
# 更新(PATCH)・削除
# ============================================================================
async def test_patch_vocab_updates_fields_and_tracks_edited(vocab_ctx: SimpleNamespace) -> None:
    created = await vocab_ctx.client.post("/api/vocab", json=_create_payload(vocab_ctx))
    vocab_id = created.json()["entry"]["id"]

    resp = await vocab_ctx.client.patch(
        f"/api/vocab/{vocab_id}",
        json={
            "kind": "collocation",
            "pos_label": "句動詞",
            "ai": {
                "context_meaning": {"short": "要するに帰着する", "long": "手動編集した語義。"},
                "mnemonic": "手動の覚えるコツ。",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "collocation"
    assert body["pos_label"] == "句動詞"
    assert body["meaning_short"] == "要するに帰着する"
    assert body["ai"]["context_meaning"] == {
        "short": "要するに帰着する",
        "long": "手動編集した語義。",
    }
    assert body["ai"]["mnemonic"] == "手動の覚えるコツ。"
    assert set(body["ai"]["edited_fields"]) == {
        "kind",
        "pos_label",
        "meaning_short",
        "meaning_long",
        "mnemonic",
    }


async def test_delete_vocab(vocab_ctx: SimpleNamespace) -> None:
    created = await vocab_ctx.client.post("/api/vocab", json=_create_payload(vocab_ctx))
    vocab_id = created.json()["entry"]["id"]

    resp = await vocab_ctx.client.delete(f"/api/vocab/{vocab_id}")
    assert resp.status_code == 204
    assert (await vocab_ctx.client.get(f"/api/vocab/{vocab_id}")).status_code == 404
    # 取り消し可能トーストは UI 側の責務(docs/11 §6.3)。再削除は 404。
    assert (await vocab_ctx.client.delete(f"/api/vocab/{vocab_id}")).status_code == 404


# ============================================================================
# 再生成トリガ(API 契約のみ。フィールド書き込みは worker テストで検証)
# ============================================================================
async def test_regenerate_resets_to_pending_and_enqueues_job(vocab_ctx: SimpleNamespace) -> None:
    created = await vocab_ctx.client.post("/api/vocab", json=_create_payload(vocab_ctx))
    vocab_id = created.json()["entry"]["id"]

    entry = await vocab_ctx.db.get(VocabEntry, vocab_id)
    assert entry is not None
    entry.generation_status = "failed"
    entry.generation_error = "前回の失敗理由"
    await vocab_ctx.db.commit()

    resp = await vocab_ctx.client.post(f"/api/vocab/{vocab_id}/regenerate", json={})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    assert job_id in vocab_ctx.wakeups

    got = (await vocab_ctx.client.get(f"/api/vocab/{vocab_id}")).json()
    assert got["generation"] == "pending"
    assert got["ai"]["generation_error"] is None


# ============================================================================
# PY-VOC-06: SRS 規則の全パターン
# ============================================================================
async def test_review_srs_follows_fixed_stage_schedule(vocab_ctx: SimpleNamespace) -> None:
    created = await vocab_ctx.client.post("/api/vocab", json=_create_payload(vocab_ctx))
    vocab_id = created.json()["entry"]["id"]
    today = today_jst()

    async def _review(result: str) -> dict[str, Any]:
        resp = await vocab_ctx.client.post(f"/api/vocab/{vocab_id}/review", json={"result": result})
        assert resp.status_code == 200, resp.text
        data: dict[str, Any] = resp.json()
        return data

    # 保存時: 段階1・翌日(初期状態そのもの)。
    detail = (await vocab_ctx.client.get(f"/api/vocab/{vocab_id}")).json()
    assert detail["srs"]["stage"] == 1
    assert detail["srs"]["next_review_at"] == (today + dt.timedelta(days=1)).isoformat()

    # 1 回目: まだあやしい → 段階1リセット・翌日・2 回目という表示。
    r1 = await _review("again")
    assert r1["srs"]["stage"] == 1
    assert r1["srs"]["next_review_at"] == (today + dt.timedelta(days=1)).isoformat()
    assert r1["srs"]["review_count"] == 1
    assert r1["next_review_display"] == "次の復習: 明日(2 回目)"

    # 2〜5 回目: 覚えた → 段階 2/3/4/5、間隔 3/7/14/30 日。
    intervals = {2: 3, 3: 7, 4: 14, 5: 30}
    for stage in (2, 3, 4, 5):
        r = await _review("good")
        assert r["srs"]["stage"] == stage
        assert (
            r["srs"]["next_review_at"] == (today + dt.timedelta(days=intervals[stage])).isoformat()
        )

    # 段階 5 を「覚えた」で通過 → 習得済み(キュー除外。next_review_at=null)。
    mastered = await _review("good")
    assert mastered["srs"]["stage"] == 5
    assert mastered["srs"]["next_review_at"] is None
    assert mastered["next_review_display"] == "習得済み"

    # 習得済みでも「まだあやしい」でいつでも段階 1 に戻せる。
    reset = await _review("again")
    assert reset["srs"]["stage"] == 1
    assert reset["srs"]["next_review_at"] == (today + dt.timedelta(days=1)).isoformat()

    # 履歴が全評価分積み上がっている(1 + 4 + 1 + 1 = 7 回)。
    final = (await vocab_ctx.client.get(f"/api/vocab/{vocab_id}")).json()
    assert final["srs"]["review_count"] == 7
    assert len(final["srs"]["history"]) == 7


# ============================================================================
# PY-VOC-07: 復習期件数の一致
# ============================================================================
async def test_review_queue_count_matches_due_chip(vocab_ctx: SimpleNamespace) -> None:
    due_ids: list[str] = []
    for i in range(2):
        created = await vocab_ctx.client.post(
            "/api/vocab", json=_create_payload(vocab_ctx, term=f"due-term-{i}")
        )
        vocab_id = created.json()["entry"]["id"]
        entry = await vocab_ctx.db.get(VocabEntry, vocab_id)
        assert entry is not None
        entry.srs_next_review_on = today_jst() - dt.timedelta(days=1)
        due_ids.append(vocab_id)
    await vocab_ctx.db.commit()

    not_due = await vocab_ctx.client.post(
        "/api/vocab", json=_create_payload(vocab_ctx, term="not-due-term")
    )
    assert not_due.status_code == 201

    mastered = await vocab_ctx.client.post(
        "/api/vocab", json=_create_payload(vocab_ctx, term="mastered-term")
    )
    mastered_id = mastered.json()["entry"]["id"]
    mastered_entry = await vocab_ctx.db.get(VocabEntry, mastered_id)
    assert mastered_entry is not None
    mastered_entry.srs_mastered = True
    mastered_entry.srs_next_review_on = today_jst() - dt.timedelta(days=1)
    await vocab_ctx.db.commit()

    queue = (await vocab_ctx.client.get("/api/vocab/review-queue")).json()
    assert queue["total"] == 2
    assert {it["id"] for it in queue["items"]} == set(due_ids)

    listed = (await vocab_ctx.client.get("/api/vocab")).json()
    assert listed["counts"]["due"] == queue["total"]
    assert listed["counts"]["all"] == 4  # サイドバーバッジ用(総語数)は due と独立


# ============================================================================
# PY-VOC-08: Markdown エクスポート
# ============================================================================
async def test_export_markdown_includes_context_and_source(vocab_ctx: SimpleNamespace) -> None:
    created = await vocab_ctx.client.post("/api/vocab", json=_create_payload(vocab_ctx))
    assert created.status_code == 201

    resp = await vocab_ctx.client.get("/api/vocab/export/markdown")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "attachment" in resp.headers["content-disposition"]
    assert "alinea-vocab-" in resp.headers["content-disposition"]

    text = resp.text
    assert "reflow" in text
    assert "The reflow procedure straightens paths." in text
    assert vocab_ctx.paper.title in text


# ============================================================================
# PY-VOC-09: 用語集からの独立性
# ============================================================================
async def test_glossary_changes_do_not_affect_vocab(
    vocab_ctx: SimpleNamespace, db_session: AsyncSession
) -> None:
    created = await vocab_ctx.client.post("/api/vocab", json=_create_payload(vocab_ctx))
    assert created.status_code == 201
    before = (await vocab_ctx.client.get("/api/vocab")).json()

    glossary = Glossary(scope="user", user_id=vocab_ctx.user_id, name="")
    db_session.add(glossary)
    await db_session.flush()
    term = GlossaryTerm(glossary_id=str(glossary.id), source_term="reflow", target_term="リフロー")
    db_session.add(term)
    await db_session.commit()

    after_create = (await vocab_ctx.client.get("/api/vocab")).json()
    assert after_create["counts"] == before["counts"]

    term.target_term = "リフロー(改)"
    await db_session.commit()
    after_update = (await vocab_ctx.client.get("/api/vocab")).json()
    assert after_update["counts"] == before["counts"]

    # glossaries.id への ON DELETE CASCADE で glossary_terms も削除される(0001)。
    await db_session.delete(glossary)
    await db_session.commit()
    after_delete = (await vocab_ctx.client.get("/api/vocab")).json()
    assert after_delete["counts"] == before["counts"]


# ============================================================================
# PY-VOC-10: Anki TSV エクスポート
# ============================================================================
def test_render_anki_tsv_fields() -> None:
    """_render_anki_tsv() が Front/Back/tags 列を正しく組み立てる。"""
    from alinea_api.routers.vocab import _render_anki_tsv
    from alinea_api.schemas.chat import AnchorRef
    from alinea_api.schemas.vocab import (
        VocabAi,
        VocabEntryDetail,
        VocabHighlight,
        VocabMeaning,
        VocabSource,
        VocabSrs,
    )

    entry = VocabEntryDetail(
        id="test-id",
        kind="word",
        term="reflow",
        meaning_short="リフロー",
        source=VocabSource(
            library_item_id="lib-id",
            paper_title="Rectified Flow Paper",
            display="Rectified Flow · §2.1",
        ),
        added_at="2026-01-01",
        generation="done",
        pos_label="noun",
        ipa="/ˈriːfloʊ/",  # noqa: RUF001 - intentional IPA (stress mark / length mark)
        anchor=AnchorRef(revision_id="rev-id", block_id="blk", display="§2.1"),
        context_sentence="The reflow procedure straightens paths.",
        highlight=VocabHighlight(start=4, end=10),
        ai=VocabAi(
            context_meaning=VocabMeaning(short="リフロー", long="パスを整列させる手順"),
            interpretation="経路の整列手法",
            etymology=None,
            mnemonic="re + flow = 再び流す",
        ),
        srs=VocabSrs(stage=1, next_review_at="2026-01-02", review_count=0, history=[]),
    )

    tsv = _render_anki_tsv([entry])
    lines = tsv.splitlines()

    # ヘッダ行
    assert lines[0] == "#separator:tab"
    assert lines[1] == "#html:true"
    assert lines[2] == "#tags column:3"

    # カード行
    assert len(lines) == 4
    parts = lines[3].split("\t")
    assert len(parts) == 3

    front, back, tags = parts
    # Front
    assert "reflow" in front
    assert "noun" in front
    assert "/ˈriːfloʊ/" in front  # noqa: RUF001 - intentional IPA (stress mark / length mark)

    # Back
    assert "リフロー" in back
    assert "パスを整列させる手順" in back
    assert "The reflow procedure straightens paths." in back
    assert "経路の整列手法" in back
    assert "re + flow = 再び流す" in back
    assert "Rectified Flow · §2.1" in back

    # Tags
    assert "alinea" in tags
    assert "word" in tags


async def test_export_anki_tsv_structure(vocab_ctx: SimpleNamespace) -> None:
    """PY-VOC-10a: エンドポイントが正しい TSV ヘッダと Content-Disposition を返す。"""
    created = await vocab_ctx.client.post("/api/vocab", json=_create_payload(vocab_ctx))
    assert created.status_code == 201

    resp = await vocab_ctx.client.get("/api/vocab/export/anki")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "attachment" in resp.headers["content-disposition"]
    assert "alinea-vocab-" in resp.headers["content-disposition"]
    assert resp.headers["content-disposition"].endswith('.txt"')

    text = resp.text
    lines = text.splitlines()
    assert lines[0] == "#separator:tab"
    assert lines[1] == "#html:true"
    assert lines[2] == "#tags column:3"
    # カード行が 1 行以上
    assert len(lines) >= 4
    # カード行はタブ 2 本(3 列)
    assert lines[3].count("\t") == 2


async def test_export_anki_contains_term_and_context(vocab_ctx: SimpleNamespace) -> None:
    """PY-VOC-10b: カード内に term と context_sentence が含まれる。"""
    created = await vocab_ctx.client.post("/api/vocab", json=_create_payload(vocab_ctx))
    assert created.status_code == 201

    resp = await vocab_ctx.client.get("/api/vocab/export/anki")
    assert resp.status_code == 200

    text = resp.text
    assert "reflow" in text
    assert "The reflow procedure straightens paths." in text
    assert "alinea" in text


async def test_export_anki_filter_kind(vocab_ctx: SimpleNamespace) -> None:
    """PY-VOC-10c: kind=word フィルタで word のみ返す(word エントリのみ登録)。"""
    # word エントリ追加
    await vocab_ctx.client.post("/api/vocab", json=_create_payload(vocab_ctx))

    # kind=word のみ要求
    resp = await vocab_ctx.client.get("/api/vocab/export/anki?kind=word")
    assert resp.status_code == 200

    card_lines = [line for line in resp.text.splitlines() if not line.startswith("#")]
    # word タグのみ
    for line in card_lines:
        tags = line.split("\t")[2]
        assert "word" in tags


def test_render_anki_tsv_always_3_columns_per_row() -> None:
    """_render_anki_tsv() の全データ行が必ず 3 列(タブ 2 本)であること(回帰テスト)。"""
    from alinea_api.routers.vocab import _render_anki_tsv
    from alinea_api.schemas.chat import AnchorRef
    from alinea_api.schemas.vocab import (
        VocabAi,
        VocabEntryDetail,
        VocabHighlight,
        VocabMeaning,
        VocabSource,
        VocabSrs,
    )

    def _entry(term: str, context: str) -> VocabEntryDetail:
        return VocabEntryDetail(
            id=f"id-{term}",
            kind="word",
            term=term,
            meaning_short="意味",
            source=VocabSource(
                library_item_id="li-1",
                paper_title="Paper\tWith\tTabs",
                display="Paper · §1",
            ),
            added_at="2026-01-01",
            generation="done",
            pos_label=None,
            ipa=None,
            anchor=AnchorRef(revision_id="rev", block_id="blk", display="§1"),
            context_sentence=context,
            highlight=VocabHighlight(start=0, end=len(term)),
            ai=VocabAi(
                context_meaning=VocabMeaning(short="short", long="line1\nline2"),
                interpretation=None,
                etymology=None,
                mnemonic=None,
            ),
            srs=VocabSrs(stage=1, next_review_at="2026-01-02", review_count=0, history=[]),
        )

    entries = [
        _entry("hello", "Hello\tworld"),   # タブを含む context
        _entry("foo", "line1\nline2"),      # 改行を含む context
        _entry("<bar>", "<b>bold</b>"),     # HTML 特殊文字
    ]
    tsv = _render_anki_tsv(entries)
    data_lines = [line for line in tsv.splitlines() if not line.startswith("#")]
    assert all(len(line.split("\t")) == 3 for line in data_lines), (
        "全データ行が 3 列でなければならない"
    )


def test_anki_cell_sanitizer() -> None:
    """_anki_cell() が改行→<br>、タブ→空白、HTML escape を行う。"""
    from alinea_api.routers.vocab import _anki_cell

    assert _anki_cell("a\tb") == "a b"
    assert _anki_cell("a\nb") == "a<br>b"
    assert _anki_cell("a\r\nb") == "a<br>b"
    assert _anki_cell("a\rb") == "a<br>b"  # 孤立 CR も <br> に変換
    assert _anki_cell("<b>bold</b>") == "&lt;b&gt;bold&lt;/b&gt;"
