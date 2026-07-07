import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { fetchShareCollection, type ShareCollectionResponse } from "./fetch-share";

const SAMPLE: ShareCollectionResponse = {
  collection: {
    name: "輪読会 2026-07",
    description: null,
    shared_by: "YK",
    updated_at: "2026-07-06T00:00:00Z",
    deadline: "2026-07-16",
    item_count: 1,
  },
  include_notes: false,
  items: [
    {
      order: 1,
      title: "Consistency Models",
      authors_short: "Song, Dhariwal",
      venue_year: "ICML 2023",
      arxiv_url: "https://arxiv.org/abs/1",
      summary_3line: ["要約。"],
      shared_note: null,
    },
  ],
};

describe("fetchShareCollection", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = vi.fn();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  test("token 形式不一致(8 文字英数以外)は fetch せず null を返す", async () => {
    const result = await fetchShareCollection("not-8-chars");
    expect(result).toBeNull();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  test("200 は ShareCollectionResponse を返す", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(SAMPLE), { status: 200 }),
    );
    const result = await fetchShareCollection("x8Kf3qPw");
    expect(result).toEqual(SAMPLE);
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/share/collections/x8Kf3qPw"),
      expect.objectContaining({ next: { revalidate: 60 } }),
    );
  });

  test("404(revoked・不存在)は null を返す", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(new Response(null, { status: 404 }));
    const result = await fetchShareCollection("x8Kf3qPw");
    expect(result).toBeNull();
  });

  test("5xx は throw する(呼び出し側は error.tsx)", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(new Response(null, { status: 500 }));
    await expect(fetchShareCollection("x8Kf3qPw")).rejects.toThrow();
  });

  test("429 は throw する", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(new Response(null, { status: 429 }));
    await expect(fetchShareCollection("x8Kf3qPw")).rejects.toThrow();
  });
});
