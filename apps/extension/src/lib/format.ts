// 相対時刻フォーマッタ(3a §5.6)。純粋関数。now を注入可能にしてテスト可能にする。

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

function isYesterday(d: Date, now: Date): boolean {
  const y = new Date(now);
  y.setDate(now.getDate() - 1);
  return isSameDay(d, y);
}

/** 時刻「H:MM」(H ゼロ埋めなし・MM 2桁)。例「8:02」 */
function hm(d: Date): string {
  return `${d.getHours()}:${pad2(d.getMinutes())}`;
}

/**
 * フッタ「直近の取り込み」完了時刻。
 * 当日→「今日 H:MM」/ 前日→「昨日 H:MM」/ 同年→「M/DD」/ それ以前→「YYYY/M/DD」。
 */
export function formatCompletedAt(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (isSameDay(d, now)) return `今日 ${hm(d)}`;
  if (isYesterday(d, now)) return `昨日 ${hm(d)}`;
  if (d.getFullYear() === now.getFullYear()) {
    return `${d.getMonth() + 1}/${pad2(d.getDate())}`;
  }
  return `${d.getFullYear()}/${d.getMonth() + 1}/${pad2(d.getDate())}`;
}

/**
 * 状態3 前回位置の時刻。
 * 当日→「今日 H:MM」/ 前日→「昨日 H:MM」/ それ以前→「M/DD H:MM」。
 */
export function formatLastSeen(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (isSameDay(d, now)) return `今日 ${hm(d)}`;
  if (isYesterday(d, now)) return `昨日 ${hm(d)}`;
  return `${d.getMonth() + 1}/${pad2(d.getDate())} ${hm(d)}`;
}

/** 追加日。常に「YYYY/MM/DD」(ゼロ埋め)。例「2026/07/02」 */
export function formatAddedAt(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}/${pad2(d.getMonth() + 1)}/${pad2(d.getDate())}`;
}
