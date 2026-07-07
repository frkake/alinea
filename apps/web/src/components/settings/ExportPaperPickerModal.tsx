"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { libraryItemsList } from "@yakudoku/api-client";
import { Modal } from "@/components/ui/Modal";
import { triggerDownload } from "@/components/settings/download";

const DEBOUNCE_MS = 200;

/** 論文単位 Markdown の対象選択モーダル(4f §4.6 #1)。 */
export interface ExportPaperPickerModalProps {
  open: boolean;
  onClose: () => void;
}

export function ExportPaperPickerModal({ open, onClose }: ExportPaperPickerModalProps) {
  const [value, setValue] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setValue("");
      setDebouncedQ("");
    }
  }, [open]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(value.trim()), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [value]);

  const query = useQuery({
    queryKey: ["settings", "paper-picker", debouncedQ],
    queryFn: async () =>
      (
        await libraryItemsList({
          query: { q: debouncedQ || undefined, limit: 20 },
          throwOnError: true,
        })
      ).data.items,
    enabled: open,
    staleTime: 60_000,
  });

  const items = query.data ?? [];

  return (
    <Modal
      open={open}
      onClose={onClose}
      width={520}
      labelledBy="export-paper-picker-title"
      initialFocusRef={inputRef}
    >
      <div style={{ padding: "16px 18px 10px" }}>
        <h2 id="export-paper-picker-title" style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>
          エクスポートする論文を選択
        </h2>
      </div>
      <div style={{ padding: "0 18px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
        <input
          ref={inputRef}
          type="text"
          value={value}
          placeholder="タイトル・著者で検索"
          aria-label="タイトル・著者で検索"
          onChange={(e) => {
            setValue(e.target.value);
          }}
          style={{
            width: "100%",
            height: 28,
            background: "var(--pr-bg-inset)",
            border: "none",
            borderRadius: 6,
            padding: "0 10px",
            fontSize: 11.5,
            fontFamily: "inherit",
            color: "var(--pr-text)",
          }}
        />
        <div style={{ maxHeight: 320, overflowY: "auto" }}>
          {items.length === 0 ? (
            <div
              style={{
                fontSize: 11.5,
                color: "var(--pr-text-muted)",
                textAlign: "center",
                padding: "24px 0",
              }}
            >
              該当する論文がありません
            </div>
          ) : (
            items.map((item) => (
              <PaperPickerRow
                key={item.id}
                title={item.paper.title}
                meta={[item.paper.authors_short, item.paper.year ? String(item.paper.year) : null]
                  .filter(Boolean)
                  .join(" · ")}
                onClick={() => {
                  triggerDownload(`/api/library-items/${item.id}/export/markdown`);
                  onClose();
                }}
              />
            ))
          )}
        </div>
      </div>
    </Modal>
  );
}

function PaperPickerRow({
  title,
  meta,
  onClick,
}: {
  title: string;
  meta: string;
  onClick: () => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        width: "100%",
        textAlign: "left",
        padding: "9px 12px",
        borderBottom: "1px solid var(--pr-border-hair)",
        border: "none",
        borderTop: "none",
        borderLeft: "none",
        borderRight: "none",
        background: hover ? "var(--pr-bg-hover)" : "transparent",
        cursor: "pointer",
        fontFamily: "inherit",
      }}
    >
      <span
        style={{
          fontSize: 12,
          fontWeight: 600,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {title}
      </span>
      <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>{meta}</span>
    </button>
  );
}
