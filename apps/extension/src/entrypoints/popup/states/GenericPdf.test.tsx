import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

// VT-XTU-02: GenericPdf(状態4)の状態遷移(3a §6.5・plans/10 §11.2)。
import { GenericPdf } from "./GenericPdf";

test("shows the guessed title, the 'estimated' badge and the URL", () => {
  render(
    <GenericPdf
      tabUrl="https://example.org/papers/attention.pdf"
      titleGuess="Attention Is All You Need"
      onSend={vi.fn()}
    />,
  );
  expect(screen.getByText("Attention Is All You Need")).toBeInTheDocument();
  expect(screen.getByText("書誌は推定")).toBeInTheDocument();
  expect(screen.getByText("https://example.org/papers/attention.pdf")).toBeInTheDocument();
});

test("falls back to the unknown-title placeholder when titleGuess is null", () => {
  render(<GenericPdf tabUrl="https://example.org/x.pdf" titleGuess={null} onSend={vi.fn()} />);
  expect(screen.getByText("(タイトル不明の PDF)")).toBeInTheDocument();
});

test("always shows the manual-send warning and never auto-sends", () => {
  const onSend = vi.fn();
  render(<GenericPdf tabUrl="https://example.org/x.pdf" titleGuess={null} onSend={onSend} />);
  expect(screen.getByText(/自動送信はしません/)).toBeInTheDocument();
  expect(onSend).not.toHaveBeenCalled();
});

test("clicking the button triggers onSend exactly once", () => {
  const onSend = vi.fn();
  render(<GenericPdf tabUrl="https://example.org/x.pdf" titleGuess={null} onSend={onSend} />);
  fireEvent.click(screen.getByRole("button", { name: "このタブの PDF を送信" }));
  expect(onSend).toHaveBeenCalledTimes(1);
});

test("while sending, the button is disabled and shows the sending label", () => {
  render(<GenericPdf tabUrl="https://example.org/x.pdf" titleGuess={null} sending onSend={vi.fn()} />);
  const button = screen.getByRole("button", { name: "送信中…" });
  expect(button).toBeDisabled();
  expect(screen.queryByRole("button", { name: "このタブの PDF を送信" })).toBeNull();
});

test("shows a permanent/queued error message when provided, without hiding the send button", () => {
  render(
    <GenericPdf
      tabUrl="https://example.org/x.pdf"
      titleGuess={null}
      error="50MB を超える PDF は送信できません"
      onSend={vi.fn()}
    />,
  );
  expect(screen.getByText("50MB を超える PDF は送信できません")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "このタブの PDF を送信" })).toBeEnabled();
});

test("privacy note is always shown (private paper, not shared)", () => {
  render(<GenericPdf tabUrl="https://example.org/x.pdf" titleGuess={null} onSend={vi.fn()} />);
  expect(screen.getByText("private 論文として保存され、共有されません")).toBeInTheDocument();
});
