import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { articlesGenerate } from "@alinea/api-client";
import { ArticleGenerateCTA } from "@/components/viewer/article/ArticleGenerateCTA";
import { ToastViewport } from "@/components/ui/Toast";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return { ...actual, articlesGenerate: vi.fn() };
});

describe("ArticleGenerateCTA (1h §5.2)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test("defaults to beginner preset with 数式を含める off", () => {
    render(
      <>
        <ArticleGenerateCTA libraryItemId="li_1" onGenerated={vi.fn()} />
        <ToastViewport />
      </>,
    );
    expect(screen.getByRole("radio", { name: "初学者向け" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: "数式を含める" })).toHaveAttribute("aria-checked", "false");
  });

  test("switching preset resets the untouched toggle to the preset's default", async () => {
    const user = userEvent.setup();
    render(
      <>
        <ArticleGenerateCTA libraryItemId="li_1" onGenerated={vi.fn()} />
        <ToastViewport />
      </>,
    );
    await user.click(screen.getByRole("radio", { name: "実装者向け" }));
    expect(screen.getByRole("switch", { name: "数式を含める" })).toHaveAttribute("aria-checked", "true");
    await user.click(screen.getByRole("radio", { name: "輪読会向け" }));
    expect(screen.getByRole("switch", { name: "数式を含める" })).toHaveAttribute("aria-checked", "false");
  });

  test("once the toggle is touched by hand, preset switches no longer override it", async () => {
    const user = userEvent.setup();
    render(
      <>
        <ArticleGenerateCTA libraryItemId="li_1" onGenerated={vi.fn()} />
        <ToastViewport />
      </>,
    );
    await user.click(screen.getByRole("switch", { name: "数式を含める" }));
    expect(screen.getByRole("switch", { name: "数式を含める" })).toHaveAttribute("aria-checked", "true");
    await user.click(screen.getByRole("radio", { name: "輪読会向け" })); // 既定は OFF だが上書きしない
    expect(screen.getByRole("switch", { name: "数式を含める" })).toHaveAttribute("aria-checked", "true");
  });

  test("submitting posts the current preset/include_math and shows progress until the job resolves", async () => {
    const user = userEvent.setup();
    vi.mocked(articlesGenerate).mockResolvedValue({ data: { job_id: "job_1" } } as never);
    render(
      <>
        <ArticleGenerateCTA libraryItemId="li_1" onGenerated={vi.fn()} />
        <ToastViewport />
      </>,
    );
    await user.click(screen.getByRole("radio", { name: "研究者向け" }));
    await user.click(screen.getByText("✦ 記事を生成"));
    expect(articlesGenerate).toHaveBeenCalledWith({
      path: { item_id: "li_1" },
      body: { preset: "researcher", include_math: true },
      throwOnError: true,
    });
    expect(await screen.findByText(/✦ 記事を生成しています…/)).toBeInTheDocument();
  });

  test("shows an error toast when the generate request fails", async () => {
    const user = userEvent.setup();
    vi.mocked(articlesGenerate).mockRejectedValue({ title: "失敗しました" });
    render(
      <>
        <ArticleGenerateCTA libraryItemId="li_1" onGenerated={vi.fn()} />
        <ToastViewport />
      </>,
    );
    await user.click(screen.getByText("✦ 記事を生成"));
    expect(await screen.findByText("失敗しました")).toBeInTheDocument();
  });
});
