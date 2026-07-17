# 他サイトアダプタ (OpenReview / ACL Anthology / PubMed) 設計 — S8 / M3

> docs/02-ingest.md §8「将来の対応サイト(アダプタ方針)」と docs/10-roadmap.md §5 M3
> 「他サイトアダプタ」を実装可能な設計に落とす。取り込みは現状 arXiv 専用。本設計は
> 「検出 + メタデータ取得 + 本文取得」を担うサイトアダプタ抽象を導入し、拡張の自動検出
> 対象を広げつつ、既存 arXiv パイプラインを一切壊さないことを最優先とする。

## 背景と現状(調査結果)

取り込みは `IngestRun`(`apps/worker/src/alinea_worker/pipeline.py`)の 8 段ステートマシン
`queued → fetching → parsing → structuring → translating_abstract → readable →
translating_body → complete` で駆動される。現状は **深く arXiv に結合** している:

- `IngestRun.__init__`(pipeline.py:934): `self.ref = normalize_arxiv_id(url or arxiv_id)`。
- `_stage_fetching`(pipeline.py:1164): arXiv Atom API で `fetch_metadata`、原本 PDF を arXiv から取得。
- `_select_source_candidate`(pipeline.py:2507): 候補を固定順 `("latex", "arxiv_html", "pdf")` で試す。
- ジョブ payload の `source` は現在 `"arxiv" | "pdf_upload"` の 2 値のみ(pipeline.py:553)。
- `SourceCandidate.source_format` は `Literal["latex", "arxiv_html", "pdf"]`(source_candidates.py:196)。
- DB CHECK `ck_document_revisions_format` = `('latex','arxiv_html','pdf')`(0001 migration)。
- `ck_source_assets_kind` = `('arxiv_latex','arxiv_html','pdf','translated_pdf','bilingual_pdf','metadata_api','extension_capture','latex_project_manifest')`。
- API の URL 分類は `ingest_check`(routers/ingest.py:143)が `parse_arxiv_url` の成否で `arxiv`/`pdf`/`unsupported` を返す。
- 重要な既存パターン: `pdf_upload`(拡張のタブ内 PDF 直送)は **新しい parser を足さず**、
  「メタデータをローカル推定 + PDF を S3 に置いてから PDF パイプライン(品質 B)へ流す」経路。
  worker では `source_format="pdf_upload"` を DB では `"pdf"` に写像する(pipeline.py:3004,3030)。

**この `pdf_upload` 経路こそが他サイトアダプタの最小変更テンプレートになる。** ACL / OpenReview /
PMC の論文本文は多くが PDF 提供であり、既存 PDF(品質 B)パイプラインをそのまま再利用できる。

対象サイトの公開メタデータ事情(調査):

| サイト | URL 例 | メタデータ | 本文 | 認証/レート | 品質見込み |
|---|---|---|---|---|---|
| **ACL Anthology** | `aclanthology.org/2023.acl-long.123/` | 静的 HTML の Highwire/Google Scholar `<meta name="citation_*">` タグ + `<id>.bib` | `<id>.pdf`(直リンク・静的) | 認証なし・実質レート制限なし(CDN 静的) | **B**(PDF) |
| **OpenReview** | `openreview.net/forum?id=XXXX` | 公開 REST(`api2.openreview.net/notes?id=`)JSON + forum ページの `citation_*` メタ | `openreview.net/pdf?id=XXXX` | 採録論文の書誌/PDF は公開。レビューは一部認証。緩いレート制限 | **B**(PDF) |
| **PubMed / PMC** | `pubmed.ncbi.nlm.nih.gov/PMID/`, `ncbi.nlm.nih.gov/pmc/articles/PMCID/` | PubMed E-utilities / PMC 記事ページの `citation_*` メタ | PMC OA サブセットのみ **JATS 全文 XML**(`.../PMCID/`) | 認証なし。E-utilities は 3req/s(API キーで 10) | OA は **A 候補**(JATS)、非 OA は abstract のみ |

## 目標

