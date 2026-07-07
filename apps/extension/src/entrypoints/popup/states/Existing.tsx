// 状態3: 既にライブラリにある(3a §4.6・§5.3)。ステータスピル + 追加日/進捗 +
// 前回位置 + 「続きから開く ↗」/「ステータス変更 ▾」。ヘッダ/フッタは App が描画。
import { useState } from "react";

import { StatusDropdown } from "@/components/StatusDropdown";
import { formatAddedAt, formatLastSeen } from "@/lib/format";
import { statusColor, statusLabel, type Status } from "@/lib/status";

export interface ExistingLastPosition {
  section_display: string;
  saved_at: string;
}

export interface ExistingProps {
  status: Status;
  addedAt: string;
  progressPct: number;
  lastPosition: ExistingLastPosition | null;
  onOpen: () => void;
  /** PATCH /api/library-items/{id}。成功で true。 */
  onChangeStatus: (status: Status) => Promise<boolean>;
  now?: Date;
}

export function Existing({
  status,
  addedAt,
  progressPct,
  lastPosition,
  onOpen,
  onChangeStatus,
  now,
}: ExistingProps) {
  const [current, setCurrent] = useState<Status>(status);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSelect = async (next: Status) => {
    setOpen(false);
    const previous = current;
    setCurrent(next); // 即時更新(3a §5.3)。
    const ok = await onChangeStatus(next);
    if (!ok) {
      setCurrent(previous);
      setError("変更できませんでした");
      setTimeout(() => setError(null), 3000);
    }
  };

  return (
    <div className="ext-body">
      <div className="ext-existing-info">
        <span className="ext-status-pill">
          <span
            className="ext-status-dot"
            style={{ background: statusColor(current) }}
            aria-hidden="true"
          />
          {statusLabel(current)}
        </span>
        <span className="ext-existing-meta">
          {formatAddedAt(addedAt)} 追加 · 進捗 {progressPct}%
        </span>
        {error && <span className="ext-error-inline">{error}</span>}
      </div>

      {lastPosition && (
        <div className="ext-last-position">
          前回: {lastPosition.section_display} · {formatLastSeen(lastPosition.saved_at, now)}
        </div>
      )}

      <div className="ext-button-row">
        <button type="button" className="ext-btn ext-btn-primary" onClick={onOpen}>
          続きから開く ↗
        </button>
        <div className="ext-dropdown-anchor">
          <StatusDropdown
            open={open}
            current={current}
            onSelect={handleSelect}
            onClose={() => setOpen(false)}
          />
          <button
            type="button"
            className="ext-btn ext-btn-secondary"
            aria-haspopup="menu"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            ステータス変更 ▾
          </button>
        </div>
      </div>
    </div>
  );
}
