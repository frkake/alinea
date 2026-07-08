import { render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { ResumeBanner } from "@/components/viewer/ResumeBanner";

describe("ResumeBanner", () => {
  test("constrains long resume text and the continue button within the banner", () => {
    render(
      <ResumeBanner
        sectionDisplay="§12.3 Extremely Long Section Title With URLs https://example.com/very/long/path"
        savedAt="2026-07-08T12:34:00.000Z"
        onResume={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    const banner = screen.getByRole("status");
    const text = screen.getByTitle(/前回はここまで:/);
    const button = screen.getByRole("button", { name: "続きから ↓" });

    expect(banner).toHaveStyle({ left: "12px", right: "12px", overflow: "hidden" });
    expect(text).toHaveStyle({ minWidth: "0", overflow: "hidden", textOverflow: "ellipsis" });
    expect(button).toHaveStyle({ maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis" });
  });
});
