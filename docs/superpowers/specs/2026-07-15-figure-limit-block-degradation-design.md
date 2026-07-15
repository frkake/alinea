# 図数上限のブロック単位縮退 + フロントからの追加画像読み込み

**日付:** 2026-07-15
**背景:** arXiv 監査(20本)で 2502.13994(687図/e-print 202MB/PDF 216MB)が取り込み失敗。原因は2つの fail-closed:
(1) `_stage_fetching` が原文PDF過大で hard fail(→ 別途修正済: 非致命化)、
(2) `_materialize_candidate_figures` が `declared figures > MAX_FIGURES_PER_DOCUMENT`(=200)のとき
`figure_limit_exceeded` を**構造的失敗**として候補全体を拒否。

本設計は (2) をブロック単位縮退に変え、超過分の図をフロントから**オンデマンド**で読み込めるようにする(P3: 黙って壊れない)。

## 目標
- 図数が上限を超える文書も、先頭 N 図を materialize し**取り込み成功**(品質 A/B)。
- 超過分は「未読込(deferred)」として原文レイアウト位置に残し、ビューアに枠+キャプションを表示。
- フロントから **図ごとに「読み込む」**、および **上限を段階的に拡張(+N)** して未読込図を materialize できる。

## 非目標
- e-print/PDF の絶対サイズ上限(128MB)の引き上げ。202MB のソース丸ごと展開はしない(bomb 防御維持)。
- 全図を無条件に一括生成すること(コスト・メモリ理由)。

## アーキテクチャ(4層)

### 1. pipeline `_materialize_candidate_figures`
- `declared_ids > MAX_FIGURES_PER_DOCUMENT` のとき、`blocks=[]`+構造拒否をやめる。
  先頭 `MAX_FIGURES_PER_DOCUMENT` 図を通常どおり materialize し、**残りを deferred** とする。
- 新しい失敗コード `figure_deferred`(figure/table ブロック単位・**degradable**)を導入。
  既存の構造的 `figure_limit_exceeded`(非degradable)とは区別。`_is_retryable_candidate_code` は False。
- deferred 図は `degradable_block_ids` に含め、候補は `accepted=True` を維持。
- deferred 図の `block_id` と再取得に必要な `asset_key`(HTML=画像src, LaTeX=アーカイブ内パス)を
  `stats.deferred_figures` に記録。

### 2. ソース保持
- 取り込み成功(縮退含む)時、arxiv_html/latex アーカイブを `source_assets` に保持し、
  後段のオンデマンド materialize が**再ダウンロード不要**で行えるようにする。
- (LaTeX の場合はアーカイブ 128MB 以内が前提。deferred の figure バイトは保持ソースから抽出。)

### 3. API
- `GET .../viewer` の figure 各要素に `deferred: bool` を露出。
- 新規 `POST /api/library-items/{id}/figures/{block_id}/materialize` → 202 + job。
  worker が保持ソースから当該図を materialize し asset を書き込む。既 materialize は 200 で即返す。
- 新規 `POST /api/library-items/{id}/figures/materialize-batch`(body: `{count:N}`)→
  未読込図を先頭から N 件 job 投入(段階拡張)。

### 4. フロント(ビューア figure ブロック)
- `deferred` 図は画像の代わりに「未読込」プレースホルダ + 「画像を読み込む」ボタン(図ごと)。
- 図一覧/本文末に「残りをまとめて読み込む(+N)」バッチ操作。
- ボタン→ endpoint 呼び出し→ job 完了ポーリング→ 画像差し替え(楽観的にスピナー表示)。

## エラーハンドリング
- deferred 図の materialize 失敗(画像過大・変換クラッシュ)は当該図のみ失敗表示。文書は壊さない。
- 保持ソースが無い/失効した場合は endpoint が 409 とし、ビューアは「読込不可(理由)」を表示。

## テスト
- pipeline: 図>200 で候補 accepted + deferred manifest 正、先頭200が materialize 済。
- pipeline: 既存の「>200で拒否」テスト(`test_latex_priority_pipeline.py`)を新挙動へ更新。
- API: materialize 単体/バッチ endpoint(202・冪等・409)。
- viewer: deferred プレースホルダ + ボタン押下で画像差し替え(コンポーネントテスト)。
- e2e: 2502.13994 が取り込み成功し、ビューアで deferred 図を読み込める。
