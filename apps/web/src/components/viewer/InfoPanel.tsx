"use client";

import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  papersReingest,
  settingsGet,
  type LicenseCard,
  type PaperBib,
  type Problem,
  type RevisionInfo,
  type TimelineEntry,
} from "@alinea/api-client";
import { useToast } from "@/components/ui/Toast";
import { IngestLogModal } from "@/components/viewer/IngestLogModal";
import { ReingestConfirmModal } from "@/components/viewer/ReingestConfirmModal";
import { CancelIngestConfirmModal } from "@/components/library/CancelIngestConfirmModal";
import { useCancelIngest } from "@/hooks/useCancelIngest";

export interface InfoPanelProps {
  paper: PaperBib;
  revision: RevisionInfo;
  licenseCard: LicenseCard;
  ingestTimeline: TimelineEntry[];
  /** エクスポート導線用。 */
  itemId: string;
  /**
   * モバイル縮退のボトムシート(mobile.md §4.5)から閲覧専用で再利用する場合 true。
   * 再取り込み(操作系)を非描画にする(決定)。処理ログの閲覧・エクスポートは維持。
   */
  readOnly?: boolean;
}

/** 品質レベルの説明文(逐語。2a §4.2-b。docs/02 の品質定義)。 */
const QUALITY_DESCRIPTION: Record<"A" | "B", string> = {
  A: "LaTeX ソースから完全構造化。数式・相互参照・図表・脚注を保持しています。",
  B: "PDF から抽出して構造化。レイアウト由来の誤りが残る可能性があります。",
};

/** 再取り込み進行行の stage 表示名(2a §5.7。全値+未知値フォールバック)。 */
const STAGE_LABELS: Record<string, string> = {
  queued: "待機中",
  fetching: "ソース取得中",
  parsing: "解析中",
  structuring: "構造化中",
  translating_abstract: "翻訳中",
  readable: "翻訳中",
  translating_body: "翻訳中",
  waiting_quota: "待機中(翻訳上限)",
};

function stageLabel(stage: string, status: string): string {
  if (status === "waiting_quota") return STAGE_LABELS.waiting_quota ?? "処理中";
  return STAGE_LABELS[stage] ?? "処理中";
}

const headingStyle: CSSProperties = {
  fontSize: 10.5,
  fontWeight: 700,
  color: "var(--pr-text-muted)",
  letterSpacing: "0.4px",
};

const chipStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  height: 19,
  padding: "0 8px",
  border: "1px solid var(--pr-border-control)",
  borderRadius: 4,
  fontSize: 10.5,
};

const actionLinkStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  padding: 0,
  fontSize: 10.5,
  cursor: "pointer",
  fontFamily: "inherit",
};

/** 2 行目以降は同一日付なら HH:mm のみ、初回は M/DD HH:mm(2a §4.2-b)。 */
function formatTimeline(entries: TimelineEntry[]): { text: string; label: string }[] {
  let prevDate = "";
  return entries.map((e) => {
    const d = new Date(e.at);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const dateKey = `${d.getMonth() + 1}/${String(d.getDate()).padStart(2, "0")}`;
    const time = prevDate === dateKey ? `${hh}:${mm}` : `${dateKey} ${hh}:${mm}`;
    prevDate = dateKey;
    return { text: time, label: e.label };
  });
}

interface JobProgress {
  stage: string;
  status: string;
  progressPct: number;
}