1. **サイトアダプタ抽象**を `packages/py-core/src/alinea_core/adapters/` に新設し、URL 検出・
   メタデータ写像・最良ソース(PDF/将来は HTML/XML)解決を各サイトが差し込める形にする。
2. arXiv パイプラインを一切変更しない(回帰ゼロ)。arXiv は既存 `arxiv/` パッケージのまま。
3. 最初の 1 アダプタ(**ACL Anthology**)を TDD で縦に通す。まずは純粋コア(URL 検出 +
   `citation_*` メタ解析)を fixture 駆動・ネットワーク非依存で実装する。
4. 残り(worker/API 統合、OpenReview、PMC)は計画に落として未実装で残す。
5. 依存追加なし(既存 `httpx` / `selectolax` を再利用)。

## 非目標

- `IngestRun` の完全な source 非依存リファクタ(PMC JATS→品質 A の provider 化)は本スライス外。
  設計だけ「フェーズ2」として示す。
- 拡張(Chrome/Edge)側 UI の実装。検出対象拡張の contentScript マッチャは計画に記すのみ。
- 認証必須ページの取得(docs/02 §8 のとおり、認証ページは拡張のタブ内 PDF 直送で受ける)。

## アーキテクチャ

### 層1: 純粋コア — `alinea_core.adapters`(本スライスで実装)

arXiv の `ids.py`(URL 正規化)+ `metadata.py`(Atom 写像)に対応する、サイト非依存の
純粋モジュール群。ネットワークに触れない = fixture で完全テスト可能。

```
packages/py-core/src/alinea_core/adapters/
  __init__.py          # 公開 API の re-export
  base.py              # SiteRef / SiteMeta / SiteAdapter プロトコル
  citation_meta.py     # Highwire/Google Scholar <meta name="citation_*"> 汎用抽出
  acl_anthology.py     # 最初のアダプタ(URL 検出 + メタ写像 + URL ビルダ)
  registry.py          # resolve_adapter(url) 順序付き解決
```

**`base.py`** — 共有型:

```python
@dataclass(frozen=True)
class SiteRef:
    """正規化済みのサイト別論文参照。arXiv の ArxivId に対応。"""
    site: str            # "acl_anthology" | "openreview" | "pubmed" | ...
    external_id: str     # サイト内の一意 ID(例 "2023.acl-long.123")
    version: str | None = None

@dataclass(frozen=True)
class SiteMeta:
    """papers 投入用に正規化したサイトメタデータ。ArxivMeta の汎用版。"""
    site: str
    external_id: str
    title: str
    authors: list[dict[str, str]]      # [{"name": "..."}] — Paper.authors と同型
    abstract: str
    published_on: str | None           # ISO 日付 or 年頭 "YYYY-01-01"
    venue: str | None
    doi: str | None
    license: LicenseId                 # 既定 "unknown"
    pdf_url: str | None                # 本文 PDF 直リンク(取得元)
    categories: list[str]              # 提案タグ材料(なければ空)

class SiteAdapter(Protocol):
    site: str
    def match(self, url: str) -> SiteRef | None: ...        # 純粋。URL 検出
    def parse_metadata(self, html: str, ref: SiteRef) -> SiteMeta: ...  # 純粋。HTML→SiteMeta
    def landing_url(self, ref: SiteRef) -> str: ...
    def pdf_url(self, ref: SiteRef) -> str | None: ...
```

**`citation_meta.py`** — 最大の再利用資産。Highwire Press / Google Scholar の
`<meta name="citation_*">` は ACL・OpenReview・PMC・主要出版社が横断的に出す事実上の標準。
`selectolax` で `citation_title` / `citation_author`(複数)/ `citation_publication_date` /
`citation_conference_title` | `citation_journal_title` / `citation_pdf_url` / `citation_doi` /
`citation_abstract` を辞書に集約する汎用関数 `extract_citation_meta(html) -> CitationMeta` を提供。
各アダプタはこの共通抽出結果を `SiteMeta` に写すだけで済む。著者名は "Last, First" 形式が多いので
`normalize_scholar_author("Last, First") -> "First Last"` を用意。

