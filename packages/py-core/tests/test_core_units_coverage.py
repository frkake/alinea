"""py-core 補助ロジックの単体テスト(ids / 進捗計算 / スコープ / 完了検知の分岐)。

plans/05 §2.2・§11.3 / plans/02 §1.1 の決定的関数を検証する。DB を使う分は実 PostgreSQL。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.ids import new_ulid, new_uuid
from yakudoku_core.db.models import DocumentRevision, Job, Paper, TranslationSet
from yakudoku_core.document.blocks import DocumentContent
from yakudoku_core.ingest.progress import (
    body_progress,
    count_active_body_jobs,
    first_translatable_section,
    readable_upto,
    stage_index,
)
from yakudoku_core.translation.pipeline import (
    compute_progress,
    compute_translation_scope,
    resolve_translation,
)

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
                payload={"set_id": set_id, "reason": "initial"},
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
