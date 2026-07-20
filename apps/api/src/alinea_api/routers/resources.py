"""resources ルータ(plans/03 §12・docs/12)。

外部リソース CRUD・種別自動判定・メタ取得・公式実装検出。

- URL 貼り付け(§12.2)のみが追加経路。種類(kind)はサーバーが URL パターンから自動判定する
  (docs/12 §2)。判定できない場合は既定で ``article``。
- メタデータは追加時・再取得時にサーバーが同期取得する(3 秒タイムアウト。§12.2「メタは同期
  取得」)。取得失敗はカード作成自体を失敗させない(P3)。**決定**: 取得失敗時
  (``meta_fetched=false``)は ``title`` をスキーム除去した URL に設定する(plans/09-screens/5a
  §4.8「メタ取得失敗」表示規則をサーバー側の値として確定する)。
- 公式実装の提案(docs/12 §5)は ``resource_links`` に行を作らず ``papers.official_repo_url`` から
  都度導出する。「+ 追加」は ``official=true`` の行を作り、「無視」は ``status='dismissed'`` の
  行を作って永続的に再提案を防ぐ(同一 URL は ``uq_resource_links_item_url`` により以後の手動
  追加も 409 になる。決定: 無視は URL 単位で永続)。
- ひとことメモの ``§`` チップ(``[[sec:{section_id}|{label}]]``)は ``DocumentRevision.content``
  (現行リビジョン)からセクション実在を検証する(annotations.py の ``_RevIndex`` と同じ方針で
  ``block_search_index`` テーブルには依存しない。テストでの索引再構築が不要)。
- **apps 間 import 禁止**(Global Constraints)につき、メタ取得ロジックは
  ``apps/worker/tasks/fetch_resource_meta.py`` と意図的に重複させる(bootstrap.py の先例と同方針)。
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from alinea_core.db.models import LibraryItem, Paper
from alinea_core.db.models import ResourceLink as ResourceLinkModel
from alinea_core.db.revisions import get_latest_paper_revision
from alinea_core.document.blocks import DocumentContent
from alinea_core.jobs.store import JobStore
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from alinea_api.deps import CurrentUser, DbDep
from alinea_api.errors import ProblemException, build_problem
from alinea_api.schemas.resources import (
    ResKind,
    ResourceCreateRequest,
    ResourceListResponse,
    ResourcePatchRequest,
    ResourceSuggestion,
)
from alinea_api.schemas.resources import (
    ResourceLink as ResourceLinkOut,
)

router = APIRouter(tags=["resources"])

_FETCH_TIMEOUT = httpx.Timeout(3.0, connect=3.0)
_USER_AGENT = "AlineaBot/1.0 (+https://alinea.app)"

# --- URL 判定・正規化(docs/12 §2) ---------------------------------------------------

_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "si",
        "feature",
        "spm",
    }
)
_SLIDE_DOMAINS = frozenset({"speakerdeck.com", "slideshare.net"})
_GITHUB_PATH_RE = re.compile(r"^/([^/]+)/([^/]+)")
_NOTE_CHIP_RE = re.compile(r"\[\[sec:([^|\]]+)\|([^\]]+)\]\]")


def _host_without_www(netloc: str) -> str:
    host = netloc.lower()
    return host[4:] if host.startswith("www.") else host


def normalize_url(raw: str) -> str:
    """トラッキングパラメータ除去+ホスト小文字化+末尾スラッシュ除去(docs/12 §2 決定)。

    パス・クエリ値は大小区別を保つ(GitHub のリポジトリ名・YouTube の動画 ID は大小区別あり)。
    """
    parts = urlsplit(raw.strip())
    host = _host_without_www(parts.netloc)
    path = parts.path.rstrip("/")
    query_pairs = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in _TRACKING_PARAMS
    )
    query = urlencode(query_pairs)
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def _strip_scheme(url: str) -> str:
    return re.sub(r"^https?://", "", url)


def _domain_label(url: str) -> str:
    return _host_without_www(urlsplit(url).netloc)


def classify_kind(url: str) -> tuple[ResKind, tuple[str, str] | None]:
    """docs/12 §2 の判定規則(上から順に評価)。github は (owner, repo) を併せて返す。"""
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
            qs = dict(parse_qsl(parts.query))
            return qs.get("v")
        if parts.path.startswith("/live/"):
            rest = parts.path[len("/live/") :]
            return rest.split("/")[0] or None
    if host == "youtu.be":
        return parts.path.lstrip("/").split("/")[0] or None
    return None


def _is_valid_http_url(url: str) -> bool:
    if not url:
        return False
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


# --- メタデータ取得(docs/12 §3。3 秒タイムアウトは呼び出し側で asyncio.wait_for) ------------


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
    return (
        0x3040 <= code <= 0x30FF  # 仮名
        or 0x4E00 <= code <= 0x9FFF  # 漢字
        or 0xFF01 <= code <= 0xFF60  # 全角記号
    )


def _estimate_reading_minutes(html: str) -> int | None:
    text = _TAG_STRIP_RE.sub(" ", html)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    cjk_count = sum(1 for ch in text if _is_cjk(ch))
    if cjk_count > len(text) * 0.3:
        minutes = math.ceil(len(text) / 600)
    else:
        words = len(text.split())
        if words == 0:
            return None
        minutes = math.ceil(words / 250)
    return max(1, minutes)


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
            duration = None  # Data API 不調時は再生時間を省略(docs/12 §3.2)
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


async def _gather_metadata(
    kind: ResKind, url: str, gh: tuple[str, str] | None
) -> tuple[str, str, str | None, dict[str, Any], bool, ResKind]:
    """(title, source_label, thumbnail_url, meta, meta_fetched, kind) を返す。

    kind='article' で取得結果が PDF だった場合は 'slides' に再分類する(docs/12 §2 決定 3)。
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


