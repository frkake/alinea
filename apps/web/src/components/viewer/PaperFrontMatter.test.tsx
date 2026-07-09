import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import {
  isPaperFrontMatterBlock,
  PaperFrontMatterBlock,
} from "@/components/viewer/PaperFrontMatter";
import type { DocBlock } from "@/components/viewer/document-types";

function frontMatterBlock(): DocBlock {
  return {
    id: "blk-meta",
    type: "paragraph",
    inlines: [
      {
        t: "text",
        v: "[1*] Zhiyuan Zhao Guang Liang Yuanhong Zheng Siyi Qian Wei Li Lindong Lu Yuhang Zang Lijun Wu Yu Qiao 1]Shanghai Artificial Intelligence Laboratory 2]Peking University 3]Shanghai Jiao Tong University [* Equal contribution ✉ Corresponding author ‡Project leader] He, ",
      },
      {
        t: "url",
        v: "github.com/opendatalab/MinerU",
        href: "https://github.com/opendatalab/MinerU",
      },
      { t: "text", v: " " },
      {
        t: "url",
        v: "huggingface.co/opendatalab/MinerU2.5-2509-1.2B",
        href: "https://huggingface.co/opendatalab/MinerU2.5-2509-1.2B",
      },
    ],
  };
}

describe("PaperFrontMatterBlock", () => {
  test("detects and renders paper metadata separately from body text", () => {
    const block = frontMatterBlock();
    expect(isPaperFrontMatterBlock(block)).toBe(true);

    const { container } = render(<PaperFrontMatterBlock block={block} />);

    expect(screen.getByText("論文メタデータ")).toBeInTheDocument();
    expect(screen.getByText("著者")).toBeInTheDocument();
    expect(screen.getByText("Zhiyuan Zhao")).toBeInTheDocument();
    expect(screen.getByText("Yu Qiao")).toBeInTheDocument();
    expect(screen.getByText("所属")).toBeInTheDocument();
    expect(screen.getByText("1. Shanghai Artificial Intelligence Laboratory")).toBeInTheDocument();
    expect(screen.getByText("2. Peking University")).toBeInTheDocument();
    expect(screen.getByText("3. Shanghai Jiao Tong University")).toBeInTheDocument();
    expect(screen.getByText("Equal contribution")).toBeInTheDocument();
    expect(screen.getByText("Corresponding author")).toBeInTheDocument();
    expect(screen.getByText("Project leader")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "GitHub opendatalab/MinerU" })).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Hugging Face opendatalab/MinerU2.5-2509-1.2B" }),
    ).toBeVisible();
    expect(container).not.toHaveTextContent("[1*]");
    expect(container).not.toHaveTextContent("He,");
  });
});
