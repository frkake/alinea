# plans/ — 「訳読 / YAKUDOKU」実装計画書群の案内

> 対象読者と前提: 本ディレクトリは「訳読 / YAKUDOKU — 論文読解ワークベンチ」を実装するエンジニア全員の入口である。**docs/00〜12 が「何を作るか」(機能仕様の正)、plans/ が「どう作るか」(実装の正)** であり、最終的な見た目の正は確定デザイン 16 画面(論文読解システム デザイン.dc.html + support.js)とその抽出値(各計画書に転記済み)である。三者が食い違ったら 見た目=確定デザイン → 機能=docs → 実装詳細=plans の順で優先する。

## 1. 位置づけ

- docs/(00〜12): プロダクト仕様。エンティティ・機能・受け入れ基準を定義する。実装技術には立ち入らない。
- plans/(本ディレクトリ): docs を確定技術スタック(§4)へ落とす実装計画。DDL・API パス・型名・px 値まで確定済みで、「または」「TBD」を含まない。
- plans/09-screens/: 画面別ピクセル仕様。確定デザインの抽出値を全量転記済みで、抽出ファイルを再参照せずに実装できる。
- 各計画書は他書の識別子(テーブル名・エンドポイント・トークン名)を再定義せず参照する。所有権: DDL=02、API=03、トークン=08、ビューア共通骨格=09-screens/viewer-shell.md。

## 2. ファイル一覧

| 番号 | ファイル | 内容 |
|---|---|---|
| 00 | [00-tech-stack.md](00-tech-stack.md) | リポジトリ構成・バージョン固定・開発環境・規約・CI |
| 01 | [01-architecture.md](01-architecture.md) | 全体構成・キュー/ジョブ基盤・SSE・S3 レイアウト・認証・デプロイ |
| 02 | [02-data-model.md](02-data-model.md) | PostgreSQL 16 完全 DDL(= Alembic 初期マイグレーション) |
| 03 | [03-api.md](03-api.md) | REST API 完全仕様(全エンドポイント・スキーマ・エラー・SSE 契約) |
| 04 | [04-llm-providers.md](04-llm-providers.md) | LLM/画像プロバイダ抽象化層(`yakudoku_llm`)・ルーティング・BYOK・計測 |
| 05 | [05-ingest-pipeline.md](05-ingest-pipeline.md) | 取り込みパイプライン(arXiv 解決・パーサ・品質 A/B・ジョブ状態機械) |
| 06 | [06-translation-pipeline.md](06-translation-pipeline.md) | 翻訳(プレースホルダ・プロンプト・共有キャッシュ・品質検査・進捗) |
| 07 | [07-ai-features.md](07-ai-features.md) | チャット/要約/記事/概要図/解説図/語彙生成/提案 |
| 08 | [08-design-system.md](08-design-system.md) | packages/tokens・共通 UI コンポーネント・タイポグラフィ・ダーク対応 |
| 09 | [09-screens/](09-screens/) | 画面別実装仕様(下表) |
| 10 | [10-extension.md](10-extension.md) | ブラウザ拡張(WXT/MV3)完全設計 |
| 11 | [11-search.md](11-search.md) | PGroonga 全文検索・横断検索・ハイライト |
| 12 | [12-testing.md](12-testing.md) | テスト戦略(pytest/Vitest/Playwright/VR・トレーサビリティ表) |
| 13 | [13-work-breakdown.md](13-work-breakdown.md) | マイルストーン別実装順序(M0〜M3 の DoD とタスク分解) |

09-screens/ の構成(viewer-shell が共通骨格、他は画面固有分):

