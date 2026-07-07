"use client";

import { useRef, useState, type ReactNode } from "react";
import type { FacetsResponse } from "@yakudoku/api-client";
import { STATUS_COLORS, STATUS_LABELS, type ReadingStatus } from "@yakudoku/tokens";
import { FilterChip } from "@/components/ui/FilterChip";
import { Popover } from "@/components/ui/Popover";

/** 属性フィルタの現在値(plans/03 §5.1・§5.14 の語彙、単数/複数は API と同一)。 */
export interface AppliedAttributeFilters {
  status: ReadingStatus[];
  tags: string[];
  collectionId: string | null;
  quality: "A" | "B" | null;
  years: number[];
}

export function emptyAttributeFilters(): AppliedAttributeFilters {
  return { status: [], tags: [], collectionId: null, quality: null, years: [] };
}

export function hasAppliedAttributeFilters(v: AppliedAttributeFilters): boolean {
  return (
    v.status.length > 0 ||
    v.tags.length > 0 ||
    v.collectionId !== null ||
    v.quality !== null ||
    v.years.length > 0
  );
}

export interface AttributeFilterBarProps {
  /** undefined = 読み込み中(選択肢・件数は出さず、既定ラベルのみ表示)。 */
  facets: FacetsResponse | undefined;
  value: AppliedAttributeFilters;
  onChange: (next: AppliedAttributeFilters) => void;
}

interface Option {
  value: string;
  label: string;
  count: number;
  dotColor?: string;
}

const STATUS_ORDER: readonly ReadingStatus[] = [
  "planned",
  "up_next",
  "reading",
  "done",
  "reread",
  "on_hold",
];

/**
 * 属性フィルタドロップダウン 5 種(1e §4.6・plans/03 §5.1・§5.2)。
 * 同一属性内は複数選択= OR(ステータス/タグ/年)、単一選択= AND 相当(コレクション/品質)。
 * 未適用はドロップダウントリガー、適用中は `FilterChip removable` に置き換わる(§4.6 の決定)。
 */
export function AttributeFilterBar({ facets, value, onChange }: AttributeFilterBarProps) {
  const statusOptions: Option[] = STATUS_ORDER.map((s) => ({
    value: s,
    label: STATUS_LABELS[s],
    count: facets?.status?.[s] ?? 0,
    dotColor: STATUS_COLORS[s],
  }));
  const tagOptions: Option[] = (facets?.tags ?? []).map((t) => ({
    value: t.tag,
    label: t.tag,
    count: t.count,
  }));
  const collectionOptions: Option[] = (facets?.collections ?? []).map((c) => ({
    value: c.id,
    label: c.name,
    count: c.count,
  }));
  const qualityOptions: Option[] = [
    { value: "A", label: "A", count: facets?.quality?.A ?? 0 },
    { value: "B", label: "B", count: facets?.quality?.B ?? 0 },
  ];
  const yearOptions: Option[] = (facets?.years ?? []).map((y) => ({
    value: String(y.year),
    label: String(y.year),
    count: y.count,
  }));

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      <AttributeDropdown
        label="ステータス"
        mode="multi"
        options={statusOptions}
        selected={value.status}
        // next の値は STATUS_ORDER(ReadingStatus)由来のオプション値のみ(実行時に保証)。
        onApply={(next) => onChange({ ...value, status: next as ReadingStatus[] })}
      />
      <AttributeDropdown
        label="タグ"
        mode="multi"
        options={tagOptions}
        selected={value.tags}
        onApply={(next) => onChange({ ...value, tags: next })}
      />
      <AttributeDropdown
        label="コレクション"
        mode="single"
        options={collectionOptions}
        selected={value.collectionId ? [value.collectionId] : []}
        onApply={(next) => onChange({ ...value, collectionId: next[0] ?? null })}
      />
      <AttributeDropdown
        label="品質"
        mode="single"
        options={qualityOptions}
        selected={value.quality ? [value.quality] : []}
        onApply={(next) =>
          onChange({ ...value, quality: (next[0] as "A" | "B" | undefined) ?? null })
        }
      />
      <AttributeDropdown
        label="年"
        mode="multi"
        options={yearOptions}
        selected={value.years.map(String)}
        onApply={(next) => onChange({ ...value, years: next.map(Number) })}
      />
    </div>
  );
}

