import type { ComponentProps } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { CodeAnalysisSettings } from "@/components/settings/CodeAnalysisSettings";

function renderPanel(overrides: Partial<ComponentProps<typeof CodeAnalysisSettings>> = {}) {
  const onModeChange = vi.fn();
  const onBudgetChange = vi.fn();
  render(
    <CodeAnalysisSettings
      mode="on_demand"
      monthlyBudgetUsd={5}
      currentMonthCostUsd={1.25}
      onModeChange={onModeChange}
      onBudgetChange={onBudgetChange}
      {...overrides}
    />,
  );
  return { onModeChange, onBudgetChange };
}

describe("CodeAnalysisSettings (Task 22 — 三モード + 費用表示)", () => {
  test("renders the three analysis modes as radios", () => {
    renderPanel();
    expect(screen.getByRole("radio", { name: /使用しない/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /必要なときだけ/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /取り込み後に自動/ })).toBeInTheDocument();
  });

  test("marks the current mode as checked", () => {
    renderPanel({ mode: "on_demand" });
    expect(screen.getByRole("radio", { name: /必要なときだけ/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("radio", { name: /使用しない/ })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  test("selecting 取り込み後に自動 calls onModeChange('automatic')", async () => {
    const user = userEvent.setup();
    const { onModeChange } = renderPanel({ mode: "on_demand" });
    await user.click(screen.getByRole("radio", { name: /取り込み後に自動/ }));
    expect(onModeChange).toHaveBeenCalledWith("automatic");
  });

  test("selecting 使用しない calls onModeChange('off')", async () => {
    const user = userEvent.setup();
    const { onModeChange } = renderPanel({ mode: "on_demand" });
    await user.click(screen.getByRole("radio", { name: /使用しない/ }));
    expect(onModeChange).toHaveBeenCalledWith("off");
  });

  test("automatic mode explains cost and target scope (excludes weakly-grounded candidates)", () => {
    renderPanel({ mode: "automatic" });
    // 対象範囲(高信頼の公式 GitHub + 採用済み GitHub)と、根拠の弱い候補は対象外である旨。
    expect(screen.getByText(/公式|active|採用済み/)).toBeInTheDocument();
    expect(screen.getByText(/根拠の弱い|suggested|候補は解析しません/)).toBeInTheDocument();
    // 既存ライブラリ全件を即時実行しない(バックフィルは別確認)ことも述べる。
    expect(screen.getByText(/バックフィル|まとめて|別途/)).toBeInTheDocument();
  });

  test("budget stepper reflects the value formatted as USD and steps by 0.50", async () => {
    const user = userEvent.setup();
    const { onBudgetChange } = renderPanel({ monthlyBudgetUsd: 5 });
    expect(screen.getByRole("status", { name: "月額予算" })).toHaveTextContent("$5.00");
    await user.click(screen.getByRole("button", { name: "月額予算を増やす" }));
    expect(onBudgetChange).toHaveBeenCalledWith(5.5);
  });

  test("budget stepper disables the increment at the 100.00 ceiling", () => {
    renderPanel({ monthlyBudgetUsd: 100 });
    expect(screen.getByRole("button", { name: "月額予算を増やす" })).toBeDisabled();
  });

  test("budget stepper disables the decrement at the 0.00 floor", () => {
    renderPanel({ monthlyBudgetUsd: 0 });
    expect(screen.getByRole("button", { name: "月額予算を減らす" })).toBeDisabled();
  });

  test("shows the current month code_analysis cost when provided", () => {
    renderPanel({ currentMonthCostUsd: 2.5 });
    expect(screen.getByText("$2.50")).toBeInTheDocument();
  });

  test("shows a placeholder for the current month cost when not available", () => {
    renderPanel({ currentMonthCostUsd: null });
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
