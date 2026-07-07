/**
 * 設定画面(4f)の型・語彙(M0 スコープ: アカウント + 翻訳の LLM 節)。
 * GET /api/settings のレスポンスは生成 SDK では緩い型({[key:string]:unknown})のため、
 * 画面で使う部分だけをここで型付けする(API の schemas/settings.py と 1:1)。
 */

/** M0 で表示する設定カテゴリ(他カテゴリは非表示=M1)。 */
export type SettingsCategory = "account" | "translation";

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
  figure_image: RouteEntry;
  overview_figure_raster_mode: boolean;
}

/** GET /api/settings のうち M0 で参照する部分。 */
export interface SettingsData {
  llm_routing: LlmRouting;
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
