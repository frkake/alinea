import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import type { FacetsResponse } from "@alinea/api-client";
import {
  AttributeFilterBar,
  emptyAttributeFilters,
  hasAppliedAttributeFilters,
  type AppliedAttributeFilters,
} from "@/components/library/AttributeFilterBar";

const facets: FacetsResponse = {
  quick: { all: 41, unread: 12, in_progress: 4, done: 23, recheck: 2 },
  status: {
    planned: 9,
    up_next: 3,
    reading: 4,
    done: 23,
    reread: 2,
    on_hold: 0,
  },
  tags: [
    { tag: "distillation", count: 5 },
    { tag: "cs.CV", count: 3 },
  ],
  collections: [{ id: "col_1", name: "輪読会 2026-07", count: 5 }],
  quality: { A: 30, B: 11 },
  years: [
    { year: 2024, count: 10 },
    { year: 2023, count: 20 },
  ],
};

// 1e §4.6: 属性フィルタ 5 種(ステータス/タグ/コレクション/品質/年)
describe("AttributeFilterBar", () => {
  test("renders 5 dropdown triggers with default labels while unapplied", () => {
    render(
      <AttributeFilterBar facets={facets} value={emptyAttributeFilters()} onChange={() => {}} />,
    );
    for (const label of ["ステータス", "タグ", "コレクション", "品質", "年"]) {
      expect(screen.getByRole("button", { name: new RegExp(`^${label}`) })).toBeInTheDocument();
    }
  });

  test("opening the tag dropdown lists options with facet counts", async () => {
    const user = userEvent.setup();
    render(
      <AttributeFilterBar facets={facets} value={emptyAttributeFilters()} onChange={() => {}} />,
    );
    await user.click(screen.getByRole("button", { name: "タグ ▾" }));
    expect(screen.getByRole("menuitemcheckbox", { name: /distillation/ })).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  test("selecting a tag option calls onChange with the value appended (OR within attribute)", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <AttributeFilterBar facets={facets} value={emptyAttributeFilters()} onChange={onChange} />,
    );
    await user.click(screen.getByRole("button", { name: "タグ ▾" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: /distillation/ }));
    expect(onChange).toHaveBeenCalledWith({ ...emptyAttributeFilters(), tags: ["distillation"] });
  });

  test("applied single-select attribute (quality) renders as a removable chip", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const value: AppliedAttributeFilters = { ...emptyAttributeFilters(), quality: "A" };
    render(<AttributeFilterBar facets={facets} value={value} onChange={onChange} />);

    expect(screen.getByText("品質: A")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: `${"品質: A"} を解除` }));
    expect(onChange).toHaveBeenCalledWith({ ...emptyAttributeFilters(), quality: null });
  });

  test("re-clicking a selected single-select option clears it", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const value: AppliedAttributeFilters = { ...emptyAttributeFilters(), quality: "A" };
    render(<AttributeFilterBar facets={facets} value={value} onChange={onChange} />);

    await user.click(screen.getByText("品質: A"));
    await user.click(screen.getByRole("menuitemradio", { name: /^A/ }));
    expect(onChange).toHaveBeenCalledWith({ ...emptyAttributeFilters(), quality: null });
  });

  test("multi-value chip shows the first label plus a remainder count", () => {
    const value: AppliedAttributeFilters = { ...emptyAttributeFilters(), years: [2024, 2023] };
    render(<AttributeFilterBar facets={facets} value={value} onChange={() => {}} />);
    expect(screen.getByText("年: 2024 +1")).toBeInTheDocument();
  });
});

describe("hasAppliedAttributeFilters", () => {
  test("is false for the empty state and true once any dimension is set", () => {
    expect(hasAppliedAttributeFilters(emptyAttributeFilters())).toBe(false);
    expect(hasAppliedAttributeFilters({ ...emptyAttributeFilters(), quality: "A" })).toBe(true);
  });
});
