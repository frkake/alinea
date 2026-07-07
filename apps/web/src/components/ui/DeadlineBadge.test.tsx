import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { DeadlineBadge } from "@/components/ui/DeadlineBadge";

describe("DeadlineBadge", () => {
  test("既定の fontSize は 9.5px(4a・4b の既存挙動に影響なし)", () => {
    render(<DeadlineBadge date="7/16" variant="chip" withLabel />);
    expect(screen.getByText("締切 7/16")).toHaveStyle({ fontSize: "9.5px" });
  });

  test("fontSize=11 を指定すると親の 11px を継承できる(plans/09-screens/4c §3 決定)", () => {
    render(<DeadlineBadge date="7/16" variant="chip" withLabel fontSize={11} />);
    expect(screen.getByText("締切 7/16")).toHaveStyle({ fontSize: "11px" });
  });

  test("date=null は '—' を表示する(fontSize の影響を受けない)", () => {
    render(<DeadlineBadge date={null} variant="chip" fontSize={11} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
