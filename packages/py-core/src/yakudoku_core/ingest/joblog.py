"""取り込み処理ログとタイムライン(plans/05 §10)。

`jobs.log`(JSONB 配列)へ各段の開始/完了/警告/失敗を追記する。タイムライン(2a 情報
パネルの 3 段)は別テーブルを持たず、`detail.timeline=true` エントリの射影とする(§10.2)。

エントリ形式(§10.1)::

    {"at": "2026-07-02T21:04:12+00:00", "stage": "fetching", "level": "info",
     "message": "arXiv から HTML 取得", "detail": {"format": "arxiv_html", "timeline": true}}
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yakudoku_core.db.models import Job

# level の値域(§10.1)。
LogLevel = str  # "info" | "warn" | "error"

# 翻訳スタイルの和名(§10.2 タイムライン 3 段目)。
_STYLE_JA = {"natural": "自然訳", "literal": "直訳"}


def now_iso() -> str:
    """UTC のタイムゾーン付き ISO 8601 文字列(表示整形はフロントの責務)。"""
    return dt.datetime.now(dt.UTC).isoformat()


def log_entry(
    stage: str,
    level: LogLevel,
    message: str,
    *,
    detail: dict[str, Any] | None = None,
    timeline: bool = False,
) -> dict[str, Any]:
    """`jobs.log` の 1 エントリを構築する(§10.1)。"""
    d: dict[str, Any] = dict(detail or {})
    if timeline:
        d["timeline"] = True
    return {"at": now_iso(), "stage": stage, "level": level, "message": message, "detail": d}


async def append_log(session: AsyncSession, job: Job, entry: dict[str, Any]) -> None:
    """`jobs.log` へエントリを 1 件追記して commit する。"""
    job.log = [*job.log, entry]
    await session.commit()


async def log(
    session: AsyncSession,
    job: Job,
    stage: str,
    level: LogLevel,
    message: str,
    *,
    detail: dict[str, Any] | None = None,
    timeline: bool = False,
) -> None:
    """`log_entry` を構築して追記する薄いヘルパ。"""
    entry = log_entry(stage, level, message, detail=detail, timeline=timeline)
    await append_log(session, job, entry)


# --- タイムライン message 生成規則(§10.2。文言は 2a 逐語に一致) --------------------


def fetch_timeline_message(source_format: str) -> str:
    """タイムライン 1 段目: fetching 完了(§10.2)。"""
    return {
        "latex": "arXiv から LaTeX ソース取得",
        "arxiv_html": "arXiv から HTML 取得",
        "pdf": "arXiv から PDF 取得",
        "pdf_upload": "PDF 取得(拡張から直接送信)",
    }.get(source_format, "arXiv から HTML 取得")


def structuring_timeline_message(stats: dict[str, Any]) -> str:
    """タイムライン 2 段目: structuring 完了(§10.2)。pages が無ければ図/表のみ。"""
    figures = int(stats.get("figures", 0) or 0)
    tables = int(stats.get("tables", 0) or 0)
    pages = stats.get("pages")
    if pages is None:
        return f"構造化・図表抽出(図{figures} / 表{tables})"
    return f"構造化・図表抽出({int(pages)}p / 図{figures} / 表{tables})"


def translation_timeline_message(
    style: str, source_version: str, *, appendix_untranslated: bool
) -> str:
    """タイムライン 3 段目: translating_body 完了(§10.2)。"""
    style_ja = _STYLE_JA.get(style, style)
    msg = f"全文翻訳 完了({style_ja} · {source_version})"
    if appendix_untranslated:
        msg += " · 付録は未翻訳"
    return msg


# --- 射影(API 応答用) -----------------------------------------------------------


def project_ingest_log(log_rows: list[Any]) -> list[dict[str, Any]]:
    """`GET /api/papers/{paper_id}/ingest-log` 用の射影(§10.1)。"""
    out: list[dict[str, Any]] = []
    for row in log_rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "at": row.get("at"),
                "stage": row.get("stage"),
                "level": row.get("level"),
                "message": row.get("message"),
            }
        )
    return out


def build_timeline(log_rows: list[Any]) -> list[dict[str, Any]]:
    """`ingest_timeline: {at, label}[]`(§10.2)。timeline=true エントリのみ。"""
    out: list[dict[str, Any]] = []
    for row in log_rows:
        if not isinstance(row, dict):
            continue
        detail = row.get("detail") or {}
        if isinstance(detail, dict) and detail.get("timeline"):
            out.append({"at": row.get("at"), "label": row.get("message")})
    return out
