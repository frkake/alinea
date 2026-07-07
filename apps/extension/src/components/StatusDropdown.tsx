// 状態3 ステータス変更ドロップダウン(3a §5.3、plans/08 §5.2 の 6 値仕様)。
import { useEffect, useRef } from "react";

import { ALL_STATUSES, statusColor, statusLabel, type Status } from "@/lib/status";

export interface StatusDropdownProps {
  open: boolean;
  current: Status;
  onSelect: (status: Status) => void;
  onClose: () => void;
}

export function StatusDropdown({ open, current, onSelect, onClose }: StatusDropdownProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    const onClickOutside = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClickOutside);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClickOutside);
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="ext-status-menu" role="menu" ref={ref}>
      {ALL_STATUSES.map((status) => (
        <button
          key={status}
          type="button"
          role="menuitemradio"
          aria-checked={status === current}
          className="ext-status-menu-item"
          onClick={() => onSelect(status)}
        >
          <span
            className="ext-status-dot"
            style={{ background: statusColor(status) }}
            aria-hidden="true"
          />
          <span className="ext-status-menu-label">{statusLabel(status)}</span>
          {status === current && (
            <span className="ext-status-menu-check" aria-hidden="true">
              ✓
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
