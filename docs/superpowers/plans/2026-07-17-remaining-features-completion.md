# Remaining Features Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 監査で判明したデータ欠損、不通導線、部分実装機能を修正し、論文から編集可能な日本語PPTXを生成する機能まで製品として完了させる。

**Architecture:** 最初にデータ保全と既存導線の欠陥を修正し、その後に既存APIだけがある機能のUIを接続する。
続いて非同期エクスポート、ユーザー別LLMルーティング、他サイト取り込み、セマンティック検索、オフライン閲覧、記事公開、論文スライド生成を独立した縦スライスとして実装する。
各スライスは機能フラグまたは既存挙動へのフォールバックを持ち、単独でテスト、コミット、ロールバックできるようにする。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、PGroonga、pgvector、ARQ、Redis、S3互換ストレージ、Next.js App Router、React、TanStack Query、TypeScript、Vitest、Playwright、PyMuPDF、KaTeX、ppt-master v2.8.0。

---

## 0. 対象範囲と固定する判断

この計画では次の項目を実装する。

- 完全バックアップの欠損、上書き、参照切れ、UIの誤表示を修正する。
- 公式リポジトリURLの上書き、スタンドアロン出力可否の誤判定、Anki TSV破損を修正する。
- 再翻訳、AI単語候補、arXiv改版差分、論文単位エクスポートのUIと非同期処理を完成させる。
- Workerのユーザー別LLMルーティングを全LLMジョブへ適用する。
- ACL Anthology、OpenReview、PubMed／PMCの取り込みをAPI、Worker、拡張まで通す。
- Hugging FaceからarXiv、GitHub、project、Model、Dataset、Spaceを関連ソース候補として収集する。
- pgvectorを使う論文／ブロック単位のセマンティック検索と「似た論文」を実装する。
- GitHub実装と論文blockの対応を、費用見積もりと実行モード付きで解析する。
- 直近10論文のオフライン閲覧を実装する。
- 記事の公開／限定公開と、公開記事への認証済みコメントを実装する。
- 論文本文、書誌、図表から編集可能な日本語PPTXを生成し、最新版を再ダウンロードできるようにする。
- 生成SDKへの移行、チャット根拠生成E2E、DB分離、仕様書の不整合を修正する。

次の項目は対象外とする。

- Anki `.apkg`、Anki Connect、Anki同期。
- 匿名コメントと論文ページ全体への公開ディスカッション。
- 有料出版社や認証必須ページの認証回避。
- ユーザー作成PPTXテンプレート、AI画像生成、画像検索、音声ナレーション、アニメーション、生成履歴の保持。

未決だった方式は次のように固定する。

- スタンドアロンHTMLはKaTeX 0.16.22を同梱し、原文表示クリーニングをWeb版と一致させる。
- PDF注釈は原文PDFへブロック単位で埋め込み、対訳PDFは原文ページと訳文ページを交互に束ねる。
- セマンティック検索はOpenAI `text-embedding-3-small`、1536次元、BYOK優先、運営キーへのフォールバック、pgvector HNSW、RRF 1:1を採用する。
- オフライン閲覧は直近10論文、1論文50 MiB、全体200 MiBを上限とし、ネットワーク障害時だけキャッシュへフォールバックする。
- 他サイトから取り込む論文はprivateを既定とし、機械判定できる互換ライセンスがある場合だけ共有可能にする。
- Hugging Faceの関連リンクは候補として提示し、採用前に確定Resourceへしない。
- GitHubコード対応解析は `off` / `on_demand` / `automatic` の三モードとし、既定を `on_demand`、月額予算を5.00 USDとする。
- PPTX生成はデスクトップの論文ビューアから開始し、用途を輪読会、研究発表、実装解説から選ぶ。
- PPTXは日本語、16:9、編集可能なnative shapeを既定とし、論文由来の図表だけを使う。
- `ppt-master` はv2.8.0のcommit `0c0bdaf0dd953afc2c00322e92f26dc02fc1c51f`へ固定し、本番実行中に上流を更新しない。
- 生成物は論文アイテムごとに最新版だけを保持し、再生成が失敗した場合は以前の成功成果物を残す。
- 記事公開は公開時点のスナップショットを保存する。
  原文、訳文、メモ、チャット、非公開の議論ブロックはスナップショットへ含めない。

監査項目と実装タスクの対応は次のとおりとする。

| 監査項目 | 実装タスク |
|---|---|
| 完全バックアップの欠損、上書き、UI誤表示 | 1〜4、15、18、21、22、28 |
| 公式URL上書き、readiness誤判定、Anki TSV破損 | 5 |
| 再翻訳ボタンの未接続 | 6 |
| 生成SDK未移行、チャット根拠E2E未検証 | 7 |
| AI単語候補UI | 8 |
| arXiv改版差分UI | 9 |
| スタンドアロンHTML／PDF／ZIP／UI | 10〜12 |
| Workerのユーザー別LLMルーティング | 13、14、28、29 |
| ACL／OpenReview／PubMed／PMC取り込み | 15〜17 |
| Hugging Face関連ソース収集 | 18 |
| セマンティック検索 | 19、20 |
| GitHubコード対応解析 | 21、22 |
| 論文のオフライン閲覧 | 23 |
| 記事公開／限定公開／コメント | 24〜26 |
| 論文プレゼンテーション／PPTX生成 | 27〜30 |
| API契約、文書、DB分離、E2E残件 | 31、32 |

## 1. 実行順とマージ単位

### 実装依頼の起点

この文書を全機能実装の起点とし、Task 1からTask 32まで番号順に実行する。
実装者は冒頭の対象範囲と固定判断を先に読み、各Stepのチェック欄、テスト、コミットを省略しない。
実装開始時は `superpowers:using-git-worktrees` を使って専用branchとworktreeを作り、ユーザーの作業中ファイルから分離する。
Task 18、21、22では、詳細なデータ境界と費用制御を[Hugging Face関連ソース収集とGitHubコード対応解析の設計](../specs/2026-07-17-huggingface-code-correspondence-design.md)で確認する。
Task 27〜30では、入力境界と上流更新規則を[論文スライド生成ツールの設計](../specs/2026-07-16-paper-presentation-tool-design.md)で確認する。
実装を複数のセッションへ分ける場合は、完了済みチェック欄、最新コミット、未解決の失敗を引き継ぎ、最初の未完了Stepから再開する。

ユーザー受け入れ確認は、各フェーズで分割して実施しても、フェーズKの完了後にまとめて実施してもよい。
まとめて実施する場合は、[最終ユーザー受け入れチェックリスト](./2026-07-17-user-acceptance-checklist.md)の全項目をリリース候補版で確認する。
ユーザー確認へ渡す前に、実装者が自動テスト、マイグレーション、OpenAPI一致を確認し、確認用アカウントとサンプルデータを用意する。

| フェーズ | タスク | マージ条件 |
|---|---|---|
| A | 1〜5 | データ欠損と既存導線のP0/P1が解消し、既存API／Worker／Webテストが通る |
| B | 6〜9 | 再翻訳と、バックエンドだけ存在する2機能がWebから利用でき、生成SDK移行が完了する |
| C | 10〜12 | 論文単位HTML／PDF／ZIPがスタンドアロンで生成できる |
| D | 13〜14 | 全Worker LLMジョブがジョブ所有者のルートを使う |
| E | 15〜18 | ACL、OpenReview、PubMed／PMC、Hugging Faceを本文取り込みまたは関連ソース候補へ流せる |
| F | 19〜20 | セマンティック検索を有効化してもユーザー境界と全文検索フォールバックが保たれる |
| G | 21〜22 | GitHubコード対応解析を費用制御付きで実行し、論文と固定commit行を往復できる |
| H | 23 | 直近10論文をオフラインで再表示でき、401をキャッシュで隠さない |
| I | 24〜26 | 記事公開、限定公開、コメント、削除、モデレーションが動く |
| J | 27〜30 | 論文から根拠付き構成を作り、安全に変換した編集可能な日本語PPTXをダウンロードできる |
| K | 31〜32 | 全体回帰、OpenAPI一致、文書一致、E2Eが通る |

フェーズごとにmainへ統合できるが、同一フェーズ内のタスクは記載順に実行する。
最終マージでは、チェックリストのP0、P1、BLOCKEDがすべて0件であり、確認したビルドとマージ対象のコミットSHAが一致していることを条件とする。

### Task 1: 完全バックアップの共有翻訳と用語集を復元対象へ含める

**Files:**

- Modify: `apps/worker/src/alinea_worker/tasks/export_user_data.py`
- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py`
- Test: `apps/worker/tests/test_export_bulk.py`
- Test: `apps/worker/tests/test_import_bulk.py`

- [ ] **Step 1: 共有翻訳と論文用用語集が欠落する失敗テストを書く**

`TranslationSet.user_id is None` の共有翻訳と、対象ユーザーの `library_item_id` に紐づく `Glossary.user_id is None` をシードする。
エクスポートした `data.json` に両方が入り、別ユーザーへのインポート後に翻訳単位と用語が復元されることを検査する。

```python
assert exported["translation_sets"][0]["scope"] == "shared"
assert exported["glossaries"][0]["library_item_id"] == str(source_item.id)
assert await scalar_count(db, TranslationUnit) == expected_units
assert await scalar_count(db, GlossaryTerm) == expected_terms
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py -k 'shared_translation or paper_glossary' -q`

Expected: 共有行が0件のためFAIL。

- [ ] **Step 3: ライブラリ項目から到達可能な翻訳と用語集を選ぶ**

エクスポート対象の `paper_id`、`revision_id`、`library_item_id` を先に集合化し、所有者列だけではなく到達関係で選択する。

```python
translation_predicate = or_(
    TranslationSet.user_id == user_id,
    and_(TranslationSet.user_id.is_(None), TranslationSet.revision_id.in_(revision_ids)),
)
glossary_predicate = or_(
    Glossary.user_id == user_id,
    Glossary.library_item_id.in_(library_item_ids),
)
```

共有行を復元するときは、既存の同一リビジョン／style／scopeを再利用し、他ユーザー専用行へ変換しない。

- [ ] **Step 4: 対象テストを通す**

Run: `uv run pytest apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py -q`

Expected: PASS。

- [ ] **Step 5: コミットする**

```bash
git add apps/worker/src/alinea_worker/tasks/export_user_data.py apps/worker/src/alinea_worker/tasks/import_user_data.py apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py
git commit -m "fix(migration): preserve shared translations and glossaries"
```

### Task 2: 完全バックアップのDB列とアセット閉包を完全にする

**Files:**

- Modify: `apps/worker/src/alinea_worker/tasks/export_user_data.py`
- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py`
- Modify: `packages/py-core/src/alinea_core/document/blocks.py`
- Test: `apps/worker/tests/test_export_bulk.py`
- Test: `apps/worker/tests/test_import_bulk.py`

- [ ] **Step 1: 現在欠落する列とアセットのラウンドトリップテストを書く**

`Paper.abstract_ja`、`summary_lines`、`arxiv_categories`、`license`、`official_repo_url`、`thumbnail_key`、`LibraryItem.suggested_tags`、`reading_position`、`queue_order`、`thumbnail_key`、`Note.source_chat_message_id`、ResourceLinkの正規化URLを埋める。
DocumentContent内のfigure/table/equationアセット、paper／libraryのサムネイル、retina兄弟をS3へ置き、復元後の列とSHA-256一致を検査する。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/worker/tests/test_import_bulk.py -k 'all_columns or document_assets or thumbnails or provenance' -q`

Expected: 欠落列または欠落オブジェクトでFAIL。

- [ ] **Step 3: シリアライザをモデル列と一致させる**

PaperとLibraryItemは許可リスト式のシリアライザを共有し、ID参照はインポートのIDマップで復元する。

```python
PAPER_EXPORT_FIELDS = (
    "arxiv_id", "doi", "pdf_sha256", "title", "authors", "abstract", "abstract_ja",
    "summary_lines", "published_on", "venue", "arxiv_categories", "license",
    "bib_estimated", "visibility", "latest_version", "official_repo_url",
    "extracted_terms", "thumbnail_key",
)
LIBRARY_EXPORT_FIELDS = (
    "status", "priority", "deadline", "tags", "suggested_tags", "one_line_note",
    "understanding", "importance", "reading_position", "queue_order",
    "total_active_seconds", "thumbnail_key", "added_at", "finished_at",
)
```

Noteの `source_chat_message_id` は `chat_message_id_map` で張り直し、存在しない参照だけを `None` にする。
ResourceLinkのURL列はモデル上の正規化済み値をそのまま保存する。

- [ ] **Step 4: 文書IRからアセットキーを再帰収集する**

```python
def iter_document_asset_keys(content: Mapping[str, object]) -> Iterator[str]:
    for block in flatten_serialized_blocks(content):
        for key in (block.get("asset_key"), block.get("thumbnail_key")):
            if isinstance(key, str) and key:
                yield key
```

Paper／LibraryItemのサムネイル、既知の `@2x` 兄弟、overview、explainer、source_assetsと集合和を取り、manifestのSHA-256とbyte sizeを検証する。

- [ ] **Step 5: ラウンドトリップテストを通す**

Run: `uv run pytest apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py -q`

Expected: PASS。

- [ ] **Step 6: コミットする**

```bash
git add apps/worker/src/alinea_worker/tasks/export_user_data.py apps/worker/src/alinea_worker/tasks/import_user_data.py packages/py-core/src/alinea_core/document/blocks.py apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py
git commit -m "fix(migration): round-trip complete records and assets"
```

### Task 3: インポートのマージ規則を「既存データ不変」に揃える

**Files:**

- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py`
- Test: `apps/worker/tests/test_import_bulk.py`

- [ ] **Step 1: 既存設定と既存ユーザーデータを上書きしないテストを書く**

復元先へ異なるテーマ、モデルルート、既存メモ、既存ResourceLinkを作る。
同じバックアップを2回取り込んでも既存値が変わらず、新規キーと新規行だけが追加されることを検査する。

```python
assert target.settings["display"]["theme"] == "dark"
assert target.settings["new_section"] == imported.settings["new_section"]
assert existing_note.body == "target value"
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/worker/tests/test_import_bulk.py -k 'preserves_target or idempotent_merge' -q`

Expected: `users.settings` の置換でFAIL。

- [ ] **Step 3: 設定を再帰的な不足キー補完へ変更する**

```python
def merge_missing(target: dict[str, object], source: Mapping[str, object]) -> dict[str, object]:
    merged = copy.deepcopy(target)
    for key, value in source.items():
        if key not in merged:
            merged[key] = copy.deepcopy(value)
        elif isinstance(merged[key], dict) and isinstance(value, Mapping):
            merged[key] = merge_missing(merged[key], value)
    return merged
```

DB行は自然キーまたは元IDマップで既存判定し、既存行へのUPDATEを行わない。

- [ ] **Step 4: 対象テストを通す**

Run: `uv run pytest apps/worker/tests/test_import_bulk.py -q`

Expected: PASS。

- [ ] **Step 5: コミットする**

```bash
git add apps/worker/src/alinea_worker/tasks/import_user_data.py apps/worker/tests/test_import_bulk.py
git commit -m "fix(import): preserve existing target data"
```