| ファイル | 画面 |
|---|---|
| viewer-shell.md | ビューア共通骨格(1a/1b/1c/2a/1h/5a 共有。ヘッダ・左レール・パネル枠・キーマップ) |
| 1a-viewer-parallel-chat.md | ビューア 対訳+チャットパネル |
| 1b-viewer-translation-annotations.md | ビューア 訳文+注釈パネル(対訳ポップ/選択メニュー) |
| 1c-viewer-dark-figures.md | ビューア ダーク+図表参照ポップオーバー+図表/参考文献 |
| 1d-dashboard.md | ダッシュボード(ホーム) |
| 1e-library-table-search.md | ライブラリ テーブル+検索ドロップダウン+一括操作 |
| 1g-finish-reading-dialog.md | 読了フロー(モーダル) |
| 1h-viewer-article-mode.md | ビューア 記事モード+全体概要図 |
| 2a-viewer-pdf-mode.md | ビューア PDF モード+情報パネル |
| 3a-extension-popup.md | 拡張ポップアップ(4 状態)+バッジ+「訳 保存」ピル |
| 4a-library-cards-notifications.md | ライブラリ カード+通知ポップオーバー |
| 4b-collection-detail.md | コレクション詳細 |
| 4c-collection-share-page.md | コレクション共有ページ(匿名・唯一の公開画面) |
| 4d-vocabulary.md | 語彙帳 |
| 4e-global-search-results.md | 横断検索結果 |
| 4f-settings.md | 設定 |
| 5a-viewer-resources.md | ビューア リソースタブ |
| mobile.md | モバイル縮退レイアウト(<768px、閲覧+ステータス変更のみ。M1) |

## 3. 読み方

- 実装開始時(全員必読): **00 → 01 → 02 → 03 → 13** の順。基盤(構成・アーキテクチャ・DDL・API)を頭に入れてから 13 で自分の担当マイルストーンのタスクを確認する。
- バックエンド機能実装時: 上記に加え担当領域の 04〜07 / 11。
- 画面実装時: **08 → 09-screens/viewer-shell.md → 該当画面ファイル** の順。ビューア系 6 画面は viewer-shell の分担表(§11)で所有コンポーネントを確認してから画面ファイルへ。
- 拡張実装時: 03 → 10 → 09-screens/3a-extension-popup.md。
- 機能仕様の根拠に遡るときは各計画書冒頭の「対象読者と前提」に列挙された docs 参照先を読む。

## 4. 計画の前提

- 技術スタック(確定。spec-decisions C 項): pnpm workspaces + Turborepo モノレポ。`apps/web`=Next.js 15(App Router)+React 19+TypeScript 5+Tailwind CSS v4 / `apps/api`=Python 3.12+FastAPI+SQLAlchemy 2+Alembic+Pydantic v2 / `apps/worker`=Python(arq) / `apps/extension`=WXT(MV3) / PostgreSQL 16+PGroonga / Redis 7 / S3 互換(dev=MinIO, prod=Cloudflare R2) / 認証=FastAPI+authlib(Google/GitHub OAuth+メールリンク、HTTPOnly セッションクッキー) / KaTeX / PDF.js / TanStack Query v5+Zustand / `packages/api-client`(OpenAPI 生成 TS クライアント) / `packages/tokens`(デザイントークン単一ソース)。
- 対象マイルストーン: docs/10 の M0(拡張で保存して、読んで、訊ける)→ M1(記録と日常運用)→ M2(記事・図・共有・語彙)→ M3(広がり)。plans 全体は M0〜M2 の全機能を確定記述し、M3 は「v2:」明示部分のみ。着手順は 13 が正。
- LLM/画像モデル ID・価格は 04 が正(2026-07-06 調査値)。モデル ID をコードに直書きしない。
- 開発シードデータ: Rectified Flow(arXiv:2209.03003)。基準ビューポート 1440×900px。

## 5. 規約

- 「決定: X。理由: …」表記: docs・デザインに無い実装詳細を計画書側で確定させた箇所。実装者はこれを仕様として扱い、独自判断で変えない。
- 識別子の正: テーブル/カラム=02、エンドポイント/型=03、トークン/CSS 変数/コンポーネント名=08、LLM タスク名=04。日本語の説明文でも識別子は英語のまま書く。
- 数値: すべて単位付き具体値(px/ms/秒/件)。デザイン由来の値は抽出値の逐語転記であり、丸め・読み替えを禁止する。
- 「⚠ 基盤への追加要求」節(05/06/07/10 等): 執筆時に基盤計画書(01〜04)へ見つかった不足・不整合の修正要求。基盤側へ反映したら要求元の節から消し込む。
- 変更手順: (1) 仕様変更はまず scratchpad/spec-decisions.md(決定リスト)に追記 → (2) docs の該当文書を更新 → (3) plans の所有計画書を更新 → (4) 参照側計画書と 12(テスト)を同期。plans だけを直して docs/decisions と乖離させることを禁止する。
