/**
 * リソースカードの表示整形(plans/09-screens/5a §4.8 の確定規則)。
 */

import type { ResourceLink } from "./types";

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** stars: 1,000 未満=整数そのまま、1,000 以上=(stars/1000) 小数1桁+「k」(末尾 .0 は落とす)。 */
export function formatStars(stars: number | null | undefined): string | null {
  if (stars == null) return null;
  if (stars < 1000) return String(stars);
  const k = Math.round((stars / 1000) * 10) / 10;
  const text = Number.isInteger(k) ? String(k) : k.toFixed(1);
  return `${text}k`;
}

/** updated_at(ISO 日付)→ YYYY-MM。 */
export function formatUpdatedMonth(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}`;
}

/** 「YouTube · {M:SS}」。3600 秒以上は H:MM:SS。754→「12:34」。 */
export function formatDuration(seconds: number | null | undefined): string | null {
  if (seconds == null) return null;
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${h}:${pad2(m)}:${pad2(s)}` : `${m}:${pad2(s)}`;
}

/** 記事アイコン文字: source_label の先頭 1 文字を toUpperCase()。空文字列時は「W」。 */
export function articleIconLabel(sourceLabel: string): string {
  const ch = sourceLabel.trim().charAt(0);
  return ch ? ch.toUpperCase() : "W";
}

/** メタ行(共通): source_label + kind 別項目を「 · 」で連結。null 項目はセグメントごと省略。 */
export function metaLine(resource: ResourceLink): string {
  if (!resource.meta_fetched) {
    return `${resource.source_label} · タイトル・メタ取得不可`;
  }
  const segments: string[] = [];
  switch (resource.kind) {
    case "github": {
      const meta = resource.meta as { language?: string | null; stars?: number | null; updated_at?: string | null };
      segments.push("GitHub");
      if (meta.language) segments.push(meta.language);
      const stars = formatStars(meta.stars);
      if (stars) segments.push(`★ ${stars}`);
      const updated = formatUpdatedMonth(meta.updated_at);
      if (updated) segments.push(`更新 ${updated}`);
      break;
    }
    case "youtube": {
      const meta = resource.meta as { duration_seconds?: number | null };
      segments.push("YouTube");
      const duration = formatDuration(meta.duration_seconds);
      if (duration) segments.push(duration);
      break;
    }
    case "slides": {
      const meta = resource.meta as { pages?: number | null };
      segments.push(resource.source_label);
      segments.push("PDF");
      if (meta.pages != null) segments.push(`${meta.pages} 枚`);
      break;
    }
    case "article": {
      const meta = resource.meta as { reading_minutes?: number | null };
      segments.push(resource.source_label);
      segments.push("解説記事");
      if (meta.reading_minutes != null) segments.push(`${meta.reading_minutes} min`);
      break;
    }
    default:
      break;
  }
  return segments.join(" · ");
}

/** 提案カード URL: スキーム(https://)除去して等幅表示。 */
export function stripScheme(url: string): string {
  return url.replace(/^https?:\/\//, "");
}

export interface NoteSegment {
  type: "text" | "chip";
  text: string;
  sectionId?: string;
}

const NOTE_CHIP_RE = /\[\[sec:([^|\]]+)\|([^\]]+)\]\]/g;

/** メモ本文の `[[sec:{id}|{label}]]` チップ記法をテキスト/チップのセグメント列に分解する。 */
export function parseNoteSegments(note: string): NoteSegment[] {
  const segments: NoteSegment[] = [];
  let lastIndex = 0;
  for (const m of note.matchAll(NOTE_CHIP_RE)) {
    const start = m.index ?? 0;
    if (start > lastIndex) segments.push({ type: "text", text: note.slice(lastIndex, start) });
    segments.push({ type: "chip", text: m[2] ?? "", sectionId: m[1] ?? "" });
    lastIndex = start + (m[0]?.length ?? 0);
  }
  if (lastIndex < note.length) segments.push({ type: "text", text: note.slice(lastIndex) });
  return segments;
}