### Task 4: データ設定画面の重複と失敗理由表示を直す

**Files:**

- Modify: `apps/web/src/components/settings/ExportSettings.tsx`
- Modify: `apps/web/src/components/settings/ExportSettings.test.tsx`
- Modify: `apps/web/src/components/settings/types.ts`

- [ ] **Step 1: UI契約テストを書く**

「JSON 一括」を削除し、「完全バックアップ」を一つだけ表示する。
失敗ジョブの `error` を表示し、既存データは上書きされないこととBYOKが対象外であることを表示するテストを追加する。

- [ ] **Step 2: 失敗を確認する**

Run: `pnpm --filter @alinea/web test -- ExportSettings.test.tsx`

Expected: 重複ボタンまたは汎用エラー文のためFAIL。

- [ ] **Step 3: Job表示型とエラー表示を統一する**

```ts
type DataJobState = {
  status: "queued" | "running" | "succeeded" | "failed";
  error?: string | null;
  summary?: ImportSummary | null;
};
```

失敗時は `job.error ?? "処理に失敗しました"` をトーストとカード内に表示する。

- [ ] **Step 4: Webテストを通す**

Run: `pnpm --filter @alinea/web test -- ExportSettings.test.tsx`

Expected: PASS。

- [ ] **Step 5: コミットする**

```bash
git add apps/web/src/components/settings/ExportSettings.tsx apps/web/src/components/settings/ExportSettings.test.tsx apps/web/src/components/settings/types.ts
git commit -m "fix(settings): clarify complete backup and import errors"
```

### Task 5: 公式リポジトリURL、出力可否、Anki TSVの欠陥を直す

**Files:**

- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Modify: `apps/api/src/alinea_api/routers/export.py`
- Modify: `apps/api/src/alinea_api/routers/vocab.py`
- Test: `apps/worker/tests/test_ingest.py`
- Test: `apps/api/tests/test_standalone_export.py`
- Test: `apps/api/tests/test_vocab.py`
- Modify: `apps/web/src/components/vocab/VocabHeader.test.tsx`
- Modify: `apps/web/src/app/(app)/vocab/[[...vocabId]]/page.test.tsx`

- [ ] **Step 1: 三つの回帰テストを書く**

既存 `official_repo_url` が再取り込みで変わらないこと、空または不正なDocumentRevisionではtranslation／bilingual HTMLがfalseになること、Ankiの全セルが常に3列1行になることを検査する。

```python
assert paper.official_repo_url == "https://github.com/manual/confirmed"
assert availability.translation_html is False
assert all(len(line.split("\t")) == 3 for line in body.splitlines()[3:])
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/worker/tests/test_ingest.py apps/api/tests/test_standalone_export.py apps/api/tests/test_vocab.py -k 'repo_url or availability or anki' -q`

Expected: 少なくとも三件FAIL。

- [ ] **Step 3: 書き込み条件とreadiness条件を修正する**

```python
if paper.official_repo_url is None and meta.official_repo_url is not None:
    paper.official_repo_url = meta.official_repo_url

translation_html = source_ready and translation_complete
bilingual_html = source_ready and translation_complete
```

- [ ] **Step 4: TSVセルをHTML化して制御文字を除去する**

Ankiの `#html:true` を利用し、セル内改行を `<br>`、タブを空白、CRを削除し、HTML特殊文字をescapeする。

```python
def _anki_cell(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return html.escape(normalized.replace("\t", " ")).replace("\n", "<br>")
```

- [ ] **Step 5: 対象テストを通す**

Run: `uv run pytest apps/worker/tests/test_ingest.py apps/api/tests/test_standalone_export.py apps/api/tests/test_vocab.py -q`

Run: `pnpm --filter @alinea/web test -- VocabHeader.test.tsx page.test.tsx`

Expected: APIとWebがPASS。

Webテストでは現在の `kind`、`due`、`q`、`sort` がAnkiダウンロードURLへ一度ずつ渡ることを検査する。

- [ ] **Step 6: コミットする**

```bash
git add apps/worker/src/alinea_worker/pipeline.py apps/api/src/alinea_api/routers/export.py apps/api/src/alinea_api/routers/vocab.py apps/worker/tests/test_ingest.py apps/api/tests/test_standalone_export.py apps/api/tests/test_vocab.py apps/web/src/components/vocab/VocabHeader.test.tsx apps/web/src/app/'(app)'/vocab/'[[...vocabId]]'/page.test.tsx
git commit -m "fix: preserve repo URLs and harden exports"
```

### Task 6: 再翻訳から提案採否までのWeb導線を接続する

**Files:**

- Create: `apps/web/src/components/viewer/use-retranslation.ts`
- Create: `apps/web/src/components/viewer/RetranslationProposal.tsx`
- Modify: `apps/web/src/components/viewer/TranslationPane.tsx`
- Modify: `apps/web/src/components/viewer/TranslatedParagraph.tsx`
- Modify: `apps/web/src/components/viewer/ParallelPopover.tsx`
- Test: `apps/web/src/components/viewer/TranslationPane.test.tsx`
- Test: `apps/web/e2e/specs/pw-07-translation.spec.ts`

- [ ] **Step 1: ボタン操作の失敗テストを書く**

再翻訳クリックで `translationsRetranslate` がunit IDと指示文を受け、SSE完了後にunits queryをinvalidateし、提案を表示することを検査する。
採用で `translationsAcceptProposal`、破棄で `translationsDiscardProposal` が呼ばれることも検査する。

- [ ] **Step 2: 失敗を確認する**

Run: `pnpm --filter @alinea/web test -- TranslationPane.test.tsx`

Expected: `translationsRetranslate` が呼ばれずFAIL。

- [ ] **Step 3: 再翻訳hookを実装する**

```ts
export function useRetranslation(unitId: string, queryKey: readonly unknown[]) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (instruction?: string) =>
      translationsRetranslate({ path: { unit_id: unitId }, body: { instruction } }),
    onSuccess: async ({ data }) => {
      await waitForJobEvent(data.job_id);
      await queryClient.invalidateQueries({ queryKey });
    },
  });
}
```

同一unitの処理中はボタンをdisabledにし、エラーをPopover内へ表示する。

- [ ] **Step 4: 提案カードを接続する**

proposalがあるunitでは現行訳と候補訳を並べ、「採用」「破棄」を表示する。
採否完了後にunits queryをinvalidateする。

- [ ] **Step 5: UnitとE2Eを通す**

Run: `pnpm --filter @alinea/web test -- TranslationPane.test.tsx`

Run: `pnpm --filter @alinea/web e2e -- pw-07-translation.spec.ts`

Expected: PASS。PW-07の再翻訳fixmeを削除する。

- [ ] **Step 6: コミットする**

```bash
git add apps/web/src/components/viewer/use-retranslation.ts apps/web/src/components/viewer/RetranslationProposal.tsx apps/web/src/components/viewer/TranslationPane.tsx apps/web/src/components/viewer/TranslatedParagraph.tsx apps/web/src/components/viewer/ParallelPopover.tsx apps/web/src/components/viewer/TranslationPane.test.tsx apps/web/e2e/specs/pw-07-translation.spec.ts
git commit -m "feat(viewer): complete retranslation proposal flow"
```

### Task 7: 生成SDKへの移行とライブチャットE2Eを完了する

**Files:**

- Modify: `apps/web/src/lib/resources-api.ts`
- Modify: `apps/web/src/components/viewer/resources/types.ts`
- Modify: `apps/web/src/components/collections/api.ts`
- Modify: `apps/web/src/components/collections/types.ts`
- Modify: `apps/web/src/app/(public)/c/[token]/fetch-share.ts`
- Modify: `apps/web/src/hooks/use-reading-session.ts`
- Modify: `apps/web/e2e/specs/pw-08-chat.spec.ts`
- Modify: `packages/llm/src/alinea_llm/testing/mock_server.py`
- Modify: `packages/llm/tests/test_mock_server.py`

- [ ] **Step 1: raw fetchを禁止する静的検査を追加する**

対象ファイルから `fetch("/api/` が消え、生成SDKのresources／collections／share／reading-session関数だけを使うことを検査する。

Run: `rg -n 'fetch\(["\x27]/api/' apps/web/src/lib/resources-api.ts apps/web/src/components/collections/api.ts apps/web/src/app/'(public)'/c/'[token]'/fetch-share.ts apps/web/src/hooks/use-reading-session.ts`

Expected before change: 一件以上出力。

- [ ] **Step 2: SDKの戻り型へ置き換える**

手書きDTOを削除し、`@alinea/api-client` の生成型と関数をimportする。
既存UIが必要とする形の変換は各ドメインの薄いmapperだけに残す。

- [ ] **Step 3: Responses API互換のfake providerを用意する**

E2E fakeは `output_config` を受け入れ、chat streamに本文とevidence eventを返す。
新規質問を送った直後の根拠チップを検査し、履歴シードだけに依存する経路を削除する。

- [ ] **Step 4: WebとチャットE2Eを通す**

Run: `pnpm --filter @alinea/web test`

Run: `pnpm --filter @alinea/web e2e -- pw-08-chat.spec.ts`

Expected: PASS。新規質問のevidence生成をskipしない。

- [ ] **Step 5: コミットする**

```bash
git add apps/web/src/lib/resources-api.ts apps/web/src/components/viewer/resources/types.ts apps/web/src/components/collections/api.ts apps/web/src/components/collections/types.ts apps/web/src/app/'(public)'/c/'[token]'/fetch-share.ts apps/web/src/hooks/use-reading-session.ts packages/llm/src/alinea_llm/testing/mock_server.py packages/llm/tests/test_mock_server.py apps/web/e2e/specs/pw-08-chat.spec.ts
git commit -m "refactor(web): finish generated client migration"
```

### Task 8: AI単語候補をビューアで抽出、採用、破棄できるようにする

**Files:**

- Create: `apps/web/src/components/viewer/VocabCandidatesPanel.tsx`
- Create: `apps/web/src/components/viewer/VocabCandidatesPanel.test.tsx`
- Modify: `apps/web/src/components/viewer/SidePanel.tsx`
- Modify: `apps/web/src/components/viewer/ViewerShell.tsx`
- Modify: `apps/web/src/components/ui/SidePanelTabs.tsx`
- Test: `apps/web/src/components/viewer/SidePanel.test.tsx`

- [ ] **Step 1: UI状態の失敗テストを書く**

未抽出、抽出中、候補あり、空、失敗の五状態を検査する。
採用と破棄が候補一覧から即時に消え、採用時は語彙queryもinvalidateされることを検査する。

- [ ] **Step 2: 失敗を確認する**

Run: `pnpm --filter @alinea/web test -- VocabCandidatesPanel.test.tsx SidePanel.test.tsx`

Expected: パネルが存在せずFAIL。

- [ ] **Step 3: TanStack Queryで候補APIを接続する**

```ts
const candidatesKey = ["vocab-candidates", itemId] as const;
const list = useQuery({ queryKey: candidatesKey, queryFn: listCandidates });
const extract = useMutation({ mutationFn: extractCandidates, onSuccess: waitThenInvalidate });
const accept = useMutation({ mutationFn: acceptCandidate, onSuccess: invalidateCandidatesAndVocab });
const dismiss = useMutation({ mutationFn: dismissCandidate, onSuccess: invalidateCandidates });
```

候補にはterm、kind、reason、文脈文を表示し、文脈中のhighlight範囲を強調する。

- [ ] **Step 4: SidePanelへ「単語候補」タブを追加する**

デスクトップとモバイルで同じtab IDを使い、pending件数をbadgeへ表示する。

- [ ] **Step 5: Webテストを通す**

Run: `pnpm --filter @alinea/web test -- VocabCandidatesPanel.test.tsx SidePanel.test.tsx ViewerShell.test.tsx`

Expected: PASS。

- [ ] **Step 6: コミットする**

```bash
git add apps/web/src/components/viewer/VocabCandidatesPanel.tsx apps/web/src/components/viewer/VocabCandidatesPanel.test.tsx apps/web/src/components/viewer/SidePanel.tsx apps/web/src/components/viewer/ViewerShell.tsx apps/web/src/components/ui/SidePanelTabs.tsx apps/web/src/components/viewer/SidePanel.test.tsx
git commit -m "feat(viewer): add AI vocabulary candidate review"
```

### Task 9: arXiv改版差分を情報パネルへ表示する

**Files:**

- Create: `apps/web/src/components/viewer/RevisionDiffPanel.tsx`
- Create: `apps/web/src/components/viewer/RevisionDiffPanel.test.tsx`
- Modify: `apps/web/src/components/viewer/InfoPanel.tsx`
- Modify: `apps/web/src/components/viewer/InfoPanel.test.tsx`
- Modify: `apps/web/src/components/viewer/ResumeBanner.tsx`

- [ ] **Step 1: 版選択と差分表示の失敗テストを書く**

`viewerListRevisions` の隣接2版を既定選択し、`viewerRevisionDiff` のadded／removed／changed件数と変更ブロックを表示することを検査する。
版が一つだけならセクションを非表示にする。

- [ ] **Step 2: 失敗を確認する**

Run: `pnpm --filter @alinea/web test -- RevisionDiffPanel.test.tsx InfoPanel.test.tsx`

Expected: 差分UIが存在せずFAIL。

- [ ] **Step 3: 読み取り専用差分パネルを実装する**

```ts
type DiffSelection = { from: string; to: string };
const queryKey = ["revision-diff", paperId, selection.from, selection.to];
```

変更ブロックはstatus、section、old_text、new_textを折り畳み表示する。
採用操作は既存 `adopt-revision` だけに任せ、差分パネルから自動切替しない。

- [ ] **Step 4: Webテストを通す**

Run: `pnpm --filter @alinea/web test -- RevisionDiffPanel.test.tsx InfoPanel.test.tsx`

Expected: PASS。

- [ ] **Step 5: コミットする**

```bash
git add apps/web/src/components/viewer/RevisionDiffPanel.tsx apps/web/src/components/viewer/RevisionDiffPanel.test.tsx apps/web/src/components/viewer/InfoPanel.tsx apps/web/src/components/viewer/InfoPanel.test.tsx apps/web/src/components/viewer/ResumeBanner.tsx
git commit -m "feat(viewer): show arXiv revision differences"
```

### Task 10: スタンドアロンHTMLの表示品質をWeb版と一致させる

**Files:**

- Create: `apps/api/src/alinea_api/static/katex/`
- Create: `apps/api/src/alinea_api/schemas/latex_display.py`
- Modify: `apps/api/src/alinea_api/schemas/standalone_html.py`
- Modify: `apps/api/pyproject.toml`
- Test: `apps/api/tests/test_standalone_html.py`

- [ ] **Step 1: 数式と原文クリーニングの失敗テストを書く**

