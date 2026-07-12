"use client";

import { useEffect, useRef, useState } from "react";

/**
 * リアルタイム更新(plans/01 §5)。ユーザー単位 SSE `/api/events` を購読し、
 * 3 回連続失敗でポーリングフォールバックへ切り替える。Last-Event-ID は
 * ネイティブ EventSource の再接続(`id:` フィールド)に委ねつつ、値を公開する。
 */

export type SSEEventType =
  | "job.progress"
  | "job.failed"
  | "job.updated"
  | "translation.unit_completed"
  | "notification.created";

export const SSE_EVENT_TYPES: readonly SSEEventType[] = [
  "job.progress",
  "job.failed",
  "job.updated",
  "translation.unit_completed",
  "notification.created",
];

export interface SSEEvent {
  type: SSEEventType | "message";
  data: unknown;
  lastEventId: string;
}

export interface UseSSEOptions {
  url?: string;
  enabled?: boolean;
  onEvent?: (event: SSEEvent) => void;
  /** ポーリングフォールバックの開始/終了通知(消費側で refetchInterval を制御)。 */
  onFallbackChange?: (active: boolean) => void;
  /** 連続失敗許容回数(既定 3、plans/01 §5)。 */
  maxFailures?: number;
  /** 無受信タイムアウト ms(既定 45000、plans/01 §5)。 */
  idleTimeoutMs?: number;
}

export interface UseSSEResult {
  connected: boolean;
  fallbackActive: boolean;
  lastEventId: string;
}

function parseData(raw: string): unknown {
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return raw;
  }
}

export function useSSE(options: UseSSEOptions = {}): UseSSEResult {
  const {
    url = "/api/events",
    enabled = true,
    onEvent,
    onFallbackChange,
    maxFailures = 3,
    idleTimeoutMs = 45000,
  } = options;

  const [connected, setConnected] = useState(false);
  const [fallbackActive, setFallbackActive] = useState(false);
  const lastEventIdRef = useRef("");

  // 最新のコールバックを参照だけ更新し、接続を貼り直さない。
  const onEventRef = useRef(onEvent);
  const onFallbackRef = useRef(onFallbackChange);
  onEventRef.current = onEvent;
  onFallbackRef.current = onFallbackChange;

  useEffect(() => {
    if (!enabled) return;
    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      // SSE 非対応環境: 即フォールバック。
      setFallbackActive(true);
      onFallbackRef.current?.(true);
      return;
    }

    let source: EventSource | null = null;
    let failures = 0;
    let idleTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const enterFallback = () => {
      setFallbackActive(true);
      onFallbackRef.current?.(true);
    };
    const leaveFallback = () => {
      setFallbackActive(false);
      onFallbackRef.current?.(false);
    };

    const armIdleTimer = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        // 無受信タイムアウト: 貼り直す。
        connect();
      }, idleTimeoutMs);
    };

    const handle = (type: SSEEvent["type"]) => (e: MessageEvent<string>) => {
      failures = 0;
      if (e.lastEventId) lastEventIdRef.current = e.lastEventId;
      armIdleTimer();
      onEventRef.current?.({ type, data: parseData(e.data), lastEventId: e.lastEventId });
    };

    function connect() {
      if (closed) return;
      source?.close();
      source = new EventSource(url, { withCredentials: true });

      source.onopen = () => {
        failures = 0;
        setConnected(true);
        leaveFallback();
        armIdleTimer();
      };

      source.onmessage = handle("message");
      for (const type of SSE_EVENT_TYPES) {
        source.addEventListener(type, handle(type) as EventListener);
      }

      source.onerror = () => {
        setConnected(false);
        failures += 1;
        if (failures >= maxFailures) {
          enterFallback();
        }
      };
    }

    connect();

    return () => {
      closed = true;
      if (idleTimer) clearTimeout(idleTimer);
      source?.close();
      setConnected(false);
    };
  }, [url, enabled, maxFailures, idleTimeoutMs]);

  return { connected, fallbackActive, lastEventId: lastEventIdRef.current };
}
