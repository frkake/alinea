import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { ShareCollectionResponse } from "./fetch-share";

const NOT_FOUND_SENTINEL = new Error("NEXT_NOT_FOUND");

vi.mock("next/navigation", () => ({
  notFound: () => {
    throw NOT_FOUND_SENTINEL;
  },
}));

const fetchShareCollection = vi.fn();
vi.mock("./fetch-share", () => ({
  fetchShareCollection: (token: string) => fetchShareCollection(token),
}));

const SAMPLE: ShareCollectionResponse = {
  collection: {
    name: "輪読会 2026-07",
    description: "7/16(木)の輪読会で扱う候補。",
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

afterEach(() => {
  vi.clearAllMocks();
});

describe("SharePage(RSC)", () => {
  test("データ取得成功時、コレクション名・カード・フッター注記を描画する", async () => {
    fetchShareCollection.mockResolvedValueOnce(SAMPLE);
    const { default: SharePage } = await import("./page");
    const element = await SharePage({ params: Promise.resolve({ token: "x8Kf3qPw" }) });
    render(element);

    expect(screen.getByRole("heading", { level: 1, name: "輪読会 2026-07" })).toBeInTheDocument();
    expect(screen.getByText("Consistency Models")).toBeInTheDocument();
    expect(screen.getByText(/このページは閲覧専用です/)).toBeInTheDocument();
    expect(fetchShareCollection).toHaveBeenCalledWith("x8Kf3qPw");
  });

  test("items が 0 件のとき EmptyState を描画する(§5.2)", async () => {
    fetchShareCollection.mockResolvedValueOnce({ ...SAMPLE, items: [] });
    const { default: SharePage } = await import("./page");
    const element = await SharePage({ params: Promise.resolve({ token: "x8Kf3qPw" }) });
    render(element);

    expect(screen.getByText("このコレクションにはまだ論文がありません")).toBeInTheDocument();
  });

  test("データが null(404/形式不正)のとき notFound() を呼ぶ", async () => {
    fetchShareCollection.mockResolvedValueOnce(null);
    const { default: SharePage } = await import("./page");
    await expect(SharePage({ params: Promise.resolve({ token: "bad" }) })).rejects.toBe(
      NOT_FOUND_SENTINEL,
    );
  });
});

describe("generateMetadata", () => {
  test("取得成功時: title/description/robots noindex/OGP を組み立てる", async () => {
    fetchShareCollection.mockResolvedValueOnce(SAMPLE);
    const { generateMetadata } = await import("./page");
    const metadata = await generateMetadata({ params: Promise.resolve({ token: "x8Kf3qPw" }) });
    expect(metadata.title).toBe("輪読会 2026-07 — 訳読で共有されたコレクション");
    expect(metadata.robots).toEqual({ index: false, follow: false });
    expect(metadata.openGraph?.title).toBe("輪読会 2026-07");
  });

  test("取得失敗(null)時: 既定タイトル + noindex", async () => {
    fetchShareCollection.mockResolvedValueOnce(null);
    const { generateMetadata } = await import("./page");
    const metadata = await generateMetadata({ params: Promise.resolve({ token: "bad" }) });
    expect(metadata.title).toBe("訳読 — 共有ページ");
    expect(metadata.robots).toEqual({ index: false, follow: false });
  });
});
