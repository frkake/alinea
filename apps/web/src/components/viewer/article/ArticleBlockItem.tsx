"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { articlesBlockRewrite, figuresRegenerateExplainer, type ArticleBlockOut } from "@yakudoku/api-client";
import { useToast } from "@/components/ui/Toast";
import { useJobEvents } from "@/hooks/useJobEvents";
import { articleKeys } from "@/components/viewer/article/queries";
import type { Article } from "@/components/viewer/article/types";
import { ArticleBlockHover } from "@/components/viewer/article/ArticleBlockHover";
import { RewriteInstructionPopover } from "@/components/viewer/article/RewriteInstructionPopover";
import { EvidencePopover } from "@/components/viewer/article/EvidencePopover";
import { HeadingBlock } from "@/components/viewer/article/HeadingBlock";
import { ParagraphBlock } from "@/components/viewer/article/ParagraphBlock";
import { QuoteSourceBlock } from "@/components/viewer/article/QuoteSourceBlock";
import { FigureEmbedBlock, FigureLinkCardBlock } from "@/components/viewer/article/FigureEmbedBlock";
import { ExplainerFigureBlock } from "@/components/viewer/article/ExplainerFigureBlock";
import { DiscussionList } from "@/components/viewer/article/DiscussionList";
import { AttributionBlock } from "@/components/viewer/article/AttributionBlock";
import type { AnchorRef } from "@/components/viewer/article/types";

const HOVER_DELAY_MS = 80;
const FLASH_MS = 1200;

export interface ArticleBlockItemProps {
  libraryItemId: string;
  articleId: string;
  block: ArticleBlockOut;
  revisionId: string;
  includeMath: boolean;
  onJumpToAnchor: (anchor: AnchorRef) => void;
}

/**
 * 記事ブロック 1 件(1h §3.1 `ArticleBlockItem`)。ホバー検知+type 別レンダラの振り分け+
 * 書き直し/再生成ジョブの追跡(§5.5)を自己完結で行う(ジョブ完了時は該当ブロックのみ
 * `['article', liId]` キャッシュを差替。記事全体は再取得しない)。
 */