interface AttributeDropdownProps {
  label: string;
  mode: "multi" | "single";
  options: Option[];
  selected: string[];
  onApply: (values: string[]) => void;
}

function AttributeDropdown({ label, mode, options, selected, onApply }: AttributeDropdownProps) {
  const anchorRef = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);

  const toggleValue = (v: string) => {
    if (mode === "single") {
      onApply(selected.includes(v) ? [] : [v]);
      setOpen(false);
      return;
    }
    const next = selected.includes(v) ? selected.filter((s) => s !== v) : [...selected, v];
    onApply(next);
  };

  const chipLabel = (): string | null => {
    if (selected.length === 0) return null;
    const first = options.find((o) => o.value === selected[0])?.label ?? selected[0];
    return selected.length === 1 ? `${label}: ${first}` : `${label}: ${first} +${selected.length - 1}`;
  };
  const applied = chipLabel();

  return (
    <span ref={anchorRef} style={{ position: "relative", display: "inline-flex" }}>
      {applied ? (
        <FilterChip
          label={applied}
          removable
          onClick={() => setOpen((v) => !v)}
          onRemove={() => onApply([])}
        />
      ) : (
        <button
          type="button"
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            height: 22,
            padding: "0 10px",
            borderRadius: 6,
            border: "1px solid var(--pr-border-control)",
            color: "var(--pr-text-mid)",
            background: "var(--pr-bg-control)",
            fontSize: 11,
            cursor: "pointer",
            fontFamily: "inherit",
          }}
        >
          {`${label} `}
          <span style={{ fontSize: 8.5, color: "var(--pr-text-muted)" }}>▾</span>
        </button>
      )}

      <Popover
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={anchorRef}
        width={220}
        placement="bottom-start"
        caret={false}
      >
        <div role="menu" aria-label={label} style={{ padding: "6px 4px", maxHeight: 280, overflowY: "auto" }}>
          {options.length === 0 ? (
            <div style={{ padding: "8px 12px", fontSize: 11.5, color: "var(--pr-text-muted)" }}>
              選択肢がありません
            </div>
          ) : (
            options.map((opt) => {
              const checked = selected.includes(opt.value);
              return (
                <button
                  key={opt.value}
                  type="button"
                  role={mode === "multi" ? "menuitemcheckbox" : "menuitemradio"}
                  aria-checked={checked}
                  onClick={() => toggleValue(opt.value)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    width: "100%",
                    height: 28,
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
                  <Indicator mode={mode} checked={checked} dotColor={opt.dotColor} />
                  <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {opt.label}
                  </span>
                  <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>{opt.count}</span>
                </button>
              );
            })
          )}
        </div>
      </Popover>
    </span>
  );
}

function Indicator({
  mode,
  checked,
  dotColor,
}: {
  mode: "multi" | "single";
  checked: boolean;
  dotColor?: string;
}): ReactNode {
  if (mode === "single") {
    return (
      <span
        aria-hidden="true"
        style={{
          width: 7,
          height: 7,
          borderRadius: "50%",
          flex: "none",
          background: dotColor ?? (checked ? "var(--pr-acc)" : "var(--pr-border-check)"),
        }}
      />
    );
  }
  return (
    <span
      aria-hidden="true"
      style={{
        width: 14,
        height: 14,
        flex: "none",
        borderRadius: 3,
        border: checked ? "1.5px solid var(--pr-acc)" : "1.5px solid var(--pr-border-check)",
        background: checked ? "var(--pr-acc)" : "transparent",
        color: "#FFFFFF",
        fontSize: 9,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {checked ? "✓" : null}
    </span>
  );
}
