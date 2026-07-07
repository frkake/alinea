"use client";

import { useRef, useState } from "react";
import { Popover } from "@/components/ui/Popover";
import { PROVIDER_LABELS, type ByokProvider } from "@/components/settings/types";

/** BYOK 1 行 + キー編集ポップオーバー(4f §4.7.4-2)。 */
export interface ApiKeyRowProps {
  provider: ByokProvider;
  masked: string | null;
  createdAt: string | null;
  onSave: (apiKey: string) => void;
  onDelete: () => void;
  divider?: boolean;
}

/** ISO 日付 → "YYYY/M/D"。 */
function formatCreatedAt(iso: string | null): string | null {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return null;
  return `${m[1]}/${Number(m[2])}/${Number(m[3])}`;
}

const smallButton = {
  height: 24,
  padding: "0 10px",
  fontSize: 11,
  fontWeight: 600,
  border: "1px solid var(--pr-border-control)",
  borderRadius: 6,
  background: "var(--pr-bg-control)",
  cursor: "pointer",
  fontFamily: "inherit" as const,
};

export function ApiKeyRow({ provider, masked, createdAt, onSave, onDelete, divider = false }: ApiKeyRowProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");

  const isSet = masked != null;
  const created = formatCreatedAt(createdAt);
  const description = isSet
    ? `${masked}${created ? ` · 登録: ${created}` : ""}`
    : "未設定";

  const trimmed = draft.trim();

  const submit = () => {
    if (!trimmed) return;
    onSave(trimmed);
    setDraft("");
    setOpen(false);
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "12px 18px",
        borderBottom: divider ? "1px solid var(--pr-border-hair)" : undefined,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>{PROVIDER_LABELS[provider]}</span>
        <span
          style={{
            fontSize: 10.5,
            color: "var(--pr-text-muted)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {description}
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <button
          ref={triggerRef}
          type="button"
          style={smallButton}
          onClick={() => {
            setOpen((v) => !v);
          }}
        >
          {isSet ? "再設定" : "設定"}
        </button>
        {isSet ? (
          <button
            type="button"
            style={{ ...smallButton, color: "var(--pr-warn)" }}
            onClick={onDelete}
          >
            削除
          </button>
        ) : null}
      </div>

      <Popover
        open={open}
        onClose={() => {
          setOpen(false);
        }}
        anchorRef={triggerRef}
        width={300}
        placement="bottom-end"
        caret={false}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: 12 }}>
          <input
            type="password"
            value={draft}
            autoFocus
            placeholder="API キーを貼り付け"
            aria-label={`${PROVIDER_LABELS[provider]} の API キー`}
            onChange={(e) => {
              setDraft(e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submit();
              }
            }}
            style={{
              flex: 1,
              minWidth: 0,
              height: 28,
              padding: "0 10px",
              border: "1px solid var(--pr-border-control)",
              borderRadius: 6,
              fontSize: 11.5,
              fontFamily: "inherit",
              background: "var(--pr-bg-control)",
              color: "var(--pr-text)",
            }}
          />
          <button
            type="button"
            disabled={!trimmed}
            onClick={submit}
            style={{
              height: 28,
              padding: "0 12px",
              border: "none",
              borderRadius: 6,
              background: "var(--pr-acc)",
              color: "#FFFFFF",
              fontSize: 11.5,
              fontWeight: 600,
              cursor: trimmed ? "pointer" : "default",
              opacity: trimmed ? 1 : 0.5,
              fontFamily: "inherit",
            }}
          >
            保存
          </button>
        </div>
      </Popover>
    </div>
  );
}
