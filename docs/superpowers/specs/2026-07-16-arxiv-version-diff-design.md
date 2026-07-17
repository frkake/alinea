# arXiv バージョン差分表示 (v1→v2) — 変更点提示

**Feature:** S10 (M3)
**日付:** 2026-07-16
**背景:** アプリは既にマルチリビジョン基盤を持つ。`document_revisions`(`source_version` v1/v2 と `parser_version`)、
adopt-revision 経路(`apps/api/.../routers/papers.py` の `adopt_revision`)、注釈の引き継ぎ/リアンカー
(`parsing/carryover.py` + `ingest/reanchor.py`)が揃っている。`difflib.SequenceMatcher` は
carryover のブロック ID 引き継ぎ(`carryover.py`)と LaTeX↔PDF 整列で既に使われている。
docs/02-ingest.md §6 は「バージョン間差分表示は将来機能」、docs/10-roadmap.md M3 に本項目がある。

**欠けているもの:** 「新版を採用して注釈を追従させる」(adopt-revision)は動くが、
**「何が変わったか」を提示する UI/差分** が無い。本設計はこの **変更提示** を埋める。

## 目標
- 同一 Paper の 2 つの `document_revisions` 間で **ブロック単位の構造差分**(追加 / 削除 / 変更 / 不変)を
  **決定的に**計算する純関数を追加する(LLM 不使用)。
- 既存の carryover が生成した **ブロック ID の一致関係を再利用**して版間のブロックを整列する。
  同一 ID → 内容ハッシュで「変更/不変」を判定、片側のみの ID → 追加/削除。
- 差分を API で公開し(`GET /api/papers/{paper_id}/revisions/diff`)、
  情報パネルの「変更点」セクションから利用できるようにする(提示面は下記「決定事項」)。

## 非目標(v1)
- 文字単位・単語単位のインライン差分レンダリング(フロントで後日。関数は old/new の平文を返すに留める)。
- LLM による自然言語の変更要約(将来のオプション。フラグで後付け可能な形にする。既定は構造差分)。
- 自動での版切替(P6: 切替は adopt-revision のユーザー操作のまま。差分表示は読み取り専用)。
- フロントの本実装(本 PR は差分エンジン + API の最初のスライス。UI は後続スライス)。

## アーキテクチャ

### 1. 差分エンジン(純関数 / py-core)
`alinea_core.parsing.version_diff.diff_revisions(old: DocumentContent, new: DocumentContent) -> RevisionDiff`

carryover の隣に置く(carryover と同じく版間ブロック操作であり、`flatten_blocks` と
`block_source_hash` を再利用するため)。

整列アルゴリズム(**carryover 結果の再利用**):
- 新版取り込み時、パイプラインは `carry_over_ids(old_blocks, new_sections)` を実行するため、
  版間で存続したブロックは **同一 `blk-...` ID** を共有する(これが carryover のマッピングそのもの)。
- 旧版・新版それぞれを `flatten_blocks` で文書順に平坦化し、**ブロック ID の列**を取り出す。
- `difflib.SequenceMatcher(None, old_ids, new_ids).get_opcodes()` で整列(既存 carryover / PDF 整列と
  同じ stdlib を再利用。新規依存なし)。ブロック ID は revision 内で一意(`assign_block_ids` が
  衝突時に `-2`,`-3` を付す)なので列比較は well-defined。
- opcode 分類:
  - `equal`(同一 ID)→ `block_source_hash` を両側で比較。一致=**unchanged** / 不一致=**changed**
    (carryover の位置・編集距離パスで ID を引き継いだが内容が変わったブロックがここに入る)。
  - `delete` / `replace` の旧側 → **removed**、`insert` / `replace` の新側 → **added**。
- 出力は opcode 順(= 新版文書順に削除ブロックが挿入位置で挟まる形)で決定的。

返り値:
```
BlockChange(status, block_id, block_type, section_id, old_text, new_text)
RevisionDiffStats(added, removed, changed, unchanged)
RevisionDiff(changes: list[BlockChange], stats)
```
`changes` は added/removed/changed のみ(unchanged は件数のみ stats に計上、payload を軽く保つ)。
`old_text`/`new_text` は `block_to_plain` で導出(added は old=None、removed は new=None)。
`section_id` は added/changed は新側、removed は旧側の所属セクション。

**carryover 未適用の版どうし**(独立パースで ID が引き継がれていない)を渡した場合は
ID が総入れ替えになり全ブロックが added+removed になる — 「整列できない」を黙って捏造せず
正直に返す安全側の縮退(P3)。

### 2. API(apps/api)
`GET /api/papers/{paper_id}/revisions/diff?from={rev_id}&to={rev_id}`
- `_paper_accessible` でアクセス制御、`get_paper_revision` で両リビジョンの所属検証(既存ヘルパ再利用)。
- `_as_content` で `DocumentRevision.content` → `DocumentContent`、`diff_revisions` を呼び `RevisionDiffResponse` を返す。
- `list_revisions`(§6.2)の隣。`from`/`to` は既存版一覧から選ぶ想定。

### 3. フロント(後続スライス。本 PR 非対象)
- 情報パネルに「変更点」セクション: サマリ(`+N 追加 / -N 削除 / ~N 変更`)+ 展開可能な変更ブロック一覧。
- 「新しいバージョンがあります」バナー(既存)の隣に「変更点を見る」導線。

## 決定事項(recommendation)
- **提示面:** 情報パネルの「変更点」セクション(専用差分モードではない)を推奨。既存のリビジョン一覧 UI の
  近傍で完結し、実装が軽い。専用の左右並置差分モードは将来の拡張余地として残す。
- **要約手法:** v1 は **構造的・決定的差分**(LLM 不使用)。LLM 要約はオプション/フラグ付きで後付け可能な形にし、
  既定は無効。テストが決定的に保てる・コスト/レイテンシ/幻覚を避けられるため。

## テスト(決定的・ライブ LLM 不使用)
- py-core `test_version_diff.py`(TDD 対象):
  - 合成 2 版で added/removed/changed/unchanged の分類を検証。
  - 恒等(同一内容)→ 変更 0。
  - 文書順(opcode 順)で added/removed が正しい位置に並ぶ。
  - `carry_over_ids` を実際に走らせて整列 → 変更判定まで(carryover 再利用の統合確認)。
- apps/api `test_revision_diff.py`: エンドポイントの stats/blocks、所属外リビジョン拒否、アクセス制御。
- `uv run pytest packages apps/api -q`(触れた領域)。
