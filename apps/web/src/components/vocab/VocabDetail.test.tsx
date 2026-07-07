import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { VocabEntryDetail } from "@yakudoku/api-client";
import { VocabDetail } from "@/components/vocab/VocabDetail";

const vocabGet = vi.fn();
const vocabUpdate = vi.fn();
const vocabRegenerate = vi.fn();
const vocabReview = vi.fn();

vi.mock("@yakudoku/api-client", () => ({
  vocabGet: (...args: unknown[]) => vocabGet(...args),
  vocabUpdate: (...args: unknown[]) => vocabUpdate(...args),
  vocabRegenerate: (...args: unknown[]) => vocabRegenerate(...args),
  vocabReview: (...args: unknown[]) => vocabReview(...args),
}));

function baseEntry(overrides: Partial<VocabEntryDetail> = {}): VocabEntryDetail {
  return {
    id: "v_1",
    kind: "idiom",
    term: "boil down to",
    meaning_short: "要するに〜に帰着する",
    source: { library_item_id: "li_1", paper_title: "Rectified Flow", display: "Rectified Flow · §2.1" },
    added_at: "2026-07-06T00:00:00",
    generation: "done",
    pos_label: "句動詞",
    ipa: "/ˌbɔɪl ˈdaʊn tə/",
    anchor: { revision_id: "rev_1", block_id: "blk_1", display: "§2.1" },
    context_sentence:
      "With this choice, the training objective boils down to a simple least squares regression problem.",
    highlight: { start: 32, end: 43 },
    ai: {
      context_meaning: { short: "要するに〜に帰着する", long: "(複雑なものが)煮詰まって**結局〜に帰着する**。" },
      interpretation: "(句動詞の読み方)\nboil(煮る)+ down(量が減る方向)+ to(到達点)。",
      etymology: "boil ← ラテン語 *bullīre*(泡立つ)。",
      mnemonic: "カレーを煮詰めるイメージ。",
      related_expressions: "come down to(ほぼ同義)",
      edited_fields: [],
      generation_error: null,
    },
    srs: { stage: 1, next_review_at: "2026-07-07T00:00:00", review_count: 0, history: [] },
    ...overrides,
  };
}

