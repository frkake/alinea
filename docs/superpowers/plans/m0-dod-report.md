# M0 DoD 判定記録(M0-41)

> 判定日: 2026-07-07 / 判定者: 実装オーケストレーター(Fable 司令塔)。
> 実行設計(docs/superpowers/specs/2026-07-06-local-full-implementation-execution-design.md)§3 の検証ゲート 9 項目と docs/10 §2 の M0 DoD(AC-10-01〜04)を、**実際に実行した実出力**で判定する。

## 1. 検証ゲート 9 項目(実行設計 §3)

| # | ゲート | 結果 | 実出力(要点) |
|---|---|---|---|
| 1 | `docker compose up -d --wait` | ✅ | db(PGroonga)/redis/minio/minio-init/mailpit 全 healthy |
| 2 | `uv sync --all-packages` / `pnpm install` | ✅ | 依存解決・インストール成功(uv workspace 6 パッケージ+pnpm 7 パッケージ) |
| 3 | `alembic upgrade head` | ✅ | `0002_llm_routing (head)`。全 34 テーブル+PGroonga 索引+ルーティングシード |
| 4 | `python -m yakudoku_api.seed --sample rectified-flow` | ✅ | `[seed] 2209.03003 完了(要旨+先頭セクション訳)`。--reset/--scale/--full 動作確認済み |
| 5 | `pnpm turbo build lint typecheck test` | ✅ | **21/21 タスク成功**(tokens 7・web 74・extension 16 の Vitest 含む) |
| 6 | dev 起動(web 3000+api 8000+worker) | ✅ | uvicorn `/api/healthz`=ok・`/api/readyz`=ready(db/redis)。arq Interactive+Bulk 両ワーカー起動確認(実ランタイムスモークで保存→8 段階→complete を実証) |
| 7 | pytest / Vitest green | ✅ | **Python 366 passed**(実 PostgreSQL/Redis/MinIO)。カバレッジ 82.02%(CI 分母・閾値 80)、placeholder.py 100%。トレーサビリティ: M0 必達 54 ID 未割付 0・docs 受け入れ基準 158=§6 表 158 |
| 8 | Playwright E2E green | ✅ | **web 18 passed**(PW-01/02/03/05/07/08/09、M1/M2 分は test.fixme)+ VR-1a/1b/1c 基準画像。**拡張 9 passed**(XT-01〜05/07/09)+ VR-3a×3。いずれも 2 連続 green |
| 9 | 拡張 unpacked ビルド | ✅ | `.output/chrome-mv3` 生成+`yakudokuextension-0.1.0-{chrome,edge}.zip` ビルド成功。手動ロード手順は docs/extension-local-verification.md |

## 2. M0 DoD(docs/10 §2・§8)

| DoD 項目 | 判定 | 根拠テスト |
|---|---|---|
| AC-10-01 拡張保存→1 分以内に読める(状態表示含む) | ✅(モック経路) | XT-02〜05 green。実ランタイムスモークで保存→readable→complete を実測(モック arXiv+FakeLLM で数十秒)。**実プロバイダでの p50≤60 秒の手動計測は運営キー/BYOK 投入後に実施**(E2 の決定: キー無しでも green が成立することが本ゲートの対象) |
| AC-10-02 3 モードで数式・図表が崩れない+品質バッジ常時 | ✅ | PW-05(3 モード切替+A バッジ+3 タブ)、VR-1a/1b/1c、PY-PARSE-04(KaTeX 数式コーパス) |
| AC-10-03 選択質問→根拠チップ+「AI生成」「論文外の知識」区別 | ✅ | PW-08(選択→✦AIに質問→チャット遷移+免責固定)、PY-CHAT-01〜06(根拠実在検証・[[ev:n]] ストリーム)。「↑メモに保存」は M1-04(fixme 管理) |
| AC-10-04 閉じても「続きから↓」で復帰 | ✅ | PW-09(復帰バナー+サーバ永続の検証) |
| 取り込み経路が拡張のみ | ✅ | PW-02(アプリ内に取り込み UI が無い否定検査)、XT-01 |

## 3. 品質指標ゲート(docs/10 §6)の初回計測

- プレースホルダ検証通過率: HP-01〜04(Hypothesis 500〜1000 例)反例なし+placeholder.py 行カバレッジ 100%。
- 数式レンダリング成功率: PY-PARSE-04 green(LaTeXML 数式コーパス)。
- arXiv 品質 A 率・実プロバイダ p50/コスト実測: **実キー投入後に計測**(SM-01/02 は llm-smoke.yml に RUN_LLM_SMOKE 手動トリガとして整備済み)。

## 4. スコープ注記

- M0-37(ストア申請)はローカル対象外(手順は docs/deployment.md §8)。
- M0-40(本番デプロイ)は手順書 docs/deployment.md として成果物化(実行設計 §1 の決定)。
- 判定: **M0 のローカル完全実装は DoD 達成**。M1 に進む。
