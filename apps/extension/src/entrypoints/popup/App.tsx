// PopupApp: 状態ルータ + データ取得 + ポーリング(3a §2.3/§2.4/§5)。
// TanStack Query は拡張に未導入のため、React state + setTimeout で 2,000ms ポーリングする。
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { browser } from "wxt/browser";

import type {
  CollectionListItem,
  IngestCheckResponse,
  IngestRecentItem,
  JobOut,
} from "@alinea/api-client";

import { FailedQueueBanner, type FailedQueueEntry } from "@/components/FailedQueueBanner";
import { PopupHeader, type HeaderBadge } from "@/components/PopupHeader";
import {
  apiCancelIngest,
  apiCheck,
  apiGetJob,
  apiGetRecent,
  apiListCollections,
  apiMe,
  apiPatchStatus,
  apiSaveArxiv,
  apiSendPdf,
  siteUrl,
  type DuplicateExisting,
  type PdfSendMeta,
} from "@/lib/api";
import { getActiveTab, type ActiveTab } from "@/lib/e2e-hooks";
import { guessPdfTitle, validatePdfBlob } from "@/lib/pdf-detect";
import { isProcessingStage } from "@/lib/pipeline";
import { resolvePopupState } from "@/lib/popup-state";
import {
  enqueueFailedSave,
  enqueueFailedUpload,
  listFailedSaves,
  listFailedUploads,
  NETWORK_ERROR,
  removeFailedSave,
  removeFailedUpload,
  updateFailedSaveError,
  updateFailedUploadError,
  type FailedSaveRecord,
  type FailedUploadRecord,
} from "@/lib/queue";
import { addActiveJob, removeActiveJob } from "@/lib/storage";
import type { Status } from "@/lib/status";

import { RecentIngests, type RecentIngestRow } from "./RecentIngests";
import { Existing } from "./states/Existing";
import { GenericPdf } from "./states/GenericPdf";
import { Loading } from "./states/Loading";
import { Login } from "./states/Login";
import { SaveForm, type SavePayload } from "./states/SaveForm";
import { Saved } from "./states/Saved";
import { Settings } from "./states/Settings";
import { Unsupported } from "./states/Unsupported";

const POLL_MS = 2000;
/** 送信タイムアウト(タブ内 PDF fetch・POST /ingest/pdf の双方に適用。plans/10 §11.2)。 */
const PDF_SEND_TIMEOUT_MS = 120_000;

interface SavedView {
  jobId: string;
  libraryItemId: string;
  title: string;
}

