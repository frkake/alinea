# PWA Installability & Offline Reading Design (S13 / M3)

## Goal

Make the Alinea web app (`apps/web`, Next.js App Router) an installable PWA and let a
reader open a previously-visited paper without a network connection (docs/10 §5 —
「PWA オフライン閲覧」). Do this without new runtime dependencies, without breaking SSR or
auth, and behind progressive enhancement so unsupported browsers are unaffected.

## Problem

The web app currently ships **no** web app manifest, **no** service worker, and **no**
offline capability (verified: no `serviceWorker` registration, no `*.webmanifest`, no
`next-pwa`/`@serwist/next` in `apps/web/package.json`). Consequently:

- The app cannot be "installed" (no manifest → no install prompt, no standalone window,
  no home-screen icon).
- Any offline navigation fails at the network layer; there is not even an app shell to
  paint, and an authed reader who loses connectivity mid-read loses the paper entirely.

## Constraints (from the roadmap and this repo)

- **No new deps for v1.** Next.js App Router has first-class support for the manifest
  (`app/manifest.ts` → `/manifest.webmanifest`, auto-linked) but **no** first-class
  service-worker story. A hand-written SW served from `public/sw.js` and registered
  client-side is the dependency-free path. `@serwist/next` / `next-pwa` are the
  alternatives, flagged below.
- **Do not break SSR/auth.** Authenticated pages are SSR'd and rely on a session cookie;
  the 401→`/login` redirect is a client-side response interceptor
  (`src/lib/auth-redirect.ts`). The SW must never serve a stale authed HTML page nor
  interfere with the login redirect.
- **Reuse existing design tokens & brand assets.** Palette lives in
  `packages/tokens/css/tokens.css` (app background `#FBFAF7`, slate accent `#3E5C76`);
  brand mark is `apps/web/src/app/icon.svg` / `apps/web/public/brand/alinea-logo.svg`.
- **Copyright (docs/09 §5.1):** full-text translations are a *private, per-user display*.
  Offline caching of viewer data is therefore per-device, private-by-nature, and must
  never be shared or exported by this feature. This does not change the licensing model —
  it is the same private display, just cached locally.

## Design

### A. Web app manifest (v1 — implement now)

Use Next.js' native `app/manifest.ts` (a typed function returning
`MetadataRoute.Manifest`, served at `/manifest.webmanifest` and auto-linked into
`<head>` by Next — zero new deps, unit-testable as a plain function):

- `name: "Alinea"`, `short_name: "Alinea"`, `description` reused from `layout.tsx`.
- `start_url: "/"`, `scope: "/"`, `id: "/"`, `display: "standalone"`, `lang: "ja"`,
  `dir: "ltr"`, `categories: ["education", "productivity"]`.
- `theme_color: "#FBFAF7"` (matches the existing `viewport.themeColor` in `layout.tsx`),
  `background_color: "#FBFAF7"` (light app background token) — splash matches the app's
  first paint. (Decision flagged: could brand the standalone title bar with the slate
  accent `#3E5C76`; chose consistency with the already-declared `theme-color` for v1.)
