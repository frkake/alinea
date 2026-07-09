"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  settingsDeleteApiKey,
  settingsGet,
  settingsListApiKeys,
  settingsPutApiKey,
  settingsUpdate,
} from "@alinea/api-client";
import { useToast } from "@/components/ui/Toast";
import { EmptyState } from "@/components/ui/EmptyState";
import { Popover } from "@/components/ui/Popover";
import { useTheme } from "@/components/ThemeProvider";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { AccountSettings } from "@/components/settings/AccountSettings";
import { TranslationSettings } from "@/components/settings/TranslationSettings";
import { DisplaySettings } from "@/components/settings/DisplaySettings";
import { ReadingSettings } from "@/components/settings/ReadingSettings";
import { ChatSettings } from "@/components/settings/ChatSettings";
import { NotificationSettings } from "@/components/settings/NotificationSettings";
import { ExtensionSettings } from "@/components/settings/ExtensionSettings";
import { ExportSettings } from "@/components/settings/ExportSettings";
import { isValidationErrorLike } from "@/components/settings/errors";
import {
  accentKeyForHex,
  type AccentHex,
  type BodyFontValue,
  type ByokProvider,
  type LlmUseCase,
  type RouteEntry,
  type SettingsCategory,
  type SettingsData,
  type StatusTransition,
  type TranslationStyle,
} from "@/components/settings/types";

/**
 * 本文サイズの即時反映用 CSS 変数(4f §5.6 の ThemeProvider 流儀に倣う自己完結実装)。
 * 現時点でビューア側の読者はまだ無い(font_size_px の実描画反映は本タスクのスコープ外)。
 */
const FONT_SIZE_CSS_VAR = "--pr-content-font-size-px";

function applyFontSizeVar(px: number): void {
  if (typeof document === "undefined") return;
  document.documentElement.style.setProperty(FONT_SIZE_CSS_VAR, `${px}px`);
}

/** 左ナビの表示順(4f §4.3)。 */
const CATEGORIES: ReadonlyArray<{ id: SettingsCategory; label: string }> = [
  { id: "account", label: "アカウント" },
  { id: "display", label: "表示" },
  { id: "translation", label: "翻訳" },
  { id: "reading", label: "読書の計測と提案" },
  { id: "chat", label: "チャット" },
  { id: "notifications", label: "通知" },
  { id: "export", label: "エクスポート" },
  { id: "extension", label: "ブラウザ拡張" },
];

