import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  viewerListRevisions,
  viewerRevisionDiff,
  type RevisionListItem,
} from "@alinea/api-client";
import { RevisionDiffPanel } from "@/components/viewer/RevisionDiffPanel";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    viewerListRevisions: vi.fn(),
    viewerRevisionDiff: vi.fn(),
  };
});

function makeRevision(overrides: Partial<RevisionListItem> = {}): RevisionListItem {
  return {
    id: "rev_1",
    quality_level: "A",
    source_version: "v1",
    parser_version: "1.0.0",
    created_at: "2026-07-01T10:00:00+09:00",
    is_current: false,
    ...overrides,
  };
}

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("RevisionDiffPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test("hides the section entirely when only one revision exists", async () => {
    vi.mocked(viewerListRevisions).mockResolvedValue({
      data: { items: [makeRevision({ id: "rev_1", is_current: true })] },
    } as never);

    renderWithClient(<RevisionDiffPanel paperId="pap_1" />);

    await waitFor(() => expect(viewerListRevisions).toHaveBeenCalledOnce());
    expect(screen.queryByText("改版差分")).not.toBeInTheDocument();
  });

  test("shows the section heading when two or more revisions exist", async () => {
    vi.mocked(viewerListRevisions).mockResolvedValue({
      data: {
        items: [
          makeRevision({ id: "rev_1", source_version: "v1", created_at: "2026-07-01T10:00:00+09:00" }),
          makeRevision({ id: "rev_2", source_version: "v2", is_current: true, created_at: "2026-07-02T10:00:00+09:00" }),
        ],
      },
    } as never);
    vi.mocked(viewerRevisionDiff).mockResolvedValue({
      data: {
        from_revision_id: "rev_1",
        to_revision_id: "rev_2",
        stats: { added: 3, removed: 1, changed: 2, unchanged: 10 },
        changes: [],
      },
    } as never);

    renderWithClient(<RevisionDiffPanel paperId="pap_1" />);

    expect(await screen.findByText("改版差分")).toBeInTheDocument();
  });

  test("auto-selects adjacent latest two revisions (newest pair) by default", async () => {
    vi.mocked(viewerListRevisions).mockResolvedValue({
      data: {
        items: [
          makeRevision({ id: "rev_1", source_version: "v1", created_at: "2026-07-01T10:00:00+09:00" }),
          makeRevision({ id: "rev_2", source_version: "v2", created_at: "2026-07-02T10:00:00+09:00" }),
          makeRevision({ id: "rev_3", source_version: "v3", is_current: true, created_at: "2026-07-03T10:00:00+09:00" }),
        ],
      },
    } as never);
    vi.mocked(viewerRevisionDiff).mockResolvedValue({
      data: {
        from_revision_id: "rev_2",
        to_revision_id: "rev_3",
        stats: { added: 3, removed: 1, changed: 2, unchanged: 10 },
        changes: [],
      },
    } as never);

    renderWithClient(<RevisionDiffPanel paperId="pap_1" />);

    await waitFor(() =>
      expect(viewerRevisionDiff).toHaveBeenCalledWith(
        expect.objectContaining({
          path: { paper_id: "pap_1" },
          query: { from: "rev_2", to: "rev_3" },
          throwOnError: true,
        }),
      ),
    );
  });

  test("shows added/removed/changed stats from the diff response", async () => {
    vi.mocked(viewerListRevisions).mockResolvedValue({
      data: {
        items: [
          makeRevision({ id: "rev_1", source_version: "v1", created_at: "2026-07-01T10:00:00+09:00" }),
          makeRevision({ id: "rev_2", source_version: "v2", is_current: true, created_at: "2026-07-02T10:00:00+09:00" }),
        ],
      },
    } as never);
    vi.mocked(viewerRevisionDiff).mockResolvedValue({
      data: {
        from_revision_id: "rev_1",
        to_revision_id: "rev_2",
        stats: { added: 5, removed: 2, changed: 3, unchanged: 20 },
        changes: [],
      },
    } as never);

    renderWithClient(<RevisionDiffPanel paperId="pap_1" />);

    expect(await screen.findByText("+5")).toBeInTheDocument();
    expect(screen.getByText("-2")).toBeInTheDocument();
    expect(screen.getByText("~3")).toBeInTheDocument();
  });

  test("renders changed blocks with status, section_id, old_text and new_text collapsed", async () => {
    vi.mocked(viewerListRevisions).mockResolvedValue({
      data: {
        items: [
          makeRevision({ id: "rev_1", source_version: "v1", created_at: "2026-07-01T10:00:00+09:00" }),
          makeRevision({ id: "rev_2", source_version: "v2", is_current: true, created_at: "2026-07-02T10:00:00+09:00" }),
        ],
      },
    } as never);
    vi.mocked(viewerRevisionDiff).mockResolvedValue({
      data: {
        from_revision_id: "rev_1",
        to_revision_id: "rev_2",
        stats: { added: 0, removed: 0, changed: 1, unchanged: 5 },
        changes: [
          {
            status: "changed",
            block_id: "blk_1",
            block_type: "paragraph",
            section_id: "sec_intro",
            old_text: "旧テキスト",
            new_text: "新テキスト",
          },
        ],
      },
    } as never);

    renderWithClient(<RevisionDiffPanel paperId="pap_1" />);

    // The block summary with status should be visible
    expect(await screen.findByText(/changed/)).toBeInTheDocument();
    expect(screen.getByText(/sec_intro/)).toBeInTheDocument();
  });

  test("expands a collapsed block to show old_text and new_text on click", async () => {
    const user = userEvent.setup();
    vi.mocked(viewerListRevisions).mockResolvedValue({
      data: {
        items: [
          makeRevision({ id: "rev_1", source_version: "v1", created_at: "2026-07-01T10:00:00+09:00" }),
          makeRevision({ id: "rev_2", source_version: "v2", is_current: true, created_at: "2026-07-02T10:00:00+09:00" }),
        ],
      },
    } as never);
    vi.mocked(viewerRevisionDiff).mockResolvedValue({
      data: {
        from_revision_id: "rev_1",
        to_revision_id: "rev_2",
        stats: { added: 0, removed: 0, changed: 1, unchanged: 5 },
        changes: [
          {
            status: "changed",
            block_id: "blk_1",
            block_type: "paragraph",
            section_id: "sec_intro",
            old_text: "旧テキスト",
            new_text: "新テキスト",
          },
        ],
      },
    } as never);

    renderWithClient(<RevisionDiffPanel paperId="pap_1" />);

    // Wait for the block row to appear then click it
    const blockRow = await screen.findByText(/sec_intro/);
    await user.click(blockRow.closest("[data-block-row]") ?? blockRow);

    expect(await screen.findByText("旧テキスト")).toBeInTheDocument();
    expect(screen.getByText("新テキスト")).toBeInTheDocument();
  });

  test("does not include any adopt/switch revision UI", async () => {
    vi.mocked(viewerListRevisions).mockResolvedValue({
      data: {
        items: [
          makeRevision({ id: "rev_1", source_version: "v1", created_at: "2026-07-01T10:00:00+09:00" }),
          makeRevision({ id: "rev_2", source_version: "v2", is_current: true, created_at: "2026-07-02T10:00:00+09:00" }),
        ],
      },
    } as never);
    vi.mocked(viewerRevisionDiff).mockResolvedValue({
      data: {
        from_revision_id: "rev_1",
        to_revision_id: "rev_2",
        stats: { added: 1, removed: 0, changed: 0, unchanged: 5 },
        changes: [],
      },
    } as never);

    renderWithClient(<RevisionDiffPanel paperId="pap_1" />);

    await screen.findByText("改版差分");
    // No adopt/switch buttons should exist
    expect(screen.queryByText(/採用/)).not.toBeInTheDocument();
    expect(screen.queryByText(/切り替え/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /切り替え|採用/ })).not.toBeInTheDocument();
  });
});