ネットワーク参照が0件、KaTeX JS／CSS／WOFF2がdata URIまたはinlineで入ること、`\label`、`\notag`、`\textbf{}`、top-level `&` がWeb版と同じ表示文字列になることをgoldenで検査する。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/api/tests/test_standalone_html.py -k 'katex or latex_clean' -q`

Expected: LaTeXソースのフォールバック表示でFAIL。

- [ ] **Step 3: KaTeX 0.16.22をパッケージデータとして固定する**

既存Web依存からライセンス、CSS、minified JS、使用フォントだけをコピーし、SHA-256 manifestを同梱する。
レンダラは外部CDNを参照しない。

- [ ] **Step 4: Webの表示クリーニングをPythonへ移植する**

`apps/web/src/components/viewer/latex-display-clean.ts` のfixtureを共有JSONへ移し、TypeScriptとPythonの双方が同じinput／outputを検査する。

- [ ] **Step 5: APIテストを通す**

Run: `uv run pytest apps/api/tests/test_standalone_html.py -q`

Expected: PASS。

- [ ] **Step 6: コミットする**

```bash
git add apps/api/src/alinea_api/static/katex apps/api/src/alinea_api/schemas/latex_display.py apps/api/src/alinea_api/schemas/standalone_html.py apps/api/pyproject.toml apps/api/tests/test_standalone_html.py
git commit -m "feat(export): embed KaTeX in standalone HTML"
```

### Task 11: 論文単位PDF／ZIP生成ジョブを実装する

**Files:**

- Create: `apps/worker/src/alinea_worker/tasks/export_paper.py`
- Create: `apps/worker/src/alinea_worker/pdf_annotations.py`
- Create: `apps/worker/tests/test_export_paper.py`
- Modify: `apps/worker/src/alinea_worker/tasks/__init__.py`
- Modify: `apps/api/alembic/versions/0013_paper_export_job_kind.py`

- [ ] **Step 1: ZIP構造、注釈、対訳PDFの失敗テストを書く**

原文HTML、訳文HTML、対訳HTML、記事HTML、原文PDF、訳文PDF、対訳PDFを選択し、ZIP内のファイル名を検査する。
PyMuPDFで原文PDFを読み戻してhighlight／text annotation数を検査し、対訳PDFのページ順を `source-1, translated-1, ...` と検査する。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/worker/tests/test_export_paper.py -q`

Expected: handler未登録でFAIL。

- [ ] **Step 3: ジョブkindとhandlerを追加する**

`ck_jobs_kind` は既存値の超集合へ `paper_export` を追加する。

```python
HANDLERS["paper_export"] = run_export_paper_job
```

ジョブは所有者、library item、選択artifactを再検証し、availableでないartifactを含む場合は開始前にfailする。

- [ ] **Step 4: PDF注釈と対訳結合を実装する**

`block_search_index.page/bbox` を使って原文PDFへ矩形とpopup本文を追加する。
bboxが無い注釈は `skipped_annotations` に数え、黙って成功扱いにしない。
出力PDFとZIPは一時ファイルへストリームし、完成後にS3へputして署名URLを返す。

- [ ] **Step 5: Workerテストを通す**

Run: `uv run pytest apps/worker/tests/test_export_paper.py -q`

Expected: PASS。

- [ ] **Step 6: コミットする**

```bash
git add apps/worker/src/alinea_worker/tasks/export_paper.py apps/worker/src/alinea_worker/pdf_annotations.py apps/worker/tests/test_export_paper.py apps/worker/src/alinea_worker/tasks/__init__.py apps/api/alembic/versions/0013_paper_export_job_kind.py
git commit -m "feat(export): build annotated paper archives"
```

### Task 12: 論文単位エクスポートAPIと選択モーダルを完成させる

**Files:**

- Modify: `apps/api/src/alinea_api/routers/export.py`
- Modify: `apps/api/src/alinea_api/schemas/export.py`
- Modify: `apps/api/tests/test_standalone_export.py`
- Modify: `packages/api-client/openapi.json`
- Modify: `packages/api-client/src/generated/`
- Create: `apps/web/src/components/viewer/PaperExportModal.tsx`
- Create: `apps/web/src/components/viewer/PaperExportModal.test.tsx`
- Modify: `apps/web/src/components/viewer/ViewerHeader.tsx`

- [ ] **Step 1: 非同期APIの失敗テストを書く**

所有権、artifact値域、availability不一致、冪等キー、job polling、download URLを検査する。

- [ ] **Step 2: APIを実装する**

```python
class PaperExportRequest(BaseModel):
    artifacts: list[Literal[
        "source_html", "translation_html", "bilingual_html", "article_html",
        "pdf_original", "pdf_translated", "pdf_bilingual",
    ]]
```

単一HTMLだけの選択は既存同期endpointへ直接遷移し、それ以外は `paper_export` jobをenqueueする。

- [ ] **Step 3: OpenAPIとSDKを再生成する**

Run: `cd apps/api && uv run python -m alinea_api.export_openapi > ../../packages/api-client/openapi.json`

Run: `pnpm --filter @alinea/api-client generate`

Expected: `exportStandaloneStart` とstatus型が生成される。

- [ ] **Step 4: ViewerHeaderへ複数選択モーダルを追加する**

availability=falseはdisabledにし、理由を表示する。
処理中は進捗、失敗時はjob.error、完了時はdownloadを表示する。

- [ ] **Step 5: APIとWebテストを通す**

Run: `uv run pytest apps/api/tests/test_standalone_export.py -q`

Run: `pnpm --filter @alinea/web test -- PaperExportModal.test.tsx ViewerHeader`

Expected: PASS。

- [ ] **Step 6: コミットする**

```bash
git add apps/api/src/alinea_api/routers/export.py apps/api/src/alinea_api/schemas/export.py apps/api/tests/test_standalone_export.py packages/api-client/openapi.json packages/api-client/src/generated apps/web/src/components/viewer/PaperExportModal.tsx apps/web/src/components/viewer/PaperExportModal.test.tsx apps/web/src/components/viewer/ViewerHeader.tsx
git commit -m "feat(export): expose standalone paper export UI"
```

### Task 13: Workerへユーザー別LLMルーターファクトリを導入する

**Files:**

- Create: `packages/py-core/src/alinea_core/llm/runtime.py`
- Create: `packages/py-core/src/alinea_core/llm/__init__.py`
- Create: `packages/py-core/tests/test_llm_runtime.py`
- Create: `apps/worker/src/alinea_worker/user_router.py`
- Modify: `apps/worker/src/alinea_worker/bootstrap.py`
- Modify: `apps/api/src/alinea_api/llm/deps.py`
- Modify: `apps/api/src/alinea_api/llm/key_store.py`
- Modify: `apps/api/src/alinea_api/llm/meter.py`
- Modify: `apps/api/src/alinea_api/llm/route_store.py`
- Test: `apps/worker/tests/test_bootstrap.py`
- Test: `apps/api/tests/test_llm_settings.py`

- [ ] **Step 1: 二ユーザーのルート分離テストを書く**

同じWorkerプロセスでuser AはOpenAI、user BはGoogleを選び、同じtaskの解決結果が混ざらないことを検査する。
失効後は次のジョブで新しいoverrideが使われることも検査する。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/worker/tests/test_bootstrap.py apps/api/tests/test_llm_settings.py -k 'per_user or route_invalidation' -q`

Expected: Workerの共有routerによりFAIL。

- [ ] **Step 3: DBルート、BYOK解決、usage meterを共有層へ移す**

apps間importを避けるため、DBと暗号化に依存する実装を `alinea_core.llm.runtime` へ置く。
API側の `key_store.py` と `route_store.py` は互換re-exportに縮め、既存importを一度に壊さない。

```python
@dataclass(frozen=True)
class LLMRuntimeConfig:
    operator_api_keys: Mapping[str, str]
    key_encryption_secret: str
    route_cache_ttl_s: int = 60
```

- [ ] **Step 4: ジョブ単位ファクトリを実装する**

```python
class UserRouterFactory:
    async def for_job(self, *, user_id: str, task: str) -> LLMRouter:
        async with self.sessionmaker() as session:
            return await build_user_router(
                session=session,
                cache=self.redis,
                config=self.runtime_config,
                user_id=str(user_id),
                task=task,
            )
```

秘密鍵やrouter自体はジョブ終了後に保持せず、60秒キャッシュはroute chain metadataだけに限定する。

- [ ] **Step 5: bootstrap contextへfactoryを載せる**

共有 `ctx["router"]` は移行期間だけ残し、新規コードは `ctx["user_router_factory"]` を使う。

- [ ] **Step 6: 対象テストを通す**

Run: `uv run pytest packages/py-core/tests/test_llm_runtime.py apps/worker/tests/test_bootstrap.py apps/api/tests/test_llm_settings.py -q`

Expected: PASS。

- [ ] **Step 7: コミットする**

```bash
git add packages/py-core/src/alinea_core/llm packages/py-core/tests/test_llm_runtime.py apps/worker/src/alinea_worker/user_router.py apps/worker/src/alinea_worker/bootstrap.py apps/api/src/alinea_api/llm/deps.py apps/api/src/alinea_api/llm/key_store.py apps/api/src/alinea_api/llm/meter.py apps/api/src/alinea_api/llm/route_store.py apps/worker/tests/test_bootstrap.py apps/api/tests/test_llm_settings.py
git commit -m "feat(worker): resolve LLM routes per user job"
```

### Task 14: 全Worker LLMジョブをユーザー別ルーターへ移行する

**Files:**

- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Modify: `apps/worker/src/alinea_worker/tasks/generate_article.py`
- Modify: `apps/worker/src/alinea_worker/tasks/generate_vocab_ai.py`
- Modify: `apps/worker/src/alinea_worker/tasks/extract_vocab_candidates.py`
- Modify: `apps/worker/src/alinea_worker/tasks/generate_overview_figure.py`
- Modify: `apps/worker/src/alinea_worker/tasks/generate_explainer_figure.py`
- Modify: `apps/worker/src/alinea_worker/tasks/translate.py`
- Test: `apps/worker/tests/test_generate_article.py`
- Test: `apps/worker/tests/test_generate_vocab_ai.py`
- Test: `apps/worker/tests/test_extract_vocab_candidates.py`
- Test: `apps/worker/tests/test_generate_overview_figure.py`
- Test: `apps/worker/tests/test_generate_explainer_figure.py`
- Test: `apps/worker/tests/test_ingest.py`

- [ ] **Step 1: task別ルートの失敗テストをパラメータ化して書く**

`translation`、`retranslation_escalation`、`article`、`vocab`、`overview_figure_dsl`、`explainer_image` を列挙し、Job.user_idに対応するrouterが一度だけ取得されることを検査する。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/worker/tests -k 'user_route' -q`

Expected: 共有router参照でFAIL。

- [ ] **Step 3: handler入口でtask専用routerを解決する**

```python
router = await ctx["user_router_factory"].for_job(
    user_id=job.user_id,
    task="vocab",
)
```

下位関数にはrouterを明示引数で渡し、ctx全体を渡して暗黙に共有routerを読む経路を削除する。
画像taskはImageProviderを同じユーザー／taskルートから解決する。

- [ ] **Step 4: 共有router参照が残っていないことを検査する**

Run: `rg -n 'ctx\["router"\]|ctx\.get\("router"\)' apps/worker/src/alinea_worker`

Expected: 互換shim以外は0件。

- [ ] **Step 5: Workerテストを通す**

Run: `uv run pytest apps/worker/tests -q`

Expected: PASS。

- [ ] **Step 6: コミットする**

```bash
git add apps/worker/src/alinea_worker/pipeline.py apps/worker/src/alinea_worker/tasks/generate_article.py apps/worker/src/alinea_worker/tasks/generate_vocab_ai.py apps/worker/src/alinea_worker/tasks/extract_vocab_candidates.py apps/worker/src/alinea_worker/tasks/generate_overview_figure.py apps/worker/src/alinea_worker/tasks/generate_explainer_figure.py apps/worker/src/alinea_worker/tasks/translate.py apps/worker/tests/test_generate_article.py apps/worker/tests/test_generate_vocab_ai.py apps/worker/tests/test_extract_vocab_candidates.py apps/worker/tests/test_generate_overview_figure.py apps/worker/tests/test_generate_explainer_figure.py apps/worker/tests/test_ingest.py
git commit -m "refactor(worker): apply per-user routing to all LLM tasks"
```

### Task 15: ACL Anthology取り込みをAPIからWorkerまで通す

**Files:**

- Create: `packages/py-core/src/alinea_core/adapters/fetch.py`
- Create: `apps/api/alembic/versions/0014_paper_external_ids.py`
- Modify: `packages/py-core/src/alinea_core/db/models.py`
- Modify: `apps/api/src/alinea_api/routers/ingest.py`
- Modify: `apps/api/src/alinea_api/schemas/ingest.py`
- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Modify: `apps/worker/src/alinea_worker/tasks/export_user_data.py`
- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py`
- Modify: `apps/extension/src/entrypoints/background.ts`
- Test: `apps/api/tests/test_ingest_api.py`
- Create: `apps/worker/tests/test_site_ingest_pipeline.py`
- Modify: `apps/worker/tests/test_export_bulk.py`
- Modify: `apps/worker/tests/test_import_bulk.py`
- Test: `apps/extension/e2e/xt.spec.ts`

- [ ] **Step 1: ACL URLからの縦スライス失敗テストを書く**

ASGI stubでlanding HTMLとPDFを返し、`ingest_check` がsiteを返すこと、`POST /api/ingest/site` がjobを作ること、Workerが品質Bのrevisionを完成させることを検査する。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/api/tests/test_ingest_api.py apps/worker/tests/test_site_ingest_pipeline.py -q`

Expected: site endpoint未実装でFAIL。

- [ ] **Step 3: 境界付きHTTPクライアントとsite endpointを実装する**

```python
class SiteIngestRequest(BaseModel):
    url: AnyHttpUrl

class SiteIngestResponse(BaseModel):
    job_id: str
    library_item_id: str
```

SSRF対策としてadapterが生成した既知hostだけを許可し、redirect後もhostを再検証する。
HTMLとPDFは既存の最大byte数、timeout、Content-Type検証を使う。

- [ ] **Step 4: 外部サイト識別子を正規化して永続化する**

```python
class PaperExternalId(Base):
    __tablename__ = "paper_external_ids"
    id: Mapped[str]
    paper_id: Mapped[str]
    site: Mapped[str]
    external_id: Mapped[str]
    canonical_url: Mapped[str]
```

`(site, external_id)` をuniqueにし、PubMedのPMIDとPMCのPMCIDのように一論文へ複数識別子を保存できるようにする。
完全バックアップにも識別子を含め、インポート時は既存Paperへの名寄せに使う。
DOI、`(site, external_id)`、PDF SHA-256の順に既存Paperを探し、どれにも一致しない場合だけ新規作成する。
互換ライセンスが取れないPaperは `visibility="private"` と `owner_user_id` を設定し、互換ライセンスが明示された場合だけ `public` を許可する。

- [ ] **Step 5: `source="site"` をローカルPDF候補として処理する**

PaperはDOI、なければ `(site, external_id)` で冪等化する。
SourceAssetは既存 `kind="pdf"` を使い、`source_url` にlanding URLを保存する。

- [ ] **Step 6: 拡張の検出と送信を接続する**

ACL URLではPDF uploadではなくsite endpointを選ぶ。
失敗時だけ既存のタブ内PDF送信を案内する。

- [ ] **Step 7: テストを通す**

Run: `uv run pytest apps/api/tests/test_ingest_api.py apps/worker/tests/test_site_ingest_pipeline.py -q`

Run: `uv run pytest apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py -k external_id -q`

