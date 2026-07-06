import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { AppHeader } from "@/components/AppHeader";

// VT-UI-01: シェル(トップバー)描画
describe("AppHeader (VT-UI-01)", () => {
  test("renders product name", () => {
    render(<AppHeader />);
    expect(screen.getByText(/訳読/)).toBeInTheDocument();
  });

  test("renders wordmark, global search and account controls", () => {
    render(<AppHeader />);
    expect(screen.getByText("YAKUDOKU")).toBeInTheDocument();
    expect(
      screen.getByLabelText("ライブラリ全体を検索 — 本文・訳文・メモ・チャット"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("通知")).toBeInTheDocument();
    expect(screen.getByLabelText("アカウント")).toBeInTheDocument();
  });

  test("hides search box when showSearch is false", () => {
    render(<AppHeader showSearch={false} />);
    expect(
      screen.queryByLabelText("ライブラリ全体を検索 — 本文・訳文・メモ・チャット"),
    ).toBeNull();
  });
});