_META_FETCH_BUDGET_SECONDS = 3.0


async def _gather_metadata_within_budget(
    kind: ResKind, url: str, gh: tuple[str, str] | None
) -> tuple[str, str, str | None, dict[str, Any], bool, ResKind]:
    """§12.2「メタは同期取得(3秒タイムアウト)」。個別の HTTP タイムアウトに加え全体予算を保証。"""
    try:
        return await asyncio.wait_for(
            _gather_metadata(kind, url, gh), timeout=_META_FETCH_BUDGET_SECONDS
        )
    except TimeoutError:
        return _strip_scheme(url), _domain_label(url), None, {}, False, kind


# --- ひとことメモの § チップ検証(docs/12 §4。ブロック単位ではなくセクション単位) -------------


def _collect_section_ids(content: DocumentContent) -> set[str]:
    ids: set[str] = set()

    def walk(sec: Any) -> None:
        ids.add(sec.id)
        for sub in sec.sections:
            walk(sub)

    for top in content.sections:
        walk(top)
    return ids


async def _resolve_section_ids(db: DbDep, item: LibraryItem) -> set[str]:
    paper = await db.get(Paper, item.paper_id)
    if paper is None or not paper.latest_revision_id:
        return set()
    revision = await get_latest_paper_revision(db, paper)
    if revision is None:
        return set()
    try:
        content = DocumentContent.model_validate(revision.content)
    except (ValueError, TypeError):
        return set()
    return _collect_section_ids(content)


async def _prepare_note(
    db: DbDep, item: LibraryItem, note: str | None
) -> tuple[str, list[dict[str, str]]]:
    """note_md・note_anchors を確定する。空白のみは ("" , []) (呼び出し側で null 化)。"""
    if note is None:
        return "", []
    refs = _NOTE_CHIP_RE.findall(note)
    if not refs:
        return note, []
    section_ids = await _resolve_section_ids(db, item)
    anchors: list[dict[str, str]] = []
    seen: set[str] = set()
    for section_id, label in refs:
        if section_id not in section_ids:
            raise ProblemException(
                "validation_error", detail=f"存在しないセクション参照です: §{label}"
            )
        if section_id in seen:
            continue
        seen.add(section_id)
        anchors.append({"type": "section", "section_id": section_id, "label": label})
    return note, anchors


