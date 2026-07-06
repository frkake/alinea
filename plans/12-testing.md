# 12. テスト戦略 実装計画

> 対象読者と前提
> 本書は「訳読 / YAKUDOKU — 論文読解ワークベンチ」の全レイヤ(apps/api / apps/worker / packages/py-core / packages/llm / apps/web / apps/extension / packages/tokens / packages/api-client)のテスト戦略の完全定義である。機能仕様の正は docs/00〜12(各文書末尾の受け入れ基準チェックリスト)、実装の正は plans/00〜08 であり、本書はその両者を「どのテストが・どの基準を・どう検証するか」に落とす。ツールバージョンは plans/00 §4.1(pytest 8.4.1 / pytest-asyncio 1.0.0 / hypothesis 6.135.26 / Vitest 3.2.4 / Playwright 1.53.2)、CI は plans/00 §8 の `ci.yml` を基盤とし、本書で追加分(VR・プロパティ・夜間ワークフロー)を確定する。テスト ID(`PY-*` / `HP-*` / `VT-*` / `PW-*` / `XT-*` / `VR-*` / `PF-*` / `SM-*` / `REV-*`)は §6 のトレーサビリティ表と実テストコードの docstring / `test.describe` 名で共通に使う。

## 1. 全体方針とレイヤ構成

### 1.1 レイヤ別方針(確定)

| レイヤ | ツール | 対象 | 実行コマンド | CI ジョブ(plans/00 §8) |
|---|---|---|---|---|
| Python ユニット | pytest 8.4.1(`-m unit`) | py-core / llm の純ロジック(パーサ・プレースホルダ・SVG レンダラ・ライセンス判定・SRS・URL 判定) | `uv run pytest -m unit` | `python` |
| Python プロパティ | pytest + hypothesis 6.135.26(`-m property`) | 翻訳プレースホルダ(§7)・SVG 決定性(§10)・アンカー表記導出 | `uv run pytest -m property` | `python` |
| Python 統合 | pytest(`-m integration`)+ 実 PostgreSQL 16(PGroonga)+ Redis 7 + MinIO | API ルータ(httpx `ASGITransport`)・ワーカータスク・DDL 制約・PGroonga 検索・ジョブ冪等性 | `uv run pytest -m integration` | `python` |
| LLM リプレイ | pytest(`-m replay`)+ vcrpy カセット(§8.2) | プロバイダアダプタの実ワイヤ形式(録画済み HTTP を再生) | `uv run pytest -m replay` | `python` |
| LLM 実 API スモーク | pytest(`-m smoke`、`RUN_LLM_SMOKE=1` 時のみ収集) | 各社実キーでの疎通・品質サンプル(§8.3) | `RUN_LLM_SMOKE=1 uv run pytest -m smoke` | 夜間 `llm-smoke.yml`(CI では skip) |
| JS ユニット/コンポーネント | Vitest 3.2.4 + @testing-library/react 16(jsdom 26) | apps/web コンポーネント・hooks・純関数、apps/extension ユニット、packages/tokens 生成検証 | `pnpm turbo test` | `js` |
| E2E | Playwright 1.53.2(Chromium・1440×900・ja-JP・Asia/Tokyo) | 主要フロー(§4)。LLM は §8.4 のモックサーバ | `pnpm --filter @yakudoku/web e2e` | `e2e` |
| 拡張 E2E | Playwright(`chromium.launchPersistentContext` + ビルド済み拡張ロード) | ポップアップ 4 状態・ピル・バッジ・送信キュー(§5) | `pnpm --filter @yakudoku/extension e2e` | `e2e` |
| ビジュアルリグレッション | Playwright `toHaveScreenshot`(専用 project `visual`) | 確定デザイン 16 画面(§9。うち 3a=拡張ポップアップは拡張 E2E 側で撮影) | `pnpm --filter @yakudoku/web e2e:vr`(3a のみ `pnpm --filter @yakudoku/extension e2e`) | `e2e` |
| パフォーマンス | k6 0.57.0 + Playwright トレース + 本番テレメトリ(plans/01 §9.4) | docs/09 §1 の 11 目標(§12) | 夜間 `perf.yml` | 夜間(CI ゲート外) |

- 決定: DB を使うテストは常に実 PostgreSQL 16 + PGroonga に対して実行する。SQLite 代替は禁止(PGroonga・部分一意インデックス・生成列・トリガが再現できないため。plans/00 §4.5 と同一決定)。
- 決定: モック境界は「外部ネットワークのみ」。LLM/画像 API・arXiv・GitHub/YouTube メタ取得はモック(§8)し、PostgreSQL / Redis / MinIO は本物を使う。自プロダクトのコード同士はモックしない(サービス層をモックしたルータテストは書かない)。
- 決定: フロントエンドの API モックは MSW 2.10 を使い、レスポンス fixture は packages/api-client の生成型(`paths`)で型検証する。手書き JSON が API 契約からずれたらコンパイルエラーで検出する。

### 1.2 テスト ID 体系

| 接頭辞 | 意味 | 定義場所 |
|---|---|---|
| `PY-<領域>-<連番>` | pytest(ユニット/統合)。領域: DB, AUTH, ING, PARSE, TR, GLS, CHAT, ANN, NOTE, LIB, COL, SHR, SRCH, NTF, SET, EXP, VOC, RES, ART, FIG, LIC, JOB, LLM | §2.4 |
| `HP-<連番>` | Hypothesis プロパティテスト | §7・§10 |
| `VT-<領域>-<連番>` | Vitest。領域: UI, VIEW, LIB, VOC, TOK, XTU(拡張ユニット) | §3 |
| `PW-<連番>` | Playwright E2E シナリオ | §4.3 |
| `XT-<連番>` | 拡張 Playwright E2E | §5.2 |
| `VR-<画面ID>` | ビジュアルリグレッション(16 画面) | §9.2 |
| `PF-<連番>` | パフォーマンス計測 | §12 |
| `SM-<連番>` | 実 API スモーク | §8.3 |
| `REV-<連番>` | 自動化対象外(文書・図の整合レビュー。PR チェックリスト運用) | §6.1 |

- 運用規則: テストコード側は pytest なら docstring 先頭、Playwright/Vitest なら `describe` 名にこの ID を含める(例 `test.describe("PW-08 チャット", …)`)。CI 失敗ログから §6 の表へ逆引きできる。

### 1.3 ディレクトリ配置(確定)

```
apps/api/tests/
  conftest.py                 # §2.2 の DB/クライアント fixture
  factories.py                # §2.3
  unit/                       # ルータを介さないサービス層ユニット
  integration/                # httpx ASGITransport 経由の API 統合(PY-AUTH/ING/LIB/…)
  cassettes/                  # vcr カセット(§8.2)
apps/worker/tests/
  conftest.py  unit/  integration/  cassettes/
packages/py-core/tests/
  unit/  property/            # HP-01〜05(プレースホルダ+SVG 決定性)、PY-PARSE、PY-FIG、PY-LIC
  golden/                     # 概要図ゴールデン SVG・KaTeX コーパス(§10・§11)
packages/llm/tests/           # plans/04 §17 のスイート(本書はそのまま採用)+ §8 の追補
apps/web/
  src/**/*.test.tsx           # Vitest(コンポーネント併置)
  e2e/
    global.setup.ts           # ログイン→storageState 保存(§4.2)
    specs/*.spec.ts           # PW-01〜22
    vr/*.spec.ts              # VR-*(§9)
    vr/__screenshots__/       # 基準画像(コミット対象)
    fixtures/                 # MSW ハンドラ・静的 arXiv ページ等
apps/extension/
  src/**/*.test.ts            # VT-XTU-*
  e2e/*.spec.ts               # XT-*
tools/perf/k6/*.js            # PF-*(§12)
```

## 2. pytest(apps/api・apps/worker・packages/py-core・packages/llm)

### 2.1 実行構成・マーカー

ルート `pyproject.toml` に確定:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["apps/api/tests", "apps/worker/tests", "packages/py-core/tests", "packages/llm/tests"]
markers = [
  "unit: DB・ネットワーク不要の純ロジック",
  "integration: 実 PostgreSQL/Redis/MinIO を使用",
  "property: hypothesis プロパティテスト",
  "replay: vcr カセット再生(録画時のみネットワーク)",
  "smoke: 実 LLM/画像 API(RUN_LLM_SMOKE=1 でのみ実行)",
]
addopts = "-ra --strict-markers"
```

- `smoke` は `conftest.py` の collection hook で `RUN_LLM_SMOKE != "1"` のとき `pytest.skip` する(CI では常に skip。§8.3)。
- カバレッジ: CI で `--cov=yakudoku_core --cov=yakudoku_api --cov=yakudoku_worker --cov=yakudoku_llm --cov-fail-under=80`。加えて重点 2 モジュールは専用ステップで行カバレッジ 100% を要求する: `yakudoku_core/translation/placeholder.py` と `yakudoku_core/figures/svg_renderer.py`(`coverage report --include=... --fail-under=100`)。理由: docs/03 §12(99.9%)と docs/09 §8(バイト同一)はこの 2 モジュールの正しさに直結する。

### 2.2 テスト DB・フィクスチャ方針(確定)

- **テスト DB**: `DATABASE_URL` の DB 名に `_test` を付けた `yakudoku_test` を使う。セッション開始時に DROP → CREATE → `CREATE EXTENSION pgroonga; CREATE EXTENSION citext; CREATE EXTENSION pgcrypto;` → `alembic upgrade head` を 1 回だけ実行(マイグレーションがテストスキーマの唯一の作成手段。手書き `create_all` は使わない)。
- **分離方式**: 各テストは外側トランザクション+SAVEPOINT ロールバック方式。トリガ(`set_updated_at`)・部分一意・PGroonga はトランザクション内で機能するため、この方式で全 DDL 挙動を検証できる。TRUNCATE 方式は使わない(遅い)。
- **Redis**: テストは DB 番号 15(`redis://localhost:6379/15`)を使い、各テスト前に `FLUSHDB`。
- **MinIO**: バケット `yakudoku-sources-test` / `yakudoku-assets-test` をセッション fixture で作成・終了時に削除。

`apps/api/tests/conftest.py`(worker 側も同一実装を `packages/py-core` の `yakudoku_core.testing.db` から import):

```python
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from yakudoku_api.main import create_app
from yakudoku_core.testing.db import build_test_database_url, prepare_test_database

@pytest.fixture(scope="session")
async def engine():
    url = build_test_database_url()          # DATABASE_URL の DB 名を yakudoku_test に置換
    await prepare_test_database(url)         # drop/create + extensions + alembic upgrade head
    engine = create_async_engine(url, pool_size=5)
    yield engine
    await engine.dispose()

@pytest.fixture
async def db(engine):
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
        yield session
        await session.close()
        await trans.rollback()               # テストごとに全変更を破棄

@pytest.fixture
async def client(db, monkeypatch):
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: db   # 同一トランザクションを共有
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

@pytest.fixture
async def user(db):        # factories.py 参照。以降 as_user(client, user) で認証済みに
    return await make_user(db)
```

### 2.3 ファクトリ(`apps/api/tests/factories.py`)

- 決定: factory-boy / polyfactory は導入せず、**SQLAlchemy モデルを直接組み立てる async ファクトリ関数**に統一する。理由: JSONB 契約(AnchorJson / DocumentContentJson。plans/02 §3)は Pydantic モデル経由で構築する必要があり、ライブラリの自動生成はこの制約と相性が悪い。
- 提供関数(全て `db: AsyncSession` を受け、依存エンティティを省略時自動生成): `make_user` / `make_paper`(既定 public・license `cc-by-4.0`)/ `make_revision`(既定: §14 の縮約 Rectified Flow content、quality A)/ `make_library_item` / `make_translation_set` / `make_translation_unit` / `make_annotation` / `make_note` / `make_chat_thread` / `make_chat_message` / `make_vocab_entry` / `make_resource_link` / `make_collection`(+entries/share token)/ `make_article`(+blocks/overview_figure)/ `make_job` / `make_notification` / `make_reading_session`。
- アンカーを要するファクトリは `anchor_for(revision, block_index, start, end)` ヘルパで実在ブロックから生成する(実在しないアンカーのテストは明示的に `broken_anchor()` を使う)。

### 2.4 主要スイート一覧(テスト ID の正)

各 ID は §6 の表から参照される。ファイルは ID 領域ごとに 1 ファイル(例 `apps/api/tests/integration/test_translations.py` に PY-TR-*)。

