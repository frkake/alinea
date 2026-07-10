"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { figuresRestoreOverviewVersion, figuresRewriteOverview, type Problem } from "@alinea/api-client";
import { useToast } from "@/components/ui/Toast";
import { useJobEvents } from "@/hooks/useJobEvents";
import { useViewerStore } from "@/stores/viewer-store";
import { articleKeys } from "@/components/viewer/article/queries";
import { ArticleMetaRow } from "@/components/viewer/article/ArticleMetaRow";
import { ArticleRegenBanner } from "@/components/viewer/article/ArticleRegenBanner";
import { OverviewFigureFrame } from "@/components/viewer/article/OverviewFigureFrame";
import { ArticleBlockItem } from "@/components/viewer/article/ArticleBlockItem";
import { asOverviewFigureRef, type AnchorRef, type Article } from "@/components/viewer/article/types";

export interface ArticleBodyProps {
  article: Article;
  libraryItemId: string;
  revisionId: string;
  onJumpToAnchor: (anchor: AnchorRef) => void;
}

/** 記事本体(1h §3.1 `ArticleBody`。760px カラム)。 */
export function ArticleBody({ article, libraryItemId, revisionId, onJumpToAnchor }: ArticleBodyProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const [overviewJobId, setOverviewJobId] = useState<string | null>(null);
  const [overviewProgressPct, setOverviewProgressPct] = useState(0);

  const articleRegenerating = useViewerStore((s) => s.articleRegenerating);
  const articleRegenProgressPct = useViewerStore((s) => s.articleRegenProgressPct);

  useJobEvents(overviewJobId, {
    onProgress: (e) => setOverviewProgressPct(e.progress_pct),
    onDone: () => {
      setOverviewJobId(null);
      void qc.invalidateQueries({ queryKey: articleKeys.article(libraryItemId, article.preset) });
      void qc.invalidateQueries({ queryKey: articleKeys.overviewFigure(article.id) });
    },
    onError: (problem: Partial<Problem>) => {
      setOverviewJobId(null);
      toast({ kind: "error", message: problem.title ?? "概要図の書き直しに失敗しました" });
    },
  });

  const onRewriteOverview = async (instruction?: string) => {
    try {
      const res = await figuresRewriteOverview({
        path: { article_id: article.id },
        body: { instruction },
        throwOnError: true,
      });
      setOverviewProgressPct(0);
      setOverviewJobId(res.data.job_id);
    } catch {
      toast({ kind: "error", message: "概要図の書き直しを開始できませんでした" });
    }
  };

  const onRestoreOverviewVersion = async (version: number) => {
    try {
      await figuresRestoreOverviewVersion({
        path: { article_id: article.id, version },
        throwOnError: true,
      });
      void qc.invalidateQueries({ queryKey: articleKeys.article(libraryItemId, article.preset) });
      void qc.invalidateQueries({ queryKey: articleKeys.overviewFigure(article.id) });
      toast({ kind: "success", message: "✓ 版を復元しました" });
    } catch {
      toast({ kind: "error", message: "版を復元できませんでした" });
    }
  };

  const overviewFigure = asOverviewFigureRef(article.overview_figure);

  return (
    <div style={{ width: 760, padding: "34px 0 64px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div
        style={{
          fontSize: 27,
          fontWeight: 700,
          lineHeight: 1.5,
          letterSpacing: "-0.2px",
          fontFamily: "var(--pr-font-ui)",
          color: "var(--pr-text)",
        }}
      >
        {article.title}
      </div>
      <ArticleMetaRow disclaimer={article.disclaimer} />
      {articleRegenerating ? <ArticleRegenBanner kind="regenerate" progressPct={articleRegenProgressPct} /> : null}
      {overviewFigure ? (
        <OverviewFigureFrame
          figure={overviewFigure}
          articleId={article.id}
          rewriting={overviewJobId !== null}
          rewritingProgressPct={overviewProgressPct}
          onRewrite={(instruction) => void onRewriteOverview(instruction)}
          onRestoreVersion={(version) => void onRestoreOverviewVersion(version)}
          onJumpToAnchor={onJumpToAnchor}
        />
      ) : null}
      {article.blocks.map((block) => (
        <ArticleBlockItem
          key={block.id}
          libraryItemId={libraryItemId}
          articleId={article.id}
          preset={article.preset}
          block={block}
          revisionId={revisionId}
          includeMath={article.include_math}
          onJumpToAnchor={onJumpToAnchor}
        />
      ))}
    </div>
  );
}
