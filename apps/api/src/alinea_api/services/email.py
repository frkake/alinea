"""メール送信(dev は Mailpit: SMTP localhost:1025)。plans/03 §2.3。

SMTP は同期 stdlib を asyncio.to_thread で呼ぶ。送信失敗はログのみ(列挙攻撃対策で応答は不変)。
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

import structlog

from alinea_api.settings import ApiSettings

log = structlog.get_logger("alinea.api.email")


def _send_sync(host: str, port: int, username: str, password: str, msg: EmailMessage) -> None:
    with smtplib.SMTP(host, port, timeout=10) as smtp:
        if username:
            smtp.login(username, password)
        smtp.send_message(msg)


async def send_email(settings: ApiSettings, *, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        await asyncio.to_thread(
            _send_sync,
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_username,
            settings.smtp_password,
            msg,
        )
    except OSError as exc:  # 接続不能・タイムアウト等は握りつぶす(応答は 202 で不変)
        await log.awarning("email_send_failed", to=to, error=str(exc))


async def send_login_link(settings: ApiSettings, *, to: str, link: str) -> None:
    body = (
        "Alinea へのログインリンクです(15 分間有効)。\n\n"
        f"{link}\n\n"
        "このメールに心当たりがない場合は破棄してください。"
    )
    await send_email(settings, to=to, subject="Alinea ログインリンク", body=body)
