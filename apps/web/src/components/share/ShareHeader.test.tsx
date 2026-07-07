import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { ShareHeader } from "@/components/share/ShareHeader";

describe("ShareHeader", () => {
  test("token 指定時、CTA は /login?next=/c/{token} を指す", () => {
    render(<ShareHeader token="x8Kf3qPw" />);
    const cta = screen.getByRole("link", { name: "訳読をはじめる" });
    expect(cta).toHaveAttribute("href", "/login?next=%2Fc%2Fx8Kf3qPw");
  });

  test("token 省略時、CTA は /login のみを指す(404/エラー画面用)", () => {
    render(<ShareHeader />);
    const cta = screen.getByRole("link", { name: "訳読をはじめる" });
    expect(cta).toHaveAttribute("href", "/login");
  });

  test("逐語文言が表示される", () => {
    render(<ShareHeader token="x8Kf3qPw" />);
    expect(screen.getByText("訳読")).toBeInTheDocument();
    expect(screen.getByText("共有されたコレクション — 閲覧専用")).toBeInTheDocument();
    expect(screen.getByText("自分のライブラリで論文を読むには")).toBeInTheDocument();
  });
});
