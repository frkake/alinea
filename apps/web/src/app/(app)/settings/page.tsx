import { SettingsClient } from "@/components/settings/SettingsClient";
import type { SettingsCategory } from "@/components/settings/types";

/**
 * 設定画面(4f、M0 スコープ)。?category= で account / translation を切替。
 * 省略・M0 未対応カテゴリ(export など)は account へ正規化(4f §1 の決定)。
 */
export default async function SettingsPage({
  searchParams,
}: {
  searchParams: Promise<{ category?: string }>;
}) {
  const { category } = await searchParams;
  const normalized: SettingsCategory = category === "translation" ? "translation" : "account";
  return <SettingsClient category={normalized} />;
}
