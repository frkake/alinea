# Feature S1: 設定の実効化 — Design

Date: 2026-07-16
Status: Design
Scope: `apps/api`, `apps/web`, `apps/extension`, `packages/api-client`

## 1. Problem

いくつかのユーザー設定は DB(`users.settings` JSONB)へ保存されるが、実際の挙動へ反映されていない(save-but-no-effect)。監査で確認された 5 つのギャップを実効化する。

## 2. Verified findings (audit)

1. **LLM provider/model 選択が無効(最優先)**
   - UI は `PATCH /api/settings` で `users.settings.llm_routing.<task>.{provider,model}` を書く(`apps/api/src/alinea_api/routers/settings.py`)。
   - しかし実行時のルート解決 `DbRouteStore`(`apps/api/src/alinea_api/llm/route_store.py:59-70`)は **`user_task_model_overrides` テーブル**を読む。本番でこのテーブルへ書く経路が存在しない(migration 0002 とテストのみ)。
   - よって translation/retranslation/chat/summary/article/vocab/figure_dsl のモデル切替はサイレントに無効。`llm_routing.overview_figure_raster_mode` だけが実際に消費される(`apps/worker/.../generate_overview_figure.py:307-310`)。
   - **重要な構造的注意**: `user_task_model_overrides` を読むのは `DbRouteStore.resolve_chain(task, user_id=...)` のみ。API 経路の `chat` と `summary`(まとめてメモ化・`notes.py`)は `build_router_for_user(..., user_id, ...)` を通るので **user_id を渡す**が、worker 経路(translation / retranslation_escalation / article / overview_figure_dsl / vocab / explainer_image)は起動時に `build_task_router(session)`(user_id なし)で **共有 1 本**を作り `ctx["router"]` に載せるだけで、ジョブ実行時にユーザー別のルータを組まない(`apps/worker/src/alinea_worker/bootstrap.py`)。したがって overrides テーブルへ橋渡ししても、実効化されるのは **API 経路の chat・summary のみ**。worker 経路タスクの per-user モデル選択を実効化するには worker 側の大改修(ジョブごとの per-user ルータ構築)が必要で、本タスクのスコープ外。

2. **チャット「注釈・メモを文脈に含める」トグル未接続**
   - `context_builder.build_chat_request` は `include_annotations` / `annotations_text` を受け取れるが、`apps/api/src/alinea_api/routers/chat.py:436` の `build_chat_request(...)` はどちらも渡していない。`settings.chat.include_annotations_and_notes` が実効化されていない(system[2] 注釈・メモが常に空)。

3. **ライト/ダークテーマ切替 UI が無い**
   - `DisplaySettings.tsx` はアクセント/書体/サイズのみ。`ThemeToggle.tsx` は存在するが未描画。`ThemeProvider` は `data-theme` + cookie を既にサポート、`display.theme` はスキーマにある。表示カテゴリにテーマ(light/dark/system)コントロールを追加する。

4. **アカウント設定の欠落**
   - `AccountSettings.tsx` は BYOK とモデルルーティングのみ。09-nonfunctional §3.5「クォータ残量は設定画面に常時表示」を含め、以下が無い: サインイン中の identity(email/OAuth)表示・ログアウト・アカウント削除導線・クォータ残量表示。API(`GET /api/settings/quota`・`GET /api/auth/me`・`POST /api/auth/logout`・`DELETE /api/auth/account`)と SDK 関数はすべて存在。

5. **拡張トグル未接続**
   - Web の `settings.extension.arxiv_inline_button` は保存されるが消費先が無い。拡張は独自の `browser.storage.local` キー `settings:arxivPillEnabled`(`apps/extension/.../states/Settings.tsx`)を使う。Web トグルは権限要求(ユーザージェスチャー)を伴えず、拡張の権限/コンテンツスクリプト登録を代行できない。

## 3. Decisions

### D1 — settings→overrides ブリッジ(PATCH 拡張)
`PATCH /api/settings` を拡張し、`llm_routing.<task>.model` が更新されたとき、対応する `user_task_model_overrides` 行を upsert する。制約どおり新規エンドポイントは作らず PATCH を拡張する。

- タスク名の対応(settings key → `llm_task_routes.task` / overrides.task):
  - `translation`→`translation`、`retranslation`→`retranslation_escalation`、`chat`→`chat`、`summary`→`summary`、`article`→`article`、`vocab`→`vocab`、`figure_dsl`→`overview_figure_dsl`。
  - `figure_image` は画像ルート(`explainer_image`)。既定チェーンは 1 本(google のみ)で overrides は既定 UX に影響が薄く、worker(image_router も user_id なし)経由のため実効化不可 → **今回はブリッジ対象外**(raster_mode だけが実効)。
