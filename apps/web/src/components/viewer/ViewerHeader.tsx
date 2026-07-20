"use client";

import { useCallback, useRef, useState, type CSSProperties } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { ReadingStatus } from "@alinea/tokens";
import { QualityBadge } from "@/components/ui/QualityBadge";
import { StatusPill } from "@/components/ui/StatusPill";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Popover } from "@/components/ui/Popover";
import { useToast } from "@/components/ui/Toast";
import { useViewerStore, type TranslationStyle } from "@/stores/viewer-store";
import type { ViewerMode } from "@/components/viewer/ViewerShell";
import { InPaperSearch } from "@/components/viewer/InPaperSearch";
import { ArticleRegenerateButton } from "@/components/viewer/article/ArticleRegenerateButton";
import { PaperExportModal } from "@/components/viewer/PaperExportModal";

/** 表示モードの 5 タブ(plans/13 §1.5・M2-07 で「記事」を追加)。 */
export const MODE_OPTIONS = [
  { value: "translation", label: "訳文" },
  { value: "parallel", label: "対訳" },
  { value: "source", label: "原文" },
  { value: "pdf", label: "PDF" },
  { value: "article", label: "記事" },
] as const satisfies ReadonlyArray<{ value: ViewerMode; label: string }>;

const STYLE_LABELS: Record<TranslationStyle, string> = {
  natural: "自然訳",
  literal: "直訳",
  easy: "やさしい訳",
};

export interface ViewerHeaderProps {
  /** `ArticleRegenerateButton`(mode=article のみ)の記事取得・再生成 API 呼び出しに使う。 */
  itemId: string;
  title: string;
  qualityLevel: "A" | "B";
  status: ReadingStatus;
  mode: ViewerMode;
  onModeChange: (mode: ViewerMode) => void;
  onStatusChange: (status: ReadingStatus) => void;
  onBack: () => void;
  /**
   * PDF アセット無し論文(2a §5.3)。true の間「PDF」セグメントを disabled にし、
   * tooltip「この論文には PDF がありません」を出す(非表示にはしない)。
   */
  pdfDisabled?: boolean;
  /** モバイル縮退(mobile.md §4.2)。true で 戻る/目次/タイトル/ステータスピル/訳文バッジの5要素に縮退する。 */
  isMobile?: boolean;
  /** モバイルの目次ボタン(≡)タップで目次ドロワーを開く(mobile.md §4.3)。 */
  onOpenToc?: () => void;
}

