"use client";

import { FinishReadingDialog } from "@/components/library/FinishReadingDialog";
import { useFinishReadingStore } from "@/components/library/finishReadingStore";

/**
 * 読了フロー(1g)ダイアログのグローバル起動口(plans/09-screens/1g §3 FinishReadingDialogHost)。
 *
 * `useFinishReadingStore` を購読するだけの画面固有コンポーネント。ステータス変更 UI の
 * 呼び出し元がどこであっても(本タスクの縮小スコープではライブラリカード・通知ポップオーバー)
 * `useFinishReadingStore.getState().open(item)` が呼ばれた時点でここが開く。
 *
 * 決定(本タスクの縮小スコープ): plans/09-screens/1g §1 は `app/(app)/layout.tsx` への常駐を
 * 正としているが、本タスクの所有範囲が `components/library/`/`components/notifications/` に
 * 限られるため、常時マウントされる `components/notifications/NotificationBell.tsx`
 * (ヘッダに常駐)からこの Host を描画する(deviations 記載)。
 */
export function FinishReadingDialogHost() {
  const item = useFinishReadingStore((s) => s.item);
  const close = useFinishReadingStore((s) => s.close);

  if (!item) return null;
  return <FinishReadingDialog item={item} onClose={close} />;
}
