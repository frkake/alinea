import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentType } from "react";
import { describe, expect, test, vi } from "vitest";
import { FigureTableBlock, type FigureTableBlockProps } from "@/components/viewer/FigureTableBlock";
import type { DocBlock } from "@/components/viewer/document-types";
import type { TranslationUnitItem } from "@alinea/api-client";

type TestTableAction = {
  status: "idle" | "pending" | "succeeded" | "error";
  start: () => void;
  retry: () => void;
  error: string | null;
};

const ActionableFigureTableBlock = FigureTableBlock as ComponentType<
  FigureTableBlockProps & { tableTranslation: TestTableAction }
>;

function canonicalTableBlock(): DocBlock {
  return {
    id: "blk-canonical-table",
    type: "table",
    number: "5",
    raw: '<table><tr><th colspan="2">Metric</th></tr><tr><td rowspan="2">Our approach</td><td>99.1</td></tr><tr><td>Stable result $x^2$</td></tr></table>',
    caption: [{ t: "text", v: "Original table caption." }],
    source_grid: {
      supported: true,
      source_format: "html",
      reason: null,
      rows: [
        [
          {
            id: "r0c0",
            source: "Metric",
            header: true,
            rowspan: 1,
            colspan: 2,
            translatable: true,
            math: [],
            latex_body_start: null,
            latex_body_end: null,
            latex_wrappers: [],
          },
        ],
        [
          {
            id: "r1c0",
            source: "Our approach",
            header: false,
            rowspan: 2,
            colspan: 1,
            translatable: true,
            math: [],
            latex_body_start: null,
            latex_body_end: null,
            latex_wrappers: [],
          },
          {
            id: "r1c1",
            source: "99.1",
            header: false,
            rowspan: 1,
            colspan: 1,
            translatable: false,
            math: [],
            latex_body_start: null,
            latex_body_end: null,
            latex_wrappers: [],
          },
        ],
        [
          {
            id: "r2c0",
            source: "Stable result $x^2$",
            header: false,
            rowspan: 1,
            colspan: 1,
            translatable: true,
            math: ["$x^2$"],
            latex_body_start: null,
            latex_body_end: null,
            latex_wrappers: [],
          },
        ],
      ],
    },
  } as DocBlock;
}

function completeTableUnit(): TranslationUnitItem {
  return {
    unit_id: "unit_table",
    block_id: "blk-canonical-table",
    text_ja: "表キャプション。 指標 提案手法 安定した結果",
    content_ja: {
      kind: "table",
      version: 1,
      caption: [{ t: "text", v: "翻訳済み表キャプション。" }],
      cells: [["指標"], ["提案手法", null], ["安定した結果 $x^2$"]],
    },
    state: "machine",
    quality_flags: [],
    proposal: null,
  };
}