Run: `pnpm --filter @alinea/extension test`

Expected: PASS。

- [ ] **Step 8: コミットする**

```bash
git add packages/py-core/src/alinea_core/adapters/fetch.py apps/api/alembic/versions/0014_paper_external_ids.py packages/py-core/src/alinea_core/db/models.py apps/api/src/alinea_api/routers/ingest.py apps/api/src/alinea_api/schemas/ingest.py apps/worker/src/alinea_worker/pipeline.py apps/worker/src/alinea_worker/tasks/export_user_data.py apps/worker/src/alinea_worker/tasks/import_user_data.py apps/extension/src/entrypoints/background.ts apps/api/tests/test_ingest_api.py apps/worker/tests/test_site_ingest_pipeline.py apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py apps/extension/e2e/xt.spec.ts
git commit -m "feat(ingest): integrate ACL Anthology site imports"
```

### Task 16: OpenReviewアダプタと取り込みを実装する

**Files:**

- Create: `packages/py-core/src/alinea_core/adapters/openreview.py`
- Modify: `packages/py-core/src/alinea_core/adapters/registry.py`
- Modify: `packages/py-core/src/alinea_core/adapters/fetch.py`
- Create: `packages/py-core/tests/fixtures/openreview_note.json`
- Modify: `packages/py-core/tests/test_site_adapters.py`
- Modify: `apps/worker/tests/test_site_ingest_pipeline.py`

- [ ] **Step 1: forum URL、note JSON、PDF fallbackの失敗テストを書く**

`forum?id=` と `/pdf?id=` を同一SiteRefへ正規化する。
API2 noteからtitle、authors、abstract、venue、date、licenseを写像し、note不在時はcitation metaへフォールバックする。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest packages/py-core/tests/test_site_adapters.py apps/worker/tests/test_site_ingest_pipeline.py -k openreview -q`

Expected: adapter未登録でFAIL。

- [ ] **Step 3: OpenReviewAdapterを実装する**

```python
class OpenReviewAdapter:
    site = "openreview"
    def match(self, url: str) -> SiteRef | None: ...
    def parse_note(self, payload: Mapping[str, object], ref: SiteRef) -> SiteMeta: ...
    def pdf_url(self, ref: SiteRef) -> str:
        return f"https://openreview.net/pdf?id={quote(ref.external_id)}"
```

公開noteとPDFだけを取得し、403／空noteではタブ内PDF fallbackを返す。

- [ ] **Step 4: テストを通す**

Run: `uv run pytest packages/py-core/tests/test_site_adapters.py apps/worker/tests/test_site_ingest_pipeline.py -k openreview -q`

Expected: PASS。

- [ ] **Step 5: コミットする**

```bash
git add packages/py-core/src/alinea_core/adapters/openreview.py packages/py-core/src/alinea_core/adapters/registry.py packages/py-core/src/alinea_core/adapters/fetch.py packages/py-core/tests/fixtures/openreview_note.json packages/py-core/tests/test_site_adapters.py apps/worker/tests/test_site_ingest_pipeline.py
git commit -m "feat(ingest): add OpenReview adapter"
```

### Task 17: PubMed／PMCアダプタとJATS品質A取り込みを実装する

**Files:**

- Create: `packages/py-core/src/alinea_core/adapters/pubmed.py`
- Create: `packages/py-core/src/alinea_core/parsing/jats.py`
- Create: `packages/py-core/tests/fixtures/pmc_article.xml`
- Modify: `packages/py-core/src/alinea_core/adapters/registry.py`
- Modify: `packages/py-core/src/alinea_core/parsing/source_candidates.py`
- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Create: `apps/api/alembic/versions/0015_jats_source_format.py`
- Modify: `packages/py-core/tests/test_site_adapters.py`
- Create: `packages/py-core/tests/test_jats_parser.py`
- Modify: `apps/worker/tests/test_site_ingest_pipeline.py`

- [ ] **Step 1: PMID／PMCID正規化とJATS変換の失敗テストを書く**

PubMed URLはPMID、PMC URLはPMCIDへ正規化する。
OA記事はJATSからsection、paragraph、figure、table、equation、citationをDocumentContentへ変換し、品質Aになることを検査する。
JATSが無いPubMed記事はabstract metadataだけを保存し、本文取得不可を明示する。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest packages/py-core/tests/test_site_adapters.py packages/py-core/tests/test_jats_parser.py apps/worker/tests/test_site_ingest_pipeline.py -k 'pubmed or pmc or jats' -q`

Expected: parser未実装でFAIL。

- [ ] **Step 3: NCBIクライアントとRedis throttleを実装する**

API keyなしは毎秒3req、ありは毎秒10reqに制限する。
E-utilitiesとPMC OA endpointは設定可能なbase URLを使い、E2Eでは実通信しない。

- [ ] **Step 4: `jats` source candidateを追加する**

`ck_document_revisions_format` とPython Literalへ `jats` を追加する。
候補順はPMCで `jats, pdf`、PubMedで `pdf` が取得できる場合だけPDFを使う。

- [ ] **Step 5: JATSをDocumentContentへ変換する**

未知tagは子テキストへ安全に縮退し、スクリプト、外部entity、DTDを拒否する。
figure assetは境界付きで取得し、失敗時はdeferred placeholderを残す。

- [ ] **Step 6: テストを通す**

Run: `uv run pytest packages/py-core/tests/test_site_adapters.py packages/py-core/tests/test_jats_parser.py apps/worker/tests/test_site_ingest_pipeline.py -q`

Expected: PASS。

- [ ] **Step 7: コミットする**

```bash
git add packages/py-core/src/alinea_core/adapters/pubmed.py packages/py-core/src/alinea_core/parsing/jats.py packages/py-core/tests/fixtures/pmc_article.xml packages/py-core/src/alinea_core/adapters/registry.py packages/py-core/src/alinea_core/parsing/source_candidates.py apps/worker/src/alinea_worker/pipeline.py apps/api/alembic/versions/0015_jats_source_format.py packages/py-core/tests/test_site_adapters.py packages/py-core/tests/test_jats_parser.py apps/worker/tests/test_site_ingest_pipeline.py
git commit -m "feat(ingest): add PubMed and PMC JATS imports"
```

### Task 18: Hugging Faceから論文と関連ソース候補を収集する

**Files:**

- Create: `packages/py-core/src/alinea_core/adapters/huggingface.py`
- Create: `packages/py-core/tests/fixtures/huggingface_paper.json`
- Modify: `packages/py-core/src/alinea_core/adapters/__init__.py`
- Modify: `packages/py-core/src/alinea_core/adapters/registry.py`
- Modify: `packages/py-core/src/alinea_core/adapters/fetch.py`
- Modify: `packages/py-core/tests/test_site_adapters.py`
- Create: `apps/api/alembic/versions/0016_huggingface_resources.py`
- Modify: `apps/api/src/alinea_api/routers/ingest.py`
- Modify: `apps/api/src/alinea_api/routers/resources.py`
- Modify: `apps/api/src/alinea_api/schemas/ingest.py`
- Modify: `apps/api/src/alinea_api/schemas/resources.py`
- Modify: `apps/api/tests/test_ingest_api.py`
- Modify: `apps/api/tests/test_resources.py`
- Modify: `packages/api-client/openapi.json`
- Modify: `packages/api-client/src/generated/`
- Modify: `apps/web/src/components/viewer/ResourcesPanel.tsx`
- Modify: `apps/web/src/components/viewer/ResourcesPanel.test.tsx`
- Modify: `apps/web/src/components/viewer/resources/types.ts`
- Modify: `apps/web/src/components/viewer/resources/ResourceCard.tsx`
- Modify: `apps/web/src/components/viewer/resources/ResourceCard.test.tsx`
- Modify: `apps/web/src/components/viewer/resources/ResourceKindIcon.tsx`
- Modify: `apps/web/src/components/viewer/resources/ResourceSuggestionCard.tsx`
- Modify: `apps/extension/src/entrypoints/background.ts`
- Modify: `apps/extension/e2e/xt.spec.ts`

- [ ] **Step 1: URL正規化と関連ソース変換の失敗テストを書く**

Paper、Model、Dataset、Spaceの有効URLと、org page、collection、settings、resolve URLなどの無効URLをfixture化する。

Paper API fixtureからPaper Page 1件、GitHub 1件、project 1件、Model 5件、Dataset 3件、Space 3件がこの順で生成されることを検査する。

```python
assert parse_huggingface_url("https://huggingface.co/papers/2307.09288") == HuggingFaceRef(
    kind="paper", external_id="2307.09288"
)
assert len(discover_paper_resources(payload)) <= 13
assert {r.relation for r in resources} >= {"github", "project", "model", "dataset", "space"}
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest packages/py-core/tests/test_site_adapters.py -k huggingface -q`

Expected: Hugging Face adapter未実装でFAIL。

- [ ] **Step 3: 純粋なURL parserと候補変換を実装する**

```python
@dataclass(frozen=True)
class HuggingFaceRef:
    kind: Literal["paper", "model", "dataset", "space"]
    external_id: str

@dataclass(frozen=True)
class DiscoveredResource:
    url: str
    kind: Literal["github", "huggingface", "project"]
    relation: str
    title: str
    official_candidate: bool
    meta: dict[str, object]
```

URL parserは `huggingface.co` と短縮host `hf.co` だけを許可する。

候補変換はnormalized URLで重複排除し、paper-levelの `githubRepo` と `projectPage` だけをofficial candidateにする。

- [ ] **Step 4: 公開Hub APIクライアントを追加する**

Paperは `GET /api/papers/{arxiv_id}`、Model／Dataset／Spaceは各repo APIから `arxiv:<ID>` tagを取得する。

httpxは既存の境界付きclientを使い、base URLを設定で上書きできるようにする。

401／403／404／429は既存のprovider error分類へ変換し、rate-limit resetまで再試行しない。

- [ ] **Step 5: Hugging Face URLを既存取り込みへ接続する**

Paper URLはpathのarXiv IDをそのまま既存arXiv pipelineへ渡す。

Model／Dataset／SpaceはAPI tagからarXiv IDが一意に決まる場合だけ既存arXiv pipelineへ渡し、0件または複数件なら選択可能な診断を返す。

取り込みに使ったHugging Face URLはactive Resourceとして登録し、関連リンクはsuggested Resourceとして保存する。

- [ ] **Step 6: 複数候補をResourceLinkへ統一する**

`ck_resource_links_kind` に `huggingface` と `project` を追加する。

`ResourceListResponse` は `suggestions: list[ResourceSuggestion]` を返し、既存 `suggestion` は互換期間だけ先頭候補を返す。

```text
POST /api/resources/{resource_id}/accept-suggestion
POST /api/resources/{resource_id}/dismiss-suggestion
```

候補のacceptはstatusをactiveへ更新し、GitHub／projectのofficial candidateだけ `official=true` にする。

dismissはstatusをdismissedへ更新し、再同期時も同じnormalized URLを復活させない。

- [ ] **Step 7: OpenAPIとSDKを再生成する**

Run: `cd apps/api && uv run python -m alinea_api.export_openapi > ../../packages/api-client/openapi.json`

Run: `pnpm --filter @alinea/api-client generate`

Expected: Hugging Face resource kind、複数suggestions、ID指定accept／dismissが生成される。

- [ ] **Step 8: Resources UIと拡張を接続する**

ResourcesPanelは候補を最大13件まで折り畳み表示し、各候補を独立して採用／却下できるようにする。

Hugging FaceカードはPaper／Model／Dataset／Space、repo ID、downloads、likesを表示し、projectカードは公式候補の根拠を表示する。

拡張はHugging Face URLをsite ingestとして判定し、対象repoにarXiv tagが無い場合は「関連論文が見つかりません」と表示する。

- [ ] **Step 9: API、Web、拡張テストを通す**

Run: `uv run pytest packages/py-core/tests/test_site_adapters.py apps/api/tests/test_ingest_api.py apps/api/tests/test_resources.py -k 'huggingface or suggestions' -q`

Run: `pnpm --filter @alinea/web test -- ResourcesPanel.test.tsx ResourceCard.test.tsx`

Run: `pnpm --filter @alinea/extension test`

Expected: PASS、実Hugging Face通信0件。

- [ ] **Step 10: コミットする**

```bash
git add packages/py-core/src/alinea_core/adapters/huggingface.py packages/py-core/tests/fixtures/huggingface_paper.json packages/py-core/src/alinea_core/adapters/__init__.py packages/py-core/src/alinea_core/adapters/registry.py packages/py-core/src/alinea_core/adapters/fetch.py packages/py-core/tests/test_site_adapters.py apps/api/alembic/versions/0016_huggingface_resources.py apps/api/src/alinea_api/routers/ingest.py apps/api/src/alinea_api/routers/resources.py apps/api/src/alinea_api/schemas/ingest.py apps/api/src/alinea_api/schemas/resources.py apps/api/tests/test_ingest_api.py apps/api/tests/test_resources.py packages/api-client/openapi.json packages/api-client/src/generated apps/web/src/components/viewer/ResourcesPanel.tsx apps/web/src/components/viewer/ResourcesPanel.test.tsx apps/web/src/components/viewer/resources/types.ts apps/web/src/components/viewer/resources/ResourceCard.tsx apps/web/src/components/viewer/resources/ResourceCard.test.tsx apps/web/src/components/viewer/resources/ResourceKindIcon.tsx apps/web/src/components/viewer/resources/ResourceSuggestionCard.tsx apps/extension/src/entrypoints/background.ts apps/extension/e2e/xt.spec.ts
git commit -m "feat(resources): discover Hugging Face paper artifacts"
```

### Task 19: pgvector基盤、埋め込みプロバイダ、インデクシングジョブを実装する

**Files:**

- Create: `docker/db/Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `docker/db/init.sql`
- Create: `apps/api/alembic/versions/0017_semantic_embeddings.py`
- Create: `packages/llm/src/alinea_llm/providers/openai_embeddings.py`
- Modify: `packages/llm/models.yaml`
- Modify: `packages/llm/routing.yaml`
- Modify: `packages/llm/tests/test_embeddings.py`
- Create: `apps/worker/src/alinea_worker/tasks/index_embeddings.py`
- Modify: `apps/worker/src/alinea_worker/tasks/__init__.py`
- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py`
- Create: `apps/worker/tests/test_index_embeddings.py`
- Modify: `apps/worker/tests/test_import_bulk.py`

- [ ] **Step 1: DBとproviderの失敗テストを書く**

`vector` extension、1536次元列、HNSW index、モデル／source_hash不一致時の再計算、同一hashのskip、ユーザー別BYOKを検査する。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest packages/llm/tests/test_embeddings.py apps/worker/tests/test_index_embeddings.py -q`

Expected: 実providerとテーブルがなくFAIL。

- [ ] **Step 3: PGroongaベースへpgvectorを追加する**

PGroongaの既存拡張を保持したDocker imageをbuildし、init.sqlとAlembicの双方で `CREATE EXTENSION IF NOT EXISTS vector` を行う。
`paper_embeddings` と `block_embeddings` はmodel、dim、source_hash、updated_atを持つ。

- [ ] **Step 4: OpenAIEmbeddingProviderを実装する**

```python
class OpenAIEmbeddingProvider:
    name = "openai"
    async def embed(self, req: EmbeddingRequest) -> EmbeddingResult:
        response = await self.client.embeddings.create(
            model="text-embedding-3-small", input=req.texts, dimensions=1536,
        )
        return EmbeddingResult(vectors=[row.embedding for row in response.data], model=response.model)
