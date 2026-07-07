"""user_service 単体テスト(plans/01 §6.1)。

- 同一 email への OAuth/メールリンクの統合(upsert): 既存ユーザーの avatar_url 補完・
  同一 provider+subject の auth_identity 再利用(重複作成しない)。
- list_providers・count_unread_notifications・purge_user(存在しない場合 False)。
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.services.user_service import (
    count_unread_notifications,
    list_providers,
    purge_user,
    upsert_user_by_email,
)
from yakudoku_core.db.models import AuthIdentity, Notification


async def test_upsert_user_by_email_fills_missing_avatar_on_existing_user(
    db_session: AsyncSession, unique_email: str
) -> None:
    created = await upsert_user_by_email(db_session, unique_email, provider="email")
    assert created.avatar_url is None

    updated = await upsert_user_by_email(
        db_session,
        unique_email,
        provider="email",
        avatar_url="http://pic.example/a.png",
    )
    assert updated.id == created.id
    assert updated.avatar_url == "http://pic.example/a.png"

    await purge_user(db_session, str(created.id))
    await db_session.commit()


async def test_upsert_user_by_email_reuses_existing_auth_identity(
    db_session: AsyncSession, unique_email: str
) -> None:
    subject = f"sub-{uuid.uuid4().hex}"
    first = await upsert_user_by_email(
        db_session, unique_email, provider="google", provider_subject=subject
    )
    # 同一 provider+subject で再度 upsert しても auth_identity は増えない(§6.1 の統合)。
    second = await upsert_user_by_email(
        db_session, unique_email, provider="google", provider_subject=subject
    )
    assert first.id == second.id

    count = await db_session.scalar(
        select(func.count())
        .select_from(AuthIdentity)
        .where(AuthIdentity.provider == "google", AuthIdentity.provider_subject == subject)
    )
    assert count == 1

    await purge_user(db_session, str(first.id))
    await db_session.commit()


async def test_list_providers_returns_distinct_sorted_providers(
    db_session: AsyncSession, unique_email: str
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    await upsert_user_by_email(
        db_session,
        unique_email,
        provider="google",
        provider_subject=f"g-{uuid.uuid4().hex}",
    )
    providers = await list_providers(db_session, str(user.id))
    assert providers == sorted({"email", "google"})

    await purge_user(db_session, str(user.id))
    await db_session.commit()


async def test_count_unread_notifications_counts_only_unread(
    db_session: AsyncSession, unique_email: str
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    db_session.add_all(
        [
            Notification(user_id=user.id, kind="translation_complete", payload={}, read=False),
            Notification(user_id=user.id, kind="translation_complete", payload={}, read=False),
            Notification(user_id=user.id, kind="translation_complete", payload={}, read=True),
        ]
    )
    await db_session.commit()

    count = await count_unread_notifications(db_session, str(user.id))
    assert count == 2

    await purge_user(db_session, str(user.id))
    await db_session.commit()


async def test_purge_user_returns_false_when_user_missing(db_session: AsyncSession) -> None:
    assert await purge_user(db_session, str(uuid.uuid4())) is False
