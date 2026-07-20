import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { ViewerHeader } from "@/components/viewer/ViewerHeader";
import { useViewerStore } from "@/stores/viewer-store";

const exportStandaloneAvailability = vi.fn();
vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    exportStandaloneAvailability: (...a: unknown[]) => exportStandaloneAvailability(...a),
  };
});

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const baseProps = {
  itemId: "li_1",
  title: "Flow Straight and Fast",
  qualityLevel: "A" as const,
  status: "reading" as const,
  mode: "translation" as const,
  onModeChange: vi.fn(),
  onStatusChange: vi.fn(),
  onBack: vi.fn(),
};

describe("ViewerHeader — export trigger (Task 12)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useViewerStore.setState({ style: "natural", literalStatus: "unknown" });
    exportStandaloneAvailability.mockResolvedValue({
      data: {
        source_html: true,
        translation_html: true,
        bilingual_html: true,
        article_html: false,
        pdf_original: false,
        pdf_translated: false,
        pdf_bilingual: false,
      },
    });
  });

  test("オーバーフローメニューの「エクスポート」でモーダルが開く", async () => {
    const user = userEvent.setup();
    renderWithClient(<ViewerHeader {...baseProps} />);

    await user.click(screen.getByRole("button", { name: "その他" }));
    await user.click(screen.getByRole("menuitem", { name: "エクスポート" }));

    // モーダルの見出しと成果物チェックボックスが表示される。
    expect(await screen.findByRole("heading", { name: "エクスポート" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: /原文 \(HTML\)/ })).toBeInTheDocument(),
    );
  });
});
