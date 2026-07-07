import { expect, test } from "vitest";

// 計画 Task 33 Step 1(バッジ状態遷移)。badgeStateFor は background.ts からも再エクスポートされる。
import { badgeStateFor } from "./badge";

test("active ingest shows amber dot", () => {
  expect(badgeStateFor([{ status: "running" }])).toEqual({ color: "#C49432", text: "●" });
  expect(badgeStateFor([])).toEqual({ color: "", text: "" });
});

test("all-terminal jobs clear the badge unless completed/unread", () => {
  expect(badgeStateFor([{ status: "succeeded" }])).toEqual({ color: "", text: "" });
  expect(badgeStateFor([{ status: "succeeded" }], { justCompleted: true })).toEqual({
    color: "#659471",
    text: "✓",
  });
  expect(badgeStateFor([{ status: "failed" }], { unread: 2 })).toEqual({
    color: "#C49432",
    text: "●",
  });
});

test("active job takes priority over completed/unread", () => {
  expect(
    badgeStateFor([{ status: "succeeded" }, { status: "running" }], {
      justCompleted: true,
      unread: 5,
    }),
  ).toEqual({ color: "#C49432", text: "●" });
});
