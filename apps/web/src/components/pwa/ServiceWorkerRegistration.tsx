"use client";

import { useEffect } from "react";
import { registerServiceWorker } from "@/lib/register-sw";

/**
 * PWA Service Worker 登録トリガ(spec 2026-07-16-pwa-offline-design §C)。
 *
 * 何も描画しない(null)。本番ビルドでのみ、マウント後に SW を登録する。
 * 開発時は Next の HMR とキャッシュが干渉するため登録しない。
 */
export function ServiceWorkerRegistration(): null {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    registerServiceWorker();
  }, []);
  return null;
}
