import { SettingsClient } from "@/components/settings/SettingsClient";
import { isSettingsCategory, type SettingsCategory } from "@/components/settings/types";

/**
 * 設定画面(4f)。?category= で 8 カテゴリを切替。
 * 省略時・不正値は account へ正規化する(4f §1 の決定。URL は書き換えない)。
 */
export default async function SettingsPage({
  searchParams,
}: {
  searchParams: Promise<{ category?: string }>;
}) {
  const { category } = await searchParams;
  const normalized: SettingsCategory = isSettingsCategory(category) ? category : "account";
  return <SettingsClient category={normalized} />;
}
