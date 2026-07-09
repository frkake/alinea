"""arXiv メタデータ取得(Atom API)+ ライセンス取得(OAI-PMH)(plans/05 §3.2・§3.3)。

Atom → papers カラムの対応表(§3.2)と comment からの venue 抽出(§3.2.1)を実装する。
ライセンスは Atom には無いため OAI-PMH で別取得し licenses.normalize_license_url で正規化する。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx
from pydantic import BaseModel, ConfigDict

from alinea_core.arxiv.fetch import FetchError, make_arxiv_client
from alinea_core.arxiv.ids import ArxivId, api_query_url, oai_url
from alinea_core.arxiv.licenses import normalize_license_url
from alinea_core.licenses import LicenseId
from alinea_core.settings import CoreSettings, get_settings

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_WS = re.compile(r"\s+")
_VERSION_RE = re.compile(r"v(\d+)$")

# comment フォールバックの会議名抽出(§3.2.1)
_VENUE = re.compile(
    r"\b(ICLR|ICML|NeurIPS|NIPS|CVPR|ICCV|ECCV|WACV|ACL|EMNLP|NAACL|COLING|AAAI|IJCAI|"
    r"KDD|WWW|TheWebConf|SIGIR|SIGGRAPH(?:\s+Asia)?|SODA|STOC|FOCS|COLT|AISTATS|UAI|"
    r"CoRL|RSS|ICRA|IROS|INTERSPEECH|ICASSP)\s*[',]?\s*((?:19|20)\d{2})\b"
)


class ArxivMeta(BaseModel):
    """papers に投入する正規化済みメタデータ(§3.2 の対応表と同型)。"""

    model_config = ConfigDict(frozen=True)

    arxiv_id: str
    title: str
    authors: list[dict[str, str]]
    abstract: str
    published_on: str | None
    arxiv_categories: list[str]
    doi: str | None
    venue: str | None
    latest_version: str
    license: LicenseId


def _clean(text: str | None) -> str:
    return _WS.sub(" ", text).strip() if text else ""


def _child_text(el: ET.Element, tag: str) -> str | None:
    child = el.find(tag)
    if child is None or child.text is None:
        return None
    return _clean(child.text)


def _extract_venue(entry: ET.Element) -> str | None:
    journal_ref = _child_text(entry, f"{_ARXIV}journal_ref")
    if journal_ref:
        return journal_ref
    comment = _child_text(entry, f"{_ARXIV}comment")
    if comment and (m := _VENUE.search(comment)):
        return f"{m.group(1)} {m.group(2)}"
    return None


def _extract_categories(entry: ET.Element) -> list[str]:
    ordered: list[str] = []
    primary = entry.find(f"{_ARXIV}primary_category")
    if primary is not None and (term := primary.get("term")):
        ordered.append(term)
    for cat in entry.findall(f"{_ATOM}category"):
        term = cat.get("term")
        if term and term not in ordered:
            ordered.append(term)
    return ordered


def _extract_latest_version(entry: ET.Element) -> str:
    raw = _child_text(entry, f"{_ATOM}id") or ""
    m = _VERSION_RE.search(raw)
    return f"v{m.group(1)}" if m else "v1"


def _parse_atom(xml_text: str, ref: ArxivId) -> ArxivMeta:
    # spec(§3.2)が stdlib xml.etree を指定。arXiv 上流の準信頼 XML のみを対象とし、
    # defusedxml は依存に無いため S314 を局所的に許容する。
    root = ET.fromstring(xml_text)  # noqa: S314
    entry = root.find(f"{_ATOM}entry")
    if entry is None:
        raise FetchError("source_not_found", f"arxiv metadata not found: {ref.versioned}")
    published = _child_text(entry, f"{_ATOM}published")
    return ArxivMeta(
        arxiv_id=ref.id,
        title=_clean(_child_text(entry, f"{_ATOM}title")),
        authors=[
            {"name": _clean(name.text)}
            for name in entry.findall(f"{_ATOM}author/{_ATOM}name")
            if name.text
        ],
        abstract=_clean(_child_text(entry, f"{_ATOM}summary")),
        published_on=published.split("T")[0] if published else None,
        arxiv_categories=_extract_categories(entry),
        doi=_child_text(entry, f"{_ARXIV}doi"),
        venue=_extract_venue(entry),
        latest_version=_extract_latest_version(entry),
        license="unknown",
    )


def _parse_license(xml_text: str) -> LicenseId:
    root = ET.fromstring(xml_text)  # noqa: S314  (§3.2 準拠。準信頼 XML のみ)
    for el in root.iter():
        if el.tag.endswith("}license") or el.tag == "license":
            return normalize_license_url(el.text)
    return "unknown"


async def _fetch_license(ref: ArxivId, http: httpx.AsyncClient, base_url: str | None) -> LicenseId:
    """OAI-PMH でライセンスを取得する。取得失敗時は unknown(安全側)。"""
    try:
        resp = await http.get(oai_url(ref, base_url), timeout=8.0)
        resp.raise_for_status()
    except httpx.HTTPError:
        return "unknown"
    return _parse_license(resp.text)


async def _fetch_metadata(
    ref: ArxivId, http: httpx.AsyncClient, settings: CoreSettings
) -> ArxivMeta:
    base_url = settings.alinea_arxiv_base_url or None
    try:
        resp = await http.get(api_query_url(ref, base_url), timeout=8.0)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        kind = "rate_limited" if code == 429 else "upstream_5xx" if code >= 500 else "network_error"
        raise FetchError(kind, f"arxiv metadata api {code}") from e
    except httpx.HTTPError as e:
        raise FetchError("network_error", str(e)) from e
    meta = _parse_atom(resp.text, ref)
    lic = await _fetch_license(ref, http, base_url)
    return meta.model_copy(update={"license": lic})


async def fetch_metadata(
    ref: ArxivId,
    *,
    http: httpx.AsyncClient | None = None,
    settings: CoreSettings | None = None,
) -> ArxivMeta:
    """arXiv Atom API + OAI-PMH からメタデータ・ライセンスを取得する(§3.2/§3.3)。

    http は注入可能。未指定なら make_arxiv_client で生成し本関数内で閉じる。
    """
    s = settings or get_settings()
    if http is None:
        async with make_arxiv_client(s) as client:
            return await _fetch_metadata(ref, client, s)
    return await _fetch_metadata(ref, http, s)