- **model_id 検証**: overrides.model_id は `llm_models(id)` への FK。存在しない model_id を挿入すると FK 違反で 500 になる。したがって upsert 前に `llm_models` に存在し `enabled=true` かつ provider が一致することを検証する。不一致は 422 `validation_error`(既存の値域違反と同じ扱い)。
- overrides は「既定チェーンからの先頭移動」意味論(`DbRouteStore._base_chain`)なので、選択が既定チェーンに無いモデルでも先頭挿入され機能する。
- 保存後は Redis ルートキャッシュ(`llm:route:{task}:{user_id}`)を無効化する(`DbRouteStore.invalidate`)。無効化しないと最大 60 秒古い解決が残る。
- **実効範囲を正直に扱う**: 上記の worker 経路制約により、ブリッジは overrides テーブルへ確実に書くが、ランタイム反映は chat・summary のみ。translation 等 worker タスクの per-user 反映は別タスク(report で flag)。overrides への書き込み自体は将来の worker 対応で自動的に効くため、今書くのは正しい前進。

### D2 — チャット注釈・メモの文脈注入
`chat.py` で `settings.chat.include_annotations_and_notes` を読み、true のとき library_item のハイライト/コメント注釈とメモを plans/07 §2.2.5 形式に整形して `build_chat_request(include_annotations=..., annotations_text=...)` へ渡す。

- 整形フォーマット(§2.2.5 逐語):
  ```
  # ユーザーの注釈・メモ(参考。回答の根拠は本文のみ)
  - ハイライト(色ラベル) [blk|位置] "quote"
  - コメント [blk|位置] "quote"(コメント: body)
  - メモ: (タイトル) 本文冒頭500文字…
  ```
- 予算 ≤4,000 トークン(§2.2.1)。超過分は切詰め。annotation の位置表記は revision の block index から `derive_display` 相当で導出(evidence validator の `with_display` を再利用)。
- annotations は `annotations` テーブル(kind=highlight/comment/bookmark)。bookmark は quote を持たないため除外、highlight/comment のみ。notes は `notes` テーブル。
- `include_annotations_and_notes=false` のときは annotations_text を組まない(トークン節約・呼び出しをスキップ)。

### D3 — テーマ切替 UI
`DisplaySettings.tsx` にテーマ行を追加し、`ThemeToggle`(既存)を描画。`SettingsClient` が `onThemeChange` を渡し、`ThemeProvider.setTheme` で即時 `data-theme`+cookie 反映しつつ `PATCH {display:{theme}}` で永続化(accent/body_font と同じ optimistic + rollback パターン)。

### D4 — アカウント設定の拡充
`AccountSettings.tsx` の先頭に「アカウント」セクションを追加:
- identity: `authMe` の email + providers(OAuth/メール)を表示。
- クォータ残量: `settingsGetQuota` の 5 カウンタを used/limit で表示(常時表示。BYOK 有効時は「無制限」表記)。09-nonfunctional §3.5。
- ログアウト: `authLogout` → ログインへ遷移。
- アカウント削除導線: 確認モーダル(合言葉 `delete`)→ `authDeleteAccount({confirm:"delete"})` → ログインへ。既存 `Modal` を使用。
- モバイル(readOnly)では削除・ログアウトの破壊的操作を隠すか無効化(既存 readOnly 方針に合わせる)。

SDK 関数(`authMe`/`authLogout`/`authDeleteAccount`/`settingsGetQuota`)は既存。エンドポイント変更なしのため **SDK 再生成は D1 が PATCH のレスポンス/リクエスト shape を変えない限り不要**。D1 は既存 body/response の形を変えないので再生成不要。

### D5 — 拡張トグルの方向(pragmatic)
**Web トグルを情報表示にする**方向を採る(拡張が権限要求主体で、Web からの実登録は不可のため)。
- 変更点: `ExtensionSettings.tsx` の説明文に「実際の有効化はブラウザ拡張のポップアップから行う」旨を明記(トグルは自分の好みメモとして保存され続ける=既存挙動維持)。破壊的変更やスキーマ変更はしない。
- 代替(拡張が account setting を読む)は、拡張が権限要求を伴うため単独では有効化を完了できず、二重の真実源になる。よって不採用。report で flag。

## 4. Non-goals

- worker 経路タスク(translation 等)の per-user モデル override 実効化(worker 大改修が必要)。
- font_size_px/line_height/content_width_px のビューア実描画反映(別スコープ)。
- 圧縮モード(§2.2.3)実装。

## 5. Test strategy

- API: `uv run pytest apps/api -q`(実 PostgreSQL・docker 稼働確認済み)。
  - PATCH→overrides upsert / model_id 検証 422 / キャッシュ無効化。
  - chat 注釈注入(include=true/false で system[2] の有無)。
- Web: `pnpm --filter @alinea/web test`。
  - DisplaySettings テーマ行、AccountSettings identity/quota/logout/delete。
- 既存テスト(test_settings_api / test_llm_settings / SettingsClient.test / settings.test)を壊さない。
