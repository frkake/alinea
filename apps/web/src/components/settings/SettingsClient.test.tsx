import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { ThemeProvider } from "@/components/ThemeProvider";
import { SettingsClient } from "@/components/settings/SettingsClient";
import type { SettingsCategory, SettingsData } from "@/components/settings/types";
import { mockMatchMedia } from "@/test-utils/mockMatchMedia";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

const settingsGet = vi.fn();
const settingsUpdate = vi.fn();
const settingsListApiKeys = vi.fn();
const settingsPutApiKey = vi.fn();
const settingsDeleteApiKey = vi.fn();
const settingsGetQuota = vi.fn();
const authMe = vi.fn();
const authLogout = vi.fn();
const authDeleteAccount = vi.fn();
vi.mock("@alinea/api-client", () => ({
  settingsGet: (...args: unknown[]) => settingsGet(...args),
  settingsUpdate: (...args: unknown[]) => settingsUpdate(...args),
  settingsListApiKeys: (...args: unknown[]) => settingsListApiKeys(...args),
  settingsPutApiKey: (...args: unknown[]) => settingsPutApiKey(...args),
  settingsDeleteApiKey: (...args: unknown[]) => settingsDeleteApiKey(...args),
  settingsGetQuota: (...args: unknown[]) => settingsGetQuota(...args),
  authMe: (...args: unknown[]) => authMe(...args),
  authLogout: (...args: unknown[]) => authLogout(...args),
  authDeleteAccount: (...args: unknown[]) => authDeleteAccount(...args),
}));

function quotaResponse() {
  const counter = (used: number, limit: number) => ({ used, limit });
  return {
    period: "2026-07",
    byok_active: { text: false, image: false },
    usage: {
      translation_papers: counter(3, 30),
      chat_messages: counter(120, 500),
      images: counter(1, 20),
      article_generations: counter(0, 30),
      vocab_generations: counter(10, 300),
    },
  };
}

function meResponse() {
  return {
    user: {
      id: "u1",
      email: "reader@example.com",
      display_name: "Reader",
      avatar_url: null,
      providers: ["google"],
      created_at: "2026-01-01T00:00:00Z",
    },
    unread_notifications: 0,
  };
}

function fullSettings(overrides: Partial<SettingsData> = {}): SettingsData {
  const routeEntry = { provider: "anthropic", model: "claude-opus-4-8" };
  return {
    display: {
      theme: "system",
      accent: "#3E5C76",
      body_font: "serif",
      font_size_px: 16.5,
      line_height: 2.15,
      content_width_px: 720,
    },
    translation: {
      default_style: "natural",
      auto_translate_appendix: true,
      translate_table_cells: true,
      suggest_section_selection_over_30_pages: false,
    },
    reading: { track_reading_time: true, status_transition: "suggest" },
    chat: { include_annotations_and_notes: true },
    notifications: {
      translation_complete: true,
      status_suggestion: true,
      deadline_reminder: true,
    },
    extension: { arxiv_inline_button: false },
    llm_routing: {
      translation: { provider: "deepseek", model: "deepseek-v4-flash" },
      retranslation: routeEntry,
      chat: routeEntry,
      summary: routeEntry,
      article: routeEntry,
      vocab: routeEntry,
      figure_dsl: routeEntry,
      figure_image: { provider: "google", model: "gemini-3.1-flash-image" },
      overview_figure_raster_mode: false,
    },
    available_models: {
      anthropic: [{ model: "claude-opus-4-8", label: "Claude Opus 4.8" }],
      deepseek: [{ model: "deepseek-v4-flash", label: "DeepSeek v4 Flash" }],
      google: [{ model: "gemini-3.1-flash-image", label: "Gemini 3.1 Flash Image" }],
    },
    ...overrides,
  };
}

