"use client";

import { useEffect } from "react";
import { useViewerStore } from "@/stores/viewer-store";
import type { ViewerMode } from "@/components/viewer/ViewerShell";

/** M0 の表示モード循環(訳文→対訳→原文→訳文)。 */
const M0_MODE_ORDER: readonly ViewerMode[] = ["translation", "parallel", "source"];

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable;
}

/**
 * ビューアのキーボードショートカット一元登録(viewer-shell §10。M0 サブセット)。
 * 入力要素フォーカス中・IME 変換中・修飾キー付きは無効(検索/選択の Esc は例外)。
 */
export function useViewerKeymap(params: {
  mode: ViewerMode;
  onModeChange: (mode: ViewerMode) => void;
  onFocusSearch: () => void;
}): void {
  const { mode, onModeChange, onFocusSearch } = params;
  const toggleBilingualPop = useViewerStore((s) => s.toggleBilingualPop);
  const setPanel = useViewerStore((s) => s.setPanel);
  const requestScroll = useViewerStore((s) => s.requestScroll);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      const store = useViewerStore.getState();

      // Esc は浮遊 UI を 1 つ閉じる(選択メニュー → 検索)。入力中でも有効。
      if (e.key === "Escape") {
        if (store.selection) {
          store.setSelection(null);
          return;
        }
        if (store.searchOpen) {
          store.closeSearch();
          return;
        }
        return;
      }

      if (isEditableTarget(e.target) || e.isComposing) return;

      switch (e.key) {
        case "/":
          e.preventDefault();
          onFocusSearch();
          break;
        case "t":
          if (mode === "translation") toggleBilingualPop();
          break;
        case "m": {
          const idx = M0_MODE_ORDER.indexOf(mode);
          const next = M0_MODE_ORDER[(idx + 1) % M0_MODE_ORDER.length];
          if (next) onModeChange(next);
          break;
        }
        case "c":
          setPanel(true, "chat");
          break;
        case "j":
          moveBlock(1, store.currentBlockId, requestScroll);
          break;
        case "k":
          moveBlock(-1, store.currentBlockId, requestScroll);
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [mode, onModeChange, onFocusSearch, toggleBilingualPop, setPanel, requestScroll]);
}

/** 次/前の段落ブロックへスクロール依頼(viewer-shell §10 の j/k)。 */
function moveBlock(
  delta: number,
  currentBlockId: string | null,
  requestScroll: (t: { kind: "block"; blockId: string }) => void,
): void {
  if (typeof document === "undefined") return;
  const els = Array.from(document.querySelectorAll<HTMLElement>("[data-block-id]"));
  if (els.length === 0) return;
  const ids = els.map((el) => el.dataset.blockId ?? "");
  const cur = currentBlockId ? ids.indexOf(currentBlockId) : 0;
  const nextIdx = Math.min(Math.max((cur < 0 ? 0 : cur) + delta, 0), ids.length - 1);
  const blockId = ids[nextIdx];
  if (blockId) requestScroll({ kind: "block", blockId });
}
