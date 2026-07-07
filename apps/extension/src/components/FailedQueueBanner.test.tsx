import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

// VT-XTU-03 の UI 側(FailedQueueBanner): 折りたたみ表示 + 再試行/破棄(plans/10 §11.4)。
import { FailedQueueBanner, type FailedQueueEntry } from "./FailedQueueBanner";

const ENTRIES: FailedQueueEntry[] = [
  { id: "a", kind: "arxiv", title: "Rectified Flow", failedAt: Date.parse("2026-07-06T21:52:00+09:00"), lastError: "network" },
  { id: "b", kind: "pdf", title: "(タイトル不明の PDF)", failedAt: Date.parse("2026-07-06T20:00:00+09:00"), lastError: "network" },
];

const NOW = new Date("2026-07-07T09:00:00+09:00");

test("renders nothing when the queue is empty and there is no notice", () => {
  const { container } = render(
    <FailedQueueBanner entries={[]} onRetry={vi.fn()} onDiscard={vi.fn()} now={NOW} />,
  );
  expect(container).toBeEmptyDOMElement();
});

test("shows the collapsed summary with the entry count", () => {
  render(<FailedQueueBanner entries={ENTRIES} onRetry={vi.fn()} onDiscard={vi.fn()} now={NOW} />);
  expect(screen.getByText("送信できなかった保存が 2 件あります")).toBeInTheDocument();
  expect(screen.queryByText("Rectified Flow")).toBeNull(); // 折りたたみ時は行を表示しない
});

test("expanding shows each row with title, time and retry/discard actions", () => {
  render(<FailedQueueBanner entries={ENTRIES} onRetry={vi.fn()} onDiscard={vi.fn()} now={NOW} />);
  fireEvent.click(screen.getByText("送信できなかった保存が 2 件あります"));
  expect(screen.getByText("Rectified Flow")).toBeInTheDocument();
  expect(screen.getByText("(タイトル不明の PDF)")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "再試行" })).toHaveLength(2);
  expect(screen.getAllByRole("button", { name: "破棄" })).toHaveLength(2);
});

test("retry/discard call back with the entry id and kind", () => {
  const onRetry = vi.fn();
  const onDiscard = vi.fn();
  render(<FailedQueueBanner entries={ENTRIES} onRetry={onRetry} onDiscard={onDiscard} now={NOW} />);
  fireEvent.click(screen.getByText("送信できなかった保存が 2 件あります"));
  fireEvent.click(screen.getAllByRole("button", { name: "再試行" })[0]);
  expect(onRetry).toHaveBeenCalledWith("a", "arxiv");
  fireEvent.click(screen.getAllByRole("button", { name: "破棄" })[1]);
  expect(onDiscard).toHaveBeenCalledWith("b", "pdf");
});

test("shows the eviction notice even when queue entries remain", () => {
  render(
    <FailedQueueBanner
      entries={ENTRIES}
      onRetry={vi.fn()}
      onDiscard={vi.fn()}
      notice="上限のため「Old Paper」を破棄しました"
      now={NOW}
    />,
  );
  expect(screen.getByText("上限のため「Old Paper」を破棄しました")).toBeInTheDocument();
});
