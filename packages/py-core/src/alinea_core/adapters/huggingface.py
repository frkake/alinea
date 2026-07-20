"""Hugging Face アダプタ + Hub API クライアント(Task 18)。

Hugging Face は **論文本文の取得元ではなく**、論文を同定して関連資料(Model / Dataset /
Space / GitHub / project page)を収集する情報源として扱う(設計 §1)。したがって本モジュールは
他アダプタ(ACL Anthology 等)と違い landing HTML → SiteMeta 写像を主目的にせず、公開 Hub API
から関連ソース候補を導出することに主眼を置く。

構成:

- :func:`parse_huggingface_url` — ``huggingface.co`` / ``hf.co`` の Paper / Model / Dataset /
  Space URL だけを検出する **純粋** パーサ。org page・collection・settings・resolve URL は拒否する。
- :class:`HuggingFaceAdapter` — :class:`SiteAdapter` プロトコル実装(registry 解決・SSRF
  allow-list 用)。``landing_url`` / ``pdf_url`` が ``huggingface.co`` を宣言するため
  ``adapter_allowed_hosts`` は ``{"huggingface.co"}`` を返す。
- :func:`discover_paper_resources` — Paper API ペイロード → :class:`DiscoveredResource` の列。
  Paper Page 1 / githubRepo 1 / projectPage 1 / linkedModels 5 / linkedDatasets 3 /
  linkedSpaces 3 を **この順** で生成し、最大 13 件・正規化 URL で重複排除する。paper-level の
  ``githubRepo`` と ``projectPage`` だけを ``official_candidate=True`` にする(設計 §3)。
- :func:`arxiv_id_from_tags` — Model / Dataset / Space repo の ``arxiv:<ID>`` タグから arXiv ID
  を **一意に決まる場合だけ** 返す(0 件 / 複数件 → ``None`` = 選択不能)。
- :class:`HuggingFaceClient` — 公開 Hub API を **設定可能な base URL** で叩く副作用層。テストは
  MockTransport + 別 base URL を注入して実 HF に一切触れない。401/403/404/429 は既存の
  :class:`SiteFetchError` へ分類する(rate-limit reset まで再試行しない方針は上位層が守る)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

import httpx

from alinea_core.adapters.base import SiteMeta, SiteRef
from alinea_core.adapters.fetch import SiteFetchError

if TYPE_CHECKING:
    from alinea_core.settings import CoreSettings

_SITE = "huggingface"
_BASE = "https://huggingface.co"

# 許可ホストは正規ホスト huggingface.co と短縮 hf.co のみ(設計 §2 / Task 15 SSRF)。
_HF_HOST = r"(?:www\.)?(?:huggingface\.co|hf\.co)"
_SCHEME = r"(?:https?://)?"
_TAIL = r"(?:[?#].*)?"

# 取り込み入口にしない予約セグメント(org page ではなく機能ページ)。
_RESERVED_TOP = frozenset(
    {
        "papers",
        "datasets",
        "spaces",
        "models",
        "collections",
        "settings",
        "organizations",
        "join",
        "login",
        "docs",
        "blog",
        "pricing",
        "search",
        "new",
        "notifications",
    }
)

# Paper: /papers/<arxiv-id>
_PAPER_RE = re.compile(rf"^{_SCHEME}{_HF_HOST}/papers/(?P<id>[^/?#]+)/?{_TAIL}$", re.IGNORECASE)
# Dataset: /datasets/<owner>/<name> または /datasets/<name>
_DATASET_RE = re.compile(
    rf"^{_SCHEME}{_HF_HOST}/datasets/(?P<id>[^/?#]+(?:/[^/?#]+)?)/?{_TAIL}$", re.IGNORECASE
)
# Space: /spaces/<owner>/<name>
_SPACE_RE = re.compile(
    rf"^{_SCHEME}{_HF_HOST}/spaces/(?P<id>[^/?#]+/[^/?#]+)/?{_TAIL}$", re.IGNORECASE
)
# Model: /<owner>/<name>(予約セグメントでない owner)。resolve/blob/tree 等のサブパスは拒否。
_MODEL_RE = re.compile(
    rf"^{_SCHEME}{_HF_HOST}/(?P<owner>[^/?#]+)/(?P<name>[^/?#]+)/?{_TAIL}$", re.IGNORECASE
)


@dataclass(frozen=True)
class HuggingFaceRef:
    """正規化済みの Hugging Face 参照。"""

    kind: Literal["paper", "model", "dataset", "space"]
    external_id: str


@dataclass(frozen=True)
class DiscoveredResource:
    """Paper API から導出した関連ソース候補(1 リンク)。"""

    url: str
    kind: Literal["github", "huggingface", "project"]
    relation: str
    title: str
    official_candidate: bool
    meta: dict[str, object] = field(default_factory=dict)


def parse_huggingface_url(raw: str) -> HuggingFaceRef | None:
    """Hugging Face の Paper / Model / Dataset / Space URL を ``HuggingFaceRef`` に解決する。

    ``huggingface.co`` と短縮 ``hf.co`` だけを許可し、org page(``/<org>`` 単体)・collection・
    settings・resolve などの非取り込み URL は ``None`` を返す。
    """
    s = (raw or "").strip()
    if not s:
        return None
    normalized = s if "://" in s else "https://" + s

    if m := _PAPER_RE.match(normalized):
        return HuggingFaceRef(kind="paper", external_id=m.group("id"))
    if m := _SPACE_RE.match(normalized):
        return HuggingFaceRef(kind="space", external_id=m.group("id"))
    if m := _DATASET_RE.match(normalized):
        return HuggingFaceRef(kind="dataset", external_id=m.group("id"))
    if m := _MODEL_RE.match(normalized):
        owner = m.group("owner")
        # 予約セグメント(datasets/spaces/collections/settings/...)は Model ではない。
        if owner.lower() in _RESERVED_TOP:
            return None
        return HuggingFaceRef(kind="model", external_id=f"{owner}/{m.group('name')}")
    return None


def normalize_candidate_url(url: str) -> str:
    """候補 URL の重複排除キー(scheme・www・末尾スラッシュ・大小を無視した弱い正規化)。"""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/").lower()
    return f"{host}{path}"


def arxiv_id_from_tags(tags: list[str] | None) -> str | None:
    """``arxiv:<ID>`` タグ列から arXiv ID を一意に決まる場合だけ返す。

    0 件・複数の相異なる ID は自動決定せず ``None`` を返す(選択可能な診断へ委ねる)。
    """
    if not tags:
        return None
    ids: set[str] = set()
    for tag in tags:
        if isinstance(tag, str) and tag.lower().startswith("arxiv:"):
            value = tag.split(":", 1)[1].strip()
            if value:
                ids.add(value)
    if len(ids) == 1:
        return next(iter(ids))
    return None


# 上限(設計 §2 の表)。
_MAX_MODELS = 5
_MAX_DATASETS = 3
_MAX_SPACES = 3
_MAX_TOTAL = 13


def _linked_id(item: object) -> str | None:
    if isinstance(item, str):
        return item or None
    if isinstance(item, dict):
        val = item.get("id") or item.get("modelId") or item.get("name")
        return str(val) if val else None
    return None


def _sorted_linked(items: object, *, sort_key: str, limit: int) -> list[dict[str, object]]:
    """linked artifacts を降順ソートして上位 ``limit`` 件の {id, meta} を返す。"""
    if not isinstance(items, list):
        return []
    rows: list[dict[str, object]] = []
    for item in items:
        rid = _linked_id(item)
        if rid is None:
            continue
        meta = item if isinstance(item, dict) else {}
        rank = meta.get(sort_key, 0)
        rank_num = rank if isinstance(rank, int | float) else 0
        rows.append({"id": rid, "meta": dict(meta), "rank": rank_num})
    rows.sort(key=lambda r: r["rank"], reverse=True)  # type: ignore[arg-type,return-value]
    return rows[:limit]


def discover_paper_resources(
    payload: dict[str, object], *, arxiv_id: str | None = None
) -> list[DiscoveredResource]:
    """Paper API ペイロード → 関連ソース候補の列(設計 §2)。

    生成順は Paper Page → githubRepo → projectPage → linkedModels(≤5)→ linkedDatasets(≤3)→
    linkedSpaces(≤3)。最大 13 件で、正規化 URL による重複排除を行う。paper-level の
    ``githubRepo`` / ``projectPage`` だけを official candidate にする。
    """
    resources: list[DiscoveredResource] = []
    seen: set[str] = set()

    def add(res: DiscoveredResource) -> None:
        if len(resources) >= _MAX_TOTAL:
            return
        key = normalize_candidate_url(res.url)
        if key in seen:
            return
        seen.add(key)
        resources.append(res)

    paper_id = str(payload.get("id") or arxiv_id or "").strip()

    # 1) Hugging Face Paper Page(候補として提示。official ではない)。
    if paper_id:
        add(
            DiscoveredResource(
                url=f"{_BASE}/papers/{paper_id}",
                kind="huggingface",
                relation="paper",
                title=str(payload.get("title") or f"Hugging Face Paper {paper_id}"),
                official_candidate=False,
                meta={"repo_type": "paper", "repo_id": paper_id},
            )
        )

    # 2) githubRepo(公式候補)。
    github = payload.get("githubRepo")
    if isinstance(github, str) and github.strip():
        add(
            DiscoveredResource(
                url=github.strip(),
                kind="github",
                relation="github",
                title=github.strip(),
                official_candidate=True,
                meta={},
            )
        )

    # 3) projectPage(公式候補)。
    project = payload.get("projectPage")
    if isinstance(project, str) and project.strip():
        add(
            DiscoveredResource(
                url=project.strip(),
                kind="project",
                relation="project",
                title=project.strip(),
                official_candidate=True,
                meta={},
            )
        )

    # 4) linkedModels(downloads 降順・≤5)。
    for row in _sorted_linked(payload.get("linkedModels"), sort_key="downloads", limit=_MAX_MODELS):
        rid = str(row["id"])
        meta = row["meta"] if isinstance(row["meta"], dict) else {}
        add(
            DiscoveredResource(
                url=f"{_BASE}/{rid}",
                kind="huggingface",
                relation="model",
                title=rid,
                official_candidate=False,
                meta={
                    "repo_type": "model",
                    "repo_id": rid,
                    "downloads": meta.get("downloads"),
                    "likes": meta.get("likes"),
                    "pipeline_tag": meta.get("pipeline_tag"),
                },
            )
        )

    # 5) linkedDatasets(downloads 降順・≤3)。
    for row in _sorted_linked(
        payload.get("linkedDatasets"), sort_key="downloads", limit=_MAX_DATASETS
    ):
        rid = str(row["id"])
        meta = row["meta"] if isinstance(row["meta"], dict) else {}
        add(
            DiscoveredResource(
                url=f"{_BASE}/datasets/{rid}",
                kind="huggingface",
                relation="dataset",
                title=rid,
                official_candidate=False,
                meta={"repo_type": "dataset", "repo_id": rid, "downloads": meta.get("downloads")},
            )
        )

    # 6) linkedSpaces(likes 降順・≤3)。
    for row in _sorted_linked(payload.get("linkedSpaces"), sort_key="likes", limit=_MAX_SPACES):
        rid = str(row["id"])
        meta = row["meta"] if isinstance(row["meta"], dict) else {}
        add(
            DiscoveredResource(
                url=f"{_BASE}/spaces/{rid}",
                kind="huggingface",
                relation="space",
                title=rid,
                official_candidate=False,
                meta={"repo_type": "space", "repo_id": rid, "likes": meta.get("likes")},
            )
        )

    return resources


class HuggingFaceAdapter:
    """Hugging Face の検出・URL ビルダ(:class:`SiteAdapter` 実装)。

    landing HTML → SiteMeta 写像は行わず(Paper 本文は arXiv パイプラインが担う)、registry 解決と
    SSRF allow-list(``adapter_allowed_hosts`` → ``{"huggingface.co"}``)のためだけに実装する。
    """

    site = _SITE

    def match(self, url: str) -> SiteRef | None:
        ref = parse_huggingface_url(url)
        if ref is None:
            return None
        return SiteRef(site=_SITE, external_id=ref.external_id)

    def landing_url(self, ref: SiteRef) -> str:
        # allow-list 導出用に正規ホストの URL を返す(external_id そのままの参照ページ)。
        return f"{_BASE}/papers/{ref.external_id}"

    def pdf_url(self, ref: SiteRef) -> str | None:
        # Hugging Face から本文 PDF は取得しない(arXiv パイプライン経由)。
        return None

    def parse_metadata(self, html: str, ref: SiteRef) -> SiteMeta:  # pragma: no cover - 未使用
        raise SiteFetchError(
            "source_not_found", "Hugging Face body is fetched via the arXiv pipeline"
        )


@dataclass
class HuggingFaceConfig:
    """Hugging Face 公開 Hub API の設定(テストで base URL を差し替える)。"""

    base_url: str = _BASE

    @classmethod
    def from_settings(cls, settings: CoreSettings) -> HuggingFaceConfig:
        base = getattr(settings, "alinea_huggingface_base_url", "") or _BASE
        return cls(base_url=base)


_HF_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_MAX_HF_JSON_BYTES = 4 * 1024 * 1024


@dataclass
class HuggingFaceClient:
    """公開 Hub API の副作用ラッパ(設定可能 base URL・SiteFetchError 分類付き)。

    - ``GET /api/papers/{arxiv_id}`` → Paper メタ(関連リンク含む)。
    - ``GET /api/{models|datasets|spaces}/{repo_id}`` → repo メタ(``arxiv:<ID>`` タグ取得)。

    401/403/404/429 は既存の provider error 分類(:class:`SiteFetchError`)へ変換する。
    rate-limit reset までの再試行抑止は上位層(worker/API)が担う。
    """

    config: HuggingFaceConfig = field(default_factory=HuggingFaceConfig)
    client: httpx.AsyncClient | None = None

    async def _get_json(self, path: str) -> dict[str, object]:
        owns = self.client is None
        http = self.client or httpx.AsyncClient()
        url = f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            try:
                resp = await http.get(url, timeout=_HF_TIMEOUT)
            except httpx.HTTPError as exc:
                raise SiteFetchError(
                    "network_error", f"Hugging Face request failed: {exc}"
                ) from exc
            status = resp.status_code
            if status == 429:
                raise SiteFetchError("rate_limited", "Hugging Face returned 429")
            if status in (401, 403):
                raise SiteFetchError("source_not_found", f"Hugging Face returned {status}")
            if status == 404:
                raise SiteFetchError("source_not_found", "Hugging Face returned 404")
            if status >= 500:
                raise SiteFetchError("upstream_5xx", f"Hugging Face returned {status}")
            if status != 200:
                raise SiteFetchError("source_not_found", f"Hugging Face returned {status}")
            data = resp.content
            if len(data) > _MAX_HF_JSON_BYTES:
                raise SiteFetchError("source_too_large", "Hugging Face response exceeds size limit")
            try:
                payload = resp.json()
            except ValueError as exc:
                raise SiteFetchError(
                    "source_not_found", "Hugging Face returned invalid JSON"
                ) from exc
            if not isinstance(payload, dict):
                raise SiteFetchError("source_not_found", "Hugging Face returned non-object JSON")
            return payload
        finally:
            if owns:
                await http.aclose()

    async def fetch_paper(self, arxiv_id: str) -> dict[str, object]:
        """``GET /api/papers/{arxiv_id}`` を取得する。"""
        return await self._get_json(f"api/papers/{arxiv_id}")

    async def fetch_repo_tags(
        self, kind: Literal["model", "dataset", "space"], repo_id: str
    ) -> list[str]:
        """Model / Dataset / Space repo の ``tags`` を取得する(``arxiv:<ID>`` 抽出用)。"""
        segment = {"model": "models", "dataset": "datasets", "space": "spaces"}[kind]
        payload = await self._get_json(f"api/{segment}/{repo_id}")
        tags = payload.get("tags")
        if isinstance(tags, list):
            return [str(t) for t in tags if t is not None]
        return []


__all__ = [
    "DiscoveredResource",
    "HuggingFaceAdapter",
    "HuggingFaceClient",
    "HuggingFaceConfig",
    "HuggingFaceRef",
    "arxiv_id_from_tags",
    "discover_paper_resources",
    "normalize_candidate_url",
    "parse_huggingface_url",
]
