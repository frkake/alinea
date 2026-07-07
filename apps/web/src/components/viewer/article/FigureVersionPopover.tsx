"use client";

import { type RefObject } from "react";
import { useQuery } from "@tanstack/react-query";
import { figuresGetOverview } from "@yakudoku/api-client";
import { Popover } from "@/components/ui/Popover";
import { articleKeys } from "@/components/viewer/article/queries";

/** 概要図の版一覧ポップオーバー(1h §5.4。width 220)。 */
export function FigureVersionPopover({
  open,
  onClose,
  anchorRef,
  articleId,
  currentVersion,
  onRestore,
}: {
  open: boolean;
  onClose: () => void;
  anchorRef: RefObject<HTMLElement | null>;
  articleId: string;
  currentVersion: number;
  onRestore: (version: number) => void;
}) {
  const query = useQuery({
    queryKey: articleKeys.overviewFigure(articleId),
    queryFn: async () =>
      (await figuresGetOverview({ path: { article_id: articleId }, throwOnError: true })).data,
    enabled: open,
    staleTime: 0,
  });
  const versions = [...(query.data?.versions ?? [])].sort((a, b) => b.version - a.version);

  return (
    <Popover open={open} onClose={onClose} anchorRef={anchorRef} width={220} placement="bottom-end">
      <div style={{ padding: "6px 0" }}>
        {versions.map((v) => (
          <div
            key={v.version}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "6px 12px",
              fontSize: 11,
              fontWeight: v.version === currentVersion ? 600 : 400,
              color: "var(--pr-text-mid)",
            }}
          >
            <span>
              版 {v.version} · {v.generated_at.slice(0, 10)}
              {v.version === currentVersion ? "(現在)" : ""}
            </span>
            {v.version !== currentVersion ? (
              <button
                type="button"
                onClick={() => onRestore(v.version)}
                style={{
                  border: "none",
                  background: "transparent",
                  padding: 0,
                  cursor: "pointer",
                  fontFamily: "inherit",
                  fontSize: 10.5,
                  color: "var(--pr-a)",
                }}
              >
                この版に戻す
              </button>
            ) : null}
          </div>
        ))}
      </div>
    </Popover>
  );
}
