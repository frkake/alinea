"use client";

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { authMe } from "@alinea/api-client";
import { meQueryKey } from "@/components/notifications/queryKeys";
import { registerServiceWorker } from "@/lib/register-sw";
import { postActiveUser } from "@/lib/offline-viewer";

/**
 * PWA Service Worker 登録トリガ(spec 2026-07-16-pwa-offline-design §C)。
 *
 * 何も描画しない(null)。本番ビルドでのみ、マウント後に SW を登録する。
 * 開発時は Next の HMR とキャッシュが干渉するため登録しない。
 *
 * Task 23(オフライン閲覧の per-user 分離): 認証済みユーザー ID を SW へ `SET_ACTIVE_USER`
 * で通知する。ユーザーが変わったら SW 側で旧ユーザーの応答を cache フォールバック候補から
 * 外す(別アカウントに他人の本文が漏れない)。未ログイン(401)時は null を通知して
 * どのユーザーの cache も選択されない状態にする。QueryClient 配下で描画される必要があるため
 * layout.tsx では Providers の内側に置く。
 */
export function ServiceWorkerRegistration(): null {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    registerServiceWorker();
  }, []);

  // me は 401 を正常系(未ログイン)として扱うため throwOnError させない。
  const meQuery = useQuery({
    queryKey: meQueryKey,
    queryFn: async () => (await authMe()).data ?? null,
    staleTime: 60_000,
    retry: false,
  });
  const userId = meQuery.data?.user.id ?? null;

  useEffect(() => {
    // controller 就任前でも取りこぼさないよう、controllerchange でも再通知する。
    postActiveUser(userId);
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    const onChange = () => postActiveUser(userId);
    navigator.serviceWorker.addEventListener("controllerchange", onChange);
    return () => navigator.serviceWorker.removeEventListener("controllerchange", onChange);
  }, [userId]);

  return null;
}
