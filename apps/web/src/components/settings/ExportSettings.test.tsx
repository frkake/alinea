import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { beforeEach, afterEach, describe, expect, test, vi } from "vitest";
import { ExportSettings } from "@/components/settings/ExportSettings";
import { triggerDownload } from "@/components/settings/download";
import { ToastViewport } from "@/components/ui/Toast";

vi.mock("@/components/settings/download", () => ({ triggerDownload: vi.fn() }));

const libraryItemsList = vi.fn();
vi.mock("@alinea/api-client", () => ({
  libraryItemsList: (...args: unknown[]) => libraryItemsList(...args),
}));

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      {ui}
      <ToastViewport />
    </QueryClientProvider>,
  );
}

function item(overrides: Partial<{ id: string; title: string; authors_short: string; year: number | null }> = {}) {
  const merged = {
    id: "item-1",
    title: "Rectified Flow",
    authors_short: "Liu et al.",
    year: 2023,
    ...overrides,
  };
  return {
    id: merged.id,
    status: "unread",
    tags: [],
    suggested_tags: [],
    quality_level: "A",
    source: "arxiv",
    progress_pct: 0,
    reading_seconds_total: 0,
    paper: {
      id: merged.id,
      title: merged.title,
      authors: [merged.authors_short],
      authors_short: merged.authors_short,
      year: merged.year,
    },
  };
}

describe("ExportSettings (Task 6: データカテゴリ + 完全バックアップ + インポート)", () => {
  test("データカテゴリに完全バックアップとインポートのカードが出る", () => {
    renderWithClient(<ExportSettings />);
    expect(screen.getByText("完全バックアップ")).toBeInTheDocument();
    expect(screen.getByText("インポート(復元)")).toBeInTheDocument();
    // BYOK は移行されない注記
    expect(screen.getByText(/BYOK/)).toBeInTheDocument();
  });

  test("readOnly では実行系(zip選択ボタン)が非描画", () => {
    renderWithClient(<ExportSettings readOnly />);
    expect(screen.queryByText("zip を選択して復元")).not.toBeInTheDocument();
  });
});

describe("ExportSettings (Task 4: JSON一括削除 / 完全バックアップ単一化 / 失敗理由表示)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    libraryItemsList.mockResolvedValue({ data: { items: [] } });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("「JSON 一括」カードは表示されない", () => {
    renderWithClient(<ExportSettings />);
    expect(screen.queryByText("JSON 一括")).not.toBeInTheDocument();
  });

  test("「完全バックアップ」はページ内に1つだけ表示される", () => {
    renderWithClient(<ExportSettings />);
    expect(screen.getAllByText("完全バックアップ")).toHaveLength(1);
  });

  test("完全バックアップ失敗時: 実際の job.error がトーストに表示される", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/export/full") {
          return { ok: true, json: async () => ({ job_id: "job_backup_err" }) };
        }
        return {
          ok: true,
          json: async () => ({
            job: { status: "failed", error: "S3 storage quota exceeded" },
            download_url: null,
          }),
        };
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<ExportSettings />);

    await user.click(screen.getByRole("button", { name: "完全バックアップ をエクスポート" }));
    await screen.findByText("S3 storage quota exceeded");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "完全バックアップ をエクスポート" })).toBeInTheDocument(),
    );
  });

  test("完全バックアップ失敗時 error が null: フォールバック文言が表示される", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/export/full") {
          return { ok: true, json: async () => ({ job_id: "job_backup_fallback" }) };
        }
        return {
          ok: true,
          json: async () => ({
            job: { status: "failed", error: null },
            download_url: null,
          }),
        };
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<ExportSettings />);

    await user.click(screen.getByRole("button", { name: "完全バックアップ をエクスポート" }));
    await screen.findByText("処理に失敗しました");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "完全バックアップ をエクスポート" })).toBeInTheDocument(),
    );
  });

  test("インポート失敗時: 実際の job.error がトーストに表示される", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/import/full") {
          return { ok: true, json: async () => ({ job_id: "job_import_err" }) };
        }
        return {
          ok: true,
          json: async () => ({
            job: { status: "failed", error: "Invalid zip format" },
            summary: null,
          }),
        };
      }),
    );

    renderWithClient(<ExportSettings />);

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["data"], "backup.zip", { type: "application/zip" });
    Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
    fireEvent.change(fileInput);

    await screen.findByText("Invalid zip format");
  });

  test("インポート失敗時 error が null: フォールバック文言が表示される", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/import/full") {
          return { ok: true, json: async () => ({ job_id: "job_import_fallback" }) };
        }
        return {
          ok: true,
          json: async () => ({
            job: { status: "failed", error: null },
            summary: null,
          }),
        };
      }),
    );

    renderWithClient(<ExportSettings />);

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["data"], "backup.zip", { type: "application/zip" });
    Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
    fireEvent.change(fileInput);

    await screen.findByText("処理に失敗しました");
  });
});

