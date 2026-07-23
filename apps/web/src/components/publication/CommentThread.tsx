"use client";

import { useMemo, useState, type CSSProperties } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  publicationCommentsCreate,
  publicationCommentsDelete,
  publicationCommentsHide,
  publicationCommentsRestore,
  publicationCommentsUpdate,
  type CommentOut,
  type Problem,
} from "@alinea/api-client";
import { useToast } from "@/components/ui/Toast";

/**
 * 公開記事コメントのクエリキー(Task 26 §4)。publication(slug)単位。
 * mutation 成功後は「その publication のコメントクエリだけ」を invalidate する
 * (他 slug・他クエリを巻き込まない)。
 */
export const publicationCommentKeys = {
  comments: (slug: string) => ["publication-comments", slug] as const,
};

export interface CommentThreadProps {
  slug: string;
  blockId: string;
  /** どのブロックへのスレッドか一目で分かるラベル(見出しテキスト等)。 */
  blockLabel?: string;
  comments: CommentOut[];
  /** 認証済みか(未認証は投稿フォームを出さない)。 */
  isAuthenticated: boolean;
  /**
   * この記事の公開者か。true のときだけ hide/restore を描画する。
   * (API は publisher の user_id を公開しないため、上位が判定して渡す。)
   */
  isPublisher: boolean;
}

function problemTitle(error: unknown, fallback: string): string {
  const problem = error as Partial<Problem> | undefined;
  return problem?.detail || problem?.title || fallback;
}

const boxStyle: CSSProperties = {
  width: "100%",
  minHeight: 60,
  padding: "8px 10px",
  border: "1px solid var(--pr-border-control)",
  borderRadius: 8,
  background: "var(--pr-bg-inset)",
  color: "var(--pr-text)",
  fontSize: 13,
  fontFamily: "inherit",
  resize: "vertical",
  boxSizing: "border-box",
};

const primaryBtn: CSSProperties = {
  height: 30,
  padding: "0 14px",
  border: "none",
  borderRadius: 6,
  background: "var(--pr-acc)",
  color: "#FFFFFF",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};

const subtleBtn: CSSProperties = {
  height: 26,
  padding: "0 10px",
  border: "1px solid var(--pr-border-control)",
  borderRadius: 6,
  background: "transparent",
  color: "var(--pr-text-sub)",
  fontSize: 11.5,
  cursor: "pointer",
  fontFamily: "inherit",
};

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("ja-JP", { dateStyle: "medium", timeStyle: "short" });
}

