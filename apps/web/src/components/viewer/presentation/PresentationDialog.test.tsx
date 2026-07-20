import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  presentationsGenerate,
  presentationsGet,
  type PresentationArtifactOut,
  type PresentationStatusResponse,
} from "@alinea/api-client";
import { PresentationDialog } from "@/components/viewer/presentation/PresentationDialog";
import { ToastViewport } from "@/components/ui/Toast";
import { MockEventSource, firstEventSource } from "@/components/viewer/article/test-utils";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    presentationsGet: vi.fn(),
    presentationsGenerate: vi.fn(),
    jobsGet: vi.fn().mockResolvedValue({
      data: { id: "job_1", kind: "presentation", status: "running", progress_pct: 0 },
    }),
  };
});

const triggerDownload = vi.fn();
vi.mock("@/components/settings/download", () => ({
  triggerDownload: (...args: unknown[]) => triggerDownload(...args),
}));

function artifact(overrides: Partial<PresentationArtifactOut> = {}): PresentationArtifactOut {
  return {
    id: "pres_1",
    library_item_id: "li_1",
    source_revision_id: "rev_1",
    generation_job_id: "job_old",
    preset: "research_talk",
    audience: "researcher",
    instruction: "",
    model_provider: "openai",
    model_id: "gpt-5.5",
    ppt_master_revision: "0c0bdaf",
    generated_at: "2026-07-16T09:30:00Z",
    updated_at: "2026-07-16T09:30:00Z",
    ...overrides,
  };
}

function status(overrides: Partial<PresentationStatusResponse> = {}): PresentationStatusResponse {
  return { artifact: null, job: null, ...overrides };
}

function renderDialog() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PresentationDialog open itemId="li_1" onClose={vi.fn()} />
      <ToastViewport />
    </QueryClientProvider>,
  );
}

function wrap(ui: ReactNode) {
  return ui;
}
void wrap;

