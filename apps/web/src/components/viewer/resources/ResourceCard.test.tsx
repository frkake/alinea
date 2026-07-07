import { render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { ResourceCard } from "./ResourceCard";
import type { ResourceLink } from "./types";

function resource(overrides: Partial<ResourceLink> = {}): ResourceLink {
  return {
    id: "res_1",
    kind: "github",
    url: "https://github.com/gnobitab/RectifiedFlow",
    official: false,
    title: "gnobitab/RectifiedFlow",
    source_label: "GitHub",
    thumbnail_url: null,
    meta: {},
    meta_fetched: true,
    note: null,
    created_at: "2026-07-01T00:00:00Z",
    ...overrides,
  };
}

const noop = {
  onJumpSection: vi.fn(),
  onEdit: vi.fn(),
  onRefreshMeta: vi.fn(),
  onDelete: vi.fn(),
};

// VT-VIEW-17: ResourceCard — kind 別メタ表示・YouTube サムネ+再生時間バッジ
describe("ResourceCard kind 別メタ表示(VT-VIEW-17)", () => {
  test("github: 言語・スター・更新月をメタ行に表示", () => {
    render(
      <ResourceCard
        resource={resource({
          kind: "github",
          meta: { language: "Python", stars: 1200, updated_at: "2023-11-15" },
        })}
        flash={false}
        {...noop}
      />,
    );
    expect(screen.getByText("gnobitab/RectifiedFlow")).toBeInTheDocument();
    expect(screen.getByText("GitHub · Python · ★ 1.2k · 更新 2023-11")).toBeInTheDocument();
  });

  test("official=true shows the 公式実装 badge", () => {
    render(
      <ResourceCard
        resource={resource({ official: true, meta: { language: "Python", stars: 1, updated_at: null } })}
        flash={false}
        {...noop}
      />,
    );
    expect(screen.getByText("公式実装")).toBeInTheDocument();
  });

  test("youtube: サムネイル+再生時間バッジ(12:34)を表示する", () => {
    render(
      <ResourceCard
        resource={resource({
          kind: "youtube",
          title: "ICLR 2023 Oral — Flow Straight and Fast",
          source_label: "YouTube",
          url: "https://www.youtube.com/watch?v=abc123",
          thumbnail_url: "https://i.ytimg.com/vi/abc123/hqdefault.jpg",
          meta: { duration_seconds: 754 },
        })}
        flash={false}
        {...noop}
      />,
    );
    expect(screen.getByText("YouTube · 12:34")).toBeInTheDocument();
    expect(screen.getByText("12:34")).toBeInTheDocument(); // サムネイル右下バッジ
    expect(screen.getByRole("link", { name: "YouTube で開く" })).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=abc123",
    );
  });

  test("slides: ドメイン・PDF・枚数をメタ行に表示", () => {
    render(
      <ResourceCard
        resource={resource({
          kind: "slides",
          title: "発表スライド(ICLR 2023)",
          source_label: "iclr.cc",
          url: "https://iclr.cc/slides/deck.pdf",
          meta: { format: "pdf", pages: 24 },
        })}
        flash={false}
        {...noop}
      />,
    );
    expect(screen.getByText("iclr.cc · PDF · 24 枚")).toBeInTheDocument();
  });

  test("article: ドメイン・解説記事・読了目安をメタ行に表示", () => {
    render(
      <ResourceCard
        resource={resource({
          kind: "article",
          title: "Rectified Flow を図で理解する",
          source_label: "zenn.dev",
          url: "https://zenn.dev/some/articles/xyz",
          meta: { reading_minutes: 15 },
        })}
        flash={false}
        {...noop}
      />,
    );
    expect(screen.getByText("zenn.dev · 解説記事 · 15 min")).toBeInTheDocument();
  });

  test("meta_fetched=false shows the 控えめな取得不可表示", () => {
    render(
      <ResourceCard
        resource={resource({ meta_fetched: false, title: "github.com/x/y", meta: {} })}
        flash={false}
        {...noop}
      />,
    );
    expect(screen.getByText("GitHub · タイトル・メタ取得不可")).toBeInTheDocument();
  });
});

// VT-VIEW-19: ResourceCard「開く ↗」 target="_blank" rel="noopener noreferrer"
describe('ResourceCard「開く ↗」(VT-VIEW-19)', () => {
  test("opens the external URL in a new tab without leaking window.opener", () => {
    render(
      <ResourceCard
        resource={resource({ url: "https://github.com/gnobitab/RectifiedFlow" })}
        flash={false}
        {...noop}
      />,
    );
    const link = screen.getByRole("link", { name: "開く ↗" });
    expect(link).toHaveAttribute("href", "https://github.com/gnobitab/RectifiedFlow");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});

describe("ResourceCard ひとことメモ", () => {
  test("§ チップをクリックすると onJumpSection が呼ばれる", () => {
    const onJumpSection = vi.fn();
    render(
      <ResourceCard
        resource={resource({
          note: "train_reflow.py が [[sec:sec-3|§2.2]] の手順に対応。",
        })}
        flash={false}
        onJumpSection={onJumpSection}
        onEdit={noop.onEdit}
        onRefreshMeta={noop.onRefreshMeta}
        onDelete={noop.onDelete}
      />,
    );
    expect(screen.getByText(/train_reflow.py/)).toBeInTheDocument();
    screen.getByText("§2.2").click();
    expect(onJumpSection).toHaveBeenCalledWith("sec-3");
  });
});
