import type { ReactNode } from "react";

/**
 * 設定シェル(4f)。(app) レイアウト(AppHeader/AppNav)配下で main を満たし、
 * 内部にカテゴリナビ + コンテンツ(SettingsClient)を配置する。
 */
export default function SettingsLayout({ children }: { children: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {children}
    </div>
  );
}
