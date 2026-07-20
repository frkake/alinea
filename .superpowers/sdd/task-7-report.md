# Task 7 Report: Finish Generated Client Migration + Mock Server Evidence

## What Was Implemented

### A. SDK Migration (`@alinea/api-client`)

1. **`apps/web/src/lib/resources-api.ts`** — replaced hand-written `fetch()` wrapper with `@alinea/api-client` SDK calls (`resourcesList`, `resourcesCreate`, `resourcesUpdate`, `resourcesDelete`, `resourcesRefreshMeta`, `resourcesSuggestionAccept`, `resourcesSuggestionDismiss`). Kept `ResourceApiError` class intact (callers use `instanceof` + `.status`). Added `throwIfError()` helper that maps SDK error responses to `ResourceApiError`. Added `toResourceLink()` and `toResourceListResponse()` mappers that coerce each optional nullable field with `?? null`.

2. **`apps/web/src/components/collections/api.ts`** — replaced hand-written `fetch()` wrapper with `@alinea/api-client` SDK calls for all 12 collection endpoints. Kept `ApiError` class intact (callers use `instanceof` + `.code`). Added `toApiError()` + `throwIfError()` helpers. Added `toCollectionEntry()` and `toCollectionDetail()` mappers that coerce each optional nullable field with `?? null`.

3. **`apps/web/src/hooks/use-reading-session.ts`** — imported `settingsGet` from `@alinea/api-client` to replace hand-written settings fetch. Kept direct `fetch()` for heartbeat (`keepalive: true`) and `sendBeacon` path (SDK cannot provide these low-level features).

4. **`apps/web/src/app/(public)/c/[token]/fetch-share.ts`** — kept direct `fetch()` with `next: { revalidate: 60 }` (SDK's `@hey-api/client-fetch` does not support Next.js `next` options). Defined narrower wrapper types (`ShareCollectionInfo`, `ShareCollectionItem`, `ShareCollectionResponse`) that convert `?: T | null` optional fields to `T | null` required fields.

### B. Type Narrowing Wrappers

Generated types use `?: T | null` (optional nullable = `T | null | undefined`) for fields callers treat as `T | null`. Fixed with two layers:

5. **`apps/web/src/components/collections/types.ts`** — exports `CollectionDetail` (Omit + required `T | null` overrides for `description`, `deadline`, `days_left`) and `CollectionEntry` (Omit + overrides for `assignee`, `presentation_minutes`, `note`). Static type safety only.

6. **`apps/web/src/components/viewer/resources/types.ts`** — exports narrowed `ResourceLink` (Omit + overrides for `thumbnail_url`, `note`, `meta`) and `ResourceListResponse`. Static type safety only.

7. **Runtime coercion mappers** (`collections/api.ts` + `resources-api.ts`) — explicit `?? null` coercion at each optional nullable field so `undefined` values from an API that omits optional fields become `null` at runtime, not just at the type level.

### C. Mock Server Enhancement (Responses API Streaming + Evidence)

8. **`packages/llm/src/alinea_llm/testing/mock_server.py`** — added Responses API streaming support:
   - `_extract_first_block_id()` — regex scan for `blk-xxx` in request fields
   - `_responses_text()` — flattens Responses API input to plain text
   - `_responses_output_text()` — appends `[[evidence:blk-xxx]]` when `output_config.evidence=true`
   - `_openai_responses_sse()` — full SSE stream with all required events: `response.created`, `response.output_item.added`, `response.content_part.added`, `response.output_text.delta` x N, `response.output_text.done`, `response.output_item.done`, `response.completed`
   - `openai_responses()` detects `stream: true` and routes to SSE path

9. **`packages/llm/tests/test_mock_server.py`** — added 3 new tests (TDD: RED then GREEN), renamed misleading docstring from "受け付けて無視する" to "受け付けてエラーにならない":
   - `test_mock_openai_responses_accepts_output_config` — `output_config` field accepted without error
   - `test_mock_openai_responses_streaming` — `stream=True` returns `text/event-stream` with correct events
   - `test_mock_openai_responses_streaming_with_evidence` — `output_config.evidence=True` + block ID in instructions returns `[[evidence:blk-xxx]]` in stream

10. **`apps/web/e2e/specs/pw-08-chat.spec.ts`** — removed `test.fixme()` for fresh question evidence chip test; added assertions for evidence chip rendering. E2E execution deferred to Task 32's consolidated gate.

## TDD Evidence (Mock Server)

### RED phase
Running `uv run --package alinea-llm pytest packages/llm/tests/test_mock_server.py -v` before implementing:
- `test_mock_openai_responses_accepts_output_config` — PASSED (server already accepted unknown fields)
- `test_mock_openai_responses_streaming` — FAILED (no SSE path existed)
- `test_mock_openai_responses_streaming_with_evidence` — FAILED (no evidence injection)

### GREEN phase
After implementing `_openai_responses_sse()` and evidence injection:
- All 11 tests PASSED

## Verification

| Check | Result |
|-------|--------|
| `rg -n 'fetch\(["\x27]/api/'` in 4 target files | 0 matches (exit 1 = no matches) |
| `pnpm --filter @alinea/web typecheck` | 18 errors (all `@alinea/tokens` module not found — pre-existing baseline) |
| `uv run --package alinea-llm pytest packages/llm/tests/test_mock_server.py -q` | 11/11 passed |
| `pnpm --filter @alinea/web test` | 373/373 pass; 51 file-level failures from `@alinea/tokens` (pre-existing) |

## Files Changed

- `apps/web/src/lib/resources-api.ts`
- `apps/web/src/components/collections/api.ts`
- `apps/web/src/components/collections/types.ts`
- `apps/web/src/components/viewer/resources/types.ts`
- `apps/web/src/hooks/use-reading-session.ts`
- `apps/web/src/app/(public)/c/[token]/fetch-share.ts`
- `packages/llm/src/alinea_llm/testing/mock_server.py`
- `packages/llm/tests/test_mock_server.py`
- `apps/web/e2e/specs/pw-08-chat.spec.ts`

## Commits

- `1c11475` — `refactor(web): finish generated client migration`
- `f2acee7` — `fix(web): replace double-casts with explicit null-coercion mappers`

## Deviations

- **`fetch-share.ts`**: kept direct `fetch()` — SDK does not support `next: { revalidate: 60 }` (Next.js server components). SDK used for type import only.
- **`use-reading-session.ts`**: kept direct `fetch()` for heartbeat (`keepalive: true`) and `sendBeacon` — SDK cannot provide these. SDK used for `settingsGet` settings check.
- **`resources-api.ts`**: imports local narrowed `ResourceLink`/`ResourceListResponse` types (not SDK types) so `ResourceCard` and `ResourcesPanel` remain type-safe. SDK types imported only as mapper input types (`SdkResourceLink`, `SdkResourceListResponse`).

## Self-Review

- Zero new typecheck errors introduced (baseline: 18 `@alinea/tokens` errors; after: same 18).
- Zero `fetch("/api/...")` calls remain in the 4 target files.
- Runtime null-coercion mappers (`toCollectionDetail`, `toCollectionEntry`, `toResourceLink`, `toResourceListResponse`) ensure `undefined` to `null` conversion is explicit and safe, not just a type-level lie.
- `ResourceApiError` and `ApiError` classes preserved with original `.status`, `.body`, `.code` properties for backward-compat `instanceof` checks.
- Mock server tests are genuine TDD: 2 of 3 failed RED, then all 3 GREEN.
- E2E pw-08-chat deferral noted explicitly in spec file comment.
- No `.superpowers/brainstorm/` files modified.
