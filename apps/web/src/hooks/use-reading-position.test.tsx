import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { libraryItemsSavePosition } from "@alinea/api-client";
import { useReadingPosition } from "@/hooks/use-reading-position";
import { useViewerStore } from "@/stores/viewer-store";

vi.mock("@alinea/api-client", () => ({ libraryItemsSavePosition: vi.fn() }));

describe("useReadingPosition", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useViewerStore.setState({ currentBlockId: null, activeSectionId: null });
  });

  test("uses a keepalive PUT on pagehide instead of a POST-only beacon", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    const beacon = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(navigator, "sendBeacon", {
      configurable: true,
      value: beacon,
    });

    renderHook(() =>
      useReadingPosition({
        itemId: "item-1",
        revisionId: "revision-1",
        mode: "translation",
      }),
    );
    act(() => {
      useViewerStore.getState().setCurrentBlock("block-1", "section-1");
    });
    await waitFor(() => expect(useViewerStore.getState().currentBlockId).toBe("block-1"));

    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/library-items/item-1/position", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      keepalive: true,
      body: JSON.stringify({
        revision_id: "revision-1",
        block_id: "block-1",
        mode: "translation",
      }),
    });
    expect(beacon).not.toHaveBeenCalled();
    expect(libraryItemsSavePosition).not.toHaveBeenCalled();
  });
});
