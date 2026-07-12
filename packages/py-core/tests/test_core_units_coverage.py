"""py-core 補助ロジックの単体テスト(ids / 進捗計算 / スコープ / 完了検知の分岐)。

plans/05 §2.2・§11.3 / plans/02 §1.1 の決定的関数を検証する。DB を使う分は実 PostgreSQL。
"""

from __future__ import annotations

import uuid

from alinea_core.db.ids import new_ulid, new_uuid
from alinea_core.db.models import (
    DocumentRevision,
    Job,
    Paper,
    TranslationSet,
    TranslationUnit,
    User,
)
from alinea_core.document.blocks import DocumentContent
from alinea_core.ingest.progress import (
    body_progress,
    count_active_body_jobs,
    finalize_ingest_if_body_complete,
    first_translatable_section,
    readable_upto,
    stage_index,
)
from alinea_core.translation.pipeline import (
    TranslationSettings,
    build_translation_plan,
    compute_progress,
    compute_translation_scope,
    resolve_translation,
)
from sqlalchemy.ext.asyncio import AsyncSession

_CONTENT = DocumentContent.model_validate(
    {
        "quality_level": "A",
        "sections": [
            {
                "id": "s1",
                "heading": {"number": "1", "title": "Intro"},
                "blocks": [
                    {"id": "b1", "type": "paragraph", "inlines": [{"t": "text", "v": "a"}]},
                    {"id": "b2", "type": "paragraph", "inlines": [{"t": "text", "v": "b"}]},
                ],
            },
            {
                "id": "s2",
                "heading": {"number": "2", "title": "Method"},
                "blocks": [{"id": "b3", "type": "paragraph", "inlines": [{"t": "text", "v": "c"}]}],
            },
            {
                "id": "s3",
                "heading": {"number": "", "title": "References"},
                "blocks": [{"id": "r1", "type": "reference_entry", "raw": "[1] X. et al."}],
            },
        ],
    }
)


# ---------------------------------------------------------------------------
# db/ids
# ---------------------------------------------------------------------------
def test_id_generators() -> None:
    u = new_uuid()
    assert uuid.UUID(u)  # 妥当な UUID 文字列
    a, b = new_ulid(), new_ulid()
    assert len(a) == 26 and len(b) == 26  # ULID は 26 文字
    assert a != b


# ---------------------------------------------------------------------------
# 進捗・スコープ(純関数)
# ---------------------------------------------------------------------------
def test_body_progress_and_stage_index() -> None:
    assert body_progress(3, 10) == 30
    assert body_progress(1, 0) == 100  # 分母 0 → 100
    assert body_progress(15, 10) == 100  # 上限クランプ
    assert stage_index("fetching") == 1
    assert stage_index("translating_body") == 6
    assert stage_index("bogus") == -1


def test_translation_scope_and_readable_upto() -> None:
    scope = compute_translation_scope(_CONTENT)
    assert scope.in_scope_block_ids == ["b1", "b2", "b3"]  # reference は対象外
    assert scope.reference_section_ids == ["s3"]
    assert first_translatable_section(_CONTENT) == "s1"

    # 先頭から連続で全訳済みのセクションまでを §n で返す。
    assert readable_upto(_CONTENT, {"b1", "b2"}) == "§1"
    assert readable_upto(_CONTENT, {"b1", "b2", "b3"}) == "§2"
    assert readable_upto(_CONTENT, {"b1"}) is None  # s1 未完 → None
    assert readable_upto(_CONTENT, set()) is None


def test_first_translatable_section_none_when_empty() -> None:
    empty = DocumentContent.model_validate({"quality_level": "A", "sections": []})
    assert first_translatable_section(empty) is None


def test_compute_progress_and_resolve_translation() -> None:
    units = [{"quality_flags": []}, {"quality_flags": ["placeholder_mismatch"]}]
    assert compute_progress(units, 2) == 50  # 1/2(ブロッキング分は分子から除外)
    assert compute_progress([], 0) == 100  # 分母 0 → 100

    personal = {"b1": "P訳"}
    base = {"b1": "共有訳", "b2": "共有訳2"}
    assert resolve_translation(personal, base, "b1") == "P訳"  # personal 優先
    assert resolve_translation(personal, base, "b2") == "共有訳2"  # base フォールバック
    assert resolve_translation(None, None, "bX") is None


