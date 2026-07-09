# M2 DoD 判定記録(M2-18)

> 判定日: 2026-07-08 / 判定者: 実装オーケストレーター(Fable 司令塔・実装 Sonnet 5)。
> 実行設計(docs/superpowers/specs/2026-07-06-local-full-implementation-execution-design.md)§3 の検証ゲート 9 項目と docs/10 §4・§8 の M2 DoD(AC-10-08〜11 ほか)を、**実際に実行した実出力**で判定する。

## 1. 検証ゲート 9 項目(実行設計 §3)

| # | ゲート | 結果 | 実出力(要点) |
|---|---|---|---|
| 1 | `docker compose up -d --wait` | ✅ | db(PGroonga)/redis/minio/mailpit 全 healthy |
| 2 | `uv sync --all-packages` / `pnpm install` | ✅ | uv 105 packages / pnpm frozen-lockfile Done。workspace は py-core/llm/figures/api/worker + web/extension/tokens/api-client |
| 3 | `alembic upgrade head` | ✅ | `0002_llm_routing (head)`(M2 は新規マイグレーション不要 — 全 34 テーブルが M0 で投入済み) |
| 4 | `python -m alinea_api.seed --sample rectified-flow --reset` | ✅ | `[seed] 2209.03003 完了` |
| 5 | `pnpm turbo build lint typecheck test` | ✅ | **21/21 タスク成功**(web 585・extension 49・figures 含む Vitest) |
| 6 | dev 起動(web+api+worker+モック) | ✅ | Playwright webServer が全スタックを自己起動し全 E2E がこの実スタックを通過 |
| 7 | pytest + カバレッジ | ✅ | **Python 904 passed / 3 skipped(SM-03/04 は RUN_LLM_SMOKE ゲートで既定 skip)**、2 連続でフレークなし。総合カバレッジ **80.47%**(閾値 80)、placeholder.py 100%。トレーサビリティ: M0/M1/M2 必達 108 ID 未割付・未実装 0(PASS)・docs 受け入れ 158=§6 表 158 |
| 8 | Playwright E2E | ✅ | **web 54 passed / 9 fixme-skip / 0 failed**(M2: PW-13/15/16/20/21 + PW-04/05 の fixme 解除)。**拡張 14 passed / 1 skip**(XT-03 コレクション欄含む)。VR: M2 の 1e/1h/4b/4c/4d/5a 追加(VR-1g/5a は根本原因解消のうえ 3 連続 green) |
| 9 | 拡張 zip ビルド | ✅ | `alineaextension-0.1.0-chrome.zip` / `-edge.zip` 生成成功 |

## 2. M2 DoD(docs/10 §4・§8)

| 項目 | 受け入れテスト | 結果 |
|---|---|---|
| AC-10-08 記事モードで読み返せ、概要図(SVG・版管理)がダウンロードできる | PW-13, PY-FIG-01〜03, PY-ART-01 | ✅ 全 green。概要図はバイト決定的 SVG(ゴールデン照合) |
| AC-10-09 共有リンクをアカウント不要・noindex の共有ページで開ける | PW-15, PY-SHR-01〜03 | ✅ 全 green。匿名 API+ライセンス縮退+X-Robots-Tag noindex |
| AC-10-10 「語彙に追加」した語が SRS 復習に現れ「原文で見る→」で戻れる | PW-20, PY-VOC-06, PY-VOC-07 | ✅ 全 green。SRS 段階 1〜5・間隔 1/3/7/14/30 日 |
| AC-10-11 LaTeX 由来(品質 A 主経路)で取り込まれ、既存 B が A へ昇格 | PY-PARSE-02, PY-ING-07, PW-11(LaTeX 経路) | ✅ 全 green。取得優先 LaTeX>HTML>PDF・B→A 昇格提案+apply |
| 記事の公開 UI が存在しない(A17・v2 送り) | PW-13 の否定検査 | ✅ green(公開 UI 不在を明示検査) |

## 3. 特記事項

- **fixme-skip 9 件**: すべて M3 スコープ(他サイトアダプタ・OCR・Anki 等)または環境制約(拡張の `chrome.permissions.request` 実ユーザージェスチャ・YouTube 実ネットワーク)であり、M2 スコープの skip は 0。
- **M2 中に検出・修正した主な実バグ**:
  - **PGroonga stemming がプランナ依存**(seq scan で TokenFilterStem を失い語形変化検索が 0 件)→ 全文検索演算子を index 名つき `pgroonga_full_text_search_condition` 形式へ全置換(横断検索・論文内検索・用語集)。
  - **記事/図/語彙/エクスポート/glossary/retranslate ジョブが arq wakeup を送っておらず** worker から不可視だった(`enqueue` は jobs 行を作るのみ)→ wakeup 配線を追加。
  - **CC BY-SA の SA 表示欠落・CC BY-ND フラグの wire 層脱落**(転載マトリクス)→ 補完。
  - **FakeLLM 要約に序数の数字**が含まれ `_summary_numbers_ok` の検証が乱数 arXiv ID と衝突 → 数字を排し決定化(VR-1g 安定化)。
  - **mock e-print が最小 stub のみ**で LaTeX 主経路の E2E 本文が貧弱 → LaTeXML HTML と同一論理構造の LaTeX ソースへ拡充。
- **カバレッジ計測**: coverage.py の async 過小報告アーティファクト(M1 で実証済み)は継続。80.47% は実カバレッジの下限。

## 4. 判定

**M2 DoD 達成。**M2-01〜17 の全成果物・全受け入れテストが実出力で green。リリースタグ `m2-complete` を打つ。これにより **M0+M1+M2(v1 全スコープ)の実装が完了**した(M3 は docs/10 §5 のとおり v1 スコープ外)。
