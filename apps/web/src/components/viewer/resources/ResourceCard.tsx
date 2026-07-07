"use client";

import { useRef, useState } from "react";
import { EvidenceChip } from "@/components/ui/EvidenceChip";
import { Popover } from "@/components/ui/Popover";
import { metaLine, parseNoteSegments } from "./format";
import { ResourceKindIcon } from "./ResourceKindIcon";
import type { ResKind, ResourceLink } from "./types";
import { YouTubeThumbnail } from "./YouTubeThumbnail";

export interface ResourceCardProps {
  resource: ResourceLink;
  /** 重複追加時の既存カードハイライト(2,000ms で親が false に戻す)。 */
  flash: boolean;
  onJumpSection: (sectionId: string) => void;
  onEdit: (patch: { title?: string; kind?: ResKind; note?: string | null }) => void;
  onRefreshMeta: () => void;
  onDelete: () => void;
}

const KIND_LABELS: Record<ResKind, string> = {
  github: "GitHub 実装",
  youtube: "YouTube 動画",
  slides: "スライド(PDF)",
  article: "解説記事",
};

/** 確定リソースカード(kind 4 種共通。plans/09-screens/5a §4.5-b〜e)。 */
export function ResourceCard({
  resource,
  flash,
  onJumpSection,
  onEdit,
  onRefreshMeta,
  onDelete,
}: ResourceCardProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [kindSubmenu, setKindSubmenu] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(resource.title);
  const [editingNote, setEditingNote] = useState(false);
  const [noteDraft, setNoteDraft] = useState(resource.note ?? "");
  const menuButtonRef = useRef<HTMLButtonElement>(null);

  const compact = (resource.kind === "slides" || resource.kind === "article") && !resource.note;
  const meta = metaLine(resource);

  const saveTitle = () => {
    const trimmed = titleDraft.trim();
    setEditingTitle(false);
    if (trimmed && trimmed !== resource.title) onEdit({ title: trimmed });
    else setTitleDraft(resource.title);
  };

  const saveNote = () => {
    setEditingNote(false);
    const trimmed = noteDraft.trim();
    onEdit({ note: trimmed ? noteDraft : null });
  };

  return (
    <div
      data-resource-id={resource.id}
      style={{
        background: "var(--pr-bg-card)",
        border: "1px solid var(--pr-border-card)",
        borderRadius: 8,
        padding: "10px 12px",
        display: "flex",
        flexDirection: compact ? "row" : "column",
        gap: compact ? 9 : 7,
        alignItems: compact ? "flex-start" : undefined,
        outline: flash ? "1.5px solid var(--pr-acc)" : undefined,
        outlineOffset: flash ? 1 : undefined,
      }}
    >
      <div style={{ display: "flex", gap: 9, alignItems: "flex-start", flex: compact ? 1 : undefined }}>
        <ResourceKindIcon kind={resource.kind} sourceLabel={resource.source_label} />
        <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0, flex: 1 }}>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            {editingTitle ? (
              <input
                autoFocus
                aria-label="タイトルを編集"
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveTitle();
                  if (e.key === "Escape") {
                    setTitleDraft(resource.title);
                    setEditingTitle(false);
                  }
                }}
                onBlur={saveTitle}
                style={{
                  fontSize: 12,
                  fontWeight: 600,
                  border: "none",
                  borderBottom: "1px solid var(--pr-acc-m)",
                  background: "transparent",
                  fontFamily: "inherit",
                  minWidth: 0,
                  flex: 1,
                }}
              />
            ) : (
              <span
                style={{
                  fontSize: 12,
                  fontWeight: 600,
                  fontFamily: resource.kind === "github" ? "'IBM Plex Mono', monospace" : "inherit",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {resource.title}
              </span>
            )}
            {resource.official ? (
              <span
                style={{
                  height: 15,
                  padding: "0 5px",
                  borderRadius: 3,
                  background: "var(--pr-official-bg, rgba(101,148,113,0.16))",
                  color: "var(--pr-official-fg, #4C7458)",
                  fontSize: 8.5,
                  fontWeight: 700,
                  flex: "none",
                  display: "inline-flex",
                  alignItems: "center",
                }}
              >
                公式実装
              </span>
            ) : null}
          </div>
          <span style={{ fontSize: 10, color: "var(--pr-text-muted)" }}>{meta}</span>
        </div>
        <a
          href={resource.url}
          target="_blank"
          rel="noopener noreferrer"
          style={{ fontSize: 11, color: "var(--pr-acc)", fontWeight: 600, flex: "none" }}
        >
          開く ↗
        </a>
        <button
          ref={menuButtonRef}
          type="button"
          aria-label="リソースの操作"
          onClick={() => setMenuOpen((v) => !v)}
          style={{
            marginLeft: 6,
            width: 16,
            fontSize: 13,
            color: "var(--pr-text-muted)",
            letterSpacing: 1,
            border: "none",
            background: "transparent",
            cursor: "pointer",
            flex: "none",
          }}
        >
          ⋯
        </button>
      </div>

      {resource.kind === "youtube" ? (
        <YouTubeThumbnail
          thumbnailUrl={resource.thumbnail_url}
          durationSeconds={(resource.meta as { duration_seconds?: number | null }).duration_seconds ?? null}
          url={resource.url}
        />
      ) : null}

      {editingNote ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <textarea
            autoFocus
            aria-label="ひとことメモ"
            value={noteDraft}
            onChange={(e) => setNoteDraft(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") saveNote();
              if (e.key === "Escape") {
                setNoteDraft(resource.note ?? "");
                setEditingNote(false);
              }
            }}
            rows={2}
            style={{
              minHeight: 40,
              fontSize: 11,
              lineHeight: 1.65,
              padding: "6px 9px",
              border: "1px solid var(--pr-acc-m)",
              borderRadius: 5,
              background: "var(--pr-bg-comment, #F7F5EF)",
              fontFamily: "inherit",
              resize: "vertical",
            }}
          />
          <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
            <button type="button" onClick={saveNote} style={saveBtnStyle}>
              保存
            </button>
            <button
              type="button"
              onClick={() => {
                setNoteDraft(resource.note ?? "");
                setEditingNote(false);
              }}
              style={cancelBtnStyle}
            >
              キャンセル
            </button>
          </div>
        </div>
      ) : resource.note ? (
        // note: チップ(内部 <button>)とのクリック領域競合を避けるため、非クリック要素
        // (<div role="button">)+チップ以外の領域クリックで編集開始にする(§5.7)。
        <div
          role="button"
          tabIndex={0}
          onClick={(e) => {
            if ((e.target as HTMLElement).closest("button")) return;
            setEditingNote(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") setEditingNote(true);
          }}
          style={{
            textAlign: "left",
            fontSize: 11,
            lineHeight: 1.65,
            color: "var(--pr-text-mid)",
            background: "var(--pr-bg-comment, #F7F5EF)",
            borderRadius: 5,
            padding: "6px 9px",
            cursor: "text",
          }}
        >
          {"💬 "}
          {parseNoteSegments(resource.note).map((seg, i) =>
            seg.type === "chip" ? (
              <EvidenceChip
                key={i}
                anchor={{ type: "section", sectionNumber: seg.text }}
                label={seg.text}
                onJump={() => {
                  if (seg.sectionId) onJumpSection(seg.sectionId);
                }}
              />
            ) : (
              <span key={i}>{seg.text}</span>
            ),
          )}
        </div>
      ) : null}

      <Popover
        open={menuOpen}
        onClose={() => {
          setMenuOpen(false);
          setKindSubmenu(false);
        }}
        anchorRef={menuButtonRef}
        width={180}
        placement="bottom-end"
        caret={false}
      >
        {kindSubmenu ? (
          <div role="menu">
            {(Object.keys(KIND_LABELS) as ResKind[]).map((k) => (
              <button
                key={k}
                type="button"
                role="menuitem"
                onClick={() => {
                  onEdit({ kind: k });
                  setKindSubmenu(false);
                  setMenuOpen(false);
                }}
                style={{
                  ...menuItemStyle,
                  background: k === resource.kind ? "var(--pr-acc-s)" : "transparent",
                  fontWeight: k === resource.kind ? 600 : 400,
                }}
              >
                {KIND_LABELS[k]}
              </button>
            ))}
          </div>
        ) : (
          <div role="menu">
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                setEditingNote(true);
              }}
              style={menuItemStyle}
            >
              {resource.note ? "メモを編集" : "メモを追加"}
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                setEditingTitle(true);
              }}
              style={menuItemStyle}
            >
              タイトルを編集
            </button>
            <button type="button" role="menuitem" onClick={() => setKindSubmenu(true)} style={menuItemStyle}>
              種類を変更
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                onRefreshMeta();
              }}
              style={menuItemStyle}
            >
              メタを再取得
            </button>
            <div style={{ height: 1, background: "var(--pr-border-hair, #ECE9DF)", margin: "4px 0" }} />
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                onDelete();
              }}
              style={{ ...menuItemStyle, color: "var(--pr-warn, #A05A42)" }}
            >
              削除
            </button>
          </div>
        )}
      </Popover>
    </div>
  );
}

const menuItemStyle = {
  display: "block",
  width: "100%",
  textAlign: "left" as const,
  height: 30,
  padding: "0 12px",
  fontSize: 11.5,
  color: "var(--pr-text-mid)",
  border: "none",
  background: "transparent",
  cursor: "pointer",
  fontFamily: "inherit",
};

const saveBtnStyle = {
  height: 20,
  padding: "0 9px",
  border: "none",
  borderRadius: 4,
  background: "var(--pr-acc)",
  color: "#FFFFFF",
  fontSize: 10,
  fontWeight: 600,
  fontFamily: "inherit",
  cursor: "pointer",
};

const cancelBtnStyle = {
  height: 20,
  padding: "0 6px",
  border: "none",
  background: "transparent",
  color: "var(--pr-text-muted)",
  fontSize: 10,
  fontFamily: "inherit",
  cursor: "pointer",
};
