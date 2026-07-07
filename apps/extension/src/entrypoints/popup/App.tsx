// PopupApp: 状態ルータ + データ取得 + ポーリング(3a §2.3/§2.4/§5)。
// TanStack Query は拡張に未導入のため、React state + setTimeout で 2,000ms ポーリングする。
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { browser } from "wxt/browser";

import type { IngestCheckResponse, IngestRecentItem, JobOut } from "@yakudoku/api-client";

import { PopupHeader, type HeaderBadge } from "@/components/PopupHeader";
import {
  apiCheck,
  apiGetJob,
  apiGetRecent,
  apiMe,
  apiPatchStatus,
  apiSaveArxiv,
  siteUrl,
} from "@/lib/api";
import { getActiveTab, type ActiveTab } from "@/lib/e2e-hooks";
import { isProcessingStage } from "@/lib/pipeline";
import { resolvePopupState } from "@/lib/popup-state";
import { addActiveJob, removeActiveJob } from "@/lib/storage";
import type { Status } from "@/lib/status";

import { RecentIngests, type RecentIngestRow } from "./RecentIngests";
import { Existing } from "./states/Existing";
import { Loading } from "./states/Loading";
import { Login } from "./states/Login";
import { SaveForm, type SavePayload } from "./states/SaveForm";
import { Saved } from "./states/Saved";
import { Unsupported } from "./states/Unsupported";

const POLL_MS = 2000;

interface SavedView {
  jobId: string;
  libraryItemId: string;
  title: string;
}

