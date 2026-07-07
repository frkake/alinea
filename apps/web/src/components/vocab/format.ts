import type { VocabSrs } from "@yakudoku/api-client";

/** 同日 0 時起点での日数差(target が起点、base が比較対象)。plans/09-screens/4d §5.6。 */
function daysBetween(target: Date, base: Date): number {
  const startTarget = new Date(target.getFullYear(), target.getMonth(), target.getDate()).getTime();
  const startBase = new Date(base.getFullYear(), base.getMonth(), base.getDate()).getTime();
  return Math.round((startBase - startTarget) / 86_400_000);
}

function absoluteDate(d: Date, now: Date): string {
  if (d.getFullYear() !== now.getFullYear()) {
    return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
  }
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** 一覧「追加」列(4d §5.6): 今日/昨日/{n}日前/{M}/{D}/{YYYY}/{M}/{D}。 */
export function formatAddedRelative(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const diff = daysBetween(d, now); // 経過日数(過去=正)
  if (diff <= 0) return "今日";
  if (diff === 1) return "昨日";
  if (diff <= 6) return `${diff}日前`;
  return absoluteDate(d, now);
}

/** 「次の復習」の日付部分(4d §5.6): 今日/明日/{n}日後/{M}/{D}/{YYYY}/{M}/{D}。 */
export function formatNextReviewDate(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const diff = daysBetween(now, d); // 残り日数(未来=正)
  if (diff <= 0) return "今日";
  if (diff === 1) return "明日";
  if (diff <= 6) return `${diff}日後`;
  return absoluteDate(d, now);
}

/** 「次の復習: 明日(2 回目)」/ 習得済み文言(4d §5.6)。 */
export function formatNextReviewDisplay(srs: VocabSrs, now: Date = new Date()): string {
  if (srs.next_review_at === null) {
    return "習得済み — 復習キューから外れています";
  }
  const dateLabel = formatNextReviewDate(srs.next_review_at, now);
  return `次の復習: ${dateLabel}(${srs.review_count + 1} 回目)`;
}

/**
 * 「解釈のしかた」1 行目の括弧見出し補足を切り出す(4d §4.2.6-3)。
 * 1 行目が「(」始まり「)」終わりならそれを見出し補足に、本文は 2 行目以降。
 * それ以外は見出し補足なし・全文が本文。
 */
export function parseInterpretation(raw: string): { headingSuffix: string | null; body: string } {
  const idx = raw.indexOf("\n");
  const firstLine = idx === -1 ? raw : raw.slice(0, idx);
  const rest = idx === -1 ? "" : raw.slice(idx + 1);
  if (firstLine.startsWith("(") && firstLine.endsWith(")") && firstLine.length >= 2) {
    return { headingSuffix: firstLine, body: rest };
  }
  return { headingSuffix: null, body: raw };
}

/**
 * 詳細ヘッダ 2 行目のメタ文言(4d §4.2.6): 「{分類ラベル} · {出典(中黒→半角スペース)} で追加 · 」。
 */
export function formatDetailMetaLine(
  posLabel: string | null,
  kindLabel: string,
  sourceDisplay: string,
): string {
  const label = posLabel ?? kindLabel;
  const cleanedDisplay = sourceDisplay.split(" · ").join(" ");
  return `${label} · ${cleanedDisplay} で追加 · `;
}
