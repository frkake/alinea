import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  libraryItemsDelete,
  papersIngestLog,
  papersReingest,
  settingsGet,
  type LicenseCard,
  type PaperBib,
  type RevisionInfo,
  type TimelineEntry,
} from "@alinea/api-client";
import { InfoPanel } from "@/components/viewer/InfoPanel";
import { ToastViewport } from "@/components/ui/Toast";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    papersReingest: vi.fn(),
    papersIngestLog: vi.fn(),
    settingsGet: vi.fn(),
    libraryItemsDelete: vi.fn(),
  };
});

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
}));

/** テスト用の EventSource スタブ(jsdom には未実装。§2.3 のジョブ SSE を模擬)。 */
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  closed = false;
  private listeners: Record<string, ((e: MessageEvent<string>) => void)[]> = {};

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: EventListener): void {
    (this.listeners[type] ??= []).push(cb as (e: MessageEvent<string>) => void);
  }

  removeEventListener(): void {
    // 未使用(cleanup は close() のみ)。
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, data?: unknown): void {
    const event = { data: data === undefined ? "" : JSON.stringify(data) } as MessageEvent<string>;
    for (const cb of this.listeners[type] ?? []) cb(event);
  }
}

function paper(overrides: Partial<PaperBib> = {}): PaperBib {
  return {
    id: "pap_1",
    title: "Flow Straight and Fast",
    authors: ["Xingchao Liu", "Chengyue Gong", "Qiang Liu"],
    authors_short: "Liu et al.",
    venue: "ICLR 2023",
    year: 2023,
    arxiv_id: "2209.03003",
    arxiv_version: null,
    doi: null,
    license: "cc-by",
    visibility: "public",
    abstract: "",
    ...overrides,
  };
}

function revision(overrides: Partial<RevisionInfo> = {}): RevisionInfo {
  return {
    id: "rev_1",
    quality_level: "A",
    source_version: "v3",
    parser_version: "1.0.0",
    page_count: 24,
    figure_count: 8,
    table_count: 4,
    created_at: "2026-07-02T21:09:00+09:00",
    ...overrides,
  } as RevisionInfo;
}

function license(overrides: Partial<LicenseCard> = {}): LicenseCard {
  return {
    license: "CC BY 4.0 — 図表転載可",
    figure_reuse: "allowed",
    message: "記事への図表埋め込み時、クレジットを自動付記します。",
    ...overrides,
  };
}

const TIMELINE: TimelineEntry[] = [
  { at: "2026-07-02T21:04:00+09:00", label: "arXiv から LaTeX ソース取得" },
  { at: "2026-07-02T21:05:00+09:00", label: "構造化・図表抽出(24p / 図8 / 表4)" },
  { at: "2026-07-02T21:09:00+09:00", label: "全文翻訳 完了(自然訳 · v3)· 付録は未翻訳" },
];

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      {ui}
      <ToastViewport />
    </QueryClientProvider>,
  );
}

