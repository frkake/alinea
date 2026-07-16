# S11 「やさしい訳」翻訳スタイル 設計仕様

**Feature:** S11 — Translation style "easy" (やさしい訳)
**Milestone:** M3
**Date:** 2026-07-16
**Status:** Accepted

---

## 1. 背景と目的

docs/03-translation.md §7 には現在 2 種類の翻訳スタイルが実装されている:
- `natural` — 自然な学術日本語(取り込み時に自動生成)
- `literal` — 直訳、語順対応を重視(M2-15 オンデマンド)

M3 で第 3 のスタイル `easy`(やさしい訳)を追加する。初学者向けの平易な日本語で、
専門用語に短い注を付す。

---

## 2. スタイル仕様

### 2.1 easy スタイルの文体規定

```
## 文体規定(やさしい訳)
- 「だ・である」調に固定する。
- 高校生や他分野の研究者が読んでも理解できる平易な日本語にする。
- 専門用語は初出時に括弧で短い説明を添える(例: 損失関数(モデルの誤りを数値化したもの))。
- 長い一文は読みやすく 2 文に分割してよい。意味を変えない範囲で言い換えを許す。
- カタカナ語・定訳・頭字語・用語表の扱いは自然訳と同じ。
```

### 2.2 スタイル識別子

`style = "easy"` — DB の `translation_sets.style` と API の `style` パラメータで使う。

---

## 3. バックエンド変更

### 3.1 プロンプト(py-core)

`packages/py-core/src/alinea_core/translation/prompts/templates.py`

`_STYLE_EASY` 定数を追加し、`build_system_preamble(style)` で `style == "easy"` 時に差し替える。
それ以外は natural と同じプリアンブル・例を使う。

### 3.2 API エンドポイント(`alinea_api/routers/translations.py`)

- `list_units` の style バリデーション: `"natural" | "literal" | "easy"` に拡張
- `start_easy_translation` 追加:
  - Request: `EasyTranslationRequest(style: Literal["easy"], priority_section_id: str | None = None)`
  - Response: `EasyTranslationResponse(set_id: str, job_id: str | None)`
  - エンドポイント: `POST /api/revisions/{revision_id}/translations`
    - 同じ URL を使い、`body.style` で `"literal"` と `"easy"` を判別する
  - `_create_easy_set` を `_create_literal_set` と対称に実装

### 3.3 ワーカー(`alinea_worker/tasks/translate.py`)

`_SECTION_REASONS` に `"easy"` を追加する(literal と同じ扱い)。

### 3.4 DB

`translation_sets.style` は `Text` 型だが、`0001_initial_schema.py` に
`ck_translation_sets_style CHECK (style IN ('natural', 'literal'))` の CHECK 制約がある。
`'easy'` を許可するマイグレーション `0011_easy_translation_style` を追加する
(`down_revision = 0009_user_scoped_ingest`。main のマイグレーション head に接続する)。

> 注: ORM モデル(`db/models.py`)側には CHECK 制約が現れず、生 SQL マイグレーション
> にのみ存在するため見落としやすい。実装時に発見して対応した。

---

## 4. フロントエンド変更

### 4.1 viewer-store.ts

`TranslationStyle = "natural" | "literal" | "easy"`

`easyStatus`, `easyJobId`, `easySetId` を literal と対称に追加。
`setEasyGeneration` アクションを追加。
`initViewer` で `easyStatus: "unknown"` にリセット。

### 4.2 ViewerHeader.tsx

- `STYLE_LABELS` に `easy: "やさしい訳"` を追加
- スタイルリストを `["natural", "literal", "easy"]` に拡張
- `ensureEasyGenerated` を `ensureLiteralGenerated` と対称に追加
- easy 選択時に `ensureEasyGenerated()` を呼ぶ

### 4.3 TranslationColumnHeader.tsx

`STYLE_LABELS` に `easy: "やさしい訳"` を追加。

### 4.4 SDK

`translations_start_easy` operation_id でエンドポイントを公開し、
openapi.json を更新して SDK を再生成する。

---

## 5. テスト

### 5.1 API テスト

`apps/api/tests/test_easy_style.py` — `test_literal_style.py` の easy 版。
カバーする主なケース:
1. easy セット作成・ジョブ生成(private 論文 → personal scope)
2. 再送冪等(同一 priority_section_id → 同一 job_id)
3. complete 時は 200 で即時返却
4. public 論文 → shared scope
5. ロールバック(enqueue 失敗時)

### 5.2 フロントエンドテスト

`ViewerHeader.easy-style.test.tsx` — `ViewerHeader.literal-style.test.tsx` の easy 版。

---

## 6. SDK 再生成手順

```bash
cd /path/to/alinea
uv run python -c "from alinea_api.main import app; import json; print(json.dumps(app.openapi()))" \
  > packages/api-client/openapi.json
pnpm --filter @alinea/api-client generate
```

---

## 7. 対象外

- easy スタイルの表示要素の UI polish(M3 フォローアップ)
- easy スタイルの用語注生成の品質調整
- easy スタイル向けの専用品質チェックルール
