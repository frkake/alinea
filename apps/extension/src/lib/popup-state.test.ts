import { expect, test } from "vitest";

import type { IngestCheckResponse } from "@alinea/api-client";

import { resolvePopupState } from "./popup-state";

// VT-XTU-01(ポップアップ状態分岐。API 応答をモックした純粋関数テスト)。
function check(partial: Partial<IngestCheckResponse>): IngestCheckResponse {
  return { kind: "unsupported", ...partial } as IngestCheckResponse;
}

test("null auth or check resolves to loading", () => {
  expect(resolvePopupState({ authed: null, check: null })).toBe("loading");
  expect(resolvePopupState({ authed: true, check: null })).toBe("loading");
});

test("unauthenticated resolves to login", () => {
  expect(resolvePopupState({ authed: false, check: null })).toBe("login");
});

test("saved item resolves to existing regardless of kind", () => {
  const c = check({
    kind: "arxiv",
    saved: { library_item_id: "li_1", status: "reading", added_at: "x", progress_pct: 42 },
  });
  expect(resolvePopupState({ authed: true, check: c })).toBe("existing");
});

test("arxiv/pdf/unsupported branch to the right states", () => {
  expect(resolvePopupState({ authed: true, check: check({ kind: "arxiv" }) })).toBe("saveform");
  expect(resolvePopupState({ authed: true, check: check({ kind: "pdf" }) })).toBe("pdf");
  expect(resolvePopupState({ authed: true, check: check({ kind: "unsupported" }) })).toBe(
    "unsupported",
  );
});
