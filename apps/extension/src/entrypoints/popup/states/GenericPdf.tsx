// 状態4: 一般ページ PDF(3a §6.5・plans/10 §11.2)。書誌は推定表示のみ・
// 「このタブの PDF を送信」の明示クリック時のみ送信する(自動送信はしない)。
// ヘッダ/フッタは App が描画する。実際のタブ内 PDF 取得・検証・アップロードも App が行い、
// このコンポーネントは表示状態(sending/error)の受け渡しに専念する。

export interface GenericPdfProps {
  tabUrl: string;
  /** lib/pdf-detect.ts の guessPdfTitle(タブから推定)。null なら「(タイトル不明の PDF)」。 */
  titleGuess: string | null;
  sending?: boolean;
  /** 検証エラー・恒久エラー・キュー投入時の案内(3a §6.5・plans/10 §11.2)。 */
  error?: string | null;
  onSend: () => void;
}

const NO_TITLE = "(タイトル不明の PDF)";

export function GenericPdf({ tabUrl, titleGuess, sending = false, error = null, onSend }: GenericPdfProps) {
  return (
    <div className="ext-body">
      <div className="ext-pdf-bib">
        <div className="ext-pdf-title-row">
          <span className="ext-pdf-title">{titleGuess ?? NO_TITLE}</span>
          <span className="ext-badge-inline">書誌は推定</span>
        </div>
        <div className="ext-pdf-url" title={tabUrl}>
          {tabUrl}
        </div>
      </div>

      <div className="ext-warnbox">
        このページはサーバーから取得できない可能性があります(学内ネットワーク等)。ボタンを押したときだけ、このタブの
        PDF を直接送信します — 自動送信はしません。
      </div>

      <button
        type="button"
        className="ext-btn ext-btn-pdf-send"
        onClick={onSend}
        disabled={sending}
      >
        {sending ? "送信中…" : "このタブの PDF を送信"}
      </button>

      {error && <div className="ext-warnbox ext-warnbox-error">{error}</div>}

      <div className="ext-privacy-note ext-privacy-note-tight">
        private 論文として保存され、共有されません
      </div>
    </div>
  );
}
