"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  translationsSelectSections,
  type Problem,
  type SectionSelectionState,
  type TocNode,
} from "@alinea/api-client";
import { Modal } from "@/components/ui/Modal";

interface LongPaperSectionSelectionProps {
  itemId: string;
  setId: string;
  pageCount: number | null;
  toc: TocNode[];
  selection: SectionSelectionState;
}

interface SelectionNode {
  node: TocNode;
  children: SelectionNode[];
  selectableIds: string[];
}

function selectionTree(nodes: TocNode[], allowed: Set<string>): SelectionNode[] {
  const output: SelectionNode[] = [];
  for (const node of nodes) {
    const children = selectionTree(node.children ?? [], allowed);
    const selectableIds = [
      ...(allowed.has(node.section_id) ? [node.section_id] : []),
      ...children.flatMap((child) => child.selectableIds),
    ];
    if (selectableIds.length > 0) output.push({ node, children, selectableIds });
  }
  return output;
}

function problemMessage(problem: unknown): string {
  if (problem != null && typeof problem === "object") {
    const value = problem as Partial<Problem>;
    if (typeof value.detail === "string" && value.detail) return value.detail;
    if (typeof value.title === "string" && value.title) return value.title;
  }
  return "セクション選択を保存できませんでした";
}

function SectionCheckbox({
  entry,
  selected,
  onToggle,
  depth,
}: {
  entry: SelectionNode;
  selected: Set<string>;
  onToggle: (ids: string[], checked: boolean) => void;
  depth: number;
}) {
  const checkboxRef = useRef<HTMLInputElement>(null);
  const selectedCount = entry.selectableIds.filter((id) => selected.has(id)).length;
  const checked = selectedCount === entry.selectableIds.length;
  const indeterminate = selectedCount > 0 && !checked;
  const label = `${entry.node.number ? `${entry.node.number} ` : ""}${entry.node.title_ja ?? entry.node.title_en}`;

  useEffect(() => {
    if (checkboxRef.current) checkboxRef.current.indeterminate = indeterminate;
  }, [indeterminate]);

  return (
    <div>
      <label
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: 9,
          padding: `7px 8px 7px ${8 + depth * 20}px`,
          borderRadius: 6,
          cursor: "pointer",
          color: "var(--pr-text-body)",
          minWidth: 0,
        }}
      >
        <input
          ref={checkboxRef}
          type="checkbox"
          aria-label={label}
          checked={checked}
          onChange={(event) => onToggle(entry.selectableIds, event.currentTarget.checked)}
          style={{ marginTop: 3, flex: "none", accentColor: "var(--pr-a)" }}
        />
        <span
          style={{
            minWidth: 0,
            overflowWrap: "anywhere",
            wordBreak: "break-word",
            lineHeight: 1.55,
            fontSize: 13,
          }}
        >
          {label}
        </span>
      </label>
      {entry.children.map((child) => (
        <SectionCheckbox
          key={child.node.section_id}
          entry={child}
          selected={selected}
          onToggle={onToggle}
          depth={depth + 1}
        />
      ))}
    </div>
  );
}

