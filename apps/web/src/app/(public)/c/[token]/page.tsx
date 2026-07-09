import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { EmptyState } from "@/components/ui/EmptyState";
import { ShareCollectionHeader } from "@/components/share/ShareCollectionHeader";
import { ShareFooterNote } from "@/components/share/ShareFooterNote";
import { ShareHeader } from "@/components/share/ShareHeader";
import { SharePaperCard } from "@/components/share/SharePaperCard";
import { ShareThemeScope } from "@/components/share/ShareThemeScope";
import { fetchShareCollection } from "./fetch-share";

/**
 * コレクション共有ページ(4c。plans/09-screens/4c)。v1 で唯一の公開(未認証)画面。
 * 全コンポーネントが Server Component(クライアント JS なし。§1)。
 */
export default async function SharePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  const data = await fetchShareCollection(token);
  if (!data) notFound();

  return (
    <ShareThemeScope>
      <div
        className="alinea-share-scope"
        style={{
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          background: "#E3E1D9",
          color: "var(--pr-text)",
        }}
      >
        <ShareHeader token={token} />
        <main style={{ flex: 1, display: "flex", justifyContent: "center", paddingTop: 28 }}>
          <div
            style={{
              width: 820,
              display: "flex",
              flexDirection: "column",
              gap: 14,
            }}
          >
            <ShareCollectionHeader
              name={data.collection.name}
              description={data.collection.description}
              sharedBy={data.collection.shared_by}
              updatedAt={data.collection.updated_at}
              itemCount={data.collection.item_count}
              deadline={data.collection.deadline}
            />
            {data.items.length === 0 ? (
              <EmptyState
                title="このコレクションにはまだ論文がありません"
                description="共有者が論文を追加すると、ここに表示されます。"
              />
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {data.items.map((item) => (
                  <SharePaperCard
                    key={item.order}
                    order={item.order}
                    title={item.title}
                    authorsShort={item.authors_short}
                    venueYear={item.venue_year}
                    arxivUrl={item.arxiv_url}
                    summary3line={item.summary_3line}
                    sharedNote={item.shared_note}
                  />
                ))}
              </div>
            )}
            <ShareFooterNote />
          </div>
        </main>
      </div>
    </ShareThemeScope>
  );
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ token: string }>;
}): Promise<Metadata> {
  const data = await fetchShareCollection((await params).token);
  if (!data) return { title: "Alinea — 共有ページ", robots: { index: false, follow: false } };
  const description =
    data.collection.description ??
    `${data.collection.shared_by} さんが共有した ${data.collection.item_count} 本の論文コレクション`;
  return {
    title: `${data.collection.name} — Alineaで共有されたコレクション`,
    description,
    robots: { index: false, follow: false },
    openGraph: {
      title: data.collection.name,
      description: description.slice(0, 120),
      siteName: "Alinea",
      images: [{ url: "/og/collection-default.png", width: 1200, height: 630 }],
    },
  };
}
