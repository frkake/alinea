"use client";

import { useRef, useState } from "react";
import { Popover } from "@/components/ui/Popover";
import { NotificationPopover } from "@/components/notifications/NotificationPopover";

/** ヘッダの通知ベル(◷)+未読ドット(#C49432)+ポップオーバー(4a §3〜5)。 */
export interface NotificationBellProps {
  /** `GET /api/auth/me` の `unread_notifications`。 */
  unreadCount: number;
}

export function NotificationBell({ unreadCount }: NotificationBellProps) {
  const anchorRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        aria-label="通知"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
        }}
        style={{
          position: "relative",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 30,
          height: 30,
          borderRadius: 7,
          border: "1px solid var(--pr-border-card)",
          background: "transparent",
          color: "var(--pr-text-sub)",
          fontSize: 13,
          cursor: "pointer",
          fontFamily: "inherit",
        }}
      >
        ◷
        {unreadCount > 0 ? (
          <span
            aria-hidden="true"
            style={{
              position: "absolute",
              top: 5,
              right: 5,
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "var(--pr-amber)",
            }}
          />
        ) : null}
      </button>
      <Popover
        open={open}
        onClose={() => {
          setOpen(false);
        }}
        anchorRef={anchorRef}
        width={352}
        placement="bottom-end"
        caretOffset={{ side: "right", px: 26 }}
      >
        <NotificationPopover
          onClose={() => {
            setOpen(false);
          }}
        />
      </Popover>
    </>
  );
}