describe("ExportSettings (4f §4.6, M2-15: Markdown + BibTeX/CSV)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    libraryItemsList.mockResolvedValue({ data: { items: [item()] } });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("renders 2 format cards (Markdown / BibTeX・CSV) - JSON 一括なし", () => {
    renderWithClient(<ExportSettings />);
    expect(screen.getByText("論文単位 Markdown")).toBeInTheDocument();
    expect(screen.getByText("BibTeX / CSV")).toBeInTheDocument();
    expect(screen.queryByText("JSON 一括")).not.toBeInTheDocument();
  });

  test("Markdown export opens the paper picker, searches, and downloads on row click", async () => {
    const user = userEvent.setup();
    renderWithClient(<ExportSettings />);

    await user.click(screen.getByRole("button", { name: "論文単位 Markdown をエクスポート" }));
    expect(await screen.findByText("エクスポートする論文を選択")).toBeInTheDocument();
    await screen.findByText("Rectified Flow");

    const input = screen.getByPlaceholderText("タイトル・著者で検索");
    fireEvent.change(input, { target: { value: "rectified" } });
    await waitFor(
      () =>
        expect(libraryItemsList).toHaveBeenCalledWith(
          expect.objectContaining({ query: { q: "rectified", limit: 20 } }),
        ),
      { timeout: 1000 },
    );

    await user.click(screen.getByText("Rectified Flow"));
    expect(triggerDownload).toHaveBeenCalledWith("/api/library-items/item-1/export/markdown");
    await waitFor(() =>
      expect(screen.queryByText("エクスポートする論文を選択")).not.toBeInTheDocument(),
    );
  });

  test("empty search result shows the 該当する論文がありません message", async () => {
    libraryItemsList.mockResolvedValue({ data: { items: [] } });
    const user = userEvent.setup();
    renderWithClient(<ExportSettings />);
    await user.click(screen.getByRole("button", { name: "論文単位 Markdown をエクスポート" }));
    expect(await screen.findByText("該当する論文がありません")).toBeInTheDocument();
  });

  test("BibTeX / CSV opens a popover with both format options", async () => {
    const user = userEvent.setup();
    renderWithClient(<ExportSettings />);

    await user.click(screen.getByRole("button", { name: "BibTeX / CSV をエクスポート" }));
    await user.click(screen.getByRole("menuitem", { name: "BibTeX (.bib)" }));
    expect(triggerDownload).toHaveBeenCalledWith("/api/export/bibtex");

    await user.click(screen.getByRole("button", { name: "BibTeX / CSV をエクスポート" }));
    await user.click(screen.getByRole("menuitem", { name: "CSV (.csv)" }));
    expect(triggerDownload).toHaveBeenCalledWith("/api/export/csv");
  });

  test("完全バックアップ: 202 → 準備中… → ポーリング → download_url でダウンロード+表示復帰", async () => {
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/export/full") {
          return { ok: true, json: async () => ({ job_id: "job_1" }) };
        }
        call += 1;
        const download_url = call >= 2 ? "https://minio.test/exports/job_1.zip" : null;
        return {
          ok: true,
          json: async () => ({ job: { status: "running" }, download_url }),
        };
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<ExportSettings />);

    await user.click(screen.getByRole("button", { name: "完全バックアップ をエクスポート" }));
    expect(await screen.findByText("準備中…")).toBeInTheDocument();

    await act(async () => {
      await vi.waitFor(() => expect(call).toBeGreaterThanOrEqual(1));
    });

    await waitFor(
      () =>
        expect(triggerDownload).toHaveBeenCalledWith("https://minio.test/exports/job_1.zip"),
      { timeout: 5000 },
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "完全バックアップ をエクスポート" })).toBeInTheDocument(),
    );
  });
});