/** 設定画面のクライアント本体(4f)。8 カテゴリを ?category= で切替。 */
export function SettingsClient({ category }: { category: SettingsCategory }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const { setAccent, setBodyFont } = useTheme();
  const contentRef = useRef<HTMLDivElement>(null);
  const isMobile = useIsMobile();

  // 左ナビ切替でコンテンツの scrollTop を 0 に戻す(4f §5.2)。
  useEffect(() => {
    // jsdom(テスト環境)は scrollTo 未実装のため optional call で握る。
    contentRef.current?.scrollTo?.({ top: 0 });
  }, [category]);

  const settingsQuery = useQuery({
    queryKey: ["settings", "detail"],
    queryFn: async () => (await settingsGet({ throwOnError: true })).data as unknown as SettingsData,
    staleTime: 60_000,
  });

  const apiKeysQuery = useQuery({
    queryKey: ["settings", "api-keys"],
    queryFn: async () => (await settingsListApiKeys({ throwOnError: true })).data.items,
    staleTime: 60_000,
    enabled: category === "account",
  });

  const patchMutation = useMutation({
    mutationFn: async (body: Record<string, unknown>) =>
      (await settingsUpdate({ body, throwOnError: true })).data as unknown as SettingsData,
    onSuccess: (data) => {
      queryClient.setQueryData(["settings", "detail"], data);
    },
    onError: () => {
      toast({ kind: "error", message: "設定を保存できませんでした。もう一度お試しください" });
    },
  });

  const saveKeyMutation = useMutation({
    mutationFn: async ({ provider, apiKey }: { provider: ByokProvider; apiKey: string }) =>
      settingsPutApiKey({ path: { provider }, body: { api_key: apiKey }, throwOnError: true }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "api-keys"] });
      void queryClient.invalidateQueries({ queryKey: ["settings", "quota"] });
    },
    onError: (err) => {
      // 422(キー形式不正)は ApiKeyRow がインライン表示するため Toast を出さない(4f §5.4)。
      if (!isValidationErrorLike(err)) {
        toast({ kind: "error", message: "API キーを保存できませんでした。もう一度お試しください" });
      }
    },
  });

  const deleteKeyMutation = useMutation({
    mutationFn: async (provider: ByokProvider) =>
      settingsDeleteApiKey({ path: { provider }, throwOnError: true }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "api-keys"] });
      void queryClient.invalidateQueries({ queryKey: ["settings", "quota"] });
    },
    onError: () => {
      toast({ kind: "error", message: "API キーを削除できませんでした。もう一度お試しください" });
    },
  });

  const settings = settingsQuery.data;

  // 初回ロード時に本文サイズ CSS 変数を反映する(§5.6)。
  useEffect(() => {
    if (settings) applyFontSizeVar(settings.display.font_size_px);
  }, [settings]);

  const onRouteChange = (useCase: LlmUseCase, entry: RouteEntry) => {
    patchMutation.mutate({ llm_routing: { [useCase]: entry } });
  };
  const onRasterChange = (next: boolean) => {
    patchMutation.mutate({ llm_routing: { overview_figure_raster_mode: next } });
  };

  // --- 翻訳(4f §4.4) ---
  const onStyleChange = (style: TranslationStyle) => {
    patchMutation.mutate({ translation: { default_style: style } });
  };
  const onAutoTranslateAppendixToggle = (checked: boolean) => {
    // UI は否定形(ON=翻訳しない)。API へは反転して送る。
    patchMutation.mutate({ translation: { auto_translate_appendix: !checked } });
  };
  const onTranslateTableCellsToggle = (checked: boolean) => {
    patchMutation.mutate({ translation: { translate_table_cells: !checked } });
  };
  const onSuggestSectionToggle = (checked: boolean) => {
    patchMutation.mutate({ translation: { suggest_section_selection_over_30_pages: checked } });
  };

  // --- 読書の計測と提案(4f §4.5) ---
  const onTrackToggle = (checked: boolean) => {
    patchMutation.mutate({ reading: { track_reading_time: checked } });
  };
  const onStatusTransitionChange = (next: StatusTransition) => {
    patchMutation.mutate({ reading: { status_transition: next } });
  };

  // --- チャット(4f §4.7.6) ---
  const onIncludeToggle = (checked: boolean) => {
    patchMutation.mutate({ chat: { include_annotations_and_notes: checked } });
  };

  // --- 通知(4f §4.7.7) ---
  const onTranslationCompleteToggle = (checked: boolean) => {
    patchMutation.mutate({ notifications: { translation_complete: checked } });
  };
  const onStatusSuggestionToggle = (checked: boolean) => {
    patchMutation.mutate({ notifications: { status_suggestion: checked } });
  };
  const onDeadlineReminderToggle = (checked: boolean) => {
    patchMutation.mutate({ notifications: { deadline_reminder: checked } });
  };

  // --- ブラウザ拡張(4f §4.7.8) ---
  const onExtensionToggle = (checked: boolean) => {
    patchMutation.mutate({ extension: { arxiv_inline_button: checked } });
  };

  // --- 表示(4f §4.7.5・即時反映は §5.6 の ThemeProvider 流儀) ---
  const onAccentChange = (hex: AccentHex) => {
    const prevHex = settings?.display.accent;
    setAccent(accentKeyForHex(hex));
    patchMutation.mutate(
      { display: { accent: hex } },
      {
        onError: () => {
          if (prevHex) setAccent(accentKeyForHex(prevHex));
        },
      },
    );
  };
  const onBodyFontChange = (font: BodyFontValue) => {
    const prev = settings?.display.body_font;
    setBodyFont(font);
    patchMutation.mutate(
      { display: { body_font: font } },
      {
        onError: () => {
          if (prev) setBodyFont(prev);
        },
      },
    );
  };
  const onFontSizeChange = (px: number) => {
    const prev = settings?.display.font_size_px;
    applyFontSizeVar(px);
    patchMutation.mutate(
      { display: { font_size_px: px } },
      {
        onError: () => {
          if (prev !== undefined) applyFontSizeVar(prev);
        },
      },
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: isMobile ? "column" : "row", minHeight: 0, flex: 1 }}>
      {/* モバイル縮退(mobile.md §1 実装 1): カテゴリナビを折りたたむ(タップで展開する
          ドロップダウンに差し替え。決定)。 */}
      {isMobile ? (
        <MobileSettingsCategoryNav
          active={category}
          onSelect={(next) => {
            router.replace(`/settings?category=${next}`);
          }}
        />
      ) : (
        <SettingsCategoryNav
          active={category}
          onSelect={(next) => {
            router.replace(`/settings?category=${next}`);
          }}
        />
      )}
      <div
        ref={contentRef}
        style={{
          flex: 1,
          minWidth: 0,
          overflowY: "auto",
          display: "flex",
          justifyContent: "center",
          alignItems: "flex-start",
        }}
      >
        <div
          style={{
            width: 720,
            maxWidth: "100%",
            padding: "26px 22px",
            display: "flex",
            flexDirection: "column",
            gap: 22,
          }}
        >
          {settingsQuery.isError ? (
            <EmptyState
              title="設定を読み込めませんでした"
              description="通信状態を確認してから再試行してください"
              action={{ label: "再試行", onClick: () => void settingsQuery.refetch() }}
            />
          ) : !settings ? (
            <div style={{ fontSize: 11.5, color: "var(--pr-text-muted)" }}>読み込み中…</div>
          ) : category === "account" ? (
            <AccountSettings
              settings={settings}
              apiKeys={apiKeysQuery.data ?? []}
              onRouteChange={onRouteChange}
              onRasterChange={onRasterChange}
              onSaveKey={(provider, apiKey) => saveKeyMutation.mutateAsync({ provider, apiKey })}
              onDeleteKey={(provider) => {
                deleteKeyMutation.mutate(provider);
              }}
              readOnly={isMobile}
            />
          ) : category === "display" ? (
            <DisplaySettings
              settings={settings}
              onAccentChange={onAccentChange}
              onBodyFontChange={onBodyFontChange}
              onFontSizeChange={onFontSizeChange}
            />
          ) : category === "translation" ? (
            <TranslationSettings
              settings={settings}
              onRouteChange={onRouteChange}
              onStyleChange={onStyleChange}
              onAutoTranslateAppendixToggle={onAutoTranslateAppendixToggle}
              onTranslateTableCellsToggle={onTranslateTableCellsToggle}
              onSuggestSectionToggle={onSuggestSectionToggle}
            />
          ) : category === "reading" ? (
            <ReadingSettings
              settings={settings}
              onTrackToggle={onTrackToggle}
              onStatusTransitionChange={onStatusTransitionChange}
            />
          ) : category === "chat" ? (
            <ChatSettings settings={settings} onIncludeToggle={onIncludeToggle} onRouteChange={onRouteChange} />
          ) : category === "notifications" ? (
            <NotificationSettings
              settings={settings}
              onTranslationCompleteToggle={onTranslationCompleteToggle}
              onStatusSuggestionToggle={onStatusSuggestionToggle}
              onDeadlineReminderToggle={onDeadlineReminderToggle}
            />
          ) : category === "export" ? (
            <ExportSettings readOnly={isMobile} />
          ) : (
            <ExtensionSettings settings={settings} onToggle={onExtensionToggle} />
          )}
        </div>
      </div>
    </div>
  );
}

