// 送信失敗キューの再試行バナー(plans/10 §11.4・docs/08 §6)。全ビュー共通で本文最上部に表示。
// 折りたたみ: 「送信できなかった保存が {n} 件あります」。展開: 各行に再試行/破棄。
import { useState } from "react";

import { formatCompletedAt } from "@/lib/format";
import { describeQueueError } from "@/lib/queue";

export interface FailedQueueEntry {
  id: string;
  kind: "arxiv" | "pdf";
  title: string;
  failedAt: number;
  lastError: string;
}

export interface FailedQueueBannerProps {
  entries: FailedQueueEntry[];
  onRetry: (id: string, kind: "arxiv" | "pdf") => void;
  onDiscard: (id: string, kind: "arxiv" | "pdf") => void;
  /** 上限超過で破棄した件の通知(黙って捨てない。plans/10 §11.3 決定)。 */
  notice?: string | null;
  now?: Date;
}

export function FailedQueueBanner({ entries, onRetry, onDiscard, notice, now }: FailedQueueBannerProps) {
  const [open, setOpen] = useState(false);

  if (entries.length === 0) {
    return notice ? <div className="ext-warnbox ext-queue-notice">{notice}</div> : null;
  }

  return (
    <div className="ext-queue-banner">
      {notice && <div className="ext-warnbox ext-queue-notice">{notice}</div>}
      <button
        type="button"
        className="ext-warnbox ext-queue-summary"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        送信できなかった保存が {entries.length} 件あります
      </button>
      {open && (
        <div className="ext-queue-list">
          {entries.map((entry) => (
            <div key={entry.id} className="ext-queue-row">
              <div className="ext-queue-row-main">
                <span className="ext-queue-title">{entry.title}</span>
                <span className="ext-queue-time">
                  {formatCompletedAt(new Date(entry.failedAt).toISOString(), now)}
                </span>
              </div>
              <div className="ext-queue-row-actions">
                <button type="button" className="ext-queue-retry" onClick={() => onRetry(entry.id, entry.kind)}>
                  再試行
                </button>
                <button
                  type="button"
                  className="ext-queue-discard"
                  onClick={() => onDiscard(entry.id, entry.kind)}
                >
                  破棄
                </button>
              </div>
              <div className="ext-queue-error">{describeQueueError(entry.lastError)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