/** 情報タブ(2a §3〜4)。書誌・品質と取り込み(タイムライン+再取り込み+処理ログ)・ライセンス・エクスポート・フッタ注記。 */
export function InfoPanel({
  paper,
  revision,
  licenseCard,
  ingestTimeline,
  itemId,
  readOnly = false,
}: InfoPanelProps) {
  const level: "A" | "B" = revision.quality_level === "B" ? "B" : "A";
  const timeline = formatTimeline(ingestTimeline);
  const reuse = licenseCard.figure_reuse;
  const licenseTone =
    reuse === "allowed"
      ? { border: "rgba(101,148,113,0.4)", bg: "rgba(101,148,113,0.10)", title: "#4C7458" }
      : reuse === "forbidden"
        ? { border: "rgba(176,104,79,0.4)", bg: "rgba(176,104,79,0.10)", title: "var(--pr-warn)" }
        : { border: "var(--pr-border-control)", bg: "var(--pr-bg-inset)", title: "var(--pr-text-mid)" };

  const toast = useToast();
  const queryClient = useQueryClient();
  const router = useRouter();

  const [reingestConfirmOpen, setReingestConfirmOpen] = useState(false);
  const [ingestLogOpen, setIngestLogOpen] = useState(false);
  const [cancelConfirmOpen, setCancelConfirmOpen] = useState(false);
  const [reingestJobId, setReingestJobId] = useState<string | null>(null);
  const [jobProgress, setJobProgress] = useState<JobProgress | null>(null);

  const cancelIngest = useCancelIngest(
    () => {
      void queryClient.invalidateQueries({ queryKey: ["library"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      toast({ kind: "success", message: "取り込みをキャンセルしました" });
      setReingestJobId(null);
      router.push("/library");
    },
    () => {
      setCancelConfirmOpen(false);
      toast({ kind: "error", message: "キャンセルできませんでした" });
    },
  );

  const reingestMutation = useMutation({
    mutationFn: async () => {
      const res = await papersReingest({ path: { paper_id: paper.id }, throwOnError: true });
      return res.data;
    },
    onSuccess: (data) => {
      setReingestConfirmOpen(false);
      setJobProgress(null);
      setReingestJobId(data.job_id);
    },
    onError: (error) => {
      const problem = error as Partial<Problem> | undefined;
      toast({
        kind: "error",
        message: problem?.code === "conflict" ? "再取り込みは既に実行中です" : "再取り込みに失敗しました",
      });
    },
  });

  const trackingQuery = useQuery({
    queryKey: ["settings", "detail"],
    queryFn: async () => (await settingsGet({ throwOnError: true })).data,
    staleTime: 60_000,
  });
  const trackingEnabled =
    (trackingQuery.data as { reading?: { track_reading_time?: boolean } } | undefined)?.reading
      ?.track_reading_time !== false;

  // 再取り込みジョブ SSE(2a §2.3・§5.7)。202 受領後のみ購読し、done/error で購読を終える。
  useEffect(() => {
    if (!reingestJobId) return;
    if (typeof EventSource === "undefined") return;

    const source = new EventSource(`/api/jobs/${reingestJobId}/events`, { withCredentials: true });

    const onProgress = (event: MessageEvent<string>) => {
      try {
        const data = JSON.parse(event.data) as { stage: string; status: string; progress_pct: number };
        setJobProgress({ stage: data.stage, status: data.status, progressPct: data.progress_pct });
      } catch {
        // 破損フレームは無視する(P3: 黙って壊れない)。
      }
    };
    const onDone = () => {
      setReingestJobId(null);
      setJobProgress(null);
      void queryClient.invalidateQueries({ queryKey: ["viewer", itemId] });
      toast({ kind: "success", message: "再取り込みが完了しました" });
      source.close();
    };
    const onSseError = (event: MessageEvent<string>) => {
      setReingestJobId(null);
      setJobProgress(null);
      let message = "再取り込みに失敗しました";
      if (event.data) {
        try {
          const problem = JSON.parse(event.data) as Problem;
          if (problem.title) message = problem.title;
        } catch {
          // 既定文言を使う(P3)。
        }
      }
      toast({ kind: "error", message });
      source.close();
    };

    source.addEventListener("progress", onProgress as EventListener);
    source.addEventListener("done", onDone as EventListener);
    source.addEventListener("error", onSseError as EventListener);

    return () => {
      source.close();
    };
  }, [reingestJobId, itemId, queryClient, toast]);

  const timelineRows: { content: ReactNode; pulsing: boolean }[] = timeline.map((t) => ({
    content: (
      <>
        {t.text} — {t.label}
      </>
    ),
    pulsing: false,
  }));
  if (jobProgress) {
    timelineRows.push({
      content: (
        <>
          {stageLabel(jobProgress.stage, jobProgress.status)} — {jobProgress.progressPct}%
        </>
      ),
      pulsing: true,
    });
  }

  const reingestPending = reingestMutation.isPending || reingestJobId !== null;

  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14, fontSize: 12 }}>
        {/* (a) 書誌情報 */}
        <section style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={headingStyle}>書誌情報</div>
          <div style={{ fontSize: 12.5, fontWeight: 600, lineHeight: 1.55, color: "var(--pr-text)" }}>
            {paper.title}
          </div>
          <div style={{ fontSize: 11, color: "var(--pr-text-sub)", lineHeight: 1.6 }}>
            {paper.authors.join(", ")}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5, paddingTop: 2 }}>
            {paper.venue ? <span style={{ ...chipStyle, color: "var(--pr-text-mid)" }}>{paper.venue}</span> : null}
            {paper.arxiv_id ? (
              <a
                href={`https://arxiv.org/abs/${paper.arxiv_id}${paper.arxiv_version ?? ""}`}
                target="_blank"
                rel="noopener noreferrer"
                style={{ ...chipStyle, color: "var(--pr-acc)", fontWeight: 600, textDecoration: "none" }}
              >
                arXiv:{paper.arxiv_id} ↗
              </a>
            ) : null}
            {paper.doi ? (
              <a
                href={`https://doi.org/${paper.doi}`}
                target="_blank"
                rel="noopener noreferrer"
                style={{ ...chipStyle, color: "var(--pr-acc)", fontWeight: 600, textDecoration: "none" }}
              >
                DOI ↗
              </a>
            ) : null}
          </div>
        </section>

        <div role="separator" style={{ height: 1, background: "var(--pr-border-hair)" }} />

        {/* (b) 品質レベルと取り込み */}
        <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={headingStyle}>品質レベルと取り込み</div>
          <div style={{ display: "flex", gap: 9, alignItems: "flex-start" }}>
            <span
              style={{
                width: 26,
                height: 26,
                flex: "none",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                borderRadius: 6,
                fontSize: 13,
                fontWeight: 700,
                background: level === "A" ? "var(--pr-acc-s)" : "var(--pr-bg-inset)",
                color: level === "A" ? "var(--pr-acc)" : "var(--pr-text-sub2)",
              }}
            >
              {level}
            </span>
            <span style={{ fontSize: 11, color: "var(--pr-text-sub)", lineHeight: 1.65 }}>
              {QUALITY_DESCRIPTION[level]}
            </span>
          </div>
          {timelineRows.length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", paddingLeft: 3 }}>
              {timelineRows.map((row, i) => {
                const last = i === timelineRows.length - 1;
                return (
                  <div key={i} style={{ display: "flex", gap: 9, fontSize: 10.5, color: "var(--pr-text-sub)" }}>
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                      <span
                        className={row.pulsing ? "alinea-pulse" : undefined}
                        style={{
                          width: 7,
                          height: 7,
                          borderRadius: "50%",
                          background: row.pulsing ? "var(--pr-acc)" : "var(--pr-green)",
                          marginTop: 3,
                          ...(row.pulsing ? { animationDuration: "1.2s" } : {}),
                        }}
                      />
                      {!last ? <span style={{ width: 1.5, flex: 1, background: "var(--pr-border-pane)" }} /> : null}
                    </div>
                    <div style={{ paddingBottom: last ? 0 : 10 }}>{row.content}</div>
                  </div>
                );
              })}
            </div>
          ) : null}
          <div style={{ display: "flex", gap: 12, paddingLeft: 3 }}>
            {readOnly ? null : reingestJobId ? (
              <button
                type="button"
                onClick={() => setCancelConfirmOpen(true)}
                disabled={cancelIngest.isPending}
                style={{
                  ...actionLinkStyle,
                  color: "var(--pr-warn)",
                  fontWeight: 600,
                  opacity: cancelIngest.isPending ? 0.6 : 1,
                  cursor: cancelIngest.isPending ? "default" : "pointer",
                }}
              >
                取り込みを中止
              </button>
            ) : (
              <button
                type="button"
                onClick={() => setReingestConfirmOpen(true)}
                disabled={reingestPending}
                style={{
                  ...actionLinkStyle,
                  color: "var(--pr-acc)",
                  fontWeight: 600,
                  opacity: reingestPending ? 0.6 : 1,
                  cursor: reingestPending ? "default" : "pointer",
                }}
              >
                再取り込み
              </button>
            )}
            <button
              type="button"
              onClick={() => setIngestLogOpen(true)}
              style={{ ...actionLinkStyle, color: "var(--pr-text-muted)" }}
            >
              処理ログ
            </button>
          </div>
        </section>

        <div role="separator" style={{ height: 1, background: "var(--pr-border-hair)" }} />

        {/* (c) ライセンス */}
        <section style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          <div style={headingStyle}>ライセンス</div>
          <div
            style={{
              border: `1px solid ${licenseTone.border}`,
              background: licenseTone.bg,
              borderRadius: 8,
              padding: "9px 11px",
              display: "flex",
              flexDirection: "column",
              gap: 3,
            }}
          >
            <div style={{ fontSize: 11.5, fontWeight: 700, color: licenseTone.title }}>{licenseCard.license}</div>
            <div style={{ fontSize: 10, color: "var(--pr-text-sub)", lineHeight: 1.6 }}>{licenseCard.message}</div>
          </div>
        </section>

        <div role="separator" style={{ height: 1, background: "var(--pr-border-hair)" }} />

        {/* (d) エクスポート */}
        <section style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          <div style={headingStyle}>エクスポート</div>
          <div style={{ display: "flex", gap: 6 }}>
            <a
              download
              href={`/api/library-items/${itemId}/export/annotations`}
              style={exportBtnStyle}
            >
              注釈 Markdown ⤓
            </a>
            <a download href={`/api/papers/${paper.id}/pdf`} style={exportBtnStyle}>
              原文 PDF ⤓
            </a>
          </div>
        </section>
      </div>

      {/* フッタ注記 */}
      <div
        style={{
          padding: "10px 14px",
          borderTop: "1px solid var(--pr-border-soft)",
          fontSize: 10,
          color: "var(--pr-text-muted)",
          lineHeight: 1.6,
        }}
      >
        {trackingEnabled
          ? "読書時間を記録しています(設定でオフにできます)"
          : "読書時間の記録はオフです(設定でオンにできます)"}
      </div>

      <ReingestConfirmModal
        open={reingestConfirmOpen}
        pending={reingestMutation.isPending}
        onCancel={() => setReingestConfirmOpen(false)}
        onConfirm={() => reingestMutation.mutate()}
      />
      <IngestLogModal open={ingestLogOpen} paperId={paper.id} onClose={() => setIngestLogOpen(false)} />
      <CancelIngestConfirmModal
        open={cancelConfirmOpen}
        pending={cancelIngest.isPending}
        onCancel={() => setCancelConfirmOpen(false)}
        onConfirm={() => cancelIngest.mutate(itemId)}
      />
    </div>
  );
}

const exportBtnStyle: CSSProperties = {
  flex: 1,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  height: 28,
  border: "1px solid var(--pr-border-control)",
  borderRadius: 6,
  fontSize: 11,
  color: "var(--pr-text-mid)",
  textDecoration: "none",
};
