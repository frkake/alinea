"use client";

import type { CSSProperties } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  notificationsAction,
  notificationsList,
  notificationsReadAll,
  notificationsUpdate,
  type MeResponse,
  type NotificationListResponse,
  type NotificationOut,
} from "@yakudoku/api-client";
import { STATUS_LABELS, type ReadingStatus } from "@yakudoku/tokens";
import { AiMark } from "@/components/ui/AIBadge";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  formatRelativeNotificationTime,
  truncateNotificationTitle,
} from "@/components/notifications/format";
import { meQueryKey, notificationsQueryKey } from "@/components/notifications/queryKeys";

/**
 * 通知ポップオーバー(4a §3〜5)。◷ ベルの Popover に描画する本体。
 * 一覧は開くたび最新化(staleTime 0)。
 */
export interface NotificationPopoverProps {
  onClose: () => void;
}

const HEADER_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "10px 14px",
  borderBottom: "1px solid var(--pr-border-hair)",
};

const MARK_ALL_STYLE: CSSProperties = {
  marginLeft: "auto",
  fontSize: 10.5,
  color: "var(--pr-acc)",
  fontWeight: 600,
  background: "transparent",
  border: "none",
  cursor: "pointer",
  fontFamily: "inherit",
};

function suggestionCopy(payload: Record<string, unknown>): { question: string; label: string } {
  const reason = payload.reason;
  if (reason === "reached_end") {
    const status = (payload.suggested_status as ReadingStatus | undefined) ?? "done";
    return { question: "を最後まで読みました。", label: STATUS_LABELS[status] };
  }
  if (reason === "read_3min") {
    const status = (payload.suggested_status as ReadingStatus | undefined) ?? "reading";
    return { question: "を 3 分以上読んでいます。", label: STATUS_LABELS[status] };
  }
  // reason === "promotion_b_to_a"(B→A 昇格提案): 4a 計画に逐語文言が無いため
  // 暫定文言で描画する(deviations 参照。適用処理自体は M1-22 の adopt-revision 接続待ち)。
  return { question: "の高品質版(品質 A)が利用可能です。", label: "適用" };
}

export interface NotificationItemProps {
  notification: NotificationOut;
  /** 最終項目なら border-bottom を付けない(4a §4.3 の決定)。 */
  isLast: boolean;
  onNavigate: (href: string, notificationId: string) => void;
  onSuggestionAction: (notificationId: string, action: "apply" | "dismiss") => void;
}

/** 通知 1 件(kind 別 3 変種。4a §4.3)。 */
export function NotificationItem({
  notification,
  isLast,
  onNavigate,
  onSuggestionAction,
}: NotificationItemProps) {
  const payload = notification.payload;
  const unread = !notification.read;

  const rowStyle: CSSProperties = {
    display: "flex",
    gap: 10,
    padding: "10px 14px",
    background: unread ? "var(--pr-bg-unread)" : "transparent",
    borderBottom: isLast ? "none" : "1px solid var(--pr-border-row)",
  };
  const dotStyle: CSSProperties = {
    width: 7,
    height: 7,
    borderRadius: "50%",
    background: unread ? "var(--pr-acc)" : "transparent",
    flex: "none",
    marginTop: 5,
  };

  if (notification.kind === "translation_complete") {
    const title = truncateNotificationTitle(String(payload.paper_title ?? ""));
    const href = `/papers/${String(payload.library_item_id ?? "")}`;
    return (
      <div style={rowStyle}>
        <span style={dotStyle} aria-hidden="true" />
        <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
          <div style={{ fontSize: 11.5, lineHeight: 1.55 }}>
            <b>翻訳が完了しました</b> — {title}
          </div>
          <div style={{ fontSize: 10, color: "var(--pr-text-muted)" }}>
            {formatRelativeNotificationTime(notification.created_at)} ·{" "}
            <a
              href={href}
              style={{ color: "var(--pr-acc)", fontWeight: 600 }}
              onClick={(e) => {
                e.preventDefault();
                onNavigate(href, notification.id);
              }}
            >
              読み始める →
            </a>
          </div>
        </div>
      </div>
    );
  }

  if (notification.kind === "status_suggestion") {
    const title = truncateNotificationTitle(String(payload.paper_title ?? ""));
    const { question, label } = suggestionCopy(payload);
    const resolved = payload.resolved as "applied" | "dismissed" | null | undefined;
    return (
      <div style={rowStyle}>
        <span style={dotStyle} aria-hidden="true" />
        <div style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0 }}>
          <div style={{ fontSize: 11.5, lineHeight: 1.55 }}>
            <AiMark /> {title} {question}
            <b>「{label}」にしますか?</b>
          </div>
          {resolved ? (
            <div
              style={{
                fontSize: 10.5,
                fontWeight: resolved === "applied" ? 600 : 400,
                color: resolved === "applied" ? "var(--pr-green)" : "var(--pr-text-muted)",
              }}
            >
              {resolved === "applied" ? `✓ 「${label}」に変更しました` : "そのままにしました"}
            </div>
          ) : (
            <div style={{ display: "flex", gap: 6 }}>
              <button
                type="button"
                onClick={() => onSuggestionAction(notification.id, "apply")}
                style={{
                  height: 22,
                  padding: "0 10px",
                  borderRadius: 5,
                  border: "none",
                  background: "var(--pr-acc)",
                  color: "#FFFFFF",
                  fontSize: 10.5,
                  fontWeight: 600,
                  cursor: "pointer",
                  fontFamily: "inherit",
                }}
              >
                変更する
              </button>
              <button
                type="button"
                onClick={() => onSuggestionAction(notification.id, "dismiss")}
                style={{
                  height: 22,
                  padding: "0 10px",
                  borderRadius: 5,
                  border: "1px solid var(--pr-border-control)",
                  background: "transparent",
                  color: "var(--pr-text-sub)",
                  fontSize: 10.5,
                  cursor: "pointer",
                  fontFamily: "inherit",
                }}
              >
                そのまま
              </button>
            </div>
          )}
          <div style={{ fontSize: 9.5, color: "var(--pr-text-muted)" }}>
            ステータスは勝手に変わりません — 提案のみ(設定で変更可)
          </div>
        </div>
      </div>
    );
  }

  // kind === "deadline_reminder"(M2-09 の cron 実装前は発火しない。防御的に実装)。
  const name = String(payload.collection_name ?? "");
  const daysLeft = Number(payload.days_left ?? 0);
  const untouched = Number(payload.unstarted_count ?? 0);
  const href = `/collections/${String(payload.collection_id ?? "")}`;
  return (
    <div style={rowStyle}>
      <span style={dotStyle} aria-hidden="true" />
      <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
        <div style={{ fontSize: 11.5, lineHeight: 1.55, color: "var(--pr-text-mid)" }}>
          {name} の締切まで <b style={{ color: "var(--pr-warn)" }}>{daysLeft} 日</b> — 未着手{" "}
          {untouched} 本
        </div>
        <div style={{ fontSize: 10, color: "var(--pr-text-muted)" }}>
          {formatRelativeNotificationTime(notification.created_at)} ·{" "}
          <a
            href={href}
            style={{ color: "var(--pr-text-muted)" }}
            onClick={(e) => {
              e.preventDefault();
              onNavigate(href, notification.id);
            }}
          >
            コレクションを開く →
          </a>
        </div>
      </div>
    </div>
  );
}