function renderDetail(vocabId = "v_1") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <VocabDetail
        vocabId={vocabId}
        onOpenSource={vi.fn()}
        onDeleteRequested={vi.fn()}
        onNotFound={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

describe("VocabDetail", () => {
  beforeEach(() => {
    vocabGet.mockReset();
    vocabUpdate.mockReset().mockResolvedValue({ data: baseEntry({ ai: { ...baseEntry().ai, etymology: "更新済み" } }) });
    vocabRegenerate.mockReset().mockResolvedValue({ data: { job_id: "job_1" } });
    vocabReview.mockReset();
  });

  test("renders the 6 sections, meta line, and AI badge for a done entry", async () => {
    vocabGet.mockResolvedValue({ data: baseEntry() });
    renderDetail();
    expect(await screen.findByText("boil down to")).toBeInTheDocument();
    expect(screen.getByText("/ˌbɔɪl ˈdaʊn tə/")).toBeInTheDocument();
    // RTL の既定ノーマライザは前後の空白をトリムするため、末尾の中黒(4d §4.2.6 の逐語仕様どおり)
    // は正規表現で緩めに一致させる。
    expect(screen.getByText(/^句動詞 · Rectified Flow §2\.1 で追加 ·/)).toBeInTheDocument();
    expect(screen.getByText("AI生成 · 編集可")).toBeInTheDocument();
    expect(screen.getByText("文脈での語義")).toBeInTheDocument();
    expect(screen.getByText("解釈のしかた(句動詞の読み方)")).toBeInTheDocument();
    expect(screen.getByText("語源メモ")).toBeInTheDocument();
    expect(screen.getByText("✦ 覚えるコツ")).toBeInTheDocument();
    expect(screen.getByText("よく出る形・近い表現")).toBeInTheDocument();
    expect(screen.getByText("文脈センテンス")).toBeInTheDocument();
  });

  test("pending generation shows 生成中 placeholders and hides the AI badge", async () => {
    vocabGet.mockResolvedValue({ data: baseEntry({ generation: "pending", ai: { edited_fields: [], generation_error: null } }) });
    renderDetail();
    expect(await screen.findByText("boil down to")).toBeInTheDocument();
    expect(screen.queryByText("AI生成 · 編集可")).toBeNull();
    expect(screen.getAllByText("生成中…").length).toBeGreaterThan(0);
  });

  test("failed generation shows the failure card with a retry action and keeps the context sentence", async () => {
    vocabGet.mockResolvedValue({
      data: baseEntry({
        generation: "failed",
        ai: { edited_fields: [], generation_error: "LLM タイムアウト" },
      }),
    });
    renderDetail();
    expect(await screen.findByText(/学習コンテンツの生成に失敗しました/)).toBeInTheDocument();
    expect(screen.getByText(/LLM タイムアウト/)).toBeInTheDocument();
    expect(screen.getByText("文脈センテンス")).toBeInTheDocument();
    fireEvent.click(screen.getByText("生成を再試行"));
    await waitFor(() => {
      expect(vocabRegenerate).toHaveBeenCalledWith(
        expect.objectContaining({ path: { vocab_id: "v_1" }, body: { fields: undefined } }),
      );
    });
  });

  test("editing a section calls PATCH with the field under ai.*", async () => {
    vocabGet.mockResolvedValue({ data: baseEntry() });
    renderDetail();
    await screen.findByText("語源メモ");

    const heading = screen.getByText("語源メモ").parentElement;
    if (!heading) throw new Error("heading wrapper missing");
    fireEvent.mouseEnter(heading.parentElement ?? heading);
    fireEvent.click(screen.getByText("編集"));
    const textarea = screen.getByLabelText("語源メモを編集");
    fireEvent.change(textarea, { target: { value: "更新した語源メモ" } });
    fireEvent.click(screen.getByText("保存"));

    await waitFor(() => {
      expect(vocabUpdate).toHaveBeenCalledWith(
        expect.objectContaining({
          path: { vocab_id: "v_1" },
          body: { ai: { etymology: "更新した語源メモ" } },
        }),
      );
    });
  });

  test("clicking ✓ 覚えた calls the review mutation", async () => {
    vocabGet.mockResolvedValue({ data: baseEntry() });
    vocabReview.mockResolvedValue({
      data: { srs: { stage: 2, next_review_at: "2026-07-11T00:00:00", review_count: 1, history: [] }, next_review_display: "次の復習: 3日後(2 回目)" },
    });
    renderDetail();
    await screen.findByText("boil down to");
    fireEvent.click(screen.getByText("✓ 覚えた"));
    await waitFor(() => {
      expect(vocabReview).toHaveBeenCalledWith(
        expect.objectContaining({ path: { vocab_id: "v_1" }, body: { result: "good" } }),
      );
    });
    expect(await screen.findByText("次の復習: 3日後(2 回目)")).toBeInTheDocument();
  });

  test("calls onNotFound when the entry no longer exists (404)", async () => {
    vocabGet.mockResolvedValue({
      error: { code: "not_found", detail: "見つかりません" },
      response: { status: 404 },
    });
    const onNotFound = vi.fn();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <VocabDetail vocabId="missing" onOpenSource={vi.fn()} onDeleteRequested={vi.fn()} onNotFound={onNotFound} />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(onNotFound).toHaveBeenCalledWith("missing");
    });
  });

  test("shows the empty state when no vocab is selected", () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <VocabDetail vocabId={null} onOpenSource={vi.fn()} onDeleteRequested={vi.fn()} onNotFound={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(screen.getByText("語彙が選択されていません")).toBeInTheDocument();
  });
});
