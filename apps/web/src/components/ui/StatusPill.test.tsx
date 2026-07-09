import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { STATUS_LABELS, type ReadingStatus } from "@alinea/tokens";
import { StatusPill } from "@/components/ui/StatusPill";

// VT-UI-02: StatusPill 6色/6ラベル
describe("StatusPill (VT-UI-02)", () => {
  test("renders reading status label", () => {
    render(<StatusPill status="reading" />);
    expect(screen.getByText("読んでいる")).toBeInTheDocument();
  });

  const ALL: ReadingStatus[] = ["planned", "up_next", "reading", "done", "reread", "on_hold"];

  test.each(ALL)("renders the label for status %s", (status) => {
    render(<StatusPill status={status} />);
    expect(screen.getByText(STATUS_LABELS[status])).toBeInTheDocument();
  });

  test("dot-label variant renders label without a button", () => {
    render(<StatusPill status="done" variant="dot-label" />);
    expect(screen.getByText("読んだ")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  test("interactive variant exposes a popup trigger", () => {
    render(<StatusPill status="planned" interactive onChange={() => {}} />);
    const trigger = screen.getByRole("button");
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
  });
});
