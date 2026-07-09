"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { collectionsList, tagsList } from "@alinea/api-client";
import { STATUS_COLORS, STATUS_LABELS, type ReadingStatus } from "@alinea/tokens";

const STATUS_ORDER: readonly ReadingStatus[] = [
  "planned",
  "up_next",
  "reading",
  "done",
  "reread",
  "on_hold",
];

export interface BulkActionBarProps {
  selectedCount: number;
  busy?: boolean;
  onClearSelection: () => void;
  onSetStatus: (status: ReadingStatus) => void;
  onAddTags: (tags: string[]) => void;
  onAddToCollection: (collectionId: string) => void;
}

type OpenMenu = "status" | "tags" | "collection" | null;

/**
 * フローティング一括操作バー(1e §4.8・§5.5、docs/06 §8.5)。
 * 選択 ≥1 件でテーブル下部中央に出現。3 アクション用のローカルメニューは
 * `Popover` を使わず本コンポーネント内で自前配置する(1e §5.4 の決定:
 * 共通 Popover は bottom 系配置のみのため、トリガー直上に開く本用途には使えない)。
 */
export function BulkActionBar({
  selectedCount,
  busy = false,
  onClearSelection,
  onSetStatus,
  onAddTags,
  onAddToCollection,
}: BulkActionBarProps) {
  const [openMenu, setOpenMenu] = useState<OpenMenu>(null);
  const barRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (openMenu === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpenMenu(null);
    };
    const onDown = (e: MouseEvent) => {
      if (barRef.current?.contains(e.target as Node)) return;
      setOpenMenu(null);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [openMenu]);

  if (selectedCount <= 0) return null;

  return (
    <div
      ref={barRef}
      role="toolbar"
      aria-label="一括操作"
      style={{
        position: "fixed",
        bottom: 22,
        left: "50%",
        transform: "translateX(-50%)",
        display: "flex",
        alignItems: "center",
        gap: 14,
        background: "var(--pr-elev-bg)",
        color: "var(--pr-elev-fg)",
        borderRadius: 10,
        padding: "10px 18px",
        boxShadow: "var(--pr-shadow-bar)",
        zIndex: 5,
        opacity: busy ? 0.5 : 1,
        pointerEvents: busy ? "none" : undefined,
      }}
    >
      <span style={{ fontSize: 12, fontWeight: 600 }}>{selectedCount} 件を選択中</span>
      <span style={{ width: 1, height: 16, background: "var(--pr-elev-divider)" }} />

      <BarTrigger
        label="ステータス変更"
        arrow
        open={openMenu === "status"}
        onClick={() => setOpenMenu((m) => (m === "status" ? null : "status"))}
      >
        {openMenu === "status" ? (
          <StatusMenu
            onSelect={(s) => {
              setOpenMenu(null);
              onSetStatus(s);
            }}
          />
        ) : null}
      </BarTrigger>

      <BarTrigger
        label="タグ追加"
        open={openMenu === "tags"}
        onClick={() => setOpenMenu((m) => (m === "tags" ? null : "tags"))}
      >
        {openMenu === "tags" ? (
          <TagPopover
            onSubmit={(tags) => {
              setOpenMenu(null);
              onAddTags(tags);
            }}
          />
        ) : null}
      </BarTrigger>

      <BarTrigger
        label="コレクションへ"
        open={openMenu === "collection"}
        onClick={() => setOpenMenu((m) => (m === "collection" ? null : "collection"))}
      >
        {openMenu === "collection" ? (
          <CollectionPopover
            onSelect={(id) => {
              setOpenMenu(null);
              onAddToCollection(id);
            }}
          />
        ) : null}
      </BarTrigger>

      <button
        type="button"
        onClick={onClearSelection}
        style={{
          border: "none",
          background: "transparent",
          color: "var(--pr-elev-fg-muted)",
          fontSize: 12,
          cursor: "pointer",
          fontFamily: "inherit",
        }}
      >
        選択解除 ×
      </button>
    </div>
  );
}

function BarTrigger({
  label,
  arrow = false,
  open,
  onClick,
  children,
}: {
  label: string;
  arrow?: boolean;
  open: boolean;
  onClick: () => void;
  children?: ReactNode;
}) {
  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={onClick}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          border: "none",
          background: "transparent",
          color: "var(--pr-elev-fg)",
          fontSize: 12,
          cursor: "pointer",
          fontFamily: "inherit",
          padding: 0,
        }}
      >
        {label}
        {arrow ? <span style={{ fontSize: 9, color: "var(--pr-elev-fg-muted)" }}>▾</span> : null}
      </button>
      {children ? (
        <div
          style={{
            position: "absolute",
            bottom: "calc(100% + 8px)",
            left: 0,
            width: 180,
            background: "var(--pr-bg-pop)",
            border: "1px solid var(--pr-border-pop)",
            borderRadius: 10,
            boxShadow: "var(--pr-shadow-pop)",
            color: "var(--pr-text)",
            overflow: "hidden",
          }}
        >
          {children}
        </div>
      ) : null}
    </span>
  );
}