# --- 所有チェック --------------------------------------------------------------------


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def _owned_item(db: DbDep, user_id: str, item_id: str) -> LibraryItem:
    if not _valid_uuid(item_id):
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, item_id)
    if item is None or str(item.user_id) != str(user_id):
        raise ProblemException("not_found")
    return item


async def _owned_resource(
    db: DbDep, user_id: str, resource_id: str
) -> tuple[ResourceLinkModel, LibraryItem]:
    if not _valid_uuid(resource_id):
        raise ProblemException("not_found")
    link = await db.get(ResourceLinkModel, resource_id)
    if link is None:
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, link.library_item_id)
    if item is None or str(item.user_id) != str(user_id):
        raise ProblemException("not_found")
    return link, item


def _to_out(link: ResourceLinkModel) -> ResourceLinkOut:
    return ResourceLinkOut(
        id=str(link.id),
        kind=link.kind,
        url=link.url,
        official=link.official,
        title=link.title,
        source_label=link.source_domain,
        thumbnail_url=link.thumbnail_url,
        meta=link.meta or {},
        meta_fetched=link.fetch_status == "ok",
        note=link.note_md or None,
        created_at=link.created_at.isoformat(),
    )


def _duplicate_response(existing: ResourceLinkModel, *, instance: str) -> JSONResponse:
    """§12.2 の 409 duplicate 本文(``existing: { resource_id }`` 付き Problem Details)。

    ingest.py の ``_duplicate_response`` と同方針(Problem + 追加フィールドを直接 JSONResponse
    で返す。apps 間 import 禁止のため複製する)。
    """
    problem = build_problem(
        "duplicate", status=409, title="すでに追加されています", instance=instance
    )
    content = problem.model_dump(mode="json")
    content["existing"] = {"resource_id": str(existing.id)}
    return JSONResponse(status_code=409, content=content, media_type="application/problem+json")


async def _find_existing(db: DbDep, item_id: str, url_normalized: str) -> ResourceLinkModel | None:
    result = await db.scalar(
        select(ResourceLinkModel).where(
            ResourceLinkModel.library_item_id == item_id,
            ResourceLinkModel.url_normalized == url_normalized,
        )
    )
    return result


# --- §12.1 GET 一覧 -------------------------------------------------------------------


async def _current_suggestion(db: DbDep, item: LibraryItem) -> ResourceSuggestion | None:
    """arXiv 公式実装の動的候補(``papers.official_repo_url`` 由来。resource_id を持たない)。"""
    paper = await db.get(Paper, item.paper_id)
    if paper is None or not paper.official_repo_url:
        return None
    normalized = normalize_url(paper.official_repo_url)
    count = await db.scalar(
        select(func.count())
        .select_from(ResourceLinkModel)
        .where(
            ResourceLinkModel.library_item_id == item.id,
            ResourceLinkModel.url_normalized == normalized,
        )
    )
    if count:
        return None
    return ResourceSuggestion(url=paper.official_repo_url, detected_from="arxiv_page")


def _suggested_to_suggestion(link: ResourceLinkModel) -> ResourceSuggestion:
    """``status='suggested'`` の resource_links 行 → 永続候補 DTO(設計 §3)。"""
    meta = link.meta or {}
    return ResourceSuggestion(
        url=link.url,
        detected_from="huggingface_paper",
        resource_id=str(link.id),
        kind=link.kind,
        relation=str(meta.get("relation")) if meta.get("relation") is not None else None,
        title=link.title or None,
        official_candidate=bool(meta.get("official_candidate", False)),
        meta={k: v for k, v in meta.items() if k not in ("relation", "official_candidate")},
    )


