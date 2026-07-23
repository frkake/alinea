import { render, screen, act } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { PresentationProgress } from "@/components/viewer/presentation/PresentationProgress";
import { MockEventSource, firstEventSource } from "@/components/viewer/article/test-utils";

// useJobEvents は接続確認に jobsGet を叩く(SSE が生きていれば結果は使わない)。
vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    jobsGet: vi.fn().mockResolvedValue({
      data: { id: "job_1", kind: "presentation", status: "running", progress_pct: 0 },
    }),
  };
});

describe("PresentationProgress (Task 30 §3 SSE 進捗)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    MockEventSource.reset();
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
  });

  test("stage を日本語ラベルへ変換して進捗率とともに表示する", () => {
    render(<PresentationProgress jobId="job_1" onDone={vi.fn()} onError={vi.fn()} />);
    // まだ progress イベントが来ていない時は総称ラベル。
    expect(screen.getByText(/スライドを生成しています/)).toBeInTheDocument();

    act(() => {
      firstEventSource().dispatch("progress", {
        job_id: "job_1",
        status: "running",
        stage: "planning",
        progress_pct: 30,
      });
    });
    expect(screen.getByText(/スライド構成を考えています/)).toBeInTheDocument();
    expect(screen.getByText(/30\s*%/)).toBeInTheDocument();

    act(() => {
      firstEventSource().dispatch("progress", {
        job_id: "job_1",
        status: "running",
        stage: "authoring_slides",
        progress_pct: 62,
      });
    });
    expect(screen.getByText(/スライドを作成しています/)).toBeInTheDocument();
    expect(screen.getByText(/62\s*%/)).toBeInTheDocument();

    act(() => {
      firstEventSource().dispatch("progress", {
        job_id: "job_1",
        status: "running",
        stage: "exporting",
        progress_pct: 90,
      });
    });
    expect(screen.getByText(/PowerPoint を書き出しています/)).toBeInTheDocument();
  });

  test("done で onDone を呼ぶ", () => {
    const onDone = vi.fn();
    render(<PresentationProgress jobId="job_1" onDone={onDone} onError={vi.fn()} />);
    act(() => {
      firstEventSource().dispatch("done", { job_id: "job_1", status: "succeeded", result: {} });
    });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  test("error で onError を『失敗した stage』つきで呼ぶ", () => {
    const onError = vi.fn();
    render(<PresentationProgress jobId="job_1" onDone={vi.fn()} onError={onError} />);
    // 直前に見た stage を失敗段階として控える。
    act(() => {
      firstEventSource().dispatch("progress", {
        job_id: "job_1",
        status: "running",
        stage: "validating",
        progress_pct: 70,
      });
    });
    act(() => {
      firstEventSource().dispatch("error", {
        code: "provider_error",
        title: "モデルが利用できません",
        status: 502,
      });
    });
    expect(onError).toHaveBeenCalledTimes(1);
    const call = onError.mock.calls[0];
    if (!call) throw new Error("onError was not called");
    expect(call[0]).toMatchObject({ code: "provider_error", title: "モデルが利用できません" });
    expect(call[1]).toBe("validating");
  });
});
