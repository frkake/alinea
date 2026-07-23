"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  publicationsCreate,
  publicationsUnpublish,
  publicationsUpdate,
  type Problem,
  type PublicationOut,
} from "@alinea/api-client";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";

/**
 * 記事公開モーダル(Task 26 §3)。所有者が記事を「限定公開(unlisted)」または
 * 「公開(public)」にする/更新する/公開解除する。公開前に、
 * - どのブロックが除外されるか(サーバ側 allow-list を反映)
 * - ライセンス判定(AI 生成の解説だけを転載、原論文の図・逐語引用は非公開)
 * を明示して情報漏えいの不安を減らす。
 *
 * API は article_id 単位。成功時は `["publication", articleId]` を invalidate する。
 */
export const publicationKeys = {
  publication: (articleId: string) => ["publication", articleId] as const,
};

export interface PublishArticleModalProps {
  open: boolean;
  onClose: () => void;
  articleId: string;
  /** 既存の公開状態(未公開なら null)。再表示・可視性変更・公開解除の分岐に使う。 */
  current?: PublicationOut | null;
  /** 公開/更新/解除に成功したときの通知(呼び出し元がキャッシュ更新・遷移に使う)。 */
  onChanged?: (pub: PublicationOut | null) => void;
}

type Visibility = "unlisted" | "public";

// サニタイザ(alinea_core.article.publication)の allow-list を UI に反映する。
const INCLUDED = [
  "見出し(AI が付けた章立て)",
  "本文の段落(AI が書いた解説)",
  "出典表記(「元論文とは別物」の注記)",
  "AI が生成した解説図(ライセンス確認済み)",
  "AI が生成した全体概要図",
];
const EXCLUDED = [
  "原文の逐語引用(quote)",
  "原論文の図・表(figure/table — 転載ライセンスの対象外)",
  "議論ブロック(あなたのハイライト由来を含み得る)",
  "メモ・チャット・注釈・訳文",
  "根拠は「論文タイトル + セクション名」だけに縮約(引用本文・オフセットは残さない)",
];

function problemTitle(error: unknown, fallback: string): string {
  const problem = error as Partial<Problem> | undefined;
  return problem?.detail || problem?.title || fallback;
}

const radioRow: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 8,
  padding: "8px 10px",
  border: "1px solid var(--pr-border-control)",
  borderRadius: 8,
  cursor: "pointer",
};

const primaryBtn: CSSProperties = {
  height: 32,
  padding: "0 16px",
  border: "none",
  borderRadius: 6,
  background: "var(--pr-acc)",
  color: "#FFFFFF",
  fontSize: 12.5,
  fontWeight: 700,
  cursor: "pointer",
  fontFamily: "inherit",
};

const subtleBtn: CSSProperties = {
  height: 32,
  padding: "0 14px",
  border: "1px solid var(--pr-border-control)",
  borderRadius: 6,
  background: "transparent",
  color: "var(--pr-text-mid)",
  fontSize: 12.5,
  cursor: "pointer",
  fontFamily: "inherit",
};

