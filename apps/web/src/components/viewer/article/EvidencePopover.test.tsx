import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, test, vi } from "vitest";
import { viewerGetBlock, type EvidenceItemOut } from "@alinea/api-client";
import { EvidencePopover } from "@/components/viewer/article/EvidencePopover";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return { ...actual, viewerGetBlock: vi.fn() };
});

function evidence(): EvidenceItemOut[] {
  return [
    { ref: 1, display: "§2.2 ¶3", anchor: { revision_id: "rev_1", block_id: "blk-2-2-p3", display: "§2.2 ¶3" } },
  ];
}

describe("EvidencePopover (1h §5.5)", () => {
  test("renders the evidence chip, an original-text preview, and a jump link", async () => {
    vi.mocked(viewerGetBlock).mockResolvedValue({
      data: {
        block: { id: "blk-2-2-p3", type: "paragraph", inlines: [{ t: "text", v: "Straight paths are computationally attractive." }] },
        section_id: "sec-2",
        display: "§2.2 ¶3",
        translation: null,
      },
    } as never);
    const onJumpToAnchor = vi.fn();
    const anchorRef = createRef<HTMLButtonElement>();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <button type="button" ref={anchorRef}>
          anchor
        </button>
        <EvidencePopover
          open
          onClose={vi.fn()}
          anchorRef={anchorRef}
          revisionId="rev_1"
          evidence={evidence()}
          onJumpToAnchor={onJumpToAnchor}
        />
      </QueryClientProvider>,
    );

    expect(screen.getByText("§2.2 ¶3")).toBeInTheDocument();
    expect(await screen.findByText(/Straight paths are computationally attractive/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("原文で見る →"));
    expect(onJumpToAnchor).toHaveBeenCalledWith(
      expect.objectContaining({ block_id: "blk-2-2-p3", display: "§2.2 ¶3" }),
    );
  });
});
