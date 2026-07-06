# M0 — コア読解体験(MVP)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 拡張から arXiv 論文を保存すると1分以内に読み始められ、数式・図表が崩れない日本語で読み通せ、選択して質問すると根拠つきで答えが返り、読みかけ位置が保存される — を実際にビルド・起動・テストが green の状態で成立させる(docs/10 §2 の M0 DoD)。

**Architecture:** pnpm+Turborepo+uv workspace のモノレポ。`apps/web`(Next.js 15/React 19/Tailwind v4)+ `apps/api`(FastAPI/SQLAlchemy 2)+ `apps/worker`(arq)+ `apps/extension`(WXT/MV3)+ `packages/{tokens,api-client,py-core,llm}`。データ基盤は docker-compose(PostgreSQL16+PGroonga / Redis7 / MinIO / Mailpit)。LLM は実プロバイダ既定 + 決定的 Fake/モックでテスト駆動。

**Tech Stack:** Node 24.4.0 / pnpm 10.13.1 / Python 3.12 / uv / Next.js 15.4.2 / React 19.1.0 / TypeScript 5.8.3 / Tailwind CSS 4.1.11 / FastAPI 0.115.14 / SQLAlchemy 2.0.41 / Alembic 1.16.2 / Pydantic 2.11.7 / arq 0.26.3 / PostgreSQL 16+PGroonga 4.0.1 / Redis 7.4 / WXT 0.20.7 / KaTeX 0.16.22。

## Global Constraints

- **正の優先順位**: 見た目=確定デザイン16画面 → 機能=docs/00〜12 → 実装詳細=plans/00〜13。食い違い時はこの順で正。
- **識別子の正**: テーブル/カラム=plans/02、エンドポイント/型=plans/03、トークン/CSS変数/コンポーネント名=plans/08、LLM タスク名=plans/04。日本語説明文でも識別子は英語のまま。
- **DDL は M0 で全量投入**: plans/02 §4 の全テーブル・全インデックス・PGroonga索引・トリガを初期マイグレーション1本(`0001_initial_schema.py`)で投入(plans/13 §1.4)。以後のマイグレーションは逸脱修正のみ。
- **未実装スコープの UI は非表示**(グレーアウトしない): M0 のサイドパネルは チャット/図表/情報 の3タブ、表示モードは 訳文/対訳/原文 の3つのみ表示(plans/13 §1.5)。ライブラリテーブルは10列全描画、未供給列は「—」。ログイン後リダイレクトは `/library`。
- **UI 文言は日本語のみ**、plans/09-screens/ の逐語。勝手な言い換え禁止。i18n 層は導入しない。
- **命名**: TS 変数/関数=camelCase、React コンポーネント=PascalCase.tsx、フック=`use-*.ts`、TS 非コンポーネント=kebab-case.ts、Python=snake_case、DB テーブル=snake_case 複数形、API パス=`/api/` + kebab-case 複数形、JSON フィールド=snake_case。
- **主キー**: 原則 `id UUID DEFAULT gen_random_uuid()`。明細系(translation_units/chat_messages/block_search_index/notifications/reading_sessions/usage_records/article_blocks)は `BIGINT GENERATED ALWAYS AS IDENTITY`(plans/02 §1.1)。ブロック安定 ID `blk-` は決定的生成(docs/01 §4.3)。
- **列挙は TEXT + CHECK**(PostgreSQL ENUM 不使用、plans/02 §1.1)。
- **import ルール**: TS は `@/` エイリアス(親方向 `../` 禁止)、apps 間 import 禁止。Python は絶対 import のみ、apps/api↔apps/worker の相互 import 禁止(共有は packages/py-core・packages/llm)。生成物(api-client/generated・tokens/dist)は手編集禁止。
- **秘匿値に `NEXT_PUBLIC_`/`WXT_` 接頭辞を付けない**(CI grep 検査)。LLM モデル ID をコード・環境変数にハードコードしない(DB 設定テーブル参照)。
- **P1 忠実性**: AI 生成物には「AI生成」「論文外の知識」ラベルと原文アンカー(根拠チップ)を付ける。**P3 黙って壊れない**: 翻訳失敗ブロックは原文+理由+再試行を表示、勝手に消さない・状態変更しない。**P6**: ステータス自動変更しない(提案のみ)。
- **基準ビューポート**: 1440×900px。ダークモード対応。E2E は Chromium のみ。
- **開発シード**: Rectified Flow(arXiv:2209.03003)。全テスト・VR・開発の共通データ源。
- **TDD**: 各機能は失敗するテストを先に書き、最小実装で green にし、頻繁にコミットする。DB を使うテストは docker-compose の実 PostgreSQL に対して実行(SQLite 代替禁止 — PGroonga 依存)。

---

## フェーズ P0 — 基盤(直列)

### Task 0: 開発ランタイムの導入(Node 24.4.0 + pnpm 10.13.1)

現環境に node/pnpm が無い(uv/docker/python3.12 は導入済み)。プロジェクト固定バージョンを導入する。

**Files:**
- Create: `.node-version`(内容: `24.4.0`)
- Create: `.python-version`(内容: `3.12.11` — 現環境の 3.12.3 で代替可、`uv python install` で 3.12.11 を取得)

- [ ] **Step 1: Node 24.4.0 を ~/.local へ導入**

```bash
cd /tmp
curl -fsSLO https://nodejs.org/dist/v24.4.0/node-v24.4.0-linux-x64.tar.xz
mkdir -p ~/.local/node-24.4.0
tar -xJf node-v24.4.0-linux-x64.tar.xz -C ~/.local/node-24.4.0 --strip-components=1
ln -sf ~/.local/node-24.4.0/bin/node ~/.local/bin/node
ln -sf ~/.local/node-24.4.0/bin/npm ~/.local/bin/npm
ln -sf ~/.local/node-24.4.0/bin/npx ~/.local/bin/npx
```

- [ ] **Step 2: corepack で pnpm 10.13.1 を有効化**

```bash
ln -sf ~/.local/node-24.4.0/bin/corepack ~/.local/bin/corepack
corepack enable --install-directory ~/.local/bin
corepack prepare pnpm@10.13.1 --activate
```

- [ ] **Step 3: バージョン確認(期待出力を検証)**

Run: `node --version && pnpm --version`
Expected: `v24.4.0` と `10.13.1`

- [ ] **Step 4: バージョンピンファイル作成**

```bash
cd /home/j0145092/workspace/paper-reader
printf '24.4.0\n' > .node-version
printf '3.12.11\n' > .python-version
```

- [ ] **Step 5: uv で Python 3.12.11 を取得**

Run: `uv python install 3.12.11 && uv python find 3.12.11`
Expected: 3.12.11 のパスが表示される

---

### Task 1: モノレポ骨格(M0-01)

**Files:**
- Create: `package.json`(root。`packageManager: "pnpm@10.13.1"`、devDeps: turbo@2.5.4, prettier@3.6.2, eslint@9.30.1、scripts: `dev`/`build`/`lint`/`typecheck`/`test`/`format`)
- Create: `pnpm-workspace.yaml`(`packages: ["apps/*", "packages/*"]`)
- Create: `turbo.json`(build/lint/typecheck/test/dev タスクのパイプライン。dev は `persistent: true, cache: false`)
- Create: `pyproject.toml`(root。`[tool.uv.workspace] members = ["apps/api", "apps/worker", "packages/py-core", "packages/llm"]`、`[tool.ruff]`/`[tool.mypy]` は plans/00 §4.2/§4.3 の逐語)
- Create: `.gitignore`(node_modules / .venv / .turbo / .next / .output / dist / generated / __pycache__ / *.egg-info / .env)
- Create: `.env.example`(plans/00 §5.2 の全変数 + plans/01 §8.4 + plans/12 §15 のベース URL 上書き変数の逐語)
- Create: `.prettierrc.json`(`{"printWidth": 100, "singleQuote": false, "semi": true, "trailingComma": "all"}`)
- Create: `eslint.config.mjs`(flat config。typescript-eslint strict + react-hooks + no-restricted-imports で `../` 禁止)
- Create: `renovate.json`(週次グループ化 PR、LLM SDK は毎日、メジャー自動マージ無し)

**Interfaces:**
- Produces: `pnpm install` / `uv sync --all-packages` が解決可能なワークスペースルート。`pnpm turbo <task>` が全 app を横断実行できる。

- [ ] **Step 1: ルート設定ファイルを作成**

plans/00 §1〜§5 の逐語値で上記ファイルを作成する。`.env.example` は plans/00 §5.2 の全ブロック + `YAKUDOKU_OPENAI_BASE_URL`/`YAKUDOKU_ANTHROPIC_BASE_URL`/`YAKUDOKU_GOOGLE_BASE_URL`/`YAKUDOKU_DEEPSEEK_BASE_URL`/`YAKUDOKU_XAI_BASE_URL`/`YAKUDOKU_ARXIV_BASE_URL`(plans/12 §15-2,3)+ `EXTENSION_ALLOWED_ORIGINS`/`API_INTERNAL_URL`/`S3_PUBLIC_ENDPOINT_URL`(plans/01 §8.4)を含める。

- [ ] **Step 2: pnpm install が通ることを確認**

Run: `cd /home/j0145092/workspace/paper-reader && pnpm install`
Expected: ワークスペースが認識され、lockfile(`pnpm-lock.yaml`)が生成される(apps/packages はまだ空でも可)

- [ ] **Step 3: 秘匿値に公開接頭辞が無いことを検査(受け入れ基準)**

Run: `grep -nE '^(NEXT_PUBLIC_|WXT_)[A-Z_]*(KEY|SECRET|TOKEN|PASSWORD)' .env.example || echo "OK: no secret leaks"`
Expected: `OK: no secret leaks`

- [ ] **Step 4: コミット**

```bash
git add -A && git commit -m "chore: initialize pnpm+turbo+uv monorepo skeleton (M0-01)"
```

---

### Task 2: docker-compose 開発環境(M0-03)

**Files:**
- Create: `docker-compose.yml`(plans/00 §3 の完全形: db=groonga/pgroonga:4.0.1-alpine-16 / redis:7.4-alpine / minio + minio-init / mailpit。全 healthcheck 付き)
- Create: `docker/db/init.sql`(`CREATE EXTENSION IF NOT EXISTS pgroonga; CREATE EXTENSION IF NOT EXISTS pgcrypto;`)

**Interfaces:**
- Produces: `docker compose up -d --wait` で db(5432)/redis(6379)/minio(9000/9001)/mailpit(1025/8025)が healthy になり、バケット `yakudoku-sources`/`yakudoku-assets` が作成される。

- [ ] **Step 1: docker-compose.yml と init.sql を作成**(plans/00 §3 の逐語)

- [ ] **Step 2: データ基盤を起動して healthy を確認**

Run: `docker compose up -d --wait`
Expected: 全サービスが `Healthy` で終了コード 0

- [ ] **Step 3: PGroonga 拡張が有効なことを確認**

Run: `docker compose exec -T db psql -U yakudoku -d yakudoku -c "SELECT extname FROM pg_extension WHERE extname IN ('pgroonga','pgcrypto');"`
Expected: `pgroonga` と `pgcrypto` の2行

