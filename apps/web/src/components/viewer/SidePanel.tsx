"use client";

import type { ReactNode } from "react";
import { SidePanelTabs, type SidePanelTabId } from "@/components/ui/SidePanelTabs";
import { EmptyState } from "@/components/ui/EmptyState";
import { useViewerStore } from "@/stores/viewer-store";
import { AnnotationListPanel } from "@/components/viewer/AnnotationListPanel";
import { NotesPanel } from "@/components/viewer/NotesPanel";

/** M0 のサイドパネルは チャット / 図表 / 情報 の 3 タブのみ(plans/13 §1.5・未実装 UI は非表示)。 */
const M0_TABS: readonly SidePanelTabId[] = ["chat", "figures", "info"];

/**
 * M1 で メモ・注釈 タブを追加(plans/13 §3.2 M1-03 / M1-04。リソース = M2 まで非表示)。
 */
const M1_TABS: readonly SidePanelTabId[] = ["chat", "notes", "annotations", "figures", "info"];

export interface SidePanelProps {
  milestone?: "M0" | "M1";
  /** 件数バッジ(注釈・リソースのみ。M0 タブには出さない)。 */
  counts?: Partial<Record<SidePanelTabId, number>>;
  /**
   * タブ本体。各画面ファイル担当(1a chat / 1c figures / 2a info)。未指定はプレースホルダ。
   * notes / annotations は本コンポーネントが直接描画する(viewer-shell §6.5: props なし契約)ため、
   * この callback は対象外(呼ばれない)。
   */
  renderTab?: (tab: SidePanelTabId) => ReactNode;
}

/**
 * サイドパネル枠(viewer-shell §6)。排他タブ・幅・開閉を所有。
 * タブ本体(ChatTab/FiguresTab/InfoTab)は各画面ファイル担当のため、
 * 未供給時はプレースホルダを描画する(M0 シェルの責務は枠まで)。
 * notes/annotations タブは本レーン所有の自己完結コンポーネント(props なし)を直接マウントする。
 */
export function SidePanel({ milestone = "M0", counts = {}, renderTab }: SidePanelProps) {
  const panelOpen = useViewerStore((s) => s.panelOpen);
  const storeTab = useViewerStore((s) => s.activeTab);
  const setPanel = useViewerStore((s) => s.setPanel);

  if (!panelOpen) return null;

  const tabs = milestone === "M1" ? M1_TABS : M0_TABS;
  const active = tabs.includes(storeTab) ? storeTab : "chat";
  // 注釈タブのみ 320px(viewer-shell §6.2)。他タブは 340px。
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
        tabs={tabs}
        onChange={(tab) => {
          // アクティブタブ再クリックで閉じる(viewer-shell §6.4)。
          if (tab === active) setPanel(false, tab);
          else setPanel(true, tab);
        }}
      />
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
        {active === "annotations" ? (
          <AnnotationListPanel />
        ) : active === "notes" ? (
          <NotesPanel />
        ) : renderTab ? (
          renderTab(active)
        ) : (
          <TabPlaceholder tab={active} />
        )}
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
