import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { SharePaperCard } from "@/components/share/SharePaperCard";

describe("SharePaperCard", () => {
  test("フル表示: 番号・書誌・arXiv リンク・要約(①②③無し)・メモ", () => {
    render(
      <SharePaperCard
        order={2}
        title="Flow Straight and Fast"
        authorsShort="Liu, Gong, Liu"
        venueYear="ICLR 2023"
        arxivUrl="https://arxiv.org/abs/2209.03003"
        summary3line={["直線に近い経路の ODE を学習。", "reflow の反復で 1 ステップ生成へ。"]}
        sharedNote="§2.2 と図2 を中心に議論したい。"
      />,
    );
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("Flow Straight and Fast")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "arXiv ↗" });
    expect(link).toHaveAttribute("href", "https://arxiv.org/abs/2209.03003");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer nofollow");
    expect(
      screen.getByText("✦ 直線に近い経路の ODE を学習。reflow の反復で 1 ステップ生成へ。"),
    ).toBeInTheDocument();
    expect(screen.getByText("共有者のメモ")).toBeInTheDocument();
    expect(screen.getByText("§2.2 と図2 を中心に議論したい。")).toBeInTheDocument();
  });

  test("縮退(§5.5): venue_year null は arXiv 直前の中点なしで書誌行を短縮する", () => {
    render(
      <SharePaperCard
        order={1}
        title="Title"
        authorsShort="A, B"
        venueYear={null}
        arxivUrl="https://arxiv.org/abs/1"
        summary3line={null}
        sharedNote={null}
      />,
    );
    expect(screen.getByText(/A, B/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "arXiv ↗" })).toBeInTheDocument();
  });

  test("縮退(§5.5): arxiv_url null でリンクと直前の中点が省略される", () => {
    render(
      <SharePaperCard
        order={4}
        title="Progressive Distillation"
        authorsShort="Salimans, Ho"
        venueYear="ICLR 2022"
        arxivUrl={null}
        summary3line={null}
        sharedNote={null}
      />,
    );
    expect(screen.queryByRole("link", { name: "arXiv ↗" })).not.toBeInTheDocument();
    expect(screen.queryByText("共有者のメモ")).not.toBeInTheDocument();
  });

  test("縮退(§5.5): summary_3line null で要約行が省略される(カード4相当)", () => {
    render(
      <SharePaperCard
        order={4}
        title="Progressive Distillation"
        authorsShort="Salimans, Ho"
        venueYear="ICLR 2022"
        arxivUrl="https://arxiv.org/abs/1"
        summary3line={null}
        sharedNote={null}
      />,
    );
    expect(screen.queryByText(/^✦/)).not.toBeInTheDocument();
  });
});
