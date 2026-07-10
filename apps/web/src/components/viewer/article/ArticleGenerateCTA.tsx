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
  preset?: Preset;
  onGenerated: () => void;
}

/** 記事未生成(404)時の生成 CTA(1h §5.2)。 */
function pendingJobKey(libraryItemId: string, preset: Preset): string {
  return `alinea-article-job:${libraryItemId}:${preset}`;
}

export function ArticleGenerateCTA({ libraryItemId, preset: fixedPreset, onGenerated }: ArticleGenerateCTAProps) {
  const toast = useToast();
  const [selectedPreset, setSelectedPreset] = useState<Preset>(fixedPreset ?? "beginner");
  const preset = fixedPreset ?? selectedPreset;
  const [includeMath, setIncludeMath] = useState(PRESET_INCLUDE_MATH_DEFAULT[preset]);
  const [toggleTouched, setToggleTouched] = useState(false);
  const [jobId, setJobId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(pendingJobKey(libraryItemId, preset));
  });
  const [progressPct, setProgressPct] = useState(0);
  const [progressStage, setProgressStage] = useState("素材を準備しています");
  const [submitting, setSubmitting] = useState(false);

  useJobEvents(jobId, {
    onProgress: (e) => {
      setProgressPct(e.progress_pct);
      setProgressStage(
        e.detail ||
          ({
            queued: "生成を開始します",
            collecting_sources: "本文・メモ・追加リソースを集めています",
            generating: "読者タイプに合わせて記事を執筆しています",
            rendering: "図と記事レイアウトを仕上げています",
          }[e.stage ?? ""] ?? "記事を生成しています"),
      );
    },
    onDone: () => {
      window.localStorage.removeItem(pendingJobKey(libraryItemId, preset));
      setJobId(null);
      onGenerated();
    },
    onError: (problem: Partial<Problem>) => {
      window.localStorage.removeItem(pendingJobKey(libraryItemId, preset));
      setJobId(null);
      toast({ kind: "error", message: problem.title ?? "記事の生成に失敗しました" });
    },
  });

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
      window.localStorage.setItem(pendingJobKey(libraryItemId, preset), res.data.job_id);
      setJobId(res.data.job_id);
    } catch (err) {
      const problem = err as Partial<Problem> | undefined;
      toast({ kind: "error", message: problem?.title ?? "記事の生成を開始できませんでした" });
    } finally {
      setSubmitting(false);
    }
  };

  const onPresetChange = (next: Preset) => {
    setSelectedPreset(next);
    if (!toggleTouched) setIncludeMath(PRESET_INCLUDE_MATH_DEFAULT[next]);
  };

  if (jobId) {
    return (
      <div style={{ marginTop: 120, display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
        <span style={{ fontSize: 12, color: "var(--pr-a)" }}>✦ 記事を生成しています… {progressPct}%</span>
        <span style={{ fontSize: 11, color: "var(--pr-text-sub)" }}>{progressStage}</span>
        <div style={{ width: 260 }}>
          <ProgressBar value={progressPct} color="accent" />
        </div>
        <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
          他の画面を開いても生成は続きます
        </span>
      </div>
    );
  }

  return (
    <div style={{ marginTop: 120, display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
      <EmptyState
        title="この論文の記事はまだありません"
        description="訳文・メモ・チャット履歴から、AI がブログ風の読み物を構成します。"
      />
      {fixedPreset ? (
        <div style={{ fontSize: 12, fontWeight: 700, color: "var(--pr-text)" }}>
          {PRESET_OPTIONS.find((option) => option.value === preset)?.label}の記事を生成
        </div>
      ) : (
        <SegmentedControl
          ariaLabel="記事のプリセット"
          size="lg"
          options={PRESET_OPTIONS}
          value={preset}
          onChange={onPresetChange}
        />
      )}
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
