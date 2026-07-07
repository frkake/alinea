// 状態5: 非対応ページ / 通信エラー(3a §5.1)。任意で「再試行」ボタン。
// M0-34〜36 では一般ページ PDF(状態4)の送信フローは未実装のため、pdf/unsupported を
// ここに集約する(followups 参照)。

export interface UnsupportedProps {
  message?: string;
  onRetry?: () => void;
}

const DEFAULT_MESSAGE =
  "このページからは取り込めません。arXiv の論文ページ、または PDF を表示中のタブで開いてください。";

export function Unsupported({ message = DEFAULT_MESSAGE, onRetry }: UnsupportedProps) {
  return (
    <div className="ext-body ext-unsupported">
      <p className="ext-unsupported-desc">{message}</p>
      {onRetry && (
        <button type="button" className="ext-btn ext-btn-secondary ext-retry-btn" onClick={onRetry}>
          再試行
        </button>
      )}
    </div>
  );
}
