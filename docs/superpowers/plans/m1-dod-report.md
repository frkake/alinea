# M1 DoD 判定記録(M1-25)

> 判定日: 2026-07-08 / 判定者: 実装オーケストレーター(Fable 司令塔・実装 Sonnet 5)。
> 実行設計(docs/superpowers/specs/2026-07-06-local-full-implementation-execution-design.md)§3 の検証ゲート 9 項目と docs/10 §3・§8 の M1 DoD(AC-10-05〜07 ほか)を、**実際に実行した実出力**で判定する。

## 1. 検証ゲート 9 項目(実行設計 §3)

| # | ゲート | 結果 | 実出力(要点) |
|---|---|---|---|
| 1 | `docker compose up -d --wait` | ✅ | db(PGroonga)/redis/minio/mailpit 全 healthy(Up 30h) |
| 2 | `uv sync --all-packages` / `pnpm install` | ✅ | uv 104 packages checked / pnpm frozen-lockfile Done |
| 3 | `alembic upgrade head` | ✅ | `0002_llm_routing (head)` |
| 4 | `python -m yakudoku_api.seed --sample rectified-flow --reset` | ✅ | `[seed] 2209.03003 完了(要旨+先頭セクション訳)` |
| 5 | `pnpm turbo build lint typecheck test` | ✅ | **21/21 タスク成功**(web 368・extension 49 Vitest 含む) |
| 6 | dev 起動(web 3000+api 8000+worker+モック 8090) | ✅ | Playwright webServer で全スタック起動・全 E2E がこの実スタックを通過 |
| 7 | pytest / Vitest green + カバレッジ | ✅ | **Python 599 passed**(2 連続)。カバレッジ **81.15%**(CI 同一コマンド・閾値 80)、placeholder.py 100% 維持。トレーサビリティ: M0/M1 必達 73 ID 未割付・未実装 0(PASS) |
| 8 | Playwright E2E green | ✅ | **web 38 passed / 13 fixme-skip / 0 failed**(PW-04〜22 の M1 分有効化・PW-08 メモ保存解除・PW-11 通知経由昇格含む)。**拡張 13 passed / 1 skip**(XT-06/08/10 含む)。VR 基準画像: 1d/1g/2a/4a/4e/4f/3a-4 追加(VR-1g は根本原因解消のうえ 6 連続 green 確認済み) |
| 9 | 拡張 unpacked/zip ビルド | ✅ | `yakudokuextension-0.1.0-chrome.zip` 生成成功 |

## 2. M1 DoD(docs/10 §3・§8)

| 項目 | 受け入れテスト | 結果 |
|---|---|---|
| AC-10-05 朝のトリアージ+読了フロー(ステータスは提案のみ=P6) | PW-18, PW-19, PY-LIB-05, PY-NTF-02 | ✅ 全 green |
| AC-10-06 横断検索(日英クロス・ヒット源明示)で発見 | PW-14, PY-SRCH-01〜05, PF-06 目標 | ✅ 全 green。実測 `GET /api/search` ×30(シードコーパス・in-process ASGI+実 PGroonga): **p50=11ms / p95=233ms**(目標 p50 1s / p95 3s) |
| AC-10-07 arXiv にない PDF を品質 B バッジつきで読め、PDF モードで突き合わせ | PY-ING-04, PW-12, XT-06 | ✅ 全 green |
| リビジョン昇格が提案のみで自動適用されない | PW-11, PY-ING-07 | ✅ 全 green(apply はユーザーの明示クリックのみ。通知 action=apply → adopt_on_complete reingest 配線済み) |
| モバイルは閲覧・ステータス変更のみ(docs/00 S1) | PW-22 | ✅ green(390×844) |

## 3. 特記事項(判定に含めた既知事項)

- **fixme-skip 13 件の内訳**: M2 スコープの解除待ち(PW-05 の 5 モード=M2-17、PW-04 の保存フィルタ/一括操作=M2-14/17、XT-03 のコレクション欄=M2 ほか)であり、plans/13 §1.3 の決定(fixme 先行マージ・担当マイルストーンで解除)に従う。M1 スコープの skip は 0。
- **カバレッジ計測の注意**: coverage.py 7.15(sys.monitoring)が ASGITransport 経由の非同期ルータで await 直後の行を過小報告するアーティファクトを 2 レーンが独立に実証(dashboard.py・jobs.py)。81.15% は実カバレッジの下限。CI で `COVERAGE_CORE=ctrace` の実験を followup として推奨。
- **M1 中に検出・修正された主な実バグ**(検証タスクの成果): ログイン後リダイレクトが auth.py 側で /library のまま/`_PDF_KINDS` 欠落で PDF モード恒久無効/ingest check が kind=pdf を返さず拡張の状態 4 が到達不能/全訳済み論文の reingest が完了しない/シード chat.json の根拠参照が 0 始まりで根拠チップが常に脱落/JobStore expire_all による全翻訳ジョブ MissingGreenlet(M0 期)/annotations.quote 生成列の Computed マーカー欠如。
- **通知の完全性**: translation_complete は当初どこからも発火していなかったため、worker 側 notify.py を新設し finalize 両経路(インライン/arq)へ配線。job_id 単位 1 回限り・opt-out 対応を完全経路テストで固定。

## 4. 判定

**M1 DoD 達成。**M1-01〜26 の全成果物・全受け入れテストが実出力で green。リリースタグ `m1-complete` を打つ。