| ID | マーカー | 内容(検証条件を含む) |
|---|---|---|
| PY-DB-01 | integration | plans/02 §4 の全テーブル・インデックス・トリガが `alembic upgrade head` 後に存在(`information_schema` 照合) |
| PY-DB-02 | integration | `alembic upgrade head → downgrade base → upgrade head` が成功(plans/02 §7 の CI 要件) |
| PY-DB-03 | integration | `users` 1 行 DELETE で個人資産(library_items 配下・collections・notifications・saved_filters・sessions・byok_api_keys・private papers)が全て消える(docs/01 §13) |
| PY-DB-04 | integration | translation_sets の CHECK/部分一意: shared+user_id・personal+user_id なしが挿入不可、同一 (revision,style) shared 2 行不可、同一ユーザー personal 2 行不可 |
| PY-DB-05 | integration | library_items: 6 値以外の status・1–5 外の understanding・(user,paper) 重複が拒否される |
| PY-DB-06 | integration | document_revisions.quality_level が A/B 以外拒否。source_format 3 値 |
| PY-DB-07 | integration | vocab_entries: 同一ユーザー×同一見出し語(大小文字非依存)重複拒否、srs_stage 1–5、既定 next_review = 翌日 |
| PY-DB-08 | integration | resource_links: (library_item, url_normalized) 重複拒否。dismissed 行も一意対象(再提案抑止) |
| PY-DB-09 | integration | notifications.kind 3 値以外拒否。未読部分インデックスの利用(EXPLAIN で確認) |
| PY-DB-10 | integration | collection_share_tokens: active はコレクションごとに 1 本、revoke 後に再発行可 |
| PY-DB-11 | integration | articles: (library_item) 一意=論文×ユーザーで記事 1 つ。attribution ブロック type が CHECK に存在 |
| PY-DB-12 | integration | overview_figures / explainer_figures: is_current 部分一意・(article,version) 一意・provider CHECK |
| PY-DB-13 | unit | AnchorJson Pydantic 検証が全アンカー保持カラム(annotations.anchor / notes.anchors / chat_messages.*_anchors / vocab_entries.context_anchor / article_blocks.evidence_anchors / resource_links.note_anchors)の書き込み経路に適用されている(スキーマ関数の網羅表テスト) |
| PY-DB-14 | integration | block_search_index を全削除→`rebuild_block_search_index(revision_id)` で content JSONB から完全再構築でき、再構築前後で全行一致(派生テーブル性。plans/02 §8) |
| PY-AUTH-01 | integration | 認証スイープ: OpenAPI の全ルートを未認証で叩き、`anonymous` 区分(plans/03 §1.2 の列挙)以外はすべて 401 を返す(docs/09 §8「それ以外のページは認証必須」) |
| PY-AUTH-02 | integration | メールリンク: 発行→15 分内単回検証で Set-Cookie、再利用・期限切れは `/login?error=link_expired` へ 302。存在有無で応答同一(列挙攻撃対策) |
| PY-AUTH-03 | integration | 拡張トークン: スコープ内 7 エンドポイントのみ通り、スコープ外は 403 `token_scope_exceeded`。再発行で旧トークン即時失効 |
| PY-ING-01 | integration | `POST /api/ingest/arxiv`: 202 で paper/library_item/job 作成、`Idempotency-Key` 再送で初回レスポンス再生 |
| PY-ING-02 | integration | 取り込みパイプライン(worker、arXiv/LLM モック): stage が `queued→fetching→parsing→structuring→translating_abstract→readable→translating_body→complete` を逐語遷移し、quality A・タイムライン 3 段(ソース取得→構造化・図表抽出→全文翻訳)がタイムスタンプ付きで jobs.log に残る |
| PY-ING-03 | integration | `GET /api/ingest/check`: arXiv URL 正規化・LaTeX 有無(Redis 24h キャッシュ)・saved 状態の 3 分岐 |
| PY-ING-04 | integration | `POST /api/ingest/pdf`: private Paper 作成・pdf_sha256 重複 409・50MB 超 413・非 PDF 415・テキストレイヤ無し PDF は `failed(parsing, "テキストが抽出できません")` |
| PY-ING-05 | integration | 重複検知: 同一 arXiv ID 再保存で 409 `duplicate` + `existing`(進捗・前回位置)。別バージョンは 202 で新 source_version。参考文献 `in_library` 判定 |
| PY-ING-06 | integration | `reingest` + `ingest-log`: 実行中の二重 reingest は 409、ログがフォールバック・使用モデルを含む |
| PY-ING-07 | integration | B→A 昇格: LaTeX 発見→`status_suggestion`(promote_revision)通知生成→`adopt-revision` で新リビジョン適用+リアンカー結果 `{moved, unplaced}`。自動適用が発生しないこと(通知のみ) |
| PY-PARSE-01 | unit | arXiv HTML パーサ: 自作フィクスチャ(見出し/段落/数式/図表/脚注/定理/コード/リスト/引用/参考文献の 11 ブロック型+インライン 8 種)→ DocumentContentJson。ブロック安定 ID の規則(docs/01 §4.3) |
| PY-PARSE-02 | unit | LaTeX パーサ(M2): 同上+相互参照解決(`\ref`/`\cite`→ ref/citation インライン)。quality A |
| PY-PARSE-03 | unit | PDF パーサ: 自作 2 ページ PDF → 段落・見出し復元、全ブロックに page+bbox(pt)。quality B |
| PY-PARSE-04 | unit | 数式コーパステスト: §14 フィクスチャの全 equation ブロック(60 式)+自作エッジコーパス(40 式)を KaTeX(サーバ側 `katex.renderToString` を Node ワンショットで実行)に通し、成功率 99% 以上・失敗式は一覧出力(docs/04 §15 の 99% 基準の静的裏付け) |
| PY-TR-01 | unit | プレースホルダ protect: 11 ブロック型ごとの保護対象(math/citation/ref/url/code/footnote)が漏れなくトークン化される |
| PY-TR-02 | integration | 検証不合格(§7 参照)ブロックが `text_ja: null` + `quality_flags: ["placeholder_mismatch"]` で保存され、API がその訳を配信しない(docs/03 §4「壊れた訳を見せない」) |
| PY-TR-03 | unit | 数値・単位検査(number_mismatch)・長さ外れ値(length_outlier)・用語違反(glossary_violation)の quality_flags 判定表 |
| PY-TR-04 | integration | 翻訳ジョブが FakeLLMProvider で全ブロックを訳し、translation_sets.status が pending→partial→complete、進捗分母が自動翻訳対象ブロックのみ(参考文献・付録・表セル除外。docs/03 §6.1) |
| PY-TR-05 | integration | 用語スナップショット: glossary_snapshot 凍結・shared セットは origin=global のみ・訳出後の全 unit がスナップショット訳語を含む(違反は glossary_violation) |
| PY-TR-06 | unit | 見出し訳の「訳題 — 原題」形式生成(「1 はじめに — Introduction」) |
| PY-TR-07 | integration | 設定 4 項目(default_style / auto_translate_appendix / translate_table_cells / suggest_section_selection_over_30_pages)が翻訳対象選定・ジョブ生成に反映される(30 ページ超はセクション選択提案でジョブ分割) |
| PY-TR-08 | integration | 直訳オンデマンド: 初回 `POST /translations {style:"literal"}` で 202+ジョブ、表示中セクション優先。complete 後の再要求は 200 `{job_id: null}`(即時) |
| PY-TR-09 | integration | 手動編集(state=edited)は再翻訳・用語変更ジョブで上書きされない。`retranslate` は `discard_edit` なしで 409 |
| PY-TR-10 | integration | 共有キャッシュ: 2 人目のユーザーが同一 public 論文を追加した時点で translation ジョブが生成されず、既存 shared セットが即時解決される(docs/03 §12 最終項)。personal フォークのマージ解決(plans/02 §5.2 のクエリ)も検証 |
| PY-GLS-01 | integration | 3 層用語集 CRUD・適用優先度(paper > user > global)・global への書き込み 403・promote で user 複製 |
| PY-GLS-02 | integration | 訳語変更: `dry_run=true` で影響ブロック数、`false` で影響ブロックのみの再翻訳ジョブ(全文再翻訳が走らないこと) |
| PY-CHAT-01 | integration | スレッド: LibraryItem 作成時にメイン自動作成・メイン削除 409・一覧順序(メイン先頭) |
| PY-CHAT-02 | integration | SSE 契約: `start→delta*→evidence→done` のイベント順序・`[[ev:n]]` 初出 delta の後に evidence が 1 回・切断後 §10.2 で確定メッセージ回復・エラー時 `event: error`(Problem 形式) |
| PY-CHAT-03 | unit | アンカー表示表記の導出: block_search_index(section_label/paragraph_ordinal/element_label)→「§2.1 ¶4」「式(5)」「図2」「表1」の決定的導出(全 block_type) |
| PY-CHAT-04 | integration | 根拠実在検証: 実在しない block_id を返す FakeLLM 応答から `[[ev:n]]` トークンごと除去され、evidence イベントが送出されない(docs/05 §5) |
| PY-CHAT-05 | unit | ChatContentJson セグメント(text / outside_knowledge / speculation)の構造保存と text_plain 導出 |
| PY-CHAT-06 | integration | quick_action 5 種+導線 4 種のプロンプト組立(選択アンカー・注釈/メモ包含設定 `chat.include_annotations_and_notes` の反映)。regenerate が新 assistant メッセージを追加し旧を残す |
| PY-ANN-01 | integration | 注釈 CRUD: kind×color×body の形状制約(highlight は color 必須・comment は body 必須・bookmark は両方 NULL)、counts 集計 |
| PY-ANN-02 | integration | リアンカー: 新リビジョンで (a) block_id 引き継ぎ分は追従 (b) quote 探索一致分は移動 (c) 失敗分のみ `orphaned=true`(消えない)。reading_position・vocab・記事アンカーも同時書き換え(plans/02 §5.3) |
| PY-ANN-03 | integration | 注釈一覧フィルタ(color / has_comment / placed=false)と Markdown エクスポート(`export/annotations`)の内容一致 |
| PY-NOTE-01 | integration | チャット→メモ昇格: `source_message_id` 指定で根拠アンカー複写・出自参照保持 |
| PY-LIB-01 | integration | 一覧フィルタ結合規則(同一属性 OR・属性間 AND・quick∩status)・NULL ソート末尾・cursor ページング安定性(挿入をはさんでも重複/欠落なし) |
| PY-LIB-02 | integration | facets 恒等式: `unread+in_progress+done+recheck == all == ライブラリ総数`(docs/06 §11) |
| PY-LIB-03 | integration | 読書位置 PUT→viewer 応答の last_position 反映(別セッション=別デバイス相当で取得) |
| PY-LIB-04 | integration | queue-order: up_next 全 ID の並べ替え、up_next 以外混在 422 |
| PY-LIB-05 | integration | ステータス提案: ハートビート 3 分で `status_suggestion` 通知(自動変更なし)、設定 `reading.status_transition` = auto/suggest/off の 3 分岐(auto のみ即適用+通知、off は通知なし) |
| PY-LIB-06 | integration | 読了フロー: `status→done` 初回遷移で finished_at 自動記録・以後不変。全項目省略(すべてスキップ)でも完了。理解度/重要度/ひとことメモの PATCH |
| PY-LIB-07 | integration | 保存フィルタ CRUD・conditions/sort の往復・count が導出値(保存されない) |
| PY-COL-01 | integration | コレクション: entries の position ドラッグ更新(DEFERRABLE 一意)・締切残日数・進捗集計(done/total)・担当(assignee/assignee_is_self)・発表時間・予備注記 |
| PY-COL-02 | integration | 共有リンク: 発行(base62 8 文字)→無効化→再発行。発行済みで再発行は 409。include_notes トグル |
| PY-SHR-01 | integration | `GET /api/share/collections/{token}`: 匿名で 200、revoked/不正 token 404。エントリ順序=コレクション順序 |
| PY-SHR-02 | integration | 共有ページ応答が書誌+✦要約+許可メモのみを含む(include_notes=false でメモ 0 件、本文・訳文・注釈・チャットのフィールド自体が存在しない) |
| PY-SHR-03 | integration | 共有ページ応答にリソース・記事・語彙・読書統計が一切含まれない(docs/12 §9)。ライセンス不明論文は書誌のみに縮退(docs/09 §5.2) |
| PY-SRCH-01 | integration | 日英クロス(§11 のデータ表): 日本語クエリ→訳文ヒット、英語クエリ→原文ヒット、両ヒット同一ブロックは 1 件統合 `matched_in:["source","translation"]` |
| PY-SRCH-02 | integration | ヒット源 5 種(body/note/annotation/chat/article)の source 判定・論文単位グループ化・facets 件数・source フィルタ |
| PY-SRCH-03 | integration | 源別遷移 target の形状(viewer anchor / note_id / thread_id+message_id / article_block_id)と snippet の `<mark>` サニタイズ |
| PY-SRCH-04 | integration | `search/preview`: 上位 3 件固定+total。論文内検索 `revisions/{id}/search` の訳文同一視 |
| PY-SRCH-05 | integration | 検索が他ユーザーの資産・private 論文にヒットしない(アクセス制御) |
| PY-NTF-01 | integration | 通知 3 種の生成(翻訳完了/提案/締切リマインド)・未読件数・read-all・`deadline_reminder` は毎日 08:00 JST cron(arq cron のスケジュール定義値を検証) |
| PY-NTF-02 | integration | 提案 2 択 action: apply=ステータス変更(§5.4 と同一経路)、dismiss=そのまま。resolved 済み再操作 409。apply が status_suggestion 以外で 422 |
| PY-SET-01 | integration | 設定 GET/PATCH: 既定値完全形(plans/03 §17.1)・deep merge・値域違反 422(accent 4 色・font_size 0.5 刻み等) |
| PY-SET-02 | integration | `llm_routing` の用途 8 キー+`overview_figure_raster_mode` の更新がルート解決(plans/04 §15)に反映。モデル ID がコードにハードコードされていない(ソース grep テスト: `deepseek-v4-flash` 等が seed 以外に出現しない) |
| PY-SET-03 | integration | BYOK: PUT→masked(末尾 4 文字)のみ返る・平文再表示 API 不在(OpenAPI 全パス走査)・DELETE 後は運営キー解決 |
| PY-SET-04 | integration | クォータ: usage_records 集計(byok=false のみ)・残量応答・超過時 429 `quota_exceeded`(チャット/画像/語彙/記事/再翻訳)と取り込み翻訳段のみ `waiting_quota` 停止→BYOK 登録で自動再開 |
| PY-EXP-01 | integration | 論文単位 Markdown: 書誌+メモ+注釈+チャット+リソース一覧(URL・メモ・§チップのテキスト化)を含む(Obsidian 互換 front-matter) |
| PY-EXP-02 | unit | BibTeX: `bibtexparser` 1.4 で再パースでき、必須フィールド(author/title/year/eprint)を含む(docs/06 §11「主要リファレンスマネージャで読み込める」の機械検証) |
| PY-EXP-03 | unit | CSV: UTF-8 BOM・16 列ヘッダ固定(plans/03 §18) |
| PY-EXP-04 | integration | 全量 JSON(export_full ジョブ): ライブラリ・注釈・メモ・チャット・語彙(SRS 含む)・リソース・記事・コレクション・設定の全キー存在(docs/00 P5) |
| PY-VOC-01 | integration | 「語彙に追加」: anchor から文脈センテンス切り出し・ハイライト範囲・出典(論文+§)・追加日が自動付与。重複語 409 |
| PY-VOC-02 | integration | AI 生成(FakeLLM): 8 フィールド(meaning_short/long, ipa, pos_label, interpretation, etymology, mnemonic, related_forms)保存・全フィールド PATCH 可能 |
| PY-VOC-03 | integration | edited_fields に入ったフィールドが regenerate で上書きされない(docs/11 §10) |
| PY-VOC-04 | integration | 生成失敗(FakeLLM 強制エラー): 語彙+文脈+出典は generation_status=failed で保存され、regenerate で再試行できる(黙って消えない) |
| PY-VOC-05 | integration | 一覧: 種別チップ・「復習期」フィルタ・語彙/追加日ソート・語彙帳内検索(`GET /api/vocab?q=`)が語彙帳のみを対象とする |
| PY-VOC-06 | unit | SRS 規則の全パターン(docs/11 §7.1): 保存=段階1・翌日/「✓覚えた」=+1 段階(間隔 1/3/7/14/30 日)/「まだあやしい」=段階1 リセット/段階5 通過=mastered(キュー除外・一覧残存)/未評価はスケジュール不変 |
| PY-VOC-07 | integration | 復習期件数の一致: review-queue 件数 = `next_review ≤ 今日 AND NOT mastered` の COUNT = チップ/バッジ用件数フィールド。サイドバーバッジ用は総語数 |
| PY-VOC-08 | integration | 語彙 Markdown エクスポート: 文脈センテンス・出典を含む |
| PY-VOC-09 | integration | 用語集(glossary_terms)の作成・変更・削除が vocab_entries に一切影響しない(独立性。docs/11 §10) |
| PY-RES-01 | unit | URL 種別判定表: github.com→github / youtube.com・youtu.be→youtube / PDF 拡張子・Content-Type→slides / その他→article(判定不能は article)。URL 正規化(トラッキングパラメータ除去) |
| PY-RES-02 | integration | メタ自動取得(HTTP モック): kind 別 meta(言語・スター・更新/duration/枚数/読了目安)・YouTube サムネ。取得失敗でも fetch_status=failed で URL のみ登録完了(P3)。kind の PATCH 変更可 |
| PY-RES-03 | integration | 公式実装検出: papers.official_repo_url → suggested 行生成 → confirm で official=true・dismiss で dismissed 永続(再提案されない) |
| PY-RES-04 | integration | note_md 内 §チップ(note_anchors)の保存とアンカー実在検証 |
| PY-RES-05 | integration | 件数バッジ = status=active の COUNT(suggested/dismissed を数えない) |
| PY-RES-06 | integration | 同一 URL(正規化後)二重登録 409 |
| PY-ART-01 | integration | 記事生成(FakeLLM): プリセット 4 種・include_math・version 管理(指示つき再生成で +1・instructions_history 追記)・ブロック rewrite が対象ブロックのみ更新 |
| PY-ART-02 | integration | 「議論したい点」: 疑問ハイライト(color=question)由来項目に origin=user_highlight が付く |
| PY-ART-03 | integration | 図表転載判定: license=arxiv-nonexclusive/unknown で figure_embed 生成がブロックされリンクカード代替、cc-by-4.0 でクレジット自動付記+ライセンスバッジ、cc-by-nd はキャプション分離(docs/09 §5.2 マトリクス全 8 行) |
| PY-ART-04 | integration | attribution ブロックが常に末尾・削除/rewrite 対象外(API で操作すると 422) |
| PY-FIG-01 | unit | DSL→SVG: cards の label/heading/body 全テキストが SVG `<text>`/`<tspan>` に過不足なく出現(文字化けゼロの構造的保証。docs/07 §3) |
| PY-FIG-02 | unit | 決定性: 同一 DSL→2 回レンダリングでバイト同一(§10)。ゴールデン SVG(`golden/overview_rectified_flow.svg`)と sha256 一致 |
| PY-FIG-03 | integration | 概要図の版管理: 再生成で version+1・is_current 移動・旧版へ `restore` で復帰(旧版データ不変) |
| PY-FIG-04 | integration | ラスターモード: `overview_figure_raster_mode=true` で ImageRouter 経由生成・false(既定)で SVG。切替が設定のみで効く |
| PY-FIG-05 | integration | 解説図: provider 3 値(openai/google/xai)での生成・S3 保存・slot/version 管理 |
| PY-FIG-06 | unit | 画像生成プロンプト仕様: プロンプトテンプレートが「画像内に文字を描かない」指示を含み、キャプションが本文情報を保持する(テンプレート契約テスト。docs/07 §3) |
| PY-LIC-01 | unit | `yakudoku_core.licenses`: docs/09 §5.2 マトリクス全 8 行(翻訳表示可否×図表転載可否×共有ページ縮退)の判定表テスト |
| PY-JOB-01 | integration | 冪等・段階再開: 各 stage 完了直後に強制終了(例外注入)→再実行で checkpoint から再開し、TranslationUnit・SourceAsset・通知が二重作成されない(docs/09 §8)。idempotency_key 再投入は既存 job を返す |
| PY-JOB-02 | integration | 部分読書: readable 到達で viewer が開け、未翻訳セクションは原文+進捗、優先繰り上げ(prioritize)でキュー先頭化 |
| PY-JOB-03 | integration | リトライ: 指数バックオフ 30s→2min→8min の 3 回・以後 failed+手動再試行。部分成功(図抽出失敗)がジョブ全体を fail させず処理ログに残る |
| PY-LLM-01〜07 | unit/integration | plans/04 §17 のスイートを次の対応で採用(決定: 番号は §17 の項番と一致しない。本表の対応が正): PY-LLM-01=ルート解決(§17-7)、PY-LLM-02=フォールバック規則(§17-2)、PY-LLM-03=BYOK(§17-6)、PY-LLM-04=エラー分類マトリクス+ストリーミング契約(§17-1・§17-3)、PY-LLM-05=structured 互換戦略+価格計算(§17-4・§17-5)。追補: PY-LLM-06=用途 retranslation が translation より上位モデルへ解決されること、PY-LLM-07=ImageRouter のプロバイダ切替とフォールバック(google→xai→openai) |