```

入力件数、応答順、次元、有限値を検証し、不正応答は保存しない。

- [ ] **Step 5: paper／blockのupsertジョブを実装する**

paperはtitle＋abstract、blockはsource_textを埋め込む。
共有revisionは一度だけ保存し、モデルまたはsource_hash一致時はskipする。
既存データ用のbatch backfill payloadにcursorとlimitを持たせる。
完全バックアップには派生データであるembeddingを含めず、インポート完了後にfeature flagが有効なら復元したrevisionのindex jobをenqueueする。

- [ ] **Step 6: DB統合テストを通す**

Run: `docker compose build db && docker compose up -d db`

Run: `(cd apps/api && uv run alembic upgrade head)`

Run: `uv run pytest packages/llm/tests/test_embeddings.py apps/worker/tests/test_index_embeddings.py -q`

Expected: PASS。

- [ ] **Step 7: コミットする**

```bash
git add docker/db/Dockerfile docker-compose.yml docker/db/init.sql apps/api/alembic/versions/0017_semantic_embeddings.py packages/llm/src/alinea_llm/providers/openai_embeddings.py packages/llm/models.yaml packages/llm/routing.yaml packages/llm/tests/test_embeddings.py apps/worker/src/alinea_worker/tasks/index_embeddings.py apps/worker/src/alinea_worker/tasks/__init__.py apps/worker/src/alinea_worker/tasks/import_user_data.py apps/worker/tests/test_index_embeddings.py apps/worker/tests/test_import_bulk.py
git commit -m "feat(search): add pgvector embedding index"
```

### Task 20: ハイブリッド検索と「似た論文」を製品導線へ接続する

**Files:**

- Modify: `apps/api/src/alinea_api/routers/search.py`
- Modify: `apps/api/src/alinea_api/schemas/search.py`
- Create: `apps/api/tests/test_semantic_search.py`
- Modify: `apps/web/src/components/search/SearchResults.tsx`
- Create: `apps/web/src/components/viewer/SimilarPapers.tsx`
- Modify: `apps/web/src/components/viewer/InfoPanel.tsx`
- Test: `apps/web/src/components/search/SearchResults.test.tsx`
- Create: `apps/web/src/components/viewer/SimilarPapers.test.tsx`

- [ ] **Step 1: ユーザー境界と縮退の失敗テストを書く**

別ユーザーの近傍が返らないこと、provider失敗／空indexではPGroonga結果だけが同じ順序で返ること、RRF融合が決定的であることを検査する。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/api/tests/test_semantic_search.py -q`

Expected: semantic経路未接続でFAIL。

- [ ] **Step 3: `/api/search` へANNとRRFを接続する**

feature flag offは現在のSQLと返却順を変えない。
onではquery embedding、ユーザーのlibrary_itemsで絞ったANN、lexical／semantic各100件、RRF k=60、weight 1:1を使う。

- [ ] **Step 4: `/api/library-items/{id}/similar` を追加する**

自分自身を除外し、自分のライブラリ内だけから上位10件を返す。
embeddingが無い対象は202でindex jobをenqueueせず、空配列と `indexing=false` を返す。

- [ ] **Step 5: 検索結果と情報パネルへ表示する**

検索結果は一致種別に「全文」「意味」「両方」を表示する。
情報パネルは似た論文のtitle、authors、類似度、ライブラリへのリンクを表示する。

- [ ] **Step 6: APIとWebテストを通す**

Run: `uv run pytest apps/api/tests/test_search_api.py apps/api/tests/test_semantic_search.py -q`

Run: `pnpm --filter @alinea/web test -- SearchResults.test.tsx SimilarPapers.test.tsx InfoPanel.test.tsx`

Expected: PASS。

- [ ] **Step 7: コミットする**

```bash
git add apps/api/src/alinea_api/routers/search.py apps/api/src/alinea_api/schemas/search.py apps/api/tests/test_semantic_search.py apps/web/src/components/search/SearchResults.tsx apps/web/src/components/viewer/SimilarPapers.tsx apps/web/src/components/viewer/InfoPanel.tsx apps/web/src/components/search/SearchResults.test.tsx apps/web/src/components/viewer/SimilarPapers.test.tsx
git commit -m "feat(search): enable hybrid semantic discovery"
```

### Task 21: GitHubコード対応解析の安全なバックエンドを実装する

**Files:**

- Create: `apps/api/alembic/versions/0018_code_analysis.py`
- Modify: `packages/py-core/src/alinea_core/db/models.py`
- Create: `packages/py-core/src/alinea_core/code_analysis/__init__.py`
- Create: `packages/py-core/src/alinea_core/code_analysis/archive.py`
- Create: `packages/py-core/src/alinea_core/code_analysis/chunks.py`
- Create: `packages/py-core/src/alinea_core/code_analysis/contracts.py`
- Create: `packages/py-core/tests/test_code_analysis.py`
- Create: `apps/api/src/alinea_api/routers/code_analysis.py`
- Create: `apps/api/src/alinea_api/schemas/code_analysis.py`
- Modify: `apps/api/src/alinea_api/main.py`
- Modify: `apps/api/src/alinea_api/routers/resources.py`
- Modify: `apps/api/src/alinea_api/schemas/settings.py`
- Modify: `apps/api/src/alinea_api/routers/settings.py`
- Modify: `apps/api/src/alinea_api/llm/deps.py`
- Modify: `packages/llm/models.yaml`
- Modify: `packages/llm/routing.yaml`
- Create: `apps/worker/src/alinea_worker/github_archive.py`
- Create: `apps/worker/src/alinea_worker/tasks/analyze_code.py`
- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Modify: `apps/worker/src/alinea_worker/tasks/__init__.py`
- Modify: `apps/worker/src/alinea_worker/tasks/export_user_data.py`
- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py`
- Create: `apps/api/tests/test_code_analysis.py`
- Modify: `apps/api/tests/test_resources.py`
- Create: `apps/worker/tests/test_analyze_code.py`
- Modify: `apps/worker/tests/test_ingest.py`
- Modify: `apps/worker/tests/test_export_bulk.py`
- Modify: `apps/worker/tests/test_import_bulk.py`

- [ ] **Step 1: 設定、権限、費用、冪等性の失敗テストを書く**

`off`、`on_demand`、`automatic` と月額予算0.00〜100.00 USDの値域を検査する。

他ユーザーのResource、suggested／dismissed Resource、非GitHub Resource、private／404 repositoryを拒否する。

同じ `(user, revision, resource, commit, analysis_version)` の成功結果を再利用し、queued／running jobを重複作成しないことを検査する。

- [ ] **Step 2: archive境界とprompt injection耐性の失敗テストを書く**

path traversal、絶対path、symlink、hardlink、device、展開後300 MiB超過、対象コード10 MiB超過、2,000 files超過、1 file 512 KiB超過をfixtureで拒否する。

`.env`、private key、certificate、credential、binary、weight、dataset、minified、generated、vendorをLLM入力から除外する。

コードコメントに「前の指示を無視せよ」が含まれてもstructured outputの検証規則が変わらないことを検査する。

- [ ] **Step 3: 失敗を確認する**

Run: `uv run pytest packages/py-core/tests/test_code_analysis.py apps/api/tests/test_code_analysis.py apps/worker/tests/test_analyze_code.py -q`

Expected: model、router、handlerが存在せずFAIL。

- [ ] **Step 4: DBモデル、job kind、設定、LLM routeを追加する**

`code_analysis_estimates`、`code_analysis_runs`、`code_correspondences` を追加する。

```python
class CodeAnalysisSettings(_Strict):
    mode: Literal["off", "on_demand", "automatic"] = "on_demand"
    monthly_budget_usd: Decimal = Decimal("5.00")

class CodeAnalysisRun(Base):
    __tablename__ = "code_analysis_runs"
    id: Mapped[str]
    user_id: Mapped[str]
    library_item_id: Mapped[str]
    resource_id: Mapped[str]
    revision_id: Mapped[str]
    commit_sha: Mapped[str]
    trigger: Mapped[str]
    status: Mapped[str]
    estimated_cost_usd: Mapped[Decimal]
    actual_cost_usd: Mapped[Decimal]
```

`ck_jobs_kind` に `code_analysis`、`ck_jobs_status` に `waiting_budget` を追加する。

`llm_routing.code_analysis` とtask route `code_analysis` を追加し、既定モデルを `claude-sonnet-5` とする。

- [ ] **Step 5: 費用見積もりAPIを実装する**

`POST /api/library-items/{item_id}/code-analysis/estimate` はGitHub repo metadataとrecursive treeからdefault branch commit、対象file数、byte数を求める。

対象論文block量、chunk上限、選択モデルのpricingから入力／出力／embedding tokenと費用を保守的に見積もり、10分有効のestimateを保存する。

```python
class CodeAnalysisEstimateResponse(BaseModel):
    estimate_id: str
    commit_sha: str
    files: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_embedding_tokens: int
    estimated_cost_usd: Decimal
    budget_remaining_usd: Decimal
    expires_at: str
```

recursive treeがtruncatedなら見積もりを拒否し、大規模repositoryとして表示する。

- [ ] **Step 6: 開始APIとautomatic triggerを実装する**

`POST /api/library-items/{item_id}/code-analysis` はestimateの所有者、有効期限、commit、設定、残予算を再検証してjobをenqueueする。

automaticはready revisionと、高信頼の公式GitHub候補またはactive GitHub Resourceが揃った時点だけ内部見積もりを作成する。

公式根拠を持たないsuggested Resourceとdismissed Resourceは対象にしない。

Resourceの手動追加／候補accept時にrevisionがreadyならResources APIがenqueueする。

高信頼の公式候補またはactive Resourceが先に存在する場合は、ingest pipelineのreadable遷移がenqueueする。

予算不足ならLLM／embeddingを呼ばず `waiting_budget` jobと通知を作る。

automaticへ切り替えた時点で既存論文を一括処理せず、別のbackfill確認APIへ分離する。

- [ ] **Step 7: 固定commit archiveを安全に取得してchunk化する**

GitHub archive endpointへcommit SHAを指定し、圧縮100 MiB、展開300 MiBをstreamingで検査する。

repository内のfileは実行せず、許可拡張子だけをsymbol境界または最大200行でchunk化する。

一時archiveと展開directoryは成功、失敗、cancelの全経路で削除する。

- [ ] **Step 8: 検索とLLM検証を実装する**

論文から最大30件のclaimをblock anchor付きで抽出する。

識別子、希少語、数式名のlexical retrieval後にTask 19のEmbeddingProviderで再順位付けし、各claimの上位chunkだけをLLMへ渡す。

LLMはpath、symbol、start／end line、excerpt、説明、confidenceをstructured outputで返す。

サーバーはpaper anchor、path、line、excerptの一致を元データで検証し、一つでも一致しない対応を保存しない。

- [ ] **Step 9: 使用量、stale判定、バックアップを実装する**

`usage_records.task="code_analysis"` の `cost_usd` をBYOK／operatorの両方で月次集計する。

revisionまたはdefault branch commitが変わった結果は削除せずstaleにする。

runとcorrespondenceは完全バックアップへ含め、復元後も固定commit URLを再利用する。

- [ ] **Step 10: バックエンドテストを通す**

Run: `uv run pytest packages/py-core/tests/test_code_analysis.py apps/api/tests/test_code_analysis.py apps/worker/tests/test_analyze_code.py -q`

Run: `uv run pytest apps/api/tests/test_resources.py apps/worker/tests/test_ingest.py -k code_analysis -q`

Run: `uv run pytest apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py -k code_analysis -q`

Expected: PASS、GitHub／embedding／LLM実通信0件。

- [ ] **Step 11: コミットする**

```bash
git add apps/api/alembic/versions/0018_code_analysis.py packages/py-core/src/alinea_core/db/models.py packages/py-core/src/alinea_core/code_analysis packages/py-core/tests/test_code_analysis.py apps/api/src/alinea_api/routers/code_analysis.py apps/api/src/alinea_api/schemas/code_analysis.py apps/api/src/alinea_api/main.py apps/api/src/alinea_api/routers/resources.py apps/api/src/alinea_api/schemas/settings.py apps/api/src/alinea_api/routers/settings.py apps/api/src/alinea_api/llm/deps.py packages/llm/models.yaml packages/llm/routing.yaml apps/worker/src/alinea_worker/github_archive.py apps/worker/src/alinea_worker/tasks/analyze_code.py apps/worker/src/alinea_worker/pipeline.py apps/worker/src/alinea_worker/tasks/__init__.py apps/worker/src/alinea_worker/tasks/export_user_data.py apps/worker/src/alinea_worker/tasks/import_user_data.py apps/api/tests/test_code_analysis.py apps/api/tests/test_resources.py apps/worker/tests/test_analyze_code.py apps/worker/tests/test_ingest.py apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py
git commit -m "feat(code-analysis): map paper claims to verified code lines"
```

### Task 22: GitHubコード対応解析の設定と結果UIを実装する

**Files:**

- Modify: `packages/api-client/openapi.json`
- Modify: `packages/api-client/src/generated/`
- Modify: `apps/web/src/components/settings/types.ts`
- Modify: `apps/web/src/components/settings/SettingsClient.tsx`
- Modify: `apps/web/src/components/settings/SettingsClient.test.tsx`
- Modify: `apps/web/src/components/settings/AccountSettings.tsx`
- Create: `apps/web/src/components/settings/CodeAnalysisSettings.tsx`
- Create: `apps/web/src/components/settings/CodeAnalysisSettings.test.tsx`
- Modify: `apps/web/src/components/viewer/ResourcesPanel.tsx`
- Create: `apps/web/src/components/viewer/resources/CodeAnalysisEstimateModal.tsx`
- Create: `apps/web/src/components/viewer/resources/CodeCorrespondencePanel.tsx`
- Create: `apps/web/src/components/viewer/resources/CodeCorrespondencePanel.test.tsx`
- Modify: `apps/web/src/components/viewer/resources/ResourceCard.tsx`
- Create: `apps/web/e2e/specs/pw-code-analysis.spec.ts`

- [ ] **Step 1: 三モードと費用表示の失敗テストを書く**

offでは既存結果を表示しつつ新規解析ボタンをdisabledにする。

on_demandではボタン、見積もり、commit、file数、token、概算費用、予算残額、確認操作を表示する。

automaticでは高信頼の公式GitHub候補とすべてのactive GitHubカードに自動解析対象を表示し、根拠の弱いsuggested／dismissedカードでは解析を開始しない。

- [ ] **Step 2: 対応結果の失敗テストを書く**

paper block link、固定commitのGitHub `#Lx-Ly` link、symbol、説明、high／medium／low、stale、対応0件、failed、waiting_budgetを検査する。

- [ ] **Step 3: 失敗を確認する**

Run: `pnpm --filter @alinea/web test -- CodeAnalysisSettings.test.tsx CodeCorrespondencePanel.test.tsx`

Expected: component未実装でFAIL。

