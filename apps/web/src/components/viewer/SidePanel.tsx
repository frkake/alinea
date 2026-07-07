"use client";

import type { ReactNode } from "react";
import { SidePanelTabs, type SidePanelTabId } from "@/components/ui/SidePanelTabs";
import { EmptyState } from "@/components/ui/EmptyState";
import { useViewerStore } from "@/stores/viewer-store";

/** M0 のサイドパネルは チャット / 図表 / 情報 の 3 タブのみ(plans/13 §1.5・未実装 UI は非表示)。 */
const M0_TABS: readonly SidePanelTabId[] = ["chat", "figures", "info"];

export interface SidePanelProps {
  milestone?: "M0";
  /** 件数バッジ(注釈・リソースのみ。M0 タブには出さない)。 */
  counts?: Partial<Record<SidePanelTabId, number>>;
  /** タブ本体。各画面ファイル担当(1a chat / 1c figures / 2a info)。未指定はプレースホルダ。 */
  renderTab?: (tab: SidePanelTabId) => ReactNode;
}

/**
 * サイドパネル枠(viewer-shell §6)。排他タブ・幅・開閉を所有。
 * タブ本体(ChatTab/FiguresTab/InfoTab)は各画面ファイル担当のため、
 * 未供給時はプレースホルダを描画する(M0 シェルの責務は枠まで)。
 */
export function SidePanel({ milestone = "M0", counts = {}, renderTab }: SidePanelProps) {
  const panelOpen = useViewerStore((s) => s.panelOpen);
  const storeTab = useViewerStore((s) => s.activeTab);
  const setPanel = useViewerStore((s) => s.setPanel);

  if (!panelOpen) return null;

  const active = M0_TABS.includes(storeTab) ? storeTab : "chat";
  // 注釈タブのみ 320px(viewer-shell §6.2)。M0 は注釈タブ非表示のため常に 340px。
  const width = active === "annotations" ? 320 : 340;

  return (
    <aside
      data-milestone={milestone}
      style={{
        width,
        flex: "none",
        background: "var(--pr-bg-card)",
        borderLeft: "1px solid var(--pr-border-pane)",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      <SidePanelTabs
        active={active}
        counts={counts}
        tabs={M0_TABS}
        onChange={(tab) => {
          // アクティブタブ再クリックで閉じる(viewer-shell §6.4)。
          if (tab === active) setPanel(false, tab);
          else setPanel(true, tab);
        }}
      />
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
        {renderTab ? renderTab(active) : <TabPlaceholder tab={active} />}
      </div>
    </aside>
  );
}

function TabPlaceholder({ tab }: { tab: SidePanelTabId }) {
  const copy: Record<SidePanelTabId, { title: string; description: string }> = {
    chat: { title: "チャット", description: "本文を選択して質問できます。" },
    figures: { title: "図表", description: "この論文の図表と参考文献を表示します。" },
    info: { title: "情報", description: "書誌・ライセンス・処理ログを表示します。" },
    notes: { title: "メモ", description: "" },
    annotations: { title: "注釈", description: "" },
    resources: { title: "リソース", description: "" },
  };
  const c = copy[tab];
  return (
    <div style={{ padding: 16 }}>
      <EmptyState title={c.title} description={c.description} />
    </div>
  );
}
