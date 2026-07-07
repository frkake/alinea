import type { AnchorRef, Problem } from "@yakudoku/api-client";

/**
 * チャット送信 SSE(plans/03 §10.3)を fetch + ReadableStream で受信するクライアント。
 *
 * ネイティブ EventSource は GET しか張れないため(POST ボディを送れない)、
 * チャットの SSE は fetch(credentials:"include") のストリームを自前でパースする。
 * イベントは `event:` 行 + `data:`(1 行 JSON)で、空行区切り。`: ping` コメント行は無視。
 * 再接続再開はしない(plans/03 §1.9)。切断/失敗の回復は呼び出し側が messages 再取得で行う。
 */

export interface ChatStartEvent {
  message_id: string;
  thread_id: string;
  user_message_id: string;
}

export interface ChatDeltaEvent {
  block_index: number;
  block_type: "markdown" | "aside";
  text: string;
  /** aside ブロック初回 delta にのみ含まれる。 */
  label?: "outside_knowledge" | "speculation";
}

export interface ChatEvidenceEvent {
  ref: number;
  display: string;
  anchor: AnchorRef;
}

export interface ChatDoneEvent {
  message_id: string;
  finish_reason: string;
}

export interface ChatStreamHandlers {
  onStart?(event: ChatStartEvent): void;
  onDelta?(event: ChatDeltaEvent): void;
  onEvidence?(event: ChatEvidenceEvent): void;
  onDone?(event: ChatDoneEvent): void;
  /** SSE 開始後の `event: error`、または fetch/接続レベルの失敗。 */
  onError?(problem: Problem): void;
}

const GENERIC_ERROR: Problem = {
  type: "about:blank",
  title: "回答の生成に失敗しました",
  status: 502,
  code: "provider_error",
};

function dispatch(rawEvent: string, handlers: ChatStreamHandlers): void {
  // 1 イベント = 複数行。`event:` と `data:`(複数行 data は改行連結)を集める。
  let eventName = "message";
  const dataLines: string[] = [];
  for (const line of rawEvent.split("\n")) {
    if (line.startsWith(":")) continue; // コメント行(`: ping`)
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).replace(/^ /, ""));
    }
  }
  if (dataLines.length === 0) return;

  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return;
  }

  switch (eventName) {
    case "start":
      handlers.onStart?.(payload as ChatStartEvent);
      break;
    case "delta":
      handlers.onDelta?.(payload as ChatDeltaEvent);
      break;
    case "evidence":
      handlers.onEvidence?.(payload as ChatEvidenceEvent);
      break;
    case "done":
      handlers.onDone?.(payload as ChatDoneEvent);
      break;
    case "error":
      handlers.onError?.(payload as Problem);
      break;
    default:
      break;
  }
}

/**
 * SSE ストリームを最後まで読み、各イベントを handlers へ逐次ディスパッチする。
 * SSE 開始前の HTTP エラー(429 等)は JSON エラーなので onError(Problem) に変換する。
 */
export async function streamChat(
  url: string,
  body: unknown,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch {
    handlers.onError?.(GENERIC_ERROR);
    return;
  }

  if (!res.ok || !res.body) {
    // SSE 開始前のエラー: Problem JSON を試み、無ければ汎用エラー。
    let problem = GENERIC_ERROR;
    try {
      problem = (await res.json()) as Problem;
    } catch {
      /* 本文なし */
    }
    handlers.onError?.(problem);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // 空行(\n\n)でイベント分割。CRLF も正規化。
      buffer = buffer.replace(/\r\n/g, "\n");
      let sep = buffer.indexOf("\n\n");
      while (sep !== -1) {
        const rawEvent = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        if (rawEvent.trim()) dispatch(rawEvent, handlers);
        sep = buffer.indexOf("\n\n");
      }
    }
    // 末尾に残ったイベント(終端の空行なし)。
    if (buffer.trim()) dispatch(buffer, handlers);
  } catch {
    if (signal?.aborted) return; // 意図的な中断は握りつぶす
    handlers.onError?.(GENERIC_ERROR);
  }
}
