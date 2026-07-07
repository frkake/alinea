"""M1-15 指示つき再翻訳・proposal・手動編集 API テスト(PY-TR-09。plans/03 §7.6〜7.8・plans/06 §11)。

- 指示つき再翻訳(`retranslate` の `instruction`)はジョブ payload に反映され、上位モデルへの
  エスカレーション(docs/03 §9)は worker 側(§21)の責務。API はジョブ作成までを担う。
- 手動編集(state=edited)のユニットは `discard_edit` なしの再翻訳を 409 `conflict`
  (detail `edit_protected`)で拒否する。用語変更ジョブでの非上書きは PY-GLS-02 で確認する。
- 手動編集 PUT・proposal accept はいずれも共有セットへの書き込みを personal フォークへ
  透過的に変換する(plans/06 §9.2)。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest_asyncio
from factories import (
    make_library_item,
    make_paper,
    make_translation_set,
    make_translation_unit,
    make_user,
)
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.services.session_service import COOKIE_NAME, create_session
from yakudoku_api.services.user_service import purge_user
from yakudoku_core.db.models import (
    DocumentRevision,
    Job,
    Paper,
    TranslationSet,
    TranslationUnit,
)
from yakudoku_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from yakudoku_core.document.inlines import Inline
from yakudoku_core.search.rebuild import rebuild_block_search_index


def _p(block_id: str, text: str) -> Block:
    return Block(id=block_id, type="paragraph", inlines=[Inline(t="text", v=text)])


def _make_document() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    _p("blk-a", "Rectified flow straightens the transport map."),
                    _p("blk-b", "The model learns a velocity field over time."),
                    _p("blk-c", "We evaluate on standard image generation benchmarks."),
                ],
            )
        ],
    )


async def _make_revision(
    db: AsyncSession, *, paper: Paper, content: DocumentContent
) -> DocumentRevision:
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=str(paper.id),
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content=content.model_dump(),
    )
    db.add(revision)
    await db.flush()
    paper.latest_revision_id = revision.id
    await rebuild_block_search_index(db, str(revision.id), content)
    return revision


@pytest_asyncio.fixture
async def ctx(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[SimpleNamespace]:
    user = await make_user(db_session, email=f"tr9-{uuid.uuid4().hex}@example.com")
    paper = await make_paper(db_session, owner=user, visibility="private")
    content = _make_document()
    revision = await _make_revision(db_session, paper=paper, content=content)
    li = await make_library_item(db_session, user=user, paper=paper, status="reading")
    shared = await make_translation_set(
        db_session, revision=revision, style="natural", scope="shared", status="complete"
    )
    await db_session.commit()
    user_id = str(user.id)  # rollback 後の属性アクセス(greenlet 事故)を避けるため先に確定

    token = await create_session(redis_client, user_id)
    client.cookies.set(COOKIE_NAME, token)
    try:
        yield SimpleNamespace(
            user=user,
            user_id=user_id,
            paper=paper,
            revision=revision,
            library_item=li,
            shared=shared,
        )
    finally:
        # rollback は既存オブジェクトを expire し、対象ユーザーが既にセッションから見えなく
        # なる場合に greenlet エラーを誘発するため使わない(commit で安全に終端する)。
        await db_session.commit()
        await purge_user(db_session, user_id)
        await db_session.commit()


# ---------------------------------------------------------------------------
# §7.6: 指示つき再翻訳・edit_protected 409
# ---------------------------------------------------------------------------
async def test_retranslate_with_instruction_and_edit_protection(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    unit = await make_translation_unit(
        db_session, translation_set=ctx.shared, block_id="blk-a", text_ja="旧訳"
    )
    await db_session.commit()

    # 指示なし再翻訳。
    r = await client.post(f"/api/translation-units/{unit.id}/retranslate", json={})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    job = await db_session.get(Job, job_id)
    assert job is not None
    assert job.payload["reason"] == "retranslate"
    assert job.payload["block_ids"] == ["blk-a"]
    assert job.payload["instruction"] == ""

    # 指示つき再翻訳(「もっと簡潔に」)。
    r2 = await client.post(
        f"/api/translation-units/{unit.id}/retranslate",
        json={"instruction": "もっと簡潔に"},
    )
    assert r2.status_code == 202, r2.text
    job2_id = r2.json()["job_id"]
    assert job2_id != job_id
    job2 = await db_session.get(Job, job2_id)
    assert job2 is not None
    assert job2.payload["reason"] == "instructed"
    assert job2.payload["instruction"] == "もっと簡潔に"

    # 同一指示の再送は冪等(同じ job_id を返す)。
    r3 = await client.post(
        f"/api/translation-units/{unit.id}/retranslate",
        json={"instruction": "もっと簡潔に"},
    )
    assert r3.status_code == 202, r3.text
    assert r3.json()["job_id"] == job2_id

    # 手動編集(state=edited)後は discard_edit なしで 409 conflict/edit_protected。
    edit_resp = await client.put(
        f"/api/translation-units/{unit.id}", json={"text_ja": "手動で編集した訳"}
    )
    assert edit_resp.status_code == 200, edit_resp.text
    edited_body = edit_resp.json()
    assert edited_body["state"] == "edited"
    edited_unit_id = edited_body["unit_id"]

    r4 = await client.post(f"/api/translation-units/{edited_unit_id}/retranslate", json={})
    assert r4.status_code == 409, r4.text
    problem = r4.json()
    assert problem["code"] == "conflict"
    assert "edit_protected" in problem["detail"]

    # discard_edit=true なら再翻訳ジョブを許可する。
    r5 = await client.post(
        f"/api/translation-units/{edited_unit_id}/retranslate",
        json={"discard_edit": True},
    )
    assert r5.status_code == 202, r5.text


# ---------------------------------------------------------------------------
# §7.7: 手動編集は共有セットを自動 personal フォーク(冪等)
# ---------------------------------------------------------------------------
async def test_manual_edit_forks_personal_set_idempotently(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    unit = await make_translation_unit(
        db_session, translation_set=ctx.shared, block_id="blk-b", text_ja="旧訳b"
    )
    await db_session.commit()

    r1 = await client.put(f"/api/translation-units/{unit.id}", json={"text_ja": "編集1"})
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["set_id"] != str(ctx.shared.id)
    assert body1["state"] == "edited"
    assert body1["text_ja"] == "編集1"

    # 元の shared unit は変更されない(plans/06 §9.2)。
    await db_session.refresh(unit)
    assert unit.state == "machine"
    assert unit.text_ja == "旧訳b"

    # 同じブロックへの再度の PUT は既存の personal フォークを再利用する。
    r2 = await client.put(f"/api/translation-units/{unit.id}", json={"text_ja": "編集2"})
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["set_id"] == body1["set_id"]
    assert body2["unit_id"] == body1["unit_id"]
    assert body2["text_ja"] == "編集2"

    personal_sets = (
        (
            await db_session.execute(
                select(TranslationSet).where(
                    TranslationSet.revision_id == ctx.revision.id,
                    TranslationSet.scope == "personal",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(personal_sets) == 1


# ---------------------------------------------------------------------------
# §7.8: proposal の採用・破棄
# ---------------------------------------------------------------------------
async def test_proposal_accept_forks_and_clears_proposal(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    unit = await make_translation_unit(
        db_session, translation_set=ctx.shared, block_id="blk-b", text_ja="旧訳"
    )
    unit.proposal = {
        "text_ja": "改善後の訳文",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "model": "claude-sonnet-5",
    }
    await db_session.commit()

    r = await client.post(f"/api/translation-units/{unit.id}/proposal/accept")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "machine"
    assert body["text_ja"] == "改善後の訳文"
    accepted_unit_id = body["unit_id"]
    assert accepted_unit_id != str(unit.id)  # shared → personal フォーク

    accepted = await db_session.get(TranslationUnit, int(accepted_unit_id))
    assert accepted is not None
    assert accepted.proposal is None
    assert accepted.model == "claude-sonnet-5"
    assert accepted.quality_flags == []

    # 元の shared unit の proposal はここでは変更しない(他ユーザーの視点に影響しないため)。
    await db_session.refresh(unit)
    assert unit.state == "machine"

    # 表示 API(§7.2)は personal 優先マージで採用結果を返す。
    r2 = await client.get(
        f"/api/revisions/{ctx.revision.id}/translations/natural/units",
        params={"section_id": "sec-1"},
    )
    assert r2.status_code == 200, r2.text
    items = {i["block_id"]: i for i in r2.json()["items"]}
    assert items["blk-b"]["text_ja"] == "改善後の訳文"
    assert items["blk-b"]["state"] == "machine"
    assert items["blk-b"]["proposal"] is None


async def test_proposal_accept_without_proposal_is_not_found(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    unit = await make_translation_unit(
        db_session, translation_set=ctx.shared, block_id="blk-c", text_ja="訳"
    )
    await db_session.commit()
    r = await client.post(f"/api/translation-units/{unit.id}/proposal/accept")
    assert r.status_code == 404, r.text


async def test_proposal_discard_clears_without_forking(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    unit = await make_translation_unit(
        db_session, translation_set=ctx.shared, block_id="blk-c", text_ja="旧訳c"
    )
    unit.proposal = {
        "text_ja": "破棄される案",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "model": "m",
    }
    await db_session.commit()

    r = await client.delete(f"/api/translation-units/{unit.id}/proposal")
    assert r.status_code == 204, r.text

    await db_session.refresh(unit)
    assert unit.proposal is None
    assert unit.state == "machine"
    assert unit.text_ja == "旧訳c"

    matching_units = (
        (
            await db_session.execute(
                select(TranslationUnit)
                .join(TranslationSet, TranslationSet.id == TranslationUnit.set_id)
                .where(
                    TranslationUnit.block_id == "blk-c",
                    TranslationSet.revision_id == ctx.revision.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(matching_units) == 1  # 破棄はフォークしない