function queueEvictionNotice(title: string | null): string {
  return `保存上限のため「${title ?? "(タイトル不明の PDF)"}」の再試行キューを破棄しました`;
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
  const [collections, setCollections] = useState<CollectionListItem[]>([]);

  // 状態4(一般ページ PDF・plans/10 §11.2)。
  const [pdfSending, setPdfSending] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [pdfExisting, setPdfExisting] = useState<DuplicateExisting | null>(null);

  // 送信失敗キュー(plans/10 §11.3・§11.4)。全ビュー共通のバナーで表示する。
  const [queueEntries, setQueueEntries] = useState<FailedQueueEntry[]>([]);
  const [queueNotice, setQueueNotice] = useState<string | null>(null);

  // 拡張設定(⚙。plans/10 §10.2)。
  const [showSettings, setShowSettings] = useState(false);

  const refreshQueue = useCallback(async () => {
    const [saves, uploads] = await Promise.all([listFailedSaves(), listFailedUploads()]);
    const entries: FailedQueueEntry[] = [
      ...saves.map((r) => ({ id: r.id, kind: "arxiv" as const, title: r.title, failedAt: r.failedAt, lastError: r.lastError })),
      ...uploads.map((r) => ({
        id: r.id,
        kind: "pdf" as const,
        title: r.titleGuess ?? "(タイトル不明の PDF)",
        failedAt: r.failedAt,
        lastError: r.lastError,
      })),
    ].sort((a, b) => a.failedAt - b.failedAt);
    setQueueEntries(entries);
  }, []);

  useEffect(() => {
    void refreshQueue();
  }, [refreshQueue]);

  const notifyEviction = useCallback((title: string | null) => {
    setQueueNotice(queueEvictionNotice(title));
    setTimeout(() => setQueueNotice(null), 5000);
  }, []);

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

  // 保存前フォームのコレクション選択肢(docs/10 §2 の M2 決定・ポーリング不要・1 回取得)。
  useEffect(() => {
    if (authed !== true) return;
    let cancelled = false;
    (async () => {
      const items = await apiListCollections();
      if (!cancelled) setCollections(items);
    })();
    return () => {
      cancelled = true;
    };
  }, [authed, reloadKey]);

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
      const idempotencyKey = crypto.randomUUID();
      const body = {
        url: tabInfo.url,
        status: payload.status,
        tags: payload.tags,
        quick_note: payload.quickNote || null,
        collection_id: payload.collectionId,
      };
      const outcome = await apiSaveArxiv(body, idempotencyKey);
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
        case "retryable":
          if (outcome.status === 0) {
            // ネットワーク断のみキュー対象(plans/10 §7.1 決定。5xx/429 はキューに入れない)。
            const record: FailedSaveRecord = {
              id: idempotencyKey,
              kind: "arxiv",
              request: body,
              title: check?.bib?.title ?? tabInfo.title,
              failedAt: Date.now(),
              lastError: NETWORK_ERROR,
            };
            const { evicted } = await enqueueFailedSave(record);
            await refreshQueue();
            if (evicted) notifyEviction(evicted.title);
            setSaveError("送信できませんでした。あとで再試行できます(失敗キューに保存しました)。");
          } else {
            setSaveError(
              outcome.message || "送信に失敗しました。しばらくしてからもう一度お試しください。",
            );
          }
          break;
        default:
          setSaveError(outcome.message);
      }
    },
    [tabInfo, check, refreshQueue, notifyEviction],
  );

  // 取り込みキャンセル(docs/08 §2.2)。ライブラリ項目ごと削除して保存前フォームへ戻す。
  const handleCancelIngest = useCallback(async () => {
    if (!savedView) return;
    const ok = await apiCancelIngest(savedView.libraryItemId);
    if (!ok) return;
    await removeActiveJob(savedView.jobId);
    setJob(null);
    setSavedView(null);
    setReloadKey((k) => k + 1);
  }, [savedView]);

  const handleChangeStatus = useCallback(
    (itemId: string) => (status: Status) => apiPatchStatus(itemId, status),
    [],
  );

  const pdfTitleGuess = useMemo(() => (tabInfo ? guessPdfTitle(tabInfo) : null), [tabInfo]);

  const handleSendPdf = useCallback(async () => {
    if (!tabInfo) return;
    setPdfError(null);
    setPdfSending(true);
    const idempotencyKey = crypto.randomUUID();

    let blob: Blob;
    try {
      const res = await fetch(tabInfo.url, {
        credentials: "include",
        signal: AbortSignal.timeout(PDF_SEND_TIMEOUT_MS),
      });
      blob = await res.blob();
    } catch {
      setPdfSending(false);
      setPdfError("このタブの PDF を取得できませんでした。ページを開き直してもう一度お試しください。");
      return;
    }

    const validation = await validatePdfBlob(blob);
    if (!validation.ok) {
      setPdfSending(false);
      setPdfError(validation.message);
      return;
    }

    const meta: PdfSendMeta = {
      source_url: tabInfo.url,
      title_guess: pdfTitleGuess,
      status: "planned",
      tags: [],
      collection_id: null,
      quick_note: null,
    };
    const outcome = await apiSendPdf(blob, meta, idempotencyKey);
    setPdfSending(false);
    switch (outcome.kind) {
      case "accepted":
        await addActiveJob(outcome.data.job_id);
        setSavedView({
          jobId: outcome.data.job_id,
          libraryItemId: outcome.data.library_item_id,
          title: pdfTitleGuess ?? "(タイトル不明の PDF)",
        });
        break;
      case "duplicate":
        if (outcome.existing) setPdfExisting(outcome.existing);
        break;
      case "retryable": {
        const record: FailedUploadRecord = {
          id: idempotencyKey,
          kind: "pdf",
          meta,
          blob,
          titleGuess: pdfTitleGuess,
          failedAt: Date.now(),
          lastError: NETWORK_ERROR,
        };
        const { evicted } = await enqueueFailedUpload(record);
        await refreshQueue();
        if (evicted) notifyEviction(evicted.titleGuess);
        setPdfError("送信できませんでした。失敗キューに保存しました — あとで再試行できます。");
        break;
      }
      case "permanent":
        setPdfError(outcome.message);
        break;
    }
  }, [tabInfo, pdfTitleGuess, refreshQueue, notifyEviction]);

  const handleRetryQueueEntry = useCallback(
    async (id: string, kind: "arxiv" | "pdf") => {
      if (kind === "arxiv") {
        const record = (await listFailedSaves()).find((r) => r.id === id);
        if (!record) return;
        const outcome = await apiSaveArxiv(record.request, record.id);
        if (outcome.kind === "accepted") {
          await addActiveJob(outcome.data.job_id);
          await removeFailedSave(id);
        } else if (outcome.kind === "duplicate") {
          await removeFailedSave(id);
        } else {
          await updateFailedSaveError(id, outcome.kind === "retryable" ? NETWORK_ERROR : "送信に失敗しました");
        }
      } else {
        const record = (await listFailedUploads()).find((r) => r.id === id);
        if (!record) return;
        const outcome = await apiSendPdf(record.blob, record.meta, record.id);
        if (outcome.kind === "accepted") {
          await addActiveJob(outcome.data.job_id);
          await removeFailedUpload(id);
        } else if (outcome.kind === "duplicate") {
          await removeFailedUpload(id);
        } else {
          await updateFailedUploadError(
            id,
            outcome.kind === "retryable" ? NETWORK_ERROR : outcome.message,
          );
        }
      }
      await refreshQueue();
    },
    [refreshQueue],
  );

  const handleDiscardQueueEntry = useCallback(
    async (id: string, kind: "arxiv" | "pdf") => {
      if (kind === "arxiv") await removeFailedSave(id);
      else await removeFailedUpload(id);
      await refreshQueue();
    },
    [refreshQueue],
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

  // 「⚙」は docs/08 §2 の 4 状態(保存前/保存直後/既にライブラリ/一般ページ PDF)のみに置く。
  const frame = (
    title: string,
    badge: HeaderBadge | undefined,
    body: ReactNode,
    showFooter: boolean,
    header?: { onBack?: () => void; settings?: boolean },
  ) => (
    <div className="ext-popup">
      <PopupHeader
        title={title}
        badge={badge}
        onBack={header?.onBack}
        onOpenSettings={
          header?.onBack ? undefined : header?.settings ? () => setShowSettings(true) : undefined
        }
      />
      <FailedQueueBanner
        entries={queueEntries}
        onRetry={(id, kind) => void handleRetryQueueEntry(id, kind)}
        onDiscard={(id, kind) => void handleDiscardQueueEntry(id, kind)}
        notice={queueNotice}
      />
      {body}
      {showFooter ? footer : null}
    </div>
  );

  // 設定(⚙)。全状態から開ける最上位ビュー(plans/10 §10.2)。
  if (showSettings) {
    return frame(
      "設定",
      undefined,
      <Settings
        version={browser.runtime.getManifest().version}
        onOpenSiteSettings={() => openTab(siteUrl("/settings"))}
      />,
      false,
      { onBack: () => setShowSettings(false) },
    );
  }

  const renderExisting = (item: {
    library_item_id: string;
    status: string;
    added_at: string;
    progress_pct: number;
    last_position?: { section_display: string; saved_at: string } | null;
  }) =>
    frame(
      "既にライブラリにあります",
      undefined,
      <Existing
        status={item.status as Status}
        addedAt={item.added_at}
        progressPct={item.progress_pct}
        lastPosition={
          item.last_position
            ? { section_display: item.last_position.section_display, saved_at: item.last_position.saved_at }
            : null
        }
        onOpen={() => openTab(siteUrl(`/papers/${item.library_item_id}`))}
        onChangeStatus={handleChangeStatus(item.library_item_id)}
      />,
      true,
      { settings: true },
    );

  // PDF 送信が 409 duplicate だった場合(3a §6.5)。savedView と同じ優先度で扱う。
  if (pdfExisting) {
    return renderExisting(pdfExisting);
  }

  // 通信エラー(3a §5.1)。
  if (connError) {
    return frame(
      "Alineaに保存",
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
        onCancel={handleCancelIngest}
      />,
      true,
      { settings: true },
    );
  }

  const state = resolvePopupState({ authed, check });
  switch (state) {
    case "login":
      return frame(
        "Alineaに保存",
        undefined,
        <Login onLogin={() => openTab(siteUrl("/login?from=extension"))} />,
        false,
      );
    case "saveform":
      return frame(
        "Alineaに保存",
        { kind: "detect", label: "arXiv 論文を検出" },
        <SaveForm
          preview={{
            title: check?.bib?.title ?? tabInfo?.title ?? "",
            metaLine: buildMetaLine(check),
            latexAvailable: check?.latex_available ?? null,
            suggestedTags: check?.suggested_tags ?? [],
            collections: collections.map((c) => ({ id: c.id, name: c.name })),
          }}
          onSave={handleSave}
          saving={saving}
          error={saveError}
        />,
        true,
        { settings: true },
      );
    case "existing": {
      const saved = check?.saved;
      // resolvePopupState が existing を返すのは saved != null のときのみ。
      if (!saved) return frame("Alineaに保存", undefined, <Loading />, false);
      return renderExisting(saved);
    }
    case "pdf":
      // 状態4: 一般ページ PDF(3a §6.5・plans/10 §11)。書誌は推定 + 明示クリックでのみ送信。
      return frame(
        "Alineaに保存",
        { kind: "pdf", label: "PDF を表示中" },
        <GenericPdf
          tabUrl={tabInfo?.url ?? ""}
          titleGuess={pdfTitleGuess}
          sending={pdfSending}
          error={pdfError}
          onSend={() => void handleSendPdf()}
        />,
        true,
        { settings: true },
      );
    case "unsupported":
      return frame(
        "Alineaに保存",
        { kind: "unsupported", label: "対応外のページ" },
        <Unsupported />,
        true,
      );
    case "loading":
    default:
      return frame("Alineaに保存", undefined, <Loading />, false);
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