# ---------------------------------------------------------------------------
# 完了検知(DB・読み取りのみ)
# ---------------------------------------------------------------------------
async def test_count_active_body_jobs(db_session: AsyncSession) -> None:
    paper = Paper(title="P", visibility="public", license="cc-by-4.0")
    db_session.add(paper)
    await db_session.flush()
    rev = DocumentRevision(
        paper_id=str(paper.id),
        source_version="v1",
        parser_version="t",
        quality_level="A",
        source_format="arxiv_html",
        content={"quality_level": "A", "sections": []},
        stats={},
    )
    db_session.add(rev)
    await db_session.flush()
    tset = TranslationSet(revision_id=str(rev.id), style="natural", scope="shared")
    db_session.add(tset)
    await db_session.flush()
    set_id = str(tset.id)

    # 初回全文翻訳(reason=initial)を queued 1 + succeeded 1 で作る → active は 1。
    db_session.add_all(
        [
            Job(
                kind="translation",
                status="queued",
                payload={
                    "set_id": set_id,
                    "reason": "initial",
                    "ingest_job_id": "old-ingest",
                },
            ),
            Job(
                kind="translation",
                status="succeeded",
                payload={"set_id": set_id, "reason": "initial"},
            ),
            # on_demand は分母外。
            Job(
                kind="translation",
                status="queued",
                payload={"set_id": set_id, "reason": "on_demand"},
            ),
        ]
    )
    await db_session.flush()
    assert await count_active_body_jobs(db_session, set_id) == 1
    assert (
        await count_active_body_jobs(
            db_session,
            set_id,
            ingest_job_id="old-ingest",
        )
        == 1
    )
    assert (
        await count_active_body_jobs(
            db_session,
            set_id,
            ingest_job_id="new-ingest",
        )
        == 0
    )


async def test_finalize_uses_stored_targets_and_does_not_complete_missing_full_scope(
    db_session: AsyncSession,
) -> None:
    content = DocumentContent.model_validate(
        {
            "quality_level": "A",
            "sections": [
                {
                    "id": "main",
                    "heading": {"number": "1", "title": "Main"},
                    "blocks": [
                        {
                            "id": "main-block",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": "Main text."}],
                        }
                    ],
                },
                {
                    "id": "appendix",
                    "heading": {"number": "A", "title": "Details"},
                    "blocks": [
                        {
                            "id": "appendix-block",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": "Appendix text."}],
                        }
                    ],
                },
            ],
        }
    )
    paper = Paper(title="Plan coverage", visibility="public", license="cc-by-4.0")
    db_session.add(paper)
    await db_session.flush()
    revision = DocumentRevision(
        paper_id=str(paper.id),
        source_version="v1",
        parser_version="test",
        quality_level="A",
        source_format="arxiv_html",
        content=content.model_dump(mode="json"),
        stats={"pages": 20},
    )
    db_session.add(revision)
    await db_session.flush()
    full_plan = build_translation_plan(content, TranslationSettings(), pages=20)
    translation_set = TranslationSet(
        revision_id=str(revision.id),
        style="natural",
        scope="shared",
        plan=full_plan.model_dump(mode="json"),
        status="partial",
    )
    ingest_job = Job(
        kind="ingest",
        status="running",
        stage="translating_body",
        progress=55,
        payload={},
    )
    db_session.add_all([translation_set, ingest_job])
    await db_session.flush()
    db_session.add(
        TranslationUnit(
            set_id=str(translation_set.id),
            block_id="main-block",
            source_hash="main",
            content_ja=[{"t": "text", "v": "本文"}],
            text_ja="本文",
            quality_flags=[],
        )
    )
    await db_session.commit()

    completed = await finalize_ingest_if_body_complete(
        db_session,
        set_id=str(translation_set.id),
        ingest_job_id=str(ingest_job.id),
        content=content,
        style="natural",
        source_version="v1",
        appendix_untranslated=False,
    )

    assert completed is False
    await db_session.refresh(translation_set)
    await db_session.refresh(ingest_job)
    assert translation_set.status == "partial"
    assert ingest_job.status == "running"

    subset_plan = build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=20,
    )
    translation_set.plan = subset_plan.model_dump(mode="json")
    await db_session.commit()
    completed_subset = await finalize_ingest_if_body_complete(
        db_session,
        set_id=str(translation_set.id),
        ingest_job_id=str(ingest_job.id),
        content=content,
        style="natural",
        source_version="v1",
        appendix_untranslated=True,
    )
    assert completed_subset is True


