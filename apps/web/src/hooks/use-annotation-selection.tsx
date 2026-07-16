// apps/web/src/hooks/use-annotation-selection.tsx
"use client";

import { useCallback, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  annotationsCreate,
  vocabCreate,
  type Annotation,
  type AnnotationListResponse,
} from "@alinea/api-client";
import { useToast } from "@/components/ui/Toast";
import type { HighlightColor } from "@/components/ui/HighlightMark";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { useViewerStore } from "@/stores/viewer-store";
import { useViewerChatStore } from "@/stores/viewer-chat-store";
import { SelectionMenu } from "@/components/viewer/SelectionMenu";
import { extractVocabContext } from "@/components/viewer/vocab-context";
import { resolveSelectionAnchor } from "@/hooks/annotation-selection-resolve";

function tmpId(): string {
  return `tmp_${typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : Date.now()}`;
}

export function useAnnotationSelection({
  itemId,
  revisionId,
  defaultSide,
}: {
  itemId: string;
  revisionId: string;
  defaultSide: "source" | "translation";
}): { onPointerUp: () => void; selectionMenu: ReactNode } {
  const toast = useToast();
  const router = useRouter();
  const qc = useQueryClient();
  const isMobile = useIsMobile();
  const selection = useViewerStore((s) => s.selection);
  const setSelection = useViewerStore((s) => s.setSelection);
  const setPanel = useViewerStore((s) => s.setPanel);
  const addPendingAnchor = useViewerChatStore((s) => s.addPendingAnchor);
  const annotationsQueryKey = ["annotations", itemId];

  const onPointerUp = useCallback(() => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
      setSelection(null);
      return;
    }
    const text = sel.toString().trim();
    if (!text) {
      setSelection(null);
      return;
    }
    const resolved = resolveSelectionAnchor(sel.getRangeAt(0), text, defaultSide);
    setSelection(resolved);
  }, [setSelection, defaultSide]);

  const createHighlight = useCallback(
    (color: HighlightColor, comment: string | null) => {
      const sel = selection;
      if (!sel || !itemId) return;
      setSelection(null);
      const anchor = {
        revision_id: revisionId,
        block_id: sel.blockId,
        start: sel.start,
        end: sel.end,
        quote: sel.quote,
        side: sel.side,
      };
      const optimistic: Annotation = {
        id: tmpId(),
        kind: "highlight",
        color,
        anchor: { ...anchor, display: "" },
        comment,
        placed: true,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      const prev = qc.getQueryData<AnnotationListResponse>(annotationsQueryKey);
      qc.setQueryData<AnnotationListResponse>(annotationsQueryKey, (old) =>
        old ? { ...old, items: [...old.items, optimistic] } : old,
      );
      void annotationsCreate({
        path: { item_id: itemId },
        body: {
          kind: "highlight",
          color,
          anchor,
          comment: comment && comment.length > 0 ? comment : null,
        },
      }).then(
        () => {
          void qc.invalidateQueries({ queryKey: annotationsQueryKey });
          void qc.invalidateQueries({ queryKey: ["viewer", itemId] });
        },
        () => {
          if (prev) qc.setQueryData(annotationsQueryKey, prev);
          toast({
            kind: "error",
            message: "注釈を保存できませんでした",
            action: { label: "再試行", onClick: () => createHighlight(color, comment) },
          });
        },
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selection, itemId, revisionId, qc, toast, setSelection],
  );

  const addToVocab = useCallback(async () => {
    const sel = selection;
    if (!sel || sel.side !== "source" || sel.start == null || sel.end == null) return;
    setSelection(null);
    const { contextSentence, highlightStart, highlightEnd } = extractVocabContext(
      sel.sourceFullText ?? sel.quote,
      sel.start,
      sel.end,
    );
    try {
      const res = await vocabCreate({
        body: {
          library_item_id: itemId,
          term: sel.quote,
          anchor: {
            revision_id: revisionId,
            block_id: sel.blockId,
            start: sel.start,
            end: sel.end,
            quote: sel.quote,
            side: "source",
          },
          context_sentence: contextSentence,
          highlight: { start: highlightStart, end: highlightEnd },
        },
      });
      if (res.response.status === 409) {
        const existingId = (res.error as { existing?: { vocab_id?: string } } | undefined)?.existing
          ?.vocab_id;
        toast({ kind: "info", message: "すでに語彙帳にあります" });
        if (existingId) router.push(`/vocab/${existingId}`);
        return;
      }
      if (!res.data) throw new Error("vocab create failed");
      toast({ kind: "success", message: `「${sel.quote}」を語彙に追加しました` });
      router.push(`/vocab/${res.data.entry.id}`);
    } catch {
      toast({ kind: "error", message: "語彙に追加できませんでした" });
    }
  }, [selection, itemId, revisionId, router, toast, setSelection]);

  const copySelection = useCallback(
    (format: "citation" | "plain") => {
      const quote = selection?.quote ?? "";
      const text = format === "plain" ? quote : `"${quote}"`;
      void navigator.clipboard?.writeText(text).then(
        () => toast({ kind: "success", message: "コピーしました" }),
        () => toast({ kind: "error", message: "コピーできませんでした" }),
      );
      setSelection(null);
    },
    [selection, toast, setSelection],
  );

  // 「✦ AIに質問」: 選択文を引用チップとして積み、チャットタブへ(数式「この式を説明」と同流儀)。
  const askAI = useCallback(() => {
    const sel = selection;
    if (!sel) return;
    const quote = sel.quote;
    addPendingAnchor({
      anchor: {
        revision_id: revisionId,
        block_id: sel.blockId,
        start: sel.start,
        end: sel.end,
        quote,
        side: sel.side,
      },
      display: quote.length > 24 ? `${quote.slice(0, 24)}…` : quote,
    });
    setSelection(null);
    setPanel(true, "chat");
  }, [selection, revisionId, addPendingAnchor, setSelection, setPanel]);

  const selectionMenu: ReactNode =
    selection && !isMobile ? (
      <SelectionMenu
        milestone="M2"
        side={selection.side}
        position={{ top: selection.rect.bottom + 8, left: selection.rect.left }}
        onAskAI={askAI}
        onCopy={copySelection}
        onHighlight={(color) => createHighlight(color, null)}
        onComment={(color, comment) => createHighlight(color, comment.length > 0 ? comment : null)}
        onAddVocab={() => void addToVocab()}
      />
    ) : null;

  return { onPointerUp, selectionMenu };
}