/** 1 件のコメントの本文/操作。visible のみ本文・編集を出し、hidden/deleted は伏せる。 */
function CommentItem({
  slug,
  comment,
  isAuthenticated,
  isPublisher,
  onMutated,
  indented,
}: {
  slug: string;
  comment: CommentOut;
  isAuthenticated: boolean;
  isPublisher: boolean;
  onMutated: () => void;
  indented?: boolean;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(comment.body);
  const [pending, setPending] = useState(false);

  const runMutation = async (fn: () => Promise<unknown>, failMsg: string) => {
    setPending(true);
    try {
      await fn();
      onMutated();
    } catch (error) {
      toast({ kind: "error", message: problemTitle(error, failMsg) });
    } finally {
      setPending(false);
    }
  };

  const onSaveEdit = () => {
    const body = draft.trim();
    if (!body) return;
    void runMutation(async () => {
      await publicationCommentsUpdate({
        path: { slug, comment_id: comment.id },
        body: { body },
        throwOnError: true,
      });
      setEditing(false);
    }, "コメントを更新できませんでした");
  };

  const onDelete = () =>
    void runMutation(
      () =>
        publicationCommentsDelete({
          path: { slug, comment_id: comment.id },
          throwOnError: true,
        }),
      "コメントを削除できませんでした",
    );

  const onHide = () =>
    void runMutation(
      () =>
        publicationCommentsHide({
          path: { slug, comment_id: comment.id },
          throwOnError: true,
        }),
      "非表示にできませんでした",
    );

  const onRestore = () =>
    void runMutation(
      () =>
        publicationCommentsRestore({
          path: { slug, comment_id: comment.id },
          throwOnError: true,
        }),
      "再表示できませんでした",
    );

  const containerStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 6,
    padding: "10px 12px",
    borderRadius: 8,
    background: "var(--pr-bg-card)",
    border: "1px solid var(--pr-border-hair)",
    marginLeft: indented ? 24 : 0,
  };

  if (comment.status === "deleted") {
    return (
      <div style={containerStyle} data-comment-id={comment.id} data-status="deleted">
        <span style={{ fontSize: 12, color: "var(--pr-text-muted)", fontStyle: "italic" }}>
          このコメントは削除されました。
        </span>
      </div>
    );
  }

  if (comment.status === "hidden") {
    return (
      <div style={containerStyle} data-comment-id={comment.id} data-status="hidden">
        <span style={{ fontSize: 12, color: "var(--pr-text-muted)", fontStyle: "italic" }}>
          このコメントは公開者によって非表示にされました。
        </span>
        {isPublisher ? (
          <div style={{ display: "flex", gap: 6 }}>
            <button type="button" style={subtleBtn} disabled={pending} onClick={onRestore}>
              再表示
            </button>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div style={containerStyle} data-comment-id={comment.id} data-status="visible">
      <div style={{ fontSize: 11, color: "var(--pr-text-muted)" }}>{formatWhen(comment.created_at)}</div>
      {editing ? (
        <>
          <textarea
            aria-label="コメントを編集"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            style={boxStyle}
          />
          <div style={{ display: "flex", gap: 6 }}>
            <button type="button" style={primaryBtn} disabled={pending} onClick={onSaveEdit}>
              保存
            </button>
            <button
              type="button"
              style={subtleBtn}
              disabled={pending}
              onClick={() => {
                setDraft(comment.body);
                setEditing(false);
              }}
            >
              取消
            </button>
          </div>
        </>
      ) : (
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "var(--pr-text)", whiteSpace: "pre-wrap" }}>
          {comment.body}
        </p>
      )}
      {!editing ? (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {isAuthenticated ? (
            <>
              <button type="button" style={subtleBtn} disabled={pending} onClick={() => setEditing(true)}>
                編集
              </button>
              <button type="button" style={subtleBtn} disabled={pending} onClick={onDelete}>
                削除
              </button>
            </>
          ) : null}
          {isPublisher ? (
            <button type="button" style={subtleBtn} disabled={pending} onClick={onHide}>
              非表示
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** 新規投稿/返信フォーム。parentId を渡すと返信になる。 */
function CommentComposer({
  slug,
  blockId,
  parentId,
  placeholder,
  onPosted,
}: {
  slug: string;
  blockId: string;
  parentId?: string;
  placeholder: string;
  onPosted: () => void;
}) {
  const toast = useToast();
  const [body, setBody] = useState("");
  const [pending, setPending] = useState(false);

  const onSubmit = () => {
    const text = body.trim();
    if (!text) return;
    setPending(true);
    void (async () => {
      try {
        await publicationCommentsCreate({
          path: { slug },
          body: parentId ? { block_id: blockId, body: text, parent_id: parentId } : { block_id: blockId, body: text },
          throwOnError: true,
        });
        setBody("");
        onPosted();
      } catch (error) {
        toast({ kind: "error", message: problemTitle(error, "コメントを投稿できませんでした") });
      } finally {
        setPending(false);
      }
    })();
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginLeft: parentId ? 24 : 0 }}>
      <textarea
        aria-label={parentId ? "返信を入力" : "コメントを入力"}
        placeholder={placeholder}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        style={boxStyle}
      />
      <div>
        <button type="button" style={primaryBtn} disabled={pending || !body.trim()} onClick={onSubmit}>
          投稿
        </button>
      </div>
    </div>
  );
}

/**
 * 1 ブロックへのコメントスレッド(Task 26 §4)。ルートコメント + 1 階層の返信を表示し、
 * 投稿・返信・編集・削除・公開者の hide/restore を生成 SDK 経由で行う。
 * mutation 成功時は該当 publication のコメントクエリだけを invalidate する。
 */
export function CommentThread({ slug, blockId, blockLabel, comments, isAuthenticated, isPublisher }: CommentThreadProps) {
  const qc = useQueryClient();
  const [replyTo, setReplyTo] = useState<string | null>(null);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: publicationCommentKeys.comments(slug) });
  };

  // ルート(parent_id=null)と返信を紐づける(1 階層のみ)。
  const { roots, repliesByParent } = useMemo(() => {
    const roots: CommentOut[] = [];
    const repliesByParent = new Map<string, CommentOut[]>();
    for (const c of comments) {
      if (c.parent_id) {
        const list = repliesByParent.get(c.parent_id) ?? [];
        list.push(c);
        repliesByParent.set(c.parent_id, list);
      } else {
        roots.push(c);
      }
    }
    return { roots, repliesByParent };
  }, [comments]);

  return (
    <section
      aria-label={blockLabel ? `「${blockLabel}」へのコメント` : "コメント"}
      data-comment-thread={blockId}
      style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}
    >
      {roots.map((root) => (
        <div key={root.id} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <CommentItem
            slug={slug}
            comment={root}
            isAuthenticated={isAuthenticated}
            isPublisher={isPublisher}
            onMutated={invalidate}
          />
          {(repliesByParent.get(root.id) ?? []).map((reply) => (
            <CommentItem
              key={reply.id}
              slug={slug}
              comment={reply}
              isAuthenticated={isAuthenticated}
              isPublisher={isPublisher}
              onMutated={invalidate}
              indented
            />
          ))}
          {isAuthenticated && root.status !== "deleted" ? (
            replyTo === root.id ? (
              <CommentComposer
                slug={slug}
                blockId={blockId}
                parentId={root.id}
                placeholder="返信を入力…"
                onPosted={() => {
                  setReplyTo(null);
                  invalidate();
                }}
              />
            ) : (
              <div style={{ marginLeft: 24 }}>
                <button type="button" style={subtleBtn} onClick={() => setReplyTo(root.id)}>
                  返信
                </button>
              </div>
            )
          ) : null}
        </div>
      ))}
      {isAuthenticated ? (
        <CommentComposer slug={slug} blockId={blockId} placeholder="コメントを入力…" onPosted={invalidate} />
      ) : null}
    </section>
  );
}