function SettingsCategoryNav({
  active,
  onSelect,
}: {
  active: SettingsCategory;
  onSelect: (next: SettingsCategory) => void;
}) {
  return (
    <nav
      aria-label="設定カテゴリ"
      style={{
        width: 216,
        flex: "none",
        borderRight: "1px solid var(--pr-border-pane)",
        background: "var(--pr-bg-pane)",
        padding: "14px 10px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        fontSize: 12.5,
      }}
    >
      {CATEGORIES.map((c) => (
        <NavItem key={c.id} id={c.id} label={c.label} selected={c.id === active} onSelect={onSelect} />
      ))}
    </nav>
  );
}

/**
 * モバイル縮退のカテゴリナビ(mobile.md §1 実装 1「設定(ナビ折りたたみ)」)。
 * 常時展開の 216px 左ナビの代わりに、現在カテゴリを表示するボタン+タップで開く
 * ドロップダウン(Popover)に折りたたむ。
 */
function MobileSettingsCategoryNav({
  active,
  onSelect,
}: {
  active: SettingsCategory;
  onSelect: (next: SettingsCategory) => void;
}) {
  const anchorRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const activeLabel = CATEGORIES.find((c) => c.id === active)?.label ?? "";

  return (
    <div
      style={{
        flex: "none",
        borderBottom: "1px solid var(--pr-border-pane)",
        background: "var(--pr-bg-pane)",
        padding: "10px 14px",
      }}
    >
      <button
        ref={anchorRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          width: "100%",
          height: 32,
          padding: "0 12px",
          border: "1px solid var(--pr-border-control)",
          borderRadius: 8,
          background: "var(--pr-bg-control)",
          color: "var(--pr-text)",
          fontSize: 12.5,
          fontWeight: 600,
          cursor: "pointer",
          fontFamily: "inherit",
        }}
      >
        カテゴリ: {activeLabel}
        <span style={{ marginLeft: "auto", color: "var(--pr-text-muted)", fontSize: 9 }}>▾</span>
      </button>
      <Popover open={open} onClose={() => setOpen(false)} anchorRef={anchorRef} width={260} caret={false}>
        <div role="menu" style={{ padding: 4 }}>
          {CATEGORIES.map((c) => (
            <button
              key={c.id}
              type="button"
              role="menuitemradio"
              aria-checked={c.id === active}
              onClick={() => {
                onSelect(c.id);
                setOpen(false);
              }}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "8px 10px",
                border: "none",
                borderRadius: 6,
                background: c.id === active ? "var(--pr-acc-s)" : "transparent",
                color: c.id === active ? "var(--pr-acc)" : "var(--pr-text-mid)",
                fontWeight: c.id === active ? 600 : 400,
                fontSize: 12.5,
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              {c.label}
            </button>
          ))}
        </div>
      </Popover>
    </div>
  );
}

function NavItem({
  id,
  label,
  selected,
  onSelect,
}: {
  id: SettingsCategory;
  label: string;
  selected: boolean;
  onSelect: (next: SettingsCategory) => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      aria-current={selected ? "page" : undefined}
      onClick={() => {
        onSelect(id);
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        textAlign: "left",
        padding: "7px 10px",
        borderRadius: 6,
        border: "none",
        color: selected ? "var(--pr-acc)" : "var(--pr-text-nav)",
        fontWeight: selected ? 600 : 400,
        background: selected ? "var(--pr-acc-s)" : hover ? "var(--pr-bg-hover)" : "transparent",
        cursor: "pointer",
        fontFamily: "inherit",
        fontSize: 12.5,
      }}
    >
      {label}
    </button>
  );
}