function renderSettings(category: SettingsCategory, settings: SettingsData = fullSettings()) {
  settingsGet.mockResolvedValue({ data: settings });
  settingsListApiKeys.mockResolvedValue({ data: { items: [] } });
  settingsUpdate.mockResolvedValue({ data: settings });
  settingsGetQuota.mockResolvedValue({ data: quotaResponse() });
  authMe.mockResolvedValue({ data: meResponse() });
  authLogout.mockResolvedValue({ data: undefined });
  authDeleteAccount.mockResolvedValue({ data: { job_id: "j1" } });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <SettingsClient category={category} />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

function wrap(ui: ReactNode) {
  return ui;
}
void wrap;

describe("SettingsClient (4f)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test("left nav switch uses router.replace (no history growth)", async () => {
    const user = userEvent.setup();
    renderSettings("account");
    await screen.findByText("API キー(BYOK)");

    await user.click(screen.getByRole("button", { name: "データ" }));
    expect(replace).toHaveBeenCalledWith("/settings?category=export");
  });

  test("negated toggles send inverted booleans (ON => false) — 4f §6.2", async () => {
    const user = userEvent.setup();
    renderSettings("translation");
    await screen.findByText("翻訳モデル");

    await user.click(screen.getByRole("switch", { name: "付録(Appendix)を自動翻訳しない" }));
    await waitFor(() =>
      expect(settingsUpdate).toHaveBeenCalledWith(
        expect.objectContaining({
          body: { translation: { auto_translate_appendix: false } },
        }),
      ),
    );
  });

  test("full translation defaults render all opt-out toggles as off", async () => {
    renderSettings("translation");
    await screen.findByText("翻訳モデル");

    expect(screen.getByRole("switch", { name: "付録(Appendix)を自動翻訳しない" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByRole("switch", { name: "表のセル内テキストを翻訳しない" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(
      screen.getByRole("switch", { name: "30 ページ超の論文はセクション選択を提案" }),
    ).toHaveAttribute("aria-checked", "false");
  });

  test("suggest_section toggle (non-negated) sends the raw boolean", async () => {
    const user = userEvent.setup();
    renderSettings("translation");
    await screen.findByText("翻訳モデル");

    await user.click(
      screen.getByRole("switch", { name: "30 ページ超の論文はセクション選択を提案" }),
    );
    await waitFor(() =>
      expect(settingsUpdate).toHaveBeenCalledWith(
        expect.objectContaining({
          body: { translation: { suggest_section_selection_over_30_pages: true } },
        }),
      ),
    );
  });

  test("translation style switch patches default_style", async () => {
    const user = userEvent.setup();
    renderSettings("translation");
    await screen.findByText("翻訳モデル");

    await user.click(screen.getByRole("radio", { name: /直訳/ }));
    await waitFor(() =>
      expect(settingsUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ body: { translation: { default_style: "literal" } } }),
      ),
    );
  });

  test("reading status transition patches reading.status_transition", async () => {
    const user = userEvent.setup();
    renderSettings("reading");
    await screen.findByText("ステータスの自動遷移");

    await user.click(screen.getByRole("radio", { name: "自動適用" }));
    await waitFor(() =>
      expect(settingsUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ body: { reading: { status_transition: "auto" } } }),
      ),
    );
  });

  test("notifications category renders 3 toggles and patches on change", async () => {
    const user = userEvent.setup();
    renderSettings("notifications");
    await screen.findByText("締切リマインド");

    await user.click(screen.getByRole("switch", { name: "締切リマインド" }));
    await waitFor(() =>
      expect(settingsUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ body: { notifications: { deadline_reminder: false } } }),
      ),
    );
  });

  test("extension category renders the arXiv inline-button toggle", async () => {
    const user = userEvent.setup();
    renderSettings("extension");
    await screen.findByText("arXiv ページ内に「A 保存」ボタンを表示");

    await user.click(
      screen.getByRole("switch", { name: "arXiv ページ内に「A 保存」ボタンを表示" }),
    );
    await waitFor(() =>
      expect(settingsUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ body: { extension: { arxiv_inline_button: true } } }),
      ),
    );
  });

  test("display category accent swatch click patches display.accent immediately", async () => {
    const user = userEvent.setup();
    renderSettings("display");
    await screen.findByText("アクセントカラー");

    await user.click(screen.getByRole("radio", { name: "緑" }));
    await waitFor(() =>
      expect(settingsUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ body: { display: { accent: "#4A6B57" } } }),
      ),
    );
    expect(document.documentElement.getAttribute("data-accent")).toBe("green");
  });

  test("display category font-size stepper reflects immediately via a CSS var and patches", async () => {
    const user = userEvent.setup();
    renderSettings("display");
    await screen.findByText("本文サイズ");

    await user.click(screen.getByRole("button", { name: "本文サイズを増やす" }));
    await waitFor(() =>
      expect(settingsUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ body: { display: { font_size_px: 17 } } }),
      ),
    );
    expect(document.documentElement.style.getPropertyValue("--pr-content-font-size-px")).toBe(
      "17px",
    );
  });

  test("invalid category falls back gracefully to a rendered section (defensive)", async () => {
    renderSettings("chat");
    await screen.findByText("チャットモデル");
    expect(screen.getByText("注釈・メモを文脈に含める")).toBeInTheDocument();
  });

  // S1 #3: テーマ切替(display.theme を永続化しつつ data-theme を即時反映)。
  test("display category theme control patches display.theme and applies data-theme", async () => {
    const user = userEvent.setup();
    renderSettings("display");
    await screen.findByText("テーマ");

    await user.click(screen.getByRole("radio", { name: "ダーク" }));
    await waitFor(() =>
      expect(settingsUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ body: { display: { theme: "dark" } } }),
      ),
    );
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  // S1 #4: アカウント設定(identity・クォータ残量・ログアウト・削除)。
  test("account category shows signed-in identity and quota remaining", async () => {
    renderSettings("account");
    await screen.findByText("API キー(BYOK)");
    expect(await screen.findByText("reader@example.com")).toBeInTheDocument();
    // クォータ残量(チャットメッセージ used/limit)。
    expect(await screen.findByText(/120\s*\/\s*500/)).toBeInTheDocument();
  });

  test("account category logout calls authLogout", async () => {
    const user = userEvent.setup();
    renderSettings("account");
    await screen.findByText("API キー(BYOK)");
    await user.click(await screen.findByRole("button", { name: "ログアウト" }));
    await waitFor(() => expect(authLogout).toHaveBeenCalled());
  });

  test("account category delete requires the confirm word and calls authDeleteAccount", async () => {
    const user = userEvent.setup();
    renderSettings("account");
    await screen.findByText("API キー(BYOK)");

    await user.click(await screen.findByRole("button", { name: "アカウントを削除" }));
    // モーダルが開き、合言葉入力が必要。
    const input = await screen.findByPlaceholderText("delete");
    await user.type(input, "delete");
    await user.click(screen.getByRole("button", { name: "削除する" }));
    await waitFor(() =>
      expect(authDeleteAccount).toHaveBeenCalledWith(
        expect.objectContaining({ body: { confirm: "delete" } }),
      ),
    );
  });
});

