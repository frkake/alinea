"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  exportStandaloneAvailability,
  exportStandaloneStart,
  exportStandaloneStatus,
  type StandaloneAvailability,
} from "@alinea/api-client";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { triggerDownload } from "@/components/settings/download";

/** ポーリング間隔(ExportSettings と同値。4f §2.3)。 */
const EXPORT_JOB_POLL_MS = 2000;

type Artifact = keyof StandaloneAvailability;

/** 選択肢(表示順)。ラベルと「未生成」時の理由を持つ。 */
const ARTIFACT_OPTIONS: ReadonlyArray<{ key: Artifact; label: string }> = [
  { key: "translation_html", label: "訳文 (HTML)" },
  { key: "bilingual_html", label: "対訳 (HTML)" },
  { key: "source_html", label: "原文 (HTML)" },
  { key: "article_html", label: "記事 (HTML)" },
  { key: "pdf_original", label: "原文 PDF" },
  { key: "pdf_translated", label: "訳文 PDF" },
  { key: "pdf_bilingual", label: "対訳 PDF" },
];

export interface PaperExportModalProps {
  open: boolean;
  itemId: string;
  onClose: () => void;
}

interface StartResponse {
  mode: string;
  job_id: string | null;
  download_url: string | null;
}

interface StatusResponse {
  job: { id: string; status: string; progress_pct?: number; error?: Record<string, unknown> | null };
  download_url: string | null;
}

/**
 * 論文単位スタンドアロンエクスポートの複数選択モーダル(Feature S3・Task 12)。
 *
 * availability=false の成果物は選択不可(理由を併記)。単一 HTML の選択は同期 URL を
 * 即ダウンロードし、複数/PDF を含む選択は paper_export job を起動して進捗・完了・失敗を出す。
 */
