import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { FigureTableBlock } from "@/components/viewer/FigureTableBlock";
import type { DocBlock } from "@/components/viewer/document-types";
import type { TranslationUnitItem } from "@alinea/api-client";

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
});
