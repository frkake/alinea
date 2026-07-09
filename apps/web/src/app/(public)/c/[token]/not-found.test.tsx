import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import ShareNotFound from "./not-found";

describe("ShareNotFound(§5.3)", () => {
  test("見出し・説明・ヘッダー・フッターを表示する(編集 UI は無い)", () => {
    render(<ShareNotFound />);
    expect(screen.getByText("このリンクは無効です")).toBeInTheDocument();
    expect(
      screen.getByText(/共有リンクが無効化されたか、URL が間違っています/),
    ).toBeInTheDocument();
    expect(screen.getByText("Alinea")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Alineaをはじめる" })).toHaveAttribute(
      "href",
      "/login",
    );
    expect(screen.getByText(/このページは閲覧専用です/)).toBeInTheDocument();
  });
});
