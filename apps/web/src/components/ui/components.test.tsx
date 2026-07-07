import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { AIBadge, AiMark } from "@/components/ui/AIBadge";
import { Card } from "@/components/ui/Card";
import { CountBadge } from "@/components/ui/CountBadge";
import { EmptyState } from "@/components/ui/EmptyState";
import { EvidenceChip } from "@/components/ui/EvidenceChip";
import { FilterChip } from "@/components/ui/FilterChip";
import { Keycap } from "@/components/ui/Keycap";
import { LibraryTable, type LibraryTableRow } from "@/components/ui/LibraryTable";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { QualityBadge } from "@/components/ui/QualityBadge";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SidePanelTabs } from "@/components/ui/SidePanelTabs";
import { SidebarNav } from "@/components/ui/SidebarNav";
import { TagChip } from "@/components/ui/TagChip";
import { Toggle } from "@/components/ui/Toggle";

// VT-UI-03: 共通コンポーネントの契約
describe("QualityBadge / AIBadge", () => {
  test("QualityBadge renders A/B with tooltips", () => {
    render(
      <>
        <QualityBadge level="A" />
        <QualityBadge level="B" size={17} />
      </>,
    );
    expect(screen.getByText("A")).toHaveAttribute(
      "title",
      "品質レベルA: LaTeXソースから完全構造化",
    );
    expect(screen.getByText("B")).toHaveAttribute("title", "品質レベルB: PDF由来");
  });

  test("AIBadge renders the three fidelity labels", () => {
    render(
      <>
        <AIBadge variant="generated" />
        <AIBadge variant="external" />
        <AIBadge variant="guess" />
        <AiMark />
      </>,
    );
    expect(screen.getByText("AI生成")).toBeInTheDocument();
    expect(screen.getByText("論文外の知識")).toBeInTheDocument();
    expect(screen.getByText("推測")).toBeInTheDocument();
    expect(screen.getByText("✦")).toBeInTheDocument();
  });
});

describe("SegmentedControl", () => {
  test("is a radiogroup and reports changes", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <SegmentedControl
        ariaLabel="表示モード"
        options={[
          { value: "translation", label: "訳文" },
          { value: "bilingual", label: "対訳" },
          { value: "original", label: "原文" },
        ]}
        value="translation"
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("radiogroup", { name: "表示モード" })).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "対訳" }));
    expect(onChange).toHaveBeenCalledWith("bilingual");
  });
});

describe("Toggle", () => {
  test("is a switch that flips on click", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} ariaLabel="対訳を表示" />);
    const sw = screen.getByRole("switch", { name: "対訳を表示" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    await user.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
  });
});

describe("ProgressBar", () => {
  test("clamps value and exposes aria attributes", () => {
    render(<ProgressBar value={140} />);
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "100");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
  });
});

describe("SidePanelTabs", () => {
  test("renders all six tabs by default", () => {
    render(<SidePanelTabs active="chat" counts={{ annotations: 6 }} onChange={() => {}} />);
    for (const label of ["チャット", "メモ", "注釈", "図表", "リソース", "情報"]) {
      expect(screen.getByRole("tab", { name: new RegExp(label) })).toBeInTheDocument();
    }
  });

  test("renders only the provided M0 subset (chat/figures/info)", () => {
    render(
      <SidePanelTabs
        active="chat"
        counts={{}}
        onChange={() => {}}
        tabs={["chat", "figures", "info"]}
      />,
    );
    expect(screen.getByRole("tab", { name: "チャット" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "図表" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "情報" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "メモ" })).toBeNull();
  });
});

describe("FilterChip / CountBadge / Card / Keycap / TagChip", () => {
  test("FilterChip removable exposes a remove control", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    render(<FilterChip label="タグ: distillation" removable onRemove={onRemove} />);
    await user.click(screen.getByRole("button", { name: "タグ: distillation を解除" }));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });

  test("misc primitives render their content", () => {
    render(
      <>
        <CountBadge count={6} variant="annotation" />
        <Card>
          <span>カード本文</span>
        </Card>
        <Keycap mono>⌘K</Keycap>
        <TagChip>distillation</TagChip>
      </>,
    );
    expect(screen.getByText("6")).toBeInTheDocument();
    expect(screen.getByText("カード本文")).toBeInTheDocument();
    expect(screen.getByText("⌘K")).toBeInTheDocument();
    expect(screen.getByText("distillation")).toBeInTheDocument();
  });
});