- [ ] **Step 4: OpenAPIとSDKを再生成する**

Run: `cd apps/api && uv run python -m alinea_api.export_openapi > ../../packages/api-client/openapi.json`

Run: `pnpm --filter @alinea/api-client generate`

Expected: estimate、start、list、rerun、settings型が生成される。

- [ ] **Step 5: アカウント設定へ解析モードと月額予算を追加する**

モデルルーティング付近へ `CodeAnalysisSettings` を置く。

modeは「使用しない」「必要なときだけ」「取り込み後に自動」の三択とし、automatic選択時は費用と対象範囲を説明する。

月額予算は0.00〜100.00 USD、0.50 USD刻みとし、現在のcode_analysis費用も表示する。

既存論文を対象にする操作は件数と総見積もりを取得した後の別確認にする。

- [ ] **Step 6: GitHub Resourceカードへ解析導線を追加する**

見積もり前、見積もり中、確認、queued、running、waiting_budget、failed、complete、staleを一つの状態機械で表示する。

estimateが失効またはcommitが変化した409では確認を閉じ、再見積もりを促す。

- [ ] **Step 7: 対応結果パネルを実装する**

highとmediumを通常表示し、lowは「関連候補」に折り畳む。

paper anchorは現在のビューアblockへ移動し、GitHub anchorは固定commitと行範囲を新規タブで開く。

結果0件は「コードが無い」ではなく「対応箇所を特定できませんでした」と表示する。

- [ ] **Step 8: UnitとE2Eを通す**

Run: `pnpm --filter @alinea/web test -- CodeAnalysisSettings.test.tsx CodeCorrespondencePanel.test.tsx SettingsClient.test.tsx ResourcesPanel.test.tsx`

Run: `pnpm --filter @alinea/web e2e -- pw-code-analysis.spec.ts`

Expected: PASS、E2Eは保存tar fixtureとFake providerだけを使う。

- [ ] **Step 9: コミットする**

```bash
git add packages/api-client/openapi.json packages/api-client/src/generated apps/web/src/components/settings/types.ts apps/web/src/components/settings/SettingsClient.tsx apps/web/src/components/settings/SettingsClient.test.tsx apps/web/src/components/settings/AccountSettings.tsx apps/web/src/components/settings/CodeAnalysisSettings.tsx apps/web/src/components/settings/CodeAnalysisSettings.test.tsx apps/web/src/components/viewer/ResourcesPanel.tsx apps/web/src/components/viewer/resources/CodeAnalysisEstimateModal.tsx apps/web/src/components/viewer/resources/CodeCorrespondencePanel.tsx apps/web/src/components/viewer/resources/CodeCorrespondencePanel.test.tsx apps/web/src/components/viewer/resources/ResourceCard.tsx apps/web/e2e/specs/pw-code-analysis.spec.ts
git commit -m "feat(web): control and inspect GitHub code analysis"
```

### Task 23: 直近10論文のオフライン閲覧を実装する

**Files:**

- Create: `apps/web/src/lib/offline-viewer.ts`
- Create: `apps/web/src/lib/offline-viewer.test.ts`
- Modify: `apps/web/public/sw.js`
- Modify: `apps/web/src/components/pwa/ServiceWorkerRegistration.tsx`
- Modify: `apps/web/src/components/viewer/ViewerShell.tsx`
- Modify: `apps/web/src/components/settings/AccountSettings.tsx`
- Create: `apps/web/src/app/offline/page.tsx`
- Test: `apps/web/e2e/specs/pw-offline-viewer.spec.ts`

- [ ] **Step 1: SWの認証安全性とLRUの失敗テストを書く**

対象GETはviewer init、document、translation units、figures、references、assetsに限定する。
fetchがthrowした場合だけcacheを返し、401／403／404／500はそのまま返す。
11件目で最古論文を消し、1論文50 MiBまたは全体200 MiBを超えた論文を完全にevictする。
ログアウトまたは別ユーザーへの切替後に前ユーザーのキャッシュを返さないことも検査する。

- [ ] **Step 2: 失敗を確認する**

Run: `pnpm --filter @alinea/web test -- offline-viewer.test.ts register-sw.test.ts`

Expected: API bypassのためFAIL。

- [ ] **Step 3: paper単位のキャッシュmanifestを実装する**

```ts
type OfflinePaperManifest = {
  userId: string;
  itemId: string;
  revisionId: string;
  urls: string[];
  bytes: number;
  lastAccessedAt: number;
};
```

ViewerShellはオンライン表示完了後に `CACHE_PAPER` messageをSWへ送り、SWが応答と関連assetを同一paper groupへ記録する。
ServiceWorkerRegistrationは認証済みuser IDを `SET_ACTIVE_USER` で通知し、user IDが変わったら旧ユーザーの応答を選択対象から外す。
明示ログアウトとアカウント削除は `PURGE_USER` の完了を待ってからログイン画面へ遷移する。

- [ ] **Step 4: network-firstとnavigation fallbackを実装する**

APIはnetwork-first、例外時のみcache fallbackにする。
`/papers/{itemId}` navigationのネットワーク失敗時はoffline shellを返し、clientがcached viewer dataを読む。
キャッシュが無い場合は保存済み論文一覧と再接続案内を表示する。

- [ ] **Step 5: ブラウザE2Eを通す**

Playwrightで一度論文を開き、contextをofflineにして同じURLを再読込する。
本文と訳文が表示され、未訪問論文はoffline案内になり、オンライン401はloginへ進むことを検査する。

Run: `pnpm --filter @alinea/web e2e -- pw-offline-viewer.spec.ts`

Expected: PASS。

- [ ] **Step 6: コミットする**

```bash
git add apps/web/src/lib/offline-viewer.ts apps/web/src/lib/offline-viewer.test.ts apps/web/public/sw.js apps/web/src/components/pwa/ServiceWorkerRegistration.tsx apps/web/src/components/viewer/ViewerShell.tsx apps/web/src/components/settings/AccountSettings.tsx apps/web/src/app/offline/page.tsx apps/web/e2e/specs/pw-offline-viewer.spec.ts
git commit -m "feat(pwa): cache recently viewed papers offline"
```

### Task 24: 記事公開のデータモデルと公開APIを実装する

**Files:**

- Create: `apps/api/alembic/versions/0019_article_publications.py`
- Modify: `packages/py-core/src/alinea_core/db/models.py`
- Create: `apps/api/src/alinea_api/routers/publications.py`
- Create: `apps/api/src/alinea_api/schemas/publications.py`
- Modify: `apps/api/src/alinea_api/main.py`
- Modify: `apps/worker/src/alinea_worker/tasks/export_user_data.py`
- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py`
- Create: `apps/api/tests/test_publications.py`
- Modify: `apps/worker/tests/test_export_bulk.py`
- Modify: `apps/worker/tests/test_import_bulk.py`

- [ ] **Step 1: 可視性、スナップショット、情報漏えいの失敗テストを書く**

private／unlisted／public、slug重複、所有権、公開解除、再公開、private paper拒否を検査する。
スナップショットにsource quote、translation、notes、chat、discussionが含まれないことを検査する。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/api/tests/test_publications.py -q`

Expected: tableとrouterがなくFAIL。

- [ ] **Step 3: Publicationモデルを追加する**

```python
class ArticlePublication(Base):
    __tablename__ = "article_publications"
    id: Mapped[str]
    article_id: Mapped[str]
    user_id: Mapped[str]
    slug: Mapped[str]
    visibility: Mapped[str]  # unlisted | public
    snapshot_version: Mapped[int]
    title: Mapped[str]
    paper_meta: Mapped[dict]
    blocks: Mapped[list[dict]]
    published_at: Mapped[datetime]
    updated_at: Mapped[datetime]
```

`article_id` と `slug` はuniqueにし、公開解除後もslugを予約してリンクの乗っ取りを防ぐ。

- [ ] **Step 4: snapshot sanitizerを実装する**

heading、paragraph、attribution、ライセンス確認済みのoverview／explainerだけを許可する。
evidenceはpaper titleとsection labelだけを残し、quote本文、block原文、訳文を除去する。

- [ ] **Step 5: APIを実装する**

所有者用create／update／unpublishと、認証不要のslug readを分ける。
unlistedはURLを知る利用者だけが読め、publicだけを検索エンジンindex許可する。

- [ ] **Step 6: APIテストを通す**

Run: `uv run pytest apps/api/tests/test_publications.py -q`

Run: `uv run pytest apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py -k publication -q`

Expected: APIと移行テストがPASS。

Publicationはバックアップへ含めるが、インポート時のvisibilityは必ずunlistedへ落とし、移行操作だけで公開URLを再公開しない。

- [ ] **Step 7: コミットする**

```bash
git add apps/api/alembic/versions/0019_article_publications.py packages/py-core/src/alinea_core/db/models.py apps/api/src/alinea_api/routers/publications.py apps/api/src/alinea_api/schemas/publications.py apps/api/src/alinea_api/main.py apps/worker/src/alinea_worker/tasks/export_user_data.py apps/worker/src/alinea_worker/tasks/import_user_data.py apps/api/tests/test_publications.py apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py
git commit -m "feat(publication): publish sanitized article snapshots"
```

### Task 25: 公開記事コメントとモデレーションAPIを実装する

**Files:**

- Create: `apps/api/alembic/versions/0020_publication_comments.py`
- Modify: `packages/py-core/src/alinea_core/db/models.py`
- Modify: `apps/api/src/alinea_api/routers/publications.py`
- Modify: `apps/api/src/alinea_api/schemas/publications.py`
- Modify: `apps/worker/src/alinea_worker/tasks/export_user_data.py`
- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py`
- Create: `apps/api/tests/test_publication_comments.py`
- Modify: `apps/worker/tests/test_export_bulk.py`
- Modify: `apps/worker/tests/test_import_bulk.py`

- [ ] **Step 1: コメント権限の失敗テストを書く**

閲覧は匿名可、投稿は認証必須、編集／削除は投稿者、hide／restoreは記事公開者に限定する。
公開スナップショットに存在するblock IDだけへコメントでき、本文は1〜4000文字、parentは同じpublicationの一階層だけに制限する。

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/api/tests/test_publication_comments.py -q`

Expected: comment tableがなくFAIL。

- [ ] **Step 3: CommentモデルとAPIを追加する**

```python
class PublicationComment(Base):
    __tablename__ = "publication_comments"
    id: Mapped[str]
    publication_id: Mapped[str]
    user_id: Mapped[str]
    parent_id: Mapped[str | None]
    block_id: Mapped[str]
    body: Mapped[str]
    status: Mapped[str]  # visible | hidden | deleted
```

削除はsoft deleteし、返信がある場合もスレッド構造を保つ。
HTMLは保存せずplain textだけを受ける。
投稿はRedisでユーザーごとに毎分10件へ制限する。
バックアップには復元対象publicationに対して本人が投稿したコメントだけを含め、第三者のコメントを本人所有データとして複製しない。

- [ ] **Step 4: APIテストを通す**

Run: `uv run pytest apps/api/tests/test_publication_comments.py -q`

Run: `uv run pytest apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py -k publication_comment -q`

Expected: APIと移行テストがPASS。

- [ ] **Step 5: コミットする**

```bash
git add apps/api/alembic/versions/0020_publication_comments.py packages/py-core/src/alinea_core/db/models.py apps/api/src/alinea_api/routers/publications.py apps/api/src/alinea_api/schemas/publications.py apps/worker/src/alinea_worker/tasks/export_user_data.py apps/worker/src/alinea_worker/tasks/import_user_data.py apps/api/tests/test_publication_comments.py apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py
git commit -m "feat(publication): add moderated article comments"
```

### Task 26: 記事公開とコメントのWeb UIを実装する

**Files:**

- Create: `apps/web/src/app/(public)/a/[slug]/page.tsx`
- Create: `apps/web/src/components/publication/PublishedArticle.tsx`
- Create: `apps/web/src/components/publication/CommentThread.tsx`
- Create: `apps/web/src/components/publication/PublishArticleModal.tsx`
- Modify: `apps/web/src/components/viewer/article/ArticlePane.tsx`
- Create: `apps/web/src/components/publication/PublishedArticle.test.tsx`
- Create: `apps/web/src/components/publication/CommentThread.test.tsx`
- Create: `apps/web/e2e/specs/pw-article-publication.spec.ts`

- [ ] **Step 1: 公開UIの失敗テストを書く**

記事所有者には公開／限定公開／公開解除を表示する。
公開ページには記事、書誌、公開者、更新日時、コメントを表示し、匿名利用者にはログインCTAだけを表示する。

- [ ] **Step 2: OpenAPIとSDKを再生成する**

Run: `cd apps/api && uv run python -m alinea_api.export_openapi > ../../packages/api-client/openapi.json`

Run: `pnpm --filter @alinea/api-client generate`

Expected: publicationsとcommentsのSDK関数が生成される。

- [ ] **Step 3: 公開モーダルと公開ページを実装する**

公開前に除外されるブロックとライセンス判定を説明する。
unlistedには `robots: noindex, nofollow`、publicにはcanonicalとOG metadataを設定する。

- [ ] **Step 4: コメントUIを実装する**

blockごとにthreadを表示し、投稿、返信、編集、削除、公開者のhide／restoreを生成SDK経由で行う。
mutation成功後は該当publication comment queryだけをinvalidateする。

- [ ] **Step 5: UnitとE2Eを通す**

Run: `pnpm --filter @alinea/web test -- PublishedArticle.test.tsx CommentThread.test.tsx`

Run: `pnpm --filter @alinea/web e2e -- pw-article-publication.spec.ts`

Expected: PASS。

- [ ] **Step 6: コミットする**

```bash
git add packages/api-client/openapi.json packages/api-client/src/generated apps/web/src/app/'(public)'/a/'[slug]'/page.tsx apps/web/src/components/publication/PublishedArticle.tsx apps/web/src/components/publication/CommentThread.tsx apps/web/src/components/publication/PublishArticleModal.tsx apps/web/src/components/publication/PublishedArticle.test.tsx apps/web/src/components/publication/CommentThread.test.tsx apps/web/src/components/viewer/article/ArticlePane.tsx apps/web/e2e/specs/pw-article-publication.spec.ts
git commit -m "feat(web): publish articles with comments"
```

### Task 27: ppt-masterを固定commitで導入し、更新と変換を再現可能にする

**Files:**

- Create: `.gitmodules`
- Create: `vendor/ppt-master`
- Modify: `.gitignore`
- Modify: `package.json`
- Create: `scripts/update-ppt-master.sh`
- Create: `scripts/verify-ppt-master.py`
- Create: `apps/worker/src/alinea_worker/presentation/__init__.py`
- Create: `apps/worker/src/alinea_worker/presentation/ppt_master.py`
- Create: `apps/worker/tests/fixtures/presentation/minimal_project/`
- Create: `apps/worker/tests/test_ppt_master_adapter.py`

- [ ] **Step 1: 上流コマンド境界の失敗テストを書く**

adapterが `vendor/ppt-master/skills/ppt-master/scripts` 以外を実行しないこと、`shell=False`、ジョブ固有の作業directory、許可リスト環境変数、commandごとのtimeoutを使うことを検査する。
品質検査が失敗した場合は `total_md_split.py`、`finalize_svg.py`、`svg_to_pptx.py` を実行しないことも検査する。

