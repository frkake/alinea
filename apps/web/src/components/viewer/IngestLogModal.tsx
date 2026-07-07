"use client";

import { useQuery } from "@tanstack/react-query";
import { papersIngestLog, type PapersIngestLogEntry } from "@yakudoku/api-client";
import { Modal } from "@/components/ui/Modal";
import { EmptyState } from "@/components/ui/EmptyState";

export interface IngestLogModalProps {
  open: boolean;
  paperId: string;
  onClose: () => void;
}

/** level バッジの表示(2a §5.7。info は非表示)。 */
const LEVEL_STYLE: Record<string, { label: string; color: string }> = {
  warn: { label: "warn", color: "var(--pr-amber)" },
  error: { label: "error", color: "var(--pr-warn)" },
};

/** 時刻整形 `M/DD HH:mm:ss`(2a §5.7)。 */
function formatLogTime(at: string | null | undefined): string {
  if (!at) return "";
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return "";
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${d.getMonth() + 1}/${dd} ${hh}:${mi}:${ss}`;
}

/**
 * 処理ログモーダル(2a §3.1 `IngestLogModal`・§4.2.5 レイアウト・§5.7 挙動。width 560px)。
 * `GET /api/papers/{paper_id}/ingest-log` を開いた時に取得する(staleTime 0 = 開くたび再取得)。
 */
export function IngestLogModal({ open, paperId, onClose }: IngestLogModalProps) {
  const query = useQuery({
    queryKey: ["ingest-log", paperId],
    queryFn: async () =>
      (await papersIngestLog({ path: { paper_id: paperId }, throwOnError: true })).data.entries,
    enabled: open,
    staleTime: 0,
  });
  const entries: PapersIngestLogEntry[] = query.data ?? [];

  return (
    <Modal open={open} onClose={onClose} width={560} labelledBy="ingest-log-title">
      <div
        style={{
          padding: 20,
          display: "flex",
          flexDirection: "column",
          gap: 10,
          maxHeight: "70vh",
          overflowY: "auto",
        }}
      >
        <h2 id="ingest-log-title" style={{ fontSize: 14, fontWeight: 700, margin: 0, color: "var(--pr-text)" }}>
          処理ログ
        </h2>
        {query.isLoading ? (
          <div style={{ fontSize: 11.5, color: "var(--pr-text-muted)" }}>読み込み中…</div>
        ) : entries.length === 0 ? (
          <EmptyState title="ログはまだありません" />
        ) : (
          <div>
            {entries.map((entry, i) => {
              const level = entry.level ? LEVEL_STYLE[entry.level] : undefined;
              return (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    gap: 10,
                    padding: "7px 0",
                    borderBottom: "1px solid var(--pr-border-hair)",
                    fontSize: 11,
                  }}
                >
                  <span
                    style={{
                      width: 96,
                      flex: "none",
                      fontFamily: "var(--pr-font-mono)",
                      color: "var(--pr-text-muted)",
                    }}
                  >
                    {formatLogTime(entry.at)}
                  </span>
                  <span style={{ width: 36, flex: "none", fontSize: 9.5, fontWeight: 700, color: level?.color }}>
                    {level?.label ?? ""}
                  </span>
                  <span style={{ flex: 1, color: "var(--pr-text-mid)" }}>{entry.message ?? ""}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Modal>
  );
}
