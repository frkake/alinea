# 他サイトアダプタ 実装計画 — S8 / M3

設計: `docs/superpowers/specs/2026-07-16-other-site-adapters-design.md`

## Global Constraints

- 既存 arXiv パイプラインを変更しない(回帰ゼロ)。`arxiv/` パッケージには触れない。
- 依存追加禁止。`httpx`(取得)・`selectolax`(HTML)・stdlib のみ。
- テストは実ネットワーク非依存。純粋コアは fixture、統合は starlette ASGI スタブ。
- Python は 3.12、型は厳格(`from __future__ import annotations`、`X | None`)。
- 全型・関数コメントは既存モジュールの日本語コメント様式に合わせる。
- 検証コマンド: `uv run pytest apps/worker apps/api -q`(統合)+ `uv run pytest packages/py-core -q`(純粋コア)。

## File Structure

新規(本スライスで実装 = フェーズ1):
- `packages/py-core/src/alinea_core/adapters/__init__.py`
- `packages/py-core/src/alinea_core/adapters/base.py`
- `packages/py-core/src/alinea_core/adapters/citation_meta.py`
- `packages/py-core/src/alinea_core/adapters/acl_anthology.py`
- `packages/py-core/src/alinea_core/adapters/registry.py`
- `packages/py-core/tests/test_site_adapters.py`
- `packages/py-core/tests/fixtures/acl_anthology_landing.html`

計画のみ(未実装 = フェーズ2/3):
- `packages/py-core/src/alinea_core/adapters/fetch.py`(HTTP クライアント)
- `packages/py-core/src/alinea_core/adapters/openreview.py`, `pubmed.py`
- `apps/api` の site 取り込みエンドポイント、`apps/worker` の `source="site"` 分岐

---

## Task 1: 純粋コア — SiteRef/SiteMeta/Adapter プロトコル + citation_meta + ACL アダプタ + registry(本スライス)

TDD。まず `packages/py-core/tests/test_site_adapters.py` に失敗するテストを書き、次に実装。

- [ ] **Step 1: fixture を保存** — `packages/py-core/tests/fixtures/acl_anthology_landing.html`。
  実際の ACL landing の `<head>` を模した最小 HTML。`<meta name="citation_title">`,
  複数 `<meta name="citation_author">`(値 "Last, First")、`citation_publication_date`(YYYY 又は
  YYYY/MM/DD)、`citation_conference_title`、`citation_pdf_url`、`citation_doi`、`citation_abstract`
  を含める。既存 arXiv fixture(`rectified_flow_arxiv.html`)と同じ流儀で最小・決定的に。

- [ ] **Step 2: 失敗するテストを書く** — `test_site_adapters.py`:
  - `test_extract_citation_meta_from_fixture`: fixture を読み `extract_citation_meta(html)` の各
    フィールド(title/authors 複数/date/venue/pdf_url/doi/abstract)を検証。
  - `test_normalize_scholar_author`: `"Devlin, Jacob" → "Jacob Devlin"`、単一トークンはそのまま。
  - `test_acl_match_valid`: `aclanthology.org/2023.acl-long.123/`,
    `.../2023.acl-long.123`, `.../2023.acl-long.123.pdf`, `https://` 有無, 旧式 `.../P19-1001/`
    が全て `SiteRef(site="acl_anthology", external_id=...)` に解決。
  - `test_acl_match_invalid`: `aclanthology.org/volumes/2023.acl-long/`(volumes 除外),
    arXiv URL, 素の文字列 → `None`。
  - `test_acl_parse_metadata`: fixture + 解決した ref → `SiteMeta`(pdf_url/venue/doi 正しい)。
  - `test_acl_url_builders`: `pdf_url(ref)`==`https://aclanthology.org/2023.acl-long.123.pdf`,
    `landing_url(ref)` 末尾スラッシュ。
  - `test_resolve_adapter`: ACL URL→(AclAnthologyAdapter, ref)、arXiv・未対応→None。

- [ ] **Step 3: テストが失敗することを確認** — `uv run pytest packages/py-core/tests/test_site_adapters.py -q`。

- [ ] **Step 4: 実装** — spec の層1 のとおり `base.py`(dataclass + Protocol)、
  `citation_meta.py`(`extract_citation_meta` / `normalize_scholar_author`。`selectolax` の
  `LexborHTMLParser` で `meta[name^=citation_]` を走査、name→値リスト辞書に集約)、
  `acl_anthology.py`(regex で URL 検出 + `extract_citation_meta` を `SiteMeta` に写像)、
  `registry.py`(順序付き `resolve_adapter`)、`__init__.py`(re-export)。

