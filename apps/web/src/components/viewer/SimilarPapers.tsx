"use client";

import type { CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";
import { libraryItemsSimilar, type SimilarPaper } from "@alinea/api-client";

/**
 * 似た論文パネル(S12 セマンティック検索。docs/10 §5・情報パネル §)。
 *
 * `GET /api/library-items/{id}/similar` を引き、意味的に近い自分のライブラリ内の論文を
 * title・authors・類似度・ライブラリへのリンクで並べる。フラグ off / 埋め込み未整備のときは
 * API が空配列 + `indexing=false` を返すため、セクションごと非表示にする(既存導線を壊さない)。
 */
export interface SimilarPapersProps {
  itemId: string;
}

const headingStyle: CSSProperties = {
  fontSize: 10.5,
  fontWeight: 700,
  color: "var(--pr-text-muted)",
  letterSpacing: "0.4px",
};

const rowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  padding: "7px 9px",
  borderRadius: 6,
  border: "1px solid var(--pr-border-hair)",
  background: "var(--pr-bg-inset)",
  textDecoration: "none",
  color: "inherit",
};

/** 類似度(0〜1)を「92%」表記にする。 */
function formatSimilarity(similarity: number): string {
  return `${Math.round(similarity * 100)}%`;
}

function SimilarPaperRow({ paper }: { paper: SimilarPaper }) {
  const authors = paper.authors.join(", ");
  return (
    <a href={`/papers/${paper.library_item_id}`} style={rowStyle} data-testid="similar-paper-row">
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span
          style={{
            flex: 1,
            minWidth: 0,
            fontSize: 11.5,
            fontWeight: 600,
            color: "var(--pr-text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {paper.title}
        </span>
        <span
          style={{ fontSize: 10, fontWeight: 700, color: "var(--pr-acc)", flex: "none" }}
          title="意味的な類似度"
        >
          {formatSimilarity(paper.similarity)}
        </span>
      </div>
      {authors ? (
        <span
          style={{
            fontSize: 10,
            color: "var(--pr-text-muted)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {authors}
        </span>
      ) : null}
    </a>
  );
}

export function SimilarPapers({ itemId }: SimilarPapersProps) {
  const query = useQuery({
    queryKey: ["similar", itemId],
    queryFn: async () => {
      const res = await libraryItemsSimilar({ path: { item_id: itemId }, throwOnError: true });
      return res.data;
    },
    staleTime: 60_000,
  });

  // 取得前・エラーは何も出さない(P3: 検索補助なので静かに縮退)。
  if (!query.isSuccess) return null;
  const { items, indexing } = query.data;

  // フラグ off / 埋め込み未整備で近傍なし → セクションごと非表示(既存 InfoPanel を壊さない)。
  if (items.length === 0 && !indexing) return null;

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={headingStyle}>似た論文</div>
      {indexing ? (
        <div style={{ fontSize: 10.5, color: "var(--pr-text-muted)", lineHeight: 1.6 }}>
          埋め込みを準備しています。少し待つと似た論文が表示されます。
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {items.map((paper) => (
            <SimilarPaperRow key={paper.library_item_id} paper={paper} />
          ))}
        </div>
      )}
    </section>
  );
}
