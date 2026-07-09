import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import type { DeadlineCollectionEntry, DeadlineItemEntry } from "@alinea/api-client";
import { DashboardDeadlines } from "@/components/library/DashboardDeadlines";

const collection: DeadlineCollectionEntry = {
  id: "col_1",
  name: "輪読会 2026-07",
  deadline: "2026-07-16",
  days_left: 10,
  done_count: 3,
  total_count: 5,
};

const item: DeadlineItemEntry = {
  library_item_id: "li_1",
  title: "Adversarial Diffusion Distillation",
  deadline: "2026-07-16",
  assignee_self: true,
  status: "up_next",
};

// M2-09: plans/09-screens/1d §4.7・docs/06 §6.3 の「締切が近い」セクション。
describe("DashboardDeadlines (1d §4.7)", () => {
  test("空のとき EmptyState を表示する", () => {
    render(
      <DashboardDeadlines
        collections={[]}
        items={[]}
        onOpenCollection={vi.fn()}
        onOpenItem={vi.fn()}
      />,
    );
    expect(screen.getByText("締切はありません")).toBeInTheDocument();
  });

  test("コレクション単位: 名前・残り日数・進捗集計を表示し、クリックで onOpenCollection", async () => {
    const onOpenCollection = vi.fn();
    render(
      <DashboardDeadlines
        collections={[collection]}
        items={[]}
        onOpenCollection={onOpenCollection}
        onOpenItem={vi.fn()}
      />,
    );
    expect(screen.getByText("輪読会 2026-07")).toBeInTheDocument();
    expect(screen.getByText("残り 10 日")).toBeInTheDocument();
    expect(screen.getByText("コレクション · 5 本中 3 本読了")).toBeInTheDocument();

    await userEvent.click(screen.getByText("輪読会 2026-07"));
    expect(onOpenCollection).toHaveBeenCalledWith("col_1");
  });

  test("論文単位: 担当発表・締切・未着手ラベルを表示し、クリックで onOpenItem", async () => {
    const onOpenItem = vi.fn();
    render(
      <DashboardDeadlines
        collections={[]}
        items={[item]}
        onOpenCollection={vi.fn()}
        onOpenItem={onOpenItem}
      />,
    );
    expect(screen.getByText("Adversarial Diffusion Distillation")).toBeInTheDocument();
    // メタ行は「担当発表 · 締切 7/16」(直下テキスト)+「未着手」(子 span)に分かれる
    // (@testing-library/dom は要素の直下テキストノードのみを対象にするため分割して検証する)。
    expect(screen.getByText("担当発表 · 締切 7/16", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("未着手")).toBeInTheDocument();

    await userEvent.click(screen.getByText("Adversarial Diffusion Distillation"));
    expect(onOpenItem).toHaveBeenCalledWith("li_1");
  });

  test("days_left 0/負値は「今日が締切」「期限超過」に切り替わる", () => {
    render(
      <DashboardDeadlines
        collections={[
          { ...collection, days_left: 0 },
          { ...collection, id: "col_2", days_left: -1 },
        ]}
        items={[]}
        onOpenCollection={vi.fn()}
        onOpenItem={vi.fn()}
      />,
    );
    expect(screen.getByText("今日が締切")).toBeInTheDocument();
    expect(screen.getByText("期限超過")).toBeInTheDocument();
  });
});
