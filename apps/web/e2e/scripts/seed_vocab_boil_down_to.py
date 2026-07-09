#!/usr/bin/env python3
"""VR-4d(語彙帳・詳細パネル)専用の E2E シード補助。

apps/web/e2e/vr/vr-m2.spec.ts からのみ ``uv run --no-sync python
apps/web/e2e/scripts/seed_vocab_boil_down_to.py <user_email>`` として呼ばれる。

§14 シード(``seed_data/rectified_flow/vocab.json`` の "boil down to" エントリ)は
``alinea_api.seed`` から実際には投入されない(article.json/overview_dsl.json と同様の
死んだ fixture。followups 参照)。VR-4d の基準画像はこの語彙(生成完了・mastered)の
詳細パネル(6 セクション表示)を撮るため、本スクリプトが同一の値で直接 INSERT する
(vocab.json の値をそのまま転記。E2E 側でのフィクショナルな追加内容は入れない)。
標準出力へ 1 行の JSON({"vocab_id": ...})を書く。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
import uuid

from alinea_api.services.deadlines import today_jst
from alinea_core.db.models import LibraryItem, Paper, User, VocabEntry
from alinea_core.db.session import get_sessionmaker
from sqlalchemy import select

TERM = "boil down to"


async def _main(user_email: str) -> None:
    maker = get_sessionmaker()
    async with maker() as db:
        user = (await db.execute(select(User).where(User.email == user_email))).scalars().first()
        if user is None:
            raise SystemExit(f"user not found: {user_email}")

        paper_res = await db.execute(select(Paper).where(Paper.arxiv_id == "2209.03003"))
        paper = paper_res.scalars().first()
        if paper is None:
            raise SystemExit(
                "rectified-flow seed paper not found (run seed --sample rectified-flow first)"
            )

        item = (
            (
                await db.execute(
                    select(LibraryItem).where(
                        LibraryItem.user_id == user.id, LibraryItem.paper_id == paper.id
                    )
                )
            )
            .scalars()
            .first()
        )
        if item is None:
            raise SystemExit("rectified-flow library item not found for user")

        entry = VocabEntry(
            id=str(uuid.uuid4()),
            user_id=str(user.id),
            library_item_id=str(item.id),
            kind="idiom",
            term=TERM,
            pos_label="phr.",
            context_anchor={
                "revision_id": str(paper.latest_revision_id) if paper.latest_revision_id else "",
                "block_id": "blk-1-p2-0b4e",
                "side": "source",
            },
            context_sentence="The core training loop reduces to a single call",
            context_hl_start=0,
            context_hl_end=10,
            meaning_short="結局〜に帰着する",
            meaning_long="複雑に見えるものが本質的に単純な事柄に要約されること。",
            generation_status="complete",
            srs_stage=5,
            srs_next_review_on=today_jst() + dt.timedelta(days=7),
            srs_review_count=9,
            srs_mastered=True,
        )
        db.add(entry)
        await db.commit()
        print(json.dumps({"vocab_id": str(entry.id), "term": TERM}))


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1]))
