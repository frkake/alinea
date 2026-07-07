"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import {
  ResourceApiError,
  acceptResourceSuggestion,
  createResource,
  deleteResource,
  dismissResourceSuggestion,
  listResources,
  patchResource,
  refreshResourceMeta,
} from "@/lib/resources-api";
import { useViewerStore } from "@/stores/viewer-store";
import { ResourceAddFooter } from "./resources/ResourceAddFooter";
import { ResourceCard } from "./resources/ResourceCard";
import { ResourceSuggestionCard } from "./resources/ResourceSuggestionCard";
import type { ResKind, ResourceListResponse } from "./resources/types";

const SKELETON_COUNT = 3;
const FLASH_MS = 2000;
const UNDO_MS = 6000;

/**
 * リソースタブ本体(docs/12・plans/09-screens/5a。viewer-shell §6.5: props なし)。
 *
 * 節サジェスト付きメモエディタ(§5.7 の SectionSuggestPopover)は本レーンでは簡略化し、
 * 生のチップ記法(`[[sec:id|label]]`)手入力に対応する形で実装する(deviations 参照)。
 */
export function ResourcesPanel() {
  const itemId = useViewerStore((s) => s.itemId);
  const requestScroll = useViewerStore((s) => s.requestScroll);
  const toast = useToast();
  const qc = useQueryClient();

  const [addPending, setAddPending] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [flashId, setFlashId] = useState<string | null>(null);
  const [suggestionPending, setSuggestionPending] = useState(false);
  const flashTimer = useRef<number | null>(null);
  const undoTimers = useRef<Map<string, number>>(new Map());
  const listRef = useRef<HTMLDivElement>(null);

  const queryKey = ["resources", itemId];
  const query = useQuery({
    queryKey,
    queryFn: () => listResources(itemId as string),
    enabled: Boolean(itemId),
    staleTime: 30_000,
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey });
    void qc.invalidateQueries({ queryKey: ["viewer", itemId] });
  };

  useEffect(
    () => () => {
      if (flashTimer.current) window.clearTimeout(flashTimer.current);
      undoTimers.current.forEach((t) => window.clearTimeout(t));
    },
    [],
  );

  if (!itemId) return null;

  const flashAndScroll = (resourceId: string) => {
    setFlashId(resourceId);
    if (flashTimer.current) window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlashId(null), FLASH_MS);
    const el = listRef.current?.querySelector<HTMLElement>(`[data-resource-id="${resourceId}"]`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const onAdd = (url: string) => {
    if (!itemId) return;
    setAddError(null);
    setAddPending(true);
    createResource(itemId, { url }).then(
      () => {
        setAddPending(false);
        invalidate();
        window.setTimeout(() => {
          listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
        }, 0);
      },
      (err: unknown) => {
        setAddPending(false);
        if (err instanceof ResourceApiError && err.status === 409) {
          const existingId = (err.body as { existing?: { resource_id?: string } } | null)?.existing
            ?.resource_id;
          if (existingId) flashAndScroll(existingId);
          toast({ kind: "info", message: "すでに追加されています" });
          return;
        }
        if (err instanceof ResourceApiError && err.status === 422) {
          setAddError("URL の形式が正しくありません");
          return;
        }
        toast({ kind: "error", message: "リソースを追加できませんでした" });
      },
    );
  };

  const onEdit = (
    resourceId: string,
    patch: { title?: string; kind?: ResKind; note?: string | null },
  ) => {
    qc.setQueryData<ResourceListResponse>(queryKey, (prev) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((it) => (it.id === resourceId ? { ...it, ...patch } : it)),
          }
        : prev,
    );
    patchResource(resourceId, patch).then(invalidate, () => {
      invalidate();
      toast({ kind: "error", message: "メモを保存できませんでした" });
    });
  };

  const onRefreshMeta = (resourceId: string) => {
    refreshResourceMeta(resourceId).then(invalidate, () => {
      toast({ kind: "error", message: "メタ情報を取得できませんでした" });
    });
  };

  const onDelete = (resourceId: string) => {
    qc.setQueryData<ResourceListResponse>(queryKey, (prev) =>
      prev
        ? { ...prev, items: prev.items.filter((it) => it.id !== resourceId), count: prev.count - 1 }
        : prev,
    );
    const restore = () => {
      const t = undoTimers.current.get(resourceId);
      if (t) window.clearTimeout(t);
      undoTimers.current.delete(resourceId);
      invalidate();
    };
    toast({ kind: "success", message: "リソースを削除しました", action: { label: "元に戻す", onClick: restore } });
    const timer = window.setTimeout(() => {
      undoTimers.current.delete(resourceId);
      deleteResource(resourceId).then(invalidate, () => {
        invalidate();
        toast({ kind: "error", message: "削除できませんでした" });
      });
    }, UNDO_MS);
    undoTimers.current.set(resourceId, timer);
  };

  const onAcceptSuggestion = () => {
    if (!itemId) return;
    setSuggestionPending(true);
    acceptResourceSuggestion(itemId).then(
      () => {
        setSuggestionPending(false);
        invalidate();
      },
      () => {
        setSuggestionPending(false);
        toast({ kind: "error", message: "追加できませんでした" });
      },
    );
  };

  const onDismissSuggestion = () => {
    if (!itemId) return;
    setSuggestionPending(true);
    dismissResourceSuggestion(itemId).then(
      () => {
        setSuggestionPending(false);
        invalidate();
      },
      () => {
        setSuggestionPending(false);
        toast({ kind: "error", message: "操作に失敗しました" });
      },
    );
  };

  if (query.isLoading) {
    return (
      <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
        <div style={{ flex: 1, padding: 12, display: "flex", flexDirection: "column", gap: 9 }}>
          {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
            <div
              key={i}
              style={{ height: 62, borderRadius: 8, background: "var(--pr-bg-muted)" }}
            />
          ))}
        </div>
        <ResourceAddFooter onAdd={onAdd} pending errorMessage={null} />
      </div>
    );
  }

  if (query.isError) {
    return (
      <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
        <div style={{ flex: 1 }}>
          <EmptyState
            title="リソースを読み込めませんでした"
            action={{ label: "再試行", onClick: () => void query.refetch() }}
          />
        </div>
        <ResourceAddFooter onAdd={onAdd} pending={addPending} errorMessage={addError} />
      </div>
    );
  }

  const data = query.data;
  const items = data?.items ?? [];
  const suggestion = data?.suggestion ?? null;
  const isEmpty = items.length === 0 && suggestion === null;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div
        ref={listRef}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          padding: 12,
          display: "flex",
          flexDirection: "column",
          gap: 9,
          background: "var(--pr-bg-feed, #FCFBF8)",
        }}
      >
        {isEmpty ? (
          <EmptyState
            title="リソースはまだありません"
            description="下の入力欄に URL を貼り付けると、GitHub 実装・動画・スライド・解説記事をこの論文にひも付けられます。"
          />
        ) : (
          <>
            {suggestion ? (
              <ResourceSuggestionCard
                suggestion={suggestion}
                onAccept={onAcceptSuggestion}
                onDismiss={onDismissSuggestion}
                pending={suggestionPending}
              />
            ) : null}
            {items.map((resource) => (
              <ResourceCard
                key={resource.id}
                resource={resource}
                flash={flashId === resource.id}
                onJumpSection={(sectionId) => requestScroll({ kind: "section", sectionId })}
                onEdit={(patch) => onEdit(resource.id, patch)}
                onRefreshMeta={() => onRefreshMeta(resource.id)}
                onDelete={() => onDelete(resource.id)}
              />
            ))}
          </>
        )}
      </div>
      <ResourceAddFooter onAdd={onAdd} pending={addPending} errorMessage={addError} />
    </div>
  );
}