export function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [check, setCheck] = useState<IngestCheckResponse | null>(null);
  const [tabInfo, setTabInfo] = useState<ActiveTab | null>(null);
  const [connError, setConnError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedView, setSavedView] = useState<SavedView | null>(null);
  const [job, setJob] = useState<JobOut | null>(null);
  const [recent, setRecent] = useState<IngestRecentItem[]>([]);

  // 初期化 / 再試行: タブ URL → me → check(3a §2.4)。
  useEffect(() => {
    let cancelled = false;
    setConnError(false);
    (async () => {
      try {
        const tab = await getActiveTab();
        if (cancelled) return;
        setTabInfo(tab);
        const me = await apiMe();
        if (cancelled) return;
        if (me === null) {
          setAuthed(false);
          return;
        }
        setAuthed(true);
        const result = await apiCheck(tab.url);
        if (!cancelled) setCheck(result);
      } catch {
        if (!cancelled) setConnError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  // フッタ「直近の取り込み」ポーリング(処理中行がある間のみ・§2.2)。
  useEffect(() => {
    if (authed !== true) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      const items = await apiGetRecent(3);
      if (stopped) return;
      setRecent(items);
      if (items.some((item) => isProcessingStage(item.pipeline.stage))) {
        timer = setTimeout(tick, POLL_MS);
      }
    };
    void tick();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [authed, reloadKey, savedView]);

  // 状態2 ジョブ進捗ポーリング(succeeded/failed で停止・§2.2)。
  useEffect(() => {
    if (!savedView) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const next = await apiGetJob(savedView.jobId);
        if (stopped) return;
        setJob(next);
        if (next.status === "succeeded" || next.status === "failed") {
          await removeActiveJob(savedView.jobId);
          return;
        }
      } catch {
        /* 一時的な失敗はポーリング継続 */
      }
      if (!stopped) timer = setTimeout(tick, POLL_MS);
    };
    void tick();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [savedView]);

  const openTab = useCallback((url: string) => {
    void browser.tabs.create({ url });
    window.close();
  }, []);

  const handleSave = useCallback(
    async (payload: SavePayload) => {
      if (!tabInfo) return;
      setSaving(true);
      setSaveError(null);
      const outcome = await apiSaveArxiv({
        url: tabInfo.url,
        status: payload.status,
        tags: payload.tags,
        quick_note: payload.quickNote || null,
        collection_id: null,
      });
      setSaving(false);
      switch (outcome.kind) {
        case "accepted":
          await addActiveJob(outcome.data.job_id);
          setSavedView({
            jobId: outcome.data.job_id,
            libraryItemId: outcome.data.library_item_id,
            title: check?.bib?.title ?? tabInfo.title,
          });
          break;
        case "duplicate":
          // 重複 → check を取り直して状態3(既にライブラリ)を表示(3a §2.4)。
          setSavedView(null);
          setReloadKey((k) => k + 1);
          break;
        default:
          // 再試行キューは M0-34〜36 では未実装(followups)。
          setSaveError("送信に失敗しました");
      }
    },
    [tabInfo, check],
  );

  const handleChangeStatus = useCallback(
    (itemId: string) => (status: Status) => apiPatchStatus(itemId, status),
    [],
  );

  const recentRows: RecentIngestRow[] = useMemo(
    () =>
      recent.map((item) => ({
        libraryItemId: item.library_item_id,
        title: item.title,
        pipeline: { stage: item.pipeline.stage, progress_pct: item.pipeline.progress_pct },
        completedAt: item.completed_at ?? null,
        viewerUrl: item.viewer_url,
      })),
    [recent],
  );

  const footer =
    authed === true ? (
      <RecentIngests items={recentRows} onOpen={(url) => openTab(siteUrl(url))} />
    ) : null;

  const frame = (title: string, badge: HeaderBadge | undefined, body: ReactNode, showFooter: boolean) => (
    <div className="ext-popup">
      <PopupHeader title={title} badge={badge} />
      {body}
      {showFooter ? footer : null}
    </div>
  );

  // 通信エラー(3a §5.1)。
  if (connError) {
    return frame(
      "訳読に保存",
      undefined,
      <Unsupported
        message="サーバーに接続できません。ネットワークを確認して開き直してください。"
        onRetry={() => setReloadKey((k) => k + 1)}
      />,
      false,
    );
  }

  // 保存直後(状態2)。resolvePopupState より優先。
  if (savedView) {
    return frame(
      "保存しました",
      { kind: "success" },
      <Saved
        title={savedView.title}
        stage={job?.stage ?? "queued"}
        progressPct={job?.progress_pct ?? 0}
        failedReason={typeof job?.error?.detail === "string" ? job.error.detail : null}
        onOpen={() => openTab(siteUrl(`/papers/${savedView.libraryItemId}`))}
        onClose={() => window.close()}
      />,
      true,
    );
  }

  const state = resolvePopupState({ authed, check });
  switch (state) {
    case "login":
      return frame(
        "訳読に保存",
        undefined,
        <Login onLogin={() => openTab(siteUrl("/login?from=extension"))} />,
        false,
      );
    case "saveform":
      return frame(
        "訳読に保存",
        { kind: "detect", label: "arXiv 論文を検出" },
        <SaveForm
          preview={{
            title: check?.bib?.title ?? tabInfo?.title ?? "",
            metaLine: buildMetaLine(check),
            latexAvailable: check?.latex_available ?? null,
            suggestedTags: check?.suggested_tags ?? [],
          }}
          onSave={handleSave}
          saving={saving}
          error={saveError}
        />,
        true,
      );
    case "existing": {
      const saved = check?.saved;
      // resolvePopupState が existing を返すのは saved != null のときのみ。
      if (!saved) return frame("訳読に保存", undefined, <Loading />, false);
      return frame(
        "既にライブラリにあります",
        undefined,
        <Existing
          status={saved.status as Status}
          addedAt={saved.added_at}
          progressPct={saved.progress_pct}
          lastPosition={
            saved.last_position
              ? {
                  section_display: saved.last_position.section_display,
                  saved_at: saved.last_position.saved_at,
                }
              : null
          }
          onOpen={() => openTab(siteUrl(`/papers/${saved.library_item_id}`))}
          onChangeStatus={handleChangeStatus(saved.library_item_id)}
        />,
        true,
      );
    }
    case "pdf":
      // 状態4(一般ページ PDF 送信)は M0-34〜36 では未実装(followups)。
      return frame(
        "訳読に保存",
        { kind: "pdf", label: "PDF を表示中" },
        <Unsupported message="この拡張の現バージョンでは、一般ページの PDF 送信に未対応です。arXiv の論文ページで開いてください。" />,
        true,
      );
    case "unsupported":
      return frame(
        "訳読に保存",
        { kind: "unsupported", label: "対応外のページ" },
        <Unsupported />,
        true,
      );
    case "loading":
    default:
      return frame("訳読に保存", undefined, <Loading />, false);
  }
}

/**
 * 書誌メタ行(3a §4.4 項1)。「authors_short · venue year · arXiv:{id} v{ver}」。
 * venue/year の欠落は「 · 」ごと省略、version 欠落は「 v…」を省略。
 */
function buildMetaLine(check: IngestCheckResponse | null): string | null {
  if (!check?.bib) return null;
  const parts: string[] = [check.bib.authors_short];
  const venueYear = [check.bib.venue, check.bib.year].filter(Boolean).join(" ");
  if (venueYear) parts.push(venueYear);
  if (check.arxiv_id) {
    parts.push(`arXiv:${check.arxiv_id}${check.arxiv_version ? ` ${check.arxiv_version}` : ""}`);
  }
  return parts.join(" · ");
}
