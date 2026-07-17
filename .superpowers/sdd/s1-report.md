# S1 「設定の実効化」— Report

Branch: `worktree-agent-ad3818796e4ad6019`
Spec: `docs/superpowers/specs/2026-07-16-settings-effectiveness-design.md`
Plan: `docs/superpowers/plans/2026-07-16-settings-effectiveness.md`

## Status: COMPLETE (all 5 findings addressed, TDD)

## What was done

### #1 LLM provider/model selection (highest priority) — bridged via PATCH
`apps/api/src/alinea_api/routers/settings.py`: `PATCH /api/settings` now bridges `llm_routing.<task>.{provider,model}` → `user_task_model_overrides` rows on any llm_routing patch. Task-name mapping applied (`retranslation`→`retranslation_escalation`, `figure_dsl`→`overview_figure_dsl`, others 1:1; `figure_image` excluded — image route, worker-only, no user_id). model_id validated against `llm_models` (exists + enabled + provider matches) before commit; mismatch/unknown → 422 `validation_error` (no partial apply, no FK 500). Redis route cache invalidated per task after upsert. No new endpoint; PATCH extended per constraint. Response/request shape unchanged → **no SDK regen needed**.

### #2 Chat "注釈・メモを文脈に含める" — wired
`context_builder.py`: added `render_annotations_context()` (plans/07 §2.2.5 format, ≤4,000-token budget). `routers/chat.py`: loads highlight/comment annotations (bookmarks excluded) + notes, reads `user.settings.chat.include_annotations_and_notes` (default True) and threads it through `_prepare_turn` → `build_chat_request(include_annotations=..., annotations_text=...)`. Disabled → system[2] omitted and DB load skipped.

### #3 Theme toggle UI — added
`DisplaySettings.tsx`: new "テーマ" row (light/dark/system SegmentedControl). `SettingsClient.tsx`: `onThemeChange` = `setTheme` (immediate `data-theme`+cookie via ThemeProvider) + `PATCH {display:{theme}}` with optimistic rollback (same pattern as accent/body_font).

### #4 Account settings — expanded
`AccountSettings.tsx`: added (a) signed-in identity (email + OAuth/email provider) from `authMe`, (b) always-visible quota (5 counters used/limit, "無制限(BYOK)" when BYOK active — 09-nonfunctional §3.5), (c) logout button (`authLogout`→/login), (d) delete-account entry with confirm-word (`delete`) Modal → `authDeleteAccount({confirm:"delete"})`→/login. `SettingsClient.tsx` adds the queries/mutations. Destructive/logout controls hidden on mobile (readOnly). All SDK fns pre-existing.

### #5 Extension toggle — documented as informational (pragmatic choice)
`ExtensionSettings.tsx`: clarified copy that the web toggle is a preference; actual enablement happens in the extension popup (permission request is a user gesture the web app cannot perform). No schema/behavior change. See "Decisions needing user".

## Commit hashes
- (see git log on branch; single feature commit — hash below)

## Test summary
API: `uv run pytest apps/api -q` → **600 passed, 2 skipped** (full suite). Web: `vitest run src/components/settings + ThemeToggle` → **36 passed**. web `tsc --noEmit` clean; eslint clean on touched; ruff+mypy clean on touched API files.

## Decisions needing user
1. **#5 direction**: chose "web toggle is informational" (extension popup owns real enablement) over "extension reads the account setting", because enabling requires a user-gesture permission request the web app can't proxy. If you want a single source of truth, the extension could read `settings.extension.arxiv_inline_button` via authenticated fetch and *prompt* the user in-popup to grant permission — larger change, flagged for your call.
2. **#1 validation**: model overrides ARE validated against `llm_models` (enabled + provider match); provider/model mismatch now returns 422. This makes the previously-passing `test_patch_nested_llm_routing_merge` (which set `chat.model=gpt-5.5` under default provider `anthropic`) invalid, so I updated it to a provider-consistent model. Confirm this stricter validation is desired (it prevents FK 500s and nonsensical routes).

## Blocking concerns
- **#1 runtime effect is partial by architecture.** `user_task_model_overrides` is only consumed by `DbRouteStore.resolve_chain(task, user_id=...)`, which is reached only on API-path tasks that pass `user_id`: **chat** and **summary** (まとめてメモ化, `notes.py`). Worker-path tasks (translation, retranslation_escalation, article, overview_figure_dsl, vocab, explainer_image) build a single shared router at worker startup via `build_task_router(session)` with **no user_id** (`apps/worker/src/alinea_worker/bootstrap.py`) and never rebuild per-user per-job. So the bridge correctly persists overrides for ALL text tasks, but only chat/summary model selection takes effect today. Making translation/article/vocab/figure_dsl per-user requires worker-side per-job router construction — out of scope for S1, needs a follow-up. The overrides written now will "light up" automatically once the worker respects them.
- No other blockers. Postgres/Redis via docker were available; SDK unchanged.
