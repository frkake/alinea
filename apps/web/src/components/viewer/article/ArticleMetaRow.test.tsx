import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { ArticleMetaRow } from "@/components/viewer/article/ArticleMetaRow";

// VT-VIEW-14: 「AI生成」・生成日付・免責「元の論文とは別物です — 根拠チップから原文へ」
describe("ArticleMetaRow (VT-VIEW-14)", () => {
  test("renders the AI-generated badge and the verbatim disclaimer", () => {
    const disclaimer =
      "訳文・メモ・チャット履歴から自動構成 · 2026-07-06 · 元の論文とは別物です — 根拠チップから原文へ";
    render(<ArticleMetaRow disclaimer={disclaimer} />);
    expect(screen.getByText("AI生成")).toBeInTheDocument();
    expect(screen.getByText(disclaimer)).toBeInTheDocument();
  });
});