async def _collect_suggestions(db: DbDep, item: LibraryItem) -> list[ResourceSuggestion]:
    """複数候補: arXiv 動的候補 + 永続 suggested(Hugging Face)候補(設計 §3)。"""
    suggestions: list[ResourceSuggestion] = []
    arxiv = await _current_suggestion(db, item)
    if arxiv is not None:
        suggestions.append(arxiv)
    rows = (
        (
            await db.execute(
                select(ResourceLinkModel)
                .where(
                    ResourceLinkModel.library_item_id == item.id,
                    ResourceLinkModel.status == "suggested",
                )
                .order_by(ResourceLinkModel.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    suggestions.extend(_suggested_to_suggestion(r) for r in rows)
    return suggestions


@router.get(
    "/api/library-items/{item_id}/resources",
    response_model=ResourceListResponse,
    operation_id="resources_list",
)
async def list_resources(item_id: str, user: CurrentUser, db: DbDep) -> ResourceListResponse:
    item = await _owned_item(db, user.id, item_id)
    rows = (
        (
            await db.execute(
                select(ResourceLinkModel)
                .where(
                    ResourceLinkModel.library_item_id == item.id,
                    ResourceLinkModel.status == "active",
                )
                .order_by(ResourceLinkModel.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    items = [_to_out(r) for r in rows]
    suggestions = await _collect_suggestions(db, item)
    # 互換: 単数 suggestion は先頭候補(件数バッジには数えない)。
    suggestion = suggestions[0] if suggestions else None
    return ResourceListResponse(
        items=items, suggestion=suggestion, suggestions=suggestions, count=len(items)
    )


# --- §12.2 POST 追加 -------------------------------------------------------------------


async def _maybe_enqueue_automatic_code_analysis(
    db: DbDep, user: CurrentUser, item: LibraryItem, link: ResourceLinkModel
) -> None:
    """automatic モードなら、active GitHub Resource 追加時にコード対応解析を内部起動する(Task 21)。

    条件(設計 §6): ユーザー設定 mode='automatic'、論文本文が ready(最新 revision あり)、
    かつ active な GitHub Resource。commit 解決は endpoint では行わず(ネットワークを叩かない)、
    commit 未解決の automatic ジョブを enqueue して worker が解決・見積り・予算チェックする。
    """
    if link.kind != "github" or link.status != "active":
        return
    settings = (user.settings or {}).get("code_analysis") or {}
    if settings.get("mode") != "automatic":
        return
    paper = await db.get(Paper, item.paper_id)
    if paper is None:
        return
    revision = await get_latest_paper_revision(db, paper)
    if revision is None:
        return  # 本文がまだ ready でない。

    store = JobStore(db)
    # commit 未解決のため idempotency は (user, item, resource) 粒度で粗く重複を防ぐ。
    idem = f"code_analysis:auto:{user.id}:{item.id}:{link.id}"
    await store.enqueue(
        kind="code_analysis",
        payload={
            "resource_id": str(link.id),
            "library_item_id": str(item.id),
            "trigger": "automatic",
        },
        idempotency_key=idem,
        priority="bulk",
        user_id=str(user.id),
        paper_id=str(item.paper_id),
        library_item_id=str(item.id),
    )


@router.post(
    "/api/library-items/{item_id}/resources",
    response_model=ResourceLinkOut,
    status_code=201,
    operation_id="resources_create",
)
async def create_resource(
    item_id: str, body: ResourceCreateRequest, user: CurrentUser, db: DbDep
) -> ResourceLinkOut | JSONResponse:
    item = await _owned_item(db, user.id, item_id)
    url = body.url.strip()
    if not _is_valid_http_url(url):
        raise ProblemException("validation_error", detail="URL の形式が正しくありません")

    normalized = normalize_url(url)
    existing = await _find_existing(db, str(item.id), normalized)
    if existing is not None:
        return _duplicate_response(existing, instance=f"/api/library-items/{item_id}/resources")

    note_md, note_anchors = await _prepare_note(db, item, body.note)

    kind, gh = classify_kind(url)
    (
        title,
        source_label,
        thumbnail_url,
        meta,
        meta_fetched,
        kind,
    ) = await _gather_metadata_within_budget(kind, url, gh)

    link = ResourceLinkModel(
        id=str(uuid.uuid4()),
        library_item_id=str(item.id),
        status="active",
        kind=kind,
        url=url,
        url_normalized=normalized,
        official=False,
        title=title,
        thumbnail_url=thumbnail_url,
        source_domain=source_label,
        meta=meta,
        fetch_status="ok" if meta_fetched else "failed",
        note_md=note_md if note_md.strip() else "",
        note_anchors=note_anchors if note_md.strip() else [],
    )
    db.add(link)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raced = await _find_existing(db, str(item.id), normalized)
        if raced is not None:
            return _duplicate_response(raced, instance=f"/api/library-items/{item_id}/resources")
        raise
    # automatic モードなら手動追加した active GitHub Resource でコード対応解析を起動する。
    await _maybe_enqueue_automatic_code_analysis(db, user, item, link)
    return _to_out(link)


# --- §12.3 PATCH 更新 ------------------------------------------------------------------


@router.patch(
    "/api/resources/{resource_id}",
    response_model=ResourceLinkOut,
    operation_id="resources_update",
)
async def patch_resource(
    resource_id: str, body: ResourcePatchRequest, user: CurrentUser, db: DbDep
) -> ResourceLinkOut:
    link, item = await _owned_resource(db, user.id, resource_id)
    fields = body.model_fields_set

    if "title" in fields and body.title is not None:
        trimmed = body.title.strip()
        if trimmed:
            link.title = trimmed

    if "kind" in fields and body.kind is not None:
        link.kind = body.kind

    if "note" in fields:
        note_md, note_anchors = await _prepare_note(db, item, body.note)
        if note_md.strip():
            link.note_md = note_md
            link.note_anchors = note_anchors
        else:
            link.note_md = ""
            link.note_anchors = []

    await db.commit()
    await db.refresh(link)
    return _to_out(link)


# --- §12.4 DELETE ----------------------------------------------------------------------


@router.delete(
    "/api/resources/{resource_id}",
    status_code=204,
    operation_id="resources_delete",
)
async def delete_resource(resource_id: str, user: CurrentUser, db: DbDep) -> Response:
    link, _item = await _owned_resource(db, user.id, resource_id)
    await db.delete(link)
    await db.commit()
    return Response(status_code=204)


# --- §12.5 メタ再取得 -------------------------------------------------------------------


@router.post(
    "/api/resources/{resource_id}/refresh-meta",
    response_model=ResourceLinkOut,
    operation_id="resources_refresh_meta",
)
async def refresh_meta(resource_id: str, user: CurrentUser, db: DbDep) -> ResourceLinkOut:
    link, _item = await _owned_resource(db, user.id, resource_id)
    kind, gh = classify_kind(link.url)
    (
        title,
        source_label,
        thumbnail_url,
        meta,
        meta_fetched,
        kind,
    ) = await _gather_metadata_within_budget(kind, link.url, gh)
    link.title = title
    link.source_domain = source_label
    link.thumbnail_url = thumbnail_url
    link.meta = meta
    link.fetch_status = "ok" if meta_fetched else "failed"
    link.kind = kind
    await db.commit()
    await db.refresh(link)
    return _to_out(link)


# --- §12.6 公式実装提案の確定・却下 -------------------------------------------------------


@router.post(
    "/api/library-items/{item_id}/resource-suggestion/accept",
    response_model=ResourceLinkOut,
    status_code=201,
    operation_id="resources_suggestion_accept",
)
async def accept_suggestion(item_id: str, user: CurrentUser, db: DbDep) -> ResourceLinkOut:
    item = await _owned_item(db, user.id, item_id)
    suggestion = await _current_suggestion(db, item)
    if suggestion is None:
        raise ProblemException("not_found")

    normalized = normalize_url(suggestion.url)
    kind, gh = classify_kind(suggestion.url)
    (
        title,
        source_label,
        thumbnail_url,
        meta,
        meta_fetched,
        kind,
    ) = await _gather_metadata_within_budget(kind, suggestion.url, gh)
    link = ResourceLinkModel(
        id=str(uuid.uuid4()),
        library_item_id=str(item.id),
        status="active",
        kind=kind,
        url=suggestion.url,
        url_normalized=normalized,
        official=True,
        title=title,
        thumbnail_url=thumbnail_url,
        source_domain=source_label,
        meta=meta,
        fetch_status="ok" if meta_fetched else "failed",
        note_md="",
        note_anchors=[],
    )
    db.add(link)
    await db.commit()
    # 候補 accept で active GitHub Resource になったら automatic 解析を起動する。
    await _maybe_enqueue_automatic_code_analysis(db, user, item, link)
    return _to_out(link)


@router.post(
    "/api/library-items/{item_id}/resource-suggestion/dismiss",
    status_code=204,
    operation_id="resources_suggestion_dismiss",
)
async def dismiss_suggestion(item_id: str, user: CurrentUser, db: DbDep) -> Response:
    item = await _owned_item(db, user.id, item_id)
    suggestion = await _current_suggestion(db, item)
    if suggestion is None:
        raise ProblemException("not_found")

    link = ResourceLinkModel(
        id=str(uuid.uuid4()),
        library_item_id=str(item.id),
        status="dismissed",
        kind="github",
        url=suggestion.url,
        url_normalized=normalize_url(suggestion.url),
        official=False,
        title="",
        source_domain="",
        meta={},
        fetch_status="pending",
        note_md="",
        note_anchors=[],
    )
    db.add(link)
    await db.commit()
    return Response(status_code=204)


# --- Task 18: ID 指定の候補 accept / dismiss(永続 suggested 行) ---------------------


async def _owned_suggested_resource(
    db: DbDep, user_id: str, resource_id: str
) -> tuple[ResourceLinkModel, LibraryItem]:
    """所有する ``status='suggested'`` の候補行を返す(active/dismissed は not_found)。"""
    link, item = await _owned_resource(db, user_id, resource_id)
    if link.status != "suggested":
        raise ProblemException("not_found")
    return link, item


@router.post(
    "/api/resources/{resource_id}/accept-suggestion",
    response_model=ResourceLinkOut,
    operation_id="resources_accept_suggestion",
)
async def accept_suggestion_by_id(
    resource_id: str, user: CurrentUser, db: DbDep
) -> ResourceLinkOut:
    """候補(suggested)を採用して active にする。

    github / project の official_candidate だけ ``official=true`` にする(設計 §3)。
    他 kind(Hugging Face の Model/Dataset/Space 等)は official=false のまま採用する。
    """
    link, item = await _owned_suggested_resource(db, user.id, resource_id)
    meta = link.meta or {}
    is_official = bool(meta.get("official_candidate", False)) and link.kind in ("github", "project")
    link.status = "active"
    link.official = is_official
    await db.commit()
    await db.refresh(link)
    # 公式 GitHub を採用したら automatic 解析を起動する(設計 §6)。
    await _maybe_enqueue_automatic_code_analysis(db, user, item, link)
    return _to_out(link)


@router.post(
    "/api/resources/{resource_id}/dismiss-suggestion",
    status_code=204,
    operation_id="resources_dismiss_suggestion",
)
async def dismiss_suggestion_by_id(resource_id: str, user: CurrentUser, db: DbDep) -> Response:
    """候補(suggested)を却下して dismissed にする。同一正規化 URL は再同期で復活させない。"""
    link, _item = await _owned_suggested_resource(db, user.id, resource_id)
    link.status = "dismissed"
    await db.commit()
    return Response(status_code=204)