describe("FigureTableBlock", () => {
  test("renders a figure asset with its caption", () => {
    const block: DocBlock = {
      id: "blk-fig",
      type: "figure",
      number: "1",
      asset_url: "/api/assets/abc",
      caption: [{ t: "text", v: "Overview." }],
    };
    render(<FigureTableBlock block={block} />);
    expect(screen.getByRole("img", { name: "図1" })).toHaveAttribute("src", "/api/assets/abc");
    expect(screen.getByText("Overview.")).toBeInTheDocument();
  });

  test("renders LaTeX tabular raw content as a table", () => {
    const block: DocBlock = {
      id: "blk-table",
      type: "table",
      number: "2",
      raw: "\\begin{tabular}{ll} Method & Score \\\\ Ours & $x^2$ \\\\ \\end{tabular}",
      caption: [{ t: "text", v: "Scores." }],
    };
    render(<FigureTableBlock block={block} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("Method")).toBeInTheDocument();
    expect(screen.getByText("Ours")).toBeInTheDocument();
    expect(screen.getByText("Scores.")).toBeInTheDocument();
  });

  test("cleans common LaTeX commands inside table cells", () => {
    const block: DocBlock = {
      id: "blk-table",
      type: "table",
      number: "2",
      raw: "\\begin{tabular}{ll} Metric & Value \\\\ Acc & 1.2 \\pm 0.1 \\\\ State & x_i \\\\ \\end{tabular}",
      caption: [{ t: "text", v: "Scores." }],
    };
    const { container } = render(<FigureTableBlock block={block} />);
    expect(screen.getByText("1.2 ± 0.1")).toBeInTheDocument();
    expect(container.textContent).not.toContain("\\pm");
    expect(container.querySelector(".katex")).not.toBeNull();
  });

  test("preserves colspan and rowspan from html tables", () => {
    const block: DocBlock = {
      id: "blk-html-table",
      type: "table",
      number: "3",
      raw: '<table><tr><th colspan="2">Group</th><th>Score</th></tr><tr><td rowspan="2">A</td><td>x</td><td>1</td></tr><tr><td>y</td><td>2</td></tr></table>',
      caption: [{ t: "text", v: "Merged cells." }],
    };
    const { container } = render(<FigureTableBlock block={block} />);

    const group = screen.getByText("Group").closest("th");
    const a = screen.getByText("A").closest("td");
    expect(group).toHaveAttribute("colspan", "2");
    expect(a).toHaveAttribute("rowspan", "2");
    expect(container.querySelectorAll("tr")).toHaveLength(3);
  });

  test.each([
    {
      name: "raw byte limit",
      raw: `<table><tr><td>${"x".repeat(256_001)}</td></tr></table>`,
    },
    {
      name: "row limit",
      raw: `<table>${"<tr><td>x</td></tr>".repeat(513)}</table>`,
    },
    {
      name: "cell limit",
      raw: `<table><tr>${"<td>x</td>".repeat(513)}</tr></table>`,
    },
    {
      name: "nesting limit",
      raw: `<table><tr><td>${"<span>".repeat(33)}x${"</span>".repeat(33)}</td></tr></table>`,
    },
  ])("fails oversized legacy table fallback closed for $name", ({ raw }) => {
    render(
      <FigureTableBlock
        block={{
          id: "bounded-legacy-table",
          type: "table",
          number: "9",
          raw,
          asset_url: "/api/assets/table-fallback",
          caption: [{ t: "text", v: "Bounded fallback." }],
        }}
      />,
    );

    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "表9" })).toHaveAttribute(
      "src",
      "/api/assets/table-fallback",
    );
    expect(screen.getByText("Bounded fallback.")).toBeInTheDocument();
  });

  test("preserves multicolumn and multirow from latex tables", () => {
    const block: DocBlock = {
      id: "blk-latex-table",
      type: "table",
      number: "4",
      raw: String.raw`\begin{tabular}{ccc}
        \multicolumn{2}{c}{Group} & Score \\
        \multirow{2}{*}{A} & x & 1 \\
         & y & 2 \\
      \end{tabular}`,
      caption: [{ t: "text", v: "Merged latex cells." }],
    };
    render(<FigureTableBlock block={block} />);

    const group = screen.getByText("Group").closest("th");
    const a = screen.getByText("A").closest("td");
    expect(group).toHaveAttribute("colspan", "2");
    expect(a).toHaveAttribute("rowspan", "2");
  });

  test("cleans itemize and custom highlight commands inside latex table cells", () => {
    const block: DocBlock = {
      id: "blk-table",
      type: "table",
      number: "1",
      raw: String.raw`\begin{tabularx}{\linewidth}{X}
        \textbf{Chain of ideas}:
        \begin{itemize}[nosep]
          \item $I_{-1}$~\citep{paper2024}: improves \mybox{red!15}{idea generation}.
        \end{itemize} \\
      \end{tabularx}`,
      caption: [{ t: "text", v: "Example." }],
    };
    const { container } = render(<FigureTableBlock block={block} />);

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(container.textContent).toContain("Chain of ideas");
    expect(container.textContent).toContain("idea generation");
    expect(container.textContent).not.toContain("\\begin");
    expect(container.textContent).not.toContain("\\mybox");
    expect(container.textContent).not.toContain("\\citep");
  });

  test("does not render legacy inline SVG raw content", () => {
    const block: DocBlock = {
      id: "blk-svg",
      type: "figure",
      number: "3",
      raw: '<div class="ltx_flex_figure"><svg width="40" height="20"><title>chart</title></svg></div>',
      caption: [{ t: "text", v: "Inline chart." }],
    };
    const { container } = render(<FigureTableBlock block={block} />);
    expect(screen.queryByRole("img", { name: "図3" })).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeNull();
    expect(screen.getByText("Inline chart.")).toBeInTheDocument();
  });

  test("renders a latex tabular embedded in a figure block", () => {
    const block: DocBlock = {
      id: "blk-fig-table",
      type: "figure",
      number: "5",
      raw: "\\begin{tabular}{c} User - Objective \\\\ Generate pytest tests \\\\ \\end{tabular}",
      caption: [{ t: "text", v: "Workflow example." }],
    };
    render(<FigureTableBlock block={block} />);

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("User - Objective")).toBeInTheDocument();
    expect(screen.getByText("Generate pytest tests")).toBeInTheDocument();
  });

  test("renders tcolorbox prompt tables as single-column table content", () => {
    const block: DocBlock = {
      id: "blk-prompt-table",
      type: "table",
      raw: String.raw`\begin{table}[h!]
        \caption{Prompt}
        \label{tab:prompt}
        \begin{tcolorbox}[colframe=orange]
        \small
        \texttt{You are a master of literature searching.\\
        Topic: \textbf{[Topic]}\\
        Queries: \dots}
        \end{tcolorbox}
      \end{table}`,
      caption: [{ t: "text", v: "Prompt." }],
    };
    const { container } = render(<FigureTableBlock block={block} />);

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(container.textContent).toContain("You are a master of literature searching.");
    expect(container.textContent).toContain("Topic: [Topic]");
    expect(container.textContent).not.toContain("\\texttt");
    expect(container.textContent).not.toContain("tcolorbox");
  });

  test("does not render raw composite figures with remote image sources", () => {
    const block: DocBlock = {
      id: "blk-svg",
      type: "figure",
      number: "3",
      raw: '<svg><foreignObject><img src="2607.05247v1/figures/overview/cube_render_grid.png"></foreignObject></svg>',
      caption: [{ t: "text", v: "Composite chart." }],
    };
    const { container } = render(<FigureTableBlock block={block} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("foreignObject")).toBeNull();
  });

  test("does not render encoded active HTML from a legacy raw figure", () => {
    const block: DocBlock = {
      id: "blk-svg",
      type: "figure",
      number: "3",
      raw: '<iframe srcdoc="&lt;script&gt;document.body.dataset.pwned=1&lt;/script&gt;"></iframe><svg onload="document.body.dataset.pwned=1"></svg>',
      caption: [{ t: "text", v: "Composite chart." }],
    };
    const { container } = render(<FigureTableBlock block={block} />);
    expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("svg")).toBeNull();
    expect(document.body.dataset.pwned).toBeUndefined();
  });

  test("uses the translated caption as the primary caption when a translated unit exists", () => {
    const block: DocBlock = {
      id: "blk-fig",
      type: "figure",
      number: "4",
      caption: [{ t: "text", v: "Original caption." }],
    };
    const unit: TranslationUnitItem = {
      unit_id: "unit_fig",
      block_id: "blk-fig",
      text_ja: "翻訳済みキャプション。",
      content_ja: [{ t: "text", v: "翻訳済みキャプション。" }],
      state: "machine",
      quality_flags: [],
      proposal: null,
    };
    render(<FigureTableBlock block={block} unit={unit} />);
    expect(screen.getByText("翻訳済みキャプション。")).toBeInTheDocument();
    expect(screen.getByText("Original: Original caption.")).toBeInTheDocument();
  });

  test("overlays a strict typed translation onto the canonical physical grid", () => {
    const { container } = render(
      <FigureTableBlock block={canonicalTableBlock()} unit={completeTableUnit()} />,
    );

    expect(screen.getByText("指標").closest("th")).toHaveAttribute("colspan", "2");
    expect(screen.getByText("提案手法").closest("td")).toHaveAttribute("rowspan", "2");
    expect(screen.getByText("99.1")).toBeInTheDocument();
    expect(screen.getByText(/安定した結果/)).toBeInTheDocument();
    expect(container.querySelector(".katex")).not.toBeNull();
    expect(container.textContent).not.toContain("$x^2$");
    expect(screen.getByText("翻訳済み表キャプション。")).toBeInTheDocument();
    expect(screen.getByText("Original: Original table caption.")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(table.parentElement).toHaveStyle({
      overflowX: "auto",
      width: "100%",
      maxWidth: "100%",
      minWidth: 0,
      boxSizing: "border-box",
    });
    expect(table.closest("figure")).toHaveStyle({
      maxWidth: "100%",
      minWidth: 0,
      boxSizing: "border-box",
    });
  });

  test("renders protected display delimiters inline without leaking the delimiters", () => {
    const block = canonicalTableBlock();
    const mathCell = block.source_grid?.rows[2]?.[0];
    if (!mathCell) throw new Error("canonical test fixture is incomplete");
    mathCell.source = String.raw`Stable result \[x_{i}^{2}\]`;
    mathCell.math = [String.raw`\[x_{i}^{2}\]`];
    const unit = completeTableUnit();
    unit.content_ja = {
      kind: "table",
      version: 1,
      caption: null,
      cells: [["指標"], ["提案手法", null], [String.raw`安定した結果 \[x_{i}^{2}\]`]],
    };

    const { container } = render(<FigureTableBlock block={block} unit={unit} />);

    expect(screen.getByText(/安定した結果/)).toBeInTheDocument();
    expect(container.querySelector(".katex")).not.toBeNull();
    expect(container.textContent).not.toContain(String.raw`\[x_{i}^{2}\]`);
  });

  test("renders the full protected math grammar after exact multiset reordering", () => {
    const block = canonicalTableBlock();
    const mathCell = block.source_grid?.rows[2]?.[0];
    if (!mathCell) throw new Error("canonical test fixture is incomplete");
    const fragments = ["$a$", "$$b$$", String.raw`\(c\)`, String.raw`\[d\]`];
    mathCell.source = String.raw`Source $a$ then $$b$$ and \(c\) plus \[d\]`;
    mathCell.math = fragments;
    const unit = completeTableUnit();
    unit.content_ja = {
      kind: "table",
      version: 1,
      caption: null,
      cells: [["指標"], ["提案手法", null], [String.raw`結果 \[d\]、\(c\)、$$b$$、$a$`]],
    };

    const { container } = render(<FigureTableBlock block={block} unit={unit} />);

    expect(container.querySelectorAll(".katex")).toHaveLength(4);
    for (const fragment of fragments) expect(container.textContent).not.toContain(fragment);
  });

  test("does not interpret an undelimited canonical identifier as whole-cell math", () => {
    const block: DocBlock = {
      id: "identifier-table",
      type: "table",
      raw: "<table><tr><td>model_name improves</td></tr></table>",
      source_grid: {
        supported: true,
        source_format: "html",
        reason: null,
        rows: [
          [
            {
              id: "r0c0",
              source: "model_name improves",
              header: false,
              rowspan: 1,
              colspan: 1,
              translatable: true,
              math: [],
              latex_body_start: null,
              latex_body_end: null,
              latex_wrappers: [],
            },
          ],
        ],
      },
    };

    const { container } = render(<FigureTableBlock block={block} />);

    expect(screen.getByText("model_name improves")).toBeInTheDocument();
    expect(container.querySelector(".katex")).toBeNull();
  });

  test("fails closed to source cells and caption for malformed typed table content", () => {
    const malformed = completeTableUnit();
    malformed.text_ja = "不正な射影をキャプションにしない";
    malformed.content_ja = {
      kind: "table",
      version: 1,
      caption: [{ t: "text", v: "不正な翻訳キャプション" }],
      cells: [["次元が不足"]],
    };

    render(<FigureTableBlock block={canonicalTableBlock()} unit={malformed} />);

    expect(screen.getByText("Metric")).toBeInTheDocument();
    expect(screen.getByText("Our approach")).toBeInTheDocument();
    expect(screen.getByText("Original table caption.")).toBeInTheDocument();
    expect(screen.queryByText("次元が不足")).not.toBeInTheDocument();
    expect(screen.queryByText("不正な翻訳キャプション")).not.toBeInTheDocument();
    expect(screen.queryByText("不正な射影をキャプションにしない")).not.toBeInTheDocument();
  });

  test.each([
    {
      name: "blank target",
      content: {
        kind: "table",
        version: 1,
        caption: [{ t: "text", v: "表示してはいけない" }],
        cells: [["指標"], ["   ", null], ["安定"]],
      },
    },
    {
      name: "unknown inline key",
      content: {
        kind: "table",
        version: 1,
        caption: [{ t: "text", v: "表示してはいけない", unknown: true }],
        cells: [["指標"], ["提案手法", null], ["安定"]],
      },
    },
    {
      name: "control character",
      content: {
        kind: "table",
        version: 1,
        caption: [{ t: "text", v: "表示してはいけない" }],
        cells: [["指標"], ["提案\n手法", null], ["安定"]],
      },
    },
  ])("fails the entire typed result closed for $name", ({ content }) => {
    const invalid = completeTableUnit();
    invalid.content_ja = content;

    render(<FigureTableBlock block={canonicalTableBlock()} unit={invalid} />);

    expect(screen.getByText("Metric")).toBeInTheDocument();
    expect(screen.getByText("Our approach")).toBeInTheDocument();
    expect(screen.queryByText("表示してはいけない")).not.toBeInTheDocument();
  });

  test("fails closed when aggregate typed caption text exceeds the shared budget", () => {
    const invalid = completeTableUnit();
    invalid.content_ja = {
      kind: "table",
      version: 1,
      caption: Array.from({ length: 17 }, (_, index) => ({
        t: "text",
        v: `${index === 0 ? "OVERSIZED_CAPTION_SHOULD_NOT_RENDER" : ""}${"x".repeat(31_960)}`,
      })),
      cells: [["指標"], ["提案手法", null], ["安定した結果 $x^2$"]],
    };

    render(<FigureTableBlock block={canonicalTableBlock()} unit={invalid} />);

    expect(screen.getByText("Metric")).toBeInTheDocument();
    expect(screen.getByText("Original table caption.")).toBeInTheDocument();
    expect(screen.queryByText(/OVERSIZED_CAPTION_SHOULD_NOT_RENDER/)).not.toBeInTheDocument();
  });

  test.each([
    { name: "missing math", first: "指標", last: "安定した結果" },
    { name: "changed math", first: "指標", last: "安定した結果 $y^2$" },
    { name: "duplicated math", first: "指標", last: "安定した結果 $x^2$ $x^2$" },
    { name: "additional math", first: "指標 $z$", last: "安定した結果 $x^2$" },
  ])("fails the entire typed result closed for $name", ({ first, last }) => {
    const invalid = completeTableUnit();
    invalid.content_ja = {
      kind: "table",
      version: 1,
      caption: [{ t: "text", v: "表示してはいけない" }],
      cells: [[first], ["提案手法", null], [last]],
    };

    render(<FigureTableBlock block={canonicalTableBlock()} unit={invalid} />);

    expect(screen.getByText("Metric")).toBeInTheDocument();
    expect(screen.getByText("Our approach")).toBeInTheDocument();
    expect(screen.queryByText("表示してはいけない")).not.toBeInTheDocument();
  });

  test("shows the table translation action only for a canonical grid with untranslated targets", () => {
    const start = vi.fn();
    const retry = vi.fn();
    const { rerender } = render(
      <ActionableFigureTableBlock
        block={canonicalTableBlock()}
        tableTranslation={{ status: "idle", start, retry, error: null }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "この表を翻訳" }));
    expect(start).toHaveBeenCalledOnce();

    rerender(
      <ActionableFigureTableBlock
        block={canonicalTableBlock()}
        tableTranslation={{ status: "pending", start, retry, error: null }}
      />,
    );
    expect(screen.getByRole("button", { name: "この表を翻訳中…" })).toBeDisabled();

    rerender(
      <ActionableFigureTableBlock
        block={canonicalTableBlock()}
        tableTranslation={{ status: "error", start, retry, error: "翻訳に失敗しました" }}
      />,
    );
    expect(screen.getByText("翻訳に失敗しました")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "再試行" }));
    expect(retry).toHaveBeenCalledOnce();

    rerender(
      <ActionableFigureTableBlock
        block={canonicalTableBlock()}
        tableTranslation={{ status: "succeeded", start, retry, error: null }}
      />,
    );
    expect(screen.getByText("表を翻訳しました")).toBeInTheDocument();
  });

  test("hides the table action when the grid is absent or typed cells are complete", () => {
    const action: TestTableAction = {
      status: "idle",
      start: vi.fn(),
      retry: vi.fn(),
      error: null,
    };
    const { rerender } = render(
      <ActionableFigureTableBlock
        block={{ id: "legacy", type: "table", raw: "A & B \\\\" }}
        tableTranslation={action}
      />,
    );
    expect(screen.queryByRole("button", { name: "この表を翻訳" })).not.toBeInTheDocument();

    rerender(
      <ActionableFigureTableBlock
        block={canonicalTableBlock()}
        unit={completeTableUnit()}
        tableTranslation={action}
      />,
    );
    expect(screen.queryByRole("button", { name: "この表を翻訳" })).not.toBeInTheDocument();
  });

  test("keeps a typed caption and shows the action for a caption-only cells-null result", () => {
    const captionOnly = completeTableUnit();
    captionOnly.content_ja = {
      kind: "table",
      version: 1,
      caption: [{ t: "text", v: "翻訳済み表キャプション。" }],
      cells: null,
    };
    const action: TestTableAction = {
      status: "idle",
      start: vi.fn(),
      retry: vi.fn(),
      error: null,
    };

    render(
      <ActionableFigureTableBlock
        block={canonicalTableBlock()}
        unit={captionOnly}
        tableTranslation={action}
      />,
    );

    expect(screen.getByText("翻訳済み表キャプション。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "この表を翻訳" })).toBeInTheDocument();
  });

  test("hides the action for a supported canonical grid with no translatable targets", () => {
    const block = canonicalTableBlock();
    const sourceGrid = block.source_grid;
    if (!sourceGrid) throw new Error("canonical test fixture is incomplete");
    block.source_grid = {
      ...sourceGrid,
      rows: sourceGrid.rows.map((row) => row.map((cell) => ({ ...cell, translatable: false }))),
    };

    render(
      <ActionableFigureTableBlock
        block={block}
        tableTranslation={{ status: "idle", start: vi.fn(), retry: vi.fn(), error: null }}
      />,
    );

    expect(screen.queryByRole("button", { name: "この表を翻訳" })).not.toBeInTheDocument();
  });

  test("rejects a canonical cell with an unknown wire field and uses the raw fallback", () => {
    const block = canonicalTableBlock();
    const firstRow = block.source_grid?.rows[0];
    const firstCell = firstRow?.[0];
    if (!firstRow || !firstCell) throw new Error("canonical test fixture is incomplete");
    firstRow[0] = {
      ...firstCell,
      unexpected: true,
    } as never;

    render(
      <ActionableFigureTableBlock
        block={block}
        tableTranslation={{ status: "idle", start: vi.fn(), retry: vi.fn(), error: null }}
      />,
    );

    expect(screen.getByText("Metric")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "この表を翻訳" })).not.toBeInTheDocument();
  });

  test("renders a load-on-demand button for a deferred figure", () => {
    const start = vi.fn();
    const block: DocBlock = {
      id: "blk-deferred",
      type: "figure",
      number: "9",
      deferred: true,
      caption: [{ t: "text", v: "Deferred overview." }],
    };
    render(
      <FigureTableBlock
        block={block}
        figureMaterialization={{ status: "idle", start, retry: vi.fn(), error: null }}
      />,
    );
    expect(screen.getByText("図が多いため未読込です")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "画像を読み込む" });
    fireEvent.click(button);
    expect(start).toHaveBeenCalledTimes(1);
  });

  test("shows loading state while a deferred figure materializes", () => {
    const block: DocBlock = { id: "blk-deferred", type: "figure", deferred: true };
    render(
      <FigureTableBlock
        block={block}
        figureMaterialization={{ status: "pending", start: vi.fn(), retry: vi.fn(), error: null }}
      />,
    );
    expect(screen.getByRole("button", { name: "画像を読み込み中…" })).toBeDisabled();
  });

  test("offers retry when a deferred figure load fails", () => {
    const retry = vi.fn();
    const block: DocBlock = { id: "blk-deferred", type: "figure", deferred: true };
    render(
      <FigureTableBlock
        block={block}
        figureMaterialization={{
          status: "error",
          start: vi.fn(),
          retry,
          error: "画像の読み込みに失敗しました",
        }}
      />,
    );
    expect(screen.getByText("画像の読み込みに失敗しました")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "再試行" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });
});
