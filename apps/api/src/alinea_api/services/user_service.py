"""ユーザーの upsert(OAuth/メールで同一 email を 1 行に統合)とアカウント削除カスケード。

plans/01 §6.1「同一メールの OAuth / メールリンクは同一 users 行に統合」。
"""

from __future__ import annotations

from alinea_core.db.models import AuthIdentity, Notification, User
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def upsert_user_by_email(
    session: AsyncSession,
    email: str,
    *,
    provider: str,
    provider_subject: str | None = None,
    display_name: str | None = None,
    avatar_url: str | None = None,
) -> User:
    """email を一意キーにユーザーを取得/作成し、provider の auth_identity を紐付ける。"""
    normalized = email.strip().lower()
    subject = provider_subject or normalized

    user = await _get_user_by_email(session, normalized)
    if user is None:
        user = User(
            email=normalized,
            display_name=display_name or normalized.split("@", 1)[0],
            avatar_url=avatar_url,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            user = await _get_user_by_email(session, normalized)
            if user is None:
                raise
    else:
        if avatar_url and not user.avatar_url:
            user.avatar_url = avatar_url

    await _ensure_auth_identity(session, user.id, provider, subject, normalized)
    await session.commit()
    await session.refresh(user)
    return user


async def _get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def _ensure_auth_identity(
    session: AsyncSession,
    user_id: str,
    provider: str,
    provider_subject: str,
    email: str,
) -> None:
    existing = await session.execute(
        select(AuthIdentity).where(
            AuthIdentity.provider == provider,
            AuthIdentity.provider_subject == provider_subject,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return
    session.add(
        AuthIdentity(
            user_id=user_id,
            provider=provider,
            provider_subject=provider_subject,
            email=email,
        )
    )


async def list_providers(session: AsyncSession, user_id: str) -> list[str]:
    result = await session.execute(
        select(AuthIdentity.provider)
        .where(AuthIdentity.user_id == user_id)
        .distinct()
        .order_by(AuthIdentity.provider)
    )
    return [row for row in result.scalars().all()]


async def count_unread_notifications(session: AsyncSession, user_id: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.read.is_(False))
    )
    return int(result.scalar_one())


async def purge_user(session: AsyncSession, user_id: str) -> bool:
    """ユーザー行を削除し、FK ON DELETE CASCADE で個人資産を全消去する(docs/01 §13)。

    削除できたら True。存在しなければ False。
    """
    user = await session.get(User, user_id)
    if user is None:
        return False
    await session.delete(user)
    await session.commit()
    return True