export function PaperExportModal({ open, itemId, onClose }: PaperExportModalProps) {
  const toast = useToast();
  const firstRef = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<Set<Artifact>>(new Set());
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [jobError, setJobError] = useState<string | null>(null);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);

  // モーダルを開くたびに状態をリセットする。
  useEffect(() => {
    if (!open) {
      setSelected(new Set());
      setJobId(null);
      setBusy(false);
      setJobError(null);
      setDownloadUrl(null);
    }
  }, [open]);

  const availabilityQuery = useQuery({
    queryKey: ["standalone-availability", itemId],
    queryFn: async (): Promise<StandaloneAvailability> =>
      (await exportStandaloneAvailability({ path: { item_id: itemId }, throwOnError: true }))
        .data,
    enabled: open,
    staleTime: 30_000,
  });
  const availability = availabilityQuery.data;

  // ジョブ進捗のポーリング(完了 or 失敗で停止)。
  const statusQuery = useQuery({
    queryKey: ["standalone-export", itemId, jobId],
    queryFn: async (): Promise<StatusResponse> =>
      (
        await exportStandaloneStatus({
          path: { item_id: itemId, job_id: jobId as string },
          throwOnError: true,
        })
      ).data as StatusResponse,
    enabled: jobId !== null,
    refetchInterval: (query) => {
      const s = query.state.data?.job.status;
      return s === "succeeded" || s === "failed" ? false : EXPORT_JOB_POLL_MS;
    },
  });

  useEffect(() => {
    const data = statusQuery.data;
    if (!data || jobId === null) return;
    if (data.job.status === "succeeded" && data.download_url) {
      setDownloadUrl(data.download_url);
      setBusy(false);
      setJobId(null);
    } else if (data.job.status === "failed") {
      const msg =
        (typeof data.job.error?.message === "string" ? data.job.error.message : null) ??
        "エクスポートに失敗しました";
      setJobError(msg);
      setBusy(false);
      setJobId(null);
    }
  }, [statusQuery.data, jobId]);

  const selectedList = useMemo(() => Array.from(selected), [selected]);

  const toggle = (key: Artifact) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    // 選択を変えたら前回の結果表示はクリアする。
    setDownloadUrl(null);
    setJobError(null);
  };

  async function onExport(): Promise<void> {
    if (selectedList.length === 0 || busy) return;
    setBusy(true);
    setJobError(null);
    setDownloadUrl(null);
    try {
      const res = await exportStandaloneStart({
        path: { item_id: itemId },
        body: { artifacts: selectedList },
        throwOnError: true,
      });
      const body = res.data as StartResponse;
      if (body.mode === "sync" && body.download_url) {
        triggerDownload(body.download_url);
        setBusy(false);
        onClose();
        return;
      }
      if (body.job_id) {
        setJobId(body.job_id);
        return; // 完了は statusQuery の effect で処理する
      }
      // 予期しない応答。
      setBusy(false);
      toast({ kind: "error", message: "エクスポートを開始できませんでした" });
    } catch {
      setBusy(false);
      toast({ kind: "error", message: "エクスポートを開始できませんでした" });
    }
  }

  const firstEnabledKey = ARTIFACT_OPTIONS.find(
    (o) => availability && availability[o.key],
  )?.key;

  return (
    <Modal
      open={open}
      onClose={onClose}
      width={420}
      labelledBy="paper-export-title"
      initialFocusRef={firstRef}
    >
      <div style={{ padding: "16px 18px 8px" }}>
        <h2 id="paper-export-title" style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>
          エクスポート
        </h2>
        <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--pr-text-muted)" }}>
          出力する成果物を選択してください(サーバ不要で開けます)
        </p>
      </div>

      <div style={{ padding: "0 18px 12px", display: "flex", flexDirection: "column", gap: 4 }}>
        {!availability ? (
          <span style={{ fontSize: 11.5, color: "var(--pr-text-muted)", padding: "12px 4px" }}>
            読み込み中…
          </span>
        ) : (
          ARTIFACT_OPTIONS.map((opt, idx) => {
            const disabled = !availability[opt.key];
            return (
              <label
                key={opt.key}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 4px",
                  fontSize: 12,
                  color: disabled ? "var(--pr-text-muted)" : "var(--pr-text)",
                  cursor: disabled ? "not-allowed" : "pointer",
                }}
              >
                <input
                  ref={opt.key === firstEnabledKey && idx === 0 ? firstRef : undefined}
                  type="checkbox"
                  aria-label={opt.label}
                  checked={selected.has(opt.key)}
                  disabled={disabled}
                  onChange={() => toggle(opt.key)}
                />
                <span>{opt.label}</span>
                {disabled && (
                  <span
                    style={{ marginLeft: "auto", fontSize: 10.5, color: "var(--pr-text-muted)" }}
                  >
                    未生成
                  </span>
                )}
              </label>
            );
          })
        )}
      </div>

      {(busy || jobError || downloadUrl) && (
        <div style={{ padding: "0 18px 8px", fontSize: 11.5 }}>
          {busy && !jobError && !downloadUrl && (
            <span style={{ color: "var(--pr-text-muted)" }}>準備中…(生成には数十秒かかることがあります)</span>
          )}
          {jobError && <span style={{ color: "var(--pr-warn)" }}>{jobError}</span>}
          {downloadUrl && (
            <a
              href={downloadUrl}
              rel="noopener"
              style={{ color: "var(--pr-a)", fontWeight: 600 }}
              onClick={() => {
                onClose();
              }}
            >
              ⤓ ダウンロード
            </a>
          )}
        </div>
      )}

      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 8,
          padding: "10px 18px 16px",
          borderTop: "1px solid var(--pr-border-hair)",
        }}
      >
        <button
          type="button"
          onClick={onClose}
          style={{
            height: 30,
            padding: "0 14px",
            border: "1px solid var(--pr-border-control)",
            borderRadius: 6,
            background: "transparent",
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: 12,
            color: "var(--pr-text-mid)",
          }}
        >
          閉じる
        </button>
        <button
          type="button"
          onClick={() => void onExport()}
          disabled={selectedList.length === 0 || busy}
          style={{
            height: 30,
            padding: "0 16px",
            border: "none",
            borderRadius: 6,
            background: "var(--pr-a)",
            color: "#fff",
            cursor: selectedList.length === 0 || busy ? "default" : "pointer",
            opacity: selectedList.length === 0 || busy ? 0.5 : 1,
            fontFamily: "inherit",
            fontSize: 12,
            fontWeight: 600,
          }}
        >
          エクスポート
        </button>
      </div>
    </Modal>
  );
}