export function NotificationPopover({ onClose }: NotificationPopoverProps) {
  const router = useRouter();
  const queryClient = useQueryClient();

  const listQuery = useQuery({
    queryKey: notificationsQueryKey,
    queryFn: async () => (await notificationsList({ throwOnError: true })).data,
    staleTime: 0,
  });

  const readAllMutation = useMutation({
    mutationFn: async () => (await notificationsReadAll({ throwOnError: true })).data,
    onSuccess: () => {
      queryClient.setQueryData<NotificationListResponse>(notificationsQueryKey, (old) =>
        old ? { ...old, items: old.items.map((n) => ({ ...n, read: true })), unread: 0 } : old,
      );
      queryClient.setQueryData<MeResponse>(meQueryKey, (old) =>
        old ? { ...old, unread_notifications: 0 } : old,
      );
    },
  });

  // 個別既読化は fire-and-forget(失敗してもロールバックしない。4a §2.2 の決定)。
  const markReadMutation = useMutation({
    mutationFn: async (notificationId: string) =>
      notificationsUpdate({ path: { notification_id: notificationId }, body: { read: true } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: meQueryKey });
    },
  });

  const actionMutation = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: "apply" | "dismiss" }) =>
      (
        await notificationsAction({
          path: { notification_id: id },
          body: { action },
          throwOnError: true,
        })
      ).data,
    onSuccess: (data) => {
      queryClient.setQueryData<NotificationListResponse>(notificationsQueryKey, (old) =>
        old
          ? {
              ...old,
              items: old.items.map((n) =>
                n.id === data.notification.id ? data.notification : n,
              ),
            }
          : old,
      );
      void queryClient.invalidateQueries({ queryKey: meQueryKey });
      if (data.library_item) {
        void queryClient.invalidateQueries({ queryKey: ["library"] });
      }
    },
  });

  const onNavigate = (href: string, notificationId: string) => {
    markReadMutation.mutate(notificationId);
    onClose();
    router.push(href);
  };

  const onSuggestionAction = (notificationId: string, action: "apply" | "dismiss") => {
    actionMutation.mutate({ id: notificationId, action });
  };

  const items = listQuery.data?.items ?? [];

  return (
    <div style={{ width: 352 }}>
      <div style={HEADER_STYLE}>
        <span style={{ fontSize: 12, fontWeight: 700 }}>通知</span>
        <button type="button" style={MARK_ALL_STYLE} onClick={() => readAllMutation.mutate()}>
          すべて既読にする
        </button>
      </div>
      <div style={{ maxHeight: 420, overflowY: "auto" }}>
        {listQuery.isPending ? (
          <div style={{ fontSize: 11.5, color: "var(--pr-text-muted)", padding: "18px 14px" }}>
            読み込み中…
          </div>
        ) : listQuery.isError ? (
          <EmptyState
            title="通知を読み込めませんでした"
            action={{ label: "再試行", onClick: () => void listQuery.refetch() }}
          />
        ) : items.length === 0 ? (
          <EmptyState title="通知はありません" />
        ) : (
          items.map((n, i) => (
            <NotificationItem
              key={n.id}
              notification={n}
              isLast={i === items.length - 1}
              onNavigate={onNavigate}
              onSuggestionAction={onSuggestionAction}
            />
          ))
        )}
      </div>
    </div>
  );
}
