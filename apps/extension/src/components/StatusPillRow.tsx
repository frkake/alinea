// ステータス 3 択ピル(3a §4.4 項2・§5.2)。role=radiogroup の単一選択トグル。
import { SAVE_STATUS_OPTIONS, statusLabel, type SaveStatus } from "@/lib/status";

export interface StatusPillRowProps {
  value: SaveStatus;
  onChange: (value: SaveStatus) => void;
}

export function StatusPillRow({ value, onChange }: StatusPillRowProps) {
  return (
    <div className="ext-row">
      <span className="ext-row-label">ステータス</span>
      <div className="ext-pill-group" role="radiogroup" aria-label="ステータス">
        {SAVE_STATUS_OPTIONS.map((status) => {
          const selected = status === value;
          return (
            <button
              key={status}
              type="button"
              role="radio"
              aria-checked={selected}
              className={selected ? "ext-pill ext-pill-selected" : "ext-pill"}
              onClick={() => onChange(status)}
            >
              {selected && <span aria-hidden="true">✓</span>}
              {statusLabel(status)}
            </button>
          );
        })}
      </div>
    </div>
  );
}
