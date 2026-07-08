"use client";

import type { ReactNode } from "react";
import { SidePanelTabs, type SidePanelTabId } from "@/components/ui/SidePanelTabs";
import { CountBadge } from "@/components/ui/CountBadge";
import { EmptyState } from "@/components/ui/EmptyState";
import { useViewerStore } from "@/stores/viewer-store";
import { AnnotationListPanel } from "@/components/viewer/AnnotationListPanel";
import { NotesPanel } from "@/components/viewer/NotesPanel";
import { ResourcesPanel } from "@/components/viewer/ResourcesPanel";

/** M0 のサイドパネルは チャット / 図表 / 情報 の 3 タブのみ(plans/13 §1.5・未実装 UI は非表示)。 */
const M0_TABS: readonly SidePanelTabId[] = ["chat", "figures", "info"];

/**
 * M1 で メモ・注釈 タブを追加(plans/13 §3.2 M1-03 / M1-04。リソース = M2 まで非表示)。
 */
const M1_TABS: readonly SidePanelTabId[] = ["chat", "notes", "annotations", "figures", "info"];

/** M2 で リソース タブを追加(plans/13 §4 M2-13。docs/12・plans/09-screens/5a)。 */
const M2_TABS: readonly SidePanelTabId[] = [
  "chat",
  "notes",
  "annotations",
  "figures",
  "resources",
  "info",
];

const TAB_LABELS: Record<SidePanelTabId, string> = {
  chat: "チャット",
  notes: "メモ",
  annotations: "注釈",
  figures: "図表",
  resources: "リソース",
  info: "情報",
};

export interface SidePanelProps {
  milestone?: "M0" | "M1" | "M2";
  /** 件数バッジ(注釈・リソースのみ。M0 タブには出さない)。 */
  counts?: Partial<Record<SidePanelTabId, number>>;
  /**
   * タブ本体。各画面ファイル担当(1a chat / 1c figures / 2a info)。未指定はプレースホルダ。
   * notes / annotations / resources は本コンポーネントが直接描画する(viewer-shell §6.5:
   * props なし契約)ため、この callback は対象外(呼ばれない)。
   */
  renderTab?: (tab: SidePanelTabId) => ReactNode;
}

/**
 * サイドパネル枠(viewer-shell §6)。排他タブ・幅・開閉を所有。
 * タブ本体(ChatTab/FiguresTab/InfoTab)は各画面ファイル担当のため、
 * 未供給時はプレースホルダを描画する(M0 シェルの責務は枠まで)。
 * notes/annotations/resources タブは本レーン所有の自己完結コンポーネント(props なし)を直接マウントする。
 */
export function SidePanel({ milestone = "M0", counts = {}, renderTab }: SidePanelProps) {
  const panelOpen = useViewerStore((s) => s.panelOpen);
  const storeTab = useViewerStore((s) => s.activeTab);
  const setPanel = useViewerStore((s) => s.setPanel);

  const tabs = milestone === "M2" ? M2_TABS : milestone === "M1" ? M1_TABS : M0_TABS;
  const active = tabs.includes(storeTab) ? storeTab : "chat";

  if (!panelOpen) {
    return (
      <SidePanelRail
        active={active}
        counts={counts}
        tabs={tabs}
        onOpen={(tab) => setPanel(true, tab)}
      />
    );
  }

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
      <div
        style={{
          display: "flex",
          alignItems: "stretch",
          borderBottom: "1px solid var(--pr-border-soft)",
          minWidth: 0,
        }}
      >
        <div style={{ flex: 1, minWidth: 0, overflowX: "auto" }}>
          <SidePanelTabs
            active={active}
            counts={counts}
            tabs={tabs}
            borderBottom={false}
            onChange={(tab) => {
              // アクティブタブ再クリックで閉じる(viewer-shell §6.4)。
              if (tab === active) setPanel(false, tab);
              else setPanel(true, tab);
            }}
          />
        </div>
        <button
          type="button"
          aria-label="サイドパネルを折りたたむ"
          title="サイドパネルを折りたたむ"
          onClick={() => setPanel(false, active)}
          style={{
            width: 34,
            flex: "none",
            border: "none",
            borderLeft: "1px solid var(--pr-border-soft)",
            background: "transparent",
            color: "var(--pr-text-sub)",
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: 13,
          }}
        >
          ⟩
        </button>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
        {active === "annotations" ? (
          <AnnotationListPanel />
        ) : active === "notes" ? (
          <NotesPanel />
        ) : active === "resources" ? (
          <ResourcesPanel />
        ) : renderTab ? (
          renderTab(active)
        ) : (
          <TabPlaceholder tab={active} />
        )}
      </div>
    </aside>
  );
}

function SidePanelRail({
  active,
  counts,
  tabs,
  onOpen,
}: {
  active: SidePanelTabId;
  counts: Partial<Record<SidePanelTabId, number>>;
  tabs: readonly SidePanelTabId[];
  onOpen: (tab: SidePanelTabId) => void;
}) {
  return (
    <aside
      aria-label="サイドパネル"
      style={{
        width: 44,
        flex: "none",
        background: "var(--pr-bg-card)",
        borderLeft: "1px solid var(--pr-border-pane)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 8,
        padding: "10px 0",
        minHeight: 0,
      }}
    >
      <button
        type="button"
        aria-label="サイドパネルを開く"
        title="サイドパネルを開く"
        onClick={() => onOpen(active)}
        style={{
          width: 28,
          height: 28,
          borderRadius: 6,
          border: "1px solid var(--pr-border-control)",
          background: "var(--pr-bg-inset)",
          color: "var(--pr-text-sub)",
          cursor: "pointer",
          fontFamily: "inherit",
          fontSize: 13,
        }}
      >
        ⟨
      </button>
      <div
        role="tablist"
        aria-label="サイドパネルタブ"
        style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4, minWidth: 0 }}
      >
        {tabs.map((tab) => {
          const isActive = tab === active;
          const count = counts[tab];
          return (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={isActive}
              aria-label={`${TAB_LABELS[tab]}を開く`}
              title={TAB_LABELS[tab]}
              onClick={() => onOpen(tab)}
              style={{
                position: "relative",
                width: 30,
                height: 28,
                borderRadius: 6,
                border: "none",
                background: isActive ? "var(--pr-acc-s)" : "transparent",
                color: isActive ? "var(--pr-acc)" : "var(--pr-text-sub2)",
                boxShadow: isActive ? "inset -2px 0 var(--pr-acc)" : undefined,
                cursor: "pointer",
                fontFamily: "inherit",
                fontSize: 11,
                fontWeight: isActive ? 700 : 600,
                padding: 0,
              }}
            >
              {TAB_LABELS[tab].slice(0, 1)}
              {typeof count === "number" && count > 0 ? (
                <span style={{ position: "absolute", top: -5, right: -5 }}>
                  <CountBadge count={count} variant="tab" />
                </span>
              ) : null}
            </button>
          );
        })}
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
