import { SOURCE_TEXT_ATTR, textOffsetWithin } from "@/components/viewer/text-offset";
import type { ViewerSelection } from "@/stores/viewer-store";

/**
 * 選択 Range を注釈アンカー(ViewerSelection)へ解決する純関数。
 * side 判別の優先順位:
 *  1. block 内に [SOURCE_TEXT_ATTR](対訳ポップ内原文・未訳フォールバック)があれば 'source'
 *  2. なければ最寄り [data-block-id] 要素の data-side(対訳ペインのセル)
 *  3. それも無ければ defaultSide(原文ペイン='source' / 訳文ペイン='translation')
 * オフセットは side のテキスト内文字位置(source は SOURCE_TEXT_ATTR 要素、それ以外は block 要素基準)。
 */
export function resolveSelectionAnchor(
  range: Range,
  selectedText: string,
  defaultSide: "source" | "translation",
): ViewerSelection | null {
  let node: Node | null = range.commonAncestorContainer;
  let blockEl: HTMLElement | null = null;
  while (node) {
    if (node instanceof HTMLElement && node.dataset.blockId) {
      blockEl = node;
      break;
    }
    node = node.parentNode;
  }
  if (!blockEl) return null;

  const ancestorEl =
    range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? (range.commonAncestorContainer as Element)
      : range.commonAncestorContainer.parentElement;
  const sourceRoot = ancestorEl?.closest(`[${SOURCE_TEXT_ATTR}]`) ?? null;

  let side: "source" | "translation";
  if (sourceRoot && blockEl.contains(sourceRoot)) {
    side = "source";
  } else if (blockEl.dataset.side === "source" || blockEl.dataset.side === "translation") {
    side = blockEl.dataset.side;
  } else {
    side = defaultSide;
  }

  const offsetRoot = side === "source" && sourceRoot ? sourceRoot : blockEl;
  const start = textOffsetWithin(offsetRoot, range.startContainer, range.startOffset);
  const end = start + selectedText.length;
  const rect = range.getBoundingClientRect();
  return {
    blockId: blockEl.dataset.blockId ?? "",
    side,
    quote: selectedText.slice(0, 500),
    start,
    end,
    rect: { top: rect.top, left: rect.left, bottom: rect.bottom, right: rect.right },
    sourceFullText: side === "source" ? (offsetRoot.textContent ?? undefined) : undefined,
  };
}
