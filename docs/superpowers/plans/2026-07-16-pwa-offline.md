# Plan: PWA Installability & Offline Reading (S13 / M3)

Spec: docs/superpowers/specs/2026-07-16-pwa-offline-design.md
Scope of this branch: **v1 slice only** — installable PWA + app-shell/static-asset SW,
behind progressive enhancement. v2 offline-data caching is designed but gated (needs
user sign-off on the auth-vs-offline fallback rule) and NOT implemented here.

## Tasks

### Task 1 — Web app manifest + icons wiring (TDD)
- Add `apps/web/src/app/manifest.ts` returning `MetadataRoute.Manifest` (name, short_name,
  description, start_url `/`, scope `/`, id `/`, display `standalone`, lang `ja`,
  theme_color `#FBFAF7`, background_color `#FBFAF7`, categories, icons: 192/512 any +
  512 maskable).
- Generate + commit PNG icons under `apps/web/public/icons/` (icon-192.png,
  icon-512.png, icon-512-maskable.png, apple-touch-icon.png) from the brand mark using
  the already-present `sharp` (one-off script; build does not depend on sharp). Commit the
  maskable source SVG for reproducibility.
- Wire `apple-touch-icon` via `metadata.icons` in `layout.tsx` (manifest link is auto).
- Test: `apps/web/src/app/manifest.test.ts` — assert required fields + icon set (fails
  before file exists, passes after).

### Task 2 — Service worker registration helper (TDD)
- Add `apps/web/src/lib/register-sw.ts#registerServiceWorker()`: guard on SW support;
  no-op + return when unsupported; else `navigator.serviceWorker.register("/sw.js",
  {scope:"/"})`.
- Add `apps/web/src/components/pwa/ServiceWorkerRegistration.tsx` ("use client", renders
  null) calling it from `useEffect` in production only. Mount once in `layout.tsx`.
- Test: `apps/web/src/lib/register-sw.test.ts` — (a) unsupported → no throw, no register;
  (b) supported → register called with `/sw.js` + scope `/`.

### Task 3 — Hand-written service worker (`apps/web/public/sw.js`)
- install: skipWaiting + precache tiny shell (icons, manifest). activate: purge old
  versioned caches + clients.claim.
- fetch (GET only): cache-first for `/_next/static/*` and icons/manifest;
  stale-while-revalidate for Google Fonts; **bypass everything else** (HTML, `/api/*`,
  auth) — the v1 auth-safety invariant.
- Versioned cache names (`alinea-static-v1`, `alinea-fonts-v1`) for clean activation purge.
- (Plain JS served statically; not part of the vitest/tsc graph.)

### Task 4 — Verification
- `pnpm --filter @alinea/web test` (green, incl. new tests).
- `pnpm --filter @alinea/web build` (succeeds; `/manifest.webmanifest` route present).
- Confirm no auth/SSR regression by inspection: SW registered prod-only, client-side,
  never caches HTML/api.

## Out of scope (gated on user decision)
- v2 offline-data caching of the last N viewed papers (viewer JSON + figures),
  network-first-with-cache-fallback, LRU eviction, and the offline-vs-401 fallback rule.