- [ ] **Step 4: MinIO バケットを確認**

Run: `docker compose exec -T minio mc ls local 2>/dev/null || docker run --rm --network yakudoku_default minio/mc:RELEASE.2025-04-16T18-13-26Z sh -c "mc alias set l http://minio:9000 yakudoku yakudoku-dev-secret && mc ls l"`
Expected: `yakudoku-sources` と `yakudoku-assets`

- [ ] **Step 5: コミット**

```bash
git add docker-compose.yml docker/db/init.sql && git commit -m "chore: add docker-compose dev data tier (M0-03)"
```

---

### Task 3: CI 骨格(M0-04)

**Files:**
- Create: `.github/workflows/ci.yml`(plans/00 §8 の完全形: js / python / openapi-drift / e2e / extension の5ジョブ)

**Interfaces:**
- Produces: 空実装でも green になる CI。以降の各タスクが自ジョブにテストを追加する。

- [ ] **Step 1: ci.yml を作成**(plans/00 §8 の逐語)

- [ ] **Step 2: YAML 構文を検証**

Run: `docker run --rm -v "$PWD":/w -w /w cytopia/yamllint:latest -d relaxed .github/workflows/ci.yml || python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('YAML OK')"`
Expected: `YAML OK`(または yamllint の警告のみ)

- [ ] **Step 3: コミット**

```bash
git add .github/workflows/ci.yml && git commit -m "ci: add 5-job CI skeleton (M0-04)"
```

---

### Task 4: packages/tokens(M0-05)

デザイントークン単一ソース。`tokens.json`(plans/08 §1〜§4 の _global.md 全値)→ `tokens.css`/`tokens.ts` 生成。

