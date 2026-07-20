"use client";

import { Fragment, type CSSProperties, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  authMe,
  publicationCommentsList,
  type CommentOut,
  type PublicArticleOut,
} from "@alinea/api-client";
import { AIBadge } from "@/components/ui/AIBadge";
import { renderArticleMarkdown } from "@/components/viewer/article/markdown";
import { CommentThread, publicationCommentKeys } from "@/components/publication/CommentThread";

/**
 * 公開記事ページ本体(Task 26 §3)。API が返すサニタイズ済みスナップショット
 * (`PublicArticleOut`)だけを描画する。原文・訳文・メモ・チャット・非公開議論は
 * スナップショットに含まれない(サーバ側 allow-list。ここでは追加取得もしない)。
 *
 * - 記事本文(heading / paragraph / attribution / explainer_figure / overview_figure)
 * - 書誌(paper_meta)・公開者(Alinea 生成である旨)・公開日時
 * - ブロック別コメントスレッド(認証済みは投稿可、匿名はログイン CTA のみ)
 */
export interface PublishedArticleProps {
  article: PublicArticleOut;
  /**
   * 閲覧者がこの記事の公開者か(hide/restore 描画の可否)。API は publisher を公開しない
   * ため上位が判定して渡す。既定 false(通常の閲覧者)。
   */
  isPublisher?: boolean;
}

interface SnapshotBlock {
  type: string;
  block_id: string;
  content: Record<string, unknown>;
  evidence: Array<{ ref?: number; paper_title?: string; section?: string }>;
}

function asBlocks(raw: PublicArticleOut["blocks"]): SnapshotBlock[] {
  return (raw ?? []).map((b, index) => {
    const block = b as Record<string, unknown>;
    return {
      type: String(block.type ?? ""),
      block_id: typeof block.block_id === "string" ? block.block_id : String(index),
      content: (block.content as Record<string, unknown>) ?? {},
      evidence: Array.isArray(block.evidence) ? (block.evidence as SnapshotBlock["evidence"]) : [],
    };
  });
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("ja-JP", { dateStyle: "long", timeStyle: "short" });
}

const LOGIN_HREF = "/login";

/** 書誌カード(公開情報のみ。paper_meta は build_paper_meta の出力形)。 */
function Bibliography({ meta }: { meta: Record<string, unknown> }) {
  const title = typeof meta.title === "string" ? meta.title : null;
  const authors = Array.isArray(meta.authors) ? (meta.authors as string[]) : [];
  const arxivId = typeof meta.arxiv_id === "string" ? meta.arxiv_id : null;
  const doi = typeof meta.doi === "string" ? meta.doi : null;
  const venue = typeof meta.venue === "string" ? meta.venue : null;
  const publishedOn = typeof meta.published_on === "string" ? meta.published_on : null;
  const license = typeof meta.license === "string" ? meta.license : null;

  return (
    <aside
      aria-label="元論文の書誌"
      style={{
        border: "1px solid var(--pr-border-hair)",
        borderRadius: 12,
        padding: "14px 16px",
        background: "var(--pr-bg-card)",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ fontSize: 11, color: "var(--pr-text-muted)", fontWeight: 600 }}>元の論文</div>
      {title ? (
        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--pr-text)", lineHeight: 1.5 }}>{title}</div>
      ) : null}
      {authors.length > 0 ? (
        <div style={{ fontSize: 12, color: "var(--pr-text-sub)" }}>{authors.join(", ")}</div>
      ) : null}
      <div style={{ fontSize: 12, color: "var(--pr-text-sub)", display: "flex", flexWrap: "wrap", gap: 10 }}>
        {venue ? <span>{venue}</span> : null}
        {publishedOn ? <span>{publishedOn}</span> : null}
        {arxivId ? (
          <a
            href={`https://arxiv.org/abs/${arxivId}`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--pr-acc)" }}
          >
            arXiv:{arxivId}
          </a>
        ) : null}
        {doi ? (
          <a
            href={`https://doi.org/${doi}`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--pr-acc)" }}
          >
            DOI:{doi}
          </a>
        ) : null}
      </div>
      {license ? (
        <div style={{ fontSize: 11, color: "var(--pr-text-muted)" }}>ライセンス: {license}</div>
      ) : null}
    </aside>
  );
}

