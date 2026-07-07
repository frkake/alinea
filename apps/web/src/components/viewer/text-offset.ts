import { SKIP_OFFSET_ATTR } from "@/components/ui/HighlightMark";

export { SKIP_OFFSET_ATTR };

/**
 * 原文(英語)を表示している要素に付けるマーカー(対訳ポップ内原文・未訳段落の原文
 * フォールバック)。選択メニューのアンカー構築(1b §5.5)で `side: 'source'` を
 * 判定するために使う(このマーカーの祖先内での選択は 'source'、それ以外は 'translation')。
 */
export const SOURCE_TEXT_ATTR = "data-yk-source-text";

/**
 * テキスト選択 → ブロック内文字オフセット変換(1b §5.5「アンカー構築」)。
 *
 * `HighlightMark` の丸数字チップ(`<button>`)のように、選択メニュー発火後に
 * DOM へ差し込まれる装飾要素はオフセット計算から除外する必要がある
 * (除外しないとハイライト作成ごとに以降の選択のオフセットがずれていく)。
 * その除外マーカーが `SKIP_OFFSET_ATTR`(定義は `HighlightMark.tsx` 側)。
 */

/**
 * `root` の子孫を文書順に辿り、テキストノードの累積長で `target`/`targetOffset`
 * (Range の start/endContainer・start/endOffset と同じ意味)までのオフセットを返す。
 * `SKIP_OFFSET_ATTR` を持つ要素は中身を辿らず・長さにも数えない。
 */
export function textOffsetWithin(root: Node, target: Node, targetOffset: number): number {
  let offset = 0;

  function isSkip(node: Node): boolean {
    return node.nodeType === Node.ELEMENT_NODE && (node as Element).hasAttribute(SKIP_OFFSET_ATTR);
  }

  function walk(node: Node): boolean {
    if (node === target) {
      offset += targetOffset;
      return true;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      offset += node.textContent?.length ?? 0;
      return false;
    }
    if (isSkip(node)) return false;
    for (const child of Array.from(node.childNodes)) {
      if (walk(child)) return true;
    }
    return false;
  }

  walk(root);
  return offset;
}
