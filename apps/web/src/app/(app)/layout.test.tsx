import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import AppLayout from "./layout";
import { mockMatchMedia } from "@/test-utils/mockMatchMedia";

vi.mock("@/components/AppHeader", () => ({
  AppHeader: ({
    onMenuClick,
    showSearch = true,
  }: {
    onMenuClick?: () => void;
    showSearch?: boolean;
  }) => (
    <div data-testid="app-header" data-show-search={String(showSearch)}>
      {onMenuClick ? (
        <button type="button" aria-label="メニューを開く" onClick={onMenuClick}>
          ☰
        </button>
      ) : (
        "app-header"
      )}
    </div>
  ),
}));
vi.mock("@/components/AppNav", () => ({
  AppNav: ({ onNavigate }: { onNavigate?: () => void }) => (
    <nav aria-label="サイドバー">
      <a href="/library" onClick={onNavigate}>
        ライブラリ
      </a>
    </nav>
  ),
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

// mobile.md §5.1: サイドバー(AppNav)を非描画にし、トップバーのハンバーガーから開く
// 左ドロワーへ差し替える(< 768px)。
describe("AppLayout mobile nav drawer (mobile.md §5.1)", () => {
  test("desktop (>= 768px): renders AppNav inline, no hamburger, no drawer", () => {
    mockMatchMedia(false);
    render(<AppLayout>content</AppLayout>);
    expect(screen.getByRole("navigation", { name: "サイドバー" })).toBeInTheDocument();
    expect(screen.queryByLabelText("メニューを開く")).toBeNull();
  });

  test("mobile (< 768px): hides AppNav until the hamburger opens the drawer", () => {
    mockMatchMedia(true);
    render(<AppLayout>content</AppLayout>);
    expect(screen.queryByRole("navigation", { name: "サイドバー" })).toBeNull();
    expect(screen.getByTestId("app-header")).toHaveAttribute("data-show-search", "false");

    fireEvent.click(screen.getByLabelText("メニューを開く"));
    expect(screen.getByRole("dialog", { name: "ナビゲーション" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "サイドバー" })).toBeInTheDocument();
  });

  test("mobile: navigating via the drawer closes it (onNavigate)", () => {
    mockMatchMedia(true);
    render(<AppLayout>content</AppLayout>);
    fireEvent.click(screen.getByLabelText("メニューを開く"));
    fireEvent.click(screen.getByText("ライブラリ"));
    expect(screen.queryByRole("dialog", { name: "ナビゲーション" })).toBeNull();
  });
});
