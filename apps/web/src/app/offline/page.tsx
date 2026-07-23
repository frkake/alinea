"use client";

import { useEffect, useState } from "react";
import { OFFLINE_VIEWER_CACHE, OFFLINE_VIEWER_MANIFEST_URL } from "@/lib/offline-viewer";

/**
 * オフラインシェル(Task 23 / spec 2026-07-16-pwa-offline-design §B v2)。
 *
 * `/papers/{itemId}` ナビゲーションがネットワーク到達不能で失敗したとき、Service Worker が
 * この precache 済みシェルを返す(login リダイレクトの代わり)。到達済み論文なら client 側の
 * ランタイムキャッシュから viewer データが読めるため、ここでは「保存済み論文一覧」と
 * 「再接続案内」を出す。キャッシュが無ければ再接続案内のみを表示する。
 *
 * ★ auth 安全性: このページは静的シェルであり、認証情報も他ユーザーのデータも一切埋め込まない。
 *   保存済み一覧は SW の viewer キャッシュ manifest(この端末のアクティブユーザー分)から読む。
 */
type SavedPaper = { itemId: string; revisionId: string };

async function readSavedPapers(): Promise<SavedPaper[]> {
  if (typeof caches === "undefined") return [];
  try {
    const cache = await caches.open(OFFLINE_VIEWER_CACHE);
    const res = await cache.match(OFFLINE_VIEWER_MANIFEST_URL);
    if (!res) return [];
    const manifests = (await res.json()) as Array<{ itemId: string; revisionId: string }>;
    return manifests.map((m) => ({ itemId: m.itemId, revisionId: m.revisionId }));
  } catch {
    return [];
  }
}

export default function OfflinePage() {
  const [papers, setPapers] = useState<SavedPaper[] | null>(null);
  const [online, setOnline] = useState(true);

  useEffect(() => {
    void readSavedPapers().then(setPapers);
    const update = () => setOnline(typeof navigator === "undefined" ? true : navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 20,
        padding: "48px 22px",
        background: "var(--pr-bg-app)",
        color: "var(--pr-text)",
        fontFamily: "var(--pr-font-ui)",
        textAlign: "center",
      }}
    >
      <div style={{ maxWidth: 520, width: "100%" }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 8px" }}>オフラインです</h1>
        <p style={{ fontSize: 13, color: "var(--pr-text-sub)", lineHeight: 1.8, margin: 0 }}>
          {online
            ? "この論文のデータは端末に保存されていません。接続を確認して再読み込みしてください。"
            : "ネットワークに接続されていません。保存済みの論文はオフラインでも開けます。"}
        </p>

        <div style={{ marginTop: 24, textAlign: "left" }}>
          <h2
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: "var(--pr-text-mid)",
              margin: "0 0 8px",
            }}
          >
            保存済みの論文
          </h2>
          {papers === null ? (
            <p style={{ fontSize: 12, color: "var(--pr-text-muted)" }}>読み込み中…</p>
          ) : papers.length === 0 ? (
            <p style={{ fontSize: 12, color: "var(--pr-text-muted)" }}>
              オフラインで開ける論文はまだありません。オンラインで論文を開くと自動で保存されます。
            </p>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 6 }}>
              {papers.map((p) => (
                <li key={p.itemId}>
                  <a
                    href={`/papers/${p.itemId}`}
                    style={{
                      display: "block",
                      padding: "10px 12px",
                      borderRadius: 8,
                      border: "1px solid var(--pr-border-control)",
                      background: "var(--pr-bg-control)",
                      color: "var(--pr-text)",
                      fontSize: 13,
                      textDecoration: "none",
                    }}
                  >
                    {p.itemId}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div style={{ marginTop: 24 }}>
          <button
            type="button"
            onClick={() => window.location.reload()}
            style={{
              height: 34,
              padding: "0 16px",
              borderRadius: 8,
              border: "1px solid var(--pr-border-control)",
              background: "var(--pr-bg-control)",
              color: "var(--pr-text)",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            再接続して再読み込み
          </button>
        </div>
      </div>
    </main>
  );
}
