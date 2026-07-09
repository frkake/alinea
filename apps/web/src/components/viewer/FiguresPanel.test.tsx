import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { viewerListFigures, viewerListReferences, type ReferenceItem } from "@alinea/api-client";
import { useViewerStore } from "@/stores/viewer-store";
import { FiguresPanel } from "./FiguresPanel";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    ingestArxiv: vi.fn(),
    viewerListFigures: vi.fn(),
    viewerListReferences: vi.fn(),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function reference(overrides: Partial<ReferenceItem> = {}): ReferenceItem {
  return {
    ref_id: "ref-1",
    aliases: ["bib-1"],
    number: "[1]",
    raw: "First reference.",
    authors: null,
    title: null,
    venue_year: null,
    arxiv_id: null,
    doi: null,
    url: null,
    in_library: null,
    ...overrides,
  };
}

describe("FiguresPanel references", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useViewerStore.setState({ pendingReferenceId: null });
    vi.mocked(viewerListFigures).mockResolvedValue({ data: { items: [] } } as never);
    vi.mocked(viewerListReferences).mockResolvedValue({
      data: {
        items: [
          reference(),
          reference({
            ref_id: "ref-2",
            aliases: ["bib-2"],
            number: "[2]",
            raw: "Second reference.",
            doi: "10.1000/example",
          }),
        ],
      },
    } as never);
  });

  test("focuses and expands the matching reference when opened from a citation", async () => {
    useViewerStore.setState({ pendingReferenceId: "bib2" });
    renderWithClient(<FiguresPanel itemId="li_1" revisionId="rev_1" />);

    expect(await screen.findByText("Second reference.")).toBeInTheDocument();
    expect(await screen.findByText("DOI")).toBeInTheDocument();
    await waitFor(() => expect(useViewerStore.getState().pendingReferenceId).toBeNull());
  });

  test("shows a clear message when a citation cannot be resolved", async () => {
    useViewerStore.setState({ pendingReferenceId: "missing-ref" });
    renderWithClient(<FiguresPanel itemId="li_1" revisionId="rev_1" />);

    expect(
      await screen.findByText("引用 missing-ref に対応する参考文献が見つかりません。"),
    ).toBeInTheDocument();
    await waitFor(() => expect(useViewerStore.getState().pendingReferenceId).toBeNull());
  });
});