export function PublishArticleModal({ open, onClose, articleId, current, onChanged }: PublishArticleModalProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const published = current != null && (current.visibility === "unlisted" || current.visibility === "public");
  const [visibility, setVisibility] = useState<Visibility>(
    current?.visibility === "public" ? "public" : "unlisted",
  );
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (open) setVisibility(current?.visibility === "public" ? "public" : "unlisted");
  }, [open, current?.visibility]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: publicationKeys.publication(articleId) });
  };

  const publicOrigin = typeof window !== "undefined" ? window.location.origin : "";

  const onPublish = () => {
    setPending(true);
    void (async () => {
      try {
        // 既に公開済みなら可視性のみ PATCH、未公開なら作成する。
        const res =
          published && current
            ? await publicationsUpdate({
                path: { article_id: articleId },
                body: { visibility },
                throwOnError: true,
              })
            : await publicationsCreate({
                path: { article_id: articleId },
                body: { visibility },
                throwOnError: true,
              });
        invalidate();
        onChanged?.(res.data);
        toast({
          kind: "success",
          message: visibility === "public" ? "記事を公開しました" : "記事を限定公開しました",
        });
        onClose();
      } catch (error) {
        toast({ kind: "error", message: problemTitle(error, "公開できませんでした") });
      } finally {
        setPending(false);
      }
    })();
  };

  const onUnpublish = () => {
    setPending(true);
    void (async () => {
      try {
        await publicationsUnpublish({ path: { article_id: articleId }, throwOnError: true });
        invalidate();
        onChanged?.(null);
        toast({ kind: "success", message: "公開を解除しました(URL は予約されます)" });
        onClose();
      } catch (error) {
        toast({ kind: "error", message: problemTitle(error, "公開を解除できませんでした") });
      } finally {
        setPending(false);
      }
    })();
  };

  return (
    <Modal open={open} onClose={onClose} width={560} dismissible={!pending} labelledBy="publish-article-title">
      <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 14, maxHeight: "80vh", overflowY: "auto" }}>
        <h2 id="publish-article-title" style={{ fontSize: 15, fontWeight: 700, margin: 0, color: "var(--pr-text)" }}>
          {published ? "公開設定を変更" : "この記事を公開"}
        </h2>

        {published && current ? (
          <div style={{ fontSize: 12, color: "var(--pr-text-sub)" }}>
            現在の公開 URL:{" "}
            <a
              href={`${publicOrigin}/a/${current.slug}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--pr-acc)" }}
            >
              /a/{current.slug}
            </a>
          </div>
        ) : null}

        {/* 何が公開され、何が除外されるか(サニタイザ allow-list の反映)。 */}
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <section style={{ flex: 1, minWidth: 220 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: "var(--pr-green)", marginBottom: 4 }}>
              公開されるもの
            </div>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, color: "var(--pr-text-sub)", lineHeight: 1.7 }}>
              {INCLUDED.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>
          <section style={{ flex: 1, minWidth: 220 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: "var(--pr-warn)", marginBottom: 4 }}>
              公開されないもの
            </div>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, color: "var(--pr-text-sub)", lineHeight: 1.7 }}>
              {EXCLUDED.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>
        </div>

        <p style={{ fontSize: 11.5, color: "var(--pr-text-muted)", lineHeight: 1.6, margin: 0 }}>
          ライセンス判定: 公開されるのは AI が新規生成した解説テキストと図だけです。原論文の図・表・
          逐語引用は転載ライセンスの対象外のため含めません。非公開論文の記事は公開できません。
        </p>

        {/* 可視性の選択。 */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }} role="radiogroup" aria-label="公開範囲">
          <label style={radioRow}>
            <input
              type="radio"
              name="visibility"
              checked={visibility === "unlisted"}
              onChange={() => setVisibility("unlisted")}
            />
            <span>
              <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--pr-text)" }}>限定公開(URL を知る人のみ)</span>
              <span style={{ display: "block", fontSize: 11, color: "var(--pr-text-muted)" }}>
                検索エンジンには載せません(noindex)。
              </span>
            </span>
          </label>
          <label style={radioRow}>
            <input
              type="radio"
              name="visibility"
              checked={visibility === "public"}
              onChange={() => setVisibility("public")}
            />
            <span>
              <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--pr-text)" }}>公開(検索エンジンに載せる)</span>
              <span style={{ display: "block", fontSize: 11, color: "var(--pr-text-muted)" }}>
                誰でも閲覧でき、検索結果にも表示されます。
              </span>
            </span>
          </label>
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 4 }}>
          {published ? (
            <button type="button" style={{ ...subtleBtn, color: "var(--pr-warn)" }} disabled={pending} onClick={onUnpublish}>
              公開を解除
            </button>
          ) : (
            <span />
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" style={subtleBtn} disabled={pending} onClick={onClose}>
              キャンセル
            </button>
            <button type="button" style={primaryBtn} disabled={pending} onClick={onPublish}>
              {published ? "変更を保存" : visibility === "public" ? "公開する" : "限定公開する"}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
