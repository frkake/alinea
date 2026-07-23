/**
 * VocabCandidatesPanel — TDD tests
 * Five UI states: not-extracted, extracting, has-candidates, empty, failed.
 * Accept/dismiss remove item immediately; accept also invalidates vocab query.
 * waitThenInvalidate: extract invalidates only AFTER the job completes via useJobEvents.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  vocabCandidatesExtract,
  vocabCandidatesList,
  vocabCandidatesAccept,
  vocabCandidatesDismiss,
} from "@alinea/api-client";
import type { VocabCandidateOut } from "@alinea/api-client";
import { type UseJobEventsOptions } from "@/hooks/useJobEvents";
import { VocabCandidatesPanel } from "@/components/viewer/VocabCandidatesPanel";
import { useViewerStore } from "@/stores/viewer-store";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    vocabCandidatesList: vi.fn(),
    vocabCandidatesExtract: vi.fn(),
    vocabCandidatesAccept: vi.fn(),
    vocabCandidatesDismiss: vi.fn(),
  };
});

vi.mock("@/hooks/useJobEvents", () => ({ useJobEvents: vi.fn() }));

// Import AFTER mock so we get the mock version
import { useJobEvents } from "@/hooks/useJobEvents";

function candidate(overrides: Partial<VocabCandidateOut> = {}): VocabCandidateOut {
  return {
    id: "cand_1",
    term: "rectified flow",
    kind: "collocation",
    reason: "Technical term for a specific normalizing flow method",
    context_sentence: "We propose rectified flow for generative modeling.",
    highlight: { start: 11, end: 25 },
    anchor: { revision_id: "rev_1", block_id: "block_1", display: "§1" },
    source: { library_item_id: "li_test", paper_title: "Test Paper", display: "§1" },
    created_at: "2026-07-01T00:00:00Z",
    ...overrides,
  };
}

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { client, ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>) };
}

describe("VocabCandidatesPanel", () => {
  let capturedJobEvents: UseJobEventsOptions | null = null;

  beforeEach(() => {
    useViewerStore.setState({ itemId: "li_test" });
    vi.clearAllMocks();
    capturedJobEvents = null;
    vi.mocked(useJobEvents).mockImplementation((_jobId, options) => {
      capturedJobEvents = options;
    });
  });

  // State 1: not-extracted — list returns empty with no extraction done
  test("not-extracted state: shows extract button when no candidates exist", async () => {
    vi.mocked(vocabCandidatesList).mockResolvedValue({
      data: { items: [], count: 0 },
    } as never);

    renderWithClient(<VocabCandidatesPanel />);

    await screen.findByText("単語候補を抽出");
    expect(screen.getByRole("button", { name: "単語候補を抽出" })).toBeInTheDocument();
  });

  // State 2: extracting — shows "抽出中…" while job is in-flight (job_id returned, useJobEvents active)
  test("extracting state: shows 抽出中… after extract is called and job_id is returned", async () => {
    vi.mocked(vocabCandidatesList).mockResolvedValue({
      data: { items: [], count: 0 },
    } as never);
    vi.mocked(vocabCandidatesExtract).mockResolvedValue({
      data: { job_id: "job_1" },
    } as Awaited<ReturnType<typeof vocabCandidatesExtract>>);

    renderWithClient(<VocabCandidatesPanel />);

    const btn = await screen.findByRole("button", { name: "単語候補を抽出" });
    fireEvent.click(btn);

    await screen.findByText("抽出中…");
    expect(screen.queryByRole("button", { name: "単語候補を抽出" })).toBeNull();
  });

  // waitThenInvalidate: invalidation happens ONLY after job done event, not immediately after POST
  test("waitThenInvalidate: extract invalidates candidates only after useJobEvents onDone fires", async () => {
    const cand = candidate();
    let listCallCount = 0;

    vi.mocked(vocabCandidatesList).mockImplementation(async () => {
      listCallCount += 1;
      // After job done, server returns the candidates
      return {
        data: { items: listCallCount > 1 ? [cand] : [], count: listCallCount > 1 ? 1 : 0 },
      } as never;
    });
    vi.mocked(vocabCandidatesExtract).mockResolvedValue({
      data: { job_id: "job_extract" },
    } as Awaited<ReturnType<typeof vocabCandidatesExtract>>);

    const { client } = renderWithClient(<VocabCandidatesPanel />);
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");

    await screen.findByRole("button", { name: "単語候補を抽出" });

    // Click extract — POST returns 202 + job_id
    fireEvent.click(screen.getByRole("button", { name: "単語候補を抽出" }));

    // Wait for extracting state
    await screen.findByText("抽出中…");

    // No invalidation yet — still waiting for job
    expect(invalidateSpy).not.toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["vocab-candidates", "li_test"] }),
    );

    // Simulate job completion via useJobEvents.onDone
    await act(async () => {
      capturedJobEvents?.onDone?.(null);
    });

    // Now invalidation should have fired
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: ["vocab-candidates", "li_test"] }),
      ),
    );

    // Candidates appear after refetch
    await screen.findAllByText("rectified flow");
  });

  // State 3: has-candidates — shows candidate cards with term, kind, reason, context highlight
  test("has-candidates state: renders term, kind, reason, and highlighted context", async () => {
    vi.mocked(vocabCandidatesList).mockResolvedValue({
      data: {
        items: [
          candidate({
            term: "rectified flow",
            kind: "collocation",
            reason: "Technical normalizing flow method",
            context_sentence: "We propose rectified flow for generative modeling.",
            highlight: { start: 11, end: 25 },
          }),
        ],
        count: 1,
      },
    } as Awaited<ReturnType<typeof vocabCandidatesList>>);

    renderWithClient(<VocabCandidatesPanel />);

    // Term appears at least once (heading + context highlight)
    const termEls = await screen.findAllByText("rectified flow");
    expect(termEls.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("collocation")).toBeInTheDocument();
    expect(screen.getByText("Technical normalizing flow method")).toBeInTheDocument();
    // The <mark> element wraps the highlighted range
    const mark = document.querySelector("mark");
    expect(mark).not.toBeNull();
    expect(mark?.textContent).toBe("rectified flow");
  });

  // State 4: empty — explicitly marked as extracted (has been extracted but 0 results)
  test("empty state after extraction: shows empty message", async () => {
    vi.mocked(vocabCandidatesList).mockResolvedValue({
      data: { items: [], count: 0 },
    } as never);

    // Pre-seed the QueryClient with extracted=true to simulate post-extraction empty state
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(["vocab-candidates", "li_test"], { items: [], count: 0, extracted: true });

    render(
      <QueryClientProvider client={client}>
        <VocabCandidatesPanel />
      </QueryClientProvider>,
    );

    await screen.findByText("候補がありません");
  });

  // State 5: failed — list query errors
  test("failed state: shows error message and retry button", async () => {
    vi.mocked(vocabCandidatesList).mockRejectedValue(new Error("Network error"));

    renderWithClient(<VocabCandidatesPanel />);

    await screen.findByText("候補を読み込めませんでした");
    expect(screen.getByRole("button", { name: "再試行" })).toBeInTheDocument();
  });

  // Accept: removes card immediately + invalidates vocab query
  test("accept removes the candidate from the list immediately", async () => {
    const cand1 = candidate({ id: "cand_1", term: "rectified flow" });
    const cand2 = candidate({
      id: "cand_2",
      term: "optimal transport",
      context_sentence: "We study optimal transport in high dimensions.",
      highlight: { start: 9, end: 25 },
    });

    // After accept, refetch returns only cand2 (server reflects the change)
    let acceptCalled = false;
    vi.mocked(vocabCandidatesList).mockImplementation(async () => {
      return {
        data: {
          items: acceptCalled ? [cand2] : [cand1, cand2],
          count: acceptCalled ? 1 : 2,
        },
      } as never;
    });
    vi.mocked(vocabCandidatesAccept).mockImplementation(async () => {
      acceptCalled = true;
      return { data: { vocab_id: "v_1" } } as never;
    });

    renderWithClient(<VocabCandidatesPanel />);

    await screen.findAllByText("rectified flow");
    await screen.findAllByText("optimal transport");

    const acceptButtons = screen.getAllByRole("button", { name: "採用" });
    fireEvent.click(acceptButtons[0]!);

    await waitFor(() => expect(screen.queryAllByText("rectified flow")).toHaveLength(0));
    expect(screen.getAllByText("optimal transport").length).toBeGreaterThan(0);

    expect(vocabCandidatesAccept).toHaveBeenCalledWith(
      expect.objectContaining({ path: { candidate_id: "cand_1" } }),
    );
  });

  // Accept: invalidates vocab query (via queryClient.invalidateQueries)
  test("accept invalidates vocab query after success", async () => {
    const cand1 = candidate({ id: "cand_1", term: "rectified flow" });

    vi.mocked(vocabCandidatesList).mockResolvedValue({
      data: { items: [cand1], count: 1 },
    } as Awaited<ReturnType<typeof vocabCandidatesList>>);
    vi.mocked(vocabCandidatesAccept).mockResolvedValue({
      data: { vocab_id: "v_1" },
    } as Awaited<ReturnType<typeof vocabCandidatesAccept>>);

    const { client } = renderWithClient(<VocabCandidatesPanel />);
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");

    await screen.findAllByText("rectified flow");
    fireEvent.click(screen.getByRole("button", { name: "採用" }));

    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: expect.arrayContaining(["vocab"]) }),
      ),
    );
  });

  // Dismiss: removes card immediately
  test("dismiss removes the candidate from the list immediately", async () => {
    const cand1 = candidate({ id: "cand_1", term: "rectified flow" });
    const cand2 = candidate({
      id: "cand_2",
      term: "optimal transport",
      context_sentence: "We study optimal transport in high dimensions.",
      highlight: { start: 9, end: 25 },
    });

    // After dismiss, refetch returns only cand2 (server reflects the change)
    let dismissCalled = false;
    vi.mocked(vocabCandidatesList).mockImplementation(async () => {
      return {
        data: {
          items: dismissCalled ? [cand2] : [cand1, cand2],
          count: dismissCalled ? 1 : 2,
        },
      } as never;
    });
    vi.mocked(vocabCandidatesDismiss).mockImplementation(async () => {
      dismissCalled = true;
      return { data: undefined } as Awaited<ReturnType<typeof vocabCandidatesDismiss>>;
    });

    renderWithClient(<VocabCandidatesPanel />);

    await screen.findAllByText("rectified flow");

    const dismissButtons = screen.getAllByRole("button", { name: "破棄" });
    fireEvent.click(dismissButtons[0]!);

    await waitFor(() => expect(screen.queryAllByText("rectified flow")).toHaveLength(0));
    expect(screen.getAllByText("optimal transport").length).toBeGreaterThan(0);

    expect(vocabCandidatesDismiss).toHaveBeenCalledWith(
      expect.objectContaining({ path: { candidate_id: "cand_1" } }),
    );
  });
});