function EvidenceRefs({ evidence }: { evidence: SnapshotBlock["evidence"] }) {
  if (!evidence || evidence.length === 0) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
      {evidence.map((e, i) => (
        <span
          key={i}
          style={{
            fontSize: 10.5,
            color: "var(--pr-text-muted)",
            border: "1px solid var(--pr-border-hair)",
            borderRadius: 999,
            padding: "1px 8px",
          }}
        >
          {e.paper_title ? `${e.paper_title}` : "根拠"}
          {e.section ? ` · ${e.section}` : ""}
        </span>
      ))}
    </div>
  );
}

/** 1 ブロックの描画。allow-list 外は描画しない(サーバ側で除去済みだが二重の防御)。 */
function BlockView({ block }: { block: SnapshotBlock }) {
  if (block.type === "heading") {
    const heading = (block.content.heading as { level?: number; text?: string } | undefined) ?? {};
    const level = heading.level && heading.level >= 2 && heading.level <= 4 ? heading.level : 2;
    const Tag = (`h${level}` as "h2" | "h3" | "h4");
    return (
      <Tag style={{ fontSize: level === 2 ? 20 : 16, fontWeight: 700, margin: 0, color: "var(--pr-text)" }}>
        {heading.text ?? ""}
      </Tag>
    );
  }
  if (block.type === "paragraph") {
    const markdown = typeof block.content.markdown === "string" ? block.content.markdown : "";
    return (
      <div style={{ fontSize: 15, lineHeight: 1.85, color: "var(--pr-text)" }}>
        {renderArticleMarkdown(markdown, false)}
        <EvidenceRefs evidence={block.evidence} />
      </div>
    );
  }
  if (block.type === "attribution") {
    const attribution = (block.content.attribution as { text?: string } | undefined) ?? {};
    return (
      <p
        style={{
          fontSize: 12,
          color: "var(--pr-text-muted)",
          fontStyle: "italic",
          margin: 0,
          padding: "10px 12px",
          borderLeft: "3px solid var(--pr-border-control)",
          background: "var(--pr-bg-inset)",
        }}
      >
        {attribution.text ?? ""}
      </p>
    );
  }
  if (block.type === "explainer_figure") {
    const explainer = (block.content.explainer as { image_url?: string; caption?: string } | undefined) ?? {};
    return (
      <figure style={{ margin: 0, display: "flex", flexDirection: "column", gap: 6 }}>
        {explainer.image_url ? (
          // 公開スナップショットの解説図(AI 生成・ライセンス確認済み)。
          <img
            src={explainer.image_url}
            alt={explainer.caption ?? "解説図"}
            style={{ maxWidth: "100%", borderRadius: 8, border: "1px solid var(--pr-border-hair)" }}
          />
        ) : null}
        {explainer.caption ? (
          <figcaption style={{ fontSize: 11.5, color: "var(--pr-text-muted)" }}>{explainer.caption}</figcaption>
        ) : null}
      </figure>
    );
  }
  if (block.type === "overview_figure") {
    const svgUrl = typeof block.content.svg_url === "string" ? block.content.svg_url : null;
    const rasterUrl = typeof block.content.raster_url === "string" ? block.content.raster_url : null;
    const src = svgUrl ?? rasterUrl;
    if (!src) return null;
    return (
      <figure style={{ margin: 0 }}>
        <img
          src={src}
          alt="全体概要図"
          style={{ maxWidth: "100%", borderRadius: 8, border: "1px solid var(--pr-border-hair)" }}
        />
      </figure>
    );
  }
  return null;
}