describe("InfoPanel (M1-21)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(settingsGet).mockResolvedValue({
      data: { reading: { track_reading_time: true } },
    } as never);
    vi.mocked(papersIngestLog).mockResolvedValue({ data: { entries: [] } } as never);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    MockEventSource.instances.length = 0;
  });

  test("renders bib, quality, license and export sections", async () => {
    renderWithClient(
      <InfoPanel
        paper={paper()}
        revision={revision()}
        licenseCard={license()}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );
    expect(screen.getByText("Flow Straight and Fast")).toBeInTheDocument();
    expect(screen.getByText("Xingchao Liu, Chengyue Gong, Qiang Liu")).toBeInTheDocument();
    expect(screen.getByText("ICLR 2023")).toBeInTheDocument();
    expect(
      screen.getByText("LaTeX ソースから完全構造化。数式・相互参照・図表・脚注を保持しています。"),
    ).toBeInTheDocument();
    expect(screen.getByText(/arXiv から LaTeX ソース取得/)).toBeInTheDocument();
    expect(screen.getByText("CC BY 4.0 — 図表転載可")).toBeInTheDocument();
    expect(screen.getByText("注釈 Markdown ⤓")).toBeInTheDocument();
    expect(screen.getByText("原文 PDF ⤓").closest("a")).toHaveAttribute(
      "href",
      "/api/papers/pap_1/pdf",
    );
  });

  test("arXiv chip href includes the version when present but the label omits it", async () => {
    renderWithClient(
      <InfoPanel
        paper={paper({ arxiv_version: "v2" })}
        revision={revision()}
        licenseCard={license()}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );
    const link = screen.getByRole("link", { name: "arXiv:2209.03003" });
    expect(link).toHaveAttribute("href", "https://arxiv.org/abs/2209.03003v2");
  });

  test("footer note reflects reading.track_reading_time=false", async () => {
    vi.mocked(settingsGet).mockResolvedValue({
      data: { reading: { track_reading_time: false } },
    } as never);
    renderWithClient(
      <InfoPanel
        paper={paper()}
        revision={revision()}
        licenseCard={license()}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );
    expect(
      await screen.findByText("読書時間の記録はオフです(設定でオンにできます)"),
    ).toBeInTheDocument();
  });

  test("footer note defaults to on when tracking setting is unavailable", async () => {
    vi.mocked(settingsGet).mockResolvedValue({ data: {} } as never);
    renderWithClient(
      <InfoPanel
        paper={paper()}
        revision={revision()}
        licenseCard={license()}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );
    expect(
      await screen.findByText("読書時間を記録しています(設定でオフにできます)"),
    ).toBeInTheDocument();
  });

  test("quality level B shows the B description and inset badge", async () => {
    renderWithClient(
      <InfoPanel
        paper={paper()}
        revision={revision({ quality_level: "B" })}
        licenseCard={license()}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );
    expect(
      screen.getByText("PDF から抽出して構造化。レイアウト由来の誤りが残る可能性があります。"),
    ).toBeInTheDocument();
  });

  test("license card renders the forbidden tone", async () => {
    renderWithClient(
      <InfoPanel
        paper={paper()}
        revision={revision()}
        licenseCard={license({ figure_reuse: "forbidden", license: "All rights reserved" })}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );
    expect(screen.getByText("All rights reserved")).toBeInTheDocument();
  });

  test("opening the processing log modal fetches and shows entries with level badges", async () => {
    const user = userEvent.setup();
    vi.mocked(papersIngestLog).mockResolvedValue({
      data: {
        entries: [
          {
            at: "2026-07-02T21:04:12+09:00",
            stage: "fetching",
            level: "info",
            message: "arXiv から LaTeX ソース取得",
          },
          {
            at: "2026-07-02T21:05:30+09:00",
            stage: "fetching",
            level: "warn",
            message: "arXiv HTML にフォールバック(LaTeX 取得失敗: 404)",
          },
          {
            at: "2026-07-02T21:06:00+09:00",
            stage: "structuring",
            level: "error",
            message: "図表抽出に失敗",
          },
        ],
      },
    } as never);
    renderWithClient(
      <InfoPanel
        paper={paper()}
        revision={revision()}
        licenseCard={license()}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );

    await user.click(screen.getByText("処理ログ"));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("処理ログ")).toBeInTheDocument();
    await waitFor(() =>
      expect(papersIngestLog).toHaveBeenCalledWith({
        path: { paper_id: "pap_1" },
        throwOnError: true,
      }),
    );
    expect(
      await screen.findByText("arXiv HTML にフォールバック(LaTeX 取得失敗: 404)"),
    ).toBeInTheDocument();
    expect(screen.getByText("図表抽出に失敗")).toBeInTheDocument();
    expect(screen.getByText("warn")).toBeInTheDocument();
    expect(screen.getByText("error")).toBeInTheDocument();
  });

  test("processing log modal shows an empty state when there are no entries", async () => {
    const user = userEvent.setup();
    renderWithClient(
      <InfoPanel
        paper={paper()}
        revision={revision()}
        licenseCard={license()}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );
    await user.click(screen.getByText("処理ログ"));
    expect(await screen.findByText("ログはまだありません")).toBeInTheDocument();
  });

  test("reingest: confirm modal shows verbatim copy and cancel closes without calling the API", async () => {
    const user = userEvent.setup();
    renderWithClient(
      <InfoPanel
        paper={paper()}
        revision={revision()}
        licenseCard={license()}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );
    await user.click(screen.getByText("再取り込み"));
    expect(screen.getByText("再取り込みしますか?")).toBeInTheDocument();
    expect(
      screen.getByText(
        "最新のソースから構造化と翻訳をやり直します。注釈は新しいリビジョンへ自動で引き継がれます(位置を失った注釈は「未配置」として残ります)。",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByText("キャンセル"));
    expect(papersReingest).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByText("再取り込みしますか?")).not.toBeInTheDocument());
  });

  test("reingest: confirming posts, shows the SSE progress row, then completes with a success toast", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    const user = userEvent.setup();
    vi.mocked(papersReingest).mockResolvedValue({ data: { job_id: "job_9" } } as never);

    renderWithClient(
      <InfoPanel
        paper={paper()}
        revision={revision()}
        licenseCard={license()}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );

    await user.click(screen.getByText("再取り込み"));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "再取り込み" }),
    );

    await waitFor(() =>
      expect(papersReingest).toHaveBeenCalledWith({
        path: { paper_id: "pap_1" },
        throwOnError: true,
      }),
    );
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    expect(MockEventSource.instances[0]?.url).toBe("/api/jobs/job_9/events");

    act(() => {
      MockEventSource.instances[0]?.emit("progress", {
        job_id: "job_9",
        status: "running",
        stage: "structuring",
        progress_pct: 42,
      });
    });
    expect(await screen.findByText(/構造化中 — 42%/)).toBeInTheDocument();

    act(() => {
      MockEventSource.instances[0]?.emit("done", {
        job_id: "job_9",
        status: "succeeded",
        result: {},
      });
    });

    expect(await screen.findByText("再取り込みが完了しました")).toBeInTheDocument();
    expect(screen.queryByText(/構造化中 — 42%/)).not.toBeInTheDocument();
    expect(MockEventSource.instances[0]?.closed).toBe(true);
  });

  test("reingest: waiting_input progress is shown as セクション選択待ち", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    const user = userEvent.setup();
    vi.mocked(papersReingest).mockResolvedValue({ data: { job_id: "job_9" } } as never);

    renderWithClient(
      <InfoPanel
        paper={paper()}
        revision={revision({ page_count: 42 })}
        licenseCard={license()}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );
    await user.click(screen.getByText("再取り込み"));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "再取り込み" }),
    );
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    act(() => {
      MockEventSource.instances[0]?.emit("progress", {
        job_id: "job_9",
        status: "waiting_input",
        stage: "selecting_sections",
        progress_pct: 45,
      });
    });

    expect(await screen.findByText(/セクション選択待ち — 45%/)).toBeInTheDocument();
  });

  test("reingest: SSE error event shows the problem title as a toast and clears the progress row", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    const user = userEvent.setup();
    vi.mocked(papersReingest).mockResolvedValue({ data: { job_id: "job_9" } } as never);

    renderWithClient(
      <InfoPanel
        paper={paper()}
        revision={revision()}
        licenseCard={license()}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );
    await user.click(screen.getByText("再取り込み"));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "再取り込み" }),
    );
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    act(() => {
      MockEventSource.instances[0]?.emit("error", {
        type: "about:blank",
        title: "ジョブに失敗しました",
        status: 502,
        code: "provider_error",
      });
    });

    expect(await screen.findByText("ジョブに失敗しました")).toBeInTheDocument();
  });

  test("reingest: 409 conflict from the API shows the already-running toast", async () => {
    const user = userEvent.setup();
    vi.mocked(papersReingest).mockRejectedValue({
      type: "about:blank",
      title: "状態が競合しています",
      status: 409,
      code: "conflict",
    });

    renderWithClient(
      <InfoPanel
        paper={paper()}
        revision={revision()}
        licenseCard={license()}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );
    await user.click(screen.getByText("再取り込み"));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "再取り込み" }),
    );

    expect(await screen.findByText("再取り込みは既に実行中です")).toBeInTheDocument();
  });

  // 取り込みキャンセル(docs/08 §2.2)。再取り込みも同じジョブ機構を使うため同じ経路で中止できる。
  test("cancel-ingest: while a reingest job is running, 取り込みを中止 deletes the item and navigates to /library", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    const user = userEvent.setup();
    vi.mocked(papersReingest).mockResolvedValue({ data: { job_id: "job_9" } } as never);
    vi.mocked(libraryItemsDelete).mockResolvedValue({} as never);

    renderWithClient(
      <InfoPanel
        paper={paper()}
        revision={revision()}
        licenseCard={license()}
        ingestTimeline={TIMELINE}
        itemId="li_1"
      />,
    );

    await user.click(screen.getByText("再取り込み"));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "再取り込み" }),
    );
    await waitFor(() => expect(papersReingest).toHaveBeenCalledTimes(1));

    expect(screen.queryByText("再取り込み")).not.toBeInTheDocument();
    await user.click(screen.getByText("取り込みを中止"));
    expect(screen.getByText("取り込みをキャンセルしますか?")).toBeInTheDocument();
    expect(libraryItemsDelete).not.toHaveBeenCalled();

    await user.click(screen.getByText("取り込みをキャンセル"));

    await waitFor(() =>
      expect(libraryItemsDelete).toHaveBeenCalledWith({
        path: { item_id: "li_1" },
        throwOnError: true,
      }),
    );
    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/library"));
    expect(await screen.findByText("取り込みをキャンセルしました")).toBeInTheDocument();
    expect(MockEventSource.instances[0]?.closed).toBe(true);
  });
});
