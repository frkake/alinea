import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { Saved } from "./Saved";

// 取り込みキャンセル(docs/08 §2.2)。処理中のみ表示し、押すと onCancel が呼ばれる。
test("shows 取り込みを中止 while processing and calls onCancel on click", () => {
  const onCancel = vi.fn();
  render(
    <Saved
      title="Flow Straight and Fast"
      stage="structuring"
      progressPct={35}
      onOpen={() => {}}
      onClose={() => {}}
      onCancel={onCancel}
    />,
  );
  fireEvent.click(screen.getByText("取り込みを中止"));
  expect(onCancel).toHaveBeenCalledTimes(1);
});

test("hides 取り込みを中止 once the pipeline has completed", () => {
  render(
    <Saved
      title="Flow Straight and Fast"
      stage="complete"
      progressPct={100}
      onOpen={() => {}}
      onClose={() => {}}
      onCancel={() => {}}
    />,
  );
  expect(screen.queryByText("取り込みを中止")).not.toBeInTheDocument();
});

test("hides 取り込みを中止 once the pipeline has failed", () => {
  render(
    <Saved
      title="Flow Straight and Fast"
      stage="failed"
      progressPct={10}
      failedReason="boom"
      onOpen={() => {}}
      onClose={() => {}}
      onCancel={() => {}}
    />,
  );
  expect(screen.queryByText("取り込みを中止")).not.toBeInTheDocument();
});
