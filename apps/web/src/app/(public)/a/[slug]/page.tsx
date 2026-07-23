import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PublishedArticle } from "@/components/publication/PublishedArticle";
import { fetchPublication } from "./fetch-publication";

/**
 * 公開記事ページ(Task 26 §3。plans/09 の公開領域)。
 *
 * サーバでサニタイズ済みスナップショット(`PublicArticleOut`)を取得して描画する。
 * private/予約中/不在は notFound()。unlisted は noindex,nofollow、public は
 * canonical + OG メタデータを設定する。本文・コメント UI はクライアント側で
 * 認証状態に応じて出し分ける(`PublishedArticle`)。
 */
export default async function PublicArticlePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const article = await fetchPublication(slug);
  if (!article) notFound();

  // TODO(publisher-identity): isPublisher defaults false here — the public
  // PublicArticleOut/CommentOut payload exposes no publisher identity, so
  // hide/restore is unreachable from the public URL until the API adds an
  // owner-by-slug signal. Server still enforces 403 on unauthorized moderation.
  return <PublishedArticle article={article} />;
}

function siteBase(): string {
  return (
    process.env.NEXT_PUBLIC_SITE_URL ??
    process.env.NEXT_PUBLIC_APP_URL ??
    "http://localhost:3000"
  ).replace(/\/$/, "");
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const article = await fetchPublication(slug);
  if (!article) {
    return { title: "Alinea — 記事", robots: { index: false, follow: false } };
  }

  const canonical = `${siteBase()}/a/${article.slug}`;
  const paperTitle = (article.paper_meta?.title as string | undefined) ?? undefined;
  const description = paperTitle
    ? `「${paperTitle}」を AI がやさしく解説した記事(Alinea 生成)。`
    : "Alinea が生成した論文解説記事。";

  // unlisted は noindex,nofollow。public のみ索引を許可し canonical + OG を付与する。
  if (article.noindex || article.visibility !== "public") {
    return {
      title: `${article.title} — Alinea`,
      description,
      robots: { index: false, follow: false },
    };
  }

  return {
    title: `${article.title} — Alinea`,
    description,
    robots: { index: true, follow: true },
    alternates: { canonical },
    openGraph: {
      type: "article",
      title: article.title,
      description: description.slice(0, 200),
      url: canonical,
      siteName: "Alinea",
    },
    twitter: {
      card: "summary_large_image",
      title: article.title,
      description: description.slice(0, 200),
    },
  };
}