describe("EvidenceChip / EmptyState", () => {
  test("EvidenceChip jumps with its anchor", async () => {
    const user = userEvent.setup();
    const onJump = vi.fn();
    render(
      <EvidenceChip
        anchor={{ type: "equation", eqNumber: 5 }}
        label="式(5) · §2.1"
        onJump={onJump}
      />,
    );
    await user.click(screen.getByRole("button", { name: "式(5) · §2.1" }));
    expect(onJump).toHaveBeenCalledWith({ type: "equation", eqNumber: 5 });
  });

  test("EmptyState shows title and description", () => {
    render(<EmptyState title="未配置 0 件" description="ドラッグで追加" />);
    expect(screen.getByText("未配置 0 件")).toBeInTheDocument();
    expect(screen.getByText("ドラッグで追加")).toBeInTheDocument();
  });
});

describe("SidebarNav", () => {
  test("marks the active item and renders links", () => {
    render(
      <SidebarNav
        main={[
          { id: "home", label: "ホーム", href: "/dashboard", active: true },
          { id: "library", label: "ライブラリ", href: "/library", count: 41 },
        ]}
        sections={[]}
      />,
    );
    const home = screen.getByRole("link", { name: /ホーム/ });
    expect(home).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /ライブラリ/ })).toHaveAttribute("href", "/library");
    expect(screen.getByText("41")).toBeInTheDocument();
  });
});

describe("LibraryTable", () => {
  const rows: LibraryTableRow[] = [
    {
      id: "a",
      title: "Rectified Flow",
      authorsLine: "Liu, Gong, Liu · ICLR 2023 · arXiv:2209.03003",
      thumbnailUrl: null,
      status: "reading",
      quality: "A",
      tags: ["distillation"],
      priority: "high",
      deadline: "7/16",
      readingHours: 3.2,
      comprehension: 4,
      addedAt: "7/1",
    },
  ];

  test("renders header labels, a row and selection checkboxes", async () => {
    const user = userEvent.setup();
    const onToggleSelect = vi.fn();
    render(
      <LibraryTable
        rows={rows}
        selectedIds={new Set()}
        onToggleSelect={onToggleSelect}
        onToggleSelectAll={() => {}}
        sort={{ key: "title", dir: "asc" }}
        onSortChange={() => {}}
        onOpenRow={() => {}}
      />,
    );
    expect(screen.getByText("論文 ↑")).toBeInTheDocument();
    expect(screen.getByText("ステータス")).toBeInTheDocument();
    expect(screen.getByText("Rectified Flow")).toBeInTheDocument();
    expect(screen.getByText("3.2h")).toBeInTheDocument();
    expect(screen.getByText("4/5")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "Rectified Flow を選択" }));
    expect(onToggleSelect).toHaveBeenCalledWith("a");
  });

  test("without onStatusChange, the status cell stays a static non-interactive StatusPill", () => {
    render(
      <LibraryTable
        rows={rows}
        selectedIds={new Set()}
        onToggleSelect={vi.fn()}
        onToggleSelectAll={() => {}}
        sort={{ key: "title", dir: "asc" }}
        onSortChange={() => {}}
        onOpenRow={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: /のステータスを変更/ })).toBeNull();
  });

  // M1 統合ポリッシュ: 1e §4.7 の StatusPill(dot-label)を interactive にする。
  test("with onStatusChange, clicking the status cell opens a menu and reports the picked status", async () => {
    const user = userEvent.setup();
    const onStatusChange = vi.fn();
    render(
      <LibraryTable
        rows={rows}
        selectedIds={new Set()}
        onToggleSelect={vi.fn()}
        onToggleSelectAll={() => {}}
        sort={{ key: "title", dir: "asc" }}
        onSortChange={() => {}}
        onOpenRow={vi.fn()}
        onStatusChange={onStatusChange}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Rectified Flow のステータスを変更" }));
    const menu = screen.getByRole("menu");
    await user.click(within(menu).getByRole("menuitemradio", { name: /読んだ/ }));
    expect(onStatusChange).toHaveBeenCalledWith("a", "done");
  });

  test("clicking the status cell does not open the row (stopPropagation)", async () => {
    const user = userEvent.setup();
    const onOpenRow = vi.fn();
    render(
      <LibraryTable
        rows={rows}
        selectedIds={new Set()}
        onToggleSelect={vi.fn()}
        onToggleSelectAll={() => {}}
        sort={{ key: "title", dir: "asc" }}
        onSortChange={() => {}}
        onOpenRow={onOpenRow}
        onStatusChange={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Rectified Flow のステータスを変更" }));
    expect(onOpenRow).not.toHaveBeenCalled();
  });
});