```python
assert calls == [
    ("project_manager.py", "init"),
    ("svg_quality_checker.py", project_dir),
    ("total_md_split.py", project_dir),
    ("finalize_svg.py", project_dir),
    ("svg_to_pptx.py", project_dir, "--merge-paragraphs"),
]
assert "OPENAI_API_KEY" not in captured_env
assert "ANTHROPIC_API_KEY" not in captured_env
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/worker/tests/test_ppt_master_adapter.py -q`

Expected: adapterとsubmoduleが存在しないためFAIL。

- [ ] **Step 3: ppt-master v2.8.0をsubmoduleとして固定する**

Run: `git submodule add https://github.com/hugohe3/ppt-master.git vendor/ppt-master`

Run: `git -C vendor/ppt-master checkout 0c0bdaf0dd953afc2c00322e92f26dc02fc1c51f`

Run: `test "$(git -C vendor/ppt-master rev-parse HEAD)" = "0c0bdaf0dd953afc2c00322e92f26dc02fc1c51f"`

Expected: 三コマンドともexit 0。

`.gitignore` には `.venv-ppt-master/` と一時生成directoryだけを追加し、submodule内の追跡ファイルを除外しない。

- [ ] **Step 4: subprocess adapterを実装する**

`PptMasterAdapter` は専用仮想環境のPythonを使い、上流スクリプトを一つずつ実行する。
標準出力と標準エラーは各64 KiBで打ち切り、APIキーとsource packet本文をログへ残さない。

```python
PPT_MASTER_REVISION = "0c0bdaf0dd953afc2c00322e92f26dc02fc1c51f"
SCRIPT_ORDER = (
    "svg_quality_checker.py",
    "total_md_split.py",
    "finalize_svg.py",
    "svg_to_pptx.py",
)
ALLOWED_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONIOENCODING")
```

変換後はPPTXをZIPとして開き、`[Content_Types].xml`、`ppt/presentation.xml`、一枚以上の `ppt/slides/slide*.xml` が存在することを検証する。
外部画像relationship、壊れたZIP、0 byte成果物は拒否する。

- [ ] **Step 5: 上流更新コマンドを実装する**

`pnpm ppt-master:update [revision]` は現在のcommitを記録し、指定したtagまたはcommitを検証する。
引数がない場合は上流 `main` の現在commitを更新候補として検証するが、成功後も差分を残すだけでcommitや本番反映を自動実行しない。
検証失敗時はsubmodule pointerと仮想環境lockを元へ戻し、成功時だけ差分を残す。

`pnpm ppt-master:smoke` はネットワークとLLMを使わず、固定SVGからnative PPTXを作ってパッケージ構造を検査する。

- [ ] **Step 6: adapterとスモークテストを通す**

Run: `uv run pytest apps/worker/tests/test_ppt_master_adapter.py -q`

Run: `pnpm ppt-master:smoke`

Expected: PASS、生成PPTXのslide数がfixtureと一致し、外部通信0件。

- [ ] **Step 7: コミットする**

```bash
git add .gitmodules .gitignore package.json scripts/update-ppt-master.sh scripts/verify-ppt-master.py vendor/ppt-master apps/worker/src/alinea_worker/presentation apps/worker/tests/fixtures/presentation/minimal_project apps/worker/tests/test_ppt_master_adapter.py
git commit -m "build(presentation): pin and verify ppt-master"
```

### Task 28: プレゼンテーションのデータモデル、LLMルート、APIを実装する

**Files:**

- Create: `apps/api/alembic/versions/0021_presentation_artifacts.py`
- Modify: `packages/py-core/src/alinea_core/db/models.py`
- Modify: `packages/py-core/src/alinea_core/storage/s3.py`
- Create: `apps/api/src/alinea_api/schemas/presentations.py`
- Create: `apps/api/src/alinea_api/routers/presentations.py`
- Modify: `apps/api/src/alinea_api/main.py`
- Modify: `apps/api/src/alinea_api/schemas/settings.py`
- Modify: `apps/api/src/alinea_api/routers/settings.py`
- Modify: `packages/llm/models.yaml`
- Modify: `packages/llm/routing.yaml`
- Modify: `apps/worker/src/alinea_worker/tasks/export_user_data.py`
- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py`
- Create: `apps/api/tests/test_presentations.py`
- Modify: `apps/api/tests/test_settings_api.py`
- Modify: `apps/worker/tests/test_export_bulk.py`
- Modify: `apps/worker/tests/test_import_bulk.py`

- [ ] **Step 1: 所有権、重複防止、成果物置換の失敗テストを書く**

三つのpresetとaudience既定値、任意指示500文字上限、他ユーザーの404、未生成downloadの404、同一論文のactive job再利用を検査する。
既存成果物がある再生成では、DBが新storage keyを指すまで旧keyを削除しないことを検査する。

```python
assert response.status_code == 202
assert second.json()["job_id"] == first.json()["job_id"]
assert job.kind == "presentation"
assert job.payload["preset"] == "reading_group"
assert "instruction" not in job.payload or len(job.payload["instruction"]) <= 500
```

- [ ] **Step 2: 失敗を確認する**

Run: `uv run pytest apps/api/tests/test_presentations.py apps/api/tests/test_settings_api.py -q`

Expected: presentation schema、router、migrationが無いためFAIL。

- [ ] **Step 3: migrationとORMを実装する**

`presentation_artifacts.library_item_id` をunique FKとし、`source_revision_id`、`generation_job_id`、`preset`、`audience`、`instruction`、`model_provider`、`model_id`、`ppt_master_revision`、`pptx_storage_key`、`generated_at`、`updated_at` を保存する。
`ck_jobs_kind` へ `presentation`、`ck_llm_task_routes_task` と `ck_user_task_model_overrides_task` へ `presentation` を追加する。

```python
class PresentationArtifact(Base):
    __tablename__ = "presentation_artifacts"
    library_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("library_items.id", ondelete="CASCADE"), unique=True
    )
    source_revision_id: Mapped[UUID] = mapped_column(ForeignKey("document_revisions.id"))
    pptx_storage_key: Mapped[str]
    ppt_master_revision: Mapped[str]
```

S3 keyは `presentations/{library_item_id}/{job_id}.pptx` とし、上書きkeyを使わない。

- [ ] **Step 4: presentation用LLMルートと設定を追加する**

既定chainはOpenAIとAnthropicの構造化出力対応モデルを含める。
Task 13〜14で導入したユーザー別ルーターから `task="presentation"` を解決し、BYOK、運営キー、quota、usage記録を既存ジョブと同じ規則で処理する。
APIキーが一つも使えない場合はjobを作る前にProblem Detailsを返す。

- [ ] **Step 5: APIとbulk queue投入を実装する**

`POST /api/library-items/{item_id}/presentation`、`GET /api/library-items/{item_id}/presentation`、`GET /api/library-items/{item_id}/presentation/download` を実装する。
POSTはready revisionを固定し、active jobのpartial unique条件またはrequest keyで二重生成を防ぎ、`alinea:bulk` へ投入する。
GETは最新版のmetadataと進行中jobを返し、downloadは所有者確認後にPPTXだけをstreamする。

- [ ] **Step 6: 完全バックアップへ成果物を含める**

artifact metadataとPPTX byteをmanifestへ追加し、別ユーザーへの復元時にlibrary item、revision、job IDを張り直す。
同じバックアップの二回目は既存artifactを再利用し、復元先に新しい成果物がある場合は上書きしない。

- [ ] **Step 7: API、設定、バックアップテストを通す**

Run: `uv run pytest apps/api/tests/test_presentations.py apps/api/tests/test_settings_api.py apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py -q`

Expected: PASS。

- [ ] **Step 8: コミットする**

```bash
git add apps/api/alembic/versions/0021_presentation_artifacts.py packages/py-core/src/alinea_core/db/models.py packages/py-core/src/alinea_core/storage/s3.py apps/api/src/alinea_api/schemas/presentations.py apps/api/src/alinea_api/routers/presentations.py apps/api/src/alinea_api/main.py apps/api/src/alinea_api/schemas/settings.py apps/api/src/alinea_api/routers/settings.py packages/llm/models.yaml packages/llm/routing.yaml apps/worker/src/alinea_worker/tasks/export_user_data.py apps/worker/src/alinea_worker/tasks/import_user_data.py apps/api/tests/test_presentations.py apps/api/tests/test_settings_api.py apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py
git commit -m "feat(presentation): add artifacts and generation API"
```

### Task 29: 根拠付き構成から安全なPPTXを生成するWorkerを実装する

**Files:**

- Create: `apps/worker/src/alinea_worker/presentation/schemas.py`
- Create: `apps/worker/src/alinea_worker/presentation/source_packet.py`
- Create: `apps/worker/src/alinea_worker/presentation/prompts.py`
- Create: `apps/worker/src/alinea_worker/presentation/runner.py`
- Create: `apps/worker/src/alinea_worker/tasks/generate_presentation.py`
- Modify: `apps/worker/src/alinea_worker/figure_assets.py`
- Modify: `apps/worker/src/alinea_worker/tasks/__init__.py`
- Modify: `apps/worker/src/alinea_worker/main.py`
- Create: `apps/worker/tests/fixtures/presentation/paper_document.json`
- Create: `apps/worker/tests/test_presentation_source_packet.py`
- Create: `apps/worker/tests/test_generate_presentation.py`
- Modify: `apps/worker/tests/test_figure_assets.py`

- [ ] **Step 1: source packetのプライバシー境界を失敗テストにする**

書誌、構造化本文、節見出し、数式、図表caption、取得済み図表assetだけを含むことを検査する。
メモ、注釈、ハイライト、チャット、記事、翻訳、BYOKに一意なsentinelを入れ、serialized packetとLLM requestの双方に現れないことを検査する。

```python
serialized = packet.model_dump_json()
for secret in (note_secret, annotation_secret, chat_secret, translation_secret, api_key):
    assert secret not in serialized
```

- [ ] **Step 2: 根拠anchor付きsource packetを実装する**

abstract、introduction、method、results、limitations、conclusionを優先し、各blockへrevision固定のanchorを付ける。
packet全体を120,000文字、単一blockを12,000文字、図表を20件に制限する。
図表byteが無い場合は番号とcaptionだけを残し、生成を継続する。

- [ ] **Step 3: スライド構成の構造化出力と検証を実装する**

用途別の枚数は輪読会10〜14枚、研究発表12〜18枚、実装解説10〜16枚とする。
最初のLLM呼び出しは各slideのtitle、claims、evidence anchors、figure IDs、layout intentを返す。
存在しないanchor、重複figure、根拠のない数値を拒否し、一回だけ修復生成した後も不正なら `planning` stageで失敗させる。

```python
class SlidePlan(BaseModel):
    index: int
    title: str
    claims: list[str]
    evidence_anchors: list[str]
    figure_ids: list[str] = []
    speaker_notes: str
    layout: Literal["title", "content", "comparison", "figure", "summary"]
