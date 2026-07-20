/**
 * VocabCandidatesPanel — TDD tests
 * Five UI states: not-extracted, extracting, has-candidates, empty, failed.
 * Accept/dismiss remove item immediately; accept also invalidates vocab query.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  vocabCandidatesExtract,
  vocabCandidatesList,
  vocabCandidatesAccept,
  vocabCandidatesDismiss,
} from "@alinea/api-client";
import type { VocabCandidateOut } from "@alinea/api-client";
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

function candidate(overrides: Partial<VocabCandidateOut> = {}): VocabCandidateOut {
  return {
    id: "cand_1",
    term: "rectified flow",
    kind: "collocation",
    reason: "Technical term for a specific normalizing flow method",
    context_sentence: "We propose rectified flow for generative modeling.",
    highlight: { start: 11, end: 25 },
    anchor: { block_id: "block_1", display: "§1" },
    source: { kind: "paragraph", block_id: "block_1" },
    created_at: "2026-07-01T00:00:00Z",
    ...overrides,
  };
}

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { client, ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>) };
}

describe("VocabCandidatesPanel", () => {
  beforeEach(() => {
    useViewerStore.setState({ itemId: "li_test" });
    vi.clearAllMocks();
  });

  // State 1: not-extracted — list returns empty with no extraction done
  test("not-extracted state: shows extract button when no candidates exist", async () => {
    vi.mocked(vocabCandidatesList).mockResolvedValue({
      data: { items: [], count: 0 },
    } as Awaited<ReturnType<typeof vocabCandidatesList>>);

    renderWithClient(<VocabCandidatesPanel />);

    await screen.findByText("単語候補を抽出");
    expect(screen.getByRole("button", { name: "単語候補を抽出" })).toBeInTheDocument();
  });

  // State 2: extracting — button is disabled while mutation is in-flight
  test("extracting state: disables extract button while extracting", async () => {
    vi.mocked(vocabCandidatesList).mockResolvedValue({
      data: { items: [], count: 0 },
    } as Awaited<ReturnType<typeof vocabCandidatesList>>);

    let resolveExtract!: () => void;
    vi.mocked(vocabCandidatesExtract).mockReturnValue(
      new Promise<Awaited<ReturnType<typeof vocabCandidatesExtract>>>((res) => {
        resolveExtract = () =>
          res({
            data: { job_id: "job_1" },
          } as Awaited<ReturnType<typeof vocabCandidatesExtract>>);
      }),
    );

    renderWithClient(<VocabCandidatesPanel />);

    const btn = await screen.findByRole("button", { name: "単語候補を抽出" });
    fireEvent.click(btn);

    await screen.findByText("抽出中…");
    expect(screen.queryByRole("button", { name: "単語候補を抽出" })).toBeNull();

    resolveExtract();
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
    } as Awaited<ReturnType<typeof vocabCandidatesList>>);

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
      } as Awaited<ReturnType<typeof vocabCandidatesList>>;
    });
    vi.mocked(vocabCandidatesAccept).mockImplementation(async () => {
      acceptCalled = true;
      return { data: { vocab_id: "v_1" } } as Awaited<ReturnType<typeof vocabCandidatesAccept>>;
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
      } as Awaited<ReturnType<typeof vocabCandidatesList>>;
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