describe("PresentationDialog (Task 30 §4/§5)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    MockEventSource.reset();
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("未生成時は 3 用途プリセットと聴衆・任意指示を表示する", async () => {
    vi.mocked(presentationsGet).mockResolvedValue({ data: status() } as never);
    renderDialog();
    await screen.findByRole("radio", { name: "輪読会" });
    expect(screen.getByRole("radio", { name: "研究発表" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "実装解説" })).toBeInTheDocument();
    // 聴衆 3 種。
    expect(screen.getByRole("radio", { name: "初学者" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "研究者" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "実装者" })).toBeInTheDocument();
    // 任意指示欄と文字数(0/500)。
    expect(screen.getByRole("textbox", { name: /任意指示/ })).toBeInTheDocument();
    expect(screen.getByText(/0\s*\/\s*500/)).toBeInTheDocument();
  });

  test("用途を切り替えると聴衆の既定値が用途別に追従する", async () => {
    vi.mocked(presentationsGet).mockResolvedValue({ data: status() } as never);
    const user = userEvent.setup();
    renderDialog();
    // 既定は輪読会 → 初学者。
    await screen.findByRole("radio", { name: "輪読会" });
    expect(screen.getByRole("radio", { name: "初学者" })).toHaveAttribute("aria-checked", "true");

    await user.click(screen.getByRole("radio", { name: "研究発表" }));
    expect(screen.getByRole("radio", { name: "研究者" })).toHaveAttribute("aria-checked", "true");

    await user.click(screen.getByRole("radio", { name: "実装解説" }));
    expect(screen.getByRole("radio", { name: "実装者" })).toHaveAttribute("aria-checked", "true");
  });

  test("任意指示は 500 文字を超えて入力できず、文字数を表示する", async () => {
    vi.mocked(presentationsGet).mockResolvedValue({ data: status() } as never);
    const user = userEvent.setup();
    renderDialog();
    const box = (await screen.findByRole("textbox", { name: /任意指示/ })) as HTMLTextAreaElement;
    expect(box).toHaveAttribute("maxLength", "500");
    await user.type(box, "背景を厚めに");
    expect(screen.getByText(/6\s*\/\s*500/)).toBeInTheDocument();
  });

  test("生成開始で選択中の用途・聴衆・指示を送信し、進捗表示へ切り替わる", async () => {
    vi.mocked(presentationsGet).mockResolvedValue({ data: status() } as never);
    vi.mocked(presentationsGenerate).mockResolvedValue({ data: { job_id: "job_1" } } as never);
    const user = userEvent.setup();
    renderDialog();
    await screen.findByRole("radio", { name: "研究発表" });
    await user.click(screen.getByRole("radio", { name: "研究発表" }));
    await user.click(screen.getByRole("button", { name: /生成する/ }));

    expect(presentationsGenerate).toHaveBeenCalledWith(
      expect.objectContaining({
        path: { item_id: "li_1" },
        body: { preset: "research_talk", audience: "researcher" },
      }),
    );
    // 進捗表示へ切り替わる(生成ボタンは消える)。
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /生成する/ })).toBeNull(),
    );
    expect(screen.getByText(/スライドを生成しています/)).toBeInTheDocument();
  });

  test("生成中は二重送信を防ぐ(進捗中に生成ボタンを出さない)", async () => {
    // GET が進行中 job を返す = リロード後も active job を追跡している状態。
    vi.mocked(presentationsGet).mockResolvedValue({
      data: status({
        job: {
          id: "job_1",
          kind: "presentation",
          status: "running",
          progress_pct: 20,
          created_at: "2026-07-16T09:00:00Z",
          updated_at: "2026-07-16T09:00:00Z",
        },
      }),
    } as never);
    renderDialog();
    await waitFor(() => expect(screen.getByText(/スライドを生成しています/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /生成する/ })).toBeNull();
    expect(presentationsGenerate).not.toHaveBeenCalled();
  });

  test("成功時は生成日時・用途・model・ppt-master revision とダウンロード/再生成を表示する", async () => {
    vi.mocked(presentationsGet).mockResolvedValue({
      data: status({ artifact: artifact() }),
    } as never);
    const user = userEvent.setup();
    renderDialog();
    await screen.findByRole("button", { name: /ダウンロード/ });
    expect(screen.getByText(/研究発表/)).toBeInTheDocument();
    expect(screen.getByText(/gpt-5\.5/)).toBeInTheDocument();
    expect(screen.getByText(/0c0bdaf/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /再生成/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /ダウンロード/ }));
    expect(triggerDownload).toHaveBeenCalledWith("/api/library-items/li_1/presentation/download");
  });

  test("既存成果物がある状態で再生成が失敗しても『ダウンロード』が残る", async () => {
    // 初回: 成果物あり・job なし。
    vi.mocked(presentationsGet).mockResolvedValue({
      data: status({ artifact: artifact() }),
    } as never);
    vi.mocked(presentationsGenerate).mockResolvedValue({ data: { job_id: "job_2" } } as never);
    const user = userEvent.setup();
    renderDialog();

    await user.click(await screen.findByRole("button", { name: /再生成/ }));
    // 再生成の開始ダイアログで「生成する」。
    await user.click(await screen.findByRole("button", { name: /生成する/ }));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    // job がサーバーエラーで失敗。
    act(() => {
      firstEventSource().dispatch("progress", {
        job_id: "job_2",
        status: "running",
        stage: "authoring_slides",
        progress_pct: 40,
      });
    });
    act(() => {
      firstEventSource().dispatch("error", {
        code: "provider_error",
        title: "生成に失敗しました",
        detail: "モデルが一時的に利用できません",
        status: 502,
      });
    });

    // 失敗表示(stage + Problem Details + 再試行)と、旧成果物のダウンロードが同時に残る。
    expect(await screen.findByText(/生成に失敗しました/)).toBeInTheDocument();
    expect(screen.getByText(/モデルが一時的に利用できません/)).toBeInTheDocument();
    expect(screen.getByText(/スライドを作成しています/)).toBeInTheDocument(); // 失敗 stage
    expect(screen.getByRole("button", { name: /再試行/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /ダウンロード/ })).toBeInTheDocument();
  });
});
