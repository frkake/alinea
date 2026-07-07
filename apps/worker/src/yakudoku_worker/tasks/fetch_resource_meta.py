"""``jobs.kind='resource_meta'`` ハンドラ(plans/02 §「非同期ジョブ」・docs/12 §3)。

外部リソース(``resource_links``)のタイトル・サムネイル・種類別メタを取得し、対象行を更新する。
段階は ``queued → fetching → complete``(plans/02)。**メタ取得は本質的にベストエフォート**
(docs/12 §3.2「取得失敗で追加自体を失敗させない」P3)なので、HTTP 取得が失敗しても
``fetch_status='failed'`` で確定させジョブ自体は ``succeed`` する(リトライで自動再送しない —
再取得はユーザー操作(カードメニュー「メタを再取得」)起点)。

payload: ``{"resource_link_id": "<uuid>"}``。

**apps 間 import 禁止**(Global Constraints)のため、URL 判定・メタ取得の実ロジックは
``apps/api/src/yakudoku_api/routers/resources.py`` と意図的に重複させる
(``yakudoku_worker.bootstrap`` が SSE envelope 形式を複製する先例と同方針)。

**現状の呼び出し元(followups)**: plans/03 §12.2/§12.5 の POST 系エンドポイントは API プロセス内で
同期的にメタを取得する契約(3 秒タイムアウト即時応答)であり、v1 のどこからもこのジョブを
enqueue しない。将来、対応急増などでジョブ化する場合の受け皿として用意し、``HANDLERS`` への
登録行(``HANDLERS["resource_meta"] = run_fetch_resource_meta_job``)は
``apps/worker/src/yakudoku_worker/tasks/__init__.py``(共有ファイル)側の変更が必要なため
followups に委ねる。
"""

from __future__ import annotations

import math
import os
import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
from yakudoku_core.db.models import Job
from yakudoku_core.db.models import ResourceLink as ResourceLinkModel
from yakudoku_core.jobs.store import JobStore

ResKind = str  # "github" | "youtube" | "slides" | "article"(worker 側は緩く str で扱う)

_FETCH_TIMEOUT = httpx.Timeout(3.0, connect=3.0)
_USER_AGENT = "YakudokuBot/1.0 (+https://yakudoku.app)"
_GITHUB_PATH_RE = re.compile(r"^/([^/]+)/([^/]+)")
_SLIDE_DOMAINS = frozenset({"speakerdeck.com", "slideshare.net"})


def _host_without_www(netloc: str) -> str:
    host = netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _strip_scheme(url: str) -> str:
    return re.sub(r"^https?://", "", url)


def _domain_label(url: str) -> str:
    return _host_without_www(urlsplit(url).netloc)


def classify_kind(url: str) -> tuple[ResKind, tuple[str, str] | None]:
    """docs/12 §2 の判定規則(apps/api/routers/resources.py と同一規則。意図的な複製)。"""
    parts = urlsplit(url)
    host = _host_without_www(parts.netloc)
    path = parts.path

    if host == "github.com":
        m = _GITHUB_PATH_RE.match(path)
        if m:
            owner, repo = m.group(1), m.group(2)
            if repo.endswith(".git"):
                repo = repo[: -len(".git")]
            return "github", (owner, repo)

    if host in ("youtube.com", "m.youtube.com") and (path == "/watch" or path.startswith("/live/")):
        return "youtube", None
    if host == "youtu.be":
        return "youtube", None

    if path.lower().endswith(".pdf") or host in _SLIDE_DOMAINS:
        return "slides", None

    return "article", None


def youtube_video_id(url: str) -> str | None:
    parts = urlsplit(url)
    host = _host_without_www(parts.netloc)
    if host in ("youtube.com", "m.youtube.com"):
        if parts.path == "/watch":
            return dict(parse_qsl(parts.query)).get("v")
        if parts.path.startswith("/live/"):
            rest = parts.path[len("/live/") :]
            return rest.split("/")[0] or None
    if host == "youtu.be":
        return parts.path.lstrip("/").split("/")[0] or None
    return None


def _parse_iso8601_duration(value: str) -> int | None:
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", value)
    if not m:
        return None
    hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    return hours * 3600 + minutes * 60 + seconds