/** ビューアヘッダ(viewer-shell §4)。M1 は 訳文/対訳/原文/PDF の 4 モード表示。 */
export function ViewerHeader({
  itemId,
  title,
  qualityLevel,
  status,
  mode,
  onModeChange,
  pdfDisabled = false,
  onStatusChange,
  onBack,
  isMobile = false,
  onOpenToc,
}: ViewerHeaderProps) {
  const style = useViewerStore((s) => s.style);
  const setStyle = useViewerStore((s) => s.setStyle);
  const literalStatus = useViewerStore((s) => s.literalStatus);
  const setLiteralGeneration = useViewerStore((s) => s.setLiteralGeneration);
  const easyStatus = useViewerStore((s) => s.easyStatus);
  const setEasyGeneration = useViewerStore((s) => s.setEasyGeneration);
  const revisionId = useViewerStore((s) => s.revisionId);
  const activeSectionId = useViewerStore((s) => s.activeSectionId);
  const panelOpen = useViewerStore((s) => s.panelOpen);
  const setPanel = useViewerStore((s) => s.setPanel);
  const queryClient = useQueryClient();
  const toast = useToast();

  // 直訳(literal)のオンデマンド生成(plans/06 §10.2・1b §4.2-7)。「直訳」選択時に
  // TranslationSet が未生成/未完了なら POST し、表示中セクション分の完了を SSE で待つ。
  const ensureLiteralGenerated = useCallback(() => {
    if (!revisionId || literalStatus !== "unknown") return;
    let closed = false;
    let source: EventSource | null = null;

    const finish = (status: "ready" | "unknown") => {
      closed = true;
      source?.close();
      setLiteralGeneration({ status, jobId: null });
    };

    (async () => {
      try {
        const res = await fetch(`/api/revisions/${revisionId}/translations`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            style: "literal",
            ...(activeSectionId ? { priority_section_id: activeSectionId } : {}),
          }),
        });
        if (!res.ok) throw new Error(`literal generation failed: ${res.status}`);
        const body = (await res.json()) as { set_id: string; job_id: string | null };
        if (closed) return;

        if (!body.job_id) {
          // 既に complete(plans/06 §10.2 手順1)。即時表示。
          setLiteralGeneration({ status: "ready", jobId: null, setId: body.set_id });
          return;
        }

        setLiteralGeneration({ status: "generating", jobId: body.job_id, setId: body.set_id });
        if (typeof EventSource === "undefined") {
          finish("ready");
          return;
        }
        source = new EventSource(`/api/jobs/${body.job_id}/events`, { withCredentials: true });
        source.addEventListener("done", () => {
          void queryClient.invalidateQueries({ queryKey: ["units", revisionId, "literal"] });
          finish("ready");
        });
        source.addEventListener("error", () => {
          finish("unknown");
        });
      } catch {
        if (!closed) {
          setLiteralGeneration({ status: "unknown", jobId: null });
          toast({ kind: "error", message: "直訳の生成を開始できませんでした" });
        }
      }
    })();
  }, [revisionId, activeSectionId, literalStatus, setLiteralGeneration, queryClient, toast]);

  const ensureEasyGenerated = useCallback(() => {
    if (!revisionId || easyStatus !== "unknown") return;
    let closed = false;
    let source: EventSource | null = null;

    const finish = (status: "ready" | "unknown") => {
      closed = true;
      source?.close();
      setEasyGeneration({ status, jobId: null });
    };

    (async () => {
      try {
        const res = await fetch(`/api/revisions/${revisionId}/translations`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            style: "easy",
            ...(activeSectionId ? { priority_section_id: activeSectionId } : {}),
          }),
        });
        if (!res.ok) throw new Error(`easy generation failed: ${res.status}`);
        const body = (await res.json()) as { set_id: string; job_id: string | null };
        if (closed) return;

        if (!body.job_id) {
          setEasyGeneration({ status: "ready", jobId: null, setId: body.set_id });
          return;
        }

        setEasyGeneration({ status: "generating", jobId: body.job_id, setId: body.set_id });
        if (typeof EventSource === "undefined") {
          finish("ready");
          return;
        }
        source = new EventSource(`/api/jobs/${body.job_id}/events`, { withCredentials: true });
        source.addEventListener("done", () => {
          void queryClient.invalidateQueries({ queryKey: ["units", revisionId, "easy"] });
          finish("ready");
        });
        source.addEventListener("error", () => {
          finish("unknown");
        });
      } catch {
        if (!closed) {
          setEasyGeneration({ status: "unknown", jobId: null });
          toast({ kind: "error", message: "やさしい訳の生成を開始できませんでした" });
        }
      }
    })();
  }, [revisionId, activeSectionId, easyStatus, setEasyGeneration, queryClient, toast]);

  const styleAnchor = useRef<HTMLButtonElement>(null);
  const overflowAnchor = useRef<HTMLButtonElement>(null);
  const [styleOpen, setStyleOpen] = useState(false);
  const [overflowOpen, setOverflowOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);

  const controlBtn: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 5,
    height: 26,
    minWidth: 0,
    padding: "0 10px",
    border: "1px solid var(--pr-border-control)",
    borderRadius: 6,
    fontSize: 11.5,
    color: "var(--pr-text-mid)",
    background: "transparent",
    cursor: "pointer",
    fontFamily: "inherit",
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  };

  // モバイル縮退(mobile.md §4.2): 戻る/目次/タイトル/ステータスピル/訳文バッジの 5 要素のみ。
  // スタイルセレクタ・論文内検索・オーバーフローメニュー・パネル開閉・モード切替タブは非描画。
  if (isMobile) {
    return (
      <header
        style={{
          height: 52,
          flex: "none",
          background: "var(--pr-bg-card)",
          borderBottom: "1px solid var(--pr-border-header)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "0 12px",
          fontFamily: "var(--pr-font-ui)",
          minWidth: 0,
        }}
      >
        <button
          type="button"
          aria-label="戻る"
          onClick={onBack}
          style={{
            width: 20,
            flex: "none",
            textAlign: "center",
            fontSize: 16,
            color: "var(--pr-text-icon)",
            border: "none",
            background: "transparent",
            cursor: "pointer",
          }}
        >
          ‹
        </button>

        <button
          type="button"
          aria-label="目次を開く"
          onClick={onOpenToc}
          style={{
            width: 20,
            flex: "none",
            fontSize: 14,
            color: "var(--pr-text-icon)",
            border: "none",
            background: "transparent",
            cursor: "pointer",
          }}
        >
          ☰
        </button>

        <span
          title={title}
          style={{
            flex: 1,
            minWidth: 0,
            fontSize: 13,
            fontWeight: 600,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            color: "var(--pr-text)",
          }}
        >
          {title}
        </span>

        <StatusPill status={status} size="md" interactive onChange={onStatusChange} />

        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            height: 18,
            padding: "0 7px",
            borderRadius: 4,
            fontSize: 10,
            fontWeight: 600,
            background: "var(--pr-acc-s)",
            color: "var(--pr-a)",
            flex: "none",
          }}
        >
          訳文
        </span>
      </header>
    );
  }

  return (
    <header
      style={{
        minHeight: 52,
        flex: "none",
        background: "var(--pr-bg-card)",
        borderBottom: "1px solid var(--pr-border-header)",
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: "6px 10px",
        padding: "7px 12px",
        fontFamily: "var(--pr-font-ui)",
        minWidth: 0,
        overflow: "hidden",
      }}
    >
      <button
        type="button"
        aria-label="戻る"
        onClick={onBack}
        style={{
          width: 20,
          flex: "none",
          textAlign: "center",
          fontSize: 16,
          color: "var(--pr-text-icon)",
          border: "none",
          background: "transparent",
          cursor: "pointer",
        }}
      >
        ‹
      </button>

      <span
        title={title}
        style={{
          fontSize: 13,
          fontWeight: 600,
          flex: "1 1 180px",
          minWidth: 90,
          maxWidth: 330,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          color: "var(--pr-text)",
        }}
      >
        {title}
      </span>

      <QualityBadge level={qualityLevel} size={18} />

      <StatusPill status={status} size="md" interactive onChange={onStatusChange} />

      <div style={{ flex: "1 1 24px", minWidth: 0 }} />

      <SegmentedControl
        options={MODE_OPTIONS.map((opt) =>
          opt.value === "pdf" && pdfDisabled
            ? { ...opt, disabled: true, title: "この論文には PDF がありません" }
            : opt,
        )}
        value={mode}
        onChange={(v) => onModeChange(v as ViewerMode)}
        size="md"
        ariaLabel="表示モード"
      />

      {mode === "article" ? (
        // 記事モードのみこのスロットに「✦ 指示つき再生成」を表示する(1h §4.2-7)。
        // 他モードは「スタイル: 自然訳 ▾」(docs/04 §2)。
        <ArticleRegenerateButton itemId={itemId} />
      ) : (
        <>
          <button
            ref={styleAnchor}
            type="button"
            aria-haspopup="menu"
            aria-expanded={styleOpen}
            title={`スタイル: ${STYLE_LABELS[style]}${(style === "literal" && literalStatus === "generating") || (style === "easy" && easyStatus === "generating") ? "(生成中…)" : ""}`}
            style={{ ...controlBtn, maxWidth: 190, flex: "0 1 auto" }}
            onClick={() => setStyleOpen((v) => !v)}
          >
            スタイル: {STYLE_LABELS[style]}
            {(style === "literal" && literalStatus === "generating") || (style === "easy" && easyStatus === "generating") ? "(生成中…)" : ""}
            <span style={{ color: "var(--pr-text-muted)", fontSize: 9 }}>▾</span>
          </button>
          <Popover
            open={styleOpen}
            onClose={() => setStyleOpen(false)}
            anchorRef={styleAnchor}
            width={180}
            placement="bottom-end"
            caret={false}
          >
            {(["natural", "literal", "easy"] as TranslationStyle[]).map((s) => (
              <button
                key={s}
                type="button"
                role="menuitem"
                onClick={() => {
                  setStyle(s);
                  setStyleOpen(false);
                  // 「直訳」選択で TranslationSet 未生成なら生成開始(1b §4.2-7・plans/06 §10.2)。
                  if (s === "literal") ensureLiteralGenerated();
                  if (s === "easy") ensureEasyGenerated();
                }}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  border: "none",
                  background: "transparent",
                  cursor: "pointer",
                  fontFamily: "inherit",
                  fontSize: 12,
                  padding: "8px 12px",
                  color: s === style ? "var(--pr-acc)" : "var(--pr-text-mid)",
                  fontWeight: s === style ? 600 : 400,
                }}
              >
                {STYLE_LABELS[s]}
              </button>
            ))}
          </Popover>
        </>
      )}

      <InPaperSearch />

      <button
        ref={overflowAnchor}
        type="button"
        aria-label="その他"
        aria-haspopup="menu"
        aria-expanded={overflowOpen}
        onClick={() => setOverflowOpen((v) => !v)}
        style={{
          flex: "none",
          fontSize: 15,
          color: "var(--pr-text-sub)",
          letterSpacing: 1,
          border: "none",
          background: "transparent",
          cursor: "pointer",
        }}
      >
        ⋯
      </button>
      <Popover
        open={overflowOpen}
        onClose={() => setOverflowOpen(false)}
        anchorRef={overflowAnchor}
        width={200}
        placement="bottom-end"
        caret={false}
      >
        <button
          type="button"
          role="menuitem"
          onClick={() => {
            setPanel(!panelOpen);
            setOverflowOpen(false);
          }}
          style={{
            display: "block",
            width: "100%",
            textAlign: "left",
            border: "none",
            background: "transparent",
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: 11.5,
            padding: "0 12px",
            height: 30,
            color: "var(--pr-text-mid)",
          }}
        >
          {panelOpen ? "サイドパネルを隠す" : "サイドパネルを表示"}
        </button>
        <button
          type="button"
          role="menuitem"
          onClick={() => {
            setExportOpen(true);
            setOverflowOpen(false);
          }}
          style={{
            display: "block",
            width: "100%",
            textAlign: "left",
            border: "none",
            background: "transparent",
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: 11.5,
            padding: "0 12px",
            height: 30,
            color: "var(--pr-text-mid)",
          }}
        >
          エクスポート
        </button>
      </Popover>

      <PaperExportModal
        open={exportOpen}
        itemId={itemId}
        onClose={() => setExportOpen(false)}
      />
    </header>
  );
}
