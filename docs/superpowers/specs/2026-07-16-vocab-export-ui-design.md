# S5: 語彙 Markdown エクスポート導線 — UI Design Spec

**Date:** 2026-07-16
**Scope:** Frontend-only; no new dependencies; no backend changes.

---

## 1. Goal

Add an "エクスポート (.md)" button to the `VocabHeader` toolbar so users can download the current
vocabulary list (respecting active kind / due / q / sort filters) as a Markdown file.

The backend `GET /api/vocab/export/markdown` and the SDK `vocabExportMarkdown` already exist and are
production-ready. The only missing piece is the Web UI affordance.

---

## 2. User-facing behaviour

| Trigger | Action |
|---------|--------|
| Click "エクスポート (.md)" | Build query-string from current filters; call `triggerDownload` with the constructed URL; no loading state needed (the browser handles the file download stream). |

Active filters forwarded to the API:
- `kind` — single string value or absent (API accepts `kind[]` array; we send `[kind]` when set).
- `due` — `"true"` or absent.
- `q` — search string or absent.
- `sort` — `"term"` or absent (API default is `added_at`).

---

## 3. Component changes

### 3.1 `VocabHeader.tsx` (modify)

Add props:
```
onExportMarkdown: () => void;
```

Render a secondary ghost-style button beside "復習をはじめる":
```
エクスポート (.md)
```

Button styling: ghost variant (border: `1px solid var(--pr-border-soft)`, transparent background,
height 28, padding 0 13px, font size 11.5, border-radius 6) — same spec as the secondary buttons
already used in adjacent views.

### 3.2 `apps/web/src/app/(app)/vocab/[[...vocabId]]/page.tsx` (modify)

Wire `onExportMarkdown` in the `<VocabHeader>` call site:

```tsx
onExportMarkdown={() => {
  const sp = new URLSearchParams();
  if (kind)   sp.set("kind", kind);
  if (dueOnly) sp.set("due", "true");
  if (q)      sp.set("q", q);
  if (sort !== "added_at") sp.set("sort", sort);
  triggerDownload(`/api/vocab/export/markdown${sp.size ? `?${sp}` : ""}`);
}}
```

Import `triggerDownload` from `@/components/settings/download`.

---

## 4. Tests

New file: `apps/web/src/components/vocab/VocabHeader.test.tsx`

| Test ID | Description |
|---------|-------------|
| VT-S5-01 | Export button is present in the rendered header. |
| VT-S5-02 | Clicking it calls `onExportMarkdown`. |

The integration (filter → URL) is thin wiring in `page.tsx` and is verified by the URL-construction
unit tests described in the plan.

---

## 5. No new dependencies

Uses only:
- `triggerDownload` from `apps/web/src/components/settings/download.ts` (already exists)
- Active filter state already available in `page.tsx` (`kind`, `dueOnly`, `q`, `sort`)
