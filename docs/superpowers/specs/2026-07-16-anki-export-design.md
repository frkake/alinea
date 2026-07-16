# S9 Anki エクスポート — 設計仕様

**Feature:** S9  
**Milestone:** M3  
**Date:** 2026-07-16  

---

## 1. 目的

語彙帳を Anki にインポートできる形式でエクスポートし、SRS 学習を Anki でも継続できるようにする。

---

## 2. 依存方針の決定

### 選択肢

| 方式 | フォーマット | 依存追加 | Anki 対応 |
|------|------------|---------|----------|
| (a) `.apkg` | SQLite ベース | `genanki`(新規) | ネイティブ完全対応 |
| (b) TSV | タブ区切りテキスト | なし | Anki の「テキストファイルから読み込む」で対応 |

### 決定: **(b) TSV — v1**

理由:
- リポジトリには `uv add` 禁止・casual dep 禁止のノルムがある(test_export.py コメント参照)。
- Anki は v2.1+ からタブ区切りテキストをネイティブインポートできる。Front/Back/tags の 3 列で完全に機能する。
- `.apkg` は将来の v2 として `genanki` 追加を明示的にユーザーが承認する形で実装する。

### 将来拡張フラグ

`.apkg` (genanki) は `docs/11-vocabulary.md` および本ファイルに TODO として記録する。
実装時は `uv add genanki` を追加し、ユーザー承認を得てから着手すること。

---

## 3. カードレイアウト

### Front

```
{term}

{pos_label}  {ipa}
```

- `pos_label` / `ipa` は生成済みの場合のみ表示(空白は省略)。

### Back

```
{meaning_short}

{meaning_long}

---
文脈: {context_sentence}

解釈: {interpretation}
語源: {etymology}
覚えるコツ: {mnemonic}

出典: {source.display}
```

- `meaning_long` / `interpretation` / `etymology` / `mnemonic` はそれぞれ非空の場合のみ出力。
- 改行は `\n`(TSV セル内の改行。Anki はセル内改行を `<br>` として表示する)。

### Tags (3 列目)

```
alinea {kind} {paper_slug}
```

- `paper_slug` = `source.paper_title` の先頭 30 文字をスペース→`_`、ASCII 以外→省略して生成。
- タグはスペース区切りで Anki に渡す。

---

## 4. TSV ファイル仕様

- 1 行目: `#separator:tab` (Anki テキストインポートのヒント行)
- 2 行目: `#html:true` (セル内改行を `<br>` として扱う)
- 3 行目: `#tags column:3` (3 列目がタグ)
- 4 行目以降: `{front}\t{back}\t{tags}`
- エンコーディング: UTF-8(BOM なし)
- MIME: `text/plain; charset=utf-8`
- ファイル名: `alinea-vocab-{YYYYMMDD}.txt`

---

## 5. エンドポイント

```
GET /api/vocab/export/anki
```

フィルタパラメータは `GET /api/vocab/export/markdown` と完全に同じ:

| パラメータ | 型 | 説明 |
|-----------|---|------|
| `kind` | `list[str]` (optional) | word / collocation / idiom |
| `due` | `bool` (optional) | 復習期のみ |
| `q` | `str` (optional) | 語彙内検索 |
| `library_item_id` | `str` (optional) | 論文絞り込み |
| `sort` | `str` (default: `added_at`) | added_at / term |

レスポンスヘッダ:
- `Content-Type: text/plain; charset=utf-8`
- `Content-Disposition: attachment; filename="alinea-vocab-{YYYYMMDD}.txt"`

`operation_id`: `vocab_export_anki`

---

## 6. Web UI

語彙帳ページ(`/vocab`)の `VocabHeader` に「Ankiへ書き出す」ボタンを追加する。

- 現在の URL フィルタ(`kind` / `due` / `q` / `sort`)を読み取ってダウンロード URL を生成する。
- `triggerDownload` で発火する(settings 画面の BibTeX / CSV と同一パターン)。
- ボタンスタイル: 既存「復習をはじめる」ボタンに倣ったセカンダリスタイル(枠線あり、背景 `var(--pr-bg-panel)`)。

---

## 7. テスト

### Backend (pytest)

テスト ID: `PY-VOC-10`  
ファイル: `apps/api/tests/test_vocab.py`

- `test_export_anki_tsv_structure`: エンドポイントが 200 を返し、ヘッダ行・カード行が正しく含まれる。
- `test_render_anki_tsv_fields`: `_render_anki_tsv()` を直接呼び、Front/Back/tags が期待どおりであることを確認。
- `test_export_anki_filter_kind`: `kind=word` フィルタが機能する。

### Frontend (vitest)

テスト ID: `TS-VOCAB-ANKI`  
ファイル: `apps/web/src/components/vocab/VocabHeader.test.tsx` (新規)

- 「Ankiへ書き出す」ボタンが描画される。
- クリックで `triggerDownload` が適切な URL で呼ばれる。

---

## 8. SDK 再生成

エンドポイント追加後:

```bash
cd apps/api && uv run python -m alinea_api.export_openapi > ../../packages/api-client/openapi.json
cd packages/api-client && pnpm generate
```

---

## 9. 将来の `.apkg` 拡張

- `genanki` 依存追加が承認されたら `GET /api/vocab/export/anki?format=apkg` を追加。
- TSV エンドポイントはそのまま残す(後方互換)。

---

## 10. 対象外(スコープ外)

- `genanki` を使った `.apkg` 生成(v2 以降)。
- Anki Connect / Anki-sync integration。
- SRS ステージの Anki への移植(Anki 側の SRS は独立して動作させる)。