- [ ] **Step 5: 緑を確認 + lint/type** — `uv run pytest packages/py-core/tests/test_site_adapters.py -q`
  と `uv run ruff check packages/py-core/src/alinea_core/adapters` + `uv run mypy`(リポジトリ設定に従う)。

- [ ] **Step 6: commit** — `feat(core): site adapter abstraction + ACL Anthology adapter (S8 phase 1)`。

---

## Task 2: 汎用サイト HTTP クライアント `adapters/fetch.py`(計画のみ)

- `make_site_client(settings)`: `make_arxiv_client` を汎用化(User-Agent 付き httpx)。
- `fetch_landing_html` / `fetch_pdf`: `read_bounded_http_body` + `MAX_ARXIV_PDF_BYTES` 相当の上限。
- `settings.py` に `alinea_acl_base_url`(既定 "")を追加(E2E スタブ差し替え用)。
- テスト: starlette ASGI スタブ(ACL landing/PDF を返す)+ `httpx.ASGITransport`。

---

## Task 3: API 取り込み統合(フェーズ 3a・計画のみ)

- `ingest_check`(routers/ingest.py): `parse_arxiv_url` が None のとき `resolve_adapter(url)` を試し、
  当たれば `kind="site"` + 書誌プレビュー(landing 取得→`parse_metadata`)を返す新レスポンス枝。
  拡張は `kind==="site"` で保存ボタンを出す。
- `POST /api/ingest/site`(新規、`ingest_arxiv`/`ingest_pdf` に倣う): `resolve_adapter` →
  `parse_metadata` → PDF をサーバ取得し S3 保存(`_ensure_arxiv_pdf_available` と同形、
  `SourceAsset.kind="pdf"`, `source_url=landing_url`)→ プレースホルダ revision → 重複判定は
  `doi` 優先(なければ `pdf_sha256`)→ ジョブ `source="site"` で enqueue。
- 実メタで `Paper.title/authors/abstract/venue/doi/license` を埋める。共有可否は spec の
  ライセンスマトリクスに従う(既定 private・unknown)。
- テスト: `apps/api/tests/test_ingest_site.py`。ASGI スタブでネットワーク非依存。

---

## Task 4: worker `source="site"` 分岐(フェーズ 3a・計画のみ)

- `IngestJobPayload.source` の許容に `"site"` を追加。
- `is_pdf_upload` を `is_local_pdf = self.payload.source in {"pdf_upload", "site"}` に一般化
  (arXiv 経路の判定は不変)。`_stage_fetching` は site も「S3 の原本 PDF 存在確認のみ」。
- `_stage_parse_and_structure` は site も PDF(品質 B)候補のみ。`source_format` は DB で `"pdf"`。
- joblog `fetch_timeline_message` に site 用メッセージを追加。
- テスト: `test_pdf_upload_pipeline.py` を site 用に複製し緑にする。

---

## Task 5: OpenReview アダプタ(計画のみ)

- `adapters/openreview.py`: `match`(`openreview.net/forum?id=` / `/pdf?id=`)、
  `pdf_url`=`openreview.net/pdf?id=<id>`。メタは forum ページの `citation_*`(citation_meta 再利用)、
  または公開 REST `api2.openreview.net/notes?id=<id>` の JSON 写像(`fetch_openreview_note`)。
- registry に追加。API/worker はフェーズ 3a をそのまま流用(PDF 品質 B)。

---

## Task 6: PubMed/PMC アダプタ + JATS 品質 A(フェーズ 3b・計画のみ)

- `adapters/pubmed.py`: `match`(`pubmed.ncbi.nlm.nih.gov/<PMID>` / `pmc/articles/PMC<PMCID>`)。
- OA サブセットは JATS 全文 XML を取得 → 新規 `parse_jats_candidate`(JATS→`DocumentContent`)。
- `SourceCandidate.source_format` の `Literal` に `"jats"` 追加 + `ck_document_revisions_format` を
  広げる Alembic マイグレーション。`IngestRun` の provider 抽象化(spec フェーズ 3b)が前提。
- E-utilities は Redis スロットル(3req/s)。非 OA は abstract のみ(本文取得不可→タブ内直送を促す)。

---

## 未決事項(要ユーザー判断) — Task 3 着手前に確認

1. サイト取り込み論文の既定 visibility(private 安全側 vs ACL/OpenReview は public 共有許可)。
2. サイト別 DB provenance を明示するため `SourceAsset.kind="site_pdf"` を足すか、`"pdf"` 再利用か。
3. アダプタ実装の優先順位(推奨: ACL → OpenReview → PubMed)。
4. PMC JATS 品質 A(フェーズ 3b)を M3 スコープに含めるか、PDF 品質 B で全サイト統一するか。
```
