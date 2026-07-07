"use client";

import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { dashboardGet, libraryItemsSetQueueOrder, type LibraryItemSummary } from "@yakudoku/api-client";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import { ContinueReading } from "@/components/library/ContinueReading";
import { UpNextQueue } from "@/components/library/UpNextQueue";
import { RecentlyAdded } from "@/components/library/RecentlyAdded";
import { StatsPanel } from "@/components/library/StatsPanel";
import { DashboardDeadlines } from "@/components/library/DashboardDeadlines";
import { useIsMobile } from "@/hooks/useMediaQuery";

/** `GET /api/dashboard` のキー。RecentlyAdded.tsx の再試行後の invalidate と値で一致させる。 */
const DASHBOARD_QUERY_KEY = ["dashboard"] as const;

/**
 * ダッシュボード(ホーム)画面(plans/09-screens/1d-dashboard.md)。
 * ルート `/dashboard`(M1-10 でログイン後既定画面に切替。plans/13 §1.5)。
 * 締切セクションは M2-09 で有効化(API の `deadlines` が実データ化された。DashboardDeadlines.tsx)。
 */
export default function DashboardPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();
  const isMobile = useIsMobile();

  const dashboardQuery = useQuery({
    queryKey: DASHBOARD_QUERY_KEY,
    queryFn: async () => (await dashboardGet({ throwOnError: true })).data,
    staleTime: 15_000,
  });

  const reorderMutation = useMutation({
    mutationFn: async (ids: string[]) =>
      (
        await libraryItemsSetQueueOrder({
          body: { library_item_ids: ids },
          throwOnError: true,
        })
      ).data,
    onError: () => {
      toast({ kind: "error", message: "並べ替えを保存できませんでした" });
      void queryClient.invalidateQueries({ queryKey: DASHBOARD_QUERY_KEY });
    },
  });

  const openReader = (id: string) => {
    router.push(`/papers/${id}`);
  };

  const handleReorder = (nextItems: LibraryItemSummary[]) => {
    queryClient.setQueryData(DASHBOARD_QUERY_KEY, (prev: typeof dashboardQuery.data) =>
      prev ? { ...prev, up_next_queue: nextItems } : prev,
    );
    reorderMutation.mutate(nextItems.map((item) => item.id));
  };

  if (dashboardQuery.isError) {
    return (
      <div style={{ padding: "20px 26px" }}>
        <EmptyState
          title="ダッシュボードを読み込めませんでした"
          description="通信に失敗しました"
          action={{ label: "再読み込み", onClick: () => void dashboardQuery.refetch() }}
        />
      </div>
    );
  }

  if (dashboardQuery.isPending) {
    return (
      <div style={{ padding: "20px 26px", fontSize: 11.5, color: "var(--pr-text-muted)" }}>
        読み込み中…
      </div>
    );
  }

  const data = dashboardQuery.data;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 18,
        padding: isMobile ? "16px" : "20px 26px",
        height: "100%",
        minHeight: 0,
      }}
    >
      <ContinueReading items={data.continue_reading} onOpen={openReader} isMobile={isMobile} />

      {/* モバイル縮退(mobile.md §5.2): 縦積み 1 カラム(flex-direction: column)。 */}
      <div
        style={
          isMobile
            ? { display: "flex", flexDirection: "column", gap: 18, minHeight: 0, overflowY: "auto" }
            : { display: "grid", gridTemplateColumns: "1fr 340px", gap: 14, flex: 1, minHeight: 0 }
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0, minHeight: 0 }}>
          <UpNextQueue
            items={data.up_next_queue}
            onOpen={openReader}
            onReorder={handleReorder}
            onOrganize={() => {
              router.push("/library?status=up_next");
            }}
            hideReorder={isMobile}
          />
          <RecentlyAdded
            weekCount={data.recent.week_count}
            items={data.recent.items}
            onOpen={openReader}
            isMobile={isMobile}
          />
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0 }}>
          <DashboardDeadlines
            collections={data.deadlines.collections}
            items={data.deadlines.items}
            onOpenCollection={(id) => {
              router.push(`/collections/${id}`);
            }}
            onOpenItem={openReader}
          />
          <StatsPanel
            finishedCount={data.stats.week.finished_count}
            readingHours={data.stats.week.reading_hours}
            weeklyHours={data.stats.weekly_hours}
          />
        </div>
      </div>
    </div>
  );
}
