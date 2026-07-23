# 設計: 論文スライド生成ツール

- 日付: 2026-07-16
- 対象: `apps/api`、`apps/worker`、`apps/web`、`packages/py-core`、`packages/llm`、`packages/api-client`、`vendor/ppt-master`
- ステータス: 承認済み・**実装済み(Task 30 として `feat/remaining-features-completion` へマージ)**。実装は API(`apps/api/src/alinea_api/routers/presentations.py`、`schemas/presentations.py`)、永続化(`presentation_artifacts` テーブル、migration `0018_presentation_artifacts`)、Web(`apps/web/src/components/viewer/presentation/`、`ViewerHeader` の「✦ ツール」)、上流固定(`vendor/ppt-master` を Git submodule でコミット `0c0bdaf0dd953afc2c00322e92f26dc02fc1c51f` に固定)、更新運用(`scripts/update-ppt-master.sh` / `scripts/ppt-master-smoke.sh` / `scripts/verify-ppt-master.py`)を含む。単体・E2E テスト(`apps/api/tests/test_presentations.py`、`apps/web/e2e/specs/pw-presentation.spec.ts`)を追加済み。**受け入れ基準の実測(E2E 実行と PPTX 出力の PowerPoint パッケージ検証)は Task 32 の統合検証で確認する**(本タスクでは未実行)。

## Context(背景)

Alinea で取り込んだ論文から、日本語の編集可能な PowerPoint を生成したい。生成器には
[hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) を使うが、ユーザーが Codex 等の
スキルとして操作する形にはしない。論文ビューア内の第一級ツールとして提供する。

上流の `ppt-master` は API サーバーではなく、AI エージェントがローカルのスクリプト群を
組み合わせるワークフローである。そのため Alinea は上流のプロジェクト初期化・SVG 検査・
PPTX 変換スクリプトを、ワーカー内の制御済みアダプタから呼び出す。

## Goal / Non-Goal

**Goal**

- 論文ビューアの「✦ ツール」から、論文本文・書誌・図表だけを使った PPTX 生成を開始できる。
- 出力は日本語の編集可能な PPTX とし、論文ごとに最新版のみを保存・再ダウンロードできる。
- `OPENAI_API_KEY` のみでも動作し、`ANTHROPIC_API_KEY` が設定されれば Claude も選択できる。
- 上流 `ppt-master` を 1 コマンドで、安全に更新・検証できる。

**Non-Goal(v1)**

- メモ、注釈、チャット履歴、ユーザー作成 PPTX テンプレートの入力。
- AI 画像生成、画像検索、音声ナレーション、アニメーション、生成履歴の保持。
- モバイルからの生成、詳細なデザインウィザード、上流 `main` の実行時自動追従。

## ユーザー体験

### 導線とダイアログ

- デスクトップの `ViewerHeader` に「✦ ツール」メニューを追加し、項目「論文からスライドを生成」を置く。
- 開始ダイアログは次だけを入力する。色・書体・画像方針など `ppt-master` の詳細設定は研究発表向けの安全な既定値に固定する。
  - 用途: `reading_group`(輪読会)、`research_talk`(研究発表)、`implementation`(実装解説)
  - 想定聴衆: `beginner`、`researcher`、`implementer`。用途に応じた既定値を設定し、必要時だけ変更可能にする。
  - 任意指示: 生成時の追加要望。長さを制限し、スライドの事実根拠としては扱わない。
- 出力言語は日本語固定。ただし論文名、著者名、固有名詞、数式、原文引用は必要に応じて原文を維持する。
- 開始後は既存のジョブ進捗 UI と SSE を使い、`preparing_source`、`planning`、`authoring_slides`、`validating`、`exporting`、`uploading` を表示する。
- 成功時は「ダウンロード」と「再生成」を表示する。失敗時は失敗段階と再試行導線を表示し、既存の最新版は残す。

### 成果物

- 論文アイテムごとに最新版の PPTX だけを保持する。
- 生成中の同一論文に対する再要求は新規ジョブを作らず、既存ジョブを返す。
- 再生成は新しい成果物のアップロードと DB 更新が成功した後にだけ旧ファイルを削除する。失敗時に旧成果物を失わない。

## API・データモデル

### API

```text
POST /api/library-items/{item_id}/presentation
body: {
  preset: "reading_group" | "research_talk" | "implementation",
  audience?: "beginner" | "researcher" | "implementer",
  instruction?: string
}
→ 202 { job_id: string }

GET /api/library-items/{item_id}/presentation
→ 200 PresentationOut | null

GET /api/library-items/{item_id}/presentation/download
→ 200 application/vnd.openxmlformats-officedocument.presentationml.presentation
```

- すべて所有者チェックを行う。未所有・存在しないアイテムは既存 API と同じ `not_found` にする。
- 生成中は `POST` が既存ジョブ ID を返す。PPTX 未生成のダウンロードは `404`、鍵やモデルが利用不能な開始要求は可視的な Problem Details を返す。

### 永続化

- `presentation_artifacts` を新設し、`library_item_id` を一意にする。
- 最低限、`id`、`library_item_id`、`source_revision_id`、`generation_job_id`、`preset`、`audience`、`instruction`、`model_provider`、`model_id`、`ppt_master_revision`、`pptx_storage_key`、`generated_at`、`updated_at` を保持する。
- S3 には一時置換を避けるため、ジョブ ID を含むキーで新成果物をアップロードする。DB が新キーを指した後に旧キーを削除する。

## 生成アーキテクチャ

