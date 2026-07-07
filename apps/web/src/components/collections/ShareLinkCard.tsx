"use client";

import { useState, type CSSProperties } from "react";
import { Card } from "@/components/ui/Card";
import { Modal } from "@/components/ui/Modal";
import { Toggle } from "@/components/ui/Toggle";
import { displayShareUrl } from "@/components/collections/format";
import type { ShareInfo } from "@/components/collections/types";

/** 共有リンクカード(plans/09-screens/4b §4.3.1・§5.5)。 */
export interface ShareLinkCardProps {
  share: ShareInfo;
  onIssue: () => void;
  onToggleNotes: (next: boolean) => void;
  onRevoke: () => void;
  issuing?: boolean;
}

export function ShareLinkCard({ share, onIssue, onToggleNotes, onRevoke, issuing }: ShareLinkCardProps) {
  const [copied, setCopied] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const copy = async () => {
    if (!share.url) return;
    try {
      await navigator.clipboard.writeText(share.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // クリップボード API 不可時は無視(呼び出し側の Toast は不要。決定・deviations)。
    }
  };

  return (
    <Card
      style={{
        width: 330,
        flex: "none",
        padding: "12px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 9,
      }}
    >
      <div style={{ display: "flex", gap: 7, alignItems: "center" }}>
        <span style={{ fontSize: 11.5, fontWeight: 700 }}>閲覧用共有リンク</span>
        <StatusBadge status={share.status} />
      </div>

      {share.status === "active" && share.url ? (
        <>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              border: "1px solid var(--pr-border-pane)",
              borderRadius: 6,
              padding: "6px 9px",
              background: "var(--pr-bg-app)",
            }}
          >
            <span
              style={{
                fontFamily: "var(--pr-font-mono)",
                fontSize: 10.5,
                color: "var(--pr-text-mid)",
                flex: 1,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {displayShareUrl(share.url)}
            </span>
            <button type="button" onClick={() => void copy()} style={linkButtonStyle}>
              {copied ? "コピーしました" : "コピー"}
            </button>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 10.5, color: "var(--pr-text-sub)" }}>
            <span>閲覧のみ · アカウント不要 · noindex</span>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span>共有ページにメモを含める</span>
              <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
                <Toggle
                  checked={share.include_notes}
                  onChange={onToggleNotes}
                  ariaLabel="共有ページにメモを含める"
                />
                <span style={{ color: "var(--pr-text-mid)", fontWeight: 600 }}>
                  {share.included_note_count} 件
                </span>
              </div>
            </div>
          </div>
          <div style={{ display: "flex", gap: 10, fontSize: 10.5 }}>
            <a href={`/c/${share.token}`} target="_blank" rel="noopener" style={accentLinkStyle}>
              共有ページを確認 →
            </a>
            <button
              type="button"
              onClick={() => setConfirmOpen(true)}
              style={{ ...linkButtonStyle, marginLeft: "auto", color: "var(--pr-text-muted)" }}
            >
              リンクを無効化
            </button>
          </div>
        </>
      ) : (
        <>
          <button type="button" disabled={issuing} onClick={onIssue} style={issueButtonStyle}>
            共有リンクを発行
          </button>
          <span style={{ fontSize: 10.5, color: "var(--pr-text-sub)" }}>
            閲覧のみ · アカウント不要 · noindex
          </span>
          {share.status === "revoked" ? (
            <span style={{ fontSize: 10, color: "var(--pr-text-muted)" }}>
              以前のリンクは無効です。再発行すると新しい URL になります。
            </span>
          ) : null}
        </>
      )}

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        width={380}
        labelledBy="revoke-share-title"
      >
        <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 10 }}>
          <h2 id="revoke-share-title" style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>
            共有リンクを無効化しますか?
          </h2>
          <p style={{ fontSize: 12, color: "var(--pr-text-sub)", lineHeight: 1.7, margin: 0 }}>
            リンクを知っている人は閲覧できなくなります。再発行すると新しい URL になります。
          </p>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button type="button" onClick={() => setConfirmOpen(false)} style={modalCancelStyle}>
              キャンセル
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirmOpen(false);
                onRevoke();
              }}
              style={modalConfirmStyle}
            >
              無効化する
            </button>
          </div>
        </div>
      </Modal>
    </Card>
  );
}

function StatusBadge({ status }: { status: ShareInfo["status"] }) {
  const active = status === "active";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        height: 16,
        padding: "0 6px",
        borderRadius: 3,
        fontSize: 9.5,
        fontWeight: 700,
        background: active ? "var(--pr-src-note-bg)" : "var(--pr-bg-inset)",
        color: active ? "var(--pr-src-note-fg)" : "var(--pr-text-sub2)",
      }}
    >
      {active ? "発行済み" : "未発行"}
    </span>
  );
}

const linkButtonStyle: CSSProperties = {
  border: "none",
  background: "transparent",
  fontSize: 10.5,
  fontWeight: 700,
  color: "var(--pr-acc)",
  cursor: "pointer",
  fontFamily: "inherit",
};

const accentLinkStyle: CSSProperties = {
  color: "var(--pr-acc)",
  fontWeight: 600,
  textDecoration: "none",
};

const issueButtonStyle: CSSProperties = {
  alignSelf: "flex-start",
  height: 24,
  padding: "0 12px",
  borderRadius: 6,
  border: "none",
  background: "var(--pr-acc)",
  color: "#FFFFFF",
  fontSize: 11,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};

const modalCancelStyle: CSSProperties = {
  height: 26,
  padding: "0 12px",
  border: "1px solid var(--pr-border-control)",
  background: "var(--pr-bg-control)",
  borderRadius: 6,
  fontSize: 11,
  cursor: "pointer",
  fontFamily: "inherit",
};

const modalConfirmStyle: CSSProperties = {
  height: 26,
  padding: "0 12px",
  border: "none",
  background: "var(--pr-warn)",
  color: "#FFFFFF",
  borderRadius: 6,
  fontSize: 11,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};
