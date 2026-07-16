# Feature S1: 設定の実効化 — Plan (TDD)

Date: 2026-07-16
Design: `docs/superpowers/specs/2026-07-16-settings-effectiveness-design.md`

各ステップは Red → Green → Refactor。API は `uv run pytest apps/api -q`、Web は `pnpm --filter @alinea/web test`。

## Step 1 — settings→overrides ブリッジ (finding #1, D1)

**Files**: `apps/api/src/alinea_api/routers/settings.py`(+ヘルパ), `apps/api/tests/test_settings_api.py`

1. (Red) `test_settings_api.py` に追加:
   - `PATCH {llm_routing:{chat:{provider:"google",model:"gemini-3.5-flash"}}}` 後、`user_task_model_overrides(user_id,'chat')` に `gemini-3.5-flash` が入る。
   - `retranslation`→task `retranslation_escalation`、`figure_dsl`→`overview_figure_dsl` の名前対応。
   - 未知/disabled/provider不一致 model は 422 `validation_error`、overrides は書かれない。
   - モデルだけ変えた PATCH でも(provider は既定のまま)overrides が入る。
   - 2 回目 PATCH で upsert(重複 PK にならない)。
2. (Green) settings.py の `update_settings` を拡張:
   - merged 検証後、`body`(patch)に含まれる `llm_routing.<task>` のうち text タスクを走査。各 task で `merged.llm_routing[task].model` を解決対象に、`_validate_and_upsert_override(db, user_id, task_key, provider, model)`。
   - `_TASK_KEY_TO_ROUTE = {"translation":"translation","retranslation":"retranslation_escalation","chat":"chat","summary":"summary","article":"article","vocab":"vocab","figure_dsl":"overview_figure_dsl"}`。`figure_image` は対象外。
   - 検証: `SELECT provider FROM llm_models WHERE id=:model AND enabled=true`。None または provider 不一致なら `ProblemException("validation_error", ...)`(commit 前)。
   - upsert: `INSERT ... ON CONFLICT (user_id,task) DO UPDATE SET model_id=..., updated_at=now()`。
   - キャッシュ無効化: `DbRouteStore(db, get_redis()).invalidate(route_task, user_id)`(cache は best-effort; redis 依存を避けるなら route_store の invalidate は cache None 安全)。
3. (Refactor) ヘルパを settings.py 内に閉じる。

## Step 2 — チャット注釈・メモの文脈注入 (finding #2, D2)

**Files**: `apps/api/src/alinea_api/chat/context_builder.py`(整形関数を追加 or chat.py 内), `apps/api/src/alinea_api/routers/chat.py`, `apps/api/tests/test_chat.py`

1. (Red) test_chat.py:
   - annotations(highlight+comment)と note を持つ item で、`include_annotations_and_notes=true`(既定)のとき送信 → assistant 生成に使われた LLMRequest の system に `# ユーザーの注釈・メモ` が含まれる。false のとき含まれない。
   - FakeLLMProvider は受け取った request を記録できる(既存 factory 注入で system を検査)。難しければ整形関数 `render_annotations_context()` の単体テストで代替(§2.2.5 形式検証)。
2. (Green):
   - `context_builder.py` に `render_annotations_context(annotations, notes, *, index/validator) -> str`(§2.2.5)を追加。予算 ≤4,000 トークン切詰め。
   - `chat.py._prepare_turn` に `settings`(既に引数)から `include = settings...`? — 注意: `_prepare_turn` は `SettingsDep`(ApiSettings)であって user settings ではない。**user settings は `user.settings`**。`send_message`/`regenerate` は `user: CurrentUser` を持つので、そこから `include_annotations_and_notes` を解決して `_prepare_turn` に渡す(bool 引数追加)。
   - include=true のとき `_load_annotations_and_notes(db, item)` で DB から取得し整形、`build_chat_request(..., include_annotations=True, annotations_text=...)`。
3. (Refactor) 整形は context_builder に集約。

## Step 3 — テーマ切替 UI (finding #3, D3)

**Files**: `apps/web/src/components/settings/DisplaySettings.tsx`, `apps/web/src/components/settings/SettingsClient.tsx`, tests(`SettingsClient.test.tsx`)

1. (Red) SettingsClient.test.tsx: display カテゴリでテーマ「ダーク」を選ぶと `settingsUpdate({body:{display:{theme:"dark"}}})` が呼ばれ、`data-theme=dark`。
2. (Green):
   - DisplaySettings に `onThemeChange` prop + 先頭に `SettingsControlRow title="テーマ"` + `ThemeToggle`(既存)を描画。ただし `ThemeToggle` は内部で `useTheme().setTheme` を呼ぶので、永続化のため DisplaySettings 側で `useTheme` の setTheme をラップするか、`ThemeToggle` を使わず `SegmentedControl` を直接置いて `onThemeChange`(= setTheme + patch)。→ **SegmentedControl 直置き**で SettingsClient の optimistic+rollback パターンに合わせる(accent/body_font と同型)。
   - SettingsClient に `onThemeChange`(setTheme→patch→onError で rollback)を追加し DisplaySettings へ渡す。
3. (Refactor) 型 `ThemePrefValue` を利用。

## Step 4 — アカウント設定拡充 (finding #4, D4)

**Files**: `apps/web/src/components/settings/AccountSettings.tsx`(+新規サブ行 or セクション), `apps/web/src/components/settings/SettingsClient.tsx`, tests

1. (Red) SettingsClient.test / 新規 test:
   - account カテゴリで email 表示・クォータ used/limit 表示・「ログアウト」ボタン・「アカウントを削除」導線が描画される。
   - ログアウトクリックで `authLogout` 呼び出し。削除→モーダル→確認で `authDeleteAccount({body:{confirm:"delete"}})`。
2. (Green):
   - SettingsClient に `authMe`/`settingsGetQuota` の useQuery(account のとき enabled)と `authLogout`/`authDeleteAccount` の mutation を追加。props を AccountSettings に渡す。
   - AccountSettings 先頭に「アカウント」セクション(identity + logout + delete)と「今月の利用状況」セクション(quota)。削除は `Modal` + 合言葉入力。
   - readOnly(モバイル)では削除/ログアウトを隠すか無効化。
   - api-client のモックに `authMe`/`settingsGetQuota`/`authLogout`/`authDeleteAccount` を追加(既存 test の vi.mock 拡張)。
3. (Refactor) quota 表示ヘルパ。

## Step 5 — 拡張トグルの明確化 (finding #5, D5)

**Files**: `apps/web/src/components/settings/ExtensionSettings.tsx`, test(既存 extension test 更新)

1. (Red/Green) 説明文に「実際の有効化はブラウザ拡張のポップアップから」明記。トグル挙動は保持(既存テストは name で当てているので説明文追加は破壊しない)。既存 test が通ることを確認。

## Step 6 — 検証・SDK・コミット

- `uv run pytest apps/api -q`(触れた領域中心。全体でも可)。
- `pnpm --filter @alinea/web test`。
- SDK 変更なし(エンドポイント不変)→ 再生成不要。念のため `pnpm --filter @alinea/web tsc`/lint が通ることを確認。
- コミット(ブランチ `worktree-agent-ad3818796e4ad6019`)。