```

任意指示は表現、強調、対象読者の希望としてだけ扱い、論文にない事実の根拠には使わない。
論文本文と任意指示に含まれる命令文はuntrusted dataとして区切り、system promptや出力schemaを変更させない。

- [ ] **Step 4: slide単位SVG生成と安全性検査を実装する**

各SVG生成には当該slideのclaimと参照抜粋だけを渡し、他slideやpacket全体を再送しない。
日本語、16:9、研究発表向け配色、論文由来図表のみという固定契約をpromptへ入れる。

`figure_assets.py` のSVG検査を公開関数 `sanitize_svg_document` として再利用できるようにし、script、event属性、外部URL、DOCTYPE、foreignObject、path traversal、過大XMLを拒否する。
上流quality checkerへ渡す前に全SVGを検査する。

- [ ] **Step 5: PresentationRunnerのstageと上流変換を実装する**

`preparing_source`、`planning`、`authoring_slides`、`validating`、`exporting`、`uploading` の順に `JobStore.checkpoint` とSSEを更新する。
job固有temporary directoryを作り、成功、失敗、cancelの全経路で削除する。

`PptMasterAdapter` は品質検査、notes分割、SVG finalize、`svg_to_pptx.py --merge-paragraphs` を一つずつ実行する。
subprocessへLLMキーを渡さず、上流スクリプトからネットワークへ接続しない。
Worker mainでは `presentation` をLLM必須kindとbulk queueへ登録し、authoring完了時に根拠検証済みのspeaker notesを `notes/total.md` へ書く。

- [ ] **Step 6: 成果物を原子的に置換する**

新PPTXをjob固有keyへuploadし、ZIP構造、slide数、最大100 MiB、SHA-256を検証してからartifact rowを更新する。
DB commit後に旧keyを削除する。
旧key削除だけが失敗した場合は新成果物を成功扱いにしてcleanup retryへ記録する。
library item削除時は参照中のPPTX keyを削除対象へ入れる。
upload、DB更新、旧key削除の各失敗を注入し、新生成が失敗した場合に旧artifactがdownload可能なままであることを検査する。

- [ ] **Step 7: Workerテストを通す**

Run: `uv run pytest apps/worker/tests/test_presentation_source_packet.py apps/worker/tests/test_generate_presentation.py apps/worker/tests/test_ppt_master_adapter.py apps/worker/tests/test_figure_assets.py -q`

Expected: PASS、live LLM通信0件、外部画像取得0件、temporary directory残存0件。

- [ ] **Step 8: コミットする**

```bash
git add apps/worker/src/alinea_worker/presentation apps/worker/src/alinea_worker/tasks/generate_presentation.py apps/worker/src/alinea_worker/figure_assets.py apps/worker/src/alinea_worker/tasks/__init__.py apps/worker/src/alinea_worker/main.py apps/worker/tests/fixtures/presentation/paper_document.json apps/worker/tests/test_presentation_source_packet.py apps/worker/tests/test_generate_presentation.py apps/worker/tests/test_figure_assets.py
git commit -m "feat(worker): generate grounded editable presentations"
```

### Task 30: スライド生成ダイアログ、進捗、ダウンロードをWebへ接続する

**Files:**

- Modify: `packages/api-client/openapi.json`
- Modify: `packages/api-client/src/generated/`
- Create: `apps/web/src/components/viewer/presentation/types.ts`
- Create: `apps/web/src/components/viewer/presentation/queries.ts`
- Create: `apps/web/src/components/viewer/presentation/PresentationDialog.tsx`
- Create: `apps/web/src/components/viewer/presentation/PresentationDialog.test.tsx`
- Create: `apps/web/src/components/viewer/presentation/PresentationProgress.tsx`
- Create: `apps/web/src/components/viewer/presentation/PresentationProgress.test.tsx`
- Modify: `apps/web/src/components/viewer/ViewerHeader.tsx`
- Modify: `apps/web/src/components/viewer/ViewerShell.tsx`
- Modify: `apps/web/src/components/viewer/ViewerShell.mobile.test.tsx`
- Modify: `apps/web/src/components/settings/types.ts`
- Modify: `apps/web/src/components/settings/SettingsClient.tsx`
- Modify: `apps/web/src/components/settings/SettingsClient.test.tsx`
- Create: `apps/web/e2e/specs/pw-presentation.spec.ts`

- [ ] **Step 1: ダイアログと進捗の失敗テストを書く**

デスクトップの「✦ ツール」、三つの用途、用途別audience既定値、500文字制限、生成中の二重送信防止を検査する。
既存成果物がある状態で再生成に失敗しても「ダウンロード」が残ることを検査する。
モバイルでは生成項目を表示しないことも検査する。

- [ ] **Step 2: OpenAPIと生成SDKを更新する**

Run: `cd apps/api && uv run python -m alinea_api.export_openapi > ../../packages/api-client/openapi.json`

Run: `pnpm --filter @alinea/api-client generate`

Expected: presentationのPOST、GET、download operationが生成される。

- [ ] **Step 3: queryと状態機械を実装する**

初回表示で既存artifactとactive jobを取得する。
開始後は共通 `useJobEvents` を使い、SSE切断時は既存polling fallbackへ移る。
stageを日本語表示へ変換し、再読込後もactive jobを追跡する。

- [ ] **Step 4: ツールメニューと開始ダイアログを実装する**

ViewerHeaderへデスクトップ専用の「✦ ツール」を追加する。
用途は輪読会、研究発表、実装解説、聴衆は初学者、研究者、実装者とし、任意指示は文字数と送信内容を明示する。
色、書体、画像方針、言語は設定項目にせず、設計書の安全な既定値を使う。

- [ ] **Step 5: 成功、失敗、再生成UIを実装する**

成功時は生成日時、用途、使用model、ppt-master revision、ダウンロード、再生成を表示する。
失敗時はstageとProblem Detailsを表示し、再試行できるようにする。
以前の成果物がある場合は失敗表示とダウンロードを同時に残す。

- [ ] **Step 6: 設定画面へpresentation modelルートを追加する**

OpenAIだけ、Anthropicだけ、両方、どちらも無しの表示を検査する。
APIキー値をWebへ返さず、選択中model IDと利用可否だけを表示する。

- [ ] **Step 7: UnitとE2Eを通す**

Run: `pnpm --filter @alinea/web test -- PresentationDialog.test.tsx PresentationProgress.test.tsx SettingsClient.test.tsx ViewerShell.mobile.test.tsx`

Run: `pnpm --filter @alinea/web e2e -- pw-presentation.spec.ts`

Expected: 三presetの開始、SSE進捗、download、再生成失敗時の旧成果物保持、モバイル非表示がPASS。

- [ ] **Step 8: コミットする**

```bash
git add packages/api-client/openapi.json packages/api-client/src/generated apps/web/src/components/viewer/presentation apps/web/src/components/viewer/ViewerHeader.tsx apps/web/src/components/viewer/ViewerShell.tsx apps/web/src/components/viewer/ViewerShell.mobile.test.tsx apps/web/src/components/settings/types.ts apps/web/src/components/settings/SettingsClient.tsx apps/web/src/components/settings/SettingsClient.test.tsx apps/web/e2e/specs/pw-presentation.spec.ts
git commit -m "feat(web): generate and download paper presentations"
```

### Task 31: API契約、仕様書、生成クライアントを現在の機能へ揃える

**Files:**

- Modify: `apps/api/src/alinea_api/routers/translations.py`
- Modify: `packages/api-client/openapi.json`
- Modify: `packages/api-client/src/generated/`
- Modify: `docs/00-product.md`
- Modify: `docs/01-domain-model.md`
- Modify: `docs/02-ingest.md`
- Modify: `docs/03-translation.md`
- Modify: `docs/06-library.md`
- Modify: `docs/07-figures-and-articles.md`
- Modify: `docs/10-roadmap.md`
- Modify: `docs/11-vocabulary.md`
- Modify: `docs/12-resources.md`
- Modify: `docs/superpowers/specs/2026-07-16-paper-presentation-tool-design.md`
- Modify: `docs/superpowers/plans/2026-07-16-all-features-integration-report.md`

- [ ] **Step 1: easy translationのoperation IDを修正する**

POST `/api/revisions/{revision_id}/translations` のoperation IDを `translations_start_easy` に変更し、生成SDK利用箇所を新名称へ移す。
旧名を使うソースが残っていないことを `rg` で検査する。

- [ ] **Step 2: OpenAPIを再生成し、実行中appと意味的一致を確認する**

Run: `cd apps/api && uv run python -m alinea_api.export_openapi > ../../packages/api-client/openapi.json`

Run: `pnpm --filter @alinea/api-client generate`

Run: `git diff --exit-code -- packages/api-client/openapi.json packages/api-client/src/generated || true`

Expected: 再生成後に追加差分が発生しない状態にする。

- [ ] **Step 3: 製品文書の非目標と将来表記を更新する**

公開機能、他サイト、Hugging Face関連ソース、GitHubコード対応解析、セマンティック検索、オフライン閲覧、PPTX生成、Anki TSV、easy translationを実装済みの契約へ変更する。
PPTX生成の入力境界、最新版のみの保存、デスクトップ専用導線、上流固定commitを製品文書と設計書で一致させる。
設計書のステータスは、受け入れ基準を満たした実測結果がある場合だけ「実装済み」へ変更する。

- [ ] **Step 4: integration reportを再評価する**

「Phase 1」を「完了」と書き換えるのは各機能の受け入れ基準が通った項目だけにする。
テスト未実行を成功として記録しない。

- [ ] **Step 5: コミットする**

```bash
git add apps/api/src/alinea_api/routers/translations.py packages/api-client/openapi.json packages/api-client/src/generated docs/00-product.md docs/01-domain-model.md docs/02-ingest.md docs/03-translation.md docs/06-library.md docs/07-figures-and-articles.md docs/10-roadmap.md docs/11-vocabulary.md docs/12-resources.md docs/superpowers/specs/2026-07-16-paper-presentation-tool-design.md docs/superpowers/plans/2026-07-16-all-features-integration-report.md
git commit -m "docs: align product contracts with completed features"
```

### Task 32: 全体回帰、DB分離、E2Eの未検証箇所を解消する

**Files:**

- Modify: `apps/api/tests/conftest.py`
- Modify: `apps/worker/tests/conftest.py`
- Modify: `apps/web/e2e/specs/pw-05-viewer-modes.spec.ts`
- Modify: `apps/web/e2e/specs/pw-12-pdf-mode.spec.ts`
- Modify: `apps/web/e2e/specs/pw-14-search.spec.ts`
- Modify: `apps/web/e2e/specs/pw-17-settings.spec.ts`
- Modify: `apps/web/e2e/specs/pw-18-finish-reading.spec.ts`
- Modify: `apps/web/e2e/specs/pw-presentation.spec.ts`
- Modify: `apps/extension/e2e/xt.spec.ts`
- Modify: `docs/superpowers/plans/2026-07-16-all-features-integration-report.md`

- [ ] **Step 1: API／WorkerのテストDBをworker IDごとに分離する**

テストsession単位でschemaまたはdatabase名を生成し、各suite終了時にdropする。
seed testと通常testを順序逆転しても同じ結果になることを検査する。

- [ ] **Step 2: stale fixmeを実検査へ置き換える**

PDF bbox同期、FigureRefPopover、appendix TOC、記事検索、読了操作、拡張inline pillを実際のUI操作とassertionへ変更する。
外部YouTube通信だけはnetwork fixtureを使い、実サイトには接続しない。

- [ ] **Step 3: Pythonの全対象suiteを通す**

Run: `uv run pytest packages apps/api apps/worker -q`

Expected: PASS、順序依存失敗0件、live LLM通信0件。

- [ ] **Step 4: TypeScriptの全対象suiteを通す**

Run: `pnpm -r typecheck`

Run: `pnpm --filter @alinea/web test`

Run: `pnpm --filter @alinea/web build`

Expected: すべてexit 0。

- [ ] **Step 5: ブラウザE2Eを通す**

Run: `pnpm --filter @alinea/web e2e`

Run: `pnpm --filter @alinea/extension e2e`

Expected: 環境依存の明示的skipを除きPASS。今回対象のfixmeは0件。

- [ ] **Step 6: ppt-master固定fixtureを再検証する**

Run: `pnpm ppt-master:smoke`

Run: `test "$(git -C vendor/ppt-master rev-parse HEAD)" = "0c0bdaf0dd953afc2c00322e92f26dc02fc1c51f"`

Expected: PPTXパッケージ検査がPASSし、submoduleが承認済みcommitと一致する。

- [ ] **Step 7: マイグレーションを往復検証する**

Run: `(cd apps/api && uv run alembic upgrade head)`

Run: `(cd apps/api && uv run alembic downgrade 0012)`

Run: `(cd apps/api && uv run alembic upgrade head)`

Expected: 三コマンドともexit 0、既存データを保持する。

- [ ] **Step 8: 最終レポートを実測値で更新する**

各コマンド、テスト件数、skip理由、既知制約をintegration reportへ記録する。
ユーザー受け入れ確認前であるため、この時点では「自動検証完了、ユーザー受け入れ待ち」と記載する。

- [ ] **Step 9: リリース候補となる最終変更をコミットする**

```bash
git add apps/api/tests/conftest.py apps/worker/tests/conftest.py apps/web/e2e/specs/pw-05-viewer-modes.spec.ts apps/web/e2e/specs/pw-12-pdf-mode.spec.ts apps/web/e2e/specs/pw-14-search.spec.ts apps/web/e2e/specs/pw-17-settings.spec.ts apps/web/e2e/specs/pw-18-finish-reading.spec.ts apps/web/e2e/specs/pw-presentation.spec.ts apps/extension/e2e/xt.spec.ts docs/superpowers/plans/2026-07-16-all-features-integration-report.md
git commit -m "test: close remaining integration coverage gaps"
```

- [ ] **Step 10: 最終コミットからリリース候補を用意する**

Run: `git status --short`

Expected: 出力なし。

Run: `git rev-parse HEAD`

Expected: リリース候補へデプロイする40文字のコミットSHA。

このSHAからリリース候補をビルドしてデプロイする。
一般ユーザー二件、公開者一件、外部サイト別のサンプルURL、バックアップ用論文、GitHubコード対応の正解例、スライド内容を人手照合できる図表付き論文を確認環境へ用意する。

- [ ] **Step 11: ユーザー受け入れ確認を依頼する**

[最終ユーザー受け入れチェックリスト](./2026-07-17-user-acceptance-checklist.md)を作業用文書またはIssueへ複製し、コミットSHA、環境URL、自動テスト結果、既知制約を記録する。
リポジトリ内のチェックリスト原本は変更しない。
原本を変更すると確認対象のコミットSHAが変わるためである。

- [ ] **Step 12: 判定結果に応じて処理する**

`FAIL` または `BLOCKED` の場合はmainへマージせず、該当タスクへ戻って修正、対象テスト、Task 32の全検証、最終コミット、リリース候補作成を繰り返す。
`GO` の場合は、確認後にコード、設定、生成物、文書を変更しない。

- [ ] **Step 13: 確認済みコミットだけをmainへ統合する**

Run: `git rev-parse HEAD`

Expected: ユーザー受け入れ記録に記載されたコミットSHAと完全に一致する。

P0、P1、BLOCKEDが0件であり、SHAが一致する場合だけmainへマージする。

## 2. 各フェーズの完了条件

フェーズAは、バックアップを別ユーザーへ二回取り込んでもデータが欠落せず、復元先の既存値を変更しない場合に完了とする。

フェーズBは、再翻訳、AI単語候補、改版差分を画面から開始し、成功、失敗、空結果を処理できる場合に完了とする。

フェーズCは、選択した成果物だけを含むZIPを別端末へ移し、ネットワークを切った状態でHTMLとPDFを開ける場合に完了とする。

フェーズDは、同一Workerで同時実行した二ユーザーのprovider、model、BYOKが混ざらない場合に完了とする。

フェーズEは、ACL、OpenReview、PMC、Hugging Faceの保存fixtureからネットワーク非依存で取り込みまたは候補収集を完了し、実サイト障害を既存のエラー分類へ変換できる場合に完了とする。

フェーズFは、意味検索がユーザー境界を越えず、埋め込み障害時に全文検索だけで応答できる場合に完了とする。

フェーズGは、GitHubコード解析が三つの実行モードと月額予算に従い、検証済みの論文anchorと固定commit行だけを保存する場合に完了とする。

フェーズHは、訪問済み論文をオフラインで再読でき、オンライン401がログイン処理へ届く場合に完了とする。

フェーズIは、公開スナップショットから私的データを除外し、コメントの投稿者権限と公開者モデレーションをAPIとUIの双方で検証できる場合に完了とする。

フェーズJは、OpenAIまたはAnthropicのユーザー別ルートから根拠付き構成を生成し、固定したppt-masterで変換した編集可能な日本語PPTXを取得できる場合に完了とする。

フェーズKの実装作業は、Task 32の必須コマンドがすべて成功し、OpenAPI再生成で差分が出ず、統合レポートが実測結果と一致する最終コミットを作成した場合に完了とする。
mainへの最終マージは、その最終コミットから作ったリリース候補が[最終ユーザー受け入れチェックリスト](./2026-07-17-user-acceptance-checklist.md)で `GO` となり、確認後の変更がない場合に行う。

## 3. ロールバック方針

- フェーズAとBは通常のコードrevertで戻す。
- `paper_export` はジョブkindを残してhandlerを無効化し、進行中ジョブを失敗状態へ確定してから戻す。
- 他サイト取り込みはadapter単位の設定フラグで停止し、既に取り込んだPaperとSourceAssetは削除しない。
- セマンティック検索は `SEMANTIC_SEARCH_ENABLED=false` で即時にPGroongaだけへ戻す。
- GitHubコード対応解析は `code_analysis.mode=off` で新規実行を停止し、保存済み結果は読み取り専用で維持する。
- オフライン閲覧はservice workerのcache versionを上げてviewer cacheを削除し、静的asset cacheだけへ戻す。
- 記事公開は新規公開とコメント投稿を停止しても既存URLの読み取りは維持する。
  緊急非公開化が必要な場合だけpublicationのvisibilityを停止状態へ更新する。
- PPTX生成はpresentation APIの開始操作とWorker handlerを機能フラグで停止し、既存成果物のdownloadは維持する。
  ppt-master更新に失敗した場合はsubmoduleと専用仮想環境を直前の検証済みcommitへ戻す。

## 4. 実装中に変更してはならない条件

- ユーザー所有の未追跡ファイル `.superpowers/brainstorm/` を変更またはコミットしない。
- BYOK秘密鍵をログ、ジョブpayload、バックアップ、埋め込みテーブルへ保存しない。
- 外部サイト、埋め込みAPI、LLMへ接続するテストを作らない。
  fixture、fake provider、ASGI transportを使う。
- private paper、notes、chat、translation本文を公開記事のレスポンスへ含めない。
- GitHub archive内のコードを実行せず、依存をインストールせず、秘密ファイルをLLMへ送らない。
- PPTX生成へメモ、注釈、ハイライト、チャット、記事、翻訳本文、APIキーを渡さない。
- ppt-masterは固定submodule以外から実行せず、本番処理中に上流をpullしない。
- 401をService Workerのキャッシュで成功応答へ置換しない。
- 機能フラグoff時の既存検索順とAPIレスポンスを変更しない。
