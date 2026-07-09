"use client";

import { useEffect, useRef } from "react";
import { settingsGet } from "@alinea/api-client";

const HEARTBEAT_MS = 30_000;
const ACTIVITY_WINDOW_MS = 60_000;
const ACTIVITY_EVENTS = ["pointermove", "pointerdown", "keydown", "wheel", "scroll", "touchstart"] as const;
const ACTIVITY_THROTTLE_MS = 250;

interface ReadingSessionBody {
  client_session_id: string;
  started_at: string;
  last_activity_at: string;
  active_seconds: number;
}

/**
 * 読書時間計測(plans/07 §8.1 / plans/03 §5.9)。
 *
 * `client_session_id` を生成し、`document.visibilityState === 'visible'` かつ直近 60 秒以内に
 * 入力イベントがある間、1 秒ティックで `active_seconds` を加算する。30 秒間隔で
 * `POST /api/library-items/{id}/reading-sessions` を送り(冪等 upsert)、`visibilitychange(hidden)` /
 * `pagehide` 時は `navigator.sendBeacon` で即時送信する。
 *
 * 設定 `reading.track_reading_time=false` のときは計測・送信を行わない。
 * `POST …/reading-sessions` は本レーン着手時点で `@alinea/api-client` に未生成のため、
 * `fetch()` 直書きで plans/03 §5.9 の契約どおりに呼ぶ(deviations 参照)。
 */
export function useReadingSession(params: { itemId: string; enabled?: boolean }): void {
  const { itemId, enabled = true } = params;
  const clientSessionId = useRef<string>(
    typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `local-${Date.now()}`,
  );
  const startedAt = useRef<string>(new Date().toISOString());
  const lastActivityAt = useRef<number>(Date.now());
  const activeSeconds = useRef<number>(0);
  const trackingAllowed = useRef<boolean>(true);
  const lastActivityEventAt = useRef<number>(0);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void settingsGet()
      .then((res) => {
        if (cancelled) return;
        const data = res.data as { reading?: { track_reading_time?: boolean } } | undefined;
        trackingAllowed.current = data?.reading?.track_reading_time !== false;
      })
      .catch(() => {
        // 取得失敗時は既定(計測する)を維持する(P3: 黙って壊れない)。
      });
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;

    const onActivity = () => {
      const now = Date.now();
      if (now - lastActivityEventAt.current < ACTIVITY_THROTTLE_MS) return;
      lastActivityEventAt.current = now;
      lastActivityAt.current = now;
    };
    for (const type of ACTIVITY_EVENTS) {
      window.addEventListener(type, onActivity, { passive: true });
    }

    const tick = window.setInterval(() => {
      if (!trackingAllowed.current) return;
      const active =
        document.visibilityState === "visible" &&
        Date.now() - lastActivityAt.current <= ACTIVITY_WINDOW_MS;
      if (active) activeSeconds.current += 1;
    }, 1_000);

    return () => {
      for (const type of ACTIVITY_EVENTS) {
        window.removeEventListener(type, onActivity);
      }
      window.clearInterval(tick);
    };
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;

    const send = (useBeacon: boolean) => {
      if (!trackingAllowed.current) return;
      const body: ReadingSessionBody = {
        client_session_id: clientSessionId.current,
        started_at: startedAt.current,
        last_activity_at: new Date(lastActivityAt.current).toISOString(),
        active_seconds: activeSeconds.current,
      };
      const url = `/api/library-items/${itemId}/reading-sessions`;
      if (useBeacon && typeof navigator.sendBeacon === "function") {
        navigator.sendBeacon(url, new Blob([JSON.stringify(body)], { type: "application/json" }));
        return;
      }
      void fetch(url, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        keepalive: true,
      }).catch(() => {
        // ネットワーク断は次回ハートビートで累計値を再送するため黙って無視する(P3)。
      });
    };

    const heartbeat = window.setInterval(() => send(false), HEARTBEAT_MS);

    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") send(true);
    };
    const onPageHide = () => send(true);

    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("pagehide", onPageHide);

    return () => {
      window.clearInterval(heartbeat);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("pagehide", onPageHide);
    };
  }, [enabled, itemId]);
}
