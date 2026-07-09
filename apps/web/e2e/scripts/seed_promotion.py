#!/usr/bin/env python3
"""PW-11(通知経由の B→A 昇格)専用の E2E シード補助。

apps/web/e2e/specs/pw-11-references-and-promotion.spec.ts からのみ ``uv run --no-sync
python apps/web/e2e/scripts/seed_promotion.py <user_email>`` として呼ばれる。

worker cron ``check_quality_promotions``(毎日 07:30 JST)の実行を E2E で待つのは非現実的
なため、cron が本来行う「``status_suggestion``(``promote_revision``)通知の INSERT」だけを
本番と同一の関数(``alinea_api.services.notifications.fire_status_suggestion``)で直接
発火する。それ以外(通知一覧の取得・「変更する」クリック・ingest ジョブの実行・
adopt-revision 相当のリアンカー)は全て実配線(実 worker + モック arXiv サーバ経由)。

前提として quality B の DocumentRevision(§4.5 の carryover 前段)+ Annotation 2 件
(1 件は新リビジョンに存在するテキストを quote に持つ = リアンカーで追従、もう 1 件は
存在しないテキスト = 未配置に残る)を作る。標準出力へ 1 行の JSON を書く。
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time
import uuid

import redis.asyncio as aioredis
from alinea_core.db.models import Annotation, DocumentRevision, LibraryItem, Paper, User
from alinea_core.db.session import get_sessionmaker
from alinea_core.settings import get_settings
from sqlalchemy import select

# packages/llm の mock_server.py `_LATEXML_HTML` の S2 段落と一致させる(quote 探索で一致)。
MOVED_QUOTE = "The mock method paragraph."
LOST_QUOTE = "This sentence does not exist anywhere in the new mock revision at all."


def _arxiv_id() -> str:
    n = (int(time.time() * 1000) + random.randint(0, 9999)) % 100000  # noqa: S311 (E2E 専用・非暗号)
    return f"{random.randint(1001, 2912)}.{n:05d}"  # noqa: S311 (E2E 専用・非暗号)


async def _main(user_email: str) -> None:
    from alinea_api.services.notifications import fire_status_suggestion

    settings = get_settings()
    maker = get_sessionmaker()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    arxiv_id = _arxiv_id()
    try:
        async with maker() as db:
            user = (
                (await db.execute(select(User).where(User.email == user_email))).scalars().first()
            )
            if user is None:
                raise SystemExit(f"user not found: {user_email}")

            paper = Paper(
                id=str(uuid.uuid4()),
                arxiv_id=arxiv_id,
                title="E2E Promotable Paper",
                visibility="public",
            )
            db.add(paper)
            await db.flush()

            old_rev = DocumentRevision(
                id=str(uuid.uuid4()),
                paper_id=paper.id,
                source_version="v1",
                parser_version="pdf-1.0.0",
                quality_level="B",
                source_format="pdf",
                content={
                    "quality_level": "B",
                    "sections": [
                        {
                            "id": "sec-1",
                            "heading": {"number": "1", "title": "Method"},
                            "blocks": [
                                {
                                    "id": "blk-moved",
                                    "type": "paragraph",
                                    "inlines": [{"t": "text", "v": MOVED_QUOTE}],
                                },
                                {
                                    "id": "blk-lost",
                                    "type": "paragraph",
                                    "inlines": [{"t": "text", "v": LOST_QUOTE}],
                                },
                            ],
                        }
                    ],
                },
            )
            db.add(old_rev)
            await db.flush()
            paper.latest_revision_id = old_rev.id

            li = LibraryItem(
                id=str(uuid.uuid4()), user_id=str(user.id), paper_id=paper.id, status="reading"
            )
            db.add(li)
            await db.flush()

            ann_moved = Annotation(
                id=str(uuid.uuid4()),
                library_item_id=li.id,
                kind="highlight",
                color="important",
                anchor={
                    "revision_id": str(old_rev.id),
                    "block_id": "blk-moved",
                    "start": 0,
                    "end": len(MOVED_QUOTE),
                    "quote": MOVED_QUOTE,
                    "side": "source",
                },
            )
            ann_lost = Annotation(
                id=str(uuid.uuid4()),
                library_item_id=li.id,
                kind="highlight",
                color="important",
                anchor={
                    "revision_id": str(old_rev.id),
                    "block_id": "blk-lost",
                    "start": 0,
                    "end": len(LOST_QUOTE),
                    "quote": LOST_QUOTE,
                    "side": "source",
                },
            )
            db.add(ann_moved)
            db.add(ann_lost)
            await db.commit()

            note = await fire_status_suggestion(
                db,
                r,
                user_id=str(user.id),
                library_item_id=str(li.id),
                paper_title=paper.title,
                reason="promotion_b_to_a",
                revision_id=str(old_rev.id),
            )
            if note is None:
                raise SystemExit("fire_status_suggestion returned None (settings gate?)")

            print(
                json.dumps(
                    {
                        "paper_id": str(paper.id),
                        "library_item_id": str(li.id),
                        "notification_id": str(note.id),
                        "old_revision_id": str(old_rev.id),
                        "arxiv_id": arxiv_id,
                        "annotation_moved_id": str(ann_moved.id),
                        "annotation_lost_id": str(ann_lost.id),
                    }
                )
            )
    finally:
        await r.aclose()


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1]))
