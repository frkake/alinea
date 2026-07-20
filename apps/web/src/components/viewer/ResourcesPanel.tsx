"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import {
  ResourceApiError,
  acceptResourceSuggestion,
  acceptResourceSuggestionById,
  createResource,
  deleteResource,
  dismissResourceSuggestion,
  dismissResourceSuggestionById,
  listResources,
  patchResource,
  refreshResourceMeta,
} from "@/lib/resources-api";
import { useViewerStore } from "@/stores/viewer-store";
import { ResourceAddFooter } from "./resources/ResourceAddFooter";
import { ResourceCard } from "./resources/ResourceCard";
import { ResourceSuggestionCard } from "./resources/ResourceSuggestionCard";
import type { ResKind, ResourceListResponse, ResourceSuggestion } from "./resources/types";

const SKELETON_COUNT = 3;
const FLASH_MS = 2000;
const UNDO_MS = 6000;
// 折り畳み時に既定で見せる候補数(残りは「他 N 件を表示」で展開)。設計 §8。
const SUGGESTIONS_COLLAPSED = 3;

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
  // 追加成功のたびに +1(ResourceAddFooter が入力欄をクリアするための合図。M2-17 followup)。
  const [addSeq, setAddSeq] = useState(0);
  const [flashId, setFlashId] = useState<string | null>(null);
  // 候補ごとの実行中フラグ(key = resource_id ?? url)。折り畳み展開状態。
  const [pendingSuggestions, setPendingSuggestions] = useState<Set<string>>(new Set());
  const [suggestionsExpanded, setSuggestionsExpanded] = useState(false);
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
        setAddSeq((n) => n + 1);
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

  const suggestionKey = (s: ResourceSuggestion): string => s.resource_id ?? s.url;

  const setSuggestionPending = (key: string, on: boolean) => {
    setPendingSuggestions((prev) => {
      const next = new Set(prev);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  };

  // resource_id を持つ候補(Hugging Face 等の永続候補)は ID 指定で、持たない候補(arXiv 動的)は
  // item スコープの従来 API で採用・却下する(設計 §3)。
  const onAcceptSuggestion = (s: ResourceSuggestion) => {
    if (!itemId) return;
    const key = suggestionKey(s);
    setSuggestionPending(key, true);
    const p = s.resource_id
      ? acceptResourceSuggestionById(s.resource_id)
      : acceptResourceSuggestion(itemId);
    p.then(
      () => {
        setSuggestionPending(key, false);
        invalidate();
      },
      () => {
        setSuggestionPending(key, false);
        toast({ kind: "error", message: "追加できませんでした" });
      },
    );
  };

  const onDismissSuggestion = (s: ResourceSuggestion) => {
    if (!itemId) return;
    const key = suggestionKey(s);
    setSuggestionPending(key, true);
    const p = s.resource_id
      ? dismissResourceSuggestionById(s.resource_id)
      : dismissResourceSuggestion(itemId);
    p.then(
      () => {
        setSuggestionPending(key, false);
        invalidate();
      },
      () => {
        setSuggestionPending(key, false);
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
        <ResourceAddFooter onAdd={onAdd} pending errorMessage={null} clearSignal={addSeq} />
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
        <ResourceAddFooter onAdd={onAdd} pending={addPending} errorMessage={addError} clearSignal={addSeq} />
      </div>
    );
  }

  const data = query.data;
  const items = data?.items ?? [];
  // suggestions(複数)が正典。互換のため単数 suggestion しか無い応答も先頭候補として拾う。
  const suggestions: ResourceSuggestion[] =
    data?.suggestions && data.suggestions.length > 0
      ? data.suggestions
      : data?.suggestion
        ? [data.suggestion]
        : [];
  const isEmpty = items.length === 0 && suggestions.length === 0;
  const visibleSuggestions = suggestionsExpanded
    ? suggestions
    : suggestions.slice(0, SUGGESTIONS_COLLAPSED);
  const hiddenCount = suggestions.length - visibleSuggestions.length;

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
            {visibleSuggestions.map((s) => (
              <ResourceSuggestionCard
                key={s.resource_id ?? s.url}
                suggestion={s}
                onAccept={() => onAcceptSuggestion(s)}
                onDismiss={() => onDismissSuggestion(s)}
                pending={pendingSuggestions.has(s.resource_id ?? s.url)}
              />
            ))}
            {hiddenCount > 0 ? (
              <button
                type="button"
                onClick={() => setSuggestionsExpanded(true)}
                style={{
                  alignSelf: "flex-start",
                  border: "none",
                  background: "transparent",
                  color: "var(--pr-acc)",
                  fontSize: 11,
                  fontWeight: 600,
                  fontFamily: "inherit",
                  cursor: "pointer",
                  padding: "2px 0",
                }}
              >
                {`他 ${hiddenCount} 件の候補を表示`}
              </button>
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
      <ResourceAddFooter onAdd={onAdd} pending={addPending} errorMessage={addError} clearSignal={addSeq} />
    </div>
  );
}
