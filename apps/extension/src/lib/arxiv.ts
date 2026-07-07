// arXiv URL 判定(3a §2.4・§5.1)。abs / pdf いずれのページからも arXiv ID と版を取り出す。
// 純粋関数。browser API に依存しないため単体テスト可能(VT-XTU-01)。

export interface ArxivRef {
  /** 例: "2209.03003" / 旧形式 "hep-th/9901001" */
  id: string;
  /** 版番号(数字のみ)。無指定なら null。例: "3"(v3) */
  version: string | null;
}

/**
 * arXiv の abs / pdf ページ URL から論文 ID と版を抽出する。
 * arXiv 以外・判定不能なら null。
 */
export function detectArxiv(rawUrl: string): ArxivRef | null {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return null;
  }
  const host = url.hostname.replace(/^www\./, "");
  if (host !== "arxiv.org" && host !== "export.arxiv.org") return null;

  // /abs/<id> | /pdf/<id> | /pdf/<id>.pdf(末尾スラッシュ許容)
  const m = url.pathname.match(/^\/(?:abs|pdf)\/(.+?)(?:\.pdf)?\/?$/i);
  if (!m) return null;

  const rest = m[1];
  // 版サフィックス vN(新旧 ID 共通)。
  const vm = rest.match(/v(\d+)$/);
  if (vm) {
    return { id: rest.slice(0, rest.length - vm[0].length), version: vm[1] };
  }
  return { id: rest, version: null };
}