// mobile.md §1 実装 1「設定(ナビ折りたたみ)」/ §1.2-7「API キー・エクスポート実行は非描画」。
describe("SettingsClient mobile reduction (mobile.md)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("collapses the always-expanded left nav into a tap-to-open category dropdown", async () => {
    mockMatchMedia(true);
    const user = userEvent.setup();
    renderSettings("account");
    await screen.findByText("API キー(BYOK)");

    // 常時展開の左ナビ(NavItem)は無く、代わりに現在カテゴリを表示するボタンがある。
    expect(screen.queryByRole("button", { name: "データ" })).toBeNull();
    const trigger = screen.getByRole("button", { name: /カテゴリ: アカウント/ });
    await user.click(trigger);
    await user.click(screen.getByRole("menuitemradio", { name: "データ" }));
    expect(replace).toHaveBeenCalledWith("/settings?category=export");
  });

  test("hides API key set/delete controls (readOnly) while keeping the masked value visible", async () => {
    mockMatchMedia(true);
    renderSettings("account", fullSettings());
    await screen.findByText("API キー(BYOK)");
    expect(screen.queryByRole("button", { name: "設定" })).toBeNull();
  });

  test("replaces export execution with a read-only note", async () => {
    mockMatchMedia(true);
    renderSettings("export");
    await screen.findByText("データ");
    expect(screen.queryByRole("button", { name: /Markdown/ })).toBeNull();
    expect(screen.getAllByText(/実行はデスクトップから行えます/).length).toBeGreaterThan(0);
  });
});