function StatusMenu({ onSelect }: { onSelect: (status: ReadingStatus) => void }) {
  return (
    <div role="menu" aria-label="ステータス変更" style={{ padding: 4 }}>
      {STATUS_ORDER.map((s) => (
        <button
          key={s}
          type="button"
          role="menuitem"
          onClick={() => onSelect(s)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            width: "100%",
            height: 30,
            padding: "0 12px",
            border: "none",
            borderRadius: 6,
            background: "transparent",
            color: "var(--pr-text-mid)",
            fontSize: 11.5,
            cursor: "pointer",
            fontFamily: "inherit",
            textAlign: "left",
          }}
        >
          <span
            style={{ width: 7, height: 7, borderRadius: "50%", background: STATUS_COLORS[s], flex: "none" }}
          />
          {STATUS_LABELS[s]}
        </button>
      ))}
    </div>
  );
}

function TagPopover({ onSubmit }: { onSubmit: (tags: string[]) => void }) {
  const [input, setInput] = useState("");
  const [debounced, setDebounced] = useState("");
  const [chips, setChips] = useState<string[]>([]);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(input.trim()), 200);
    return () => clearTimeout(t);
  }, [input]);

  const suggestQuery = useQuery({
    queryKey: ["tags", "suggest", debounced],
    queryFn: async () => (await tagsList({ query: { q: debounced, limit: 8 }, throwOnError: true })).data,
    enabled: debounced.length > 0,
  });

  const addChip = (raw: string) => {
    const v = raw.trim();
    if (!v || chips.includes(v)) return;
    setChips((prev) => [...prev, v]);
    setInput("");
    setDebounced("");
  };

  return (
    <div style={{ padding: 10, width: 240 }}>
      {chips.length > 0 ? (
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
          {chips.map((c) => (
            <span
              key={c}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                height: 18,
                padding: "0 6px",
                borderRadius: 3,
                background: "var(--pr-bg-inset)",
                color: "var(--pr-text-sub)",
                fontSize: 10.5,
              }}
            >
              {c}
              <span
                role="button"
                aria-label={`${c} を削除`}
                tabIndex={0}
                onClick={() => setChips((prev) => prev.filter((t) => t !== c))}
                style={{ cursor: "pointer" }}
              >
                ×
              </span>
            </span>
          ))}
        </div>
      ) : null}
      <input
        aria-label="タグを追加"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            addChip(input);
          }
        }}
        placeholder="タグ名"
        style={{
          width: "100%",
          height: 28,
          padding: "0 10px",
          border: "1px solid var(--pr-border-control)",
          borderRadius: 6,
          fontSize: 12,
          fontFamily: "inherit",
          marginBottom: 6,
        }}
      />
      {debounced && (suggestQuery.data?.items.length ?? 0) > 0 ? (
        <div role="listbox" aria-label="タグ候補" style={{ marginBottom: 6 }}>
          {suggestQuery.data?.items.map((t) => (
            <button
              key={t.tag}
              type="button"
              role="option"
              onClick={() => addChip(t.tag)}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "4px 6px",
                border: "none",
                background: "transparent",
                fontSize: 11,
                cursor: "pointer",
                fontFamily: "inherit",
                color: "var(--pr-text-mid)",
              }}
            >
              {t.tag}
            </button>
          ))}
        </div>
      ) : null}
      <button
        type="button"
        disabled={chips.length === 0}
        onClick={() => onSubmit(chips)}
        style={{
          width: "100%",
          height: 26,
          border: "none",
          borderRadius: 6,
          background: "var(--pr-acc)",
          color: "#FFFFFF",
          fontSize: 11,
          fontWeight: 600,
          cursor: chips.length === 0 ? "default" : "pointer",
          opacity: chips.length === 0 ? 0.5 : 1,
          fontFamily: "inherit",
        }}
      >
        追加
      </button>
    </div>
  );
}

function CollectionPopover({ onSelect }: { onSelect: (collectionId: string) => void }) {
  const query = useQuery({
    queryKey: ["collections"],
    queryFn: async () => (await collectionsList({ throwOnError: true })).data,
  });
  const items = query.data?.items ?? [];

  return (
    <div role="menu" aria-label="コレクションへ追加" style={{ width: 240, padding: 4 }}>
      {items.length === 0 ? (
        <div style={{ padding: "8px 10px", fontSize: 11.5, color: "var(--pr-text-muted)" }}>
          コレクションがありません
        </div>
      ) : (
        items.map((c) => (
          <button
            key={c.id}
            type="button"
            role="menuitem"
            onClick={() => onSelect(c.id)}
            style={{
              display: "flex",
              alignItems: "center",
              width: "100%",
              height: 30,
              padding: "0 12px",
              border: "none",
              borderRadius: 6,
              background: "transparent",
              color: "var(--pr-text-mid)",
              fontSize: 11.5,
              cursor: "pointer",
              fontFamily: "inherit",
              textAlign: "left",
              gap: 6,
            }}
          >
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>{c.name}</span>
            <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>{c.item_count}</span>
          </button>
        ))
      )}
    </div>
  );
}
