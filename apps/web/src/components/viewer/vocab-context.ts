/**
 * 「語彙に追加」の文脈センテンス抽出(plans/09-screens/1b §5.5「アンカー構築」の続き)。
 *
 * deviations: `SelectionMenu.tsx` の `onAddVocab` は M2-12(語彙帳バックエンド+選択メニュー
 * UI)の所有範囲外と明記されており(同ファイルの `onAddVocab` docstring)、呼び出し側
 * (`TranslationPane.tsx` 等の `SelectionController` 相当)がこの関数で文脈センテンスと
 * ハイライト範囲を計算し `POST /api/vocab` の `context_sentence` / `highlight` を組み立てる
 * 想定だった。実装が誰にも配線されていなかった(PW-10/PW-20 の前提が成立しない)ため、
 * 本レーン(M2-17)で最小実装する。
 */
export interface VocabContext {
  contextSentence: string;
  highlightStart: number;
  highlightEnd: number;
}

/** `Intl.Segmenter` 不可・文境界検出失敗時のフォールバック窓幅(選択の前後何文字を含めるか)。 */
const FALLBACK_WINDOW = 160;

/** `text.slice(start, end)` を trim した結果と、trim 後の座標系での offset 補正値を返す。 */
function trimWithOffset(text: string): { trimmed: string; leadTrim: number } {
  const trimmed = text.trim();
  const leadTrim = text.length - text.trimStart().length;
  return { trimmed, leadTrim };
}

function clampHighlight(rawStart: number, rawEnd: number, length: number): { start: number; end: number } {
  const start = Math.max(0, Math.min(rawStart, length));
  const end = Math.max(start, Math.min(rawEnd, length));
  return { start, end };
}

/**
 * `fullText`(選択元の原文全体。対訳ポップ内原文または未訳フォールバック原文の textContent)
 * から、`[start, end)`(選択範囲。`fullText` 基準のオフセット)を含む 1 文を抜き出し、
 * その文内でのハイライト範囲を返す。`Intl.Segmenter`(文分割)が使える環境ではそれを使い、
 * 使えない・境界が見つからない場合は選択の前後 `FALLBACK_WINDOW` 文字の窓を文脈とする。
 */
export function extractVocabContext(fullText: string, start: number, end: number): VocabContext {
  const { start: safeStart, end: safeEnd } = clampHighlight(start, end, fullText.length);

  const SegmenterCtor = (Intl as unknown as { Segmenter?: typeof Intl.Segmenter }).Segmenter;
  if (SegmenterCtor) {
    const segmenter = new SegmenterCtor("en", { granularity: "sentence" });
    for (const seg of segmenter.segment(fullText)) {
      const segStart = seg.index;
      const segEnd = segStart + seg.segment.length;
      if (safeStart >= segStart && safeStart < segEnd) {
        const { trimmed, leadTrim } = trimWithOffset(seg.segment);
        const { start: hlStart, end: hlEnd } = clampHighlight(
          safeStart - segStart - leadTrim,
          safeEnd - segStart - leadTrim,
          trimmed.length,
        );
        return { contextSentence: trimmed, highlightStart: hlStart, highlightEnd: hlEnd };
      }
    }
  }

  const winStart = Math.max(0, safeStart - FALLBACK_WINDOW);
  const winEnd = Math.min(fullText.length, safeEnd + FALLBACK_WINDOW);
  const { trimmed, leadTrim } = trimWithOffset(fullText.slice(winStart, winEnd));
  const { start: hlStart, end: hlEnd } = clampHighlight(
    safeStart - winStart - leadTrim,
    safeEnd - winStart - leadTrim,
    trimmed.length,
  );
  return { contextSentence: trimmed, highlightStart: hlStart, highlightEnd: hlEnd };
}
