/**
 * 設定画面(4f)の型・語彙(M1-17: 8 カテゴリ完成)。
 * GET /api/settings のレスポンスは生成 SDK では緩い型({[key:string]:unknown})のため、
 * 画面で使う部分だけをここで型付けする(API の schemas/settings.py と 1:1)。
 */

import type { AccentKey } from "@/lib/theme";

/** 設定カテゴリ 8 種(4f §4.3。左ナビの表示順と同一)。 */
export type SettingsCategory =
  | "account"
  | "display"
  | "translation"
  | "reading"
  | "chat"
  | "notifications"
  | "export"
  | "extension";

export const SETTINGS_CATEGORIES: readonly SettingsCategory[] = [
  "account",
  "display",
  "translation",
  "reading",
  "chat",
  "notifications",
  "export",
  "extension",
];

export function isSettingsCategory(v: string | undefined): v is SettingsCategory {
  return !!v && (SETTINGS_CATEGORIES as readonly string[]).includes(v);
}

/** 既定の翻訳スタイル(4f §4.4.1)。 */
export type TranslationStyle = "natural" | "literal";
/** ステータスの自動遷移(4f §4.5.2)。 */
export type StatusTransition = "auto" | "suggest" | "off";
/** テーマ(display.theme。本画面では未描画=M1-17 スコープ外だが型は保持)。 */
export type ThemePrefValue = "light" | "dark" | "system";
/** 本文の書体(4f §4.7.5)。 */
export type BodyFontValue = "serif" | "sans";
/** アクセントの hex 値(plans/03 §17.1)。 */
export type AccentHex = "#3E5C76" | "#4A6B57" | "#6E5A7E" | "#7A5C48";

export interface DisplayPrefs {
  theme: ThemePrefValue;
  accent: AccentHex;
  body_font: BodyFontValue;
  font_size_px: number;
  line_height: number;
  content_width_px: number;
}

export interface TranslationPrefs {
  default_style: TranslationStyle;
  auto_translate_appendix: boolean;
  translate_table_cells: boolean;
  suggest_section_selection_over_30_pages: boolean;
}

export interface ReadingPrefs {
  track_reading_time: boolean;
  status_transition: StatusTransition;
}

export interface ChatPrefs {
  include_annotations_and_notes: boolean;
}

export interface NotificationsPrefs {
  translation_complete: boolean;
  status_suggestion: boolean;
  deadline_reminder: boolean;
}

export interface ExtensionPrefs {
  arxiv_inline_button: boolean;
}

/** GitHub コード対応解析のモード(設計 §6・API schemas/settings.py CodeAnalysisSettings)。 */
export type CodeAnalysisMode = "off" | "on_demand" | "automatic";

/**
 * GitHub コード対応解析の設定(Task 21・22)。
 * API は monthly_budget_usd を JSONB の都合で文字列でシリアライズするため、
 * 画面側は number へ正規化して扱う(SettingsClient のクエリで変換)。
 */
export interface CodeAnalysisPrefs {
  mode: CodeAnalysisMode;
  monthly_budget_usd: number;
}

/** アクセントの hex → data-accent キー対応(plans/08 §2.3 ACCENTS)。 */
export const ACCENT_SWATCHES: ReadonlyArray<{ hex: AccentHex; key: AccentKey; label: string }> = [
  { hex: "#3E5C76", key: "slate", label: "スレートブルー" },
  { hex: "#4A6B57", key: "green", label: "緑" },
  { hex: "#6E5A7E", key: "purple", label: "紫" },
  { hex: "#7A5C48", key: "terracotta", label: "テラコッタ" },
];

export function accentKeyForHex(hex: string): AccentKey {
  return ACCENT_SWATCHES.find((s) => s.hex === hex)?.key ?? "slate";
}

/** データ操作ジョブの状態(Task 4: エクスポート・インポート共通)。 */
export type DataJobState = {
  status: "queued" | "running" | "succeeded" | "failed";
  error?: string | null;
  summary?: ImportSummary | null;
};

/** インポートサマリ(Task 4)。 */
export type ImportSummary = Record<string, unknown>;

export type ProviderId = "openai" | "anthropic" | "google" | "deepseek" | "xai";

/** LLM 用途(plans/03 §17.1 llm_routing)。 */
export type LlmUseCase =
  | "translation"
  | "retranslation"
  | "chat"
  | "summary"
  | "article"
  | "vocab"
  | "figure_dsl"
  | "presentation"
  | "figure_image";

export type ByokProvider = ProviderId;

export interface ModelOption {
  model: string;
  label: string;
}

export type AvailableModels = Record<string, ModelOption[]>;

export interface RouteEntry {
  provider: string;
  model: string;
}

export interface LlmRouting {
  translation: RouteEntry;
  retranslation: RouteEntry;
  chat: RouteEntry;
  summary: RouteEntry;
  article: RouteEntry;
  vocab: RouteEntry;
  figure_dsl: RouteEntry;
  presentation: RouteEntry;
  figure_image: RouteEntry;
  overview_figure_raster_mode: boolean;
}

/**
 * presentation ルートで使えるプロバイダ(設計 §LLM ルーティング)。既定チェーンは
 * OpenAI と Anthropic の両方を含み、鍵の有無で片方だけになる。Web には API キー値は
 * 一切返さず、この 2 プロバイダの利用可否(available_models の有無)だけで表示を決める。
 */
export const PRESENTATION_PROVIDERS: readonly ProviderId[] = ["openai", "anthropic"];

/** GET /api/settings のレスポンス(4f 全カテゴリで参照する部分)。 */
export interface SettingsData {
  display: DisplayPrefs;
  translation: TranslationPrefs;
  reading: ReadingPrefs;
  chat: ChatPrefs;
  notifications: NotificationsPrefs;
  extension: ExtensionPrefs;
  llm_routing: LlmRouting;
  code_analysis: CodeAnalysisPrefs;
  available_models: AvailableModels;
}

export const PROVIDER_LABELS: Record<ProviderId, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  deepseek: "DeepSeek",
  xai: "xAI",
};

/** テキスト用途の許可プロバイダ(plans/03 §17.1)。 */
export const TEXT_PROVIDERS: readonly ProviderId[] = ["openai", "anthropic", "google", "deepseek"];
/** figure_image のみの許可プロバイダ。 */
export const IMAGE_PROVIDERS: readonly ProviderId[] = ["openai", "google", "xai"];
/** BYOK 登録対象の 5 プロバイダ(4f §4.7.4)。 */
export const BYOK_PROVIDERS: readonly ByokProvider[] = [
  "openai",
  "anthropic",
  "google",
  "deepseek",
  "xai",
];

/** 用途別の許可プロバイダ。 */
export function providersFor(useCase: LlmUseCase): readonly ProviderId[] {
  return useCase === "figure_image" ? IMAGE_PROVIDERS : TEXT_PROVIDERS;
}
