import { EmptyState } from "@/components/ui/EmptyState";

/**
 * ライブラリ(1e/4a)のプレースホルダ。ログイン後の既定入口(Global Constraints)。
 * 本体のテーブル/カード実装は後続タスク(M0-30 ライブラリ画面)で差し込む。
 */
export default function LibraryPage() {
  return (
    <div style={{ padding: "16px 22px" }}>
      <EmptyState
        title="ライブラリ"
        description="論文を保存するとここに一覧が表示されます。"
      />
    </div>
  );
}
