#!/usr/bin/env python3
"""PW-20(語彙帳・SRS 復習)専用の E2E シード補助。

apps/web/e2e/specs/pw-20-vocabulary.spec.ts からのみ ``uv run --no-sync python
apps/web/e2e/scripts/seed_due_vocab.py <user_email>`` として呼ばれる。

§14 シード(vocab.json)の "trajectory" / "coupling" は当初 srs_next_review_on=今日だが、
このテストや他 spec が「復習をはじめる」を一度実行すると SRS が進み next_review が未来日へ
ずれる。2 連続実行のいずれでも「復習をはじめる」ボタンが必ず活性(due ≥ 1)であることを
保証するため、生成完了済み・today 期限の VocabEntry を実行ごとに一意な見出し語で直接
INSERT する(§14 の共有シードは変更しない。書き込み系テストは自分が作ったデータのみを
残す運用規則には反しない — このエントリはテスト末尾で `DELETE /api/vocab/{id}` される)。
標準出力へ 1 行の JSON({"vocab_id": ..., "term": ...})を書く。
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

from alinea_api.services.deadlines import today_jst
from alinea_core.db.models import LibraryItem, Paper, User, VocabEntry
from alinea_core.db.session import get_sessionmaker
from sqlalchemy import select


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

        term = f"e2e due term {uuid.uuid4().hex[:8]}"
        entry = VocabEntry(
            id=str(uuid.uuid4()),
            user_id=str(user.id),
            library_item_id=str(item.id),
            kind="word",
            term=term,
            pos_label="n.",
            ipa="/i: tuː dʒiː/",  # noqa: RUF001 (E2E ダミー IPA)
            context_anchor={
                "revision_id": str(paper.latest_revision_id) if paper.latest_revision_id else "",
                "block_id": "blk-1-p1-c4d1",
                "side": "source",
            },
            context_sentence=f"This is the {term} shown in a fixed context sentence.",
            context_hl_start=12,
            context_hl_end=12 + len(term),
            meaning_short="E2E 用ダミー語義",
            meaning_long="PW-20 の復習セッション検証専用に作成された語彙(due today)。",
            interpretation="この文脈での解釈メモ(E2E ダミー)。",
            etymology="e2e シード由来。",
            mnemonic="テストのために作られた語だと覚える。",
            related_forms="none",
            generation_status="complete",
            srs_stage=1,
            srs_next_review_on=today_jst(),
            srs_review_count=0,
            srs_mastered=False,
        )
        db.add(entry)
        await db.commit()
        print(json.dumps({"vocab_id": str(entry.id), "term": term}))


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1]))