export function LongPaperSectionSelection({
  itemId,
  setId,
  pageCount,
  toc,
  selection,
}: LongPaperSectionSelectionProps) {
  const queryClient = useQueryClient();
  const selectableIds = selection.selectable_section_ids;
  const identity = `${setId}:${selectableIds.join("\u0000")}`;
  const initialIds =
    selection.selected_section_ids.length > 0 ? selection.selected_section_ids : selectableIds;
  const [state, setState] = useState(() => ({
    identity,
    open: selection.required,
    selected: new Set(initialIds),
    pending: false,
    error: null as string | null,
    accepted: false,
  }));
  const current =
    state.identity === identity
      ? state
      : {
          identity,
          open: selection.required,
          selected: new Set(initialIds),
          pending: false,
          error: null,
          accepted: false,
        };
  const allowed = useMemo(() => new Set(selectableIds), [selectableIds]);
  const tree = useMemo(() => selectionTree(toc, allowed), [allowed, toc]);
  const selectedIds = selectableIds.filter((id) => current.selected.has(id));
  const allSelected = selectedIds.length === selectableIds.length;

  if (!selection.required || current.accepted) return null;

  const update = (next: Partial<typeof current>) => {
    setState((previous) => ({
      ...(previous.identity === identity ? previous : current),
      ...next,
      identity,
    }));
  };

  const toggle = (ids: string[], checked: boolean) => {
    const next = new Set(current.selected);
    for (const id of ids) {
      if (checked) next.add(id);
      else next.delete(id);
    }
    update({ selected: next, error: null });
  };

  const submit = async () => {
    if (current.pending || selectedIds.length === 0) return;
    update({ pending: true, error: null });
    try {
      await translationsSelectSections({
        path: { set_id: setId },
        body: { section_ids: selectedIds },
        throwOnError: true,
      });
      update({ pending: false, open: false, accepted: true });
      await queryClient.invalidateQueries({ queryKey: ["viewer", itemId], exact: true });
    } catch (problem) {
      update({ pending: false, error: problemMessage(problem) });
    }
  };

  return (
    <>
      {!current.open ? (
        <button
          type="button"
          onClick={() => update({ open: true })}
          style={{
            position: "fixed",
            top: 58,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 20,
            maxWidth: "calc(100vw - 32px)",
            border: "1px solid var(--pr-a)",
            borderRadius: 999,
            padding: "8px 14px",
            background: "var(--pr-bg-card)",
            color: "var(--pr-acc)",
            boxShadow: "var(--pr-shadow-pop)",
            fontFamily: "var(--pr-font-ui)",
            fontSize: 12,
            fontWeight: 700,
            cursor: "pointer",
            overflowWrap: "anywhere",
          }}
        >
          翻訳するセクションを選択
        </button>
      ) : null}
      <Modal
        open={current.open}
        onClose={() => update({ open: false })}
        width={620}
        labelledBy="long-paper-selection-title"
      >
        <div style={{ padding: "20px 22px 12px" }}>
          <h2
            id="long-paper-selection-title"
            style={{ margin: 0, fontSize: 17, color: "var(--pr-text)", lineHeight: 1.4 }}
          >
            翻訳するセクションを選択
          </h2>
          <p
            style={{
              margin: "7px 0 0",
              color: "var(--pr-text-muted)",
              fontSize: 12.5,
              lineHeight: 1.7,
            }}
          >
            {pageCount != null ? `${pageCount}ページの論文です。` : "長い論文です。"}
            全文を選択したまま開始することも、読む範囲だけに絞ることもできます。
          </p>
        </div>
        <div
          style={{
            margin: "0 14px",
            padding: "5px 0",
            maxHeight: "min(52vh, 520px)",
            overflowY: "auto",
            overflowX: "hidden",
            border: "1px solid var(--pr-border-card)",
            borderRadius: 8,
            minWidth: 0,
          }}
        >
          {tree.map((entry) => (
            <SectionCheckbox
              key={entry.node.section_id}
              entry={entry}
              selected={current.selected}
              onToggle={toggle}
              depth={0}
            />
          ))}
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 10,
            padding: "10px 22px 0",
            flexWrap: "wrap",
          }}
        >
          <button
            type="button"
            onClick={() =>
              update({
                selected: allSelected ? new Set<string>() : new Set(selectableIds),
                error: null,
              })
            }
            disabled={current.pending}
            style={{
              border: "none",
              background: "transparent",
              color: "var(--pr-acc)",
              cursor: "pointer",
            }}
          >
            {allSelected ? "すべて解除" : "すべて選択"}
          </button>
          <span style={{ fontSize: 12, color: "var(--pr-text-muted)" }}>
            {selectedIds.length} / {selectableIds.length} セクション
          </span>
        </div>
        <div style={{ minHeight: 28, padding: "5px 22px 0" }}>
          {selectedIds.length === 0 ? (
            <span style={{ color: "var(--pr-red)", fontSize: 12 }}>
              1つ以上のセクションを選択してください
            </span>
          ) : current.error ? (
            <span role="alert" style={{ color: "var(--pr-red)", fontSize: 12 }}>
              {current.error}
            </span>
          ) : null}
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 9,
            padding: "8px 22px 20px",
            flexWrap: "wrap",
          }}
        >
          <button type="button" onClick={() => update({ open: false })} disabled={current.pending}>
            後で選ぶ
          </button>
          {current.error ? (
            <button type="button" onClick={() => void submit()} disabled={current.pending}>
              再試行
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => void submit()}
            disabled={current.pending || selectedIds.length === 0}
          >
            {current.pending
              ? "開始しています…"
              : allSelected
                ? "全文を翻訳"
                : "選択したセクションを翻訳"}
          </button>
        </div>
      </Modal>
    </>
  );
}
