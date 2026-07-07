/**
 * 通知関連の TanStack Query キー(AppHeader / NotificationBell / NotificationPopover で共有)。
 * 文字列リテラル直書きを避け、キャッシュ無効化の対象を 1 箇所で揃える。
 */
export const meQueryKey = ["me"] as const;
export const notificationsQueryKey = ["notifications"] as const;
