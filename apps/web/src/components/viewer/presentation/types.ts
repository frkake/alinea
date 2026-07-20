import type {
  PresentationArtifactOut,
  PresentationStatusResponse,
} from "@alinea/api-client";

/**
 * 論文→スライド生成ツールの Web 型・語彙(Task 30)。設計 2026-07-16 の
 * 「用途 3 種 + 想定聴衆 3 種 + 任意指示(≤500)」に対応する。色・書体・画像方針・
 * 言語は設定項目にせず、設計書の安全な既定値を worker 側で固定する。
 */

/** 生成成果物メタデータ(api-client の型をそのまま使う)。 */
export type PresentationArtifact = PresentationArtifactOut;

/** GET /presentation のレスポンス(最新成果物 + 進行中 job)。 */
export type PresentationStatus = PresentationStatusResponse;

/** 用途プリセット(API の PresentationGenerateRequest.preset と一致)。 */
export type Preset = "reading_group" | "research_talk" | "implementation";

/** 想定聴衆(worker の _AUDIENCE_LABEL キーと一致)。 */
export type Audience = "beginner" | "researcher" | "implementer";

/** 任意指示の最大文字数(API の INSTRUCTION_MAX_LEN と一致)。 */
export const INSTRUCTION_MAX_LEN = 500;

/** 用途 3 種の表示ラベル。 */
export const PRESET_OPTIONS: ReadonlyArray<{ value: Preset; label: string }> = [
  { value: "reading_group", label: "輪読会" },
  { value: "research_talk", label: "研究発表" },
  { value: "implementation", label: "実装解説" },
];

/** 想定聴衆 3 種の表示ラベル。 */
export const AUDIENCE_OPTIONS: ReadonlyArray<{ value: Audience; label: string }> = [
  { value: "beginner", label: "初学者" },
  { value: "researcher", label: "研究者" },
  { value: "implementer", label: "実装者" },
];

/**
 * 用途ごとの既定聴衆(API の PRESET_DEFAULT_AUDIENCE と揃える)。
 * API は "students"/"researchers"/"practitioners" を既定に持つが、Web は audience を
 * 明示送信するため、UI 側の 3 値(beginner/researcher/implementer)へ写像する。
 */
export const PRESET_DEFAULT_AUDIENCE: Record<Preset, Audience> = {
  reading_group: "beginner",
  research_talk: "researcher",
  implementation: "implementer",
};

/** 用途の表示ラベル(成功カードなどでの逆引き)。 */
export function presetLabel(preset: string): string {
  return PRESET_OPTIONS.find((o) => o.value === preset)?.label ?? preset;
}

/** 想定聴衆の表示ラベル(成功カードなどでの逆引き)。 */
export function audienceLabel(audience: string): string {
  return AUDIENCE_OPTIONS.find((o) => o.value === audience)?.label ?? audience;
}

/** worker の SSE stage → 日本語ラベル(設計 §ユーザー体験の 6 stage)。 */
export const STAGE_LABELS: Record<string, string> = {
  preparing_source: "論文の素材を準備しています",
  planning: "スライド構成を考えています",
  authoring_slides: "スライドを作成しています",
  validating: "スライドを検証しています",
  exporting: "PowerPoint を書き出しています",
  uploading: "生成物を保存しています",
};

/** stage を日本語ラベルへ変換する(未知 stage は総称ラベル)。 */
export function stageLabel(stage: string | null | undefined): string {
  if (stage && STAGE_LABELS[stage]) return STAGE_LABELS[stage];
  return "スライドを生成しています";
}