export function ArticleBlockItem({
  libraryItemId,
  articleId,
  block,
  revisionId,
  includeMath,
  onJumpToAnchor,
}: ArticleBlockItemProps) {
  const qc = useQueryClient();
  const toast = useToast();

  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [popover, setPopover] = useState<"rewrite" | "evidence" | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [flash, setFlash] = useState(false);
  const showTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const toolbarAnchorRef = useRef<HTMLDivElement>(null);

  const rewriting = jobId !== null;
  const isExplainer = block.type === "explainer_figure";

  useJobEvents<{ block?: ArticleBlockOut }>(jobId, {
    onDone: (result) => {
      setJobId(null);
      if (isExplainer) {
        // §5.5 例外: result.block を持たないため記事全体を invalidate する。
        void qc.invalidateQueries({ queryKey: articleKeys.article(libraryItemId) });
      } else if (result?.block) {
        const updatedBlock = result.block;
        qc.setQueryData<Article>(articleKeys.article(libraryItemId), (prev) =>
          prev
            ? { ...prev, blocks: prev.blocks.map((b) => (b.id === updatedBlock.id ? updatedBlock : b)) }
            : prev,
        );
      } else {
        void qc.invalidateQueries({ queryKey: articleKeys.article(libraryItemId) });
      }
      setFlash(true);
      flashTimer.current = setTimeout(() => setFlash(false), FLASH_MS);
    },
    onError: () => {
      setJobId(null);
      toast({ kind: "error", message: "× ブロックの書き直しに失敗しました" });
    },
  });

  useEffect(
    () => () => {
      if (showTimer.current) clearTimeout(showTimer.current);
      if (flashTimer.current) clearTimeout(flashTimer.current);
    },
    [],
  );

  const startRewrite = async (instruction?: string) => {
    try {
      const res = isExplainer
        ? await figuresRegenerateExplainer({
            path: { figure_id: block.content.explainer?.figure_id ?? "" },
            body: { instruction },
            throwOnError: true,
          })
        : await articlesBlockRewrite({
            path: { article_id: articleId, block_id: block.id },
            body: { instruction },
            throwOnError: true,
          });
      setJobId(res.data.job_id);
    } catch {
      toast({ kind: "error", message: "× ブロックの書き直しに失敗しました" });
    }
  };

  const locked = block.locked || block.type === "attribution";
  const hasEvidence = block.evidence.length > 0;
  // mouseleave では即時非表示にするが、ポップオーバー(ツールバー外の portal)を開いている
  // 間はホバーが外れても表示を保つ(§5.5 決定。ちらつき防止)。
  const visible = !locked && (hovered || focused || popover !== null);

  const onMouseEnter = () => {
    if (locked) return;
    showTimer.current = setTimeout(() => setHovered(true), HOVER_DELAY_MS);
  };
  const onMouseLeave = () => {
    if (showTimer.current) clearTimeout(showTimer.current);
    setHovered(false);
  };

  let content: ReactNode = null;
  switch (block.type) {
    case "heading":
      content = block.content.heading ? <HeadingBlock heading={block.content.heading} /> : null;
      break;
    case "paragraph":
      content =
        block.content.markdown != null ? (
          <ParagraphBlock
            markdown={block.content.markdown}
            includeMath={includeMath}
            evidence={block.evidence}
            onJumpToAnchor={onJumpToAnchor}
          />
        ) : null;
      break;
    case "quote_source":
      content = block.content.quote ? (
        <QuoteSourceBlock quote={block.content.quote} onJumpToAnchor={onJumpToAnchor} />
      ) : null;
      break;
    case "figure_embed":
      content = block.content.figure ? (
        <FigureEmbedBlock figure={block.content.figure} />
      ) : block.content.figure_link_card ? (
        <FigureLinkCardBlock
          card={block.content.figure_link_card}
          anchor={block.evidence[0]?.anchor ?? null}
          onJumpToAnchor={onJumpToAnchor}
        />
      ) : null;
      break;
    case "explainer_figure":
      content = block.content.explainer ? (
        <ExplainerFigureBlock explainer={block.content.explainer} />
      ) : null;
      break;
    case "discussion":
      content = block.content.discussion ? <DiscussionList discussion={block.content.discussion} /> : null;
      break;
    case "attribution":
      content = block.content.attribution ? (
        <AttributionBlock attribution={block.content.attribution} />
      ) : null;
      break;
    default:
      content = null;
  }

  return (
    <div
      data-block-id={block.id}
      data-article-block="true"
      tabIndex={locked ? undefined : 0}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      onFocus={() => !locked && setFocused(true)}
      onBlur={() => setFocused(false)}
      style={{
        position: "relative",
        opacity: rewriting ? 0.55 : 1,
        background: flash ? "var(--pr-as)" : "transparent",
        transition: "background-color 1200ms ease-out",
        borderRadius: 6,
      }}
    >
      <div ref={toolbarAnchorRef} style={{ position: "absolute", top: 0, right: 0, width: 0, height: 0 }} />
      <ArticleBlockHover
        visible={visible}
        rewriting={rewriting}
        hasEvidence={hasEvidence}
        onRewriteClick={() => setPopover("rewrite")}
        onRegenerate={() => void startRewrite(undefined)}
        onShowEvidence={() => setPopover("evidence")}
      />
      {content}
      <RewriteInstructionPopover
        open={popover === "rewrite"}
        onClose={() => setPopover(null)}
        anchorRef={toolbarAnchorRef}
        placeholder="例: もっと平易に / 式を使って"
        pending={rewriting}
        onSubmit={(instruction) => {
          setPopover(null);
          void startRewrite(instruction.length > 0 ? instruction : undefined);
        }}
      />
      {hasEvidence ? (
        <EvidencePopover
          open={popover === "evidence"}
          onClose={() => setPopover(null)}
          anchorRef={toolbarAnchorRef}
          revisionId={revisionId}
          evidence={block.evidence}
          onJumpToAnchor={onJumpToAnchor}
        />
      ) : null}
    </div>
  );
}
