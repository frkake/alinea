"use client";

import { useState } from "react";
import { articlesGenerate, type Problem } from "@alinea/api-client";
import type { Preset } from "@/components/viewer/article/types";
import { EmptyState } from "@/components/ui/EmptyState";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Toggle } from "@/components/ui/Toggle";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { useToast } from "@/components/ui/Toast";
import { useJobEvents } from "@/hooks/useJobEvents";

const PRESET_OPTIONS: ReadonlyArray<{ value: Preset; label: string }> = [
  { value: "beginner", label: "初学者向け" },
  { value: "implementer", label: "実装者向け" },
  { value: "researcher", label: "研究者向け" },
  { value: "reading_group", label: "輪読会向け" },
];

/** プリセット既定の「数式を含める」(plans/03 §19.2・docs/07 §2.6)。 */
const PRESET_INCLUDE_MATH_DEFAULT: Record<Preset, boolean> = {
  beginner: false,
  implementer: true,
  researcher: true,
  reading_group: false,
};

export interface ArticleGenerateCTAProps {
  libraryItemId: string;
  onGenerated: () => void;
}

/** 記事未生成(404)時の生成 CTA(1h §5.2)。 */
export function ArticleGenerateCTA({ libraryItemId, onGenerated }: ArticleGenerateCTAProps) {
  const toast = useToast();
  const [preset, setPreset] = useState<Preset>("beginner");
  const [includeMath, setIncludeMath] = useState(PRESET_INCLUDE_MATH_DEFAULT.beginner);
  const [toggleTouched, setToggleTouched] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [progressPct, setProgressPct] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  useJobEvents(jobId, {
    onProgress: (e) => setProgressPct(e.progress_pct),
    onDone: () => {
      setJobId(null);
      onGenerated();
    },
    onError: (problem: Partial<Problem>) => {
      setJobId(null);
      toast({ kind: "error", message: problem.title ?? "記事の生成に失敗しました" });
    },
  });

  const onPresetChange = (next: Preset) => {
    setPreset(next);
    if (!toggleTouched) setIncludeMath(PRESET_INCLUDE_MATH_DEFAULT[next]);
  };

  const onSubmit = async () => {
    if (submitting || jobId) return;
    setSubmitting(true);
    try {
      const res = await articlesGenerate({
        path: { item_id: libraryItemId },
        body: { preset, include_math: includeMath },
        throwOnError: true,
      });
      setProgressPct(0);
      setJobId(res.data.job_id);
    } catch (err) {
      const problem = err as Partial<Problem> | undefined;
      toast({ kind: "error", message: problem?.title ?? "記事の生成を開始できませんでした" });
    } finally {
      setSubmitting(false);
    }
  };

  if (jobId) {
    return (
      <div style={{ marginTop: 120, display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
        <span style={{ fontSize: 12, color: "var(--pr-a)" }}>✦ 記事を生成しています… {progressPct}%</span>
        <div style={{ width: 260 }}>
          <ProgressBar value={progressPct} color="accent" />
        </div>
      </div>
    );
  }

  return (
    <div style={{ marginTop: 120, display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
      <EmptyState
        title="この論文の記事はまだありません"
        description="訳文・メモ・チャット履歴から、AI がブログ風の読み物を構成します。"
      />
      <SegmentedControl
        ariaLabel="記事のプリセット"
        size="lg"
        options={PRESET_OPTIONS}
        value={preset}
        onChange={onPresetChange}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Toggle
          ariaLabel="数式を含める"
          checked={includeMath}
          onChange={(next) => {
            setToggleTouched(true);
            setIncludeMath(next);
          }}
        />
        <span style={{ fontSize: 11.5, color: "var(--pr-text-mid)" }}>数式を含める</span>
      </div>
      <button
        type="button"
        disabled={submitting}
        onClick={() => void onSubmit()}
        style={{
          height: 30,
          padding: "0 16px",
          background: "var(--pr-a)",
          color: "#FFFFFF",
          border: "none",
          borderRadius: 7,
          fontSize: 12,
          fontWeight: 600,
          cursor: submitting ? "default" : "pointer",
          opacity: submitting ? 0.7 : 1,
          fontFamily: "inherit",
        }}
      >
        ✦ 記事を生成
      </button>
    </div>
  );
}
