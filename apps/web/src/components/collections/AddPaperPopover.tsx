"use client";

import { useEffect, useState, type CSSProperties, type RefObject } from "react";
import { useQuery } from "@tanstack/react-query";
import { libraryItemsList } from "@yakudoku/api-client";
import { Popover } from "@/components/ui/Popover";

/**
 * 「+ 論文を追加」ポップオーバー(plans/09-screens/4b §5.6)。
 * 候補検索は既存の `GET /api/library-items?q=` を使う(`@yakudoku/api-client` に生成済み)。
 */
export interface AddPaperPopoverProps {
  open: boolean;
  onClose: () => void;
  anchorRef: RefObject<HTMLElement | null>;
  existingLibraryItemIds: ReadonlySet<string>;
  onSelect: (libraryItemId: string) => void;
}

export function AddPaperPopover({
  open,
  onClose,
  anchorRef,
  existingLibraryItemIds,
  onSelect,
}: AddPaperPopoverProps) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  const query = useQuery({
    queryKey: ["libraryItems", "addPaperSearch", debounced],
    queryFn: async () =>
      (
        await libraryItemsList({
          query: { q: debounced, limit: 10 },
          throwOnError: true,
        })
      ).data,
    enabled: open && debounced.length > 0,
  });

  if (!open) return null;

  const items = query.data?.items ?? [];

  return (
    <Popover open={open} onClose={onClose} anchorRef={anchorRef} width={360} placement="bottom-end">
      <div style={{ padding: 10, display: "flex", flexDirection: "column", gap: 6 }}>
        <input
          type="text"
          autoFocus
          placeholder="タイトル・著者で検索"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={searchInputStyle}
        />
        <div style={{ maxHeight: 320, overflowY: "auto" }}>
          {debounced.length === 0 ? (
            <div style={emptyTextStyle}>ライブラリから検索して追加します</div>
          ) : items.length === 0 ? (
            <div style={emptyTextStyle}>見つかりませんでした</div>
          ) : (
            items.map((item) => {
              const added = existingLibraryItemIds.has(item.id);
              return (
                <button
                  key={item.id}
                  type="button"
                  disabled={added}
                  onClick={() => onSelect(item.id)}
                  style={resultRowStyle}
                >
                  <span
                    style={{
                      flex: 1,
                      minWidth: 0,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      fontSize: 11.5,
                      fontWeight: 600,
                    }}
                  >
                    {item.paper.title}
                  </span>
                  {added ? (
                    <span style={{ fontSize: 9.5, color: "var(--pr-text-muted)", flex: "none" }}>
                      追加済み
                    </span>
                  ) : null}
                </button>
              );
            })
          )}
        </div>
      </div>
    </Popover>
  );
}

const searchInputStyle: CSSProperties = {
  height: 30,
  background: "var(--pr-bg-inset)",
  border: "none",
  borderRadius: 6,
  padding: "0 10px",
  fontSize: 11.5,
  fontFamily: "inherit",
};

const emptyTextStyle: CSSProperties = {
  fontSize: 10.5,
  color: "var(--pr-text-muted)",
  padding: 12,
};

const resultRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  padding: "8px 12px",
  width: "100%",
  border: "none",
  background: "transparent",
  textAlign: "left",
  cursor: "pointer",
  fontFamily: "inherit",
};
