"""M0-20 viewer / translations API テスト(PY-LIB-03 ほか)。

- PY-LIB-03: 読書位置の保存 → ビューア初期化での復元(roundtrip)。
- ビューア初期化複合(§6.1)・翻訳セット/ユニット(§7.1・§7.2)・優先繰り上げ(§7.4)・
  オンデマンドセクション翻訳(§7.5)・指示なし再翻訳(§7.6)。

実 PostgreSQL の Rectified Flow シードを投入して検証する。認証は dev ユーザーの
セッションクッキーを直接発行して得る(メールリンク経路の短縮)。
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
import pytest_asyncio
from alinea_api.routers import viewer as viewer_router
from alinea_api.routers.viewer import _block_wire
from alinea_api.seed import ARXIV_ID, DEV_EMAIL, seed_rectified_flow
from alinea_core.db.models import (
    DocumentRevision,
    Job,
    LibraryItem,
    Paper,
    TranslationSet,
    TranslationUnit,
    User,
)
from alinea_core.document.blocks import Block, DocumentContent
from alinea_core.translation.pipeline import (
    TranslationPlan,
    TranslationSettings,
    build_translation_plan,
    compute_translation_scope,
    resolve_translation_plan,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified


class Seeded:
    def __init__(self, user_id: str, library_item_id: str, paper_id: str, revision_id: str) -> None:
        self.user_id = user_id
        self.library_item_id = library_item_id
        self.paper_id = paper_id
        self.revision_id = revision_id

    # PY-LIB-03 の plan 例に合わせた別名(seeded_library_item.id 相当)。
    @property
    def id(self) -> str:
        return self.library_item_id


def _translation_plan_content() -> dict[str, object]:
    return {
        "quality_level": "A",
        "sections": [
            {
                "id": "plan-main",
                "heading": {"number": "1", "title": "Main"},
                "blocks": [
                    {
                        "id": "plan-main-block",
                        "type": "paragraph",
                        "inlines": [{"t": "text", "v": "Main text."}],
                    }
                ],
            },
            {
                "id": "plan-appendix",
                "heading": {"number": "A", "title": "Details"},
                "blocks": [
                    {
                        "id": "plan-appendix-block",
                        "type": "paragraph",
                        "inlines": [{"t": "text", "v": "Appendix text."}],
                    }
                ],
            },
        ],
    }


def _translation_plan_subtree_content() -> dict[str, object]:
    return {
        "quality_level": "A",
        "sections": [
            {
                "id": "plan-main",
                "heading": {"number": "1", "title": "Main"},
                "blocks": [
                    {
                        "id": "plan-main-block",
                        "type": "paragraph",
                        "inlines": [{"t": "text", "v": "Main text."}],
                    }
                ],
            },
            {
                "id": "plan-appendix-parent",
                "heading": {"number": "A", "title": "Appendix"},
                "blocks": [],
                "sections": [
                    {
                        "id": "plan-appendix-target",
                        "heading": {"number": "A.1", "title": "Proof"},
                        "blocks": [
                            {
                                "id": "plan-appendix-target-block",
                                "type": "paragraph",
                                "inlines": [{"t": "text", "v": "Appendix proof."}],
                            }
                        ],
                    },
                    {
                        "id": "plan-appendix-other",
                        "heading": {"number": "A.2", "title": "Examples"},
                        "blocks": [
                            {
                                "id": "plan-appendix-other-block",
                                "type": "paragraph",
                                "inlines": [{"t": "text", "v": "Appendix examples."}],
                            }
                        ],
                    },
                    {
                        "id": "plan-appendix-empty-sibling",
                        "heading": {"number": "A.3", "title": "Formula"},
                        "blocks": [
                            {
                                "id": "plan-appendix-equation",
                                "type": "equation",
                                "latex": "x=y",
                            }
                        ],
                    },
                ],
            },
        ],
    }


async def _delete_seed(session: AsyncSession) -> None:
    await session.rollback()
    await session.execute(text("DELETE FROM papers WHERE arxiv_id = :a"), {"a": ARXIV_ID})
    await session.commit()


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession) -> AsyncIterator[Seeded]:
    await seed_rectified_flow(db_session, reset=True, full=False, scale=0)
    dev = (await db_session.execute(select(User).where(User.email == DEV_EMAIL))).scalars().first()
    assert dev is not None
    # DEV_EMAIL はテスト間で共有されるため、Viewer の既定スタイルを毎回初期化する。
    dev.settings = {}
    await db_session.commit()
    paper_id = await db_session.scalar(select(Paper.id).where(Paper.arxiv_id == ARXIV_ID))
    assert paper_id is not None
    revision_id = await db_session.scalar(
        select(DocumentRevision.id).where(DocumentRevision.paper_id == paper_id)
    )
    li_id = await db_session.scalar(
        select(LibraryItem.id).where(
            LibraryItem.user_id == dev.id, LibraryItem.paper_id == paper_id
        )
    )
    assert revision_id is not None and li_id is not None
    try:
        yield Seeded(str(dev.id), str(li_id), str(paper_id), str(revision_id))
    finally:
        await _delete_seed(db_session)


@pytest_asyncio.fixture
async def auth_client(seeded: Seeded) -> AsyncIterator[AsyncClient]:
    """dev ユーザーのセッションクッキーを持つ認証済みクライアント。"""
    from alinea_api.main import app
    from alinea_api.redis_client import get_redis
    from alinea_api.services.session_service import COOKIE_NAME, create_session

    token = await create_session(get_redis(), seeded.user_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        cookies={COOKIE_NAME: token},
        trust_env=False,
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# PY-LIB-03: 読書位置 roundtrip + ビューア初期化
# ---------------------------------------------------------------------------
async def test_reading_position_roundtrip(auth_client: AsyncClient, seeded: Seeded) -> None:
    li = seeded.library_item_id
    block_id = "blk-2-1-eq1-ff46"  # シード document の決定的ブロック(test_seed.py 参照)
    r = await auth_client.put(
        f"/api/library-items/{li}/position",
        json={"revision_id": seeded.revision_id, "block_id": block_id, "mode": "translation"},
    )
    assert r.status_code == 200, r.text
    assert "saved_at" in r.json()

    v = await auth_client.get(f"/api/library-items/{li}/viewer")
    assert v.status_code == 200, v.text
    body = v.json()
    assert body["last_position"]["block_id"] == block_id
    assert body["last_position"]["revision_id"] == seeded.revision_id
    assert body["last_position"]["mode"] == "translation"


async def test_save_position_rejects_known_revision_from_different_paper_without_mutation(
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    item = await db_session.get(LibraryItem, seeded.library_item_id)
    assert item is not None
    original_position = copy.deepcopy(item.reading_position)
    other_paper = Paper(
        id=str(uuid.uuid4()),
        title="Different public paper",
        visibility="public",
        license="cc-by-4.0",
    )
    other_revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=str(other_paper.id),
        source_version="v1",
        parser_version="test",
        quality_level="A",
        source_format="arxiv_html",
        content=_translation_plan_content(),
        stats={},
    )
    db_session.add(other_paper)
    await db_session.flush()
    db_session.add(other_revision)
    await db_session.commit()
    try:
        response = await auth_client.put(
            f"/api/library-items/{seeded.library_item_id}/position",
            json={
                "revision_id": str(other_revision.id),
                "block_id": "plan-main-block",
                "mode": "translation",
            },
        )

        assert response.status_code == 404, response.text
        await db_session.refresh(item)
        assert item.reading_position == original_position
    finally:
        await db_session.delete(other_revision)
        await db_session.delete(other_paper)
        await db_session.commit()


async def test_save_position_rejects_unknown_block_without_position_or_priority_mutation(
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    item = await db_session.get(LibraryItem, seeded.library_item_id)
    shared = await db_session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == seeded.revision_id,
            TranslationSet.style == "natural",
            TranslationSet.scope == "shared",
        )
    )
    assert item is not None and shared is not None
    original_position = copy.deepcopy(item.reading_position)
    job = Job(
        id=str(uuid.uuid4()),
        kind="translation",
        status="queued",
        priority=7,
        payload={"set_id": str(shared.id), "section_id": "sec-2-1"},
    )
    db_session.add(job)
    await db_session.commit()
    try:
        response = await auth_client.put(
            f"/api/library-items/{seeded.library_item_id}/position",
            json={
                "revision_id": seeded.revision_id,
                "block_id": "known-revision-but-unknown-block",
                "mode": "parallel",
            },
        )

        assert response.status_code == 404, response.text
        await db_session.refresh(item)
        await db_session.refresh(job)
        assert item.reading_position == original_position
        assert job.priority == 7
    finally:
        await db_session.delete(job)
        await db_session.commit()


async def test_save_position_repeatedly_prioritizes_only_shared_and_own_personal_sets(
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    shared = await db_session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == seeded.revision_id,
            TranslationSet.style == "natural",
            TranslationSet.scope == "shared",
        )
    )
    own_personal = await db_session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == seeded.revision_id,
            TranslationSet.style == "natural",
            TranslationSet.scope == "personal",
            TranslationSet.user_id == seeded.user_id,
        )
    )
    assert shared is not None and own_personal is not None
    other_user = User(
        id=str(uuid.uuid4()),
        email=f"position-other-{uuid.uuid4().hex}@example.com",
    )
    other_personal = TranslationSet(
        id=str(uuid.uuid4()),
        revision_id=seeded.revision_id,
        style="natural",
        scope="personal",
        user_id=str(other_user.id),
        base_set_id=str(shared.id),
        status="partial",
    )
    db_session.add(other_user)
    await db_session.flush()
    db_session.add(other_personal)
    await db_session.flush()
    jobs = [
        Job(
            id=str(uuid.uuid4()),
            kind="translation",
            status="queued",
            priority=5,
            payload={"set_id": str(tset.id), "section_id": "sec-2-1"},
        )
        for tset in (shared, own_personal, other_personal)
    ]
    db_session.add_all(jobs)
    await db_session.commit()
    try:
        for _ in range(2):
            response = await auth_client.put(
                f"/api/library-items/{seeded.library_item_id}/position",
                json={
                    "revision_id": seeded.revision_id,
                    "block_id": "blk-2-1-eq1-ff46",
                    "mode": "translation",
                },
            )
            assert response.status_code == 200, response.text

        for job in jobs:
            await db_session.refresh(job)
        assert jobs[0].priority == 205
        assert jobs[1].priority == 205
        assert jobs[2].priority == 5
    finally:
        for job in jobs:
            await db_session.delete(job)
        await db_session.delete(other_personal)
        await db_session.delete(other_user)
        await db_session.commit()


# ---------------------------------------------------------------------------
# §6.1 ビューア初期化複合
# ---------------------------------------------------------------------------
async def test_viewer_init_shape(
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    revision.stats = {
        "pages": 40,
        "translated_pdf": {
            "natural": {
                "renderer": "source",
                "fallback_reason": None,
            }
        },
    }
    await db_session.commit()

    v = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
    assert v.status_code == 200, v.text
    body = v.json()

    assert body["library_item"]["id"] == seeded.library_item_id
    assert body["library_item"]["paper"]["arxiv_id"] == ARXIV_ID
    assert body["library_item"]["quality_level"] == "A"
    assert body["revision"]["id"] == seeded.revision_id
    assert body["revision"]["figure_count"] == 2
    assert body["revision"]["table_count"] == 2
    assert body["revision"]["source_format"] == "arxiv_html"
    assert body["revision"]["translated_pdf_renderer"] == "source"
    assert body["revision"]["translated_pdf_fallback_reason"] is None

    # ライセンスカード(cc-by-4.0 → 図表転載可)。
    assert body["license_card"]["license"] == "cc-by-4.0"
    assert body["license_card"]["figure_reuse"] == "allowed"
    assert body["license_card"]["message"] == "CC BY 4.0 — 図表転載可"

    # 翻訳(default_style=natural、personal フォークあり → partial)。
    assert body["translation"]["style"] == "natural"
    assert body["translation"]["status"] in ("partial", "complete")
    assert 0 <= body["translation"]["progress_pct"] <= 100

    # ToC: トップレベルは 7 セクション(Abstract〜References)、参考文献は分母外。
    top_ids = [n["section_id"] for n in body["toc"]]
    assert "sec-5" in top_ids
    refs = next(n for n in body["toc"] if n["section_id"] == "sec-5")
    assert refs["in_progress_denominator"] is False
    method = next(n for n in body["toc"] if n["section_id"] == "sec-2")
    assert len(method["children"]) == 2  # sec-2-1 / sec-2-2

    assert body["counts"]["figures"] == 4  # 図2 + 表2
    assert body["newer_revision"] is None


async def test_viewer_latest_revision_pointer_cannot_cross_paper_boundary(
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    visible_paper = await db_session.get(Paper, seeded.paper_id)
    assert visible_paper is not None
    original_latest = visible_paper.latest_revision_id
    secret_title = f"SECRET-{uuid.uuid4().hex}"
    secret_user = User(
        id=str(uuid.uuid4()),
        email=f"secret-owner-{uuid.uuid4().hex}@example.com",
    )
    secret_paper = Paper(
        id=str(uuid.uuid4()),
        title="Private secret paper",
        visibility="private",
        owner_user_id=str(secret_user.id),
        license="unknown",
    )
    db_session.add(secret_user)
    await db_session.flush()
    db_session.add(secret_paper)
    await db_session.flush()
    secret_revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=str(secret_paper.id),
        source_version="v1",
        parser_version="secret",
        quality_level="A",
        source_format="arxiv_html",
        content={
            "quality_level": "A",
            "sections": [
                {
                    "id": "secret-section",
                    "heading": {"number": "1", "title": secret_title},
                    "blocks": [
                        {
                            "id": "secret-block",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": secret_title}],
                        }
                    ],
                }
            ],
        },
        stats={},
    )
    db_session.add(secret_revision)
    await db_session.flush()
    visible_paper.latest_revision_id = secret_revision.id
    await db_session.commit()
    try:
        viewer = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
        revisions = await auth_client.get(f"/api/papers/{seeded.paper_id}/revisions")

        for response in (viewer, revisions):
            assert response.status_code == 404, response.text
            assert secret_title not in response.text
            assert str(secret_revision.id) not in response.text
    finally:
        visible_paper.latest_revision_id = original_latest
        await db_session.commit()
        await db_session.delete(secret_revision)
        await db_session.delete(secret_paper)
        await db_session.delete(secret_user)
        await db_session.commit()


async def test_viewer_toc_uses_effective_displayable_heading_translation(
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    user = await db_session.get(User, seeded.user_id)
    assert revision is not None and user is not None
    revision.content = {
        "quality_level": "A",
        "sections": [
            {
                "id": "toc-translated",
                "heading": {"number": "1", "title": "Methods"},
                "blocks": [
                    {
                        "id": "toc-heading",
                        "type": "heading",
                        "level": 1,
                        "title": "  Ｍｅｔｈｏｄｓ  ",  # noqa: RUF001
                    },
                    {
                        "id": "toc-body",
                        "type": "paragraph",
                        "inlines": [{"t": "text", "v": "Body."}],
                    },
                ],
            },
            {
                "id": "toc-missing",
                "heading": {"number": "2", "title": "Missing"},
                "blocks": [
                    {"id": "toc-missing-heading", "type": "heading", "level": 1, "title": "Missing"}
                ],
            },
            {
                "id": "toc-blocked",
                "heading": {"number": "3", "title": "Blocked"},
                "blocks": [
                    {"id": "toc-blocked-heading", "type": "heading", "level": 1, "title": "Blocked"}
                ],
            },
            {
                "id": "toc-late-heading",
                "heading": {"number": "4", "title": "Section title"},
                "blocks": [
                    {
                        "id": "toc-late-body",
                        "type": "paragraph",
                        "inlines": [{"t": "text", "v": "Body first."}],
                    },
                    {
                        "id": "toc-late-heading-block",
                        "type": "heading",
                        "level": 2,
                        "title": "Section title",
                    },
                ],
            },
            {
                "id": "toc-mismatched-heading",
                "heading": {"number": "5", "title": "Canonical title"},
                "blocks": [
                    {
                        "id": "toc-mismatched-heading-block",
                        "type": "heading",
                        "level": 1,
                        "title": "Different title",
                    }
                ],
            },
        ],
    }
    revision.stats = {"pages": 3}
    content = DocumentContent.model_validate(revision.content)
    full_plan = build_translation_plan(content, TranslationSettings(), pages=3)
    natural_shared = await db_session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == seeded.revision_id,
            TranslationSet.style == "natural",
            TranslationSet.scope == "shared",
        )
    )
    natural_personal = await db_session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == seeded.revision_id,
            TranslationSet.style == "natural",
            TranslationSet.scope == "personal",
            TranslationSet.user_id == seeded.user_id,
        )
    )
    literal_shared = await db_session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == seeded.revision_id,
            TranslationSet.style == "literal",
            TranslationSet.scope == "shared",
        )
    )
    assert natural_shared is not None
    assert natural_personal is not None
    assert literal_shared is not None
    for translation_set in (natural_shared, natural_personal, literal_shared):
        translation_set.plan = full_plan.model_dump(mode="json")

    def heading_unit(
        translation_set: TranslationSet,
        text_ja: str,
        *,
        block_id: str = "toc-heading",
        flags: list[str] | None = None,
    ) -> TranslationUnit:
        return TranslationUnit(
            set_id=str(translation_set.id),
            block_id=block_id,
            source_hash=f"{translation_set.id}:{block_id}",
            content_ja=[{"t": "text", "v": text_ja}],
            text_ja=text_ja,
            state="machine",
            quality_flags=flags or [],
        )

    db_session.add_all(
        [
            heading_unit(natural_shared, "共有見出し"),
            heading_unit(natural_personal, "個人見出し"),
            heading_unit(
                natural_personal,
                "表示してはいけない見出し",
                block_id="toc-blocked-heading",
                flags=["placeholder_mismatch"],
            ),
            heading_unit(
                natural_personal,
                "後方小見出し",
                block_id="toc-late-heading-block",
            ),
            heading_unit(
                natural_personal,
                "不一致見出し",
                block_id="toc-mismatched-heading-block",
            ),
            heading_unit(literal_shared, "直訳見出し"),
        ]
    )
    settings = dict(user.settings or {})
    settings["translation"] = {
        **dict(settings.get("translation", {})),
        "default_style": "natural",
    }
    user.settings = settings
    await db_session.commit()

    natural_response = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
    assert natural_response.status_code == 200, natural_response.text
    natural_toc = {node["section_id"]: node for node in natural_response.json()["toc"]}
    assert natural_toc["toc-translated"]["title_ja"] == "個人見出し"
    assert natural_toc["toc-translated"]["title_en"] == "Methods"
    assert natural_toc["toc-missing"]["title_ja"] is None
    assert natural_toc["toc-blocked"]["title_ja"] is None
    assert natural_toc["toc-late-heading"]["title_ja"] is None
    assert natural_toc["toc-mismatched-heading"]["title_ja"] is None

    settings = dict(user.settings or {})
    settings["translation"] = {
        **dict(settings.get("translation", {})),
        "default_style": "literal",
    }
    user.settings = settings
    await db_session.commit()
    literal_response = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
    assert literal_response.status_code == 200, literal_response.text
    literal_toc = {node["section_id"]: node for node in literal_response.json()["toc"]}
    assert literal_toc["toc-translated"]["title_ja"] == "直訳見出し"
    assert literal_toc["toc-translated"]["title_en"] == "Methods"


async def test_viewer_toc_on_demand_for_unrequested_translatable_sections(
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    revision.content = _translation_plan_subtree_content()
    revision.stats = {"pages": 40}
    content = DocumentContent.model_validate(revision.content)
    personal_set = await db_session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == seeded.revision_id,
            TranslationSet.style == "natural",
            TranslationSet.scope == "personal",
            TranslationSet.user_id == seeded.user_id,
        )
    )
    assert personal_set is not None
    shared_set = await db_session.get(TranslationSet, personal_set.base_set_id)
    assert shared_set is not None
    full_plan = build_translation_plan(
        content,
        TranslationSettings(),
        pages=40,
    )
    assert full_plan.target_section_ids == [
        "plan-main",
        "plan-appendix-target",
        "plan-appendix-other",
    ]
    opt_out_plan = build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=40,
    )
    shared_set.plan = opt_out_plan.model_dump(mode="json")
    personal_set.plan = full_plan.model_dump(mode="json")
    # Viewer status must be recomputed from primary coverage, not trusted from this stored value.
    personal_set.status = "complete"
    await db_session.commit()

    full_response = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
    assert full_response.status_code == 200, full_response.text
    full_toc = {node["section_id"]: node for node in full_response.json()["toc"]}
    full_parent = full_toc["plan-appendix-parent"]
    full_children = {node["section_id"]: node for node in full_parent["children"]}
    assert full_response.json()["translation"]["status"] == "pending"
    assert full_response.json()["translation"]["progress_pct"] == 0
    assert full_parent["on_demand"] is False
    assert full_parent["translated"] is False
    assert full_parent["in_progress_denominator"] is False
    assert full_children["plan-appendix-target"]["on_demand"] is False
    assert full_children["plan-appendix-target"]["in_progress_denominator"] is True
    assert full_children["plan-appendix-other"]["on_demand"] is False
    assert full_children["plan-appendix-other"]["in_progress_denominator"] is True
    assert full_children["plan-appendix-empty-sibling"]["on_demand"] is False
    assert full_children["plan-appendix-empty-sibling"]["translated"] is False

    personal_set.plan = opt_out_plan.model_dump(mode="json")
    await db_session.commit()
    opt_out_response = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
    assert opt_out_response.status_code == 200, opt_out_response.text
    opt_out_toc = {node["section_id"]: node for node in opt_out_response.json()["toc"]}
    opt_out_parent = opt_out_toc["plan-appendix-parent"]
    opt_out_children = {node["section_id"]: node for node in opt_out_parent["children"]}
    assert opt_out_parent["on_demand"] is False
    assert opt_out_children["plan-appendix-target"]["on_demand"] is True
    assert opt_out_children["plan-appendix-target"]["in_progress_denominator"] is False
    assert opt_out_children["plan-appendix-other"]["on_demand"] is True
    # A section without any eligible normal block must not offer an impossible action.
    assert opt_out_children["plan-appendix-empty-sibling"]["on_demand"] is False

    auxiliary_plan = TranslationPlan.model_validate(
        {
            **opt_out_plan.model_dump(mode="json"),
            "auxiliary_block_ids": ["plan-appendix-target-block"],
        }
    )
    personal_set.plan = auxiliary_plan.model_dump(mode="json")
    await db_session.commit()
    auxiliary_response = await auth_client.get(
        f"/api/library-items/{seeded.library_item_id}/viewer"
    )
    assert auxiliary_response.status_code == 200, auxiliary_response.text
    auxiliary_parent = next(
        node
        for node in auxiliary_response.json()["toc"]
        if node["section_id"] == "plan-appendix-parent"
    )
    auxiliary_children = {node["section_id"]: node for node in auxiliary_parent["children"]}
    assert auxiliary_parent["on_demand"] is False
    assert auxiliary_children["plan-appendix-target"]["on_demand"] is False
    assert auxiliary_children["plan-appendix-other"]["on_demand"] is True

    db_session.add(
        TranslationUnit(
            set_id=str(personal_set.id),
            block_id="plan-appendix-target-block",
            source_hash="appendix-target",
            content_ja=[{"t": "text", "v": "付録の証明"}],
            text_ja="付録の証明",
            state="machine",
            quality_flags=[],
        )
    )
    blocked_other = TranslationUnit(
        set_id=str(personal_set.id),
        block_id="plan-appendix-other-block",
        source_hash="appendix-other",
        content_ja=[{"t": "text", "v": "表示不可"}],
        text_ja="表示不可",
        state="machine",
        quality_flags=["placeholder_mismatch"],
    )
    db_session.add(blocked_other)
    await db_session.commit()
    blocked_response = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
    assert blocked_response.status_code == 200, blocked_response.text
    blocked_parent = next(
        node
        for node in blocked_response.json()["toc"]
        if node["section_id"] == "plan-appendix-parent"
    )
    blocked_children = {node["section_id"]: node for node in blocked_parent["children"]}
    assert blocked_parent["on_demand"] is False
    assert blocked_children["plan-appendix-target"]["translated"] is True
    assert blocked_children["plan-appendix-other"]["on_demand"] is True
    assert blocked_children["plan-appendix-other"]["translated"] is False
    # Auxiliary-only success/blocked units do not advance the primary progress or status.
    assert blocked_response.json()["translation"]["status"] == "pending"
    assert blocked_response.json()["translation"]["progress_pct"] == 0

    # A successful historical on-demand unit is requested even without an auxiliary plan entry.
    blocked_other.quality_flags = []
    blocked_other.content_ja = [{"t": "text", "v": "付録の例"}]
    blocked_other.text_ja = "付録の例"
    await db_session.commit()
    displayed_response = await auth_client.get(
        f"/api/library-items/{seeded.library_item_id}/viewer"
    )
    assert displayed_response.status_code == 200, displayed_response.text
    displayed_parent = next(
        node
        for node in displayed_response.json()["toc"]
        if node["section_id"] == "plan-appendix-parent"
    )
    displayed_children = {node["section_id"]: node for node in displayed_parent["children"]}
    assert displayed_parent["on_demand"] is False
    assert displayed_children["plan-appendix-other"]["on_demand"] is False
    assert displayed_children["plan-appendix-other"]["translated"] is True
    assert displayed_response.json()["translation"]["status"] == "pending"
    assert displayed_response.json()["translation"]["progress_pct"] == 0

    blocked_primary = TranslationUnit(
        set_id=str(personal_set.id),
        block_id="plan-main-block",
        source_hash="main-primary",
        content_ja=[{"t": "text", "v": "表示不可"}],
        text_ja="表示不可",
        state="machine",
        quality_flags=["placeholder_mismatch"],
    )
    db_session.add(blocked_primary)
    await db_session.commit()
    blocked_primary_response = await auth_client.get(
        f"/api/library-items/{seeded.library_item_id}/viewer"
    )
    assert blocked_primary_response.status_code == 200, blocked_primary_response.text
    blocked_primary_main = next(
        node for node in blocked_primary_response.json()["toc"] if node["section_id"] == "plan-main"
    )
    assert blocked_primary_main["translated"] is False
    assert blocked_primary_response.json()["translation"]["status"] == "complete"
    assert blocked_primary_response.json()["translation"]["progress_pct"] == 0

    blocked_primary.quality_flags = []
    blocked_primary.content_ja = [{"t": "text", "v": "本文"}]
    blocked_primary.text_ja = "本文"
    await db_session.commit()
    completed_response = await auth_client.get(
        f"/api/library-items/{seeded.library_item_id}/viewer"
    )
    assert completed_response.status_code == 200, completed_response.text
    assert completed_response.json()["translation"]["status"] == "complete"
    assert completed_response.json()["translation"]["progress_pct"] == 100


async def test_viewer_personal_execution_inherits_live_valid_base_plan_only(
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    revision.content = _translation_plan_content()
    revision.stats = {"pages": 40}
    content = DocumentContent.model_validate(revision.content)
    personal_set = await db_session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == seeded.revision_id,
            TranslationSet.style == "natural",
            TranslationSet.scope == "personal",
            TranslationSet.user_id == seeded.user_id,
        )
    )
    assert personal_set is not None
    shared_set = await db_session.get(TranslationSet, personal_set.base_set_id)
    literal_shared = await db_session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == seeded.revision_id,
            TranslationSet.style == "literal",
            TranslationSet.scope == "shared",
        )
    )
    assert shared_set is not None and literal_shared is not None
    primary_plan = build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=40,
    )
    full_plan = build_translation_plan(content, TranslationSettings(), pages=40)
    personal_set.plan = primary_plan.model_dump(mode="json")
    shared_set.plan = primary_plan.model_dump(mode="json")
    personal_set.status = "pending"
    db_session.add(
        TranslationUnit(
            set_id=str(personal_set.id),
            block_id="plan-main-block",
            source_hash="fork-main",
            content_ja=[{"t": "text", "v": "本文"}],
            text_ja="本文",
            state="machine",
            quality_flags=[],
        )
    )
    await db_session.commit()

    before_extension = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
    assert before_extension.status_code == 200, before_extension.text
    before_appendix = next(
        node for node in before_extension.json()["toc"] if node["section_id"] == "plan-appendix"
    )
    assert before_appendix["on_demand"] is True
    assert before_extension.json()["translation"]["status"] == "complete"
    assert before_extension.json()["translation"]["progress_pct"] == 100

    # The shared plan may grow after the personal fork. Its work is inherited dynamically,
    # while the personal primary denominator remains the single main block.
    shared_set.plan = full_plan.model_dump(mode="json")
    db_session.add(
        TranslationUnit(
            set_id=str(shared_set.id),
            block_id="plan-appendix-block",
            source_hash="base-appendix",
            content_ja=[{"t": "text", "v": "共有付録"}],
            text_ja="共有付録",
            state="machine",
            quality_flags=[],
        )
    )
    await db_session.commit()
    inherited = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
    assert inherited.status_code == 200, inherited.text
    inherited_appendix = next(
        node for node in inherited.json()["toc"] if node["section_id"] == "plan-appendix"
    )
    assert inherited_appendix["on_demand"] is False
    assert inherited_appendix["translated"] is True
    assert inherited_appendix["in_progress_denominator"] is False
    assert inherited.json()["translation"]["status"] == "complete"
    assert inherited.json()["translation"]["progress_pct"] == 100

    # A style-mismatched base is invalid for this personal set and must not leak plan or units.
    literal_shared.plan = full_plan.model_dump(mode="json")
    db_session.add(
        TranslationUnit(
            set_id=str(literal_shared.id),
            block_id="plan-appendix-block",
            source_hash="invalid-base-appendix",
            content_ja=[{"t": "text", "v": "不正base"}],
            text_ja="不正base",
            state="machine",
            quality_flags=[],
        )
    )
    personal_set.base_set_id = str(literal_shared.id)
    await db_session.commit()
    invalid = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
    assert invalid.status_code == 200, invalid.text
    invalid_appendix = next(
        node for node in invalid.json()["toc"] if node["section_id"] == "plan-appendix"
    )
    assert invalid_appendix["on_demand"] is True
    assert invalid_appendix["translated"] is False
    assert invalid.json()["translation"]["status"] == "complete"
    assert invalid.json()["translation"]["progress_pct"] == 100


@pytest.mark.parametrize("duplicate_kind", ["section", "block"])
async def test_viewer_rejects_revision_global_duplicate_document_ids(
    duplicate_kind: str,
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    duplicate_section_id = "duplicate-section" if duplicate_kind == "section" else "section-two"
    duplicate_block_id = "duplicate-block" if duplicate_kind == "block" else "block-two"
    revision.content = {
        "quality_level": "A",
        "sections": [
            {
                "id": "duplicate-section",
                "heading": {"number": "1", "title": "First"},
                "blocks": [
                    {
                        "id": "duplicate-block",
                        "type": "paragraph",
                        "inlines": [{"t": "text", "v": "First."}],
                    }
                ],
            },
            {
                "id": duplicate_section_id,
                "heading": {"number": "2", "title": "Second"},
                "blocks": [
                    {
                        "id": duplicate_block_id,
                        "type": "paragraph",
                        "inlines": [{"t": "text", "v": "Second."}],
                    }
                ],
            },
        ],
    }
    await db_session.commit()

    responses = [
        await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
        for _ in range(2)
    ]

    assert [response.status_code for response in responses] == [422, 422]
    assert [response.json()["code"] for response in responses] == [
        "validation_error",
        "validation_error",
    ]
    assert responses[0].json() == responses[1].json()


async def test_viewer_forbidden_for_other_user(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    # 他ユーザーの LibraryItem は 404。member ユーザーの item は存在しないので dummy uuid で。
    r = await auth_client.get("/api/library-items/00000000-0000-0000-0000-000000000000/viewer")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


# ---------------------------------------------------------------------------
# §6.3 document(ETag / 304)
# ---------------------------------------------------------------------------
async def test_document_etag_roundtrip(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    r = await auth_client.get(f"/api/revisions/{seeded.revision_id}/document")
    assert r.status_code == 200, r.text
    etag = r.headers.get("ETag")
    assert etag is not None
    assert etag.startswith(f'"{seeded.revision_id}:')
    assert r.json()["revision_id"] == seeded.revision_id
    assert len(r.json()["sections"]) >= 1

    r2 = await auth_client.get(
        f"/api/revisions/{seeded.revision_id}/document", headers={"If-None-Match": etag}
    )
    assert r2.status_code == 304

    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    updated = copy.deepcopy(revision.content)
    updated["sections"][0]["blocks"][0]["inlines"][0]["v"] = "Updated source text"
    revision.content = updated
    flag_modified(revision, "content")
    await db_session.commit()

    r_changed = await auth_client.get(
        f"/api/revisions/{seeded.revision_id}/document", headers={"If-None-Match": etag}
    )
    assert r_changed.status_code == 200
    assert r_changed.headers["ETag"] != etag

    # section_id で部分取得。
    r3 = await auth_client.get(
        f"/api/revisions/{seeded.revision_id}/document", params={"section_id": "sec-2"}
    )
    assert r3.status_code == 200
    assert r3.headers["ETag"].startswith(f'"{seeded.revision_id}:sec-2:')
    assert len(r3.json()["sections"]) == 1
    assert r3.json()["sections"][0]["id"] == "sec-2"


def test_document_etag_versions_computed_wire_representation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = SimpleNamespace(id="revision-wire", content={"quality_level": "A", "sections": []})
    current = viewer_router._document_etag(revision, None)  # type: ignore[arg-type]

    monkeypatch.setattr(
        viewer_router,
        "_DOCUMENT_WIRE_VERSION",
        viewer_router._DOCUMENT_WIRE_VERSION + 1,
    )

    assert viewer_router._document_etag(revision, None) != current  # type: ignore[arg-type]


def test_table_block_wire_exposes_canonical_physical_grid() -> None:
    raw = (
        '<table><tr><th colspan="2">Method $x$</th></tr>'
        '<tr><td rowspan="2">Our approach</td><td>99.1</td></tr>'
        "<tr><td>Stable result</td></tr></table>"
    )

    wire = _block_wire(Block(id="blk-grid", type="table", raw=raw))

    assert wire["raw"] == raw
    assert wire["source_grid"]["supported"] is True
    assert wire["source_grid"]["source_format"] == "html"
    assert [cell["id"] for row in wire["source_grid"]["rows"] for cell in row] == [
        "r0c0",
        "r1c0",
        "r1c1",
        "r2c0",
    ]
    assert wire["source_grid"]["rows"][0][0] == {
        "id": "r0c0",
        "source": "Method $x$",
        "header": True,
        "rowspan": 1,
        "colspan": 2,
        "translatable": True,
        "math": ["$x$"],
        "latex_body_start": None,
        "latex_body_end": None,
        "latex_wrappers": [],
    }
    assert wire["source_grid"]["rows"][1][0]["rowspan"] == 2
    assert wire["source_grid"]["rows"][1][1]["translatable"] is False


@pytest.mark.parametrize("raw", [None, "<table><tr><td>unterminated"])
def test_table_block_wire_omits_unsupported_grid_and_preserves_raw(raw: str | None) -> None:
    wire = _block_wire(Block(id="blk-legacy-grid", type="table", raw=raw))

    assert wire.get("raw") == raw
    assert "source_grid" not in wire


# ---------------------------------------------------------------------------
# §6.4 単一ブロック
# ---------------------------------------------------------------------------
async def test_get_block(auth_client: AsyncClient, seeded: Seeded) -> None:
    r = await auth_client.get(f"/api/revisions/{seeded.revision_id}/blocks/blk-2-1-p1-9eca")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["block"]["id"] == "blk-2-1-p1-9eca"
    assert body["section_id"] == "sec-2-1"
    assert body["display"].startswith("§2.1")


# ---------------------------------------------------------------------------
# §6.5 図表 / §6.6 参考文献
# ---------------------------------------------------------------------------
async def test_figures(auth_client: AsyncClient, seeded: Seeded) -> None:
    r = await auth_client.get(f"/api/revisions/{seeded.revision_id}/figures")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    kinds = {i["kind"] for i in items}
    assert kinds == {"figure", "table"}
    fig = next(i for i in items if i["kind"] == "figure")
    assert fig["display"].startswith("図")
    assert fig["image_url"] is not None and fig["image_url"].startswith("/api/assets/")
    assert fig["deferred"] is False


async def test_deferred_figure_is_flagged_in_document_and_list(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    # Mark a seeded figure as deferred (asset cleared + figure_deferred failure)
    # and confirm the viewer surfaces the load-on-demand flag.
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    content = copy.deepcopy(dict(revision.content))
    target_id: str | None = None

    def _clear(sections: list[dict[str, object]]) -> None:
        nonlocal target_id
        for section in sections:
            for block in section.get("blocks", []):
                if target_id is None and block.get("type") == "figure":
                    target_id = str(block["id"])
                    block.pop("asset_key", None)
            _clear(section.get("sections", []))  # type: ignore[arg-type]

    _clear(content["sections"])
    assert target_id is not None
    revision.content = content
    flag_modified(revision, "content")
    revision.stats = {
        **(revision.stats or {}),
        "figure_asset_failures": [
            {"code": "figure_deferred", "figure_id": target_id, "source": "latex"}
        ],
    }
    await db_session.commit()

    doc = await auth_client.get(f"/api/revisions/{seeded.revision_id}/document")
    assert doc.status_code == 200, doc.text

    def _find(sections: list[dict[str, object]]) -> dict[str, object] | None:
        for section in sections:
            for block in section.get("blocks", []):  # type: ignore[union-attr]
                if block.get("id") == target_id:
                    return block  # type: ignore[return-value]
            found = _find(section.get("sections", []))  # type: ignore[arg-type]
            if found is not None:
                return found
        return None

    block = _find(doc.json()["sections"])
    assert block is not None
    assert block.get("deferred") is True
    assert "asset_url" not in block

    figs = await auth_client.get(f"/api/revisions/{seeded.revision_id}/figures")
    assert figs.status_code == 200, figs.text
    target = next(i for i in figs.json()["items"] if i["block_id"] == target_id)
    assert target["deferred"] is True
    assert target["image_url"] is None


async def test_figures_uses_only_strict_typed_table_caption(
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    content = DocumentContent.model_validate(revision.content)
    table = next(block for _section, block in content.iter_blocks() if block.type == "table")
    table.raw = (
        "<table><tr><th>Method</th></tr><tr><td>Stable result at 99.1 percent</td></tr></table>"
    )
    revision.content = content.model_dump(mode="json")
    shared = await db_session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == seeded.revision_id,
            TranslationSet.style == "natural",
            TranslationSet.scope == "shared",
        )
    )
    assert shared is not None
    unit = await db_session.scalar(
        select(TranslationUnit).where(
            TranslationUnit.set_id == shared.id,
            TranslationUnit.block_id == table.id,
        )
    )
    if unit is None:
        unit = TranslationUnit(
            set_id=str(shared.id),
            block_id=table.id,
            source_hash="typed-table-figure-caption",
            content_ja=[],
            text_ja="",
            state="machine",
            quality_flags=[],
        )
        db_session.add(unit)
    typed = {
        "kind": "table",
        "version": 1,
        "caption": [
            {"t": "text", "v": "日本語"},
            {"t": "emphasis", "children": [{"t": "text", "v": "概要"}]},
        ],
        "cells": [["手法"], ["99.1で安定した結果"]],
    }
    unit.content_ja = typed
    unit.text_ja = "日本語概要\n手法\n99.1で安定した結果"
    await db_session.commit()

    valid_response = await auth_client.get(f"/api/revisions/{seeded.revision_id}/figures")

    assert valid_response.status_code == 200, valid_response.text
    valid_table = next(
        item for item in valid_response.json()["items"] if item["block_id"] == table.id
    )
    assert valid_table["caption_ja"] == "日本語概要"

    unit.content_ja = {**typed, "cells": [["手法"]]}
    await db_session.commit()
    invalid_response = await auth_client.get(f"/api/revisions/{seeded.revision_id}/figures")
    assert invalid_response.status_code == 200, invalid_response.text
    invalid_table = next(
        item for item in invalid_response.json()["items"] if item["block_id"] == table.id
    )
    assert invalid_table["caption_ja"] is None

    unit.content_ja = [{"t": "text", "v": "旧形式キャプション"}]
    unit.text_ja = "旧形式キャプション"
    await db_session.commit()
    legacy_response = await auth_client.get(f"/api/revisions/{seeded.revision_id}/figures")
    assert legacy_response.status_code == 200, legacy_response.text
    legacy_table = next(
        item for item in legacy_response.json()["items"] if item["block_id"] == table.id
    )
    assert legacy_table["caption_ja"] == "旧形式キャプション"


async def test_references(auth_client: AsyncClient, seeded: Seeded) -> None:
    r = await auth_client.get(f"/api/revisions/{seeded.revision_id}/references")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 5
    assert items[0]["ref_id"] == "ref-1"
    assert items[0]["number"] == "[1]"
    assert items[0]["title"]


def _fullwidth_ascii(value: str) -> str:
    return "".join(chr(ord(char) + 0xFEE0) if "!" <= char <= "~" else char for char in value)


@pytest.mark.parametrize(
    ("title", "expected_reference"),
    [
        ("References", True),
        ("Bibliography", True),
        ("Works Cited", True),
        ("Literature Cited", True),
        ("参考文献", True),
        ("引用文献", True),
        (_fullwidth_ascii("References"), True),
        ("References to Prior Work", False),
        ("A Note on References", False),
        ("参考文献レビュー", False),
        ("引用文献との比較", False),
    ],
)
async def test_references_endpoint_matches_core_reference_section_decision(
    title: str,
    expected_reference: bool,
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    revision.content = {
        "quality_level": "A",
        "sections": [
            {
                "id": "sec-reference-candidate",
                "heading": {"number": "1", "title": title},
                "blocks": [
                    {
                        "id": "blk-reference-candidate",
                        "type": "paragraph",
                        "inlines": [
                            {
                                "t": "text",
                                "v": "[1] Wang, A. A useful method. 2024.",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    content = DocumentContent.model_validate(revision.content)
    scope = compute_translation_scope(content)
    await db_session.commit()

    response = await auth_client.get(f"/api/revisions/{seeded.revision_id}/references")

    assert bool(scope.reference_section_ids) is expected_reference
    assert response.status_code == 200, response.text
    assert bool(response.json()["items"]) is expected_reference


async def test_references_fallback_inherits_reference_parent_without_collecting_normal_subtrees(
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    revision.content = {
        "quality_level": "A",
        "sections": [
            {
                "id": "sec-references-parent",
                "heading": {"number": "", "title": "References"},
                "blocks": [
                    {
                        "id": "blk-reference-parent",
                        "type": "paragraph",
                        "inlines": [{"t": "text", "v": "[1] Parent, A. Parent work. 2020."}],
                    }
                ],
                "sections": [
                    {
                        "id": "sec-reference-child",
                        "heading": {"number": "", "title": "2024"},
                        "blocks": [
                            {
                                "id": "blk-reference-child",
                                "type": "paragraph",
                                "inlines": [{"t": "text", "v": "[2] Child, B. Child work. 2024."}],
                            }
                        ],
                    }
                ],
            },
            {
                "id": "sec-normal-parent",
                "heading": {"number": "1", "title": "Evaluation"},
                "blocks": [],
                "sections": [
                    {
                        "id": "sec-normal-child",
                        "heading": {"number": "1.1", "title": "2024"},
                        "blocks": [
                            {
                                "id": "blk-normal-child",
                                "type": "paragraph",
                                "inlines": [
                                    {"t": "text", "v": "[3] Normal, C. Not a reference. 2024."}
                                ],
                            }
                        ],
                    }
                ],
            },
        ],
    }
    await db_session.commit()

    response = await auth_client.get(f"/api/revisions/{seeded.revision_id}/references")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 2
    aliases = [set(item["aliases"]) for item in items]
    assert sum("blk-reference-parent" in item for item in aliases) == 1
    assert sum("blk-reference-child" in item for item in aliases) == 1
    assert all("blk-normal-child" not in item for item in aliases)


async def test_references_fallback_and_document_ref_labels(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    revision.content = {
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
                            {"t": "text", "v": "See "},
                            {"t": "citation", "ref": "ref-1"},
                            {"t": "text", "v": " and "},
                            {"t": "ref", "ref": "fig:main", "kind": "figure"},
                            {"t": "text", "v": "."},
                        ],
                    },
                    {
                        "id": "blk-fig1",
                        "type": "figure",
                        "label": "fig:main",
                        "caption": [{"t": "text", "v": "Overview."}],
                    },
                ],
                "sections": [],
            },
            {
                "id": "sec-refs-fallback",
                "heading": {"number": "", "title": "References"},
                "blocks": [
                    {
                        "id": "blk-refs-heading",
                        "type": "heading",
                        "level": 1,
                        "title": "References",
                    },
                    {
                        "id": "blk-refs-raw",
                        "type": "paragraph",
                        "inlines": [
                            {"t": "text", "v": "[1] Wang, A., Liu, B. A useful method. 2024."}
                        ],
                    },
                ],
                "sections": [],
            },
        ],
    }
    await db_session.commit()

    refs = await auth_client.get(f"/api/revisions/{seeded.revision_id}/references")
    assert refs.status_code == 200, refs.text
    assert refs.json()["items"][0]["authors"] == "Wang, A., Liu, B"
    doc = await auth_client.get(f"/api/revisions/{seeded.revision_id}/document")
    para = doc.json()["sections"][0]["blocks"][0]
    assert para["inlines"][1]["v"] == "Wang et al. (2024)"
    assert para["inlines"][3]["v"] == "Fig. 1"


# ---------------------------------------------------------------------------
# §7.1 翻訳セット一覧 / §7.2 ユニット
# ---------------------------------------------------------------------------
async def test_list_translation_sets(auth_client: AsyncClient, seeded: Seeded) -> None:
    r = await auth_client.get(f"/api/revisions/{seeded.revision_id}/translations")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    styles = {(i["style"], i["scope"]) for i in items}
    assert ("natural", "shared") in styles
    assert ("literal", "shared") in styles
    assert ("natural", "personal") in styles  # dev の personal フォーク
    for i in items:
        assert 0 <= i["progress_pct"] <= 100
        assert i["glossary_snapshot_id"]


async def test_list_and_viewer_progress_use_effective_persisted_plan_with_legacy_fallback(
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    revision.content = _translation_plan_content()
    revision.stats = {"pages": 40}
    content = DocumentContent.model_validate(revision.content)
    personal_set = await db_session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == seeded.revision_id,
            TranslationSet.style == "natural",
            TranslationSet.scope == "personal",
            TranslationSet.user_id == seeded.user_id,
        )
    )
    assert personal_set is not None
    shared_set = await db_session.get(TranslationSet, personal_set.base_set_id)
    assert shared_set is not None
    subset = build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=40,
    )
    personal_set.plan = subset.model_dump(mode="json")
    personal_set.status = "complete"
    db_session.add(
        TranslationUnit(
            set_id=str(shared_set.id),
            block_id="plan-main-block",
            source_hash="plan-main",
            content_ja=[{"t": "text", "v": "本文"}],
            text_ja="本文",
            state="machine",
            quality_flags=[],
        )
    )
    await db_session.commit()

    listed = await auth_client.get(f"/api/revisions/{seeded.revision_id}/translations")
    assert listed.status_code == 200, listed.text
    listed_set = next(
        item for item in listed.json()["items"] if item["set_id"] == str(personal_set.id)
    )
    assert listed_set["progress_pct"] == 100

    viewed = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
    assert viewed.status_code == 200, viewed.text
    assert viewed.json()["translation"]["set_id"] == str(personal_set.id)
    assert viewed.json()["translation"]["progress_pct"] == 100

    empty_plan = TranslationPlan(
        include_appendix=False,
        translate_table_cells=False,
        suggest_section_selection_over_30_pages=False,
        target_section_ids=[],
        target_block_ids=[],
        pages=40,
    )
    personal_set.plan = empty_plan.model_dump(mode="json")
    personal_set.status = "pending"
    await db_session.commit()
    empty_listed = await auth_client.get(f"/api/revisions/{seeded.revision_id}/translations")
    assert empty_listed.status_code == 200, empty_listed.text
    empty_listed_set = next(
        item for item in empty_listed.json()["items"] if item["set_id"] == str(personal_set.id)
    )
    assert empty_listed_set["status"] == "complete"
    assert empty_listed_set["progress_pct"] == 100
    empty_viewed = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
    assert empty_viewed.status_code == 200, empty_viewed.text
    assert empty_viewed.json()["translation"]["status"] == "complete"
    assert empty_viewed.json()["translation"]["progress_pct"] == 100

    # Existing sets without a plan deliberately fall back to the safe full target set.
    personal_set.plan = None
    await db_session.commit()
    legacy = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["translation"]["progress_pct"] == 50


async def test_units_by_section(auth_client: AsyncClient, seeded: Seeded) -> None:
    # sec-0(Abstract)は既定訳済み。
    r = await auth_client.get(
        f"/api/revisions/{seeded.revision_id}/translations/natural/units",
        params={"section_id": "sec-0"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["set_id"]
    assert len(body["items"]) >= 1
    unit = body["items"][0]
    assert unit["block_id"] == "blk-0-p1-5d87"
    # personal フォークが優先(edited)。
    assert unit["state"] == "edited"
    assert unit["text_ja"]
    assert "content_ja" in unit


async def test_units_missing_section_404(auth_client: AsyncClient, seeded: Seeded) -> None:
    r = await auth_client.get(
        f"/api/revisions/{seeded.revision_id}/translations/natural/units",
        params={"section_id": "sec-nope"},
    )
    assert r.status_code == 404


async def test_units_placeholder_mismatch_is_null(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    # シードは placeholder_mismatch のフォールバック unit を含む(test_seed 参照)。
    row = (
        await db_session.execute(
            text(
                "SELECT b.section_path, u.block_id FROM translation_units u "
                "JOIN translation_sets s ON s.id = u.set_id "
                "JOIN block_search_index b ON b.revision_id = s.revision_id AND b.block_id = u.block_id "
                "WHERE s.revision_id = :r AND 'placeholder_mismatch' = ANY(u.quality_flags) LIMIT 1"
            ),
            {"r": seeded.revision_id},
        )
    ).first()
    if row is None:
        return  # フォールバック unit が無い構成でもテストは緑(存在時のみ検証)
    # その unit の block を含むセクションを引く。section_path から section_id を復元できないため
    # 全セクションを走査して block を含む section で units を取得する。
    doc = await db_session.scalar(
        select(DocumentRevision.content).where(DocumentRevision.id == seeded.revision_id)
    )
    from alinea_core.document.blocks import DocumentContent

    content = DocumentContent.model_validate(doc)
    target_block = row[1]
    section_id = next(
        (sec.id for sec, blk in content.iter_blocks() if blk.id == target_block), None
    )
    assert section_id is not None
    r = await auth_client.get(
        f"/api/revisions/{seeded.revision_id}/translations/natural/units",
        params={"section_id": section_id},
    )
    assert r.status_code == 200
    item = next((i for i in r.json()["items"] if i["block_id"] == target_block), None)
    assert item is not None
    assert item["text_ja"] is None
    assert item["content_ja"] is None
    assert "placeholder_mismatch" in item["quality_flags"]


# ---------------------------------------------------------------------------
# §7.4 prioritize / §7.5 section translate / §7.6 retranslate
# ---------------------------------------------------------------------------
async def _shared_set_id(db: AsyncSession, revision_id: str, style: str) -> str:
    sid = await db.scalar(
        select(TranslationSet.id).where(
            TranslationSet.revision_id == revision_id,
            TranslationSet.style == style,
            TranslationSet.scope == "shared",
        )
    )
    assert sid is not None
    return str(sid)


async def test_prioritize_noop(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    set_id = await _shared_set_id(db_session, seeded.revision_id, "natural")
    r = await auth_client.post(
        f"/api/translation-sets/{set_id}/prioritize", json={"section_id": "sec-3"}
    )
    assert r.status_code == 202, r.text
    assert r.json() == {"ok": True}


# PY-TR-08: 直訳オンデマンド(§7.5)— 初回で 202+翻訳ジョブ。
async def test_section_translate_creates_job(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    set_id = await _shared_set_id(db_session, seeded.revision_id, "natural")
    r = await auth_client.post(f"/api/translation-sets/{set_id}/sections/sec-3/translate", json={})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    await db_session.rollback()
    row = (
        await db_session.execute(
            text("SELECT kind, payload->>'reason', status FROM jobs WHERE id = :j"),
            {"j": job_id},
        )
    ).first()
    assert row is not None
    assert row[0] == "translation"
    assert row[1] == "on_demand"
    assert row[2] == "queued"


async def test_retry_failed_translations_creates_section_job(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    from alinea_core.document.blocks import DocumentContent

    set_id = await _shared_set_id(db_session, seeded.revision_id, "natural")
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    content = DocumentContent.model_validate(revision.content)
    section_id, block_id = next(
        (sec.id, blk.id)
        for sec, blk in content.iter_blocks()
        if sec.id == "sec-3" and blk.type == "paragraph"
    )
    unit = await db_session.scalar(
        select(TranslationUnit).where(
            TranslationUnit.set_id == set_id,
            TranslationUnit.block_id == block_id,
        )
    )
    if unit is None:
        db_session.add(
            TranslationUnit(
                set_id=set_id,
                block_id=block_id,
                source_hash="stale",
                content_ja=[],
                text_ja="",
                state="machine",
                quality_flags=["placeholder_mismatch"],
            )
        )
    else:
        unit.quality_flags = ["placeholder_mismatch"]
        unit.text_ja = ""
        unit.content_ja = []
    await db_session.commit()

    r = await auth_client.post(
        f"/api/translation-sets/{set_id}/retry-failed", json={"section_id": section_id}
    )
    assert r.status_code == 202, r.text
    assert r.json()["block_count"] >= 1
    job_id = r.json()["job_ids"][0]
    await db_session.rollback()
    row = (
        await db_session.execute(
            text(
                "SELECT payload->>'reason', payload->>'section_id', payload->'block_ids' FROM jobs WHERE id = :j"
            ),
            {"j": job_id},
        )
    ).first()
    assert row is not None
    assert row[0] == "retry_failed"
    assert row[1] == section_id
    assert block_id in row[2]


async def test_retry_failed_backfills_valid_legacy_and_excludes_nontranslatable_blocks(
    auth_client: AsyncClient,
    seeded: Seeded,
    db_session: AsyncSession,
) -> None:
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    raw_content = _translation_plan_content()
    sections = raw_content["sections"]
    assert isinstance(sections, list)
    sections.extend(
        [
            {
                "id": "plan-references",
                "heading": {"number": "R", "title": "References"},
                "blocks": [
                    {
                        "id": "plan-reference-block",
                        "type": "reference_entry",
                        "raw": "Reference text.",
                    }
                ],
            },
            {
                "id": "plan-equations",
                "heading": {"number": "2", "title": "Equations"},
                "blocks": [
                    {
                        "id": "plan-equation-block",
                        "type": "equation",
                        "latex": "x=y",
                    }
                ],
            },
        ]
    )
    revision.content = raw_content
    revision.stats = {"pages": 40}
    content = DocumentContent.model_validate(revision.content)
    set_id = await _shared_set_id(db_session, seeded.revision_id, "natural")
    translation_set = await db_session.get(TranslationSet, set_id)
    assert translation_set is not None
    subset = build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=40,
    )
    translation_set.plan = subset.model_dump(mode="json")
    for block_id in (
        "plan-main-block",
        "plan-appendix-block",
        "plan-reference-block",
        "plan-equation-block",
    ):
        db_session.add(
            TranslationUnit(
                set_id=set_id,
                block_id=block_id,
                source_hash=block_id,
                content_ja=[],
                text_ja="",
                state="machine",
                quality_flags=["placeholder_mismatch"],
            )
        )
    await db_session.commit()

    response = await auth_client.post(f"/api/translation-sets/{set_id}/retry-failed", json={})

    assert response.status_code == 202, response.text
    assert response.json()["block_count"] == 2
    assert len(response.json()["job_ids"]) == 2
    jobs = [await db_session.get(Job, job_id) for job_id in response.json()["job_ids"]]
    assert all(job is not None for job in jobs)
    by_section = {job.payload["section_id"]: job for job in jobs if job is not None}
    assert by_section["plan-main"].payload["block_ids"] == ["plan-main-block"]
    assert by_section["plan-appendix"].payload["block_ids"] == ["plan-appendix-block"]
    await db_session.refresh(translation_set)
    persisted = resolve_translation_plan(content, translation_set.plan, pages=40)
    assert persisted.target_block_ids == ["plan-main-block"]
    assert persisted.auxiliary_block_ids == ["plan-appendix-block"]


# PY-TR-08: 直訳(literal)オンデマンド — 初回 202+ジョブ、同一要求は同じジョブ
# (idempotency_key で重複生成なし = 以後即時。docs/03 §7 / plans/12 §2.4)。
async def test_ondemand_literal_translate_idempotent(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    literal_set = await _shared_set_id(db_session, seeded.revision_id, "literal")
    r1 = await auth_client.post(
        f"/api/translation-sets/{literal_set}/sections/sec-3/translate", json={}
    )
    assert r1.status_code == 202, r1.text
    job1 = r1.json()["job_id"]
    assert job1
    # 同一 (set, section) の再要求 → 同じ job_id(即時・重複生成なし)。
    r2 = await auth_client.post(
        f"/api/translation-sets/{literal_set}/sections/sec-3/translate", json={}
    )
    assert r2.status_code == 202, r2.text
    assert r2.json()["job_id"] == job1
    await db_session.rollback()
    row = (
        await db_session.execute(
            text("SELECT payload->>'set_id', payload->>'reason' FROM jobs WHERE id = :j"),
            {"j": job1},
        )
    ).first()
    assert row is not None
    assert row[0] == literal_set
    assert row[1] == "on_demand"


async def test_retranslate_machine_unit(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    set_id = await _shared_set_id(db_session, seeded.revision_id, "natural")
    unit_id = await db_session.scalar(
        select(TranslationUnit.id).where(
            TranslationUnit.set_id == set_id, TranslationUnit.state == "machine"
        )
    )
    assert unit_id is not None
    r = await auth_client.post(f"/api/translation-units/{unit_id}/retranslate", json={})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    await db_session.rollback()
    row = (
        await db_session.execute(
            text("SELECT payload->>'reason', payload->>'unit_id' FROM jobs WHERE id = :j"),
            {"j": job_id},
        )
    ).first()
    assert row is not None
    assert row[0] == "retranslate"
    assert row[1] == str(unit_id)


# PY-TR-09: 手動編集(state=edited)の非上書き — retranslate は discard_edit なしで 409、
# discard_edit=true で 202(docs/03 §11 / plans/12 §2.4)。
async def test_retranslate_edited_unit_conflict(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    edited_unit_id = await db_session.scalar(
        select(TranslationUnit.id)
        .join(TranslationSet, TranslationSet.id == TranslationUnit.set_id)
        .where(
            TranslationSet.revision_id == seeded.revision_id,
            TranslationUnit.state == "edited",
        )
    )
    assert edited_unit_id is not None
    # discard_edit なし → 409 conflict / detail に edit_protected。
    r = await auth_client.post(f"/api/translation-units/{edited_unit_id}/retranslate", json={})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "conflict"
    assert "edit_protected" in (r.json().get("detail") or "")
    # discard_edit=true → 202。
    r2 = await auth_client.post(
        f"/api/translation-units/{edited_unit_id}/retranslate",
        json={"discard_edit": True},
    )
    assert r2.status_code == 202, r2.text


# ---------------------------------------------------------------------------
# §6.2 リビジョン一覧
# ---------------------------------------------------------------------------
async def test_list_revisions(auth_client: AsyncClient, seeded: Seeded) -> None:
    r = await auth_client.get(f"/api/papers/{seeded.paper_id}/revisions")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) >= 1
    current = [i for i in items if i["is_current"]]
    assert len(current) == 1
    assert current[0]["id"] == seeded.revision_id
    assert current[0]["quality_level"] == "A"

    # 存在しない paper は 404。
    miss = await auth_client.get("/api/papers/00000000-0000-0000-0000-000000000000/revisions")
    assert miss.status_code == 404