```text
ViewerHeader「✦ ツール」
  → POST /presentation
  → jobs.kind='presentation' (bulk queue)
  → PresentationRunner
      1. DocumentRevision + 図表アセット → source packet
      2. LLM route 'presentation' → 根拠付きスライド構成
      3. slide ごとに SVG を生成
      4. SVG 安全性検証 + ppt-master quality check
      5. ppt-master finalize / SVG-to-PPTX
      6. S3 upload → presentation_artifacts を原子的に更新
```

### 入力と根拠

- source packet は構造化本文、書誌、節見出し、数式、図表キャプション、取得済み図表アセットから作る。
- メモ、注釈、ハイライト、チャット、記事は source packet に含めない。ログにも API キーや元論文本文の不必要な複製を残さない。
- 最初の LLM 呼び出しは、各スライドの主張・使う節/図表・根拠アンカーを持つ構成を生成する。各 SVG 生成はそのスライドに必要な抜粋だけを渡す。
- 図表アセットが無い場合は、キャプションと番号を使うテキスト表現へフォールバックする。生成全体を失敗させない。
- AI 画像生成・検索は使わず、論文由来の図表のみを使う。したがって Anthropic のテキスト API キーだけでも生成できる。
- 数式は `ppt-master` の数式処理を優先する。処理できない場合は元 LaTeX を編集可能なテキストとして残し、ジョブ全体は失敗させない。

### LLM ルーティングと鍵

- `llm_task_routes`、`user_task_model_overrides`、設定スキーマ、Web のモデルルーティング UI に `presentation` 用途を追加する。
- 既存の `build_router_for_user(..., task="presentation")` と鍵ストアを利用する。運営キーと BYOK の優先順位、使用量記録、リトライ規則は既存の LLM 機構に従う。
- 既定チェーンは OpenAI と Anthropic の両方を含める。`OPENAI_API_KEY` だけなら OpenAI、`ANTHROPIC_API_KEY` だけなら Anthropic が選ばれ、両方ある場合はユーザー設定のモデルを優先する。
- API キーを `ppt-master` の `.env`、作業ディレクトリ、S3、ジョブログへ書き出さない。LLM 呼び出しは Alinea のルーターだけが担う。

### ppt-master 実行境界

- `vendor/ppt-master` を Git submoduleで管理し、v2.8.0のcommit `0c0bdaf0dd953afc2c00322e92f26dc02fc1c51f`へ固定する。
- 上流スクリプトは専用仮想環境からサブプロセス実行する。Alinea の Python 依存と混在させない。
- Alinea 側の `PresentationRunner` が上流のプロジェクト契約へ変換し、`project_manager.py`、`svg_quality_checker.py`、`total_md_split.py`、`finalize_svg.py`、`svg_to_pptx.py` を順番に呼ぶ。
- LLM が返した SVG は、外部 URL、スクリプト、イベント属性、危険な XML 構造を拒否する既存の SVG 安全性検証を通してから上流変換へ渡す。
- 作業領域はジョブ固有の一時ディレクトリに作り、成功・失敗・中断後に削除する。

## 更新運用

- `pnpm ppt-master:update` を追加する。既定では上流 `main` を fetch し、新しい submodule コミット候補を検証する。
- 更新コマンドは次を順に実行する。
  1. 現在の submodule コミットと依存関係ハッシュを記録する。
  2. 候補コミットへ切り替え、専用仮想環境へ上流 `requirements.txt` を同期する。
  3. Alinea 所有の静的フィクスチャで最小デッキを生成し、SVG 品質検査と PPTX 出力を実行する。
  4. PPTX が生成され、PowerPoint パッケージ構造を持つことを検証する。
  5. 失敗時は submodule と仮想環境を元のコミットへ戻す。成功時だけ Git 差分として残す。
- 更新後は変更された submodule ポインタと互換性試験を通常のレビュー・コミット・デプロイ経路で反映する。本番で上流 `main` を pull しない。
- 成果物メタデータには利用した `ppt_master_revision` を保存し、障害時にどの上流版が出力した PPTX か追跡可能にする。

## テスト戦略

- **source packet:** メモ・注釈・チャットを含めないこと、図表とキャプションのフォールバック、長い論文の抜粋選定を単体試験する。
- **API:** 所有者チェック、リクエスト検証、同一論文のジョブ重複防止、未生成ダウンロードの 404、最新成果物の取得を試験する。
- **worker:** Fake LLM と上流実行スタブで各 stage、SVG 拒否、品質検査失敗、旧成果物の保持、成功時の原子的な置換、S3 削除順を試験する。
- **LLM 設定:** OpenAI のみ、Anthropic のみ、両方なし、BYOK 優先、`presentation` の使用量計測・クォータを試験する。
- **Web:** ツールメニュー、3用途プリセット、聴衆変更、任意指示、SSE 進捗、成功後のダウンロードと再生成を試験する。
- **上流更新:** `ppt-master:update` の成功・失敗ロールバックを試験し、CI では固定フィクスチャによる上流互換性スモークテストを実行する。

## 受け入れ基準

- [ ] デスクトップの論文ビューアから、3用途プリセットでスライド生成を開始できる。
- [ ] 生成に論文本文・書誌・図表だけが使われ、メモ・注釈・チャットは送信されない。
- [ ] `OPENAI_API_KEY` のみ、または `ANTHROPIC_API_KEY` のみで生成可能で、モデル選択は既存の設定画面で行える。
- [ ] 正常終了時に編集可能な日本語 PPTX をダウンロードでき、再生成後は最新版だけが残る。
- [ ] 新しい生成に失敗しても、以前に成功した PPTX は失われない。
- [ ] `pnpm ppt-master:update` が上流更新、依存関係同期、スモークテスト、失敗時ロールバックを一括実行する。
