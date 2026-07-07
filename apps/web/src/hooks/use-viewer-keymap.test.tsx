import { renderHook } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { useViewerKeymap } from "@/hooks/use-viewer-keymap";
import { useViewerStore } from "@/stores/viewer-store";

// viewer-shell §10: キー `b` はブックマーク切替シグナルを発火する(実処理は 1b 側)。
describe("useViewerKeymap key `b` (viewer-shell §10 / 1b §5.4)", () => {
  beforeEach(() => {
    useViewerStore.setState({ bookmarkToggleSignal: 0, selection: null, searchOpen: false });
  });

  test("pressing b increments bookmarkToggleSignal", () => {
    renderHook(() =>
      useViewerKeymap({ mode: "translation", onModeChange: vi.fn(), onFocusSearch: vi.fn() }),
    );
    fireEvent.keyDown(window, { key: "b" });
    expect(useViewerStore.getState().bookmarkToggleSignal).toBe(1);
  });

  test("does nothing while an input is focused", () => {
    renderHook(() =>
      useViewerKeymap({ mode: "translation", onModeChange: vi.fn(), onFocusSearch: vi.fn() }),
    );
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    fireEvent.keyDown(input, { key: "b" });
    expect(useViewerStore.getState().bookmarkToggleSignal).toBe(0);
    input.remove();
  });
});
