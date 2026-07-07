// タブ内 PDF 判定・書誌ローカル推定(3a §6.5・plans/10 §11.1)。
// 一次判定はサーバー(GET /api/ingest/check の kind==="pdf")。このモジュールは
// 表示補助(書誌ローカル推定)と送信前の安全確認(サイズ/マジックバイト)のみを行う純粋関数群。
// ネットワーク I/O や chrome.* API は一切呼ばない(=自動送信・自動スキャンを行わないことの型的保証)。

/** guessPdfTitle が読む最小限のタブ情報(chrome.tabs.Tab 全体には依存しない)。 */
export interface PdfTitleSource {
  title?: string | null;
  url?: string | null;
}

/**
 * 書誌ローカル推定(plans/10 §11.1 逐語)。
 * Chrome の PDF ビューアは tab.title に PDF メタデータの Title(なければファイル名)を入れる。
 * title が空、または title 自体が「*.pdf」(メタデータ無しでファイル名がそのまま入った場合)は
 * URL 末尾のファイル名にフォールバックする。フォールバックも取れなければ null(=状態4側は
 * 「(タイトル不明の PDF)」を表示する)。
 */
export function guessPdfTitle(tab: PdfTitleSource): string | null {
  const t = (tab.title ?? "").trim();
  if (t === "" || /\.pdf$/i.test(t)) {
    const m = /\/([^/?#]+)\.pdf(?:[?#]|$)/i.exec(tab.url ?? "");
    return m ? decodeURIComponent(m[1]).replace(/[_-]+/g, " ") : null;
  }
  return t;
}

/** 50MB(plans/03 §3.3・plans/10 §11.2)。クライアント側の事前拒否にも使う。 */
export const MAX_PDF_BYTES = 50 * 1024 * 1024;

const PDF_MAGIC = "%PDF-";

export type PdfValidation = { ok: true } | { ok: false; message: string };

/** 先頭 5 バイトが PDF マジックナンバーと一致するか。 */
export function hasPdfMagic(head: Uint8Array): boolean {
  if (head.length < PDF_MAGIC.length) return false;
  let s = "";
  for (let i = 0; i < PDF_MAGIC.length; i += 1) s += String.fromCharCode(head[i]);
  return s === PDF_MAGIC;
}

/**
 * Blob 先頭 N バイトの読み取り。実行時(拡張の popup/background)は標準の
 * `Blob.prototype.arrayBuffer()` を使う。Vitest(jsdom)の Blob 実装は arrayBuffer() を
 * 持たないため、その場合のみ FileReader にフォールバックする(テスト環境限定の分岐)。
 */
async function readBlobHead(blob: Blob, length: number): Promise<Uint8Array> {
  const head = blob.slice(0, length);
  if (typeof head.arrayBuffer === "function") {
    return new Uint8Array(await head.arrayBuffer());
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error ?? new Error("PDF の読み取りに失敗しました"));
    reader.readAsArrayBuffer(head);
  });
}

/**
 * 送信前のクライアント側検証(plans/10 §11.2 決定): 50MB 超・非 PDF(マジックバイト不一致)を
 * アップロード前に拒否する(413/415 相当のメッセージをサーバー往復なしで即時表示)。
 */
export async function validatePdfBlob(blob: Blob): Promise<PdfValidation> {
  if (blob.size > MAX_PDF_BYTES) {
    return { ok: false, message: "50MB を超える PDF は送信できません" };
  }
  const head = await readBlobHead(blob, 5);
  if (!hasPdfMagic(head)) {
    return { ok: false, message: "PDF として読み取れませんでした" };
  }
  return { ok: true };
}