## 3. Vitest(apps/web・apps/extension・packages/tokens)

- 構成: `vitest.config.ts` は `environment: "jsdom"`、`setupFiles: ["./src/test/setup.ts"]`(@testing-library/jest-dom 6・MSW サーバ起動)。スナップショットは DOM スナップショットを使わず、明示アサーションのみ(壊れやすいスナップショットを CI ゲートにしない)。
- 決定: コンポーネントテストの対象は plans/08 §5 の共通 22 コンポーネント+状態分岐を持つ画面部品に限定する。レイアウト・配色の正しさは §9 の VR が担う(二重に検証しない)。

| ID | 対象 | 検証 |
|---|---|---|
| VT-UI-01 | AppHeader / ログイン画面 | プロダクト名「訳読 / YAKUDOKU」表記(docs/00) |
| VT-UI-02 | QualityBadge | A=アクセント淡色/B=グレー+「PDF 取り込み」文言(plans/08 §5.3) |
| VT-UI-03 | StatusPill | 6 値×色トークン(`--status-*`)対応・日本語ラベル(読む予定/すぐ読む/読んでいる/読んだ/あとで再読/保留) |
| VT-VIEW-01 | SidePanelTabs | 排他 6 タブ(チャット/メモ/注釈/図表/リソース/情報)・注釈/リソースの CountBadge |
| VT-VIEW-02 | SectionHeading | 「訳題 — 原題」併記・原題は Source Serif 4 イタリック淡色 |
| VT-VIEW-03 | TranslationColumnHeader | 「✦ AI翻訳」常時表示・「段落対応 ⇄」 |
| VT-VIEW-04 | TocTree | 進捗 96%・節 ✓・注釈数・未翻訳付録「開くと翻訳します(オンデマンド)」・参考文献淡色(分母外) |
| VT-VIEW-05 | ParallelPopover | ホバー「対」・キー `t` 開閉・「¶2 / 1 Introduction」・「訳がおかしい?」フッタ |
| VT-VIEW-06 | FigureRefPopover | 図表参照クリック→その場ポップ(スクロール位置不変)・両言語キャプション・3 アクション |
| VT-VIEW-07 | BilingualPane | 段落単位 2 カラムの行アライン(段落数不一致データでも対応が崩れない) |
| VT-VIEW-08 | AnnotationListPanel | フィルタ(すべて/重要/疑問/アイデア/コメントのみ)+未配置 0 件表示+「⤓ Markdown エクスポート」導線 |
| VT-VIEW-09 | EvidenceHighlight | 「✦ チャットの根拠 · 式(5)」本文側バッジの表示/解除 |
| VT-VIEW-10 | ChatMessage | 「AI生成」バッジ・「論文外の知識」「推測」ボックス・`[[ev:n]]`→EvidenceChip 展開 |
| VT-VIEW-11 | ChatComposer | 免責文の固定表示(逐語)・定型チップ 5 種 |
| VT-VIEW-12 | QuickActionChips | 5 種のラベルと quick_action 値の対応・入力候補 2 種 |
| VT-VIEW-13 | OverviewFigureFrame | 「AI生成 · 版 N」・「✦ 書き直し指示」・「SVG ⤓」(download 属性) |
| VT-VIEW-14 | ArticleMetaRow | 「AI生成」・生成日付・免責「元の論文とは別物です — 根拠チップから原文へ」 |
| VT-VIEW-15 | ArticleBlockHover | 「✦ 書き直し指示/再生成/根拠を表示」の 3 操作出現 |
| VT-VIEW-16 | DiscussionList | 「あなたの疑問ハイライトから」バッジ(origin=user_highlight のみ) |
| VT-VIEW-17 | ResourceCard | kind 別メタ表示・YouTube サムネ+再生時間バッジ |
| VT-VIEW-18 | ResourceTabBadge | active 件数のみ表示(suggested を数えない) |
| VT-VIEW-19 | ResourceCard「開く ↗」 | `target="_blank" rel="noopener noreferrer"` |
| VT-LIB-01 | LibraryCard | ✦3 行要約(①②③)・パイプライン進捗・タグ提案チップ・「読み始める」 |
| VT-LIB-02 | LibraryTable + BulkActionBar | 10 列固定ヘッダ・複数選択でフローティングバー(ステータス変更/タグ追加/コレクションへ) |
| VT-LIB-03 | UpNextQueue | 6 件で「積みすぎかも?」バナー表示・閉じられる |
| VT-VOC-01 | VocabList | 種別チップ 3 分類+復習期チップ絞り込み・ソート |
| VT-VOC-02 | VocabSearchBox | 語彙帳内検索がグローバル検索(⌘K)と独立(store 分離) |
| VT-VOC-03 | ReviewFooter | 「次の復習: 明日(2 回目)」整形・2 ボタン(まだあやしい/✓ 覚えた) |
| VT-VOC-04 | VocabBadges | 復習期バッジ=復習期件数、サイドバーバッジ=総語数 |
| VT-TOK-01 | packages/tokens | 生成 CSS が _global.md の全値と一致: アクセント 4 色とダーク対応マップ、`--pr-as/-am/-ads/-adm` の rgba(0.10/0.32/0.14/0.40)導出、注釈 4 色(#C49432/#5884AA/#659471/#82827E)、ステータス 6 色、書体 4 系統、`::selection` rgba(62,92,118,0.22)(plans/08 §2 の値を期待値ハードコード) |
| VT-XTU-01 | apps/extension manifest | wxt ビルド出力の manifest.json スナップショット: `permissions=["activeTab","storage"]`・`optional_host_permissions=["https://arxiv.org/*"]`・MV3(docs/08 §8 審査要件) |
| VT-XTU-02 | lib/pdf-detect | タブ内 PDF 判定・書誌ローカル推定(arXiv URL 判別・自動送信しない判定分岐) |
| VT-XTU-03 | background 送信キュー | 失敗送信の chrome.storage.local 永続・指数リトライ・重複排除(chrome API はモック) |

## 4. Playwright E2E(apps/web)

### 4.1 構成(`apps/web/playwright.config.ts` 確定値)

```ts
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  expect: {
    timeout: 10_000,
    toHaveScreenshot: { maxDiffPixelRatio: 0.001, threshold: 0.2, animations: "disabled", caret: "hide" },
  },
  use: {
    baseURL: "http://localhost:3000",
    viewport: { width: 1440, height: 900 },      // docs/09 §6 基準ビューポート
    locale: "ja-JP", timezoneId: "Asia/Tokyo",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "setup", testMatch: /global\.setup\.ts/ },
    { name: "e2e", testDir: "./e2e/specs", dependencies: ["setup"],
      use: { storageState: "e2e/.auth/user.json" } },
    { name: "visual", testDir: "./e2e/vr", dependencies: ["setup"],
      use: { storageState: "e2e/.auth/user.json" } },
  ],
  webServer: [
    { command: "uv run python -m yakudoku_llm.testing.mock_server --port 8090", port: 8090, reuseExistingServer: true },
    { command: "uv run uvicorn yakudoku_api.main:app --port 8000", port: 8000, reuseExistingServer: true,
      env: { /* §8.4 のモック向け BASE_URL 群 */ } },
    // 決定: arq ワーカーは待受ポートを持たないため webServer では起動できない。
    // global.setup.ts が child_process.spawn("uv", ["run", "arq", "yakudoku_worker.main.WorkerSettings"]) で
    // 起動し(既存プロセスがあれば再利用)、globalTeardown で停止する。
    { command: "pnpm dev --port 3000", port: 3000, reuseExistingServer: true },
  ],
});
```

- 前提データ: E2E 実行前に `uv run python -m yakudoku_api.seed --sample rectified-flow --reset`(§14)。テスト間はシードを共有し、**書き込み系テストは自分が作ったデータのみを削除する**(シード本体を変更するテストは `test.describe.serial` + 終了時に seed 再投入)。
- 決定: 実行ブラウザは Chromium のみ(plans/00 §4.5)。クロスブラウザ(Safari/Firefox 最新 2 版。docs/09 §6)は自動化せず、リリース前チェックリストの手動確認項目とする(v2: WebKit/Firefox プロジェクト追加)。

### 4.2 認証セットアップ(`global.setup.ts`)

メールリンク認証を実経路で通す(= PY-AUTH-02 の E2E 版を兼ねる): (1) `/login` で `dev@yakudoku.test` を送信 → (2) Mailpit API `GET http://localhost:8025/api/v1/messages` から最新メールのリンクを抽出 → (3) リンクへ遷移しダッシュボード表示を確認 → (4) `storageState` を `e2e/.auth/user.json` に保存。2 人目ユーザー(共有・担当テスト用)は `member@yakudoku.test` で同様に `e2e/.auth/member.json`。

### 4.3 E2E シナリオ(PW-01〜22 確定)

| ID | シナリオ(主アサーション) |
|---|---|
| PW-01 | ログイン(メールリンク)→ ダッシュボード表示・ヘッダにプロダクト名・ベルアイコン |
| PW-02 | 取り込み UI 非存在の否定検査: ダッシュボード・ライブラリ(表/カード)・ヘッダの全域に「+追加」「取り込み」「アップロード」「ドロップ」に該当する要素・`input[type=file]` が存在しない(docs/00・02・08) |
| PW-03 | 取り込み進行の可視化: API 直呼びで ingest 開始 → 最近追加カードに「✓ 書誌 → ✓ アブスト訳・要約 → 本文翻訳中 n%」進捗・「§n まで読めます」・「読み始める」で部分読書(未翻訳セクションは原文+翻訳中表示、完了分が SSE で差し替わる) |
| PW-04 | ライブラリ: テーブル 10 列・クイックフィルタ 5 種の件数・属性フィルタ 5 種・「この条件を保存」→サイドバーに件数付き表示・複数選択→一括操作バー 3 操作 |
| PW-05 | ビューア基本: 5 モードワンクリック切替・6 タブ排他・A/B バッジ常時・「図2」「式(5)」クリックでその場ポップ(スクロール位置不変)・目次進捗と節 ✓ |
| PW-06 | 情報パネル: 品質 A 定義文言(逐語)・取り込みタイムライン 3 段(タイムスタンプ付き)・処理ログ・「再取り込み」実行・ライセンスカード「CC BY 4.0 — 図表転載可」 |
| PW-07 | 翻訳操作: 段落ホバー「対」/キー `t` で対訳ポップ(¶ 表示)→「訳がおかしい?」→再翻訳・指示つき再翻訳(proposal 差分→採用)・スタイル切替(直訳初回=生成開始、再切替=即時)・未翻訳付録を開く→オンデマンド翻訳開始 |
| PW-08 | チャット: スレッド作成/切替・本文選択→「✦AIに質問」(引用チップ)・SSE 回答に根拠チップ「§2.1 ¶4」→クリックで本文ジャンプ+一時ハイライト・本文側「✦ チャットの根拠」双方向同期・「AI生成」/「論文外の知識」表示・「↑ メモに保存」でアンカー保持 |
| PW-09 | 読書位置: 読み進め→リロードで「続きから ↓」バナー→1 クリック復帰。別コンテキスト(別デバイス相当)でも同位置から再開 |
| PW-10 | 選択メニュー: 4 色ハイライト・コメント・語彙に追加・コピーの実行。注釈一覧のフィルタと件数 |
| PW-11 | リビジョン昇格: B 論文に昇格提案通知→「変更する」→新リビジョン適用・注釈追従・失位置分が「未配置」に残る・参考文献展開→「+ この論文も取り込む」/「ライブラリに有り ✓」 |
| PW-12 | PDF モード: 「同期: p.5 ≒ §2.2」・bbox 選択→「≒ §2.2 ¶2 — 訳文で見る →」で対応段落へ・「この位置を訳文で開く →」 |
| PW-13 | 記事モード: モード切替から開く→プリセット選択生成→メタ行(AI生成・日付・免責)・概要図(3 カード・版 2・SVG ⤓ ダウンロード検証・書き直し指示→版 3→版 2 へ復帰)・ブロックホバー 3 操作・根拠チップ→原文ジャンプ・出典ブロック末尾固定・**公開/限定公開/コメント UI が存在しない**(否定) |
| PW-14 | 横断検索: 1e ドロップダウン(プレビュー 3 件+すべての結果)→ 4e 全結果(源バッジ・論文グループ・日英クロス §11 データ)→源別遷移 4 種 |
| PW-15 | コレクション+共有: 順序ドラッグ・担当・締切→共有リンク発行→**ログアウト状態の新コンテキスト**で `/c/{token}` 閲覧(書誌+要約+許可メモのみ・`<meta name="robots" content="noindex">`・順序保持・「訳読をはじめる」CTA)→無効化で 404 |
| PW-16 | エクスポート: 設定画面から Markdown / BibTeX / CSV ダウンロード+JSON 一括(ジョブ完了→ download_url) |
| PW-17 | 設定: 8 カテゴリ表示・翻訳 4 項目の切替が挙動に反映(付録トグル→目次表示変化)・アクセント 4 色切替(CSS 変数値検証)・本文書体切替・BYOK 登録→マスク表示・再表示不可 |
| PW-18 | 読了フロー(1g): 最終セクション到達→読了提案→モーダル(読了日・累計時間の自動記録表示・理解度 4/5・重要度・ひとことメモ・「すべてスキップ」経路)→「記事モードで読み返す →」遷移 |
| PW-19 | ダッシュボード+通知: 続きを読む(≤3)・すぐ読むキュー(ドラッグ順序)・締切・統計(今週読了・12 週棒グラフ)・ベル→ポップオーバー 3 種・提案「変更する/そのまま」・「すべて既読にする」 |
| PW-20 | 語彙帳: ビューアで選択→「語彙に追加」→ 4d で AI 生成 6 セクション表示→編集→「復習をはじめる」→ 2 択評価→「次の復習」更新→「原文で見る →」で該当センテンスハイライト |
| PW-21 | リソース: URL 貼り付け(4 種)→自動判定・メタ表示→公式実装の破線提案カード「+ 追加」→公式バッジ/「無視」→再提案なし→メモに §2.2 チップ→クリックで本文ジャンプ |
| PW-22 | モバイル(viewport 390×844): ビューア閲覧・ステータス変更ができ、取り込み操作が要求されない(docs/00) |

## 5. 拡張機能テスト(apps/extension)

### 5.1 方式(確定)

- ユニット(VT-XTU-01〜03)は Vitest + `@webext-core/fake-browser`(chrome API モック)。
- E2E は Playwright の `chromium.launchPersistentContext(userDataDir, { args: ["--disable-extensions-except=<dist>", "--load-extension=<dist>"] })` でビルド済み拡張(`wxt build` の `.output/chrome-mv3`)をロードする。ポップアップは `chrome-extension://{id}/popup.html?tab_url={対象URL}` を直接開いて検証する(ツールバー実クリックは自動化不能のため。`tab_url` クエリはテスト専用の現在タブ URL 上書きで、ビルドフラグ `WXT_E2E=1` のときのみ有効)。
- 外部依存: arXiv abs ページは `context.route("https://arxiv.org/**")` で `e2e/fixtures/arxiv-abs-2209.03003.html`(自作フィクスチャ)を返す。API(localhost:8000)は本物、API から先の arXiv アクセスは §8.4 のフィクスチャサーバへ向ける。

### 5.2 シナリオ(XT-01〜10)

| ID | シナリオ |
|---|---|
| XT-01 | 拡張が唯一の取り込み経路: ポップアップから保存した論文がライブラリに現れる(webアプリ側 PW-02 と対) |
| XT-02 | 状態1(保存前): 書誌プレビュー・「✓ LaTeX ソースあり — 品質レベル A 見込み」・タグ提案チップ |
| XT-03 | 保存操作: ステータス 3 択(既定「読む予定」)・タグ・コレクション・ひとことメモ・**Enter キーで保存**・クリック数 2 以内(ポップアップ表示→保存) |
| XT-04 | 状態2(保存直後): 同一ポップアップ内で「✓ 書誌 → ✓ 構造化 → 翻訳中 n%」+「サイトで開く ↗」が処理途中で機能 |
| XT-05 | 状態3(既にライブラリ): 重複保存 UI が出ず、現ステータス・追加日・進捗・前回位置・「続きから開く ↗」・「ステータス変更 ▾」(PATCH が反映) |
| XT-06 | 状態4(一般 PDF): 警告+「このタブのPDFを送信」明示クリックのみで送信(自動送信なしをネットワーク傍受で検証)・private 保存・書誌は推定表示 |
| XT-07 | フッタ「直近の取り込み」3 件(処理中=進捗率/完了=時刻) |
| XT-08 | 「訳 保存」ピル: 既定オフ(コンテントスクリプト非注入)→設定オンで arXiv abs のみ注入・保存後「✓ 保存済み」・非 arXiv ページに非注入 |
| XT-09 | ツールバーアイコン琥珀ドット: 処理中/未読通知ありで `chrome.action.setBadgeBackgroundColor(#C49432)` 相当のバッジ状態(background の action 状態を service worker 経由で検証) |
| XT-10 | 送信キュー永続: API 停止状態で保存→失敗キュー→コンテキスト再起動(persistent context 再作成)後もキューが残り、API 復旧で自動送信される(docs/08 §8) |

## 6. トレーサビリティ表(docs 受け入れ基準 → テスト)

- 対象: docs/00〜12 の全受け入れ基準チェックリスト(158 項目)。docs/README.md の 4 項目は文書整合レビュー(REV-01)として扱い、本表からは除外する。
- AC ID 規約: `AC-<doc番号>-<項目連番>`(各 doc のチェックリスト出現順)。
- 検証レイヤが複数の行は「主担当 + 補助」の順。**全 158 項目に最低 1 つの自動テストまたは REV 割当があることを、この表自体の網羅チェックスクリプト(`tools/traceability/check.py`: docs の `- [ ]` 件数と本表の行数を突合)で CI 検証する。**

### 6.1 REV(自動化対象外)項目の扱い

`REV-*` は「機械検証が原理的に不可能または費用対効果が合わない文書整合項目」のみに許す。PR テンプレートのチェックリストで運用する。

| ID | 内容 |
|---|---|
| REV-01 | docs 間の記述整合(00 Q1〜Q5 と各 doc の矛盾なし・README 目次) |
| REV-02 | docs/01 の ER 図に全エンティティが含まれる(図の更新漏れ) |
| REV-03 | Grafana ダッシュボード定義(plans/01 §9.4)が docs/09 §1 の全 11 目標を可視化している |
| REV-04 | マイルストーンごとの品質指標(docs/10 §6)実測レビュー(リリース判定会) |

### 6.2 docs/00 プロダクト概要

| AC | 基準(要約) | レイヤ | テストID |
|---|---|---|---|
| AC-00-01 | プロダクト名の全 UI 統一 | Vitest+E2E | VT-UI-01, PW-01 |
| AC-00-02 | S1〜S6 がエンドツーエンドで実現可能 | E2E | PW-03〜PW-21(全景), XT-02〜04 |
| AC-00-03 | アプリ内に「+追加」・取り込みダイアログが存在しない | E2E | PW-02 |
| AC-00-04 | モバイルで閲覧+ステータス変更・取り込み非要求 | E2E | PW-22 |
| AC-00-05 | AI 生成物に「AI生成」ラベル+根拠チップ(P1) | pytest+Vitest+E2E | PY-CHAT-05, VT-VIEW-10/14, PW-08, PW-13 |
| AC-00-06 | ステータス自動変更なし・常に提案通知(P6) | pytest+E2E | PY-LIB-05, PY-NTF-02, PW-19 |
| AC-00-07 | 品質 A/B 2 段階・バッジ常時表示 | pytest+Vitest+E2E | PY-DB-06, VT-UI-02, PW-05 |
| AC-00-08 | 表示 5 モード・サイドパネル 6 タブ | Vitest+E2E | VT-VIEW-01, PW-05 |
| AC-00-09 | 共有ページ: アカウント不要・閲覧専用・noindex・書誌+要約+許可メモのみ(P7) | pytest+E2E | PY-SHR-01〜03, PW-15 |
| AC-00-10 | 記事の公開・限定公開・コメント UI が v1 に存在しない | E2E | PW-13(否定検査) |
| AC-00-11 | Markdown / BibTeX・CSV / 全量 JSON エクスポート(P5) | pytest+E2E | PY-EXP-01〜04, PW-16 |
| AC-00-12 | Q1〜Q5 の決定と docs の無矛盾 | レビュー | REV-01 |

### 6.3 docs/01 ドメインモデル

| AC | 基準(要約) | レイヤ | テストID |
|---|---|---|---|
| AC-01-01 | quality_level A/B 2 値+意味+常時バッジ | pytest+Vitest | PY-DB-06, VT-UI-02 |
| AC-01-02 | LibraryItem の全属性+クイックフィルタが派生値 | pytest | PY-DB-05, PY-LIB-01, PY-LIB-02 |
| AC-01-03 | ResourceLink の kind4種・official・メタ・§チップメモ・提案状態遷移 | pytest | PY-DB-08, PY-RES-01〜04 |
| AC-01-04 | VocabEntry の 3 分類・文脈・AI 8 フィールド・編集保護・SRS | pytest | PY-DB-07, PY-VOC-02/03/06 |
| AC-01-05 | Notification 3 種+既読+提案はユーザー操作のみ | pytest | PY-DB-09, PY-NTF-01/02 |
| AC-01-06 | SavedFilter=名前+条件+ソート、件数は導出 | pytest | PY-LIB-07 |
| AC-01-07 | Collection/Entry の全属性(共有トークン・担当・発表時間ほか) | pytest | PY-DB-10, PY-COL-01/02 |
| AC-01-08 | Article: 論文×ユーザーで 1・自動構成・版・根拠付きブロック・公開属性なし | pytest | PY-DB-11, PY-ART-01 |
| AC-01-09 | OverviewFigure(DSL+決定的 SVG+版)と ExplainerFigure の区別 | pytest | PY-DB-12, PY-FIG-02/03/05 |
| AC-01-10 | ER 図の網羅 | レビュー | REV-02 |
| AC-01-11 | 全位置参照が Anchor に一本化 | pytest | PY-DB-13 |

### 6.4 docs/02 取り込み

| AC | 基準(要約) | レイヤ | テストID |
|---|---|---|---|
| AC-02-01 | 拡張のみが取り込み経路・アプリ内 UI 不在 | E2E+拡張 | PW-02, XT-01 |
| AC-02-02 | 保存前の書誌プレビュー+「品質レベル A 見込み」 | 拡張+pytest | XT-02, PY-ING-03 |
| AC-02-03 | 保存時ステータス 3 択・タグ・コレクション・メモ・Enter | 拡張 | XT-03 |
| AC-02-04 | arXiv は URL のみ送信・PDF は明示操作のみ | 拡張+Vitest | XT-06, VT-XTU-02 |
| AC-02-05 | タブ内 PDF は private・共有対象外 | pytest+拡張 | PY-ING-04, PY-SHR-03, XT-06 |
| AC-02-06 | LaTeX→品質 A+定義文言+タイムライン 3 段 | pytest+E2E | PY-ING-02, PW-06 |
| AC-02-07 | 全論文に A/B バッジ・B は由来が読む前に分かる | Vitest+E2E | VT-UI-02, PW-04/05 |
| AC-02-08 | 3 秒カード/20 秒要約/60 秒 readable(p50) | 性能 | PF-03(+本番テレメトリ) |
| AC-02-09 | 進捗+読書可能範囲表示・部分読書 | pytest+E2E | PY-JOB-02, PW-03 |
| AC-02-10 | 重複保存で LibraryItem 非重複+既存表示 | pytest+拡張 | PY-ING-05, XT-05 |
| AC-02-11 | 再取り込み・処理ログ・失敗 3 点セット表示 | pytest+E2E | PY-ING-06, PY-JOB-03, PW-06 |
| AC-02-12 | B→A 昇格の通知提案+ワンクリック適用(自動適用なし) | pytest+E2E | PY-ING-07, PW-11 |

### 6.5 docs/03 翻訳

| AC | 基準(要約) | レイヤ | テストID |
|---|---|---|---|
| AC-03-01 | プレースホルダ検証通過率 99.9%・不合格ブロック非表示 | プロパティ+pytest+計測 | HP-01〜04, PY-TR-02(+ メトリクス `translation_placeholder_failures_total`) |
| AC-03-02 | スナップショット用語の訳ゆれゼロ | pytest+スモーク | PY-TR-05, SM-02 |
| AC-03-03 | 見出し原題併記 | pytest+Vitest | PY-TR-06, VT-VIEW-02 |
| AC-03-04 | 「✦ AI翻訳」表示 | Vitest | VT-VIEW-03 |
| AC-03-05 | 目次進捗・節✓・オンデマンド明示 | Vitest+E2E | VT-VIEW-04, PW-05 |
| AC-03-06 | 設定 4 項目の反映 | pytest+E2E | PY-TR-07, PW-17 |
| AC-03-07 | 直訳オンデマンド初回生成・以後即時 | pytest+E2E | PY-TR-08, PW-07 |
| AC-03-08 | 「訳がおかしい?」→再翻訳導線 | Vitest+E2E | VT-VIEW-05, PW-07 |
| AC-03-09 | 再翻訳が上位モデル | pytest | PY-LLM-06 |
| AC-03-10 | 訳語変更→影響ブロックのみ 1 分以内 | pytest+性能 | PY-GLS-02, PF-08 |
| AC-03-11 | 手動編集の非上書き | pytest | PY-TR-09 |
| AC-03-12 | 2 人目は翻訳待ちゼロ | pytest | PY-TR-10 |

### 6.6 docs/04 ビューア

| AC | 基準(要約) | レイヤ | テストID |
|---|---|---|---|
| AC-04-01 | 5 モードワンクリック切替 | E2E | PW-05 |
| AC-04-02 | 排他 6 タブ+件数バッジ | Vitest+E2E | VT-VIEW-01, PW-05 |
| AC-04-03 | 対訳ポップ(対/`t`・¶・訳がおかしい?) | Vitest+E2E | VT-VIEW-05, PW-07 |
| AC-04-04 | 前回位置バナー→1 クリック復帰 | E2E | PW-09 |
| AC-04-05 | 図/式参照のその場ポップ(位置不変) | Vitest+E2E | VT-VIEW-06, PW-05 |
| AC-04-06 | 対訳の段落対応が崩れない | Vitest+VR | VT-VIEW-07, VR-1a |
| AC-04-07 | チャット根拠の双方向同期 | E2E | PW-08(+VT-VIEW-09) |
| AC-04-08 | PDF bbox→訳文/この位置を訳文で | E2E | PW-12 |
| AC-04-09 | 参考文献→取り込み/有り✓ | pytest+E2E | PY-ING-05, PW-11 |
| AC-04-10 | 選択メニュー 5 操作 | E2E | PW-10 |
| AC-04-11 | 再翻訳・切替・昇格後のハイライト維持+未配置 | pytest+E2E | PY-ANN-02, PW-11 |
| AC-04-12 | 注釈一覧フィルタ+Markdown エクスポート | pytest+Vitest | PY-ANN-03, VT-VIEW-08 |
| AC-04-13 | 未翻訳付録のオンデマンド翻訳 | pytest+E2E | PY-TR-08, PW-07 |
| AC-04-14 | 別デバイスで前回位置から再開 | pytest+E2E | PY-LIB-03, PW-09 |
| AC-04-15 | 品質 A の数式レンダリング成功率 99%以上 | pytest+計測 | PY-PARSE-04(+ 本番 KaTeX エラーカウンタ) |

### 6.7 docs/05 チャット

| AC | 基準(要約) | レイヤ | テストID |
|---|---|---|---|
| AC-05-01 | スレッド表示・切替・作成 | pytest+E2E | PY-CHAT-01, PW-08 |
| AC-05-02 | 選択質問チップ+引用プレビュー+元位置ジャンプ | E2E | PW-08 |
| AC-05-03 | 参照ブロックの本文側強調バッジ | Vitest+E2E | VT-VIEW-09, PW-08 |
| AC-05-04 | 根拠チップ ¶ 粒度+ジャンプ+一時ハイライト | pytest+E2E | PY-CHAT-03, PW-08 |
| AC-05-05 | 実在しない根拠チップの表示前除去 | pytest | PY-CHAT-04 |
| AC-05-06 | AI生成バッジ+「論文外の知識」ボックス | pytest+Vitest | PY-CHAT-05, VT-VIEW-10 |
| AC-05-07 | 記載なし事項ででっち上げない | スモーク | SM-03 |
| AC-05-08 | 免責文の固定表示(逐語) | Vitest | VT-VIEW-11 |
| AC-05-09 | 定型チップ 5 種+「実験設定の整理」で表形式 | pytest+Vitest+スモーク | PY-CHAT-06, VT-VIEW-12, SM-03 |
| AC-05-10 | 「↑ メモに保存」でアンカー保持昇格 | pytest+E2E | PY-NOTE-01, PW-08 |
| AC-05-11 | チャットが横断検索にヒット・スレッドへ遷移 | pytest+E2E | PY-SRCH-02/03, PW-14 |

### 6.8 docs/06 ライブラリ

| AC | 基準(要約) | レイヤ | テストID |
|---|---|---|---|
| AC-06-01 | 6 値ステータスの色一貫 | Vitest+VR | VT-UI-03, VR-1e |
| AC-06-02 | クイックフィルタ件数合計=総数 | pytest | PY-LIB-02 |
| AC-06-03 | ステータス非自動変更(設定 3 値) | pytest | PY-LIB-05 |
| AC-06-04 | 読了フロー全スキップ可+自動記録 | pytest+E2E | PY-LIB-06, PW-18 |
| AC-06-05 | 10 列テーブル+一括操作バー | Vitest+E2E | VT-LIB-02, PW-04 |
| AC-06-06 | 保存フィルタのサイドバー件数表示 | pytest+E2E | PY-LIB-07, PW-04 |
| AC-06-07 | キュー 6 本で「積みすぎかも?」+閉じられる | Vitest | VT-LIB-03 |
| AC-06-08 | コレクション(順序・締切・担当・発表時間・進捗)+共有順序反映 | pytest+E2E | PY-COL-01, PW-15 |
| AC-06-09 | 共有リンク: 匿名閲覧・noindex・許可メモのみ | pytest+E2E | PY-SHR-01/02, PW-15 |
| AC-06-10 | 通知 3 種+すべて既読 | pytest+E2E | PY-NTF-01, PW-19 |
| AC-06-11 | 「EMA teacher」クロス検索・源バッジ・該当位置へ(S5) | pytest+E2E | PY-SRCH-01/03, PW-14 |
| AC-06-12 | BibTeX が主要マネージャで読める | pytest | PY-EXP-02 |

### 6.9 docs/07 概要図・記事

| AC | 基準(要約) | レイヤ | テストID |
|---|---|---|---|
| AC-07-01 | 概要図全テキストの正常レンダリング(構造的保証) | pytest | PY-FIG-01 |
| AC-07-02 | 3 カードフロー・版バッジ・書き直し・SVG⤓ | Vitest+E2E | VT-VIEW-13, PW-13 |
| AC-07-03 | 再生成で版増・前版へ復帰 | pytest+E2E | PY-FIG-03, PW-13 |
| AC-07-04 | 解説図のプロバイダ切替生成 | pytest | PY-FIG-05, PY-LLM-07 |
| AC-07-05 | 重要情報はキャプション側(画像内テキスト非依存) | pytest+スモーク | PY-FIG-06, SM-04 |
| AC-07-06 | ラスターモード設定切替(既定 SVG) | pytest | PY-FIG-04, PY-SET-02 |
| AC-07-07 | 記事がモード切替から開ける | E2E | PW-13 |
| AC-07-08 | プリセット 4 種で生成・再生成 | pytest+E2E | PY-ART-01, PW-13 |
| AC-07-09 | 記事メタ行(AI生成・日付・免責) | Vitest | VT-VIEW-14 |
| AC-07-10 | ブロックホバー 3 操作 | Vitest+E2E | VT-VIEW-15, PW-13 |
| AC-07-11 | 根拠チップ・「原文で見る →」ジャンプ | E2E | PW-13 |
| AC-07-12 | 「議論したい点」由来バッジ | pytest+Vitest | PY-ART-02, VT-VIEW-16 |
| AC-07-13 | ライセンス不可→ブロック+リンクカード/可→クレジット自動付記 | pytest | PY-LIC-01, PY-ART-03 |
| AC-07-14 | 出典ブロック末尾固定・削除不可 | pytest+E2E | PY-ART-04, PW-13 |
| AC-07-15 | 読了フロー→記事モード遷移 | E2E | PW-18 |

### 6.10 docs/08 拡張

| AC | 基準(要約) | レイヤ | テストID |
|---|---|---|---|
| AC-08-01 | アプリ内取り込み UI 不在・拡張唯一 | E2E+拡張 | PW-02, XT-01 |
| AC-08-02 | 2 クリック以内で保存 | 拡張 | XT-03 |
| AC-08-03 | 書誌プレビュー+A 見込み判定 | 拡張 | XT-02 |
| AC-08-04 | 保存前指定(3 択・タグ・コレクション・メモ)+即時反映 | 拡張 | XT-03, XT-04 |
| AC-08-05 | Enter で保存 | 拡張 | XT-03 |
| AC-08-06 | 保存直後のパイプライン+「サイトで開く ↗」 | 拡張 | XT-04 |
| AC-08-07 | 既存論文で重複 UI なし・続きから/ステータス変更 | 拡張 | XT-05 |
| AC-08-08 | 一般 PDF: 警告+明示送信のみ+private | 拡張+pytest | XT-06, PY-ING-04 |
| AC-08-09 | 直近の取り込み 3 件 | 拡張 | XT-07 |
| AC-08-10 | ピル既定オフ・arXiv 限定・保存済み表示 | 拡張 | XT-08 |
| AC-08-11 | 琥珀ドット | 拡張 | XT-09 |
| AC-08-12 | 再起動後もキュー残存 | 拡張+Vitest | XT-10, VT-XTU-03 |
| AC-08-13 | activeTab 基本の審査通過権限 | Vitest | VT-XTU-01 |

### 6.11 docs/09 非機能

| AC | 基準(要約) | レイヤ | テストID |
|---|---|---|---|
| AC-09-01 | 性能テレメトリ(p50/p95 可視) | 性能+レビュー | PF-01, REV-03 |
| AC-09-02 | ジョブ強制終了→再実行で無破損(冪等・段階再開) | pytest | PY-JOB-01 |
| AC-09-03 | 用途別ルーティング=設定テーブル・再デプロイなし変更 | pytest | PY-LLM-01, PY-SET-02 |
| AC-09-04 | フォールバック連鎖継続+処理ログで判別 | pytest | PY-LLM-02, PY-ING-06 |
| AC-09-05 | BYOK: クォータ非消費・暗号化・マスク | pytest | PY-LLM-03(plans/04 §17-6), PY-SET-03 |
| AC-09-06 | クォータ表示+超過挙動(待機/BYOK 誘導) | pytest | PY-SET-04, PY-JOB-03 |
| AC-09-07 | 共有ページのみ匿名・他は認証必須 | pytest+E2E | PY-AUTH-01, PW-15 |
| AC-09-08 | ライセンス別の図表転載判定 | pytest | PY-LIC-01, PY-ART-03 |
| AC-09-09 | CSS トークン=確定デザイン値一致 | Vitest | VT-TOK-01 |
| AC-09-10 | 同一データ→バイト同一 SVG | pytest+プロパティ | PY-FIG-02, HP-05 |
| AC-09-11 | 日英クロス+源バッジ | pytest+E2E | PY-SRCH-01/02, PW-14 |

### 6.12 docs/10 ロードマップ(DoD)

| AC | 基準(要約) | レイヤ | テストID |
|---|---|---|---|
| AC-10-01 | M0: 拡張保存→1 分以内に読める(状態表示含む) | 拡張+性能 | XT-02〜05, PF-03 |
| AC-10-02 | M0: 3 モードで数式・図表が崩れない+品質バッジ | E2E+VR+pytest | PW-05, VR-1a/1b/1c, PY-PARSE-04 |
| AC-10-03 | M0: 選択質問→根拠チップ+ラベル区別 | E2E | PW-08 |
| AC-10-04 | M0: 閉じても「続きから↓」 | E2E | PW-09 |
| AC-10-05 | M1: トリアージ+読了フロー+P6 | E2E | PW-18, PW-19 |
| AC-10-06 | M1: 横断検索で 2 分以内に発見(S5) | E2E+性能 | PW-14, PF-06 |
| AC-10-07 | M1: PDF 拡張送信→品質 B+PDF モード突き合わせ | pytest+E2E | PY-ING-04, PW-12, XT-06 |
| AC-10-08 | M2: 記事モード+概要図 SVG DL | E2E | PW-13 |
| AC-10-09 | M2: 共有リンク(4c) | E2E | PW-15 |
| AC-10-10 | M2: 語彙 SRS+原文で見る | E2E | PW-20 |
| AC-10-11 | M2: LaTeX 主経路+B→A 昇格 | pytest+E2E | PY-PARSE-02, PY-ING-07, PW-11 |
| AC-10-12 | M3: arXiv 以外の対応ソース | —(M3 実装時に追加。v1 対象外) | — |
| AC-10-13 | 全期間: 品質指標の計測とリリース判定 | 性能+スモーク+レビュー | PF-01〜08, SM-01〜04, REV-04 |

### 6.13 docs/11 語彙帳

| AC | 基準(要約) | レイヤ | テストID |
|---|---|---|---|
| AC-11-01 | 「語彙に追加」1 アクションで文脈・出典・追加日自動 | pytest+E2E | PY-VOC-01, PW-20 |
| AC-11-02 | AI 8 フィールド生成・全編集可・編集は再生成非上書き | pytest | PY-VOC-02, PY-VOC-03 |
| AC-11-03 | 生成失敗でも保存+失敗理由+再試行 | pytest | PY-VOC-04 |
| AC-11-04 | 種別/復習期チップ絞り込み+ソート | pytest+Vitest | PY-VOC-05, VT-VOC-01 |
| AC-11-05 | 語彙検索が帳内のみ | pytest+Vitest | PY-VOC-05, VT-VOC-02 |
| AC-11-06 | 「原文で見る →」でハイライト付きビューア | E2E | PW-20 |
| AC-11-07 | SRS 規則どおりの更新+「次の復習」表示 | pytest+Vitest | PY-VOC-06, VT-VOC-03 |
| AC-11-08 | 復習期件数一致・サイドバー=総語数 | pytest+Vitest | PY-VOC-07, VT-VOC-04 |
| AC-11-09 | Markdown エクスポート(文脈+出典) | pytest | PY-VOC-08 |
| AC-11-10 | 用語集と独立 | pytest | PY-VOC-09 |

### 6.14 docs/12 リソース

| AC | 基準(要約) | レイヤ | テストID |
|---|---|---|---|
| AC-12-01 | URL 4 種の自動判定 | pytest | PY-RES-01 |
| AC-12-02 | 判定不能→article+種類変更可 | pytest | PY-RES-01, PY-RES-02 |
| AC-12-03 | メタ自動表示・失敗でも URL 登録完了 | pytest | PY-RES-02 |
| AC-12-04 | YouTube サムネ+再生時間バッジ | pytest+Vitest | PY-RES-02, VT-VIEW-17 |
| AC-12-05 | メモの §チップ→本文ジャンプ | pytest+E2E | PY-RES-04, PW-21 |
| AC-12-06 | 公式実装検出→提案→追加/無視(永続) | pytest+E2E | PY-RES-03, PW-21 |
| AC-12-07 | 件数バッジ=確定数 | pytest+Vitest | PY-RES-05, VT-VIEW-18 |
| AC-12-08 | 「開く ↗」新規タブ | Vitest | VT-VIEW-19 |
| AC-12-09 | 同一 URL 二重登録防止 | pytest | PY-DB-08, PY-RES-06 |
| AC-12-10 | 共有ページに非表示 | pytest | PY-SHR-03 |
| AC-12-11 | Markdown エクスポートにリソース | pytest | PY-EXP-01 |

## 7. 翻訳プレースホルダのプロパティテスト(Hypothesis)

対象: `yakudoku_core/translation/placeholder.py` の `protect(block) -> ProtectedText` / `restore(protected, llm_output) -> RestoredInlines` / `validate(protected, llm_output) -> ValidationResult`(docs/03 §4)。

### 7.1 生成規則(strategies 完全形)

`packages/py-core/tests/property/test_placeholder_props.py`:

```python
from hypothesis import given, settings, strategies as st

# プレースホルダ括弧 ⟦⟧(U+27E6/U+27E7)を含まない任意テキスト(和英混在・絵文字・結合文字を含む)
text_fragment = st.text(
    alphabet=st.characters(blacklist_characters="⟦⟧", blacklist_categories=("Cs",)),
    min_size=0, max_size=120,
)

inline_kind = st.sampled_from(["MATH", "CIT", "REF", "URL", "CODE", "FN"])

@st.composite
def inline_token(draw, index: int):
    kind = draw(inline_kind)
    return {"t": kind, "id": f"{kind.lower()}-{index}"}      # 例 ⟦MATH:math-3⟧

@st.composite
def block_inlines(draw):
    """text と非 text インラインの交互列(非 text 0〜30 個、docs/01 §4.2 の 8 種相当)。"""
    n = draw(st.integers(min_value=0, max_value=30))
    parts, idx = [], 0
    for _ in range(n):
        parts.append({"t": "text", "v": draw(text_fragment)})
        parts.append(draw(inline_token(idx))); idx += 1
    parts.append({"t": "text", "v": draw(text_fragment)})
    return parts

# 「LLM の応答」シミュレータ: 正常系=トークン順序の任意置換+周辺テキスト差し替え
@st.composite
def wellbehaved_llm_output(draw, tokens: list[str]):
    order = draw(st.permutations(tokens))
    glue = [draw(text_fragment) for _ in range(len(order) + 1)]
    return "".join(g + t for g, t in zip(glue, list(order) + [""])).rstrip()

# 異常系の変異: 削除 / 複製 / 1 文字改変 / 括弧欠け
mutation = st.sampled_from(["drop", "duplicate", "mutate_id", "break_bracket"])
```

### 7.2 プロパティ(HP-01〜04 確定)

| ID | プロパティ | 設定 |
|---|---|---|
| HP-01 | **往復不変**: `restore(protect(b), protect(b).text) == b`(LLM 恒等応答で元インライン列に完全復元。C9) | `max_examples=1000` |
| HP-02 | **順序置換の受理**: 任意の `wellbehaved_llm_output` に対し `validate` が合格し、`restore` 後の非 text インライン集合(id 付き)が元と一致・text 部分が応答テキストと一致 | `max_examples=1000` |
| HP-03 | **変異の拒絶**: トークンが 1 つ以上ある入力への `drop`/`duplicate`/`mutate_id`/`break_bracket` 変異はすべて `validate` 不合格(`placeholder_mismatch`)。誤って復元されない(`restore` は例外) | `max_examples=1000` |
| HP-04 | **検証の完全性**: `validate` 合格 ⟺ 「全トークンがちょうど 1 回ずつ出現」(正規表現による独立実装 oracle と一致)。トークン 0 個のブロックは常に合格 | `max_examples=500` |

- 実行設定: `settings(deadline=None)`、CI プロファイル `derandomize=False` + `print_blob=True`(失敗例の再現 blob をログに残す)。発見された反例は `@example(...)` として恒久固定する(回帰コーパス化)。
- 補助プロパティ(同ファイル): 数値検査 `number_mismatch` の oracle(原文と訳文の数値多重集合比較)を数値入りテキスト生成で検証する。

## 8. LLM 依存テストの構成(3 層+E2E モック)

### 8.1 第 1 層: FakeLLMProvider(決定的・既定)

- 実装: `packages/llm/src/yakudoku_llm/testing/fake_provider.py`(plans/04 §2)。pytest の既定であり、**マーカーなしの全テストは Fake のみを使う**。
- 決定的応答規則(確定):
  - `generate`: 最終 user メッセージ中のプレースホルダトークン(`⟦…⟧`)を抽出し、固定テンプレート `「(訳) {先頭40文字} {トークンを出現逆順に連結}」` を返す(翻訳検証ロジックが実際に働く応答)。
  - `generate_structured`: `json_schema.name` をキーに固定 JSON を返すルックアップ表(`overview_figure_dsl_v1` → §14 の Rectified Flow DSL、`vocab_entry_v1` → boil down to の 8 フィールド、`article_v1` → 6 ブロック記事、`chat_answer_v1` → `[[ev:1]]` +根拠 1 件+outside_knowledge 1 段落)。未知スキーマは `SCHEMA_VALIDATION` エラー(テスト書き漏れの検出)。
  - `stream`: `generate` 結果を 20 文字ごとの `text_delta` に分割し `start→delta*→usage→end` を送出。
  - 故障注入: `FakeLLMProvider(script=[...])` で呼び出し n 回目のエラー種(`RATE_LIMITED` 等)・不正応答(トークン欠落)を指定できる(PY-TR-02・PY-LLM-02・PY-VOC-04 用)。
  - `FakeImageProvider`: コミット済み 1024×1024 PNG(`packages/llm/tests/fixtures/fake_image.png`、8KB)を返す。
- usage は入力/出力文字数から決定的に算出(`tokens = ceil(chars / 4)`)し、価格計算テストと接続する。

### 8.2 第 2 層: 録画リプレイ(vcr)

- ツール: **vcrpy 7.0.0 + pytest-recording 0.13.4**(マーカー `replay`)。対象は各プロバイダアダプタの「実ワイヤ形式の解析」(ストリーミングフレーム・structured output・エラー本文)で、Fake では検証できない SDK 境界のみ。
- カセット: `apps/{api,worker}/tests/cassettes/` と `packages/llm/tests/cassettes/` に JSON で保存(コミット対象)。録画は実キーを持つ開発者が `pytest -m replay --record-mode=once` で行い、**CI は `--record-mode=none`**(ネットワーク完全遮断。カセット欠落は失敗)。
- 秘匿情報の除去(必須。録画フィルタで強制): `filter_headers=["authorization", "x-api-key", "api-key", "x-goog-api-key", "openai-organization", "set-cookie"]` + レスポンス中の `request_id` 系はそのまま(秘匿でない)。カセットに `sk-` / `AIza` が含まれないことを CI の grep 検査で二重チェックする。
- マッチング: `match_on=["method", "host", "path"]` + リクエスト本文はハッシュ比較しない(SDK のフィールド順差異を許容)。カセット 1 テスト 1 ファイル。
- 更新規則: SDK メジャー更新(Renovate PR)で `replay` スイートが失敗したら再録画する。カセットは 6 ヶ月以上放置しない(モデル ID の廃止追随。docs/09 §3.2)。

### 8.3 第 3 層: 実 API スモーク(SM-01〜04)

`RUN_LLM_SMOKE=1` のときのみ収集(CI の PR/merge ゲートでは常に skip)。夜間ワークフロー `.github/workflows/llm-smoke.yml`(cron `0 18 * * *` UTC = JST 03:00、`workflow_dispatch` 可)で実行し、失敗は GitHub Issue 自動起票(`peter-evans/create-issue-from-file`)。

| ID | 内容 | 上限コスト/回 |
|---|---|---|
| SM-01 | 疎通: 全テキスト 5 社(OpenAI/Anthropic/Google/DeepSeek/xAI)×既定モデルで 16 トークン生成+structured 1 件、画像 3 社(OpenAI/Google/xAI)×1 枚(plans/04 §17-8 と同一) | $0.30 |
| SM-02 | 翻訳品質サンプル: §14 の代表 8 ブロック(数式・引用・用語入り)を既定翻訳モデルで実翻訳し、プレースホルダ検証合格・用語スナップショット順守・「だ・である」調(文末正規表現)を機械判定 | $0.05 |
| SM-03 | チャット品質サンプル: 「この論文で ImageNet の結果は?」(記載なし)→回答に根拠チップ 0 件+記載がない旨の定型判定(「記載」「述べられていない」等の語彙マッチ)、「実験設定の整理」→ Markdown 表(`|` 行)を含む | $0.10 |
| SM-04 | 解説図 1 枚実生成: PNG デコード可能・1536×1024・プロンプトに文字排除指示が入った状態で生成成功 | $0.15 |

- 決定: スモークの品質判定は「機械判定可能な必要条件」のみとする(LLM-as-judge は導入しない。フレークをゲートにしないため)。品質の定性評価は REV-04(リリース判定)で docs/03 §5 の対訳例集を用いて行う。

### 8.4 E2E 用モック LLM サーバ

- 実装: `packages/llm/src/yakudoku_llm/testing/mock_server.py`(FastAPI、ポート 8090)。§8.1 と同一の決定的応答規則を HTTP で提供する。エミュレートするエンドポイント: `POST /openai/v1/responses`・`POST /openai/v1/images/generations`(OpenAI)/ `POST /anthropic/v1/messages`(Anthropic、SSE 対応)/ `POST /google/v1beta/models/{model}:generateContent`・`:streamGenerateContent`・画像(Google)/ `POST /deepseek/chat/completions`(DeepSeek)/ `POST /xai/v1/chat/completions`・`/xai/v1/images/generations`(xAI)。
- 接続方法: api/worker はプロバイダ別ベース URL 上書き環境変数(§15 ⚠-2)を `http://localhost:8090/{provider}` に設定して起動する。API キーは `test-stub` 固定。
- arXiv・外部メタ取得も同サーバに同居させる: `GET /arxiv/abs/{id}`(フィクスチャ HTML)・`GET /arxiv/e-print/{id}`(フィクスチャ tar.gz)・`GET /arxiv/api/query`(Atom)・GitHub/YouTube oEmbed 相当。api/worker は `YAKUDOKU_ARXIV_BASE_URL=http://localhost:8090/arxiv` で参照する(§15 ⚠-3)。

## 9. ビジュアルリグレッション(確定デザイン 16 画面)

### 9.1 運用方式(確定)

- 方式: Playwright `expect(page).toHaveScreenshot()`。プロジェクト `visual`、実行は**必ず** Docker イメージ `mcr.microsoft.com/playwright:v1.53.2-noble` 内(フォントラスタライズ差の排除。ローカルも `pnpm --filter @yakudoku/web e2e:vr` が docker run でラップする)。フォントは Google Fonts をテスト時のみローカル同梱(`e2e/fixtures/fonts/` に woff2 を置き `context.route` で差し替え。CI の外部フォント取得を禁止し決定性を確保)。
- しきい値(確定): 全画面共通 `maxDiffPixelRatio: 0.001`(1440×900 = 1,296,000px 中 約 1,296px)+ ピクセル単位 `threshold: 0.2`。例外: VR-2a(PDF.js canvas)のみ `maxDiffPixelRatio: 0.002`。`animations: "disabled"`・`caret: "hide"`・日時表示要素は `data-vr-mask` 属性を付け `mask` オプションで塗りつぶす(「昨日 21:52」等の相対時刻)。シード日時は mock clock(`page.clock.setFixedTime("2026-07-06T09:00:00+09:00")`)で固定する。
- 基準画像: `apps/web/e2e/vr/__screenshots__/`(VR-3a-1〜4 のみ `apps/extension/e2e/__screenshots__/`。Git LFS ではなく通常コミット。PNG 19 枚 = web 15 画面+拡張 4 状態 ≈ 数 MB)。**初回基準の採択手順**: 実装画面のスクリーンショットを確定デザイン(`論文読解システム デザイン.dc.html` の該当画面)と並置した比較シートを PR に添付し、デザイン抽出ファイル(extract/<画面ID>.md。plans/08 §1 と同一の参照)の数値との目視照合レビューを通ったものだけを基準化する。以後の更新は `--update-snapshots` + PR の before/after 差分画像レビュー必須(基準画像の無説明更新は CI の CODEOWNERS で `plans/12` 責任者レビューを強制)。
- 対象状態はライトモード。ダークは VR-1c の 1 枚で代表する(全 16 画面×2 テーマはメンテコストが利益を超えるため。テーマ切替の正しさは VT-TOK-01 と PW-17 が担保)。

### 9.2 画面と前提状態(VR-*)

すべて §14 シードデータを表示し、viewport 1440×900・固定時刻で撮影する。撮影枚数は計 19 枚(下表 15 行の web 画面 15 枚+VR-3a の拡張 4 状態。デザイン画面数としては 3a を 1 画面と数えて 16 画面)。決定: VR-3a-1〜4 は §5.1 の拡張 E2E コンテキスト(`apps/extension/e2e/vr.spec.ts`)で撮影する(ポップアップは拡張ロードが必要なため web の `visual` プロジェクトでは撮影できない)。Docker イメージ・`toHaveScreenshot` 設定・マスク規則は §9.1 と同一。

| ID | 画面 | ルート・状態 |
|---|---|---|
| VR-1a | 対訳・高密度 | `/papers/{li}?mode=parallel`、チャットタブ・根拠チップ付き回答表示 |
| VR-1b | 訳文・ゆったり | `?mode=translation`、注釈タブ・対訳ポップ開・選択メニュー表示 |
| VR-1c | ダーク+図表 | `?mode=translation`・`data-theme=dark`、図表参照ポップオーバー開・図表タブ |
| VR-1d | ダッシュボード | `/dashboard`(続き 3・キュー 6 本で警告・締切・最近追加・統計 12 週) |
| VR-1e | ライブラリ(表) | `/library?view=table`、検索ドロップダウン開・2 行選択で一括バー |
| VR-1g | 読了フロー | ビューア上に読了モーダル(理解度 4/5 選択状態) |
| VR-1h | 記事モード | `?mode=article`、概要図 版 2・ブロックホバー状態 |
| VR-2a | PDF モード | `?mode=pdf`、p.5・bbox 選択チップ・情報パネル |
| VR-3a-1〜4 | 拡張 4 状態 | ポップアップページ直接表示(幅 372px・高さ auto の fullPage 撮影)。保存前/保存直後/既にライブラリ/一般 PDF |
| VR-4a | ライブラリ(カード)+通知 | `/library?view=cards`、通知ポップオーバー開(未読 3 種) |
| VR-4b | コレクション詳細 | `/collections/{id}`(締切・担当・共有リンク発行済み) |
| VR-4c | 共有ページ | `/c/{token}`(匿名コンテキスト) |
| VR-4d | 語彙帳 | `/vocab`(復習期 4・詳細パネル boil down to) |
| VR-4e | 横断検索 | `/search?q=EMA%20teacher`(確定デザイン 4e と同一クエリ。§14 シードのメモ・チャット・記事に「EMA teacher」を含む文があり、源バッジ 4 種=本文/チャット/メモ/記事が揃う) |
| VR-4f | 設定 | `/settings/translation`(翻訳カテゴリ) |
| VR-5a | リソースタブ | ビューア・リソースタブ(4 種+公式提案カード) |

## 10. SVG 概要図の決定性テスト

### 10.1 レンダラへの要求仕様(テストが強制する契約)

`yakudoku_core/figures/svg_renderer.py` は以下を満たす(違反は PY-FIG-02 / HP-05 で検出):

1. 出力に**タイムスタンプ・乱数・環境依存値を含めない**(生成日時はメタデータに持たずDB 側 `generated_at` のみ)。
2. 要素 ID は DSL から決定的に導出(`card-0` / `connector-0-1`)。
3. 数値は `f"{v:.2f}"` で固定小数 2 桁に正規化(浮動小数の表現ゆれ排除)。
4. 属性順序はテンプレート文字列で固定(dict 順序・シリアライザに依存しない)。
5. 文字コード UTF-8(BOM なし)・改行 `\n`・XML 宣言 `<?xml version="1.0" encoding="UTF-8"?>` 固定。
6. フォントは名前参照(`IBM Plex Sans JP`)のみで埋め込まない。テキスト折返しは自前の決定的アルゴリズム(文字数ベース)で行い、ブラウザ計測を使わない。
7. 色はデザイントークン値をリテラル展開(packages/tokens の JSON をビルド時取り込み)。

### 10.2 テスト(確定)

| ID | 内容 |
|---|---|
| PY-FIG-02 | (a) §14 の Rectified Flow DSL を 2 回レンダリング→ `bytes` 完全一致。(b) ゴールデンファイル `packages/py-core/tests/golden/overview_rectified_flow.svg` と sha256 一致(意図的変更時はゴールデン更新を同一 PR で行い、差分レビュー)。(c) 生成 SVG が `xml.etree` で well-formed |
| HP-05 | Hypothesis: 任意の妥当な OverviewDslJson(cards 2〜3 枚・見出し/本文は §7.1 の text_fragment・emphasis 3 値)に対し、(a) 2 回レンダリングのバイト同一 (b) 全カードテキストが SVG 中に出現 (c) 出力にマイクロ秒/日付パターン(正規表現 `\d{4}-\d{2}-\d{2}` / `\d+\.\d{3,}`)が出現しない。`max_examples=300` |
| PY-FIG-02b | プロセス独立性: レンダリングを `subprocess` で 2 プロセス実行して一致(`PYTHONHASHSEED` 依存の検出)。CI では `PYTHONHASHSEED=random` を明示設定 |

## 11. 検索の日英クロステストデータ

### 11.1 固定フィクスチャ(`apps/api/tests/fixtures/search_corpus.py`。§14 シードにも同一データを含める)

| # | 種別 | 原文(source_text) | 訳文(text_ja) | 位置 |
|---|---|---|---|---|
| S1 | 本文 | "Rectified flow learns straight transport paths between two distributions." | 「整流フロー(rectified flow)は 2 つの分布間の直線的な輸送経路を学習する。」 | §1 ¶1 |
| S2 | 本文 | "We use an EMA teacher to stabilize distillation." | 「蒸留を安定させるため EMA teacher を用いる。」(原語併記なしの原語残し) | §3 ¶2 |
| S3 | 本文 | "the training objective boils down to a least squares regression" | 「学習目的は最小二乗回帰に帰着する」 | §2.1 ¶4 |
| S4 | メモ | — | 「reflow の反復回数と直線性の関係を後で確認」 | note |
| S5 | 注釈 | quote="straight transport paths" | comment=「拡散モデルとの違いはここ」 | annotation(idea) |
| S6 | チャット | — | assistant text_plain に「1 回の reflow で経路がほぼ直線になります」 | chat メイン |
| S7 | 記事 | — | article_block text_plain に「整流フローを一言でいえば『まっすぐ流す』ことです」 | article ¶2 |

### 11.2 期待結果(PY-SRCH-01/02 のアサーション表)

| クエリ | 期待ヒット | matched_in / source |
|---|---|---|
| `整流フロー` | S1(訳文)+ S7(記事) | S1: `["translation"]` / S7: `article` |
| `rectified flow` | S1(原文+訳文の原語併記 → **1 件に統合**) | `["source","translation"]` |
| `EMA teacher` | S2(原文と訳文の両方にヒット → 1 件統合。docs/06 §11 の S5 例) | `["source","translation"]` |
| `least squares` | S3(原文) | `["source"]` |
| `最小二乗` | S3(訳文)+ S6(チャット) | S3: `["translation"]` / S6: `chat` |
| `reflow` | S4(メモ)+ S6(チャット) | `note` / `chat` |
| `まっすぐ` | S7(記事) | `article` |
| `transport paths` | S1(原文)+ S5(注釈 quote) | `["source"]` / `annotation` |
| `存在しない語XYZQ` | 0 件(total=0・facets 全 0) | — |

- 追加アサーション: 論文グループ化(S1〜S3 は同一 library_item に集約)・facets の source 件数がヒット数と一致・snippet の `<mark>` が該当語のみを囲む・`snippet_lang` が S1 原文=en / 訳文=ja。
- 権限系(PY-SRCH-05): 同一コーパスを別ユーザーにも複製し、クエリ結果が自分の library_item 分のみであることを件数で検証する。
- PW-14 はこの表の `EMA teacher` と `整流フロー` の 2 クエリを UI で再生し、源バッジ色(本文=青/チャット=紫/メモ=緑/記事=グレー)と源別遷移を確認する。

## 12. パフォーマンステスト(docs/09 §1 の計測方法)

### 12.1 三層の計測(確定)

1. **PF(合成負荷・夜間)**: `.github/workflows/perf.yml`(cron `0 17 * * *` UTC = JST 02:00)。docker compose 一式+シード(`--sample rectified-flow --reset --scale 300`: ライブラリ 300 件・翻訳済み 3 論文)+ §8.4 モック LLM(遅延注入: 翻訳 1 ブロック 300ms 固定)。しきい値超過でワークフロー失敗。
2. **本番テレメトリ(常時)**: plans/01 §9.4 のメトリクス(`http_request_duration_seconds` / `job_duration_seconds` / `chat_first_token_seconds`)+ Grafana。p50/p95 の実測の正はこちら(AC-09-01)。
3. **LLM 実測(月次手動)**: LLM 実時間に支配される目標(取り込み 20s/60s/5min・チャット初回トークン・記事・解説図・語彙)は合成環境で意味のある値が出ないため、本番相当環境+実プロバイダで月 1 回 `RUN_LLM_SMOKE=1 uv run pytest -m smoke -k perf` として計測し、REV-04 の判定資料にする。

### 12.2 目標→計測の対応(全 11 目標)

| docs/09 §1 の操作 | p50/p95 目標 | 計測 | ID |
|---|---|---|---|
| ライブラリ・ダッシュボード表示 | 1s / 3s | k6: `GET /api/library-items(300件)`・`GET /api/dashboard` + Playwright `navigation → networkidle` 実測(各 20 回、中央値/95%点) | PF-02 |
| ビューア初期表示(翻訳済み) | 2s / 5s | Playwright: `/papers/{li}` 遷移→本文初回描画(`data-testid=viewer-body` 可視)まで。k6: `GET /viewer` + `GET /document?section_id` | PF-04 |
| 保存→カード表示 | 3s / 10s | ingest API 呼出→`GET /api/ingest/recent` に出現するまでポーリング(モック LLM) | PF-03 |
| アブスト訳+3行要約 | 20s / 60s | 同上、stage=`translating_abstract` 完了まで。**実測は三層目**(モックでは配管遅延のみを 5s 上限で監視) | PF-03 / SM 実測 |
| readable | 60s / 3min | 同上、stage=`readable` まで(モックは 15s 上限の配管監視) | PF-03 / SM 実測 |
| 全文翻訳(〜10p) | 5min / 15min | 三層目(実プロバイダ)+本番 `job_duration_seconds{kind="ingest",stage="translating_body"}` | SM 実測 |
| チャット初回トークン | 5s / 15s | 本番 `chat_first_token_seconds`。合成では SSE `start`→初回 `delta` を 1s 上限で配管監視 | PF-05 |
| 記事初回生成 | 30s / 90s | 三層目+本番 `job_duration_seconds{kind="article"}` | SM 実測 |
| 解説図 1 枚 | 20s / 60s | 三層目+本番 `job_duration_seconds{kind="figure"}` | SM 実測 |
| 語彙保存時 AI 生成 | 3s / 10s | 三層目+本番 `job_duration_seconds{kind="vocab"}` | SM 実測 |
| 横断検索・全文検索 | 1s / 3s | k6: `GET /api/search?q=`(§11 コーパス×300 論文分の索引)p95<3000ms、`GET /api/search/preview` p50<300ms(plans/03 §15.2) | PF-06 |

| 追加 | 目標 | 計測 | ID |
|---|---|---|---|
| テレメトリ存在検証 | — | 統合テスト: `/metrics` に plans/01 §9.4 の全メトリクス名が露出(api・worker) | PF-01(pytest, integration) |
| 拡張 check 応答 | p50 500ms | k6: `GET /api/ingest/check`(Redis キャッシュヒット時) | PF-07 |
| 用語変更→再翻訳反映 | 1 分以内(docs/03 §12) | 合成: glossary PATCH(用語 `distillation`。§14 `document.json` はこの語の出現をちょうど 12 ブロックに固定する)→影響 12 ブロックの unit 更新完了まで(モック LLM 300ms/ブロック)< 60s | PF-08 |

- k6 スクリプトは `tools/perf/k6/{library,viewer,search,ingest}.js`。共通設定: `vus: 10, duration: "60s"`、`thresholds: { http_req_duration: ["p(50)<…", "p(95)<…"] }`(値は上表)。実行環境は CI ランナー(ubuntu-24.04)固定とし、結果はワークフローの artifact(JSON)として 90 日保存、推移は Grafana ではなく artifact 比較スクリプト `tools/perf/compare.py`(前回比 +30% で警告)で追う。

## 13. CI ゲート(必須チェックの確定)

### 13.1 PR / main のマージゲート(すべて必須。plans/00 §8 の 5 ジョブ+本書追加分)

| チェック | 内容(本書の追加分を含む) |
|---|---|
| `js` | lint / typecheck / Vitest(VT-* 全部)/ build / prettier |
| `python` | ruff / mypy(strict)/ pytest `-m "unit or property or integration or replay"`(HP-*・PY-* 全部)+ カバレッジ 80%・重点 2 モジュール 100%(§2.1)+ `alembic upgrade→downgrade→upgrade`(PY-DB-02)+ カセット秘匿 grep(§8.2)+ トレーサビリティ網羅チェック(§6) |
| `openapi-drift` | 生成クライアントのドリフト検出(plans/00 §8) |
| `e2e` | Playwright `e2e` プロジェクト(PW-01〜22)+ `visual` プロジェクト(VR-*)+ 拡張 E2E(XT-01〜10)。LLM・arXiv は §8.4 モック |
| `extension` | Chrome / Edge zip ビルド+ manifest 検査(VT-XTU-01 は js ジョブ側) |

- フレーク方針(確定): Playwright は `retries: 2`(CI)。**リトライで通った(flaky)テストは成功扱いだがレポートに `flaky` として集計され、同一テストが 7 日間に 3 回 flaky になったら Issue 自動起票**(Playwright JSON レポートを `tools/flaky/report.py` が集計)。pytest はリトライしない(フレーク=バグとして扱う)。
- 実 LLM キーは PR CI に一切渡さない(fork PR の秘匿漏えい防止。`OPENAI_API_KEY: test-stub` 固定)。

### 13.2 夜間・定期ワークフロー(マージゲート外)

| ワークフロー | cron(UTC) | 内容 |
|---|---|---|
| `llm-smoke.yml` | `0 18 * * *` | SM-01〜04(実キー。GitHub Environments の secrets、main ブランチのみ) |
| `perf.yml` | `0 17 * * *` | PF-02〜08(k6 + Playwright 計測) |
| `deps-audit.yml` | `0 20 * * 1` | `pnpm audit` / `uv run pip-audit` / Playwright イメージのダイジェスト更新確認 |

### 13.3 リリース前チェックリスト(自動化しない確認。タグ作成 PR のテンプレート)

1. SM-01〜04 直近 7 日間グリーン。2. REV-01〜04 実施。3. クロスブラウザ手動確認(Safari / Firefox 最新 2 版で PW-05/PW-09 相当を目視)。4. 本番テレメトリの p95 が docs/09 §1 目標内。5. VR 基準画像とデザイン抽出ファイルの照合(変更があった画面のみ)。

## 14. サンプルデータ: Rectified Flow シード(C10)

### 14.1 入口と配置(確定)

- 実行: `uv run python -m yakudoku_api.seed --sample rectified-flow [--reset] [--scale N]`(plans/00 §8/§9 の呼び出しと同一。`--reset` は既存シードを削除して再投入、`--scale N` は PF 用にダミー論文を N 件複製)。
- フィクスチャ配置: `apps/api/src/yakudoku_api/seed_data/rectified_flow/` に JSON、`assets/` にバイナリ。

```
seed_data/rectified_flow/
  bib.json                # 書誌(arXiv:2209.03003, ICLR 2023, authors, abstract, abstract_ja, summary_lines ①②③)
  document.json           # DocumentContentJson(quality A)。§1〜§3+付録A冒頭の抜粋 約120ブロック
                          #  — 11 ブロック型・インライン 8 種・数式 60 式・図 4・表 2・脚注・定理を網羅
  translation_natural.json# 全対象ブロックの自然訳(shared・complete)。§11 の検索コーパス文を含む
  translation_literal.json# 直訳(§1 のみ partial — オンデマンド途中状態の再現)
  glossary_global.json    # 分野別定訳シード(rectified flow=整流フロー, distillation=蒸留 ほか 20 語)
  chat.json               # メインスレッド 6 メッセージ(根拠チップ・outside_knowledge・error 1 件。1 往復は「EMA teacher」を含む — VR-4e/PW-14 の源バッジ 4 種用)
  annotations.json        # 4 色×各 2+コメント 2+ブックマーク 1+未配置 1
  notes.json              # 2 件(1 件はチャット昇格・アンカー付き。1 件は「EMA teacher」を含む — VR-4e 用)
  vocab.json              # word/collocation/idiom 各 1+復習期 4 件分の srs 状態
  resources.json          # github(公式・active)/youtube/slides/article+suggested 1+dismissed 1
  article.json            # preset=beginner・版 1・全ブロック型(attribution 含む)。paragraph 1 つに「EMA teacher」を含む(VR-4e 用)
  overview_dsl.json       # 版 1・版 2 の DSL(版 2 が current)— PY-FIG-02 ゴールデンの源
  assets/
    fig-1.png … fig-4.png # 図プレースホルダ(自作)
    thumbnail.png
    explainer-0.png       # 解説図(自作ダミー)
    sample-b.pdf          # 自作 2 ページ PDF(品質 B 論文用・テキストレイヤあり)
    arxiv-abs.html        # §8.4 フィクスチャサーバが返す abs ページ
    eprint.tar.gz         # 縮約 LaTeX ソース(自作。PY-PARSE-02 と昇格テスト用)
```

### 14.2 投入内容(確定)

| エンティティ | 内容 |
|---|---|
| users | `dev@yakudoku.test`(主)・`member@yakudoku.test`(共有/担当用)。パスワードなし(メールリンク) |
| papers | (1) Rectified Flow(public・A・license `cc-by-4.0`)※実論文の実ライセンスに関わらずシードでは転載可経路のテストのため cc-by-4.0 を設定 (2) `sample-b.pdf` 由来 private 論文(B・license unknown — 転載不可経路)(3) `--scale` 時のダミー書誌 N 件 |
| library_items | dev に 12 件(6 ステータス×2。タグ・優先度・締切・理解度・提案タグ・queue_order を分散)。Rectified Flow は status=reading・進捗 42%・前回位置 §2.1 |
| translations | 自然訳 shared complete+直訳 partial+personal フォーク 1 unit(edited) |
| collection | 「輪読会 2026-07」: 5 論文・締切 2026-07-16・担当 2 名・発表 25 分・予備注記・共有トークン発行済み(include_notes=true) |
| reading_sessions | 直近 12 週分(1d 統計棒グラフ用の決定的な分布) |
| notifications | 3 種各 1(未読 2・既読 1) |
| jobs | complete の ingest 1(タイムライン 3 段+処理ログ)+ translating_body 68% の進行中 1(PW-03 用) |
| saved_filters | 「締切あり」1 件 |

- ライセンス注記(確定): `document.json` は実論文の**抜粋+要約的短縮**(各セクション先頭 2 段落+全図表キャプション+数式)であり全文を含めない。全文での動作確認が必要な開発者は `python -m yakudoku_api.seed --sample rectified-flow --full` で実 arXiv から取得しローカル生成する(`--full` の生成物はコミット禁止・.gitignore 対象)。
- 決定: pytest のファクトリ既定 content(§2.3 `make_revision`)・E2E・VR・PF はすべてこの同一フィクスチャを使う。テストデータの正を 1 箇所(seed_data)に集約し、期待値(「§2.1 ¶4」「式(5)」「図2」等)のドリフトを防ぐ。

## 15. ⚠ 基盤への追加要求

本書の作成にあたり基盤計画書(plans/00〜08)に以下の不足・不整合を発見した。**本書は各項の「本書の暫定」を前提に書かれている**。基盤側の修正時は本書も追随する。

1. **⚠ 基盤への追加要求(plans/00)**: §4.5 と §8 のコメントが「plans/08 テスト計画」を参照しているが、plans/08 はデザインシステムである。参照先を本書 `plans/12-testing.md` に修正すること。
2. **⚠ 基盤への追加要求(plans/04 §16)**: E2E・統合テストでプロバイダをローカルモック(§8.4)へ向けるためのベース URL 上書き環境変数が未定義。`YAKUDOKU_OPENAI_BASE_URL` / `YAKUDOKU_ANTHROPIC_BASE_URL` / `YAKUDOKU_GOOGLE_BASE_URL` / `YAKUDOKU_DEEPSEEK_BASE_URL` / `YAKUDOKU_XAI_BASE_URL`(未設定時は各 SDK 既定)を plans/04 §16 に追加すること。
3. **⚠ 基盤への追加要求(plans/01 §8.4)**: arXiv アクセスのベース URL 上書き `YAKUDOKU_ARXIV_BASE_URL`(既定 `https://arxiv.org`)を追加すること(§5・§8.4 のフィクスチャサーバ利用に必須)。
4. **⚠ 基盤への追加要求(plans/01 §4.2 と plans/02 §4.13 の不整合)**: `jobs` テーブルの DDL が二重定義され、kind の値域(plans/01: `ingest_paper` 等 12 種 / plans/02: 7 種)・status 綴り(`cancelled`/`canceled`)・PK 型(TEXT ULID / UUID)が食い違う。plans/03 §1.7 の API `Job.kind`(13 種)とも一致しない。どちらかを正として統一すること。本書の暫定: **DB は plans/02、API 契約は plans/03 を正**とし、kind の写像はアプリ層に置く前提でテスト(PY-JOB-*)を記述した。
5. **⚠ 基盤への追加要求(plans/02 §4.6 と plans/03 §1.6 の不整合)**: ステータス列挙が DB(`to_read/read_soon/reading/finished/revisit/on_hold`)と API(`planned/up_next/reading/done/reread/on_hold`)で異なるが、写像がどこにも確定していない。対応表(to_read↔planned, read_soon↔up_next, finished↔done, revisit↔reread)を plans/02 か 03 に明記すること。本書は PY-DB-05(DB 値)・PY-LIB-*(API 値)でこの対応表を前提にした。
6. **⚠ 基盤への追加要求(plans/00 §2 と plans/04 §2 の不整合)**: LLM 抽象化層の配置が plans/00(`packages/py-core` 内 `yakudoku_core.llm`)と plans/04(`packages/llm` = `yakudoku_llm`)で食い違う。本書は **plans/04 を正**(`packages/llm`・`FakeLLMProvider` は `yakudoku_llm.testing`)としてテストパスを記述した。plans/00 §2 を修正すること。
7. **⚠ 基盤への追加要求(plans/00 §9 と plans/02 §7 の不整合)**: シード入口が `python -m yakudoku_api.seed`(plans/00)と `apps/api/scripts/seed_dev.py`(plans/02)で食い違う。本書は plans/00 の **`python -m yakudoku_api.seed`** を正とした。
8. **⚠ 基盤への追加要求(plans/01 §6 と plans/03 §1.3 の不整合)**: CSRF 対策が「ダブルサブミット」(plans/01)と「Origin 検証のみ」(plans/03)で食い違う。PY-AUTH スイートは plans/03(Origin 検証)を前提に書いた。統一すること。
9. **⚠ 基盤への追加要求(拡張のテスト用フック)**: ポップアップ E2E(§5.1)のため、`WXT_E2E=1` ビルド時のみ有効な `popup.html?tab_url=` の現在タブ上書きを apps/extension 実装に含めること(plans/00 §2 の apps/extension 構成に 1 ファイル `lib/e2e-hooks.ts` 追加)。

## 16. 本書の受け入れ基準

- [ ] docs/00〜12 の全 158 チェックリスト項目が §6 の表に 1 行ずつ存在し、REV 以外の全行に自動テスト ID が割り当てられている(`tools/traceability/check.py` が CI で件数突合)
- [ ] HP-01〜04 が hypothesis 6.135.26 で通り、意図的な変異注入(§7.2 の 4 種)を 100% 拒絶する
- [ ] PY-FIG-02 / HP-05 により、同一 DSL からの SVG がプロセス・実行をまたいでバイト同一である
- [ ] CI(PR)が実 LLM・実 arXiv へ一切接続せず(ネットワーク遮断の record-mode=none とモックサーバのみ)、`RUN_LLM_SMOKE=1` なしで smoke が収集されない
- [ ] VR の全 19 枚(web 15 画面+拡張ポップアップ 4 状態)が固定 Docker イメージ内で安定して再現し、`maxDiffPixelRatio 0.001`(2a のみ 0.002)で 10 回連続グリーン
- [ ] §11 の検索コーパスで PY-SRCH-01 の期待結果表が全行成立する
- [ ] `python -m yakudoku_api.seed --sample rectified-flow --reset` 後に PW-01〜22・VR-* が順不同で全通過する(テスト間独立性)
- [ ] §15 の基盤側修正が反映された時点で、本書の該当参照(ジョブ kind・ステータス写像・LLM パッケージパス)が追随更新されている
