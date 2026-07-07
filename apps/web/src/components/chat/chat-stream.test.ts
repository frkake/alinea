import { afterEach, describe, expect, test, vi } from "vitest";
import { streamChat } from "@/components/chat/chat-stream";

/** SSE テキストを 1 本の ReadableStream(複数チャンク)として返す fetch モック。 */
function mockFetchSSE(sse: string, chunkSize = 12): void {
  const bytes = new TextEncoder().encode(sse);
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (let i = 0; i < bytes.length; i += chunkSize) {
        controller.enqueue(bytes.slice(i, i + chunkSize));
      }
      controller.close();
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("streamChat (plans/03 §10.3)", () => {
  test("parses start/delta/evidence/done in order across chunk boundaries", async () => {
    const sse = [
      'event: start',
      'data: {"message_id":"m1","thread_id":"t1","user_message_id":"u1"}',
      '',
      ': ping',
      '',
      'event: delta',
      'data: {"block_index":0,"block_type":"markdown","text":"結局 [[ev:1]] です。"}',
      '',
      'event: evidence',
      'data: {"ref":1,"display":"式(5) · §2.1","anchor":{"revision_id":"rev1","block_id":"blk-eq5","display":"式(5) · §2.1"}}',
      '',
      'event: delta',
      'data: {"block_index":1,"block_type":"aside","label":"outside_knowledge","text":"補足です。"}',
      '',
      'event: done',
      'data: {"message_id":"m1","finish_reason":"stop"}',
      '',
    ].join("\n");
    mockFetchSSE(sse);

    const events: string[] = [];
    const deltas: string[] = [];
    let evidenceDisplay = "";
    let doneId = "";

    await streamChat(
      "/api/chat/threads/t1/messages",
      { content: "?" },
      {
        onStart: (e) => {
          events.push("start");
          expect(e.message_id).toBe("m1");
          expect(e.user_message_id).toBe("u1");
        },
        onDelta: (e) => {
          events.push("delta");
          deltas.push(e.text);
          if (e.block_type === "aside") expect(e.label).toBe("outside_knowledge");
        },
        onEvidence: (e) => {
          events.push("evidence");
          evidenceDisplay = e.display;
          expect(e.anchor.block_id).toBe("blk-eq5");
        },
        onDone: (e) => {
          events.push("done");
          doneId = e.message_id;
        },
        onError: () => events.push("error"),
      },
    );

    expect(events).toEqual(["start", "delta", "evidence", "delta", "done"]);
    expect(deltas).toEqual(["結局 [[ev:1]] です。", "補足です。"]);
    expect(evidenceDisplay).toBe("式(5) · §2.1");
    expect(doneId).toBe("m1");
  });

  test("surfaces an error event as onError(Problem)", async () => {
    const sse = [
      'event: error',
      'data: {"type":"about:blank","title":"回答の生成に失敗しました","status":502,"code":"provider_error"}',
      '',
    ].join("\n");
    mockFetchSSE(sse);

    const problem = await new Promise<{ title: string } | null>((resolve) => {
      void streamChat("/x", {}, { onError: (p) => resolve(p) });
    });
    expect(problem?.title).toBe("回答の生成に失敗しました");
  });

  test("converts a pre-stream HTTP error into onError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ type: "about:blank", title: "レート制限", status: 429, code: "rate_limited" }), {
          status: 429,
        }),
      ),
    );
    const problem = await new Promise<{ status: number } | null>((resolve) => {
      void streamChat("/x", {}, { onError: (p) => resolve(p) });
    });
    expect(problem?.status).toBe(429);
  });
});