**`acl_anthology.py`** — 最初のアダプタ:
- `match`: `aclanthology.org/<id>[/|.pdf]` を検出。ID は現行 `YYYY.venue-type.NNN` と
  旧式 `[A-Z]\d{2}-\d{4}`(例 `P19-1001`)。volumes ページ(`/volumes/...`)は非対象。
- `parse_metadata`: `extract_citation_meta` の結果を `SiteMeta` に写像。DOI は
  `citation_doi`(ACL は `10.18653/v1/...`)。venue は `citation_conference_title`。
- `pdf_url`: `https://aclanthology.org/<id>.pdf`。`landing_url`: `.../<id>/`。

**`registry.py`** — `resolve_adapter(url) -> tuple[SiteAdapter, SiteRef] | None`。
順序付きリスト `[AclAnthologyAdapter(), OpenReviewAdapter(), PubmedAdapter()]` を順に `match`。
arXiv は既存 `parse_arxiv_url` が先に処理する(registry には含めない)ため衝突しない。

### 層2: サイト HTTP クライアント(計画のみ)

arXiv の `fetch.py`(`make_arxiv_client` + throttle)に対応。サイトごとに
`alinea_core/<site>/fetch.py` を作らず、**汎用 `adapters/fetch.py`** に集約する:
- `make_site_client(settings)`: `User-Agent` 付き `httpx.AsyncClient`(既定 `arxiv_user_agent` を
  汎用化した `alinea_user_agent`)。
- `fetch_landing_html(client, url)` / `fetch_pdf(client, url, max_bytes)`:境界付き取得
  (`read_bounded_http_body` を再利用)。
- OpenReview REST は `fetch_openreview_note(client, id)`(JSON)を別途持つ。
- 設定は `alinea_<site>_base_url`(E2E/CI 上書き用。空文字=実サイト)を各サイト分追加。
  レート制御: PMC E-utilities のみ Redis スロットル(arXiv の `arxiv_throttle` 汎用版)を通す。

### 層3: パイプライン統合(計画のみ・2 段構え)

**フェーズ 3a(推奨・最小変更): 「サイト = メタデータ付き pdf_upload」**

`pdf_upload` 経路がそのまま使える。API で完結させ、worker の arXiv 状態機械は触らない:

1. `POST /api/ingest/site`(新規)または `ingest_check`/`ingest_arxiv` を一般化。拡張は URL を送る。
2. API 側で `resolve_adapter(url)` → landing HTML 取得 → `parse_metadata` → `SiteMeta`。
3. `SiteMeta.pdf_url` から PDF をサーバ取得し S3 へ保存(`_ensure_arxiv_pdf_available` と同形。
   `SourceAsset.kind="pdf"`、`source_url=landing_url`)。プレースホルダ revision も同様に用意。
4. `Paper` を作成/UPSERT: `doi`(あれば)で重複判定、`title`/`authors`/`abstract`/`venue`/
   `license` を **推定でなく実メタで** 埋める(pdf_upload と違い「書誌は推定」バッジは出さない)。
5. ジョブを `source="site"` で enqueue。worker では `source="site"` を `pdf_upload` と同じ
   「原本 PDF 存在確認 → PDF 候補(品質 B)」経路に流す(`is_pdf_upload` 相当のフラグを
   `is_local_pdf = source in {"pdf_upload","site"}` に一般化)。`source_format` は DB で `"pdf"`。

この経路の DB 影響は最小: `ck_document_revisions_format` は `"pdf"` を再利用するので **変更不要**。
`SourceAsset.kind` は `"pdf"` を再利用可(`source_url` で出所を保持)。provenance を明示したい
場合のみ `site_pdf` kind を足すマイグレーションを追加(任意)。

**フェーズ 3b(将来・品質 A 対応): source 非依存 provider**

PMC OA の JATS 全文や、将来のサイト HTML 全文を品質 A で取り込むには、`IngestRun` の
「fetching + 候補選定」を provider 抽象へ切り出す:

```python
class SourceProvider(Protocol):
    async def resolve_metadata(self, ...) -> PaperMetaLike: ...
    def ordered_candidates(self) -> list[CandidateFetcher]: ...  # arXiv=[latex,html,pdf], acl=[pdf], pmc=[jats,pdf]
```

`ArxivProvider` は現行挙動を保存。`SourceCandidate.source_format` の `Literal` に `"jats"` を、
`ck_document_revisions_format` に `"jats"` を足すマイグレーションが必要。新規 `parse_jats_candidate`
(JATS XML → `DocumentContent`)を追加。これは大きいので M3 後半に分離する。

## エラーハンドリングと P3(黙って壊れない)

- URL 非対応 → `resolve_adapter` は `None`。`ingest_check` は `unsupported`(現行と同じ)。
- landing HTML にメタが無い/PDF リンク欠落 → `SiteMeta.pdf_url is None`。API は `provider_error`
  を返し拡張に「このタブの PDF を送信」フォールバック(タブ内直送)を促す(docs/02 §8)。
- PDF 取得失敗は arXiv と同じ `FetchError` 分類(`source_not_found`/`source_too_large`/`network_error`)。
- 認証必須(OpenReview の未公開・出版社ペイウォール)→ 取得 403/JSON 空 → 上と同じフォールバック。

## セキュリティ / ライセンス / 共有

- 取得 HTML は準信頼。`citation_*` メタは `selectolax` で属性値のみ読む(スクリプト実行なし)。
  抽出値は既存 `sanitize_untrusted_text` で無害化してから Paper に載せる。
- **ライセンス/共有可否は要ユーザー判断**(下記)。docs/02 §8 は「出版社コンテンツは private」
  とするが、ACL Anthology(CC BY 4.0)・OpenReview(オープンレビュー)はオープンアクセス。
  既定は安全側 `license="unknown"` + `visibility="private"` とし、明示的にオープンと確認できた
  サイト(ACL の CC BY)だけ public 共有を許すマトリクスを設定で持つのが安全。

## テスト方針

- 純粋コア(本スライス): `packages/py-core/tests/test_site_adapters.py`。
  - `match`: 有効/無効 URL の直積(trailing slash・`.pdf`・旧式 ID・volumes 除外・非 ACL)。
  - `parse_metadata`: 保存した ACL landing fixture(`tests/fixtures/acl_anthology_landing.html`)
    から title/authors/abstract/venue/doi/pdf_url を検証。ネットワーク非依存。
  - `extract_citation_meta`: 汎用抽出の単体(複数 author・欠落フィールド)。
  - `resolve_adapter`: ACL URL→AclAnthologyAdapter、arXiv/未対応→None。
- 統合(計画のみ): worker は既存 `conftest._make_arxiv_stub` に倣い、サイト landing/PDF を
  返す starlette ASGI スタブ + `httpx.ASGITransport` を追加(実通信なし)。`ingest` は pdf_upload
  パイプラインの既存テスト(`test_pdf_upload_pipeline.py`)を site 用に複製して緑にする。

## 優先順位の推奨

**最初に実装すべきは ACL Anthology。** 理由:
1. 静的サイト・認証なし・実質レート制限なしで、URL→PDF/bib が完全に予測可能。
2. landing ページが Highwire/Scholar `citation_*` メタを綺麗に出す。ここで作る汎用
   `citation_meta.py` は **OpenReview forum・PMC・主要出版社にそのまま再利用** でき、後続アダプタの
   限界コストを大幅に下げる(最大の投資対効果)。
3. 本文が PDF のみ → 既存 PDF(品質 B)パイプラインを無改造で再利用でき、統合リスク最小。

2 番目は **OpenReview**(公開 REST が綺麗、PDF 品質 B、ICLR/NeurIPS カバレッジが高い)。
3 番目 **PubMed/PMC**(OA は JATS 全文=品質 A の価値が高いが、フェーズ 3b の provider 化が要る)。
```