_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([a-zA-Z:_-]+)\s*=\s*"([^"]*)"|([a-zA-Z:_-]+)\s*=\s*\'([^\']*)\'')
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _parse_og_tags(html: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for tag in _META_TAG_RE.findall(html):
        attrs: dict[str, str] = {}
        for m in _ATTR_RE.finditer(tag):
            if m.group(1) is not None:
                attrs[m.group(1).lower()] = m.group(2)
            else:
                attrs[m.group(3).lower()] = m.group(4)
        prop = (attrs.get("property") or attrs.get("name") or "").lower()
        if prop.startswith("og:") and "content" in attrs:
            result[prop] = attrs["content"]
    return result


def _extract_title_tag(html: str) -> str | None:
    m = _TITLE_TAG_RE.search(html)
    if not m:
        return None
    text = re.sub(r"\s+", " ", m.group(1)).strip()
    return text or None


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return 0x3040 <= code <= 0x30FF or 0x4E00 <= code <= 0x9FFF or 0xFF01 <= code <= 0xFF60


def _estimate_reading_minutes(html: str) -> int | None:
    text = re.sub(r"\s+", " ", _TAG_STRIP_RE.sub(" ", html)).strip()
    if not text:
        return None
    cjk_count = sum(1 for ch in text if _is_cjk(ch))
    if cjk_count > len(text) * 0.3:
        return max(1, math.ceil(len(text) / 600))
    words = len(text.split())
    if words == 0:
        return None
    return max(1, math.ceil(words / 250))


async def _fetch_github_meta(client: httpx.AsyncClient, owner: str, repo: str) -> dict[str, Any]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
    token = os.environ.get("GITHUB_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = await client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return {
        "language": data.get("language"),
        "stars": data.get("stargazers_count"),
        "updated_at": data.get("pushed_at") or data.get("updated_at"),
    }


async def _fetch_youtube_meta(
    client: httpx.AsyncClient, video_id: str | None, url: str
) -> tuple[dict[str, Any], str, str | None]:
    resp = await client.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"})
    resp.raise_for_status()
    data = resp.json()
    title = str(data.get("title") or "")
    thumbnail_url = data.get("thumbnail_url")
    duration: int | None = None
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if api_key and video_id:
        try:
            resp2 = await client.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"id": video_id, "part": "contentDetails", "key": api_key},
            )
            resp2.raise_for_status()
            items = resp2.json().get("items") or []
            if items:
                duration = _parse_iso8601_duration(items[0]["contentDetails"]["duration"])
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            duration = None
    return {"duration_seconds": duration}, title, thumbnail_url


def _count_pdf_pages(pdf_bytes: bytes) -> int | None:
    pages, _title = _read_pdf(pdf_bytes)
    return pages


def _read_pdf(pdf_bytes: bytes) -> tuple[int | None, str | None]:
    """(枚数, メタデータのタイトル)。PDF をサーバーで取得しページ数をカウントする(docs/12 §3.2)。"""
    try:
        import fitz as pymupdf  # pymupdf は fitz として import 可能(mypy override 済み)
    except ImportError:
        return None, None
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            title = (doc.metadata or {}).get("title") or None
            return int(doc.page_count), (title.strip() or None if title else None)
    except Exception:
        return None, None


async def _fetch_slides_meta(client: httpx.AsyncClient, url: str) -> tuple[dict[str, Any], str]:
    resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
    resp.raise_for_status()
    pages, pdf_title = _read_pdf(resp.content)
    filename = urlsplit(url).path.rsplit("/", 1)[-1]
    title = pdf_title or filename or _strip_scheme(url)
    return {"format": "pdf", "pages": pages}, title


async def gather_metadata(
    kind: ResKind, url: str, gh: tuple[str, str] | None
) -> tuple[str, str, str | None, dict[str, Any], bool, ResKind]:
    """(title, source_label, thumbnail_url, meta, meta_fetched, kind) を返す(§3)。

    kind='article' の取得結果が PDF だった場合は 'slides' に再分類する(docs/12 §2 決定 3)。
    """
    fallback_title = _strip_scheme(url)
    domain_label = _domain_label(url)
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=_FETCH_TIMEOUT) as client:
            if kind == "github" and gh is not None:
                owner, repo = gh
                meta = await _fetch_github_meta(client, owner, repo)
                return f"{owner}/{repo}", "GitHub", None, meta, True, "github"
            if kind == "youtube":
                video_id = youtube_video_id(url)
                meta, title, thumb = await _fetch_youtube_meta(client, video_id, url)
                return title or fallback_title, "YouTube", thumb, meta, True, "youtube"
            if kind == "slides":
                meta, title = await _fetch_slides_meta(client, url)
                return title or fallback_title, domain_label, None, meta, True, "slides"

            resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").lower()
            if "application/pdf" in content_type:
                pages = _count_pdf_pages(resp.content)
                return (
                    fallback_title,
                    domain_label,
                    None,
                    {"format": "pdf", "pages": pages},
                    True,
                    "slides",
                )
            html = resp.text
            og = _parse_og_tags(html)
            title = og.get("og:title") or _extract_title_tag(html) or fallback_title
            thumb = og.get("og:image")
            site = og.get("og:site_name") or domain_label
            minutes = _estimate_reading_minutes(html)
            return title, site, thumb, {"reading_minutes": minutes}, True, "article"
    except Exception:
        return fallback_title, domain_label, None, {}, False, kind


async def run_fetch_resource_meta_job(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """``kind='resource_meta'`` ハンドラ。対象 ``ResourceLink`` のメタを取得・保存する。"""
    session = store.session
    payload = job.payload or {}
    resource_link_id = str(payload.get("resource_link_id", ""))
    link = await session.get(ResourceLinkModel, resource_link_id) if resource_link_id else None
    if link is None:
        # 対象が既に削除されている等(通常到達しない)。ジョブとしては成立させる(P3)。
        await store.succeed(str(job.id), {"skipped": True, "reason": "resource_link_not_found"})
        return

    kind, gh = classify_kind(link.url)
    title, source_label, thumbnail_url, meta, meta_fetched, kind = await gather_metadata(
        kind, link.url, gh
    )
    link.title = title
    link.source_domain = source_label
    link.thumbnail_url = thumbnail_url
    link.meta = meta
    link.fetch_status = "ok" if meta_fetched else "failed"
    link.kind = kind
    await session.commit()
    await store.succeed(
        str(job.id), {"resource_link_id": str(link.id), "meta_fetched": meta_fetched}
    )