- `icons`: `192×192` + `512×512` `"any"` PNGs plus a `512×512` `"maskable"` PNG
  (full-bleed slate background so Android adaptive-icon masks don't clip the glyph).

Icons are generated **once** from the existing brand mark and committed as static PNGs
under `apps/web/public/icons/` (see §D). An `apple-touch-icon` (180×180) is wired via
`metadata.icons` for iOS home-screen installs.

### B. Service worker strategy

**Recommendation for v1: hand-written, dependency-free `public/sw.js`** registered
client-side behind progressive enhancement. Rationale in §"Alternatives".

**v1 scope — app-shell / static-asset runtime caching only (safe, no auth risk):**

- `install`: `skipWaiting()`; precache the offline-safe shell essentials (icons,
  manifest, the small offline notice). Keep the precache list tiny — Next's hashed assets
  are cached at runtime, not precached.
- `activate`: delete caches whose version prefix ≠ current; `clients.claim()`.
- `fetch` (GET only; ignore everything else):
  - Same-origin `/_next/static/*` (content-hashed, immutable): **cache-first**. Safe
    because filenames change on every deploy, so cache-first never serves stale code;
    `activate` purges superseded caches.
  - Google Fonts (`fonts.googleapis.com`, `fonts.gstatic.com`): **stale-while-revalidate**.
  - Icons / manifest: cache-first.
  - **Everything else — HTML navigations, `/api/*`, auth — bypasses the SW entirely
    (straight to network).** This is the invariant that protects SSR + auth: no cached
    authed HTML, no interference with the 401→`/login` redirect, no stale sessions.

This slice makes the app installable and makes repeat loads instant/offline-resilient for
the *shell*, with **zero** risk to auth because no dynamic/HTML/API response is ever
cached or served from cache.

**v2 scope — offline reading of visited papers (DESIGNED, GATED on the flagged decision):**

- Cache the last **N = 10** viewed papers' viewer payloads. The viewer fetches
  `viewerInit(item_id)` (`/api/viewer/...`) and translation/figure assets via
  `@alinea/api-client`. Strategy: **network-first with cache fallback** for these specific
  `/api/viewer/*` + figure-asset GETs, keyed by `item_id`, so a previously-opened paper
  renders from cache when offline while online reads stay fresh.
- Navigation fallback: when a `/papers/[itemId]` navigation fails offline, serve a cached
  app-shell document (the client then hydrates and reads paper data from the runtime
  cache) instead of a browser error — and critically, **not** the login redirect.
- Eviction: LRU over an explicit `alinea-viewer-v1` cache, capped at N papers (track an
  ordered `item_id` list in the cache metadata / IndexedDB; evict the least-recently
  opened paper's payload + figures together as a unit). Bound total figure bytes per paper.
- Auth interaction (the subtle part, hence gated): offline, `/api/viewer/*` may 401 if the
  session cookie expired. The SW must treat a *network failure* (offline) differently from
  a *401* (online but unauthed): fall back to cache **only on network failure**, and pass
  a real 401 through so the normal login redirect still fires when actually online. This
  is the design decision that needs sign-off before building v2 (see "Decisions needing
  user").

### C. Registration (progressive enhancement)

- A pure helper `src/lib/register-sw.ts#registerServiceWorker()` guards on
  `typeof navigator !== "undefined" && "serviceWorker" in navigator`; a no-op otherwise
  (older browsers, SSR). Unit-testable in jsdom.
- A tiny client component `src/components/pwa/ServiceWorkerRegistration.tsx` (renders
  `null`) calls it from `useEffect` **in production only** (`process.env.NODE_ENV`), after
  mount, to avoid dev HMR/caching friction. Mounted once from the root `layout.tsx`.
- No regression if unsupported: the guard returns early; the app behaves exactly as today.

### D. Icons (build-time-free)

192/512/maskable-512/apple-touch(180) PNGs are rendered **once** from the brand mark
(`app/icon.svg` for the "any" set; a full-bleed slate variant for the maskable one) and
committed under `apps/web/public/icons/`. Generation uses `sharp` (already present as a
transitive dep) invoked one-off — the *build* does not depend on `sharp`; only committed
PNGs ship. The maskable source SVG is committed for reproducibility.

## Alternatives considered

1. **`@serwist/next` (or `next-pwa`).** First-class Next integration, Workbox routing,
   precache manifest generation. Rejected for v1: adds a build dependency + config surface
   for what v1 needs (static-asset caching), against the "no new deps" constraint. Revisit
   if v2 offline-data routing + precise precache-manifest management justify it; the
   hand-written SW is intentionally small enough to swap out.
2. **Hand-written `public/sw.js` (chosen).** Zero deps, fully controlled fetch handling,
   easy to keep auth-safe (explicit allow-list of what to cache). Cost: we own the caching
   logic and versioning.
3. **Manifest via a static `public/manifest.webmanifest` + manual `<link>`.** Rejected in
   favor of Next's `app/manifest.ts`, which is typed, testable, and auto-linked.
4. **Cache HTML navigations in v1.** Rejected: risks serving stale authed pages and
   fighting the login redirect. Deferred to the gated v2 navigation-fallback design.

## Acceptance criteria

- `GET /manifest.webmanifest` returns a valid manifest with `name`, `short_name`,
  `start_url`, `display: "standalone"`, `theme_color`, `background_color`, and icons
  including 192, 512, and a maskable 512 (asserted by a unit test on the manifest function).
- The root document links the manifest and an apple-touch-icon; `theme-color` is present
  and consistent with the manifest.
- `registerServiceWorker()` is a no-op (no throw) when `serviceWorker` is unsupported, and
  calls `navigator.serviceWorker.register("/sw.js", { scope: "/" })` when supported
  (asserted by unit tests).
- `public/sw.js` never caches HTML navigations, `/api/*`, or auth responses (v1 invariant);
  only same-origin `/_next/static/*`, Google Fonts, and app icons/manifest are cached.
- `pnpm --filter @alinea/web build` succeeds; `pnpm --filter @alinea/web test` stays green;
  no behavior change when the SW / manifest are unsupported.
- v2 offline-data caching is documented and gated; not implemented in this slice.
