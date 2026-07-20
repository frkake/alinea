import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { PaperExportModal } from "@/components/viewer/PaperExportModal";
import { triggerDownload } from "@/components/settings/download";
import { ToastViewport } from "@/components/ui/Toast";

vi.mock("@/components/settings/download", () => ({ triggerDownload: vi.fn() }));

const exportStandaloneAvailability = vi.fn();
const exportStandaloneStart = vi.fn();
const exportStandaloneStatus = vi.fn();
vi.mock("@alinea/api-client", () => ({
  exportStandaloneAvailability: (...a: unknown[]) => exportStandaloneAvailability(...a),
  exportStandaloneStart: (...a: unknown[]) => exportStandaloneStart(...a),
  exportStandaloneStatus: (...a: unknown[]) => exportStandaloneStatus(...a),
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

const ALL_AVAILABLE = {
  source_html: true,
  translation_html: true,
  bilingual_html: true,
  article_html: true,
  pdf_original: true,
  pdf_translated: true,
  pdf_bilingual: true,
};

function availability(overrides: Partial<typeof ALL_AVAILABLE> = {}) {
  return { data: { ...ALL_AVAILABLE, ...overrides } };
}

describe("PaperExportModal (Task 12: 論文単位エクスポート選択モーダル)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    exportStandaloneAvailability.mockResolvedValue(availability());
  });

  test("未生成 (availability=false) の成果物はチェックボックスが無効で理由を表示する", async () => {
    exportStandaloneAvailability.mockResolvedValue(
      availability({ translation_html: false, pdf_translated: false, pdf_bilingual: false }),
    );
    renderWithClient(<PaperExportModal open itemId="li_1" onClose={vi.fn()} />);

    const translation = await screen.findByRole("checkbox", { name: /訳文 \(HTML\)/ });
    expect(translation).toBeDisabled();
    const source = screen.getByRole("checkbox", { name: /原文 \(HTML\)/ });
    expect(source).toBeEnabled();
    // 未生成の理由が表示される
    expect(screen.getAllByText(/未生成/).length).toBeGreaterThan(0);
  });

  test("単一 HTML を選んでエクスポートすると同期 URL をダウンロードして閉じる", async () => {
    exportStandaloneStart.mockResolvedValue({
      data: {
        mode: "sync",
        job_id: null,
        download_url: "/api/library-items/li_1/export/standalone/source.html",
      },
    });
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderWithClient(<PaperExportModal open itemId="li_1" onClose={onClose} />);

    const source = await screen.findByRole("checkbox", { name: /原文 \(HTML\)/ });
    await user.click(source);
    await user.click(screen.getByRole("button", { name: "エクスポート" }));

    await waitFor(() =>
      expect(triggerDownload).toHaveBeenCalledWith(
        "/api/library-items/li_1/export/standalone/source.html",
      ),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    // job ステータスのポーリングはしない
    expect(exportStandaloneStatus).not.toHaveBeenCalled();
  });

  test("複数 / PDF 選択でジョブを起動し、進捗→完了ダウンロードを表示する", async () => {
    exportStandaloneStart.mockResolvedValue({
      data: { mode: "job", job_id: "job_1", download_url: null },
    });
    let poll = 0;
    exportStandaloneStatus.mockImplementation(async () => {
      poll += 1;
      if (poll >= 2) {
        return {
          data: {
            job: { id: "job_1", status: "succeeded", progress_pct: 100 },
            download_url: "https://minio.test/exports/job_1.zip",
          },
        };
      }
      return {
        data: {
          job: { id: "job_1", status: "running", progress_pct: 40 },
          download_url: null,
        },
      };
    });
    const user = userEvent.setup();
    renderWithClient(<PaperExportModal open itemId="li_1" onClose={vi.fn()} />);

    await user.click(await screen.findByRole("checkbox", { name: /原文 \(HTML\)/ }));
    await user.click(screen.getByRole("checkbox", { name: /原文 PDF/ }));
    await user.click(screen.getByRole("button", { name: "エクスポート" }));

    // 進捗表示
    await waitFor(() => expect(screen.getByText(/準備中/)).toBeInTheDocument());
    // 完了後のダウンロードリンク
    await waitFor(
      () => expect(screen.getByRole("link", { name: /ダウンロード/ })).toBeInTheDocument(),
      { timeout: 5000 },
    );
    expect(screen.getByRole("link", { name: /ダウンロード/ })).toHaveAttribute(
      "href",
      "https://minio.test/exports/job_1.zip",
    );
  });

  test("ジョブ失敗時は job.error を表示する", async () => {
    exportStandaloneStart.mockResolvedValue({
      data: { mode: "job", job_id: "job_err", download_url: null },
    });
    exportStandaloneStatus.mockResolvedValue({
      data: {
        job: {
          id: "job_err",
          status: "failed",
          progress_pct: 0,
          error: { message: "PDF 生成に失敗しました" },
        },
        download_url: null,
      },
    });
    const user = userEvent.setup();
    renderWithClient(<PaperExportModal open itemId="li_1" onClose={vi.fn()} />);

    await user.click(await screen.findByRole("checkbox", { name: /原文 \(HTML\)/ }));
    await user.click(screen.getByRole("checkbox", { name: /訳文 PDF/ }));
    await user.click(screen.getByRole("button", { name: "エクスポート" }));

    await waitFor(
      () => expect(screen.getByText(/PDF 生成に失敗しました/)).toBeInTheDocument(),
      { timeout: 5000 },
    );
  });

  test("何も選択していないとエクスポートボタンは無効", async () => {
    renderWithClient(<PaperExportModal open itemId="li_1" onClose={vi.fn()} />);
    await screen.findByRole("checkbox", { name: /原文 \(HTML\)/ });
    expect(screen.getByRole("button", { name: "エクスポート" })).toBeDisabled();
  });
});