async def test_finalize_personal_set_uses_exact_base_overlay(
    db_session: AsyncSession,
) -> None:
    content = DocumentContent.model_validate(
        {
            "quality_level": "A",
            "sections": [
                {
                    "id": "main",
                    "heading": {"number": "1", "title": "Main"},
                    "blocks": [
                        {
                            "id": "main-block",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": "Main text."}],
                        }
                    ],
                }
            ],
        }
    )
    user = User(email=f"overlay-finalize-{uuid.uuid4().hex}@example.com")
    paper = Paper(title="Overlay finalization", visibility="public", license="cc-by-4.0")
    db_session.add_all([user, paper])
    await db_session.flush()
    revision = DocumentRevision(
        paper_id=str(paper.id),
        source_version="v1",
        parser_version="test",
        quality_level="A",
        source_format="arxiv_html",
        content=content.model_dump(mode="json"),
        stats={"pages": 1},
    )
    db_session.add(revision)
    await db_session.flush()
    plan = build_translation_plan(content, TranslationSettings(), pages=1).model_dump(mode="json")
    shared = TranslationSet(
        revision_id=str(revision.id),
        style="natural",
        scope="shared",
        plan=plan,
        status="complete",
    )
    db_session.add(shared)
    await db_session.flush()
    personal = TranslationSet(
        revision_id=str(revision.id),
        style="natural",
        scope="personal",
        user_id=str(user.id),
        base_set_id=str(shared.id),
        plan=plan,
        status="partial",
    )
    ingest_job = Job(
        kind="ingest",
        status="running",
        stage="translating_body",
        progress=55,
        payload={},
    )
    db_session.add_all([personal, ingest_job])
    await db_session.flush()
    db_session.add(
        TranslationUnit(
            set_id=str(shared.id),
            block_id="main-block",
            source_hash="main",
            content_ja=[{"t": "text", "v": "本文"}],
            text_ja="本文",
            quality_flags=[],
        )
    )
    await db_session.commit()

    completed = await finalize_ingest_if_body_complete(
        db_session,
        set_id=str(personal.id),
        ingest_job_id=str(ingest_job.id),
        content=content,
        style="natural",
        source_version="v1",
        appendix_untranslated=False,
    )

    assert completed is True
    await db_session.refresh(personal)
    await db_session.refresh(ingest_job)
    assert personal.status == "complete"
    assert ingest_job.status == "succeeded"


async def test_finalize_empty_target_completes_without_translation_units(
    db_session: AsyncSession,
) -> None:
    content = DocumentContent.model_validate({"quality_level": "A", "sections": []})
    paper = Paper(title="Empty target", visibility="public", license="cc-by-4.0")
    db_session.add(paper)
    await db_session.flush()
    revision = DocumentRevision(
        paper_id=str(paper.id),
        source_version="v1",
        parser_version="test",
        quality_level="A",
        source_format="arxiv_html",
        content=content.model_dump(mode="json"),
        stats={"pages": 1},
    )
    db_session.add(revision)
    await db_session.flush()
    plan = build_translation_plan(content, TranslationSettings(), pages=1)
    translation_set = TranslationSet(
        revision_id=str(revision.id),
        style="natural",
        scope="shared",
        plan=plan.model_dump(mode="json"),
        status="pending",
    )
    ingest_job = Job(
        kind="ingest",
        status="running",
        stage="translating_body",
        progress=55,
        payload={},
    )
    db_session.add_all([translation_set, ingest_job])
    await db_session.commit()

    completed = await finalize_ingest_if_body_complete(
        db_session,
        set_id=str(translation_set.id),
        ingest_job_id=str(ingest_job.id),
        content=content,
        style="natural",
        source_version="v1",
        appendix_untranslated=False,
    )

    assert completed is True
    await db_session.refresh(translation_set)
    await db_session.refresh(ingest_job)
    assert translation_set.status == "complete"
    assert ingest_job.status == "succeeded"
    assert ingest_job.progress == 100
