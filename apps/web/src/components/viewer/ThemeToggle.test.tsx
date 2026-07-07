import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { ThemeProvider } from "@/components/ThemeProvider";
import { ThemeToggle } from "@/components/viewer/ThemeToggle";

// M0-31: ダークモード切替(data-theme を <html> へ適用)
describe("ThemeToggle", () => {
  test("renders light/dark/system options and applies data-theme on select", () => {
    render(
      <ThemeProvider initialTheme="light">
        <ThemeToggle />
      </ThemeProvider>,
    );
    expect(screen.getByText("ライト")).toBeInTheDocument();
    expect(screen.getByText("ダーク")).toBeInTheDocument();
    expect(screen.getByText("システム")).toBeInTheDocument();

    fireEvent.click(screen.getByText("ダーク"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});
