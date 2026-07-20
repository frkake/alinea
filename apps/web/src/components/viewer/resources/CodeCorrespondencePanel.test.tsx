import type { ComponentProps } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import type { CorrespondenceOut, RunOut } from "@alinea/api-client";
import { CodeCorrespondencePanel } from "@/components/viewer/resources/CodeCorrespondencePanel";

function run(overrides: Partial<RunOut> = {}): RunOut {
  return {
    run_id: "run_1",
    resource_id: "res_1",
    revision_id: "rev_1",
    commit_sha: "abc123def456abc123def456abc123def456abcd",
    trigger: "on_demand",
    status: "succeeded",
    stale: false,
    estimated_cost_usd: "0.12",
    actual_cost_usd: "0.10",
    error: null,
    created_at: "2026-07-18T00:00:00Z",
    finished_at: "2026-07-18T00:05:00Z",
    ...overrides,
  };
}

function correspondence(overrides: Partial<CorrespondenceOut> = {}): CorrespondenceOut {
  return {
    paper_anchor: { revision_id: "rev_1", block_id: "blk_5", section_id: "sec_2" },
    claim_text: "The rectified flow objective is minimized via the loss in Eq. (3).",
    path: "src/train.py",
    symbol: "rectified_flow_loss",
    start_line: 40,
    end_line: 58,
    code_excerpt: "def rectified_flow_loss(...):\n    ...",
    explanation_ja: "この関数が式(3)の損失を実装している。",
    confidence: "high",
    ...overrides,
  };
}

function renderPanel(overrides: Partial<ComponentProps<typeof CodeCorrespondencePanel>> = {}) {
  const onJumpBlock = vi.fn();
  render(
    <CodeCorrespondencePanel
      repoUrl="https://github.com/gnobitab/RectifiedFlow"
      run={run()}
      correspondences={[correspondence()]}
      stale={false}
      onJumpBlock={onJumpBlock}
      {...overrides}
    />,
  );
  return { onJumpBlock };
}

describe("CodeCorrespondencePanel (Task 22 — 対応結果)", () => {
  test("renders the symbol and Japanese explanation of a correspondence", () => {
    renderPanel();
    expect(screen.getByText("rectified_flow_loss")).toBeInTheDocument();
    expect(screen.getByText(/この関数が式\(3\)の損失を実装している。/)).toBeInTheDocument();
  });

  test("paper anchor jumps to the viewer block (not a new tab)", async () => {
    const user = userEvent.setup();
    const { onJumpBlock } = renderPanel();
    await user.click(screen.getByRole("button", { name: /論文の該当箇所/ }));
    expect(onJumpBlock).toHaveBeenCalledWith("blk_5");
  });

  test("GitHub anchor links to the fixed commit and line range in a new tab", () => {
    renderPanel();
    const link = screen.getByRole("link", { name: /src\/train\.py/ });
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/gnobitab/RectifiedFlow/blob/abc123def456abc123def456abc123def456abcd/src/train.py#L40-L58",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  test("high and medium correspondences render normally; low folds into 関連候補", () => {
    renderPanel({
      correspondences: [
        correspondence({ symbol: "high_sym", confidence: "high" }),
        correspondence({ symbol: "medium_sym", confidence: "medium" }),
        correspondence({ symbol: "low_sym", confidence: "low" }),
      ],
    });
    // high / medium are visible directly.
    expect(screen.getByText("high_sym")).toBeInTheDocument();
    expect(screen.getByText("medium_sym")).toBeInTheDocument();
    // low is folded away behind a "関連候補" disclosure.
    expect(screen.getByText(/関連候補/)).toBeInTheDocument();
    expect(screen.queryByText("low_sym")).toBeNull();
  });

  test("expanding 関連候補 reveals the low-confidence correspondences", async () => {
    const user = userEvent.setup();
    renderPanel({
      correspondences: [correspondence({ symbol: "low_sym", confidence: "low" })],
    });
    await user.click(screen.getByRole("button", { name: /関連候補/ }));
    expect(screen.getByText("low_sym")).toBeInTheDocument();
  });

  test("zero correspondences on a succeeded run shows 対応箇所を特定できませんでした (not コードが無い)", () => {
    renderPanel({ correspondences: [] });
    expect(screen.getByText("対応箇所を特定できませんでした")).toBeInTheDocument();
    expect(screen.queryByText(/コードが無い/)).toBeNull();
  });

  test("stale result shows a 古い結果 banner", () => {
    renderPanel({ stale: true });
    expect(screen.getByText(/古い結果|リポジトリが更新/)).toBeInTheDocument();
  });

  test("failed run shows a failure notice and does not leak partial correspondences", () => {
    renderPanel({
      run: run({ status: "failed", error: "provider_error" }),
      correspondences: [],
    });
    expect(screen.getByText(/解析に失敗しました/)).toBeInTheDocument();
  });

  test("waiting_budget run shows the budget-exceeded message and a settings link", () => {
    renderPanel({
      run: run({ status: "waiting_budget" }),
      correspondences: [],
    });
    expect(screen.getByText(/予算を超えるため待機中/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /設定/ })).toBeInTheDocument();
  });
});
