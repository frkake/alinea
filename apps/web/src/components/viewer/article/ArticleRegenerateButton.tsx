"use client";

import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { articlesRegenerate, type Problem } from "@yakudoku/api-client";
import type { Preset } from "@/components/viewer/article/types";
import { AiMark } from "@/components/ui/AIBadge";
import { useToast } from "@/components/ui/Toast";
import { useJobEvents } from "@/hooks/useJobEvents";
import { articleKeys, fetchArticle } from "@/components/viewer/article/queries";
import { useViewerStore } from "@/stores/viewer-store";
import { RegeneratePopover } from "@/components/viewer/article/RegeneratePopover";

/**
 * ヘッダ「✦ 指示つき再生成」(1h §4.2-7・§5.3)。mode=article の時のみ表示される
 * (viewer-shell §4.3・ViewerHeader.tsx の分岐)。記事が未生成の間はレンダリングしない。
 */
export function ArticleRegenerateButton({ itemId }: { itemId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const btnRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  const setArticleRegenState = useViewerStore((s) => s.setArticleRegenState);

  const articleQuery = useQuery({
    queryKey: articleKeys.article(itemId),
    queryFn: () => fetchArticle(itemId),
    retry: false,
    staleTime: Infinity,
  });

  useJobEvents(jobId, {
    onProgress: (e) => setArticleRegenState({ regenerating: true, progressPct: e.progress_pct }),
    onDone: () => {
      setJobId(null);
      setArticleRegenState({ regenerating: false, progressPct: 0 });
      const article = articleQuery.data;
      void qc.invalidateQueries({ queryKey: articleKeys.article(itemId) });
      if (article) void qc.invalidateQueries({ queryKey: articleKeys.articleVersions(article.id) });
      toast({ kind: "success", message: `✓ 記事を再生成しました(版 ${(article?.version ?? 0) + 1})` });
    },
    onError: (problem: Partial<Problem>) => {
      setJobId(null);
      setArticleRegenState({ regenerating: false, progressPct: 0 });
      if (problem.code === "quota_exceeded") {
        toast({ kind: "error", message: "今月の生成クォータを使い切りました" });
      } else if (problem.code === "rate_limited") {
        toast({ kind: "error", message: "操作が多すぎます。しばらく待って再試行してください" });
      } else {
        toast({ kind: "error", message: problem.title ?? "記事の再生成に失敗しました" });
      }
    },
  });

  const article = articleQuery.data;
  if (!article) return null;

  const onSubmit = async (req: { instruction?: string; preset?: Preset; include_math?: boolean }) => {
    setOpen(false);
    try {
      const res = await articlesRegenerate({
        path: { article_id: article.id },
        body: req,
        throwOnError: true,
      });
      setArticleRegenState({ regenerating: true, progressPct: 0 });
      setJobId(res.data.job_id);
    } catch (err) {
      const problem = err as Partial<Problem> | undefined;
      if (problem?.code === "quota_exceeded") {
        toast({ kind: "error", message: "今月の生成クォータを使い切りました" });
      } else if (problem?.code === "rate_limited") {
        toast({ kind: "error", message: "操作が多すぎます。しばらく待って再試行してください" });
      } else {
        toast({ kind: "error", message: problem?.title ?? "記事の再生成を開始できませんでした" });
      }
    }
  };

  const pending = jobId !== null;

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        disabled={pending}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          height: 26,
          padding: "0 10px",
          border: "1px solid var(--pr-border-control)",
          borderRadius: 6,
          fontSize: 11.5,
          color: "var(--pr-a)",
          fontWeight: 600,
          background: "transparent",
          cursor: pending ? "default" : "pointer",
          opacity: pending ? 0.5 : 1,
          pointerEvents: pending ? "none" : "auto",
          fontFamily: "inherit",
        }}
      >
        <AiMark /> 指示つき再生成
      </button>
      <RegeneratePopover
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={btnRef}
        currentPreset={article.preset}
        currentIncludeMath={article.include_math}
        onSubmit={(req) => void onSubmit(req)}
        pending={pending}
      />
    </>
  );
}