export function PublishedArticle({ article, isPublisher = false }: PublishedArticleProps) {
  const blocks = asBlocks(article.blocks);

  // 認証状態(未認証は 401 で reject される)。ログイン CTA / 投稿フォームの出し分けに使う。
  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: async () => (await authMe({ throwOnError: true })).data,
    retry: false,
    staleTime: 60_000,
  });
  const isAuthenticated = meQuery.isSuccess && Boolean(meQuery.data?.user);

  // この publication のコメント一覧(認証不要)。block_id で束ねる。
  const commentsQuery = useQuery({
    queryKey: publicationCommentKeys.comments(article.slug),
    queryFn: async () => (await publicationCommentsList({ path: { slug: article.slug }, throwOnError: true })).data,
    staleTime: 30_000,
  });
  const commentsByBlock = new Map<string, CommentOut[]>();
  for (const c of commentsQuery.data ?? []) {
    const list = commentsByBlock.get(c.block_id) ?? [];
    list.push(c);
    commentsByBlock.set(c.block_id, list);
  }

  const metaTitle = (article.paper_meta?.title as string | undefined) ?? undefined;

  const columnStyle: CSSProperties = {
    width: "100%",
    maxWidth: 760,
    margin: "0 auto",
    padding: "36px 16px 96px",
    display: "flex",
    flexDirection: "column",
    gap: 18,
  };

  return (
    <article style={columnStyle}>
      <header style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <h1 style={{ fontSize: 28, fontWeight: 800, lineHeight: 1.45, margin: 0, color: "var(--pr-text)" }}>
          {article.title}
        </h1>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", fontSize: 11.5, color: "var(--pr-text-muted)" }}>
          <AIBadge variant="generated" />
          <span>Alinea で生成・公開された解説記事</span>
          {article.published_at ? <span>· 公開 {formatWhen(article.published_at)}</span> : null}
        </div>
        <p style={{ fontSize: 12, color: "var(--pr-text-muted)", margin: 0, lineHeight: 1.6 }}>
          この記事は AI が生成した{metaTitle ? `「${metaTitle}」の` : ""}解説であり、元の論文とは別物です。
        </p>
      </header>

      <Bibliography meta={article.paper_meta ?? {}} />

      <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
        {blocks.map((block) => (
          <Fragment key={block.block_id}>
            <div data-published-block={block.block_id} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <BlockView block={block} />
              <BlockComments
                slug={article.slug}
                block={block}
                comments={commentsByBlock.get(block.block_id) ?? []}
                isAuthenticated={isAuthenticated}
                isPublisher={isPublisher}
                showLoginCta={meQuery.isFetched && !isAuthenticated}
              />
            </div>
          </Fragment>
        ))}
      </div>
    </article>
  );
}

/** ブロック直下のコメント領域。匿名にはログイン CTA、認証済みには投稿フォーム付きスレッド。 */
function BlockComments({
  slug,
  block,
  comments,
  isAuthenticated,
  isPublisher,
  showLoginCta,
}: {
  slug: string;
  block: SnapshotBlock;
  comments: CommentOut[];
  isAuthenticated: boolean;
  isPublisher: boolean;
  showLoginCta: boolean;
}): ReactNode {
  const hasComments = comments.length > 0;
  // コメントが 1 件も無く匿名でもないなら、UI を出さない(ノイズ回避)。
  if (!hasComments && !isAuthenticated && !showLoginCta) return null;

  const label =
    block.type === "heading"
      ? String((block.content.heading as { text?: string } | undefined)?.text ?? "")
      : undefined;

  return (
    <div style={{ paddingLeft: 4 }}>
      {hasComments || isAuthenticated ? (
        <CommentThread
          slug={slug}
          blockId={block.block_id}
          blockLabel={label}
          comments={comments}
          isAuthenticated={isAuthenticated}
          isPublisher={isPublisher}
        />
      ) : null}
      {showLoginCta ? (
        <p style={{ fontSize: 12, color: "var(--pr-text-muted)", margin: "6px 0 0" }}>
          コメントするには
          <a href={LOGIN_HREF} style={{ color: "var(--pr-acc)", margin: "0 4px" }}>
            ログイン
          </a>
          してください。
        </p>
      ) : null}
    </div>
  );
}
