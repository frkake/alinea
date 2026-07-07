# 拡張機能ローカル実機検証(unpacked)チェックリスト

> 対象: `apps/extension`(WXT / Manifest V3)。自動 E2E(Playwright・`apps/extension/e2e/`)とは別に、
> **実ブラウザ(Chrome / Edge)へ unpacked ロードして手動確認**する手順(実行設計 E3 / docs/08 §8 の
> ストア審査前チェック)。自動 E2E がカバーしない「ツールバー実クリック」「実 arXiv ページでの動作」を
> 人手で確認する。

## 0. 前提

- Node 24.4.0 / pnpm 10.13.1 / uv / Docker(データ基盤)。
- 企業プロキシ環境ではローカル接続に `NO_PROXY=localhost,127.0.0.1` を必ず設定する。
- 拡張は開発では API オリジン `http://localhost:3000` に対して動く(`WXT_API_BASE` 既定)。

## 1. 開発サーバー一式を起動

```bash
# データ基盤(PostgreSQL / Redis / MinIO / Mailpit)
docker compose up -d --wait

# マイグレーション + シード(初回のみ)
cd apps/api && uv run alembic upgrade head && cd ../..
uv run python -m yakudoku_api.seed --sample rectified-flow --reset

# アプリ一式(web:3000 / api:8000 / worker / wxt dev）
pnpm dev
```

期待: `curl -s localhost:8000/api/healthz` が `{"status":"ok"}`。`pnpm dev` で web / api / worker /
`wxt` の dev が起動する。取り込みを実際に流す場合は BulkWorker / InteractiveWorker と
モック arXiv(任意)も起動する:

```bash
# 実 arXiv を叩かずに検証する場合(決定的モック)
uv run python -m yakudoku_llm.testing.mock_server --port 8090   # 別ターミナル
# ワーカーはモック arXiv + FakeLLM で起動する
YAKUDOKU_FAKE_LLM=1 YAKUDOKU_ARXIV_BASE_URL=http://localhost:8090/arxiv \
  uv run arq yakudoku_worker.main.BulkWorker        # 別ターミナル
YAKUDOKU_FAKE_LLM=1 YAKUDOKU_ARXIV_BASE_URL=http://localhost:8090/arxiv \
  uv run arq yakudoku_worker.main.InteractiveWorker # 別ターミナル
```

## 2. 拡張をビルド(unpacked ロード用)

```bash
# Chrome / Edge 共通の chrome-mv3 出力
pnpm --filter @yakudoku/extension build          # → apps/extension/.output/chrome-mv3
# Edge 専用ビルドが要る場合
pnpm --filter @yakudoku/extension build:edge     # → apps/extension/.output/edge-mv3
```

> `pnpm dev`(`wxt`)の HMR 出力(`.output/chrome-mv3` を dev 用に生成)を使ってもよい。ストア審査前の
> 最終確認は `build` 出力(本番相当)で行う。

## 3. Chrome へ unpacked ロード

1. `chrome://extensions` を開く。
2. 右上の **デベロッパーモード** を ON。
3. **「パッケージ化されていない拡張機能を読み込む」** をクリック。
4. `apps/extension/.output/chrome-mv3` を選択。
5. 拡張「訳読 — 論文をライブラリへ」が一覧に出て、ツールバーにアイコンが表示される。

**Edge の場合**: `edge://extensions` → 開発者モード ON → 「展開して読み込み」で同じ
`apps/extension/.output/chrome-mv3`(または `edge-mv3`)を選択する。

## 4. 手動検証チェックリスト

順に実施し、各項目にチェックする。

### 4.1 認証

- [ ] 未ログイン状態でツールバーアイコンをクリック → ポップアップに **「保存にはログインが必要です。」**
      と **「ログイン」** ボタンが出る(状態0)。
- [ ] `http://localhost:3000/login` を開き、`dev@yakudoku.test` でメールリンクログイン
      (リンクは Mailpit UI `http://localhost:8025` で確認)。ログイン後 `/library` に着地する。

### 4.2 保存(状態1 → 状態2)

- [ ] `https://arxiv.org/abs/2209.03003`(Rectified Flow)を開く。
- [ ] ツールバーアイコンをクリック → ポップアップに **書誌プレビュー**・
      **「✓ LaTeX ソースあり — 品質レベル A 見込み」**・タグ提案チップが出る(状態1)。
- [ ] ステータス3択(既定「読む予定」)・タグ・ひとことメモを入力できる。
- [ ] **Enter キー**(または保存ボタン)で保存 → 同じポップアップ内が状態2に切り替わり、
      **「✓ 書誌 → ✓ 構造化 → 翻訳中 n%」** の進捗と **「サイトで開く ↗」** が出る。
- [ ] ツールバーアイコンに **琥珀ドットのバッジ**(処理中)が付く → 完了で緑チェックに変わる。

### 4.3 パイプライン進捗 → ビューア

- [ ] 進捗が進み、`readable` 到達後に **「サイトで開く ↗」/「続きから開く ↗」** で
      `http://localhost:3000/papers/{id}` のビューアが開く。
- [ ] ビューアで **訳文** が表示される(未翻訳セクションは原文+翻訳中表示、完了分が差し替わる)。

### 4.4 既にライブラリ(状態3)

- [ ] 保存済みの `2209.03003` ページで再度ポップアップを開く → **重複保存フォームが出ず**、
      現ステータス・追加日・進捗・前回位置・**「続きから開く ↗」**・**「ステータス変更 ▾」** が出る。
- [ ] 「ステータス変更 ▾」で別ステータスに変更 → web 側ライブラリに反映される。

### 4.5 フッタ「直近の取り込み」

- [ ] ポップアップ下部に **「直近の取り込み」** が最大 3 件表示される(処理中=進捗率 / 完了=時刻)。
- [ ] 行クリックで該当ビューアが開く。

### 4.6 非対応ページ

- [ ] arXiv 以外のページ(例 `https://example.com`)でポップアップを開く → **「対応外のページ」**
      表示になり、保存フォームが出ない。

## 5. アンインストール / 再読み込み

- コード変更後は `chrome://extensions` の拡張カードで **再読み込み(⟳)** を押す(または `pnpm dev` の HMR)。
- 検証終了後は不要ならカードの「削除」でアンインストールする。

## 6. 既知の制約(M0 時点)

- 一般ページ PDF の送信(状態4)・ページ内「訳 保存」ピル・送信キューの永続再送は M0-34〜36 では
  未実装(popup は該当時に「対応外」表示)。実装後に本チェックリストへ項目を追加する。
- ツールバーアイコンの実クリックは自動 E2E では検証できないため(Playwright 制約)、本手順の 4.2 /
  4.4 のバッジ・実クリック確認が実機検証の要となる。
