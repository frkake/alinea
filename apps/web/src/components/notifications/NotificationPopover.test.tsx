import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  notificationsAction,
  notificationsList,
  type LibraryItemSummary,
  type NotificationOut,
} from "@alinea/api-client";
import { NotificationPopover } from "@/components/notifications/NotificationPopover";
import { useFinishReadingStore } from "@/components/library/finishReadingStore";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    notificationsList: vi.fn(),
    notificationsAction: vi.fn(),
    notificationsReadAll: vi.fn(async () => ({ data: undefined })),
    notificationsUpdate: vi.fn(async () => ({ data: undefined })),
  };
});

function suggestion(overrides: Partial<NotificationOut> = {}): NotificationOut {
  return {
    id: "ntf_1",
    kind: "status_suggestion",
    read: false,
    created_at: "2026-07-08T10:00:00Z",
    payload: { paper_title: "Flow Straight and Fast", reason: "reached_end", suggested_status: "done" },
    ...overrides,
  };
}

function libraryItem(overrides: Partial<LibraryItemSummary> = {}): LibraryItemSummary {
  return {
    id: "li_1",
    paper: {
      id: "pap_1",
      title: "Flow Straight and Fast",
      authors: ["Xingchang Liu"],
      authors_short: "Liu",
      venue: "ICLR 2023",
      year: 2023,
      arxiv_id: "2209.03003",
      license: "cc-by",
      visibility: "public",
      abstract: "",
    },
    status: "done",
    priority: null,
    deadline: null,
    tags: [],
    suggested_tags: [],
    quality_level: "A",
    source: "arxiv",
    progress_pct: 100,
    comprehension: null,
    reading_seconds_total: 120,
    added_at: "2026-07-02T00:00:00Z",
    updated_at: "2026-07-08T00:00:00Z",
    finished_at: "2026-07-08T00:00:00Z",
    ...overrides,
  };
}

function renderPopover() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NotificationPopover onClose={() => {}} />
    </QueryClientProvider>,
  );
}

// M1-06: 通知ポップオーバー「変更する」→ done 遷移時の読了フロー起動配線(1g §2.3)。
describe("NotificationPopover finish-reading wiring (M1-06)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useFinishReadingStore.setState({ item: null });
  });

  test('clicking 変更する that resolves to status "done" opens the finish-reading dialog', async () => {
    const user = userEvent.setup();
    vi.mocked(notificationsList).mockResolvedValue({
      data: { items: [suggestion()], unread: 1 },
    } as never);
    const done = libraryItem();
    vi.mocked(notificationsAction).mockResolvedValue({
      data: { notification: suggestion({ payload: { ...suggestion().payload, resolved: "applied" } }), library_item: done },
    } as never);

    renderPopover();
    await screen.findByText("変更する");
    await user.click(screen.getByText("変更する"));

    await waitFor(() => expect(useFinishReadingStore.getState().item).toEqual(done));
  });

  test("does not open the dialog when the resulting status is not done", async () => {
    const user = userEvent.setup();
    vi.mocked(notificationsList).mockResolvedValue({
      data: {
        items: [
          suggestion({
            payload: { paper_title: "Flow", reason: "read_3min", suggested_status: "reading" },
          }),
        ],
        unread: 1,
      },
    } as never);
    const reading = libraryItem({ status: "reading", finished_at: null });
    vi.mocked(notificationsAction).mockResolvedValue({
      data: { notification: suggestion(), library_item: reading },
    } as never);

    renderPopover();
    await screen.findByText("変更する");
    await user.click(screen.getByText("変更する"));

    await waitFor(() => expect(notificationsAction).toHaveBeenCalledTimes(1));
    expect(useFinishReadingStore.getState().item).toBeNull();
  });
});