**Files:**
- Create: `packages/tokens/package.json`(name: `@yakudoku/tokens`、exports: `./css`→dist/tokens.css, `./js`→dist/tokens.js, `./fonts`→css/fonts.css, `./theme`→css/theme.css)
- Create: `packages/tokens/src/tokens.json`(色・書体・導出規則・注釈4色・ステータス6色。plans/08 §1 の全値)
- Create: `packages/tokens/build.mjs`(tokens.json → dist/tokens.css + dist/tokens.ts。アクセント4色の rgba 導出 0.10/0.32/0.14/0.40 を計算)
- Create: `packages/tokens/src/accent.ts`(アクセント4色 #3E5C76/#4A6B57/#6E5A7E/#7A5C48 と導出関数)
- Create: `packages/tokens/css/fonts.css` / `packages/tokens/css/theme.css`(Tailwind v4 @theme マッピング)
- Test: `packages/tokens/src/tokens.test.ts`(VT-TOK-01: 生成 CSS 変数値が _global.md 期待値と一致)

**Interfaces:**
- Produces: CSS 変数 `--pr-a`/`--pr-as`/`--pr-am`/`--pr-ad`/`--pr-ads`/`--pr-adm`/`--pr-jp`、注釈 `--ann-important:#C49432`/`--ann-question:#5884AA`/`--ann-idea:#659471`/`--ann-term:#82827E`、ステータス `--st-toread:#9AA0A6`/`--st-next:#C49432`/`--st-reading:var(--pr-a)`/`--st-done:#659471`/`--st-reread:#8E7AA6`/`--st-hold:#B0ACA2`。`@yakudoku/tokens/js` から型付き定数 export。

- [ ] **Step 1: tokens.json を plans/08 §1 の全値で作成**

- [ ] **Step 2: 失敗するテストを書く(VT-TOK-01)**

```ts
// packages/tokens/src/tokens.test.ts
import { test, expect } from "vitest";
import tokens from "./tokens.json";
test("annotation colors match _global.md verbatim", () => {
  expect(tokens.annotation.important).toBe("#C49432");
  expect(tokens.annotation.question).toBe("#5884AA");
  expect(tokens.annotation.idea).toBe("#659471");
  expect(tokens.annotation.term).toBe("#82827E");
});
test("status colors match _global.md verbatim", () => {
  expect(tokens.status.toread).toBe("#9AA0A6");
  expect(tokens.status.next).toBe("#C49432");
});
```

- [ ] **Step 3: テストが失敗することを確認**

Run: `pnpm --filter @yakudoku/tokens test`
Expected: FAIL(tokens.json が未整備、または vitest 未設定)

- [ ] **Step 4: build.mjs と accent.ts を実装、vitest 設定を追加**

- [ ] **Step 5: テストが通ることを確認 + ビルド生成物を確認**

Run: `pnpm --filter @yakudoku/tokens build && pnpm --filter @yakudoku/tokens test`
Expected: dist/tokens.css・dist/tokens.ts が生成され、テスト PASS。`grep -q -- '--ann-important: #C49432' packages/tokens/dist/tokens.css` が成功

- [ ] **Step 6: コミット**

```bash
git add packages/tokens && git commit -m "feat(tokens): design token single source with generated CSS/TS (M0-05)"
```

---

### Task 5: packages/py-core 雛形 + DB モデルと完全 DDL(M0-06)

SQLAlchemy 2 モデル全量 + Alembic 初期マイグレーション1本。plans/02 §4 の全テーブルを投入する。

**Files:**
- Create: `packages/py-core/pyproject.toml`(name: `yakudoku-core`、deps: sqlalchemy 2.0.41, asyncpg 0.30.0, pydantic 2.11.7, python-ulid 3.0.0, pydantic-settings 2.10.1)
- Create: `packages/py-core/src/yakudoku_core/db/session.py`(async engine/sessionmaker、`get_session()`)
- Create: `packages/py-core/src/yakudoku_core/db/ids.py`(接頭辞付き ULID 生成: `new_id("usr")` → `usr_01J...`。接頭辞表は plans/03 §1.6)
- Create: `packages/py-core/src/yakudoku_core/db/models/*.py`(plans/02 §4 の全30+テーブルを Mapped[] スタイルで。1 ファイル = 関連テーブル群)
- Create: `packages/py-core/src/yakudoku_core/settings.py`(`CoreSettings`(pydantic-settings)。`DATABASE_URL`/`REDIS_URL`/`S3_*` 等を型付きで読む)
- Create: `apps/api/pyproject.toml`(name: `yakudoku-api`、deps に yakudoku-core・fastapi・alembic・uvicorn)
- Create: `apps/api/alembic.ini` / `apps/api/alembic/env.py`
- Create: `apps/api/alembic/versions/0001_initial_schema.py`(plans/02 §4 の全 DDL を上から順に。全 index・PGroonga 索引・`set_updated_at()` トリガ含む)
- Test: `packages/py-core/tests/test_schema.py`(PY-DB-01〜12: 全テーブル存在・主キー型・CHECK 制約・FK カスケード・部分一意制約・PGroonga 索引の検証)

**Interfaces:**
- Consumes: docker-compose の PostgreSQL(Task 2)。
- Produces: `alembic upgrade head` で plans/02 §4 の全テーブルが存在。`from yakudoku_core.db.models import User, Paper, LibraryItem, DocumentRevision, TranslationSet, TranslationUnit, Job, ...`。`new_id(prefix: str) -> str`。`CoreSettings` シングルトン。

- [ ] **Step 1: 失敗するテストを書く(PY-DB-01: 全テーブル存在)**

```python
# packages/py-core/tests/test_schema.py
import pytest
from sqlalchemy import text
EXPECTED_TABLES = {
    "users","auth_identities","sessions","byok_api_keys","papers","source_assets",
    "document_revisions","block_search_index","translation_sets","translation_units",
    "glossaries","glossary_terms","library_items","chat_threads","chat_messages","notes",
    "annotations","vocab_entries","resource_links","collections","collection_entries",
    "collection_share_tokens","saved_filters","notifications","articles","article_blocks",
    "overview_figures","explainer_figures","reading_sessions","jobs","usage_records",
}
@pytest.mark.asyncio
async def test_all_tables_exist(db_session):
    rows = await db_session.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'"))
    actual = {r[0] for r in rows}
    assert EXPECTED_TABLES <= actual, EXPECTED_TABLES - actual
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd /home/j0145092/workspace/paper-reader && uv run pytest packages/py-core/tests/test_schema.py -v`
Expected: FAIL(テーブル未作成、または alembic 未実行)

- [ ] **Step 3: モデルと 0001_initial_schema.py を実装**(plans/02 §4 の全 DDL)

- [ ] **Step 4: マイグレーションを適用してテストを通す**

```bash
docker compose up -d --wait db redis
cd apps/api && uv run alembic upgrade head && cd ../..
uv run pytest packages/py-core/tests/test_schema.py -v
```
Expected: マイグレーション成功、PY-DB-01 PASS

- [ ] **Step 5: 残りの PY-DB-02〜12(主キー型・CHECK・FK カスケード・部分一意・PGroonga索引)を追加して green**

Run: `uv run pytest packages/py-core/tests/test_schema.py -v`
Expected: PY-DB-01〜12 全 PASS

- [ ] **Step 6: コミット**

```bash
git add packages/py-core apps/api/alembic apps/api/pyproject.toml apps/api/alembic.ini && git commit -m "feat(db): full DDL initial migration + SQLAlchemy models (M0-06)"
```

---

### Task 6: py-core ドメイン基盤(M0-07)

構造化ドキュメント中間表現 + アンカー + S3 ラッパ + ライセンス判定 + block_search_index 再構築。

**Files:**
- Create: `packages/py-core/src/yakudoku_core/document/blocks.py`(Block Pydantic モデル: 11 ブロック型。plans/02 §3 の JSONB 契約)
- Create: `packages/py-core/src/yakudoku_core/document/inlines.py`(インライン8種)
- Create: `packages/py-core/src/yakudoku_core/document/anchor.py`(`AnchorJson`: revision_id + block_id + start + end。plans/02 §3.1)
- Create: `packages/py-core/src/yakudoku_core/document/stable_id.py`(`derive_block_id()` 決定的生成。docs/01 §4.3)
- Create: `packages/py-core/src/yakudoku_core/storage/s3.py`(aioboto3 ラッパ、キー設計は plans/01 §7.1、署名付き URL 発行)
- Create: `packages/py-core/src/yakudoku_core/licenses.py`(arXiv ライセンスマトリクス判定。docs/09 §5.2)
- Create: `packages/py-core/src/yakudoku_core/search/rebuild.py`(`rebuild_block_search_index(revision)`: content JSONB → block_search_index を DELETE→INSERT)
- Test: `packages/py-core/tests/test_document.py`(PY-DB-13: アンカー往復・block_search_index 再構築)、`test_licenses.py`(PY-LIC-01: ライセンスマトリクス全行)

**Interfaces:**
- Consumes: `yakudoku_core.db.models`(Task 5)。
- Produces: `Block`/`Inline`/`Section`/`AnchorJson`(Pydantic)、`derive_block_id(section_idx, para_idx, content_hash) -> str`(`blk-` 接頭辞)、`S3Storage.put/get/presign_get`、`classify_license(license_id) -> LicensePolicy`、`rebuild_block_search_index(session, revision) -> int`(挿入行数)。

- [ ] **Step 1: 失敗するテストを書く(PY-DB-13: アンカー実在検証)**

```python
# packages/py-core/tests/test_document.py
from yakudoku_core.document.stable_id import derive_block_id
def test_block_id_is_deterministic():
    a = derive_block_id(section_idx=3, para_idx=2, content="Rectified flow is a method")
    b = derive_block_id(section_idx=3, para_idx=2, content="Rectified flow is a method")
    assert a == b and a.startswith("blk-")
def test_block_id_changes_with_content():
    a = derive_block_id(section_idx=3, para_idx=2, content="X")
    b = derive_block_id(section_idx=3, para_idx=2, content="Y")
    assert a != b
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest packages/py-core/tests/test_document.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: document/・storage/・licenses.py・search/rebuild.py を実装**

- [ ] **Step 4: テストを通す(PY-DB-13, PY-DB-14, PY-LIC-01)**

Run: `uv run pytest packages/py-core/tests/test_document.py packages/py-core/tests/test_licenses.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add packages/py-core/src/yakudoku_core/{document,storage,search,licenses.py} packages/py-core/tests && git commit -m "feat(core): document IR, anchors, S3 wrapper, license matrix, search index rebuild (M0-07)"
```

---

## フェーズ P1 — 3レーン並列(P0 完了後)

### Task 7: packages/llm 抽象化層 + Fake(M0-08)

5社テキスト + 3社画像アダプタ、ルーティング、フォールバック、structured output、count_tokens、Fake。

**Files:**
- Create: `packages/llm/pyproject.toml`(name: `yakudoku-llm`、deps: openai 1.93.0, anthropic 0.57.1, google-genai 1.24.0, tiktoken)
- Create: `packages/llm/src/yakudoku_llm/types.py`(`ChatRequest`/`ChatResponse`/`Delta`/`ImageRequest` 等)
- Create: `packages/llm/src/yakudoku_llm/errors.py`(`ProviderError`/`RateLimitError`/`QuotaError` 分類)
- Create: `packages/llm/src/yakudoku_llm/protocols.py`(`LLMProvider`/`ImageProvider` Protocol)
- Create: `packages/llm/src/yakudoku_llm/registry.py`(`ModelRegistry`: models.yaml を読む)
- Create: `packages/llm/src/yakudoku_llm/routing.py` / `router.py`(`LLMRouter`: リトライ・フォールバック連鎖)
- Create: `packages/llm/src/yakudoku_llm/structured.py`(各社の structured output 互換化)
- Create: `packages/llm/src/yakudoku_llm/tokens.py`(count_tokens)
- Create: `packages/llm/src/yakudoku_llm/providers/{openai_provider,anthropic_provider,google_provider,deepseek_provider,xai_provider}.py` + `providers/images/*.py`
- Create: `packages/llm/models.yaml` / `packages/llm/routing.yaml`(plans/04 §7,§8 のシード)
- Create: `packages/llm/src/yakudoku_llm/testing/fake_provider.py`(`FakeLLMProvider`/`FakeImageProvider`: 決定的応答)
- Test: `packages/llm/tests/test_router.py`(PY-LLM-01〜07: フォールバック連鎖・structured output・count_tokens・Fake 決定性・未設定プロバイダ除外)

**Interfaces:**
- Produces: `LLMProvider.chat(req) -> AsyncIterator[Delta]` / `.complete(req) -> ChatResponse`、`ImageProvider.generate(req) -> ImageResult`、`LLMRouter.route(task: str, ...)`(フォールバック連鎖・未設定キー自動除外)、`ModelRegistry.from_yaml(path)`、`FakeLLMProvider(responses: dict)`。

- [ ] **Step 1: 失敗するテストを書く(PY-LLM-04: フォールバック連鎖)**

```python
# packages/llm/tests/test_router.py
import pytest
from yakudoku_llm.testing.fake_provider import FakeLLMProvider
from yakudoku_llm.router import LLMRouter
from yakudoku_llm.errors import ProviderError
@pytest.mark.asyncio
async def test_router_falls_back_on_primary_error():
    primary = FakeLLMProvider(fail=True)
    secondary = FakeLLMProvider(responses={"translate": "訳文"})
    router = LLMRouter(chain=[("primary","m1",primary), ("secondary","m2",secondary)])
    resp = await router.complete(task="translate", prompt="Rectified flow")
    assert resp.text == "訳文"
    assert resp.fallback_rank == 1
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest packages/llm/tests/test_router.py -v`
Expected: FAIL(module not found)

- [ ] **Step 3: types/errors/protocols/router/registry/providers/Fake を実装**(plans/04 全章)

- [ ] **Step 4: テストを通す(PY-LLM-01〜07)**

Run: `uv run pytest packages/llm/tests/ -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add packages/llm && git commit -m "feat(llm): 5 text + 3 image adapters, router, fallback, fake provider (M0-08)"
```

---

### Task 8: E2E 用モック LLM/外部サーバ(M0-09)

**Files:**
- Create: `packages/llm/src/yakudoku_llm/testing/mock_server.py`(FakeLLM と同一の決定的応答を HTTP で提供。5社エンドポイント + arXiv abs/e-print/Atom + GitHub/YouTube oEmbed 相当。ポート8090)

**Interfaces:**
- Consumes: `FakeLLMProvider`(Task 7)。
- Produces: `YAKUDOKU_{OPENAI,ANTHROPIC,GOOGLE,DEEPSEEK,XAI}_BASE_URL` と `YAKUDOKU_ARXIV_BASE_URL` を localhost:8090 に向けると実プロバイダ呼び出しがモックへ差し替わる。

- [ ] **Step 1: 失敗するテストを書く(モックサーバの決定性)**

```python
# packages/llm/tests/test_mock_server.py
import pytest, httpx
from yakudoku_llm.testing.mock_server import build_app
@pytest.mark.asyncio
async def test_mock_openai_chat_deterministic():
    app = build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r1 = await c.post("/v1/chat/completions", json={"model":"gpt-5.5","messages":[{"role":"user","content":"hi"}]})
        r2 = await c.post("/v1/chat/completions", json={"model":"gpt-5.5","messages":[{"role":"user","content":"hi"}]})
    assert r1.json() == r2.json()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest packages/llm/tests/test_mock_server.py -v`
Expected: FAIL

- [ ] **Step 3: mock_server.py を実装**

- [ ] **Step 4: テストを通す**

Run: `uv run pytest packages/llm/tests/test_mock_server.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add packages/llm/src/yakudoku_llm/testing/mock_server.py packages/llm/tests/test_mock_server.py && git commit -m "feat(llm): deterministic mock server for E2E/CI (M0-09)"
```

---

### Task 9: API 共通基盤(M0-10)

**Files:**
- Create: `apps/api/src/yakudoku_api/main.py`(FastAPI 生成・ルータ登録・ミドルウェア・`/api/openapi.json`)
- Create: `apps/api/src/yakudoku_api/settings.py`(pydantic-settings)
- Create: `apps/api/src/yakudoku_api/deps.py`(DB セッション・認証ユーザーの Depends)
- Create: `apps/api/src/yakudoku_api/errors.py`(RFC 9457 Problem Details + 安定 code。plans/03 §1.4)
- Create: `apps/api/src/yakudoku_api/schemas/common.py`(cursor ページング・SSE 共通。plans/03 §1.7)
- Create: `apps/api/src/yakudoku_api/logging.py`(structlog 構造化ログ)
- Create: `apps/api/src/yakudoku_api/export_openapi.py`(openapi.json を stdout へ出す5行スクリプト)
- Create: `apps/api/package.json`(`dev`: uvicorn --reload、`lint`/`test` が uv コマンドを呼ぶ)
- Test: `apps/api/tests/test_platform.py`(PF-01: healthz/readyz・Problem Details 形式・レート制限)

**Interfaces:**
- Consumes: `yakudoku_core.db.session`(Task 5)。
- Produces: `app`(FastAPI)、`GET /api/healthz`/`GET /api/readyz`、`ProblemDetail` 例外→レスポンス変換、`CursorPage[T]`、`python -m yakudoku_api.export_openapi`。

- [ ] **Step 1: 失敗するテストを書く(PF-01: healthz と Problem Details)**

```python
# apps/api/tests/test_platform.py
import pytest
from httpx import AsyncClient, ASGITransport
from yakudoku_api.main import app
@pytest.mark.asyncio
async def test_healthz_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
@pytest.mark.asyncio
async def test_not_found_is_problem_json():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/nonexistent")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "not_found"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest apps/api/tests/test_platform.py -v`
Expected: FAIL(app import error)

- [ ] **Step 3: main.py・errors.py・deps.py・settings.py を実装**

- [ ] **Step 4: テストを通す + OpenAPI エクスポートを確認**

Run: `uv run pytest apps/api/tests/test_platform.py -v && uv run python -m yakudoku_api.export_openapi | head -c 40`
Expected: PASS、`{"openapi":"3.` で始まる出力

- [ ] **Step 5: コミット**

```bash
git add apps/api/src apps/api/tests apps/api/package.json && git commit -m "feat(api): platform base — problem details, cursor paging, health, openapi export (M0-10)"
```

---

### Task 10: 認証(M0-11)

**Files:**
- Create: `apps/api/src/yakudoku_api/routers/auth.py`(`/api/auth/*` 8 エンドポイント。plans/03 §2)
- Modify: `apps/api/src/yakudoku_api/deps.py`(`current_user` Depends、Origin 検証 CSRF ミドルウェア)
- Create: `apps/api/src/yakudoku_api/services/session_service.py`(サーバーセッション: SHA-256 ハッシュ保存 + Redis キャッシュ)
- Test: `apps/api/tests/test_auth.py`(PY-AUTH-01〜03, PY-DB-03: メールリンク発行/検証・OAuth upsert・ログアウト失効・アカウント削除カスケード)

**Interfaces:**
- Consumes: `current_user` Depends、`CoreSettings`、Mailpit(dev SMTP)。
- Produces: `POST /api/auth/email/request`・`GET /api/auth/email/verify`・`GET /api/auth/oauth/{provider}/{start,callback}`・`POST /api/auth/logout`・`GET /api/auth/me`・`POST /api/auth/extension-token`。セッションクッキー `yk_session`(HttpOnly/SameSite=Lax)。

- [ ] **Step 1: 失敗するテストを書く(PY-AUTH-01: メールリンク一巡)**

```python
# apps/api/tests/test_auth.py
@pytest.mark.asyncio
async def test_email_link_login_flow(client, mailpit):
    r = await client.post("/api/auth/email/request", json={"email":"u@example.com"})
    assert r.status_code == 200
    token = await mailpit.latest_link_token()   # Mailpit API から抽出
    r2 = await client.get(f"/api/auth/email/verify?token={token}", follow_redirects=False)
    assert r2.status_code == 302
    assert "yk_session" in r2.headers.get("set-cookie","")
    me = await client.get("/api/auth/me")
    assert me.json()["email"] == "u@example.com"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest apps/api/tests/test_auth.py -v`
Expected: FAIL

- [ ] **Step 3: auth.py・session_service.py・Origin 検証を実装**

- [ ] **Step 4: テストを通す(PY-AUTH-01〜03, PY-DB-03)**

Run: `uv run pytest apps/api/tests/test_auth.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/api/src/yakudoku_api/routers/auth.py apps/api/src/yakudoku_api/services/session_service.py apps/api/tests/test_auth.py && git commit -m "feat(api): auth — email magic link, oauth, sessions, csrf origin check (M0-11)"
```

---

### Task 11: ジョブ基盤 + SSE(M0-12)

**Files:**
- Create: `apps/worker/pyproject.toml`(name: `yakudoku-worker`、deps に yakudoku-core・yakudoku-llm・arq)
- Create: `apps/worker/src/yakudoku_worker/main.py` / `settings.py`(`InteractiveWorker`/`BulkWorker`、キュー `yk:interactive`/`yk:bulk`)
- Create: `packages/py-core/src/yakudoku_core/jobs/store.py`(claim・checkpoint 段階再開・指数バックオフ 30s→2min→8min・冪等性キー。plans/01 §4)
- Create: `packages/py-core/src/yakudoku_core/jobs/requeue.py`(`python -m yakudoku_core.jobs.requeue` 回復コマンド)
- Create: `apps/api/src/yakudoku_api/routers/jobs.py`(ジョブ API + ユーザー単位 SSE `GET /api/events`。Redis Pub/Sub + Last-Event-ID 再送。plans/03 §21)
- Create: `apps/worker/package.json`(`dev`: arq --watch)
- Test: `apps/api/tests/test_jobs.py`(PY-JOB-01, PY-JOB-03: claim 排他・段階再開・冪等性キー・SSE 再送)

**Interfaces:**
- Consumes: `Job` モデル(Task 5)、Redis。
- Produces: `JobStore.enqueue(kind, payload, priority, idempotency_key) -> job_id`・`.claim(job_id) -> Job | None`・`.checkpoint(job_id, stage, data)`・`.fail_with_retry(job_id, error)`、`GET /api/events`(SSE)。

- [ ] **Step 1: 失敗するテストを書く(PY-JOB-01: claim 排他)**

```python
# apps/api/tests/test_jobs.py
@pytest.mark.asyncio
async def test_claim_is_exclusive(db_session):
    from yakudoku_core.jobs.store import JobStore
    store = JobStore(db_session)
    jid = await store.enqueue(kind="translate_section", payload={}, idempotency_key="k1")
    first = await store.claim(jid)
    second = await store.claim(jid)
    assert first is not None and second is None   # 二重 enqueue でも1回だけ実行
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest apps/api/tests/test_jobs.py -v`
Expected: FAIL

- [ ] **Step 3: JobStore・worker settings・SSE を実装**

- [ ] **Step 4: テストを通す(PY-JOB-01, PY-JOB-03)**

Run: `uv run pytest apps/api/tests/test_jobs.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/worker packages/py-core/src/yakudoku_core/jobs apps/api/src/yakudoku_api/routers/jobs.py apps/api/tests/test_jobs.py && git commit -m "feat(jobs): job store with claim/checkpoint/backoff + user SSE (M0-12)"
```

---

### Task 12: LLM ルーティング・BYOK・クォータ(M0-13)

**Files:**
- Create: `apps/api/src/yakudoku_api/llm/route_store.py`(`llm_models`/`llm_task_routes`/`user_task_model_overrides` を読む)
- Create: `apps/api/src/yakudoku_api/llm/key_store.py`(`DbKeyStore`: Fernet 暗号化・マスク表示)
- Create: `apps/api/src/yakudoku_api/llm/meter.py`(`DbMeterHook`: usage_records 記録)
- Create: `apps/api/src/yakudoku_api/llm/deps.py`(ユーザー文脈で `LLMRouter` を構築、月次クォータ判定 → 429 `quota_exceeded`)
- Test: `apps/api/tests/test_llm_settings.py`(PY-SET-02〜04 の LLM 側: BYOK 暗号化往復・マスク応答・クォータ超過)

**Interfaces:**
- Consumes: `LLMRouter`(Task 7)、`llm_models`/BYOK テーブル(Task 5)。
- Produces: `build_router_for_user(user, task) -> LLMRouter`(BYOK 優先・運用キーフォールバック・未設定除外)、`DbKeyStore.put/get/mask`、`check_quota(user, task)`。

- [ ] **Step 1: 失敗するテストを書く(PY-SET-02: BYOK 暗号化往復)**

```python
# apps/api/tests/test_llm_settings.py
@pytest.mark.asyncio
async def test_byok_key_roundtrip_and_mask(db_session):
    from yakudoku_api.llm.key_store import DbKeyStore
    ks = DbKeyStore(db_session)
    await ks.put(user_id="usr_x", provider="openai", plaintext="sk-secret-1234")
    got = await ks.get(user_id="usr_x", provider="openai")
    assert got == "sk-secret-1234"          # 復号往復
    masked = await ks.mask(user_id="usr_x", provider="openai")
    assert masked.endswith("1234") and "secret" not in masked
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest apps/api/tests/test_llm_settings.py -v`
Expected: FAIL

- [ ] **Step 3: route_store・key_store・meter・deps を実装**

- [ ] **Step 4: テストを通す(PY-SET-02〜04 LLM 側)**

Run: `uv run pytest apps/api/tests/test_llm_settings.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/api/src/yakudoku_api/llm apps/api/tests/test_llm_settings.py && git commit -m "feat(llm): db routing, byok fernet key store, usage meter, quota (M0-13)"
```

---

## フェーズ P2 — 取り込み・翻訳パイプライン(クリティカルパス・直列が主)

### Task 13: arXiv 解決(M0-14)

**Files:**
- Create: `packages/py-core/src/yakudoku_core/arxiv/ids.py`(URL/ID 正規化。新旧形式全パターン)
- Create: `packages/py-core/src/yakudoku_core/arxiv/metadata.py`(メタデータ API・OAI-PMH ライセンス取得と正規化表)
- Create: `packages/py-core/src/yakudoku_core/arxiv/fetch.py`(LaTeX ソース有無判定=「品質レベル A 見込み」・Redis 24h キャッシュ・取得レート制限)
- Test: `packages/py-core/tests/test_arxiv.py`(PY-ING-03 の判定部: ID 正規化・LaTeX 有無判定)

**Interfaces:**
- Consumes: `YAKUDOKU_ARXIV_BASE_URL`(モックサーバ Task 8)。
- Produces: `normalize_arxiv_id(url_or_id) -> ArxivId`、`fetch_metadata(id) -> ArxivMeta`、`probe_latex_available(id) -> bool`。

- [ ] **Step 1: 失敗するテストを書く(PY-ING-03: ID 正規化)**

```python
# packages/py-core/tests/test_arxiv.py
import pytest
from yakudoku_core.arxiv.ids import normalize_arxiv_id
@pytest.mark.parametrize("inp", [
    "https://arxiv.org/abs/2209.03003","arxiv.org/abs/2209.03003v2",
    "2209.03003","https://arxiv.org/pdf/2209.03003.pdf"])
def test_normalize_arxiv_id(inp):
    assert normalize_arxiv_id(inp).id == "2209.03003"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest packages/py-core/tests/test_arxiv.py -v`
Expected: FAIL

- [ ] **Step 3: arxiv/ を実装**

- [ ] **Step 4: テストを通す**

Run: `uv run pytest packages/py-core/tests/test_arxiv.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add packages/py-core/src/yakudoku_core/arxiv packages/py-core/tests/test_arxiv.py && git commit -m "feat(ingest): arxiv id normalization, metadata, latex probe (M0-14)"
```

---

### Task 14: arXiv HTML パーサ(M0-15)

**Files:**
- Create: `packages/py-core/src/yakudoku_core/parsing/html_parser.py`(DOM→11 ブロック型 + インライン8種。selectolax)
- Create: `packages/py-core/src/yakudoku_core/parsing/block_ids.py`(ブロック安定 ID 生成)
- Create: `packages/py-core/src/yakudoku_core/parsing/carryover.py`(リビジョン間 carryover)
- Create: `packages/py-core/src/yakudoku_core/parsing/pdf_sync.py`(品質 A の page+bbox 同期。M0 は HTML 主経路なので最小)
- Test: `packages/py-core/tests/test_html_parser.py`(PY-PARSE-01, PY-PARSE-04: ブロック分解・KaTeX 数式コーパス検証)

**Interfaces:**
- Consumes: `Block`/`derive_block_id`(Task 6)。
- Produces: `parse_arxiv_html(html: str) -> ParsedDocument`(sections + blocks + figures)。

- [ ] **Step 1: 失敗するテストを書く(PY-PARSE-01: 見出し・段落・数式の分解)**

```python
# packages/py-core/tests/test_html_parser.py
from yakudoku_core.parsing.html_parser import parse_arxiv_html
def test_parses_heading_paragraph_math():
    html = '<section><h2>Introduction</h2><p>Rectified flow.</p><math>x</math></section>'
    doc = parse_arxiv_html(html)
    kinds = [b.kind for b in doc.blocks]
    assert "heading" in kinds and "paragraph" in kinds and "math_block" in kinds
    assert all(b.id.startswith("blk-") for b in doc.blocks)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest packages/py-core/tests/test_html_parser.py -v`
Expected: FAIL

- [ ] **Step 3: html_parser・block_ids・carryover を実装**

- [ ] **Step 4: テストを通す(PY-PARSE-01, PY-PARSE-04)**

Run: `uv run pytest packages/py-core/tests/test_html_parser.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add packages/py-core/src/yakudoku_core/parsing packages/py-core/tests/test_html_parser.py && git commit -m "feat(ingest): arxiv html parser → 11 block types + stable ids (M0-15)"
```

---

### Task 15: プレースホルダプロトコル(M0-16)

翻訳で数式・図参照・引用を保護するプロトコル。往復不変が最重要(Hypothesis プロパティテスト必須)。

**Files:**
- Create: `packages/py-core/src/yakudoku_core/translation/placeholder.py`(`protect`/`restore`/`validate`。source_hash。plans/06 §4)
- Test: `packages/py-core/tests/test_placeholder.py`(PY-TR-01, HP-01〜04: 往復不変・全トークン1回・検証)

**Interfaces:**
- Produces: `protect(text) -> (protected: str, mapping: dict)`、`restore(protected, mapping) -> str`、`validate(protected, mapping) -> bool`。不変: `restore(protect(x)[0], protect(x)[1]) == x`。

- [ ] **Step 1: 失敗するプロパティテストを書く(HP-01: 往復不変)**

```python
# packages/py-core/tests/test_placeholder.py
from hypothesis import given, strategies as st
from yakudoku_core.translation.placeholder import protect, restore
@given(st.text())
def test_protect_restore_roundtrip(x):
    protected, mapping = protect(x)
    assert restore(protected, mapping) == x
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest packages/py-core/tests/test_placeholder.py -v`
Expected: FAIL

- [ ] **Step 3: placeholder.py を実装**

- [ ] **Step 4: テストを通す(PY-TR-01, HP-01〜04)**

Run: `uv run pytest packages/py-core/tests/test_placeholder.py -v`
Expected: 全 PASS(Hypothesis 反例なし)

- [ ] **Step 5: コミット**

```bash
git add packages/py-core/src/yakudoku_core/translation/placeholder.py packages/py-core/tests/test_placeholder.py && git commit -m "feat(translation): placeholder protect/restore protocol with property tests (M0-16)"
```

---

### Task 16: 翻訳パイプライン(M0-17)

**Files:**
- Create: `packages/py-core/src/yakudoku_core/translation/pipeline.py`(自然訳プロンプト・文脈パッキング・structured output・自動品質検査5種・共有キャッシュ解決・進捗計算)
- Create: `packages/py-core/src/yakudoku_core/translation/glossary.py`(用語スナップショット凍結部)
- Create: `packages/py-core/src/yakudoku_core/translation/prompts/`(system 2 層 + バッチ user)
- Create: `apps/worker/src/yakudoku_worker/tasks/translate.py`(`translate_section` ジョブ)
- Test: `packages/py-core/tests/test_translation.py`(PY-TR-02〜07, PY-TR-10: 品質検査・共有キャッシュ・進捗写像・スコープ判定)

**Interfaces:**
- Consumes: `placeholder`(Task 15)、`LLMRouter`(Task 7/12)、`FakeLLMProvider`。
- Produces: `translate_section(session, translation_set_id, section_id, router)`、`resolve_translation(personal_set, base_set, block_id)`(personal→base マージ)、`compute_progress(units, total) -> int`。

- [ ] **Step 1: 失敗するテストを書く(PY-TR-05: 原文フォールバック)**

```python
# packages/py-core/tests/test_translation.py
@pytest.mark.asyncio
async def test_placeholder_failure_falls_back_to_source(fake_router_breaking_placeholder):
    unit = await translate_block("Eq. \\ref{eq5}", router=fake_router_breaking_placeholder)
    assert unit.state == "source_fallback"   # 黙って壊れない: 原文フォールバック
    assert "eq5" in unit.text_ja and unit.quality_flags
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest packages/py-core/tests/test_translation.py -v`
Expected: FAIL

- [ ] **Step 3: pipeline・glossary・prompts・translate task を実装**

- [ ] **Step 4: テストを通す(PY-TR-02〜07, PY-TR-10)**

Run: `uv run pytest packages/py-core/tests/test_translation.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add packages/py-core/src/yakudoku_core/translation apps/worker/src/yakudoku_worker/tasks/translate.py packages/py-core/tests/test_translation.py && git commit -m "feat(translation): section pipeline, quality checks, shared cache, progress (M0-17)"
```

---

### Task 17: 取り込みステートマシン(M0-18)

**Files:**
- Create: `apps/worker/src/yakudoku_worker/tasks/ingest.py`(`ingest_paper` の8段階駆動)
- Create: `apps/worker/src/yakudoku_worker/pipeline.py`(状態機械の駆動: queued→fetching→parsing→structuring→translating_abstract→readable→translating_body→complete)
- Create: `packages/py-core/src/yakudoku_core/ingest/{dedupe,thumbnail,joblog,progress}.py`
- Test: `apps/worker/tests/test_ingest.py`(PY-ING-02, PY-ING-05, PY-JOB-02: 段階遷移・重複検知・readable 先頭直接翻訳・段階再開)

**Interfaces:**
- Consumes: `JobStore`(Task 11)、arxiv(Task 13)、html_parser(Task 14)、translate(Task 16)。
- Produces: `ingest_paper(ctx, job_id)`(arq タスク)、`detect_duplicate(session, arxiv_id) -> LibraryItem | None`。段階が `readable` に達したら先頭セクションが翻訳済みで開ける。

- [ ] **Step 1: 失敗するテストを書く(PY-ING-02: readable 到達で先頭セクション翻訳済み)**

```python
# apps/worker/tests/test_ingest.py
@pytest.mark.asyncio
async def test_ingest_reaches_readable_with_first_section_translated(worker_ctx, seed_arxiv_mock):
    job_id = await enqueue_ingest(url="https://arxiv.org/abs/2209.03003")
    await run_until_stage(worker_ctx, job_id, "readable")
    doc = await get_revision_for(job_id)
    first = first_translatable_section(doc)
    assert has_translation(first)   # readable で先頭セクションは訳出済み
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest apps/worker/tests/test_ingest.py -v`
Expected: FAIL

- [ ] **Step 3: ingest・pipeline・dedupe・thumbnail・progress を実装**

- [ ] **Step 4: テストを通す(PY-ING-02, PY-ING-05, PY-JOB-02)**

Run: `uv run pytest apps/worker/tests/test_ingest.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/worker/src/yakudoku_worker/tasks/ingest.py apps/worker/src/yakudoku_worker/pipeline.py packages/py-core/src/yakudoku_core/ingest apps/worker/tests/test_ingest.py && git commit -m "feat(ingest): 8-stage ingest state machine, dedupe, readable-first (M0-18)"
```

---

## フェーズ P3 — API 面(M0-18 後に並列)

### Task 18: ingest / papers / assets API(M0-19)

**Files:**
- Create: `apps/api/src/yakudoku_api/routers/ingest.py`(`GET /api/ingest/check` 3分岐・`POST /api/ingest/arxiv` 202+Idempotency-Key・`GET /api/ingest/recent`)
- Create: `apps/api/src/yakudoku_api/routers/papers.py`(reingest・ingest-log・pdf 配信)
- Create: `apps/api/src/yakudoku_api/routers/assets.py`(`GET /api/assets/{id}` 302+署名付き URL)
- Test: `apps/api/tests/test_ingest_api.py`(PY-ING-01, PY-ING-03, PY-ING-06)

**Interfaces:**
- Consumes: `ingest_paper` タスク投入(Task 17)、`JobStore`。
- Produces: `GET /api/ingest/check?url=`→`{bibliography, latex_available, existing_library_item}`、`POST /api/ingest/arxiv`→202`{paper_id, library_item_id, job_id}`。

- [ ] **Step 1: 失敗するテストを書く(PY-ING-01: check の3分岐)**

```python
# apps/api/tests/test_ingest_api.py
@pytest.mark.asyncio
async def test_ingest_check_returns_preview(auth_client, seed_arxiv_mock):
    r = await auth_client.get("/api/ingest/check", params={"url":"https://arxiv.org/abs/2209.03003"})
    assert r.status_code == 200
    body = r.json()
    assert body["latex_available"] is True
    assert body["existing_library_item"] is None
    assert "title" in body["bibliography"]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest apps/api/tests/test_ingest_api.py -v`
Expected: FAIL

- [ ] **Step 3: ingest・papers・assets ルータを実装**

- [ ] **Step 4: テストを通す(PY-ING-01, PY-ING-03, PY-ING-06)**

Run: `uv run pytest apps/api/tests/test_ingest_api.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/api/src/yakudoku_api/routers/{ingest,papers,assets}.py apps/api/tests/test_ingest_api.py && git commit -m "feat(api): ingest check/arxiv/recent, papers, asset delivery (M0-19)"
```

---

### Task 19: viewer / translations API(M0-20)

**Files:**
- Create: `apps/api/src/yakudoku_api/routers/viewer.py`(ビューア初期化複合エンドポイント・document・blocks・figures・references)
- Create: `apps/api/src/yakudoku_api/routers/translations.py`(翻訳セット取得・units・prioritize・オンデマンドセクション翻訳・指示なし retranslate・読書位置 PUT)
- Test: `apps/api/tests/test_viewer_api.py`(PY-LIB-03: 読書位置 PUT・ビューア初期化)

**Interfaces:**
- Consumes: `translate_section` 繰り上げ(Task 16)、`current_user`。
- Produces: `GET /api/viewer/{library_item_id}`(初期化)、`GET /api/translation-sets/{id}/units`、`POST /api/translation-sets/{id}/prioritize`、`POST /api/translation-sets/{id}/retranslate`、`PUT /api/library-items/{id}/position`。

- [ ] **Step 1: 失敗するテストを書く(PY-LIB-03: 読書位置保存・復元)**

```python
# apps/api/tests/test_viewer_api.py
@pytest.mark.asyncio
async def test_reading_position_roundtrip(auth_client, seeded_library_item):
    li = seeded_library_item.id
    r = await auth_client.put(f"/api/library-items/{li}/position", json={"block_id":"blk-3-p2-a1f9"})
    assert r.status_code == 200
    v = await auth_client.get(f"/api/viewer/{li}")
    assert v.json()["reading_position"]["block_id"] == "blk-3-p2-a1f9"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest apps/api/tests/test_viewer_api.py -v`
Expected: FAIL

- [ ] **Step 3: viewer・translations ルータを実装**

- [ ] **Step 4: テストを通す(PY-LIB-03)**

Run: `uv run pytest apps/api/tests/test_viewer_api.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/api/src/yakudoku_api/routers/{viewer,translations}.py apps/api/tests/test_viewer_api.py && git commit -m "feat(api): viewer init, translation units, prioritize, reading position (M0-20)"
```

---

### Task 20: チャットバックエンド + API(M0-21)

**Files:**
- Create: `apps/api/src/yakudoku_api/chat/context_builder.py`(文脈ビルダー)
- Create: `apps/api/src/yakudoku_api/chat/stream_pipeline.py`(`[[ev:n]]` ストリーム変換)
- Create: `apps/api/src/yakudoku_api/chat/evidence.py`(根拠実在検証)
- Create: `apps/api/src/yakudoku_api/routers/chat.py`(SSE 送信 API・スレッド CRUD・regenerate。plans/03 §10)
- Test: `apps/api/tests/test_chat.py`(PY-CHAT-01〜06: 文脈構築・ストリーム・根拠検証・定型アクション)

**Interfaces:**
- Consumes: `build_router_for_user`(Task 12)、`document`(Task 6)。
- Produces: `POST /api/chat/threads/{id}/messages`(SSE `message.delta`/`message.completed`/`message.failed`)、`GET/POST /api/chat/threads`、evidence アンカーは実在検証済みのみ返す。

- [ ] **Step 1: 失敗するテストを書く(PY-CHAT-03: 壊れた根拠の除去)**

```python
# apps/api/tests/test_chat.py
@pytest.mark.asyncio
async def test_broken_evidence_anchor_is_stripped():
    from yakudoku_api.chat.evidence import verify_evidence
    anchors = [{"block_id":"blk-real"}, {"block_id":"blk-nonexistent"}]
    verified = await verify_evidence(anchors, existing_block_ids={"blk-real"})
    assert verified == [{"block_id":"blk-real"}]   # 実在しない根拠は除去(P1)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest apps/api/tests/test_chat.py -v`
Expected: FAIL

- [ ] **Step 3: context_builder・stream_pipeline・evidence・chat ルータを実装**

- [ ] **Step 4: テストを通す(PY-CHAT-01〜06)**

Run: `uv run pytest apps/api/tests/test_chat.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/api/src/yakudoku_api/chat apps/api/src/yakudoku_api/routers/chat.py apps/api/tests/test_chat.py && git commit -m "feat(api): chat context, streaming, evidence verification (M0-21)"
```

---

### Task 21: ライブラリ API(M0-22)+ 設定 API(M0-23)

**Files:**
- Create: `apps/api/src/yakudoku_api/routers/library_items.py`(一覧・facets・GET/PATCH/DELETE・tags。plans/03 §5)
- Create: `apps/api/src/yakudoku_api/routers/settings.py`(`GET/PATCH /api/settings` deep merge・値域検証)
- Create: `apps/api/src/yakudoku_api/routers/llm_settings.py`(BYOK PUT/DELETE マスク応答・`GET /api/settings/quota`)
- Test: `apps/api/tests/test_library_api.py`(PY-LIB-01, PY-LIB-02)、`test_settings_api.py`(PY-SET-01)

**Interfaces:**
- Consumes: `DbKeyStore`(Task 12)、`current_user`。
- Produces: `GET /api/library-items`(cursor・フィルタ・ソート・facets)、`GET/PATCH /api/settings`、`PUT/DELETE /api/settings/byok/{provider}`(平文再表示なし)。

- [ ] **Step 1: 失敗するテストを書く(PY-LIB-01: 一覧フィルタ)**

```python
# apps/api/tests/test_library_api.py
@pytest.mark.asyncio
async def test_library_list_filters_by_status(auth_client, seeded_items):
    r = await auth_client.get("/api/library-items", params={"status":"up_next"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert all(i["status"] == "up_next" for i in items)
    assert "next_cursor" in r.json()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest apps/api/tests/test_library_api.py apps/api/tests/test_settings_api.py -v`
Expected: FAIL

- [ ] **Step 3: library_items・settings・llm_settings ルータを実装**

- [ ] **Step 4: テストを通す(PY-LIB-01,02, PY-SET-01,03,04)**

Run: `uv run pytest apps/api/tests/test_library_api.py apps/api/tests/test_settings_api.py -v`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/api/src/yakudoku_api/routers/{library_items,settings,llm_settings}.py apps/api/tests/test_library_api.py apps/api/tests/test_settings_api.py && git commit -m "feat(api): library items, settings, byok settings (M0-22, M0-23)"
```

---

### Task 22: packages/api-client(M0-24)

**Files:**
- Create: `packages/api-client/package.json`(name: `@yakudoku/api-client`、devDeps: @hey-api/openapi-ts 0.78.3、scripts: `generate`)
- Create: `packages/api-client/openapi-ts.config.ts`(入力 openapi.json・出力 src/generated/)
- Create: `packages/api-client/src/index.ts`(fetch ラッパ `credentials: "include"`)
- Modify: `.github/workflows/ci.yml`(openapi-drift ジョブを実接続)

**Interfaces:**
- Consumes: `python -m yakudoku_api.export_openapi`(Task 9)、全ルータ(Task 18〜21)。
- Produces: `@yakudoku/api-client` の型付きクライアント(手書き禁止・生成のみ)。

- [ ] **Step 1: OpenAPI を書き出してクライアント生成**

```bash
uv run python -m yakudoku_api.export_openapi > packages/api-client/openapi.json
pnpm --filter @yakudoku/api-client generate
```
Expected: `packages/api-client/src/generated/` に型が生成される

- [ ] **Step 2: ドリフト検査(生成が冪等)**

Run: `pnpm --filter @yakudoku/api-client generate && git diff --exit-code packages/api-client/src/generated`
Expected: 差分なし(終了コード 0)

- [ ] **Step 3: コミット**

```bash
git add packages/api-client && git commit -m "feat(api-client): openapi-generated typed client + drift check (M0-24)"
```

---

### Task 23: シードデータ(M0-25)

**Files:**
- Create: `apps/api/src/yakudoku_api/seed_data/rectified_flow/`(bib.json, document.json, translation_natural.json ほか plans/12 §14.1 の全ファイル)
- Create: `apps/api/src/yakudoku_api/seed.py`(`python -m yakudoku_api.seed --sample rectified-flow [--reset] [--scale N] [--full]`)
- Test: `apps/api/tests/test_seed.py`(投入後に論文・リビジョン・翻訳セットが存在)

**Interfaces:**
- Produces: `python -m yakudoku_api.seed --sample rectified-flow` で Rectified Flow(arXiv:2209.03003)が投入される。全テスト・VR・開発の共通データ源。

- [ ] **Step 1: 失敗するテストを書く**

```python
# apps/api/tests/test_seed.py
@pytest.mark.asyncio
async def test_seed_creates_rectified_flow(db_session, run_seed):
    await run_seed("rectified-flow")
    from sqlalchemy import text
    n = await db_session.scalar(text("SELECT count(*) FROM papers WHERE arxiv_id='2209.03003'"))
    assert n == 1
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest apps/api/tests/test_seed.py -v`
Expected: FAIL

- [ ] **Step 3: seed_data フィクスチャと seed.py を実装**

- [ ] **Step 4: テストを通す + 実投入を確認**

```bash
uv run pytest apps/api/tests/test_seed.py -v
uv run python -m yakudoku_api.seed --sample rectified-flow --reset
```
Expected: PASS、投入ログに `2209.03003` 完了

- [ ] **Step 5: コミット**

```bash
git add apps/api/src/yakudoku_api/seed_data apps/api/src/yakudoku_api/seed.py apps/api/tests/test_seed.py && git commit -m "feat(seed): rectified-flow sample data + seed command (M0-25)"
```

---

## フェーズ P4 — 画面(M0-24 と M0-27 後に並列)

### Task 24: web アプリシェル(M0-26)

**Files:**
- Create: `apps/web/package.json`(name: `@yakudoku/web`、deps: next 15.4.2, react 19.1.0, @tanstack/react-query 5.81.5, zustand 5.0.6, @yakudoku/api-client, @yakudoku/tokens)
- Create: `apps/web/next.config.ts` / `tsconfig.json`(paths `@/*`)/ `postcss.config.mjs`(Tailwind v4)
- Create: `apps/web/src/app/layout.tsx`(next/font・ThemeProvider・QueryClientProvider)
- Create: `apps/web/src/app/(auth)/login/page.tsx`
- Create: `apps/web/src/components/AppHeader.tsx` / `SidebarNav.tsx`
- Create: `apps/web/src/lib/sse.ts`(`/api/events` 購読 + ポーリングフォールバック)
- Create: `apps/web/src/styles/globals.css`(`@import "@yakudoku/tokens/css"; @theme` マッピング)
- Test: `apps/web/src/app/layout.test.tsx`(VT-UI-01: シェル描画)

**Interfaces:**
- Consumes: `@yakudoku/api-client`(Task 22)、`@yakudoku/tokens`(Task 4)。
- Produces: `(app)`/`(public)`/`(auth)` セグメント、`AppHeader`/`SidebarNav`、`useSSE()` フック。

- [ ] **Step 1: 失敗するテストを書く(VT-UI-01)**

```tsx
// apps/web/src/components/AppHeader.test.tsx
import { render, screen } from "@testing-library/react";
import { AppHeader } from "./AppHeader";
test("renders product name", () => {
  render(<AppHeader />);
  expect(screen.getByText(/訳読/)).toBeInTheDocument();
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pnpm --filter @yakudoku/web test`
Expected: FAIL

- [ ] **Step 3: layout・AppHeader・SidebarNav・sse・globals.css を実装**

- [ ] **Step 4: テストを通す + ビルド確認**

Run: `pnpm --filter @yakudoku/web test && pnpm --filter @yakudoku/web build`
Expected: PASS、Next.js ビルド成功

- [ ] **Step 5: コミット**

```bash
git add apps/web && git commit -m "feat(web): app shell — layout, header, sidebar, sse, login (M0-26)"
```

---

### Task 25: UI 共通コンポーネント M0 分(M0-27)

**Files:**
- Create: `apps/web/src/components/ui/` に17種(SegmentedControl / StatusPill / QualityBadge / FilterChip / CountBadge / Toggle / Card / Popover / Modal / ProgressBar / SearchBox / SidebarNav / Table / SidePanelTabs / EvidenceChip / AIBadge / Toast / EmptyState)+ Keycap / TagChip + SVG アイコン基盤
- Test: `apps/web/src/components/ui/*.test.tsx`(VT-UI-02, VT-UI-03)

**Interfaces:**
- Consumes: `@yakudoku/tokens/js`(Task 4)。
- Produces: 各コンポーネント(plans/08 §5 の props 契約)。`StatusPill` は6ステータス色、`QualityBadge` は A/B、`AIBadge` は「AI生成」。

- [ ] **Step 1: 失敗するテストを書く(VT-UI-02: StatusPill 6色)**

```tsx
// apps/web/src/components/ui/StatusPill.test.tsx
import { render, screen } from "@testing-library/react";
import { StatusPill } from "./StatusPill";
test("renders reading status label", () => {
  render(<StatusPill status="reading" />);
  expect(screen.getByText("読んでいる")).toBeInTheDocument();
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pnpm --filter @yakudoku/web test`
Expected: FAIL

- [ ] **Step 3: 17種のコンポーネントを実装**(plans/08 §5 の px・色・文言逐語)

- [ ] **Step 4: テストを通す(VT-UI-02, VT-UI-03)**

Run: `pnpm --filter @yakudoku/web test`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/web/src/components/ui && git commit -m "feat(web): 17 shared UI components (M0-27)"
```

---

### Task 26: ビューアシェル(M0-28)

**Files:**
- Create: `apps/web/src/components/viewer/{ViewerShell,ViewerHeader,TocTree,SidePanel}.tsx`
- Create: `apps/web/src/hooks/use-reading-position.ts`
- Create: `apps/web/src/stores/viewer-store.ts`(Zustand: 表示モード・サイドパネルタブ・選択状態)
- Test: `apps/web/src/components/viewer/ViewerShell.test.tsx`(VT-VIEW-01, VT-VIEW-04)

**Interfaces:**
- Consumes: `useSSE`(Task 24)、UI 共通(Task 25)、`GET /api/viewer/{id}`(Task 19)。
- Produces: `ViewerShell`(表示モード URL 契約 `?mode=`・左レール44px⇄目次232px・M0 は3タブ)、`useReadingPosition`。

- [ ] **Step 1: 失敗するテストを書く(VT-VIEW-01: 3タブのみ表示)**

```tsx
// apps/web/src/components/viewer/SidePanel.test.tsx
import { render, screen } from "@testing-library/react";
import { SidePanel } from "./SidePanel";
test("M0 shows only 3 tabs (chat/figures/info), hides notes/annotations/resources", () => {
  render(<SidePanel milestone="M0" />);
  expect(screen.getByRole("tab", {name:"チャット"})).toBeInTheDocument();
  expect(screen.getByRole("tab", {name:"図表"})).toBeInTheDocument();
  expect(screen.getByRole("tab", {name:"情報"})).toBeInTheDocument();
  expect(screen.queryByRole("tab", {name:"メモ"})).toBeNull();
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pnpm --filter @yakudoku/web test`
Expected: FAIL

- [ ] **Step 3: ViewerShell・ViewerHeader・TocTree・SidePanel・stores を実装**

- [ ] **Step 4: テストを通す(VT-VIEW-01, VT-VIEW-04)**

Run: `pnpm --filter @yakudoku/web test`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/web/src/components/viewer apps/web/src/hooks/use-reading-position.ts apps/web/src/stores/viewer-store.ts && git commit -m "feat(web): viewer shell — header, toc, side panel (3 tabs), reading position (M0-28)"
```

---

### Task 27: 訳文モード(M0-29)

**Files:**
- Create: `apps/web/src/components/viewer/{TranslationPane,SectionHeading,ParallelPopover,SelectionMenu,SummaryCard,ResumeBanner}.tsx`
- Create: `apps/web/src/lib/katex-render.ts`
- Test: `apps/web/src/components/viewer/TranslationPane.test.tsx`(VT-VIEW-02, VT-VIEW-05)

**Interfaces:**
- Consumes: `ViewerShell`(Task 26)、`GET /api/translation-sets/{id}/units`(Task 19)。
- Produces: `TranslationPane`(前回位置バナー・✦3行要約・KaTeX ブロック・段落ホバー「対」・選択メニュー2項目・ゆったり組版16.5px/行間2.15/720px)。

- [ ] **Step 1: 失敗するテストを書く(VT-VIEW-02: 選択メニュー M0 は2項目)**

```tsx
// apps/web/src/components/viewer/SelectionMenu.test.tsx
import { render, screen } from "@testing-library/react";
import { SelectionMenu } from "./SelectionMenu";
test("M0 selection menu shows only ask-AI and copy", () => {
  render(<SelectionMenu milestone="M0" />);
  expect(screen.getByText("✦AIに質問")).toBeInTheDocument();
  expect(screen.getByText("コピー")).toBeInTheDocument();
  expect(screen.queryByText("語彙に追加")).toBeNull();
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pnpm --filter @yakudoku/web test`
Expected: FAIL

- [ ] **Step 3: TranslationPane ほかを実装**

- [ ] **Step 4: テストを通す(VT-VIEW-02, VT-VIEW-05)**

Run: `pnpm --filter @yakudoku/web test`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/web/src/components/viewer/{TranslationPane,SectionHeading,ParallelPopover,SelectionMenu,SummaryCard,ResumeBanner}.tsx apps/web/src/lib/katex-render.ts && git commit -m "feat(web): translation mode — summary, katex, parallel popover, selection menu (M0-29)"
```

---

### Task 28: 対訳モード + チャットパネル(M0-30)

**Files:**
- Create: `apps/web/src/components/viewer/{BilingualPane,TranslationColumnHeader}.tsx`
- Create: `apps/web/src/components/chat/{ChatPanel,ChatMessage,ChatComposer,QuickActionChips,EvidenceHighlight}.tsx`
- Test: `apps/web/src/components/chat/ChatPanel.test.tsx`(VT-VIEW-03, VT-VIEW-07, VT-VIEW-09〜12)

**Interfaces:**
- Consumes: `POST /api/chat/threads/{id}/messages` SSE(Task 20)、`BilingualPane`。
- Produces: `BilingualPane`(段落単位2カラム)、`ChatPanel`(定型チップ5種・「AI生成」バッジ・「論文外の知識」ボックス・根拠チップ¶粒度・双方向同期・免責文固定)。

- [ ] **Step 1: 失敗するテストを書く(VT-VIEW-09: AI生成バッジと免責)**

```tsx
// apps/web/src/components/chat/ChatMessage.test.tsx
import { render, screen } from "@testing-library/react";
import { ChatMessage } from "./ChatMessage";
test("assistant message shows AI badge and evidence chip", () => {
  render(<ChatMessage role="assistant" content="…" evidenceAnchors={[{blockId:"blk-1",label:"¶2"}]} />);
  expect(screen.getByText("AI生成")).toBeInTheDocument();
  expect(screen.getByText("¶2")).toBeInTheDocument();
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pnpm --filter @yakudoku/web test`
Expected: FAIL

- [ ] **Step 3: BilingualPane・ChatPanel ほかを実装**

- [ ] **Step 4: テストを通す(VT-VIEW-03,07,09〜12)**

Run: `pnpm --filter @yakudoku/web test`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/web/src/components/viewer/{BilingualPane,TranslationColumnHeader}.tsx apps/web/src/components/chat && git commit -m "feat(web): bilingual mode + chat panel with evidence chips (M0-30)"
```

---

### Task 29: ダーク + 図表 + 情報タブ(M0-31)

**Files:**
- Create: `apps/web/src/components/viewer/{FigureRefPopover,FiguresPanel,ReferencesList,InfoPanel,ThemeToggle}.tsx`
- Test: `apps/web/src/components/viewer/FigureRefPopover.test.tsx`(VT-VIEW-06)

**Interfaces:**
- Consumes: `GET /api/viewer/{id}` の figures/references(Task 19)。
- Produces: `ThemeToggle`(`data-theme` 切替・FOUC 防止)、`FigureRefPopover`(両言語キャプション・「図の位置へ移動→」)、`FiguresPanel`/`ReferencesList`/`InfoPanel`(書誌・品質バッジ・ライセンスカード)。

- [ ] **Step 1: 失敗するテストを書く(VT-VIEW-06: 図参照ポップオーバー)**

```tsx
// apps/web/src/components/viewer/FigureRefPopover.test.tsx
import { render, screen } from "@testing-library/react";
import { FigureRefPopover } from "./FigureRefPopover";
test("figure popover shows bilingual caption and jump action", () => {
  render(<FigureRefPopover figure={{captionEn:"Figure 3",captionJa:"図3"}} />);
  expect(screen.getByText("図3")).toBeInTheDocument();
  expect(screen.getByText("図の位置へ移動→")).toBeInTheDocument();
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pnpm --filter @yakudoku/web test`
Expected: FAIL

- [ ] **Step 3: FigureRefPopover ほかを実装**

- [ ] **Step 4: テストを通す(VT-VIEW-06)**

Run: `pnpm --filter @yakudoku/web test`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/web/src/components/viewer/{FigureRefPopover,FiguresPanel,ReferencesList,InfoPanel,ThemeToggle}.tsx && git commit -m "feat(web): dark mode, figure popover, figures/references/info panels (M0-31)"
```

---

### Task 30: ライブラリ画面 M0 分(M0-32)+ 設定画面 M0 分(M0-33)

**Files:**
- Create: `apps/web/src/app/(app)/library/page.tsx`
- Create: `apps/web/src/components/library/{LibraryTable,LibraryCard,QuickFilterBar,ViewSwitch}.tsx`
- Create: `apps/web/src/app/(app)/settings/` レイアウト + account ページ(BYOK フォーム)
- Test: `apps/web/src/components/library/LibraryTable.test.tsx`(VT-LIB-01, VT-LIB-02 の10列部)

**Interfaces:**
- Consumes: `GET /api/library-items`(Task 21)、`PUT /api/settings/byok/{provider}`(Task 21)。
- Produces: ライブラリ テーブル(10列固定・未供給列「—」・クイックフィルタ5種)+ カード(✦3行要約・進捗)、設定 account(BYOK 登録・マスク・モデル選択)。

- [ ] **Step 1: 失敗するテストを書く(VT-LIB-01: 10列固定)**

```tsx
// apps/web/src/components/library/LibraryTable.test.tsx
import { render, screen } from "@testing-library/react";
import { LibraryTable } from "./LibraryTable";
test("renders 10 columns, dashes for unsupplied", () => {
  render(<LibraryTable items={[{id:"li_1",title:"Rectified Flow",status:"reading",priority:null}]} />);
  expect(screen.getAllByRole("columnheader")).toHaveLength(10);
  expect(screen.getByText("—")).toBeInTheDocument();   // 未供給列
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pnpm --filter @yakudoku/web test`
Expected: FAIL

- [ ] **Step 3: library ページ・コンポーネント・settings account を実装**

- [ ] **Step 4: テストを通す(VT-LIB-01, VT-LIB-02 10列部)**

Run: `pnpm --filter @yakudoku/web test`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/web/src/app/\(app\)/library apps/web/src/app/\(app\)/settings apps/web/src/components/library && git commit -m "feat(web): library table/cards + settings account/byok (M0-32, M0-33)"
```

---

## フェーズ P5 — 拡張(M0-24 後に直列)

### Task 31: 拡張基盤(M0-34)

**Files:**
- Create: `apps/extension/package.json`(name: `@yakudoku/extension`、deps: wxt 0.20.7, @wxt-dev/module-react 1.1.3, react 19.1.0, @yakudoku/api-client)
- Create: `apps/extension/wxt.config.ts`(manifest: permissions activeTab+storage、optional_host_permissions arxiv.org)
- Create: `apps/extension/tsconfig.json`
- Create: `apps/extension/src/entrypoints/popup/App.tsx`
- Create: `apps/extension/src/lib/{api,storage,arxiv,e2e-hooks}.ts`
- Test: `apps/extension/src/lib/arxiv.test.ts`(VT-XTU-01)

**Interfaces:**
- Consumes: `@yakudoku/api-client`(Task 22)、セッションクッキー共有。
- Produces: WXT プロジェクト、`popup/App.tsx`(未ログイン UI 含む)、`WXT_E2E=1` 時の `?tab_url=` フック。

- [ ] **Step 1: 失敗するテストを書く(VT-XTU-01: arXiv URL 判定)**

```ts
// apps/extension/src/lib/arxiv.test.ts
import { test, expect } from "vitest";
import { detectArxiv } from "./arxiv";
test("detects arxiv abs url", () => {
  expect(detectArxiv("https://arxiv.org/abs/2209.03003")?.id).toBe("2209.03003");
  expect(detectArxiv("https://example.com")).toBeNull();
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pnpm --filter @yakudoku/extension test`
Expected: FAIL

- [ ] **Step 3: wxt.config・popup/App・lib を実装**

- [ ] **Step 4: テストを通す + ビルド確認**

Run: `pnpm --filter @yakudoku/extension test && pnpm --filter @yakudoku/extension build`
Expected: PASS、`.output/chrome-mv3/` が生成される

- [ ] **Step 5: コミット**

```bash
git add apps/extension && git commit -m "feat(extension): WXT MV3 base, popup shell, api client, arxiv detect (M0-34)"
```

---

### Task 32: ポップアップ3状態(M0-35)

**Files:**
- Create: `apps/extension/src/entrypoints/popup/states/{SaveForm,Saved,Existing}.tsx`
- Create: `apps/extension/src/lib/{pipeline,format}.ts`
- Test: `apps/extension/src/entrypoints/popup/states/SaveForm.test.tsx`(VT-XTU 保存前フォーム)

**Interfaces:**
- Consumes: `GET /api/ingest/check`・`POST /api/ingest/arxiv`(Task 18)。
- Produces: 保存前(書誌プレビュー・「品質レベル A 見込み」・ステータス3択・タグ提案・Enter 保存)/保存直後(パイプライン3行・2000ms ポーリング)/既存(進捗・続きから開く)。コレクション欄は M2 まで非表示。

- [ ] **Step 1: 失敗するテストを書く(保存前フォームの品質見込み表示)**

```tsx
// apps/extension/src/entrypoints/popup/states/SaveForm.test.tsx
import { render, screen } from "@testing-library/react";
import { SaveForm } from "./SaveForm";
test("shows quality A estimate when latex available", () => {
  render(<SaveForm preview={{title:"Rectified Flow", latexAvailable:true}} />);
  expect(screen.getByText(/品質レベル A 見込み/)).toBeInTheDocument();
  expect(screen.queryByText("コレクション")).toBeNull();   // M2 まで非表示
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pnpm --filter @yakudoku/extension test`
Expected: FAIL

- [ ] **Step 3: 3状態と pipeline/format を実装**

- [ ] **Step 4: テストを通す**

Run: `pnpm --filter @yakudoku/extension test`
Expected: 全 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/extension/src/entrypoints/popup/states apps/extension/src/lib/{pipeline,format}.ts && git commit -m "feat(extension): 3-state popup — save form, saved, existing (M0-35)"
```

---

### Task 33: 拡張バッジ・直近3件(M0-36)

**Files:**
- Create: `apps/extension/src/entrypoints/background.ts`(琥珀ドット #C49432 状態機械・MV3 ライフサイクル対応ポーリング)
- Create: `apps/extension/src/entrypoints/popup/RecentIngests.tsx`
- Create: 拡張アイコンアセット(`apps/extension/public/icon/`)
- Test: `apps/extension/src/entrypoints/background.test.ts`(バッジ状態遷移)

**Interfaces:**
- Consumes: `GET /api/ingest/recent`(Task 18)。
- Produces: ツールバーバッジ状態機械、フッタ「直近の取り込み」3件。

- [ ] **Step 1: 失敗するテストを書く(バッジ状態)**

```ts
// apps/extension/src/entrypoints/background.test.ts
import { test, expect } from "vitest";
import { badgeStateFor } from "./background";
test("active ingest shows amber dot", () => {
  expect(badgeStateFor([{status:"running"}])).toEqual({color:"#C49432", text:"●"});
  expect(badgeStateFor([])).toEqual({color:"", text:""});
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pnpm --filter @yakudoku/extension test`
Expected: FAIL

- [ ] **Step 3: background・RecentIngests・アイコンを実装**

- [ ] **Step 4: テストを通す + 両ターゲットビルド**

Run: `pnpm --filter @yakudoku/extension test && pnpm --filter @yakudoku/extension build && pnpm --filter @yakudoku/extension build:edge`
Expected: PASS、`.output/chrome-mv3/` と Edge 版が生成される

- [ ] **Step 5: コミット**

```bash
git add apps/extension/src/entrypoints/background.ts apps/extension/src/entrypoints/popup/RecentIngests.tsx apps/extension/public && git commit -m "feat(extension): toolbar badge + recent ingests (M0-36)"
```

---

## フェーズ P6 — 検証・統合・リリース

### Task 34: pytest M0 スイート確定(M0-38)

**Files:**
- Create: `apps/api/tests/conftest.py`(fixtures: db_session・auth_client・seed_arxiv_mock・mailpit)
- Create: `apps/api/tests/factories.py`
- Create: `tools/traceability/check.py`(受け入れ基準↔テスト ID 網羅チェック)
- Modify: `.github/workflows/ci.yml`(python ジョブでカバレッジ)

**Interfaces:**
- Produces: 全 M0 pytest が実 PostgreSQL に対して green、カバレッジ 80%+ 重点2モジュール(placeholder/translation)100%。

- [ ] **Step 1: conftest・factories・traceability を実装**

- [ ] **Step 2: M0 pytest 全通し実行**

```bash
docker compose up -d --wait db redis minio minio-init mailpit
cd apps/api && uv run alembic upgrade head && cd ../..
uv run pytest --cov=yakudoku_core --cov=yakudoku_llm --cov=yakudoku_api --cov=yakudoku_worker
```
Expected: 全 PASS、カバレッジ ≥80%、placeholder/translation =100%

- [ ] **Step 3: トレーサビリティ検査**

Run: `uv run python tools/traceability/check.py`
Expected: M0 の全受け入れ基準がテスト ID に割付済み(未割付 0)

- [ ] **Step 4: コミット**

```bash
git add apps/api/tests/conftest.py apps/api/tests/factories.py tools/traceability && git commit -m "test: M0 pytest suite, fixtures, traceability check (M0-38)"
```

---

### Task 35: E2E・VR(M0 分)(M0-39)

**Files:**
- Create: `apps/web/playwright.config.ts`(Chromium 1440×900、webServer で api+worker+web 起動)
- Create: `apps/web/e2e/global.setup.ts`(メールリンク認証・モックサーバ接続)
- Create: `apps/web/e2e/specs/`(PW-01/02/03/09 + PW-05/07/08 の M0 スコープ)
- Create: `apps/web/e2e/vr/`(VR-1a/1b/1c/3a-1〜3)
- Create: `apps/extension/e2e/`(XT-01〜05/07/09)

**Interfaces:**
- Consumes: 全 API(Task 18〜21)、全画面(Task 24〜30)、拡張(Task 31〜33)、モックサーバ(Task 8)、シード(Task 23)。
- Produces: クリティカルパス E2E(保存→readable→ビューア→チャット→位置復元)が green。

- [ ] **Step 1: playwright.config・global.setup を作成**

- [ ] **Step 2: PW-01(ログイン→ライブラリ)を書いて実行**

```bash
docker compose up -d --wait
cd apps/api && uv run alembic upgrade head && cd ../..
uv run python -m yakudoku_api.seed --sample rectified-flow --reset
pnpm --filter @yakudoku/web exec playwright install --with-deps chromium
pnpm --filter @yakudoku/web e2e
```
Expected: PW-01 PASS

- [ ] **Step 3: PW-02(取り込み経路が拡張のみ = アプリ内に+追加ボタン無し)・PW-03・PW-05(3モード)・PW-08(選択質問→根拠)・PW-09(位置復元)を追加**

Run: `pnpm --filter @yakudoku/web e2e`
Expected: 全 PASS

- [ ] **Step 4: 拡張 E2E(XT-01〜05: 保存→進捗→ビューア到達)を追加して実行**

Run: `pnpm --filter @yakudoku/extension e2e`
Expected: XT-01〜05 PASS

- [ ] **Step 5: コミット**

```bash
git add apps/web/playwright.config.ts apps/web/e2e apps/extension/e2e && git commit -m "test(e2e): M0 playwright + VR + extension e2e (M0-39)"
```

---

### Task 36: 拡張ローカル実機検証(手動シナリオ)

E3 の「Chrome/Edge 拡張を unpacked ロードで実機検証」を満たす。自動 E2E とは別に、実ブラウザでの手動確認手順を記録する。

**Files:**
- Create: `docs/extension-local-verification.md`(unpacked ロード手順と検証チェックリスト)

- [ ] **Step 1: 開発サーバー一式を起動**

```bash
docker compose up -d --wait
pnpm dev   # web:3000 / api:8000 / worker / wxt dev
```
Expected: 全プロセス起動、`curl -s localhost:8000/api/healthz` が ok

- [ ] **Step 2: 手動検証チェックリストを docs に記録**

以下を `docs/extension-local-verification.md` に記載: (1) `chrome://extensions` → デベロッパーモード → 「パッケージ化されていない拡張機能を読み込む」で `apps/extension/.output/chrome-mv3` を選択、(2) http://localhost:3000/login でログイン、(3) https://arxiv.org/abs/2209.03003 を開き拡張ポップアップで保存、(4) パイプライン進捗が進む、(5) ビューアで訳文が開ける。Edge は `edge://extensions` で同 `.output` を使用。

- [ ] **Step 3: コミット**

```bash
git add docs/extension-local-verification.md && git commit -m "docs: extension local unpacked verification checklist"
```

---

### Task 37: ホスティング手順書

実行設計 §7 の成果物。

**Files:**
- Create: `docs/deployment.md`(plans/01 §8 を運用手順に。Caddy 単一オリジン + prod docker-compose + .env.production チェックリスト + R2/OAuth/SMTP 設定 + alembic→GHCR→pull&up 順序 + pg_dump バックアップ/リストア + ストア申請手順)

- [ ] **Step 1: docs/deployment.md を作成**(plans/01 §8 準拠)

- [ ] **Step 2: 内容の完全性を確認**(環境変数チェックリストが plans/01 §8.4 の全変数を網羅、ストア申請節を含む)

- [ ] **Step 3: コミット**

```bash
git add docs/deployment.md && git commit -m "docs: global hosting runbook (Caddy + compose + R2 + store submission)"
```

---

### Task 38: M0 DoD 判定(M0-41)

**Files:**
- Create: `docs/superpowers/plans/m0-dod-report.md`(検証ゲート9項目 + AC-10-01〜04 の判定記録)

- [ ] **Step 1: 実行設計 §3 の検証ゲート9項目を実行して記録**

```bash
docker compose up -d --wait
uv sync --all-packages && pnpm install
cd apps/api && uv run alembic upgrade head && cd ../..
uv run python -m yakudoku_api.seed --sample rectified-flow --reset
pnpm turbo build lint typecheck test
uv run pytest
pnpm --filter @yakudoku/web e2e
pnpm --filter @yakudoku/extension e2e
```
Expected: 全 green

- [ ] **Step 2: AC-10-01〜04 + 取り込み経路が拡張のみ を判定して記録**

各 DoD 項目に対応するテスト(XT-02〜05・PW-05・PW-08・PW-09・PW-02)の結果を m0-dod-report.md に記録。未達があれば明示。

- [ ] **Step 3: コミット + タグ**

```bash
git add docs/superpowers/plans/m0-dod-report.md && git commit -m "docs: M0 DoD verification report (M0-41)"
git tag m0-complete
```

---

## Self-Review 結果

**Spec coverage(実行設計 §3・plans/13 M0 との突き合わせ)**:
- M0-01〜41 の全タスク → 本プラン Task 0〜38 に対応(M0-02 の計画書整合は本プランが plans を正として参照するため吸収。M0-37 ストア申請は Task 37 の手順書に記載でローカルスコープ外を明示。M0-40 本番デプロイは Task 37 の手順書に集約)。
- 検証ゲート9項目 → Task 38 で実行。
- Chrome/Edge 拡張ローカル検証(E3)→ Task 31〜33(ビルド)+ Task 36(実機)。
- ホスティング手順書 → Task 37。
- キー無しビルド/テスト green(E2)→ Fake/mock(Task 7/8)+ E2E スタブ(Task 35)。

**Placeholder scan**: 各 Task に実コード例・実コマンド・期待出力を記載。verbatim な DDL/API スキーマ/px 値は plans/ を単一ソースとして参照(DRY。数千行の重複を避ける意図的判断)。

**Type consistency**: `JobStore`/`LLMRouter`/`FakeLLMProvider`/`derive_block_id`/`AnchorJson`/`build_router_for_user`/`resolve_translation` を Interfaces ブロックで宣言し、消費側 Task と一致。

**注記**: M1・M2 の詳細プランは M0 の検証ゲート通過後に別途作成する(段階完遂 E1)。plans/13 §3〜4 に M1/M2 の WBS が既にあるため、そこから同形式で展開する。
